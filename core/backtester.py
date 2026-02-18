"""
=============================================================================
BACKTESTER — Walk-Forward Simulation with Indian Market Transaction Costs
=============================================================================
Simulates the full trading system historically with strict no-lookahead rules.
Computes realistic performance after brokerage, STT, slippage.
"""

import numpy as np
import pandas as pd
import json
import logging
from datetime import datetime
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass, field, asdict

from config.settings import (
    CAPITAL, RISK_PER_TRADE, MAX_POSITIONS, MAX_PER_STOCK,
    GOLD_HEDGE_WEIGHT, CASH_BUFFER, MODEL_VERSION,
    WALK_FORWARD_FOLDS, TRAIN_WINDOW_DAYS, OOS_WINDOW_DAYS,
    ATR_MULTIPLIER_STOP, HARD_STOP_PCT, BREAKEVEN_TRIGGER, TRAIL_START,
    TRAIL_MULTIPLIER, DRAWDOWN_REDUCE_THRESHOLD, DRAWDOWN_CASH_THRESHOLD,
    RSI_LOW, RSI_HIGH, MIN_MODEL_PROB
)
from data.features import run_feature_pipeline, FEATURE_COLS, build_features
from core.regime import RegimeDetector, MarketRegime
from models.direction_model import DirectionModel
from risk.engine import (
    DrawdownMonitor, DrawdownState, compute_transaction_cost,
    TrailingStopEngine, PositionSizer
)

log = logging.getLogger("backtester")


@dataclass
class BacktestTrade:
    symbol:       str
    entry_date:   str
    exit_date:    str
    entry_price:  float
    exit_price:   float
    quantity:     int
    gross_pnl:    float
    net_pnl:      float     # after all costs
    hold_days:    int
    exit_reason:  str       # STOP_HIT | TRAIL | SIGNAL_FADE | EOD
    model_prob:   float
    regime:       str

    def to_dict(self) -> dict:
        return {k: (round(v, 4) if isinstance(v, float) else v)
                for k, v in asdict(self).items()}


@dataclass
class BacktestResults:
    start_date:      str
    end_date:        str
    total_days:      int
    total_trades:    int
    winning_trades:  int
    losing_trades:   int
    win_rate:        float
    avg_win:         float
    avg_loss:        float
    expectancy:      float
    total_return:    float
    ann_return:      float
    sharpe:          float
    sortino:         float
    max_drawdown:    float
    calmar:          float
    profit_factor:   float
    total_costs_inr: float
    nav_history:     List[float] = field(default_factory=list)
    trades:          List[Dict]  = field(default_factory=list)

    def to_dict(self) -> dict:
        d = asdict(self)
        d.pop("nav_history")   # can be large
        d.pop("trades")
        return {k: (round(v, 4) if isinstance(v, float) else v) for k, v in d.items()}


class WalkForwardBacktester:
    """
    Walk-forward backtest with strict train/OOS separation.

    Each fold:
    1. Train XGBoost on training window
    2. Detect regime using indicator history up to that date
    3. Score all stocks on OOS data
    4. Apply entry filters and position sizing
    5. Simulate bracket orders with realistic fills
    6. Track NAV, drawdown, trades

    Never uses any future data in train/signal steps.
    """

    def __init__(self):
        self.regime_detector = RegimeDetector()
        self.trail_engine    = TrailingStopEngine()
        self.position_sizer  = PositionSizer()

    def run(
        self,
        price_data:    Dict[str, pd.DataFrame],   # {symbol: OHLCV DataFrame}
        nifty_close:   pd.Series,
        india_vix:     pd.Series,
        fii_flows:     pd.Series,
        benchmark:     pd.Series,
    ) -> BacktestResults:
        """
        Run complete walk-forward backtest.
        Returns BacktestResults with all metrics and trade log.
        """
        log.info("[BT] Starting walk-forward backtest...")

        # Build full feature universe
        feature_df  = run_feature_pipeline(price_data, benchmark, horizon=30)
        all_dates   = sorted(feature_df.index.unique())
        total_dates = len(all_dates)

        log.info(f"[BT] Universe: {len(price_data)} symbols | {total_dates} dates")

        # State
        cash            = CAPITAL * (1 - GOLD_HEDGE_WEIGHT - CASH_BUFFER)
        positions: Dict = {}   # {symbol: {entry_price, quantity, stop, atr, entry_date, prob}}
        nav_history     = [CAPITAL]
        trades: List[BacktestTrade] = []
        total_costs     = 0
        dd_monitor      = DrawdownMonitor(CAPITAL)

        # Walk-forward folds
        first_oos = TRAIN_WINDOW_DAYS
        model     = DirectionModel()

        # Precompute regime history
        regime_hist = {}
        for i in range(20, total_dates):
            try:
                state = self.regime_detector.detect(
                    nifty_close.iloc[:i+1],
                    india_vix.iloc[:i+1],
                    fii_flows.iloc[:i+1],
                    str(all_dates[i])
                )
                regime_hist[str(all_dates[i])] = state
            except Exception:
                pass

        # Main simulation loop
        for day_idx in range(first_oos, total_dates):
            today_str = str(all_dates[day_idx])
            regime    = regime_hist.get(today_str)

            # ── Retrain model every OOS_WINDOW_DAYS ──────────────────────────
            if (day_idx - first_oos) % OOS_WINDOW_DAYS == 0:
                train_dates = all_dates[max(0, day_idx - TRAIN_WINDOW_DAYS): day_idx]
                train_data  = feature_df[feature_df.index.isin(train_dates)]
                if len(train_data) >= 1000:
                    log.info(f"[BT] Fold retrain at {today_str} ({len(train_data):,} samples)")
                    try:
                        model.train(train_data, run_wf_cv=False)   # no CV in BT loops
                    except Exception as e:
                        log.warning(f"[BT] Train error: {e}")

            # ── Get today's price data ────────────────────────────────────────
            today_prices = {}
            for sym, df in price_data.items():
                if today_str in df.index.astype(str).values:
                    row = df[df.index.astype(str) == today_str]
                    if not row.empty:
                        today_prices[sym] = row.iloc[0].to_dict()

            # ── Drawdown check ────────────────────────────────────────────────
            equity_val = sum(
                today_prices.get(sym, {}).get("close", p["entry_price"]) * p["quantity"]
                for sym, p in positions.items()
                if sym in today_prices
            )
            nav = cash + equity_val + (CAPITAL * GOLD_HEDGE_WEIGHT)
            dd_state, dd_pct = dd_monitor.update(nav, today_str)

            # ── Check stops & trails for open positions ───────────────────────
            for sym in list(positions.keys()):
                pos = positions[sym]
                if sym not in today_prices:
                    continue
                close = today_prices[sym]["close"]
                low   = today_prices[sym].get("low", close * 0.99)
                atr   = today_prices[sym].get("atr_14", pos["atr"])

                # Intra-day stop hit check (use low of day)
                hit_price = None
                if low <= pos["stop"]:
                    hit_price = pos["stop"]   # assume filled at stop

                if hit_price:
                    gross = (hit_price - pos["entry_price"]) * pos["quantity"]
                    buy_cost  = compute_transaction_cost(pos["entry_price"] * pos["quantity"], "BUY")
                    sell_cost = compute_transaction_cost(hit_price * pos["quantity"], "SELL")
                    net = gross - buy_cost["total_cost"] - sell_cost["total_cost"]
                    total_costs += buy_cost["total_cost"] + sell_cost["total_cost"]
                    cash += hit_price * pos["quantity"] - sell_cost["total_cost"]
                    trades.append(BacktestTrade(
                        symbol      = sym,
                        entry_date  = pos["entry_date"],
                        exit_date   = today_str,
                        entry_price = pos["entry_price"],
                        exit_price  = hit_price,
                        quantity    = pos["quantity"],
                        gross_pnl   = gross,
                        net_pnl     = net,
                        hold_days   = (pd.Timestamp(today_str) - pd.Timestamp(pos["entry_date"])).days,
                        exit_reason = "STOP_HIT",
                        model_prob  = pos["prob"],
                        regime      = regime.regime.value if regime else "UNKNOWN"
                    ))
                    del positions[sym]
                    continue

                # Update trailing stop
                update = self.trail_engine.update(
                    sym, pos["entry_price"], close, pos["stop"], atr
                )
                if update.action != "NO_CHANGE":
                    positions[sym]["stop"] = update.new_stop

            # ── Score and enter new positions ─────────────────────────────────
            if model._trained and regime and regime.allow_new_longs:
                today_features = feature_df[feature_df.index.astype(str) == today_str]
                if not today_features.empty:
                    try:
                        scores = model.predict(today_features)
                        scores = scores.sort_values("model_prob", ascending=False)

                        for _, row in scores.iterrows():
                            sym       = row["symbol"]
                            model_prob= float(row["model_prob"])

                            if sym in positions or sym not in today_prices:
                                continue
                            if len(positions) >= MAX_POSITIONS:
                                break

                            # Drawdown gate
                            if dd_state == DrawdownState.CASH_MODE:
                                break

                            pdata  = today_prices[sym]
                            close  = pdata.get("close", 0)
                            ema50  = pdata.get("ema_50", close)
                            ema200 = pdata.get("ema_200", close)
                            rsi    = pdata.get("rsi_14", 60)
                            atr    = pdata.get("atr_14", close * 0.02)
                            swing  = pdata.get("swing_low_20d", close * 0.92)
                            avg_vol= pdata.get("avg_volume_20d", 0)

                            # Entry filters
                            if model_prob < MIN_MODEL_PROB: continue
                            if close <= ema50:   continue
                            if close <= ema200:  continue
                            if not (RSI_LOW <= rsi <= RSI_HIGH): continue
                            if avg_vol < 100000: continue

                            # Position sizing
                            size = self.position_sizer.compute(
                                symbol            = sym,
                                entry_price       = close,
                                atr_14            = atr,
                                swing_low_20d     = swing,
                                available_capital = cash,
                                drawdown_state    = dd_state,
                            )

                            if size.shares <= 0:
                                continue

                            # Slippage on entry
                            fill_price = close * (1 + 0.001)   # 10bps slippage
                            cost = compute_transaction_cost(fill_price * size.shares, "BUY")
                            total_cost = fill_price * size.shares + cost["total_cost"]

                            if total_cost > cash:
                                continue

                            cash -= total_cost
                            total_costs += cost["total_cost"]
                            positions[sym] = {
                                "entry_price": fill_price,
                                "quantity":    size.shares,
                                "stop":        size.initial_stop,
                                "atr":         atr,
                                "entry_date":  today_str,
                                "prob":        model_prob
                            }
                    except Exception as e:
                        log.debug(f"[BT] Scoring error {today_str}: {e}")

            # ── Record daily NAV ──────────────────────────────────────────────
            equity_val = sum(
                today_prices.get(sym, {}).get("close", p["entry_price"]) * p["quantity"]
                for sym, p in positions.items()
            )
            nav = cash + equity_val + (CAPITAL * GOLD_HEDGE_WEIGHT)
            nav_history.append(nav)

        # ── Compute final metrics ─────────────────────────────────────────────
        results = self._compute_metrics(
            nav_history, trades, total_costs,
            str(all_dates[first_oos]), str(all_dates[-1])
        )
        results.nav_history = nav_history
        results.trades      = [t.to_dict() for t in trades]

        log.info(f"[BT] Complete | Sharpe={results.sharpe:.2f} | "
                 f"MaxDD={results.max_drawdown:.2%} | "
                 f"WinRate={results.win_rate:.2%} | "
                 f"Trades={results.total_trades}")

        return results

    def _compute_metrics(
        self,
        nav_history: List[float],
        trades: List[BacktestTrade],
        total_costs: float,
        start: str,
        end: str
    ) -> BacktestResults:
        navs    = np.array(nav_history)
        returns = np.diff(navs) / navs[:-1]
        n_days  = len(navs)

        # Return metrics
        total_ret  = (navs[-1] - CAPITAL) / CAPITAL
        ann_ret    = (1 + total_ret) ** (252 / n_days) - 1

        # Risk-adjusted
        rf_daily   = (1.065) ** (1/252) - 1
        excess     = returns - rf_daily
        sharpe     = (np.mean(excess) / (np.std(excess) + 1e-10)) * np.sqrt(252)
        downside   = excess[excess < 0]
        sortino    = (np.mean(excess) / (np.std(downside) + 1e-10)) * np.sqrt(252) if len(downside) else 0

        # Drawdown
        peak    = np.maximum.accumulate(navs)
        dd      = (navs - peak) / (peak + 1e-10)
        max_dd  = float(np.min(dd))
        calmar  = ann_ret / (abs(max_dd) + 1e-10)

        # Trade stats
        pnls       = [t.net_pnl for t in trades]
        winners    = [p for p in pnls if p > 0]
        losers     = [p for p in pnls if p < 0]
        win_rate   = len(winners) / len(pnls) if pnls else 0
        avg_win    = np.mean(winners) if winners else 0
        avg_loss   = np.mean(losers) if losers else 0
        expectancy = win_rate * avg_win + (1-win_rate) * avg_loss
        pf         = sum(winners) / (abs(sum(losers)) + 1e-10) if losers else 99

        return BacktestResults(
            start_date      = start,
            end_date        = end,
            total_days      = n_days,
            total_trades    = len(trades),
            winning_trades  = len(winners),
            losing_trades   = len(losers),
            win_rate        = round(win_rate, 4),
            avg_win         = round(float(avg_win), 2),
            avg_loss        = round(float(avg_loss), 2),
            expectancy      = round(float(expectancy), 2),
            total_return    = round(total_ret, 4),
            ann_return      = round(ann_ret, 4),
            sharpe          = round(float(sharpe), 4),
            sortino         = round(float(sortino), 4),
            max_drawdown    = round(max_dd, 4),
            calmar          = round(float(calmar), 4),
            profit_factor   = round(float(pf), 4),
            total_costs_inr = round(total_costs, 2),
        )

    def print_results(self, results: BacktestResults):
        r = results
        eligible = (r.sharpe >= 1.0 and abs(r.max_drawdown) <= 0.20)
        print(f"""
╔══════════════════════════════════════════════════════════════╗
║              BACKTEST RESULTS SUMMARY                        ║
║              {r.start_date} → {r.end_date}              ║
╠══════════════════════════════════════════════════════════════╣
║  Total Return:     {r.total_return*100:>8.2f}%   Ann Return: {r.ann_return*100:>7.2f}%    ║
║  Sharpe Ratio:     {r.sharpe:>8.4f}   Sortino:    {r.sortino:>7.4f}    ║
║  Max Drawdown:    -{abs(r.max_drawdown)*100:>8.2f}%   Calmar:     {r.calmar:>7.4f}    ║
║  Profit Factor:    {r.profit_factor:>8.4f}                            ║
╠══════════════════════════════════════════════════════════════╣
║  Total Trades:     {r.total_trades:>8}                            ║
║  Win Rate:         {r.win_rate*100:>8.2f}%   Expectancy: ₹{r.expectancy:>8.2f}   ║
║  Avg Win:         ₹{r.avg_win:>8.2f}   Avg Loss:  ₹{r.avg_loss:>8.2f}   ║
║  Total Costs:     ₹{r.total_costs_inr:>10,.2f}                      ║
╠══════════════════════════════════════════════════════════════╣
║  Go-Live Eligible: {'✅ YES — Sharpe≥1 & MaxDD<20%' if eligible else '❌ NO — Does not meet thresholds'}   ║
╚══════════════════════════════════════════════════════════════╝""")

        if not eligible:
            if r.sharpe < 1.0:
                print(f"  → Sharpe {r.sharpe:.2f} below required 1.0")
            if abs(r.max_drawdown) > 0.20:
                print(f"  → Max DD {abs(r.max_drawdown)*100:.1f}% above 20% limit")
