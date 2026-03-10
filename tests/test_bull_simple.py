################################################################################
# SIMPLE BULL TREND TESTER (Python version)
################################################################################

import sys
import os
from pathlib import Path
from datetime import datetime

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np
import pandas as pd
from portfolio_manager import PortfolioManager
from data.features import FEATURE_COLS
from config.settings import CAPITAL


print("════════════════════════════════════════════════════════════════")
print("  BULL TREND BACKTEST - Testing Favorable Market Conditions")
print("════════════════════════════════════════════════════════════════")
print()

# Parameters (same as bash arguments)
DAYS = int(sys.argv[1]) if len(sys.argv) > 1 else 30
SYMBOLS = int(sys.argv[2]) if len(sys.argv) > 2 else 80

print(f"Parameters: {DAYS} days, {SYMBOLS} symbols")
print()

# Step 1: Clean state
print("[1/3] Cleaning old state...")
for f in ["portfolio_state.json", "order_book.jsonl"]:
    if os.path.exists(f):
        os.remove(f)
print("✓ State cleaned")

print("\n[2/3] Generating BULL market...")

SEED = 42
np.random.seed(SEED)

dates = pd.date_range("2025-06-01", periods=DAYS, freq="B")
symbols = [f"STOCK_{i:03d}" for i in range(SYMBOLS)]

# Bull market conditions
nifty_returns = np.random.normal(0.001, 0.005, DAYS)
nifty_close = pd.Series(18000 * np.cumprod(1 + nifty_returns), index=dates)

india_vix = pd.Series(np.random.uniform(12, 15, DAYS), index=dates)
fii_flows = pd.Series(np.random.normal(2500, 1000, DAYS), index=dates)

print(f"Nifty: {nifty_close.iloc[0]:.0f} → {nifty_close.iloc[-1]:.0f} "
      f"(+{(nifty_close.iloc[-1]/nifty_close.iloc[0]-1)*100:.1f}%)")
print(f"VIX avg: {india_vix.mean():.1f}")
print(f"FII avg: +₹{fii_flows.mean():.0f}cr")

# Generate EOD data
eod_by_date = {}
all_features = []

for idx, curr_date in enumerate(dates):
    eod_by_date[str(curr_date.date())] = {}

    for sym in symbols:
        seed = (SEED + (hash(sym) & 0xffffffff) + idx) % (2**32)
        rng = np.random.default_rng(seed)

        base = 500 + rng.integers(0, 2000)
        close = base * (1 + nifty_returns[idx] * rng.uniform(0.9, 1.2)) ** (idx + 1)

        eod_by_date[str(curr_date.date())][sym] = {
            "close": close,
            "open": close * 0.999,
            "high": close * 1.005,
            "low": close * 0.995,
            "volume": int(rng.uniform(1_000_000, 2_000_000)),
            "ema_50": close * 0.97,
            "ema_200": close * 0.92,
            "rsi_14": rng.uniform(55, 70),
            "atr_14": close * 0.02,
            "swing_low_20d": close * 0.95,
            "avg_volume_20d": 1_500_000,
            "avg_value_20d": close * 1_500_000,
        }

        all_features.append(
            {
                "date": curr_date,
                "symbol": sym,
                "close": close,
                **{col: np.random.randn() for col in FEATURE_COLS},
                "target": np.random.randint(0, 2),
            }
        )

feature_df = pd.DataFrame(all_features).set_index("date")

print("\n[3/3] Running backtest...")

pm = PortfolioManager()

nav_history = []
all_trades = []

for date_str in sorted(eod_by_date.keys()):
    eod = eod_by_date[date_str]
    features = feature_df[feature_df.index == pd.Timestamp(date_str)]

    if features.empty:
        features = pd.DataFrame([{"symbol": s} for s in eod.keys()])
        features.index = pd.DatetimeIndex([pd.Timestamp(date_str)] * len(features))

    report = pm.run_eod(eod, nifty_close, india_vix, fii_flows, features, date_str)

    nav_history.append(
        {
            "date": date_str,
            "nav": report["portfolio_summary"]["nav"],
            "regime": report["regime"]["regime"],
            "entries": report["decisions"]["entries_today"],
            "positions": report["portfolio_summary"]["open_positions"],
        }
    )

    all_trades.extend(report["decisions"]["entries"])
    all_trades.extend(report["decisions"]["exits"])

# Results
nav_df = pd.DataFrame(nav_history)

final_nav = nav_df["nav"].iloc[-1]
total_return = (final_nav - CAPITAL) / CAPITAL * 100
total_entries = nav_df["entries"].sum()

print("\n" + "=" * 70)
print("RESULTS")
print("=" * 70)
print(f"Starting NAV: ₹{CAPITAL:,.0f}")
print(f"Ending NAV:   ₹{final_nav:,.0f}")
print(f"Return:       {total_return:+.2f}%")
print(f"Entries:      {total_entries}")
print(f"Positions:    {nav_df['positions'].iloc[-1]}")
print("=" * 70)

# Save results
os.makedirs("reporting", exist_ok=True)
timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

csv_path = f"reporting/bull_test_{timestamp}.csv"
nav_df.to_csv(csv_path, index=False)

print(f"\nSaved: {csv_path}")

print("\n" + "=" * 70)
if total_entries > 0:
    print("✅ SUCCESS: System traded in BULL conditions")
else:
    print("⚠️  WARNING: No trades detected")
print("=" * 70)