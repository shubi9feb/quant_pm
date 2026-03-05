"""
=============================================================================
REGIME DETECTOR — Nifty EMA + India VIX + FII Flow Classifier
=============================================================================
Classifies the market into one of four regimes each day:
  BULL_TREND  → full allocation, all entries enabled
  SIDEWAYS    → reduced allocation, tighter filters
  BEAR_TREND  → hedged, only high-conviction entries
  CRISIS      → cash heavy, no new longs

The regime gates which signals from the direction model are actionable.
"""

import numpy as np
import pandas as pd
from enum import Enum
from dataclasses import dataclass
from typing import Tuple
import logging

log = logging.getLogger("regime_detector")


class MarketRegime(Enum):
    BULL_TREND  = "BULL_TREND"    # All systems go
    SIDEWAYS    = "SIDEWAYS"      # Cautious, reduce size
    BEAR_TREND  = "BEAR_TREND"    # Hedged, minimal new longs
    CRISIS      = "CRISIS"        # Capital preservation mode


@dataclass
class RegimeState:
    regime:          MarketRegime
    nifty_trend:     str           # "ABOVE_EMA" | "BELOW_EMA"
    vix_level:       float
    vix_signal:      str           # "LOW_FEAR" | "MODERATE" | "HIGH_FEAR" | "EXTREME_FEAR"
    fii_flow_signal: str           # "STRONG_BUY" | "NEUTRAL" | "SELLING"
    score:           float         # composite score 0–1 (higher = more bullish)
    allocation_mult: float         # multiply target allocations by this
    allow_new_longs: bool
    date:            str

    def to_dict(self) -> dict:
        return {
            "regime":          self.regime.value,
            "nifty_trend":     self.nifty_trend,
            "vix_level":       round(self.vix_level, 2),
            "vix_signal":      self.vix_signal,
            "fii_flow_signal": self.fii_flow_signal,
            "score":           round(self.score, 4),
            "allocation_mult": self.allocation_mult,
            "allow_new_longs": self.allow_new_longs,
            "date":            self.date
        }


class RegimeDetector:
    """
    Three-factor market regime classifier.

    Factor 1 – Nifty EMA trend (trend direction)
    Factor 2 – India VIX level (fear/risk appetite)
    Factor 3 – FII net flows 20-day momentum (institutional appetite)
    """

    def __init__(
        self,
        nifty_ema_period:    int   = 20,
        vix_bull_threshold:  float = 15.0,
        vix_bear_threshold:  float = 25.0,
        vix_crisis_threshold:float = 35.0,
        fii_lookback:        int   = 20,
        fii_bull_percentile: float = 60,
        fii_bear_percentile: float = 40,
    ):
        self.nifty_ema_period     = nifty_ema_period
        self.vix_bull_threshold   = vix_bull_threshold
        self.vix_bear_threshold   = vix_bear_threshold
        self.vix_crisis_threshold = vix_crisis_threshold
        self.fii_lookback         = fii_lookback
        self.fii_bull_percentile  = fii_bull_percentile
        self.fii_bear_percentile  = fii_bear_percentile

    def _score_nifty_trend(self, nifty: pd.Series) -> Tuple[str, float]:
        """
        Returns trend label and score (0–1) based on price vs EMA and slope.
        """
        ema = nifty.ewm(span=self.nifty_ema_period, adjust=False).mean()
        current_price = nifty.iloc[-1]
        current_ema   = ema.iloc[-1]

        # EMA slope over last 5 days (guard for short series)
        if len(ema) >= 5:
            ema_slope = (ema.iloc[-1] - ema.iloc[-5]) / (ema.iloc[-5] + 1e-10)
        else:
            ema_slope = 0.0

        above = current_price > current_ema
        trend_label = "ABOVE_EMA" if above else "BELOW_EMA"

        # Score: above EMA + positive slope = max bullish
        if above and ema_slope > 0.002:
            score = 1.0
        elif above and ema_slope >= 0:
            score = 0.7
        elif above and ema_slope < 0:
            score = 0.5   # price above EMA but EMA weakening
        elif not above and ema_slope < 0:
            score = 0.0
        else:
            score = 0.2   # below EMA but slope turning up

        return trend_label, score

    def _score_vix(self, vix_current: float) -> Tuple[str, float]:
        """
        Returns VIX signal label and fear score (0=extreme fear, 1=low fear).
        """
        if vix_current < self.vix_bull_threshold:
            return "LOW_FEAR", 1.0
        elif vix_current < self.vix_bear_threshold:
            # Linear interpolation between thresholds
            score = 1.0 - ((vix_current - self.vix_bull_threshold) /
                           (self.vix_bear_threshold - self.vix_bull_threshold)) * 0.7
            return "MODERATE", score
        elif vix_current < self.vix_crisis_threshold:
            score = 0.3 - ((vix_current - self.vix_bear_threshold) /
                           (self.vix_crisis_threshold - self.vix_bear_threshold)) * 0.3
            return "HIGH_FEAR", score
        else:
            return "EXTREME_FEAR", 0.0

    def _score_fii_flows(self, fii_net: pd.Series) -> Tuple[str, float]:
        """
        FII net flows: rolling sum vs historical percentile.
        Positive and above 60th percentile = institutional buying.
        """
        roll_sum = fii_net.rolling(self.fii_lookback).sum()
        if roll_sum.empty or roll_sum.isna().all():
            return "NEUTRAL", 0.5

        current  = roll_sum.iloc[-1]
        hist     = roll_sum.dropna()

        pct_rank = (hist < current).mean()   # percentile rank 0–1

        if pct_rank >= self.fii_bull_percentile / 100:
            return "STRONG_BUY", 1.0
        elif pct_rank >= 0.5:
            return "NEUTRAL_BUY", 0.65
        elif pct_rank >= self.fii_bear_percentile / 100:
            return "NEUTRAL_SELL", 0.35
        else:
            return "SELLING", 0.0

    def detect(
        self,
        nifty_close: pd.Series,
        india_vix:   pd.Series,
        fii_net_flows: pd.Series,
        date: str = None
    ) -> RegimeState:
        """
        Main detection function. Call once per trading day after EOD.

        Args:
            nifty_close   : Nifty 50 daily close prices (DatetimeIndex)
            india_vix     : India VIX daily close (DatetimeIndex)
            fii_net_flows : Daily FII net cash market flows in ₹cr (DatetimeIndex)
            date          : Date string for logging (default: last index)

        Returns:
            RegimeState with regime classification and metadata
        """
        if date is None:
            date = str(nifty_close.index[-1].date() if hasattr(nifty_close.index[-1], 'date') else nifty_close.index[-1])

        # ── Score each factor ────────────────────────────────────────────────
        nifty_label, nifty_score = self._score_nifty_trend(nifty_close)
        vix_current              = float(india_vix.iloc[-1])
        vix_label,   vix_score   = self._score_vix(vix_current)
        fii_label,   fii_score   = self._score_fii_flows(fii_net_flows)

        # ── Composite score (weighted average) ───────────────────────────────
        # Trend is most important, VIX next, flows informational
        composite = 0.45 * nifty_score + 0.35 * vix_score + 0.20 * fii_score

        # ── Classify regime ──────────────────────────────────────────────────
        if vix_label == "EXTREME_FEAR":
            regime          = MarketRegime.CRISIS
            allocation_mult = 0.25
            allow_new_longs = False

        elif composite >= 0.70:
            regime          = MarketRegime.BULL_TREND
            allocation_mult = 1.0
            allow_new_longs = True

        elif composite >= 0.45:
            regime          = MarketRegime.SIDEWAYS
            allocation_mult = 0.70
            allow_new_longs = True   # but higher prob threshold enforced in model

        elif composite >= 0.25:
            regime          = MarketRegime.BEAR_TREND
            allocation_mult = 0.40
            allow_new_longs = False  # only hold existing, no new longs

        else:
            regime          = MarketRegime.CRISIS
            allocation_mult = 0.25
            allow_new_longs = False

        state = RegimeState(
            regime          = regime,
            nifty_trend     = nifty_label,
            vix_level       = vix_current,
            vix_signal      = vix_label,
            fii_flow_signal = fii_label,
            score           = composite,
            allocation_mult = allocation_mult,
            allow_new_longs = allow_new_longs,
            date            = date
        )

        log.info(f"[REGIME] {date} → {regime.value} | score={composite:.3f} | "
                 f"VIX={vix_current:.1f} | alloc_mult={allocation_mult}")

        return state

    def history(
        self,
        nifty_close:   pd.Series,
        india_vix:     pd.Series,
        fii_net_flows: pd.Series
    ) -> pd.DataFrame:
        """
        Compute regime history over all dates in the series.
        Useful for backtest and visualization.
        """
        records = []
        min_len = 250  # need enough history for EMA

        for i in range(min_len, len(nifty_close)):
            state = self.detect(
                nifty_close.iloc[:i+1],
                india_vix.iloc[:i+1],
                fii_net_flows.iloc[:i+1],
                date=str(nifty_close.index[i].date())
            )
            records.append(state.to_dict())

        return pd.DataFrame(records).set_index("date")


# ─────────────────────────────────────────────────────────────────────────────
# REGIME-ADJUSTED ENTRY GATE
# ─────────────────────────────────────────────────────────────────────────────

def regime_adjusted_min_prob(regime: MarketRegime, base_prob: float = 0.65) -> float:
    """
    In sideways/bear markets, raise the minimum model probability threshold
    to enforce higher conviction before entering new positions.
    """
    adjustments = {
        MarketRegime.BULL_TREND:  0.00,    # base threshold unchanged
        MarketRegime.SIDEWAYS:    +0.05,   # require 0.70+
        MarketRegime.BEAR_TREND:  +0.10,   # require 0.75+ (mostly unused, longs blocked)
        MarketRegime.CRISIS:      +0.15,   # require 0.80+ (longs blocked anyway)
    }
    return base_prob + adjustments.get(regime, 0.0)
