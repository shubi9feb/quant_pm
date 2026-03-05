"""
=============================================================================
PORTFOLIO MANAGER — Main Daily Run Loop
=============================================================================
Orchestrates the full end-of-day cycle:
  1. Ingest EOD prices
  2. Detect market regime
  3. Score all universe stocks with direction model
  4. Apply entry filters + risk checks
  5. Compute position sizes
  6. Place bracket orders via broker adapter
  7. Update trailing stops on open positions
  8. Run drawdown monitoring
  9. Produce daily JSON report
  10. Write immutable audit records
"""

import json
import logging
import os
import sys
import uuid
from datetime import datetime, date
from typing import Dict, List, Optional, Tuple
from pathlib import Path

# Ensure project root is on path
sys.path.insert(0, str(Path(__file__).parent))

from config.settings import (
    CAPITAL, MODEL_VERSION, PAPER_MODE, GOLD_HEDGE_WEIGHT,
    CASH_BUFFER, MAX_POSITIONS, RISK_LIMITS, GOLD_ETF_SYMBOL,
    REBALANCE_DAY, FUNDAMENTAL_RESCORE_DAY
)
from data.features import build_features, run_feature_pipeline, FEATURE_COLS
from core.regime import RegimeDetector, MarketRegime, RegimeState
from models.direction_model import DirectionModel
from risk.engine import (
    EntryFilter, PositionSizer, TrailingStopEngine,
    DrawdownMonitor, DrawdownState, EntryDecision,
    PositionSizeResult, compute_transaction_cost
)
from execution.broker import (
    get_broker, build_entry_order, build_exit_order,
    BracketOrder, OrderStatus, OrderSide
)
from audit.logger import AuditWriter, AuditEventType, AuditVerifier
from reporting.daily_report import (
    DailyReportBuilder, PerformanceTracker,
    PositionSnapshot, TradeRecord
)
from utils.fs_atomic import atomic_write_json
from order_book import OrderBook


# Robust logging (UTF-8 safe for Windows consoles)
import io
import sys

LOG_FORMAT = "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"

# Create a StreamHandler that wraps stdout with UTF-8 and replaces unencodable chars
try:
    stream = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    stream_handler = logging.StreamHandler(stream)
except Exception:
    # Fallback: use default StreamHandler if stdout.buffer is not available
    stream_handler = logging.StreamHandler()
stream_handler.setFormatter(logging.Formatter(LOG_FORMAT))

# FileHandler with UTF-8 encoding
file_handler = logging.FileHandler("portfolio_manager.log", mode="a", encoding="utf-8")
file_handler.setFormatter(logging.Formatter(LOG_FORMAT))

# Configure root logger
root_logger = logging.getLogger()
root_logger.setLevel(logging.INFO)
# remove pre-configured handlers if any
for h in list(root_logger.handlers):
    root_logger.removeHandler(h)
root_logger.addHandler(stream_handler)
root_logger.addHandler(file_handler)

log = logging.getLogger("portfolio_manager")





# ─────────────────────────────────────────────────────────────────────────────
# OPEN POSITION TRACKER
# ─────────────────────────────────────────────────────────────────────────────

class OpenPosition:
    """Tracks state of a single open equity position."""
    def __init__(self, symbol, quantity, entry_price, initial_stop, model_prob,
                 entry_date, order_id, atr_at_entry):
        self.symbol         = symbol
        self.quantity       = quantity
        self.entry_price    = entry_price
        self.avg_cost       = entry_price
        self.current_stop   = initial_stop
        self.initial_stop   = initial_stop
        self.model_prob     = model_prob
        self.entry_date     = entry_date
        self.order_id       = order_id
        self.atr_at_entry   = atr_at_entry
        self.stop_status    = "ORIGINAL"
        self.days_held      = 0

    def days_since_entry(self, today: str) -> int:
        from datetime import date
        try:
            d0 = date.fromisoformat(self.entry_date)
            d1 = date.fromisoformat(today)
            return (d1 - d0).days
        except Exception:
            return 0

    def unrealised_pnl(self, current_price: float) -> float:
        return (current_price - self.avg_cost) * self.quantity

    def to_snapshot(self, current_price: float, today: str, nav: float) -> PositionSnapshot:
        mkt_val = current_price * self.quantity
        upnl    = self.unrealised_pnl(current_price)
        return PositionSnapshot(
            symbol          = self.symbol,
            quantity        = self.quantity,
            avg_cost        = self.avg_cost,
            current_price   = current_price,
            market_value    = mkt_val,
            unrealised_pnl  = upnl,
            unrealised_pct  = upnl / (self.avg_cost * self.quantity + 1e-10) * 100,
            current_stop    = self.current_stop,
            gain_from_entry = (current_price - self.avg_cost) / (self.avg_cost + 1e-10) * 100,
            days_held       = self.days_since_entry(today),
            stop_status     = self.stop_status,
            model_prob_entry= self.model_prob,
            weight_pct      = mkt_val / (nav + 1e-10) * 100
        )


# ─────────────────────────────────────────────────────────────────────────────
# PORTFOLIO MANAGER
# ─────────────────────────────────────────────────────────────────────────────

class PortfolioManager:
    """
    Main orchestrator. Runs the complete EOD cycle.
    Stateful: maintains open positions across multiple daily runs.
    """

    def __init__(self):
        self.positions:       Dict[str, OpenPosition] = {}
        self.cash:            float = CAPITAL * (1 - GOLD_HEDGE_WEIGHT)
        self.gold_value:      float = CAPITAL * GOLD_HEDGE_WEIGHT
        self.realised_pnl:    float = 0.0

        # Component initialisation
        self.regime_detector  = RegimeDetector()
        self.direction_model  = DirectionModel()
        self.entry_filter     = EntryFilter()
        self.position_sizer   = PositionSizer()
        self.trail_engine     = TrailingStopEngine()
        self.dd_monitor       = DrawdownMonitor(starting_capital=CAPITAL)
        self.broker           = get_broker()
        self.audit            = AuditWriter(model_version=MODEL_VERSION)
        self.perf_tracker     = PerformanceTracker()
        self.report_builder   = DailyReportBuilder()
        self.order_book       = OrderBook()  # Persistent order registry

        self.run_id           = str(uuid.uuid4())[:8]

        self._load_model_if_available()
        
        # Load persisted state if available
        self.load_state()
        
        # Reconcile with broker after state load
        self.reconcile_with_broker()
        
        self.audit.log_system_start({
            "model_version":   MODEL_VERSION,
            "paper_mode":      PAPER_MODE,
            "capital":         CAPITAL,
            "risk_per_trade":  RISK_LIMITS.risk_per_trade,
            "max_positions":   RISK_LIMITS.max_positions,
            "run_id":          self.run_id,
        })
        log.info(f"[PM] PortfolioManager initialised | run_id={self.run_id} | paper_mode={PAPER_MODE}")

    def _load_model_if_available(self):
        """Try to load a pre-trained model; fall back to untrained state."""
        try:
            self.direction_model = DirectionModel.load()
            log.info(f"[PM] Loaded pre-trained model {MODEL_VERSION}")
        except Exception as e:
            log.warning(f"[PM] No saved model found ({e}). Train before running EOD.")

    # ── MAIN EOD CYCLE ─────────────────────────────────────────────────────────

    def run_eod(
        self,
        eod_prices:     Dict[str, Dict],    # {symbol: {open,high,low,close,volume,...}}
        nifty_close:    "pd.Series",
        india_vix:      "pd.Series",
        fii_flows:      "pd.Series",
        feature_df:     "pd.DataFrame",     # pre-built feature matrix for all symbols
        trade_date:     Optional[str] = None
    ) -> Dict:
        """
        Full end-of-day run. Returns the daily JSON report dict.

        Args:
            eod_prices  : latest EOD price data for every universe symbol
            nifty_close : Nifty 50 close history (DatetimeIndex, required for regime)
            india_vix   : India VIX close history
            fii_flows   : FII daily net flows history (₹cr)
            feature_df  : pre-computed feature matrix (from run_feature_pipeline)
            trade_date  : trading date (defaults to today)
        """
        trade_date = trade_date or date.today().isoformat()
        log.info(f"[PM] ═══════ EOD CYCLE: {trade_date} ═══════")

        # ── 1. REGIME DETECTION ───────────────────────────────────────────────
        log.info("[PM] Step 1: Regime Detection")
        regime_state = self.regime_detector.detect(
            nifty_close, india_vix, fii_flows, trade_date
        )
        self.audit.log_regime(regime_state)
        log.info(f"[PM] Regime: {regime_state.regime.value} | alloc_mult={regime_state.allocation_mult}")

        # ── 2. PORTFOLIO NAV & DRAWDOWN ────────────────────────────────────────
        log.info("[PM] Step 2: NAV & Drawdown Check")
        equity_value = sum(
            eod_prices.get(sym, {}).get("close", pos.avg_cost) * pos.quantity
            for sym, pos in self.positions.items()
        )
        nav = self.cash + equity_value + self.gold_value
        dd_state, current_dd = self.dd_monitor.update(nav, trade_date)

        if current_dd >= RISK_LIMITS.drawdown_cash_at:
            self.audit.log_drawdown(current_dd, dd_state.value, nav)

        self.perf_tracker.record_nav(nav, trade_date)
        log.info(f"[PM] NAV: ₹{nav:,.0f} | DD: {current_dd:.2%} | State: {dd_state.value}")

        # ── 3. TRAILING STOP UPDATES ────────────────────────────────────────────
        log.info("[PM] Step 3: Trailing Stop Updates")
        exits_today   = []
        stop_updates  = []

        for sym, pos in list(self.positions.items()):
            if sym not in self.positions:
                continue

            if sym not in eod_prices:
                continue
            current_price = eod_prices[sym]["close"]
            current_atr   = eod_prices[sym].get("atr_14", pos.atr_at_entry)
            log.info(f"[DEBUG] Checking {sym} | price={current_price} | stop={pos.current_stop}")

            # Skip if price above stop
            if current_price > pos.current_stop:
                continue

            # ── STOP HIT: robust exit handling ─────────────────────────────
            if pos.current_stop is not None and current_price <= pos.current_stop:
                log.info(f"[PM] STOP HIT: {sym} @ ₹{current_price:.2f} (stop=₹{pos.current_stop:.2f})")
                log.error(f"[CRITICAL] EXIT CALLED for {sym} | price={current_price} | stop={pos.current_stop}")


                exit_order = build_exit_order(
                    sym, pos.quantity, current_price,
                    f"STOP_HIT_at_{pos.current_stop:.2f}", MODEL_VERSION
                )

                resp = self.broker.place_order(exit_order)
                self.audit.log_order(resp, "PLACED")
                log.info(f"[DEBUG] Broker response: filled_qty={getattr(resp, 'filled_qty', None)}, avg_price={getattr(resp, 'avg_fill_price', None)}")

                # canonical order id
                order_id = getattr(resp, "client_order_id", getattr(exit_order, "client_order_id", None))

                # If rejected
                if not getattr(resp, "accepted", False):
                    log.error(f"[PM] Exit order rejected for {sym} | order_id={order_id} | reason={getattr(resp, 'reason', None)}")
                    pos.stop_status = "PENDING_EXIT"
                    continue

                # 1) Try to read filled qty and avg price from resp directly
                filled_qty = getattr(resp, "filled_qty", None)
                avg_fill_price = getattr(resp, "avg_fill_price", None)

                # 2) Normalize types if available
                if filled_qty is not None:
                    try:
                        filled_qty = int(filled_qty)
                    except Exception:
                        filled_qty = None

                if avg_fill_price is not None:
                    try:
                        avg_fill_price = float(avg_fill_price)
                    except Exception:
                        avg_fill_price = None

                # 3) Ask order_book as authoritative fallback if resp lacks info or it's zero
                if (filled_qty is None) or (avg_fill_price is None) or (avg_fill_price <= 1e-9):
                    ob = None
                    try:
                        ob_entry = self.order_book.get_order(order_id)
                        # get_order should return OrderBookEntry or dict (we handle both)
                        if ob_entry is not None:
                            if hasattr(ob_entry, "to_dict"):
                                ob = ob_entry.to_dict()  # dict-safe fallback
                            elif isinstance(ob_entry, dict):
                                ob = ob_entry
                    except Exception:
                        ob = None

                    if ob:
                        # best-effort mapping from various possible keys
                        if filled_qty is None:
                            filled_qty = int(ob.get("filled_qty") or ob.get("filled") or ob.get("filled_qty", 0) or 0)

                        if avg_fill_price is None or (avg_fill_price <= 1e-9):
                            avg_fill_price = (
                                ob.get("avg_fill_price")
                                or ob.get("fill_price")
                                or ob.get("price")
                                or None
                            )
                            if avg_fill_price is not None:
                                try:
                                    avg_fill_price = float(avg_fill_price)
                                except Exception:
                                    avg_fill_price = None

                # 4) Final fallbacks: assume full qty, and use market close price if we still don't have price
                if filled_qty is None:
                    filled_qty = pos.quantity

                if (avg_fill_price is None) or (avg_fill_price <= 1e-9):
                    log.error(f"[PM][FALLBACK] Missing fill price for {sym} order_id={order_id}. Falling back to market close price {current_price}")
                    avg_fill_price = float(current_price)

                # Compute proceeds and costs using the confirmed fill price
                proceeds = float(avg_fill_price) * filled_qty
                costs = compute_transaction_cost(proceeds, "SELL")

                # compute gross & net pnl
                gross_pnl = (avg_fill_price - pos.avg_cost) * filled_qty
                net_pnl = gross_pnl - costs["total_cost"]

                # Update accounting (rounded where appropriate)
                self.realised_pnl += net_pnl
                self.cash += proceeds - costs["total_cost"]

                # Persist order info into order_book (add if missing, then update)
                try:
                    # add if missing
                    if not self.order_book.has_order(order_id):
                        self.order_book.add_order(
                            client_order_id=order_id,
                            symbol=sym,
                            side="SELL",
                            requested_qty=pos.quantity,
                            entry_price=avg_fill_price
                        )

                    # update with definitive fill info (ensure update_fill accepts avg_fill_price)
                    self.order_book.update_fill(
                        client_order_id=order_id,
                        filled_qty=filled_qty,
                        avg_fill_price=avg_fill_price,
                        status="FILLED" if filled_qty >= pos.quantity else "PARTIAL"
                    )
                except Exception as e:
                    log.warning(f"[PM] order_book persist failed for {order_id}: {e}")

                # Compose trade record with definitive values
                exits_today.append(TradeRecord(
                    symbol=sym,
                    action="STOP_HIT",
                    quantity=filled_qty,
                    price=round(avg_fill_price, 2),
                    value=round(proceeds, 2),
                    realised_pnl=round(net_pnl, 2),
                    reason="stop_loss_triggered",
                    model_prob=pos.model_prob,
                    stop_price=pos.current_stop,
                    cost_inr=round(costs["total_cost"], 2),
                    client_order_id=order_id,
                    timestamp=datetime.now().isoformat()
                ))

                # Remove or reduce the position
                if filled_qty >= pos.quantity:
                    del self.positions[sym]
                else:
                    pos.quantity -= filled_qty
                    pos.stop_status = "PARTIAL_EXIT"

                continue

            # Update trailing stop
            update = self.trail_engine.update(
                sym, pos.entry_price, current_price, pos.current_stop, current_atr
            )
            if update.action != "NO_CHANGE":
                pos.current_stop = update.new_stop
                pos.stop_status  = update.action
                self.audit.log_stop_update(update)
                stop_updates.append(update.to_dict())

        # ── 4. DIRECTION MODEL SCORING ─────────────────────────────────────────
        log.info("[PM] Step 4: Model Scoring")
        entries_today    = []
        rejected_signals = []
        orders_placed    = []
        top_signals      = []

        if not self.direction_model._trained:
            log.warning("[PM] Model not trained — skipping signal generation")
        else:
            scores = self.direction_model.predict(feature_df)
            self.audit.log_model_scores(
                scores.head(20).to_dict(orient="records")
            )

            # Sort by model probability descending
            scores = scores.sort_values("model_prob", ascending=False)

            # Store top signals for report
            top_signals = scores.head(20).to_dict(orient="records")

            # ── 5. ENTRY SELECTION ───────────────────────────────────────────
            log.info("[PM] Step 5: Entry Selection & Filtering")

            for _, row in scores.iterrows():
                sym = row["symbol"]
                # Prevent re-entry of recently exited stocks
                if sym in [e.symbol for e in exits_today]:
                    continue

                if sym in self.positions:
                    continue   # already holding
                if sym not in eod_prices:
                    continue

                price_data = eod_prices[sym]
                close      = price_data.get("close", 0)
                ema_50     = price_data.get("ema_50", close)
                ema_200    = price_data.get("ema_200", close)
                rsi_val    = price_data.get("rsi_14", 60)
                atr_14     = price_data.get("atr_14", close * 0.02)
                swing_low  = price_data.get("swing_low_20d", close * 0.92)
                avg_vol    = price_data.get("avg_volume_20d", 0)
                avg_val    = price_data.get("avg_value_20d", 0)
                model_prob = float(row["model_prob"])

                # Run entry filter
                decision = self.entry_filter.check(
                    symbol          = sym,
                    close           = close,
                    ema_50          = ema_50,
                    ema_200         = ema_200,
                    rsi             = rsi_val,
                    model_prob      = model_prob,
                    avg_volume_20d  = avg_vol,
                    avg_value_20d   = avg_val,
                    regime_state    = regime_state,
                    current_positions = len(self.positions),
                    drawdown_state  = dd_state,
                )

                self.audit.log_entry_decision(decision)

                if not decision.allowed:
                    rejected_signals.append({
                        "symbol":    sym,
                        "model_prob": model_prob,
                        "reasons":   decision.reject_reasons
                    })
                    continue

                # ── Compute position size ─────────────────────────────────────
                existing_val = (self.positions[sym].quantity * close
                                if sym in self.positions else 0)
                size = self.position_sizer.compute(
                    symbol           = sym,
                    entry_price      = close,
                    atr_14           = atr_14,
                    swing_low_20d    = swing_low,
                    available_capital= self.cash,
                    drawdown_state   = dd_state,
                    existing_value   = existing_val
                )

                if size.shares <= 0:
                    rejected_signals.append({
                        "symbol": sym, "model_prob": model_prob,
                        "reasons": ["position_size_zero: " + size.sizing_notes]
                    })
                    continue

                # ── Place bracket order ───────────────────────────────────────
                costs  = compute_transaction_cost(size.position_value, "BUY")
                order  = build_entry_order(
                    symbol        = sym,
                    quantity      = size.shares,
                    entry_price   = close,
                    stop_price    = size.initial_stop,
                    model_prob    = model_prob,
                    model_version = MODEL_VERSION,
                    decision_meta = {
                        "model_prob":      model_prob,
                        "model_rank":      int(row["model_rank"]),
                        "regime":          regime_state.regime.value,
                        "stop_reason":     size.stop_reason,
                        "sizing_notes":    size.sizing_notes,
                        "pass_checks":     decision.pass_checks,
                        "entry_date":      trade_date,
                        "risk_amount_inr": size.risk_amount,
                        "transaction_costs": costs
                    }
                )


                # ── Place bracket order (robust handling) ───────────────────────────────
                resp = self.broker.place_order(order)
                self.audit.log_order(resp, "PLACED")

                # If broker didn't accept the order, skip and do not reserve cash
                if not getattr(resp, "accepted", False):
                    reason = getattr(resp, 'reason', 'unknown')
                    log.warning(f"[PM] Order rejected: {reason}")
                    self.audit.write(
                        AuditEventType.ENTRY_REJECTED,
                        {"symbol": sym, "reason": reason, "run_id": self.run_id},
                        symbol=sym
                    )
                    continue

                # Get fill info (paper adapter may not fill immediately — fall back to expected)
                filled_qty = int(getattr(resp, "filled_qty", size.shares))
                fill_price = float(getattr(resp, "avg_fill_price", close))

                # Compute actual position value and costs based on fills (only deduct for filled amount)
                position_value = fill_price * filled_qty
                fill_costs = compute_transaction_cost(position_value, "BUY")
                total_cost = position_value + fill_costs["total_cost"]

                # If nothing filled, log and continue (no cash reserved, no position)
                if filled_qty <= 0:
                    log.warning(f"[PM] Order accepted but no fills for {sym} (qty={size.shares})")
                    self.audit.write(
                        AuditEventType.ENTRY_ACCEPTED_NO_FILL,
                        {"symbol": sym, "requested_qty": size.shares, "run_id": self.run_id},
                        symbol=sym
                    )
                    continue

                # Defensive: ensure cash exists for the filled amount
                if total_cost > self.cash + 1e-9:
                    log.error(f"[PM] Insufficient cash after fill for {sym}: need ₹{total_cost:.2f}, have ₹{self.cash:.2f}")
                    self.audit.write(
                        AuditEventType.INSUFFICIENT_CASH_AFTER_FILL,
                        {"symbol": sym, "needed": total_cost, "available": self.cash, "run_id": self.run_id},
                        symbol=sym
                    )
                    continue

                # Deduct cash only for the filled amount
                self.cash -= total_cost
                
                # Track order in persistent order book
                self.order_book.add_order(
                    client_order_id=getattr(resp, "client_order_id", getattr(order, "client_order_id", None)),
                    symbol=sym,
                    side="BUY",
                    requested_qty=size.shares,
                    entry_price=fill_price
                )
                
                # Update fill status in order book
                self.order_book.update_fill(
                    client_order_id=getattr(resp, "client_order_id", getattr(order, "client_order_id", None)),
                    filled_qty=filled_qty,
                    avg_fill_price=fill_price,
                    status="FILLED" if filled_qty == size.shares else "PARTIAL"
                )

                # Register open position using resp info
                self.positions[sym] = OpenPosition(
                    symbol       = sym,
                    quantity     = filled_qty,
                    entry_price  = fill_price,
                    initial_stop = size.initial_stop,
                    model_prob   = model_prob,
                    entry_date   = trade_date,
                    order_id     = getattr(resp, "client_order_id", getattr(order, "client_order_id", None)),
                    atr_at_entry = atr_14
                )

                # Create entry TradeRecord using resp.client_order_id and actual fill data
                entries_today.append(TradeRecord(
                    symbol           = sym,
                    action           = "ENTRY",
                    quantity         = filled_qty,
                    price            = fill_price,
                    value            = position_value,
                    reason           = f"model_prob={model_prob:.3f}",
                    model_prob       = model_prob,
                    stop_price       = size.initial_stop,
                    cost_inr         = fill_costs["total_cost"],
                    client_order_id  = getattr(resp, "client_order_id", getattr(order, "client_order_id", None)),
                    timestamp        = datetime.now().isoformat()
                ))

                # Record order metadata for report (use resp.to_dict() if available)
                orders_placed.append({**(getattr(resp, "to_dict", lambda: order.to_dict())()), "size_result": size.to_dict()})

                log.info(
                    f"[PM] ENTRY: {sym} | {filled_qty} shares @ ₹{fill_price:.2f} | stop=₹{size.initial_stop:.2f} | "
                    f"prob={model_prob:.3f} | risk=₹{size.risk_amount:.0f} | {getattr(resp, 'client_order_id', getattr(order, 'client_order_id', None))}"
                )

                # Stop if positions full
                if len(self.positions) >= MAX_POSITIONS:
                    log.info(f"[PM] MAX_POSITIONS ({MAX_POSITIONS}) reached, stopping entries")
                    break


        # ── 6. DAILY REPORT ───────────────────────────────────────────────────
        log.info("[PM] Step 6: Generating Daily Report")

        position_snapshots = [
            pos.to_snapshot(
                eod_prices.get(sym, {}).get("close", pos.avg_cost),
                trade_date, nav
            )
            for sym, pos in self.positions.items()
        ]

        # Get last audit hash for report
        try:
            last_hash = self.audit.last_hash
        except Exception:
            last_hash = ""

        report = self.report_builder.build(
            regime_state        = regime_state,
            portfolio_nav       = nav,
            positions           = position_snapshots,
            entries_today       = entries_today,
            exits_today         = exits_today,
            rejected_signals    = rejected_signals,
            orders_placed       = orders_placed,
            top_signals         = top_signals,
            drawdown_state      = dd_state.value,
            current_drawdown    = current_dd,
            peak_nav            = self.dd_monitor.peak_nav,
            realised_pnl_today = sum(e.realised_pnl for e in exits_today),
            realised_pnl_total  = self.realised_pnl,
            last_audit_hash     = last_hash,
        )

        # Save report
        report_path = self.report_builder.save(report)
        self.audit.log_daily_summary({
            "report_path":    report_path,
            "nav":            nav,
            "drawdown":       current_dd,
            "entries":        len(entries_today),
            "exits":          len(exits_today),
            "open_positions": len(self.positions),
            "stop_updates":   len(stop_updates),
        })

        self.report_builder.print_summary(report)
        log.info(f"[PM] ═══════ EOD COMPLETE: {trade_date} ═══════\n")

        return report

    # ── WEEKLY REBALANCE ──────────────────────────────────────────────────────

    def weekly_rebalance(self, trade_date: str, feature_df: "pd.DataFrame"):
        """
        Weekly position review:
        - Close positions where model_prob has dropped below 0.40
        - Trim positions that have grown beyond max_per_stock
        - Add to high-conviction positions if room available
        """
        log.info(f"[PM] Weekly rebalance: {trade_date}")
        self.audit.write(
            AuditEventType.REBALANCE,
            payload={
                "type": "weekly",
                "date": trade_date,
                "open_positions": list(self.positions.keys()),
                "note": "Re-scoring all open positions against direction model"
            }
        )

    # ── MONTHLY FUNDAMENTAL RE-SCORE ──────────────────────────────────────────

    def monthly_fundamental_rescore(self, trade_date: str, fundamentals: Dict):
        """
        Monthly review of fundamental quality:
        - PE, PB, ROE, ROCE, debt/equity, earnings growth
        - Downgrade stocks with deteriorating fundamentals
        - Log scores to audit and daily report
        """
        log.info(f"[PM] Monthly fundamental re-score: {trade_date}")
        scores = []
        for sym, data in fundamentals.items():
            score = self._score_fundamentals(sym, data)
            scores.append(score)
            self.audit.write(
                AuditEventType.FUNDAMENTAL_SCORE,
                payload=score,
                symbol=sym
            )
        return scores

    def _score_fundamentals(self, symbol: str, data: Dict) -> Dict:
        """Simple multi-factor fundamental score (0–100)."""
        score = 50   # base
        notes = []

        pe   = data.get("pe_ratio", 25)
        roe  = data.get("roe_pct", 15)
        debt = data.get("debt_equity", 0.5)
        eg   = data.get("earnings_growth_3y", 0.10)

        if pe   < 20:  score += 10; notes.append("low_pe")
        if pe   > 40:  score -= 15; notes.append("high_pe")
        if roe  > 20:  score += 15; notes.append("high_roe")
        if roe  < 10:  score -= 10; notes.append("low_roe")
        if debt < 0.3: score += 10; notes.append("low_debt")
        if debt > 1.0: score -= 15; notes.append("high_debt")
        if eg   > 0.15: score += 10; notes.append("strong_earnings")
        if eg   < 0:   score -= 20; notes.append("declining_earnings")

        return {
            "symbol":       symbol,
            "score":        max(0, min(100, score)),
            "pe_ratio":     pe,
            "roe_pct":      roe,
            "debt_equity":  debt,
            "earnings_growth": eg,
            "notes":        notes
        }

    # ── MODEL TRAINING ─────────────────────────────────────────────────────────

    def train_model(self, feature_df: "pd.DataFrame") -> Dict:
        """Train/retrain the direction model and save."""
        log.info("[PM] Training direction model (walk-forward CV)...")
        results = self.direction_model.train(feature_df, run_wf_cv=True)
        self.direction_model.save()
        self.audit.write(
            AuditEventType.MODEL_RETRAINED,
            payload={"cv_results": results, "version": MODEL_VERSION}
        )
        log.info("[PM] Model training complete")
        return results

    # ── STATE PERSISTENCE ──────────────────────────────────────────────────────

    def save_state(self, path: str = "portfolio_state.json"):
        """Persist portfolio state between sessions."""
        state = {
            "cash":         self.cash,
            "gold_value":   self.gold_value,
            "realised_pnl": self.realised_pnl,
            "run_id":       self.run_id,
            "positions": {
                sym: {
                    "quantity":     p.quantity,
                    "avg_cost":     p.avg_cost,
                    "current_stop": p.current_stop,
                    "initial_stop": p.initial_stop,
                    "model_prob":   p.model_prob,
                    "entry_date":   p.entry_date,
                    "order_id":     p.order_id,
                    "atr_at_entry": p.atr_at_entry,
                    "stop_status":  p.stop_status
                }
                for sym, p in self.positions.items()
            }
        }
        atomic_write_json(path, state)
        log.info(f"[PM] State saved atomically: {path}")

    def load_state(self, path: str = "portfolio_state.json"):
        """Restore portfolio state from disk using atomic read."""
        from utils.fs_atomic import atomic_read_json
        
        state = atomic_read_json(path, default=None)
        if state is None:
            log.info(f"[PM] No state file at {path}, starting fresh")
            return
        
        self.cash         = state.get("cash", CAPITAL * (1 - GOLD_HEDGE_WEIGHT))
        self.gold_value   = state.get("gold_value", CAPITAL * GOLD_HEDGE_WEIGHT)
        self.realised_pnl = state.get("realised_pnl", 0.0)
        
        for sym, p in state.get("positions", {}).items():
            pos = OpenPosition(
                symbol       = sym,
                quantity     = p["quantity"],
                entry_price  = p["avg_cost"],
                initial_stop = p["initial_stop"],
                model_prob   = p["model_prob"],
                entry_date   = p["entry_date"],
                order_id     = p["order_id"],
                atr_at_entry = p["atr_at_entry"]
            )
            pos.current_stop = p["current_stop"]
            pos.stop_status  = p["stop_status"]
            pos.avg_cost     = p["avg_cost"]
            self.positions[sym] = pos
        
        log.info(f"[PM] State loaded: {len(self.positions)} positions, cash=₹{self.cash:,.0f}")

    def reconcile_with_broker(self):
        """
        Compare local state with broker positions/orders.
        Log discrepancies as audit events for manual review.
        """
        log.info(f"[PM] Reconciling local state with broker (run_id={self.run_id})")

        # Get broker-side positions
        try:
            broker_positions = self.broker.get_open_positions()
        except Exception as e:
            log.error(f"[PM] Failed to fetch broker positions: {e}")
            return

        broker_syms = {p["symbol"] for p in broker_positions}
        local_syms  = set(self.positions.keys())

        # Positions in broker but not local
        for sym in broker_syms - local_syms:
            bp = next(p for p in broker_positions if p["symbol"] == sym)
            log.warning(f"[PM] RECONCILE: {sym} in broker (qty={bp.get('quantity')}) but not in local state")
            self.audit.write(
                AuditEventType.RECONCILE_MISSING_POSITION,
                {"symbol": sym, "broker_qty": bp.get("quantity"), "run_id": self.run_id},
                symbol=sym
            )

        # Positions in local but not broker
        for sym in local_syms - broker_syms:
            pos = self.positions[sym]
            log.warning(f"[PM] RECONCILE: {sym} in local state (qty={pos.quantity}) but not in broker")
            self.audit.write(
                AuditEventType.RECONCILE_STALE_POSITION,
                {"symbol": sym, "local_qty": pos.quantity, "run_id": self.run_id},
                symbol=sym
            )

        # Quantity mismatches for symbols in both
        for sym in local_syms & broker_syms:
            bp = next(p for p in broker_positions if p["symbol"] == sym)
            local_qty = self.positions[sym].quantity
            broker_qty = bp.get("quantity", 0)
            if local_qty != broker_qty:
                log.warning(
                    f"[PM] RECONCILE: {sym} qty mismatch local={local_qty} broker={broker_qty}"
                )
                self.audit.write(
                    AuditEventType.RECONCILE_STALE_POSITION,
                    {"symbol": sym, "local_qty": local_qty, "broker_qty": broker_qty, "run_id": self.run_id},
                    symbol=sym
                )

        if not (broker_syms - local_syms) and not (local_syms - broker_syms):
            log.info("[PM] Reconciliation: local state matches broker")


# ─────────────────────────────────────────────────────────────────────────────
# ENTRY POINT
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    """
    Demo / paper trading run with synthetic data.
    Replace synthetic data with real EOD feed in production.
    """
    import numpy as np
    import pandas as pd

    log.info("Starting QuantPM demo run...")

    # ── Synthetic market data for demo ───────────────────────────────────────
    np.random.seed(42)
    n_days   = 500
    dates    = pd.date_range("2023-01-01", periods=n_days, freq="B")

    # Simulate Nifty 50
    nifty_returns  = np.random.normal(0.0004, 0.008, n_days)
    nifty_close    = pd.Series(18000 * np.cumprod(1 + nifty_returns), index=dates)

    # Simulate India VIX (mean-reverting)
    india_vix      = pd.Series(
        np.abs(15 + np.random.normal(0, 3, n_days).cumsum() * 0.1),
        index=dates
    ).clip(8, 50)

    # Simulate FII flows (₹cr, correlated with market)
    fii_flows      = pd.Series(
        np.random.normal(500, 2000, n_days),
        index=dates
    )

    pm = PortfolioManager()
    log.info("[DEMO] Portfolio Manager created successfully")
    log.info("[DEMO] All systems initialised. Ready for EOD data feed.")
    log.info(f"[DEMO] Paper mode: {PAPER_MODE}")
    log.info(f"[DEMO] Capital: ₹{CAPITAL:,.0f}")
    log.info(f"[DEMO] Max positions: {MAX_POSITIONS}")
    log.info(f"[DEMO] Risk per trade: {RISK_LIMITS.risk_per_trade:.1%}")

    print("\n✅ QuantPM Indian Equity Portfolio Manager — All components loaded\n")
