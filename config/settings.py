"""
=============================================================================
QUANTITATIVE PORTFOLIO MANAGER - CONFIGURATION
Indian Equities | Cash Only | No Derivatives
=============================================================================
"""
from dataclasses import dataclass, field
from typing import List, Optional
import os

# ─────────────────────────────────────────────────────────────────────────────
# CAPITAL & RISK PARAMETERS  (IMMUTABLE - change requires code review + audit)
# ─────────────────────────────────────────────────────────────────────────────
CAPITAL              = 100_000        # INR - total deployed capital
RISK_PER_TRADE       = 0.015          # 1.5% per trade = ₹1,500
MAX_POSITIONS        = 8              # maximum simultaneous open positions
MAX_PER_STOCK        = 0.15          # 15% of capital per single stock
GOLD_HEDGE_WEIGHT    = 0.10          # 10% in gold ETF (Sovereign Gold Bond / GOLDBEES)
CASH_BUFFER          = 0.10          # 10% always in cash/liquid funds

# ─────────────────────────────────────────────────────────────────────────────
# ENTRY CONDITIONS
# ─────────────────────────────────────────────────────────────────────────────
MIN_MODEL_PROB       = 0.65          # XGBoost outperformance probability threshold
RSI_LOW              = 50            # RSI momentum lower bound
RSI_HIGH             = 70            # RSI momentum upper bound
MIN_AVG_VOLUME_20D   = 500_000       # minimum 20-day average daily volume (shares)
MIN_AVG_VALUE_20D    = 5_000_000     # minimum 20-day average daily value (INR) for liquidity
EMA_SHORT            = 50            # must be above 50-EMA
EMA_LONG             = 200           # must be above 200-EMA

# ─────────────────────────────────────────────────────────────────────────────
# STOP LOSS & TRAILING RULES
# ─────────────────────────────────────────────────────────────────────────────
ATR_PERIOD           = 14
ATR_MULTIPLIER_STOP  = 2.0           # initial stop = 2×ATR(14)
HARD_STOP_PCT        = 0.08          # 8% hard cap stop-loss
BREAKEVEN_TRIGGER    = 0.05          # move stop to breakeven at +5% gain
TRAIL_START          = 0.10          # start trailing at +10% gain
TRAIL_MULTIPLIER     = 2.0           # trailing = 2×ATR

# ─────────────────────────────────────────────────────────────────────────────
# DRAWDOWN MITIGATIONS (AUTOMATED)
# ─────────────────────────────────────────────────────────────────────────────
DRAWDOWN_REDUCE_THRESHOLD = 0.12     # at 12% DD: reduce new buys by 50%
DRAWDOWN_CASH_THRESHOLD   = 0.18     # at 18% DD: move 50% to cash

# ─────────────────────────────────────────────────────────────────────────────
# REGIME DETECTOR
# ─────────────────────────────────────────────────────────────────────────────
NIFTY_EMA_PERIOD     = 20            # Nifty trend EMA
VIX_BULL_THRESHOLD   = 15            # VIX < 15 → low fear (bullish)
VIX_BEAR_THRESHOLD   = 25            # VIX > 25 → high fear (bearish)
FII_FLOW_LOOKBACK    = 20            # days for FII flow momentum

# ─────────────────────────────────────────────────────────────────────────────
# MODEL PARAMETERS
# ─────────────────────────────────────────────────────────────────────────────
PREDICTION_HORIZON   = 30            # days forward for outperformance prediction
MODEL_VERSION        = "xgb_v1.0.0"
WALK_FORWARD_FOLDS   = 5
TRAIN_WINDOW_DAYS    = 504           # ~2 years training
OOS_WINDOW_DAYS      = 126           # ~6 months OOS per fold

# ─────────────────────────────────────────────────────────────────────────────
# REBALANCING SCHEDULE
# ─────────────────────────────────────────────────────────────────────────────
REBALANCE_DAY        = "Friday"      # weekly rebalance on Fridays
FUNDAMENTAL_RESCORE_DAY = 1         # monthly re-score on 1st trading day of month

# ─────────────────────────────────────────────────────────────────────────────
# EXECUTION & BROKERAGE
# ─────────────────────────────────────────────────────────────────────────────
SLIPPAGE_BPS         = 10            # 10 bps slippage assumption
BROKERAGE_PCT        = 0.0003        # 0.03% per leg (Zerodha flat ₹20 or %)
STT_DELIVERY         = 0.001         # 0.1% STT on delivery sell side
EXCHANGE_TXN_CHARGES = 0.0000325     # NSE transaction charge
SEBI_CHARGES         = 0.000001      # SEBI turnover charges
STAMP_DUTY           = 0.00015       # 0.015% on buy side

# ─────────────────────────────────────────────────────────────────────────────
# PAPER TRADING GATE
# ─────────────────────────────────────────────────────────────────────────────
PAPER_TRADE_MONTHS   = 6             # minimum paper trading before live
GO_LIVE_SHARPE_MIN   = 1.0          # Sharpe ratio threshold
GO_LIVE_MAX_DD       = 0.20         # max drawdown threshold (backtest + OOS)

# ─────────────────────────────────────────────────────────────────────────────
# UNIVERSE
# ─────────────────────────────────────────────────────────────────────────────
NIFTY_500_URL        = "https://www1.nseindia.com/content/indices/ind_nifty500list.csv"
GOLD_ETF_SYMBOL      = "GOLDBEES"    # NSE symbol for gold ETF hedge

# ─────────────────────────────────────────────────────────────────────────────
# PATHS
# ─────────────────────────────────────────────────────────────────────────────
DATA_DIR             = "data/market"
AUDIT_DIR            = "audit/logs"
MODEL_DIR            = "models/saved"
REPORT_DIR           = "reporting/daily"

# ─────────────────────────────────────────────────────────────────────────────
# BROKER ADAPTER (environment-driven)
# ─────────────────────────────────────────────────────────────────────────────
BROKER_API_KEY       = os.getenv("BROKER_API_KEY", "PAPER_MODE")
BROKER_API_SECRET    = os.getenv("BROKER_API_SECRET", "PAPER_MODE")
PAPER_MODE           = os.getenv("PAPER_MODE", "true").lower() == "true"

@dataclass
class RiskLimits:
    """Immutable risk parameters validated at startup."""
    capital: float              = CAPITAL
    risk_per_trade: float       = RISK_PER_TRADE
    max_positions: int          = MAX_POSITIONS
    max_per_stock: float        = MAX_PER_STOCK
    gold_hedge: float           = GOLD_HEDGE_WEIGHT
    cash_buffer: float          = CASH_BUFFER
    hard_stop_pct: float        = HARD_STOP_PCT
    drawdown_reduce_at: float   = DRAWDOWN_REDUCE_THRESHOLD
    drawdown_cash_at: float     = DRAWDOWN_CASH_THRESHOLD

    def validate(self):
        assert self.cash_buffer + self.gold_hedge < 1.0, "Buffer+hedge must be < 100%"
        assert self.max_positions > 0
        assert 0 < self.risk_per_trade < 0.05, "Risk per trade must be 0–5%"
        assert 0 < self.hard_stop_pct <= 0.10
        assert self.drawdown_reduce_at < self.drawdown_cash_at
        return self

RISK_LIMITS = RiskLimits().validate()
