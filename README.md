# QuantPM — Autonomous Quantitative Portfolio Manager
### Indian Equities | Cash Only | No Derivatives
**Version:** `xgb_v1.0.0` | **Status:** Paper Trading Mode

---

## Architecture Overview

```
┌─────────────────────────────────────────────────────────────────────┐
│                      DAILY EOD CYCLE                                │
│                                                                     │
│  EOD Prices ──► Feature Pipeline ──► Regime Detector               │
│                       │                    │                        │
│                       ▼                    ▼                        │
│              XGBoost Direction      Market State                    │
│                  Model Scores      (BULL/SIDE/BEAR/CRISIS)          │
│                       │                    │                        │
│                       └────────┬───────────┘                        │
│                                ▼                                    │
│                        Entry Filter Gate                            │
│                   (prob ≥ 0.65, EMA50/200, RSI,                    │
│                    liquidity, regime, drawdown)                     │
│                                │                                    │
│                                ▼                                    │
│                       Position Sizer                                │
│                   (1.5% risk, max 15%/stock)                        │
│                                │                                    │
│                                ▼                                    │
│               ┌────────────────────────────┐                       │
│               │   Broker Adapter           │                       │
│               │ [PAPER] or [LIVE:Zerodha]  │                       │
│               │  Idempotent bracket orders │                       │
│               └────────────────────────────┘                       │
│                                │                                    │
│                                ▼                                    │
│         Trailing Stop Engine ──► Drawdown Monitor                  │
│                                │                                    │
│                                ▼                                    │
│      ┌──────────────────────────────────────────┐                  │
│      │  Audit Writer (hash-chained JSONL)       │                  │
│      │  Daily JSON Report                       │                  │
│      └──────────────────────────────────────────┘                  │
└─────────────────────────────────────────────────────────────────────┘
```

---

## Directory Structure

```
quant_pm/
├── config/
│   └── settings.py          ← ALL parameters (immutable risk rules)
├── data/
│   └── features.py          ← EOD ingestion, 21 technical features, z-scoring
├── core/
│   ├── regime.py            ← Nifty EMA + VIX + FII flow regime detector
│   └── backtester.py        ← Walk-forward backtest with Indian market costs
├── models/
│   └── direction_model.py   ← XGBoost + walk-forward CV + calibration
├── risk/
│   └── engine.py            ← Entry filter, position sizer, trailing stops,
│                               drawdown monitor, transaction costs
├── execution/
│   └── broker.py            ← Bracket orders, idempotent IDs, paper/live adapters
├── audit/
│   └── logger.py            ← Tamper-evident hash-chained JSONL audit logs
├── reporting/
│   └── daily_report.py      ← Daily JSON report, performance tracker
├── tests/
│   └── test_system.py       ← Full test suite (risk rules, sizing, audit, filters)
├── portfolio_manager.py     ← Main orchestrator — daily run loop
└── requirements.txt
```

---

## Risk Rules (Immutable)

| Parameter | Value | Location |
|---|---|---|
| Capital | ₹1,00,000 | `CAPITAL` |
| Risk per trade | 1.5% (₹1,500) | `RISK_PER_TRADE` |
| Max positions | 8 | `MAX_POSITIONS` |
| Max per stock | 15% | `MAX_PER_STOCK` |
| Gold hedge | 10% | `GOLD_HEDGE_WEIGHT` |
| Cash buffer | 10% | `CASH_BUFFER` |
| Initial stop | max(2×ATR14, swing_low_20d, 8%) | `ATR_MULTIPLIER_STOP` |
| Breakeven trigger | +5% gain | `BREAKEVEN_TRIGGER` |
| Trail trigger | +10% gain, 2×ATR | `TRAIL_START / TRAIL_MULTIPLIER` |
| Reduce buys at | 12% drawdown | `DRAWDOWN_REDUCE_THRESHOLD` |
| Cash mode at | 18% drawdown | `DRAWDOWN_CASH_THRESHOLD` |

---

## Entry Conditions (ALL must pass)

```
✓  Model probability ≥ 0.65 (regime-adjusted: +5% sideways, +10% bear)
✓  Price > 50-day EMA
✓  Price > 200-day EMA
✓  RSI(14) between 50 and 70
✓  Avg Volume 20d ≥ 5,00,000 shares
✓  Avg Daily Value 20d ≥ ₹50,00,000
✓  Regime allows new longs (BULL/SIDEWAYS only)
✓  Drawdown state not CASH_MODE
✓  Open positions < 8
```

---

## Regime States

| State | VIX | Nifty | FII | Alloc Mult | New Longs |
|---|---|---|---|---|---|
| BULL_TREND | < 15 | Above EMA, rising | Buying | 1.00 | ✅ |
| SIDEWAYS | 15–25 | Mixed | Neutral | 0.70 | ✅ (prob +5%) |
| BEAR_TREND | 25–35 | Below EMA | Selling | 0.40 | ❌ |
| CRISIS | > 35 | — | — | 0.25 | ❌ |

---

## Drawdown Mitigation (Automated)

```
NAV ≥ peak - 12%  →  NORMAL       (full operation)
NAV ≥ peak - 18%  →  REDUCED_BUYS (new position risk halved)
NAV < peak - 18%  →  CASH_MODE    (no new longs, hold/exit only)
```

---

## Features (21 Cross-Sectionally Z-Scored)

| Category | Features |
|---|---|
| Trend | `price_to_ema50`, `price_to_ema200`, `ema50_to_200` |
| Momentum | `ret_5d`, `ret_10d`, `ret_20d`, `ret_60d`, `rsi_14`, `rsi_28` |
| MACD | `macd`, `macd_sig`, `macd_hist`, `macd_cross` |
| Volatility | `atr_pct`, `vol_20d`, `bb_pctb`, `bb_bw` |
| Trend Strength | `adx_14` |
| Volume | `vol_ratio_20d`, `obv_normalized` |
| Gap Risk | `gap_pct` |

---

## Transaction Cost Model (Indian Equities)

| Cost | Rate | Side |
|---|---|---|
| Brokerage | ₹20 or 0.03% (lower) | Both |
| STT | 0.1% | Sell only |
| NSE Exchange | 0.00325% | Both |
| SEBI | 0.0001% | Both |
| Stamp Duty | 0.015% | Buy only |
| GST on brokerage | 18% | Both |
| Slippage | 10 bps | Both |
| **Round-trip total** | **~0.37%** | |

---

## Paper-to-Live Gate

Paper trading minimum **6 months** required before going live.

| Metric | Required | Purpose |
|---|---|---|
| Sharpe Ratio | ≥ 1.0 | Risk-adjusted return quality |
| Max Drawdown | ≤ 20% | Capital preservation |
| Paper period | ≥ 6 months | Statistical significance |
| Expectancy | > 0 | Positive edge documented |

Set `PAPER_MODE=false` + `BROKER_API_KEY` in environment to activate live trading.

---

## Audit System

Every decision is recorded in tamper-evident JSONL format:
- **Hash chaining**: each record includes SHA256 of prior record
- **Immutable**: append-only, never modified after write
- **Verifiable**: `AuditVerifier.verify_date("2024-02-14")` detects tampering
- **Complete trail**: signal → filter → size → order → fill → exit

---

## Daily JSON Report Structure

```json
{
  "report_date":       "2024-02-14",
  "model_version":     "xgb_v1.0.0",
  "paper_mode":        true,
  "regime":            {"regime": "BULL_TREND", "vix_level": 12.5, ...},
  "portfolio_summary": {"nav": 103500, "total_pnl": 3500, "open_positions": 5},
  "positions":         [...],
  "decisions": {
    "entries_today":   2,
    "exits_today":     1,
    "rejected_today":  8,
    "entries":         [...],
    "exits":           [...],
    "rejected_signals":[...]
  },
  "orders_placed":     [...],
  "risk_status":       {"drawdown_state": "NORMAL", "current_drawdown": 0.023},
  "top_signals":       [...top 10 model scores...],
  "performance":       {"total_return_pct": 3.5, "sharpe_ratio": 1.24},
  "last_audit_hash":   "a3f9..."
}
```

---

## Quick Start

```bash
# 1. Install dependencies
pip install -r requirements.txt

# 2. Train the model (requires historical data)
python -c "
from portfolio_manager import PortfolioManager
pm = PortfolioManager()
results = pm.train_model(your_feature_df)
"

# 3. Run daily EOD cycle (paper mode)
python portfolio_manager.py

# 4. Verify audit chain
python -c "
from audit.logger import AuditVerifier
print(AuditVerifier.verify_date('2024-02-14'))
"

# 5. Check go-live eligibility after 6+ months
python -c "
from reporting.daily_report import PerformanceTracker
# ... after recording daily NAVs
tracker.print_performance()
"
```

---

## Key Design Decisions

**Walk-forward CV (no lookahead):** XGBoost is retrained every 6 months on a
rolling 2-year window, with OOS metrics reported from the holdout period only.
In-sample fitting never sees OOS data.

**Idempotent orders:** Client order IDs are SHA256 hashes of
`(symbol, side, qty, price, date)`. Submitting the same order twice is safe —
the broker adapter returns the existing order rather than creating a duplicate.

**Regime-gated entries:** Regime detection acts as a top-level filter before
model scores are even considered. In BEAR/CRISIS regimes, no new longs are
permitted regardless of model probability.

**1:3 risk/reward target:** Bracket orders are placed with a 3×risk profit
target. If target is not reached, the trailing stop ratchets up at +10% gain.

**Indian cost accuracy:** STT (sell-side), stamp duty (buy-side), NSE
transaction charges, SEBI turnover charges, GST on brokerage, and 10bps
slippage are all modelled explicitly, yielding ~37bps round-trip.
