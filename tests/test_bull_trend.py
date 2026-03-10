#!/usr/bin/env python3
"""
BULL TREND BACKTEST TESTER (Python version)

Replaces test_bull_trend.sh
Runs full pipeline:
1. Backup original script
2. Generate synthetic bull market
3. Run backtest
4. Analyze metrics
5. Display sample trades
6. Compare regimes
7. Save reports
"""

import os
import sys
import json
import shutil
import argparse
from pathlib import Path
from datetime import datetime

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from portfolio_manager import PortfolioManager
from data.features import FEATURE_COLS
from config.settings import CAPITAL


# -------------------------------------------------------------------
# Utility printing
# -------------------------------------------------------------------

def banner(title):
    print("\n" + "═" * 70)
    print(title.center(70))
    print("═" * 70)


# -------------------------------------------------------------------
# Step 1 Backup script
# -------------------------------------------------------------------

def backup_original():
    banner("STEP 1 - BACKUP ORIGINAL SCRIPT")

    src = Path("scripts/paper_backtest_walkforward.py")
    dst = Path("scripts/paper_backtest_walkforward.py.backup")

    if not src.exists():
        print("Original script not found, skipping backup.")
        return

    if not dst.exists():
        shutil.copy(src, dst)
        print("✓ Backup created")
    else:
        print("✓ Backup already exists")


# -------------------------------------------------------------------
# Synthetic bull market generator
# -------------------------------------------------------------------

def generate_bull_market(n_symbols=80, n_days=30, seed=42):

    np.random.seed(seed)

    dates = pd.date_range("2025-06-01", periods=n_days, freq="B")
    symbols = [f"STOCK_{i:03d}" for i in range(n_symbols)]

    nifty_returns = np.random.normal(0.0008, 0.006, n_days)
    nifty_close = pd.Series(18000 * np.cumprod(1 + nifty_returns), index=dates)

    india_vix = pd.Series(np.clip(12 + np.random.randn(n_days), 12, 18), index=dates)

    fii_flows = pd.Series(np.random.normal(2000, 1500, n_days), index=dates)

    print(f"Nifty: {nifty_close.iloc[0]:.0f} → {nifty_close.iloc[-1]:.0f}")

    eod_by_date = {}
    feature_rows = []

    for idx, d in enumerate(dates):

        eod_by_date[str(d.date())] = {}

        for sym in symbols:

            base = 500 + np.random.randint(0, 2000)

            close = base * (1 + nifty_returns[idx]) ** (idx + 1)

            eod_by_date[str(d.date())][sym] = {
                "close": close,
                "open": close * 0.999,
                "high": close * 1.005,
                "low": close * 0.995,
                "volume": int(abs(np.random.normal(1_500_000, 500_000))),
                "ema_50": close * 0.97,
                "ema_200": close * 0.92,
                "rsi_14": np.random.uniform(55, 70),
                "atr_14": close * 0.02,
                "swing_low_20d": close * 0.95,
                "avg_volume_20d": 1_500_000,
                "avg_value_20d": close * 1_500_000,
            }

            feature_rows.append(
                {
                    "date": d,
                    "symbol": sym,
                    "close": close,
                    **{col: np.random.randn() for col in FEATURE_COLS},
                    "target": np.random.randint(0, 2),
                }
            )

    feature_df = pd.DataFrame(feature_rows).set_index("date")

    return eod_by_date, nifty_close, india_vix, fii_flows, feature_df


# -------------------------------------------------------------------
# Run backtest
# -------------------------------------------------------------------

def run_backtest(eod, nifty, vix, fii, features):

    pm = PortfolioManager()

    nav_history = []
    trades = []

    for date_str in sorted(eod.keys()):

        df = features[features.index == pd.Timestamp(date_str)]

        if df.empty:
            df = pd.DataFrame({"symbol": list(eod[date_str].keys())})
            df.index = pd.DatetimeIndex([pd.Timestamp(date_str)] * len(df))

        report = pm.run_eod(eod[date_str], nifty, vix, fii, df, date_str)

        nav_history.append(
            {
                "date": date_str,
                "nav": report["portfolio_summary"]["nav"],
                "positions": report["portfolio_summary"]["open_positions"],
                "entries": report["decisions"]["entries_today"],
                "regime": report["regime"]["regime"],
            }
        )

        trades.extend(report["decisions"]["entries"])
        trades.extend(report["decisions"]["exits"])

        pm.save_state()

    return pd.DataFrame(nav_history), trades


# -------------------------------------------------------------------
# Metrics
# -------------------------------------------------------------------

def compute_metrics(nav_df, trades):

    start_nav = CAPITAL
    end_nav = nav_df["nav"].iloc[-1]

    total_return = (end_nav - start_nav) / start_nav * 100

    exits = [t for t in trades if t.get("type") == "exit"]

    wins = [t for t in exits if t.get("realised_pnl", 0) > 0]

    win_rate = len(wins) / len(exits) * 100 if exits else 0

    return {
        "total_return_pct": total_return,
        "win_rate_pct": win_rate,
        "total_trades": len(trades),
        "ending_nav": end_nav,
    }


# -------------------------------------------------------------------
# Sample trades
# -------------------------------------------------------------------

def show_sample_trades(trades):

    entries = [t for t in trades if t.get("type") == "entry"]

    print("\nSample Entry Trades:")

    for t in entries[:5]:

        print(
            f"{t.get('symbol')}  qty={t.get('quantity')}  price={t.get('price'):.2f}"
        )


# -------------------------------------------------------------------
# MAIN
# -------------------------------------------------------------------

def main():

    parser = argparse.ArgumentParser()

    parser.add_argument("--days", type=int, default=30)
    parser.add_argument("--symbols", type=int, default=80)
    parser.add_argument("--seed", type=int, default=42)

    args = parser.parse_args()

    banner("BULL TREND BACKTEST")

    backup_original()

    banner("GENERATING SYNTHETIC BULL MARKET")

    eod, nifty, vix, fii, features = generate_bull_market(
        args.symbols, args.days, args.seed
    )

    banner("RUNNING BACKTEST")

    nav_df, trades = run_backtest(eod, nifty, vix, fii, features)

    banner("COMPUTING METRICS")

    metrics = compute_metrics(nav_df, trades)

    print(json.dumps(metrics, indent=2))

    os.makedirs("reporting", exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    nav_df.to_csv(f"reporting/bull_nav_{timestamp}.csv", index=False)

    with open(f"reporting/bull_trades_{timestamp}.json", "w") as f:
        json.dump(trades, f, indent=2)

    show_sample_trades(trades)

    banner("TEST COMPLETE")


if __name__ == "__main__":
    main()