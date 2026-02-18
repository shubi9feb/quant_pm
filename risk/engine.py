"""
=============================================================================
RISK ENGINE — Position Sizing, Stop Loss, Drawdown Monitoring
=============================================================================
All risk rules are enforced here. The engine is the single source of truth
for position sizes, stop prices, and drawdown mitigations.
"""

import numpy as np
import pandas as pd
from dataclasses import dataclass, field, asdict
from typing import Optional, Dict, List, Tuple
from enum import Enum
import logging
from datetime import datetime

from config.settings import (
    CAPITAL, RISK_PER_TRADE, MAX_POSITIONS, MAX_PER_STOCK,
    GOLD_HEDGE_WEIGHT, CASH_BUFFER,
    ATR_PERIOD, ATR_MULTIPLIER_STOP, HARD_STOP_PCT,
    BREAKEVEN_TRIGGER, TRAIL_START, TRAIL_MULTIPLIER,
    DRAWDOWN_REDUCE_THRESHOLD, DRAWDOWN_CASH_THRESHOLD,
    MIN_MODEL_PROB, RSI_LOW, RSI_HIGH,
    MIN_AVG_VOLUME_20D, MIN_AVG_VALUE_20D,
    EMA_SHORT, EMA_LONG, RISK_LIMITS
)
from core.regime import MarketRegime, RegimeState, regime_adjusted_min_prob

log = logging.getLogger("risk_engine")


# ─────────────────────────────────────────────────────────────────────────────
# DATA CLASSES
# ─────────────────────────────────────────────────────────────────────────────

class DrawdownState(Enum):
    NORMAL            = "NORMAL"           # < 12% DD — full operation
    REDUCED_BUYS      = "REDUCED_BUYS"     # 12-18% DD — new buys halved
    CASH_MODE         = "CASH_MODE"        # > 18% DD — 50% cash, minimal new longs


@dataclass
class PositionSizeResult:
    symbol:          str
    shares:          int
    position_value:  float
    risk_amount:     float        # ₹ at risk (entry – stop) × shares
    entry_price:     float
    initial_stop:    float
    stop_reason:     str          # "ATR" | "SWING_LOW" | "HARD_CAP"
    pct_of_capital:  float
    sizing_notes:    str

    def to_dict(self) -> dict:
        return {k: (round(v, 4) if isinstance(v, float) else v)
                for k, v in asdict(self).items()}


@dataclass
class EntryDecision:
    symbol:         str
    allowed:        bool
    reject_reasons: List[str] = field(default_factory=list)
    pass_checks:    List[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class TrailingStopUpdate:
    symbol:           str
    old_stop:         float
    new_stop:         float
    action:           str     # "BREAKEVEN" | "TRAIL" | "NO_CHANGE"
    gain_pct:         float
    current_atr:      float

    def to_dict(self) -> dict:
        return {k: (round(v, 4) if isinstance(v, float) else v)
                for k, v in asdict(self).items()}


# ─────────────────────────────────────────────────────────────────────────────
# ENTRY FILTER ENGINE
# ─────────────────────────────────────────────────────────────────────────────

class EntryFilter:
    """
    Multi-condition gate that must be fully passed before any long entry.
    Each condition is independently logged for audit.
    """

    def check(
        self,
        symbol:          str,
        close:           float,
        ema_50:          float,
        ema_200:         float,
        rsi:             float,
        model_prob:      float,
        avg_volume_20d:  float,
        avg_value_20d:   float,
        regime_state:    RegimeState,
        current_positions: int,
        drawdown_state:  DrawdownState,
        model_min_prob:  float = MIN_MODEL_PROB
    ) -> EntryDecision:
        """
        Run all entry checks. Returns EntryDecision with detailed reasons.
        """
        reject_reasons = []
        pass_checks    = []

        # ── Regime gate ─────────────────────────────────────────────────────
        if not regime_state.allow_new_longs:
            reject_reasons.append(f"REGIME_BLOCK: regime={regime_state.regime.value} blocks new longs")
        else:
            pass_checks.append(f"REGIME_OK: {regime_state.regime.value}")

        # ── Drawdown gate ───────────────────────────────────────────────────
        if drawdown_state == DrawdownState.CASH_MODE:
            reject_reasons.append("DRAWDOWN_CASH_MODE: >18% drawdown, no new buys")
        elif drawdown_state == DrawdownState.REDUCED_BUYS:
            pass_checks.append("DRAWDOWN_REDUCED: 12-18% DD, will halve size")
        else:
            pass_checks.append("DRAWDOWN_NORMAL")

        # ── Capacity gate ────────────────────────────────────────────────────
        if current_positions >= MAX_POSITIONS:
            reject_reasons.append(f"MAX_POSITIONS: {current_positions}/{MAX_POSITIONS} slots filled")
        else:
            pass_checks.append(f"CAPACITY_OK: {current_positions}/{MAX_POSITIONS}")

        # ── Model probability ────────────────────────────────────────────────
        adjusted_min_prob = regime_adjusted_min_prob(regime_state.regime, model_min_prob)
        if model_prob < adjusted_min_prob:
            reject_reasons.append(
                f"MODEL_PROB: {model_prob:.3f} < {adjusted_min_prob:.3f} "
                f"(regime-adjusted from {model_min_prob:.3f})"
            )
        else:
            pass_checks.append(f"MODEL_PROB_OK: {model_prob:.3f} ≥ {adjusted_min_prob:.3f}")

        # ── Price above key EMAs ─────────────────────────────────────────────
        if close <= ema_50:
            reject_reasons.append(f"BELOW_EMA50: {close:.2f} ≤ {ema_50:.2f}")
        else:
            pass_checks.append(f"ABOVE_EMA50: {close:.2f} > {ema_50:.2f}")

        if close <= ema_200:
            reject_reasons.append(f"BELOW_EMA200: {close:.2f} ≤ {ema_200:.2f}")
        else:
            pass_checks.append(f"ABOVE_EMA200: {close:.2f} > {ema_200:.2f}")

        # ── RSI momentum band ────────────────────────────────────────────────
        if not (RSI_LOW <= rsi <= RSI_HIGH):
            reject_reasons.append(f"RSI_BAND: {rsi:.1f} not in [{RSI_LOW}–{RSI_HIGH}]")
        else:
            pass_checks.append(f"RSI_OK: {rsi:.1f} in [{RSI_LOW}–{RSI_HIGH}]")

        # ── Liquidity ────────────────────────────────────────────────────────
        if avg_volume_20d < MIN_AVG_VOLUME_20D:
            reject_reasons.append(
                f"LOW_VOLUME: avg_vol_20d={avg_volume_20d:,.0f} < {MIN_AVG_VOLUME_20D:,.0f}"
            )
        else:
            pass_checks.append(f"VOLUME_OK: {avg_volume_20d:,.0f}")

        if avg_value_20d < MIN_AVG_VALUE_20D:
            reject_reasons.append(
                f"LOW_VALUE: avg_val_20d=₹{avg_value_20d:,.0f} < ₹{MIN_AVG_VALUE_20D:,.0f}"
            )
        else:
            pass_checks.append(f"VALUE_OK: ₹{avg_value_20d:,.0f}")

        allowed = len(reject_reasons) == 0
        return EntryDecision(symbol=symbol, allowed=allowed,
                             reject_reasons=reject_reasons, pass_checks=pass_checks)


# ─────────────────────────────────────────────────────────────────────────────
# POSITION SIZING ENGINE
# ─────────────────────────────────────────────────────────────────────────────

class PositionSizer:
    """
    Fixed-fractional risk-based position sizing.
    Position size derived from: risk_amount / (entry - stop)
    """

    def compute(
        self,
        symbol:          str,
        entry_price:     float,
        atr_14:          float,
        swing_low_20d:   float,
        available_capital: float,
        drawdown_state:  DrawdownState,
        existing_value:  float = 0.0,   # current value of any existing position in this stock
    ) -> PositionSizeResult:
        """
        Compute shares to buy with 3-way stop determination.

        Stop = max(2×ATR, last swing low, entry × (1 - 8%))
        Risk = entry - stop
        Shares = risk_amount / risk_per_share, then clip to capital limits.
        """
        base_capital = CAPITAL

        # ── Compute initial stop price ───────────────────────────────────────
        stop_atr       = entry_price - (ATR_MULTIPLIER_STOP * atr_14)
        stop_swing_low = swing_low_20d
        stop_hard_cap  = entry_price * (1 - HARD_STOP_PCT)

        # Take the highest (tightest) of all three — gives widest stop in INR terms
        initial_stop = max(stop_atr, stop_swing_low, stop_hard_cap)
        initial_stop = min(initial_stop, entry_price * 0.999)  # never above entry

        # ── Identify binding stop reason ─────────────────────────────────────
        if initial_stop == stop_hard_cap:
            stop_reason = "HARD_CAP_8PCT"
        elif initial_stop == stop_swing_low:
            stop_reason = "SWING_LOW"
        else:
            stop_reason = "ATR_2X"

        # ── Risk per share ────────────────────────────────────────────────────
        risk_per_share = entry_price - initial_stop
        if risk_per_share <= 0:
            return PositionSizeResult(
                symbol=symbol, shares=0, position_value=0,
                risk_amount=0, entry_price=entry_price,
                initial_stop=initial_stop, stop_reason="ZERO_RISK",
                pct_of_capital=0, sizing_notes="Risk per share ≤ 0, skip"
            )

        # ── Base risk amount ──────────────────────────────────────────────────
        risk_inr = base_capital * RISK_PER_TRADE   # e.g. ₹1,500

        # ── Drawdown adjustment ───────────────────────────────────────────────
        if drawdown_state == DrawdownState.REDUCED_BUYS:
            risk_inr *= 0.50
            sizing_notes = "DRAWDOWN_REDUCED: risk halved (12-18% DD)"
        else:
            sizing_notes = "NORMAL_RISK"

        # ── Compute raw shares ────────────────────────────────────────────────
        raw_shares  = risk_inr / risk_per_share
        raw_value   = raw_shares * entry_price

        # ── Cap at max_per_stock ──────────────────────────────────────────────
        max_value   = base_capital * MAX_PER_STOCK - existing_value
        if raw_value > max_value:
            raw_value  = max(0, max_value)
            raw_shares = raw_value / entry_price
            sizing_notes += f" | CAPPED_MAX_STOCK(15%): ₹{max_value:,.0f}"

        # ── Cap at available capital ──────────────────────────────────────────
        investable   = available_capital * (1 - CASH_BUFFER - GOLD_HEDGE_WEIGHT)
        if raw_value > investable:
            raw_value  = investable
            raw_shares = raw_value / entry_price
            sizing_notes += f" | CAPPED_AVAILABLE: ₹{investable:,.0f}"

        # ── Round down to whole shares ────────────────────────────────────────
        shares       = max(0, int(raw_shares))
        final_value  = shares * entry_price
        actual_risk  = shares * risk_per_share
        pct_capital  = final_value / base_capital

        return PositionSizeResult(
            symbol         = symbol,
            shares         = shares,
            position_value = round(final_value, 2),
            risk_amount    = round(actual_risk, 2),
            entry_price    = round(entry_price, 2),
            initial_stop   = round(initial_stop, 2),
            stop_reason    = stop_reason,
            pct_of_capital = round(pct_capital, 4),
            sizing_notes   = sizing_notes
        )


# ─────────────────────────────────────────────────────────────────────────────
# TRAILING STOP ENGINE
# ─────────────────────────────────────────────────────────────────────────────

class TrailingStopEngine:
    """
    Manages stop adjustments for open positions:
    +5% gain  → move stop to breakeven (entry price)
    +10% gain → begin trailing at 2×ATR below current price
    """

    def update(
        self,
        symbol:       str,
        entry_price:  float,
        current_price:float,
        current_stop: float,
        current_atr:  float
    ) -> TrailingStopUpdate:
        """
        Compute updated stop price for an open position.
        Stop can only ever INCREASE (ratchet up).
        """
        gain_pct  = (current_price - entry_price) / (entry_price + 1e-10)
        new_stop  = current_stop
        action    = "NO_CHANGE"

        if gain_pct >= TRAIL_START:
            # Active trailing: 2×ATR below current price
            trail_stop = current_price - (TRAIL_MULTIPLIER * current_atr)
            if trail_stop > current_stop:
                new_stop = trail_stop
                action   = "TRAIL"

        elif gain_pct >= BREAKEVEN_TRIGGER:
            # Move to breakeven
            if entry_price > current_stop:
                new_stop = entry_price
                action   = "BREAKEVEN"

        # Ratchet: stop can never decrease
        new_stop = max(new_stop, current_stop)

        return TrailingStopUpdate(
            symbol       = symbol,
            old_stop     = round(current_stop, 2),
            new_stop     = round(new_stop, 2),
            action       = action,
            gain_pct     = round(gain_pct, 4),
            current_atr  = round(current_atr, 2)
        )


# ─────────────────────────────────────────────────────────────────────────────
# DRAWDOWN MONITOR
# ─────────────────────────────────────────────────────────────────────────────

class DrawdownMonitor:
    """
    Tracks peak NAV and enforces automated drawdown mitigations.
    State machine: NORMAL → REDUCED_BUYS → CASH_MODE
    """

    def __init__(self, starting_capital: float = CAPITAL):
        self.peak_nav      = starting_capital
        self.starting_nav  = starting_capital
        self._history: List[Dict] = []

    def update(self, current_nav: float, date: str) -> Tuple[DrawdownState, float]:
        """
        Update peak and compute current drawdown state.

        Returns (DrawdownState, current_drawdown_pct)
        """
        if current_nav > self.peak_nav:
            self.peak_nav = current_nav

        drawdown = (self.peak_nav - current_nav) / (self.peak_nav + 1e-10)

        if drawdown >= DRAWDOWN_CASH_THRESHOLD:
            state = DrawdownState.CASH_MODE
        elif drawdown >= DRAWDOWN_REDUCE_THRESHOLD:
            state = DrawdownState.REDUCED_BUYS
        else:
            state = DrawdownState.NORMAL

        record = {
            "date":       date,
            "nav":        round(current_nav, 2),
            "peak_nav":   round(self.peak_nav, 2),
            "drawdown":   round(drawdown, 4),
            "dd_state":   state.value
        }
        self._history.append(record)

        if state != DrawdownState.NORMAL:
            log.warning(f"[DD] {date}: {state.value} | drawdown={drawdown:.2%} | "
                        f"NAV=₹{current_nav:,.0f} | peak=₹{self.peak_nav:,.0f}")

        return state, drawdown

    def max_drawdown(self) -> float:
        if not self._history:
            return 0.0
        return max(r["drawdown"] for r in self._history)

    def history_df(self) -> pd.DataFrame:
        return pd.DataFrame(self._history)


# ─────────────────────────────────────────────────────────────────────────────
# TRANSACTION COST MODEL (Indian market specific)
# ─────────────────────────────────────────────────────────────────────────────

def compute_transaction_cost(
    trade_value: float,
    side:        str      # "BUY" | "SELL"
) -> dict:
    """
    Full Indian equity delivery transaction cost breakdown.
    All charges in INR.
    """
    from config.settings import (
        BROKERAGE_PCT, STT_DELIVERY, EXCHANGE_TXN_CHARGES,
        SEBI_CHARGES, STAMP_DUTY, SLIPPAGE_BPS
    )

    brokerage   = min(trade_value * BROKERAGE_PCT, 20)   # Zerodha: lower of % or ₹20
    stt         = trade_value * STT_DELIVERY if side == "SELL" else 0   # STT on sell only
    exchange    = trade_value * EXCHANGE_TXN_CHARGES
    sebi        = trade_value * SEBI_CHARGES
    stamp       = trade_value * STAMP_DUTY if side == "BUY" else 0       # stamp on buy only
    slippage    = trade_value * (SLIPPAGE_BPS / 10000)
    gst_on_brok = brokerage * 0.18

    total = brokerage + stt + exchange + sebi + stamp + slippage + gst_on_brok

    return {
        "side":         side,
        "trade_value":  round(trade_value, 2),
        "brokerage":    round(brokerage, 2),
        "stt":          round(stt, 2),
        "exchange":     round(exchange, 2),
        "sebi":         round(sebi, 2),
        "stamp":        round(stamp, 2),
        "gst":          round(gst_on_brok, 2),
        "slippage":     round(slippage, 2),
        "total_cost":   round(total, 2),
        "cost_pct":     round(total / (trade_value + 1e-10), 5)
    }
