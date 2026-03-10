#!/usr/bin/env python3
"""
run_bull_backtest.py

Comprehensive BULL trend backtest runner.

Usage:
    # default: 30 business days, 80 symbols
    python tests/run_bull_backtest.py

    # custom
    python tests/run_bull_backtest.py --days 90 --symbols 120 --seed 42 --start 2025-06-01
"""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Tuple

import numpy as np
import pandas as pd

# Ensure project root is on sys.path so imports work when running from project root
# File is assumed to live in: project/tests/run_bull_backtest.py
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# Try importing project modules; provide a helpful error if missing.
try:
    from portfolio_manager import PortfolioManager
except Exception as exc:
    raise ImportError(
        "Failed to import 'portfolio_manager'. Make sure you're running this from the project root "
        "and that portfolio_manager.py exists. Original error: " + str(exc)
    )

try:
    from data.features import FEATURE_COLS
except Exception as exc:
    raise ImportError(
        "Failed to import 'data.features.FEATURE_COLS'. Ensure data/features.py exists. Original error: "
        + str(exc)
    )

try:
    from config.settings import CAPITAL
except Exception as exc:
    raise ImportError(
        "Failed to import 'config.settings.CAPITAL'. Ensure config/settings.py exists and defines CAPITAL. "
        "Original error: "
        + str(exc)
    )

# -------------------------
# Logging
# -------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("bull_backtest")


# -------------------------
# Utility: stable seed
# -------------------------
def stable_32bit_seed(key: str, idx: int, base: int = 42) -> int:
    """
    Produce a stable 32-bit seed for a given symbol string and day index.
    Uses sha256 to ensure cross-machine determinism.
    """
    h = hashlib.sha256(key.encode("utf-8")).digest()
    h_int = int.from_bytes(h[:4], "big")  # first 4 bytes -> 32-bit integer
    return (base + h_int + idx) % (2**32)


# -------------------------
# Market generation
# -------------------------
def generate_bull_market(
    n_days: int,
    n_symbols: int,
    start_date: str = "2025-06-01",
    base_index: float = 18000.0,
    seed: int = 42,
) -> Tuple[pd.Series, pd.Series, pd.Series, List[str], pd.DatetimeIndex]:
    """
    Create synthetic bull market signals:
    - nifty_close: cumulative uptrend series
    - india_vix: low uniform VIX
    - fii_flows: positive buying flows
    - symbols: symbol list
    - dates: business-date index
    """
    rng = np.random.default_rng(seed)
    dates = pd.date_range(start_date, periods=n_days, freq="B")
    symbols = [f"STOCK_{i:03d}" for i in range(n_symbols)]

    nifty_returns = rng.normal(0.001, 0.005, n_days)  # small daily positive drift
    nifty_close = pd.Series(base_index * np.cumprod(1 + nifty_returns), index=dates)

    india_vix = pd.Series(rng.uniform(12.0, 15.0, n_days), index=dates)
    fii_flows = pd.Series(rng.normal(2500.0, 1000.0, n_days), index=dates)

    logger.info(
        "Generated bull market: %s → %s (%+.2f%%)",
        int(nifty_close.iloc[0]),
        int(nifty_close.iloc[-1]),
        (nifty_close.iloc[-1] / nifty_close.iloc[0] - 1) * 100,
    )

    return nifty_close, india_vix, fii_flows, symbols, dates


# -------------------------
# EOD & feature generation
# -------------------------
def generate_eod_and_features(
    symbols: List[str],
    dates: pd.DatetimeIndex,
    nifty_returns: pd.Series,
    seed_base: int = 42,
    use_subset_features: int | None = None,
) -> Tuple[Dict[str, Dict[str, dict]], pd.DataFrame]:
    """
    Generate:
    - eod_by_date: dict mapping 'YYYY-MM-DD' -> {symbol: eod_dict}
    - feature_df: DataFrame indexed by date with rows for each symbol
    """
    eod_by_date: Dict[str, Dict[str, dict]] = {}
    features_rows: List[dict] = []

    for idx, curr_date in enumerate(dates):
        date_key = str(curr_date.date())
        eod_by_date[date_key] = {}

        for sym in symbols:
            seed = stable_32bit_seed(sym, idx, base=seed_base)
            rng = np.random.default_rng(seed)

            # base price chosen deterministically-ish but varied by symbol & date
            base_price = 500 + rng.integers(0, 2000)
            # embed some of the market drift into the close
            drift = float(nifty_returns.iloc[idx]) if idx < len(nifty_returns) else 0.0
            # magnify drift slightly across days
            close = base_price * (1 + drift * rng.uniform(0.9, 1.2)) ** (idx + 1)

            eod_by_date[date_key][sym] = {
                "close": float(close),
                "open": float(close * 0.999),
                "high": float(close * 1.005),
                "low": float(close * 0.995),
                "volume": int(rng.uniform(1_000_000, 2_000_000)),
                "ema_50": float(close * 0.97),
                "ema_200": float(close * 0.92),
                "rsi_14": float(rng.uniform(55, 70)),
                "atr_14": float(close * 0.02),
                "swing_low_20d": float(close * 0.95),
                "avg_volume_20d": 1_500_000,
                "avg_value_20d": float(close * 1_500_000),
            }

            # Feature row: include FEATURE_COLS plus a few diagnostic cols
            feature_row = {
                "date": curr_date,
                "symbol": sym,
                "close": float(close),
                # target is 1 in bullish regime (you can change this if needed)
                "target": 1,
            }

            # attach features; if FEATURE_COLS is large we optionally sample subset
            if use_subset_features and use_subset_features < len(FEATURE_COLS):
                chosen = FEATURE_COLS[:use_subset_features]
            else:
                chosen = FEATURE_COLS

            for c in chosen:
                feature_row[c] = float(rng.normal())

            features_rows.append(feature_row)

    feature_df = pd.DataFrame(features_rows).set_index("date")
    return eod_by_date, feature_df


# -------------------------
# Running backtest
# -------------------------
def run_backtest_and_collect(
    pm: PortfolioManager,
    eod_by_date: Dict[str, Dict[str, dict]],
    nifty_close: pd.Series,
    india_vix: pd.Series,
    fii_flows: pd.Series,
    feature_df: pd.DataFrame,
) -> Tuple[pd.DataFrame, List[dict]]:
    """
    Loop over dates, call pm.run_eod and collect nav/regime/entries.
    Returns nav_df and list of trade events (entries+exits).
    """
    results = []
    trades = []

    for date_str in sorted(eod_by_date.keys()):
        features = feature_df[feature_df.index == pd.Timestamp(date_str)]
        if features.empty:
            # minimal placeholder features: at least symbol column for pm.run_eod
            symbols = list(eod_by_date[date_str].keys())
            features = pd.DataFrame({"symbol": symbols})
            features.index = pd.DatetimeIndex([pd.Timestamp(date_str)] * len(symbols))

        report = pm.run_eod(eod_by_date[date_str], nifty_close, india_vix, fii_flows, features, date_str)

        # defensive checks - some PM implementations may return nested structures differently
        nav = report.get("portfolio_summary", {}).get("nav", float("nan"))
        regime = report.get("regime", {}).get("regime", "unknown")
        entries_today = report.get("decisions", {}).get("entries_today", 0)
        entries = report.get("decisions", {}).get("entries", [])
        exits = report.get("decisions", {}).get("exits", [])
        positions = report.get("portfolio_summary", {}).get("open_positions", {})

        results.append(
            {
                "date": date_str,
                "nav": nav,
                "regime": regime,
                "entries_today": entries_today,
                "positions": positions,
            }
        )

        # collect trades if present
        if isinstance(entries, list):
            trades.extend(entries)
        if isinstance(exits, list):
            trades.extend(exits)

    nav_df = pd.DataFrame(results)
    return nav_df, trades


# -------------------------
# Save & reporting
# -------------------------
def save_reports(nav_df: pd.DataFrame, trades: List[dict], out_dir: Path) -> Tuple[Path, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    csv_path = out_dir / f"bull_test_nav_{timestamp}.csv"
    json_path = out_dir / f"bull_test_trades_{timestamp}.json"
    nav_df.to_csv(csv_path, index=False)
    with json_path.open("w", encoding="utf-8") as fh:
        json.dump(trades, fh, indent=2, default=str)
    return csv_path, json_path


# -------------------------
# CLI / main
# -------------------------
def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Simple BULL trend backtest runner")
    p.add_argument("--days", "-d", type=int, default=30, help="Number of business days to simulate")
    p.add_argument("--symbols", "-s", type=int, default=80, help="Number of symbols in universe")
    p.add_argument("--seed", type=int, default=42, help="Base RNG seed")
    p.add_argument("--start", type=str, default="2025-06-01", help="Backtest start date (YYYY-MM-DD)")
    p.add_argument("--out", type=str, default="reporting", help="Output directory for CSV/JSON")
    p.add_argument("--features-subset", type=int, default=None, help="If set, use only first N FEATURE_COLS")
    return p.parse_args()


def main():
    args = parse_args()
    logger.info("BULL trend backtest starting (days=%s symbols=%s seed=%s)", args.days, args.symbols, args.seed)

    # Generate market
    nifty_close, india_vix, fii_flows, symbols, dates = generate_bull_market(
        args.days, args.symbols, start_date=args.start, seed=args.seed
    )

    # Generate eod + features
    eod_by_date, feature_df = generate_eod_and_features(
        symbols,
        dates,
        nifty_close.pct_change().fillna(0),
        seed_base=args.seed,
        use_subset_features=args.features_subset,
    )

    # Instantiate portfolio manager
    pm = PortfolioManager()

    # Run backtest
    nav_df, trades = run_backtest_and_collect(pm, eod_by_date, nifty_close, india_vix, fii_flows, feature_df)

    # Summary
    if nav_df.empty:
        logger.warning("No NAV results were produced. Check PortfolioManager.run_eod behavior/returns.")
    else:
        starting_nav = CAPITAL
        final_nav = nav_df["nav"].iloc[-1]
        total_return = (final_nav - starting_nav) / starting_nav * 100 if not pd.isna(final_nav) else float("nan")
        total_entries = int(nav_df["entries_today"].sum()) if "entries_today" in nav_df.columns else 0

        print("\n" + "=" * 70)
        print("RESULTS")
        print("=" * 70)
        print(f"Starting NAV:    ₹{starting_nav:,.0f}")
        print(f"Ending NAV:      ₹{final_nav:,.0f}")
        print(f"Total Return:    {total_return:+.2f}%")
        print(f"Total Entries:   {total_entries}")
        # positions summary safe access
        last_positions = nav_df["positions"].iloc[-1] if "positions" in nav_df.columns else {}
        print(f"Final Positions: {last_positions}")
        print("=" * 70)

        # Regime breakdown
        if "regime" in nav_df.columns:
            print("\nRegime Distribution:")
            for regime, count in nav_df["regime"].value_counts().items():
                print(f"  {regime}: {count} days ({count / len(nav_df) * 100:.0f}%)")

        # Days with entries
        if "entries_today" in nav_df.columns:
            entry_days = nav_df[nav_df["entries_today"] > 0]
            print(f"\nTrading Activity:")
            print(f"  Days with entries: {len(entry_days)}/{len(nav_df)} ({len(entry_days) / len(nav_df) * 100:.0f}%)")

    # Save reports
    out_dir = Path(args.out)
    csv_path, json_path = save_reports(nav_df, trades, out_dir)
    print(f"\nSaved NAV:   {csv_path}")
    print(f"Saved trades:{json_path}")

    # Gate check
    if nav_df.empty or nav_df["entries_today"].sum() == 0:
        print("\n⚠️  WARNING: No trades were recorded. Check regime detection and decision logic.")
    else:
        print("\n✅ SUCCESS: System executed trades in BULL conditions.")

    logger.info("Backtest complete.")


if __name__ == "__main__":
    main()