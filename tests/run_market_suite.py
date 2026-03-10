#!/usr/bin/env python3
"""
run_market_suite.py

Runs multiple synthetic market-regime backtests (bull, bear, sideways),
collects results, and saves CSV + plots.

Save as: tests/run_market_suite.py
Run from project root:
    python tests/run_market_suite.py
Examples:
    python tests/run_market_suite.py --days 90 --symbols 120 --scenarios bull,bear
    python tests/run_market_suite.py --plot False
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
import matplotlib.pyplot as plt

# ensure project root on path (script lives in project/tests/)
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# project imports - fail fast with useful messages
try:
    from portfolio_manager import PortfolioManager
except Exception as exc:
    raise ImportError("Cannot import portfolio_manager.py. Run from project root. " + str(exc))

try:
    from data.features import FEATURE_COLS
except Exception as exc:
    raise ImportError("Cannot import data.features.FEATURE_COLS. " + str(exc))

try:
    from config.settings import CAPITAL
except Exception as exc:
    raise ImportError("Cannot import config.settings.CAPITAL. " + str(exc))

# logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("market_suite")

# -------------------------
# deterministic seed helper
# -------------------------
def stable_32bit_seed(key: str, idx: int, base: int = 42) -> int:
    h = hashlib.sha256(key.encode("utf-8")).digest()
    return (base + int.from_bytes(h[:4], "big") + idx) % (2**32)

# -------------------------
# market scenario generator
# -------------------------
def generate_market_scenario(
    scenario: str,
    n_days: int,
    n_symbols: int,
    start_date: str,
    seed: int = 42,
) -> Tuple[pd.Series, pd.Series, pd.Series, List[str], pd.DatetimeIndex]:
    rng = np.random.default_rng(seed)
    dates = pd.date_range(start_date, periods=n_days, freq="B")
    symbols = [f"STOCK_{i:03d}" for i in range(n_symbols)]

    if scenario == "bull":
        drift_mu = 0.001
        drift_sigma = 0.005
        vix_low, vix_high = 12.0, 15.0
        fii_mu, fii_sigma = 2500.0, 1000.0
    elif scenario == "bear":
        # negative drift, higher volatility and selling FII flows
        drift_mu = -0.0015
        drift_sigma = 0.008
        vix_low, vix_high = 20.0, 35.0
        fii_mu, fii_sigma = -1500.0, 1200.0
    elif scenario == "sideways":
        drift_mu = 0.0
        drift_sigma = 0.006
        vix_low, vix_high = 14.0, 20.0
        fii_mu, fii_sigma = 0.0, 800.0
    else:
        raise ValueError("Unknown scenario: " + scenario)

    returns = rng.normal(drift_mu, drift_sigma, n_days)
    base_index = 18000.0
    nifty_close = pd.Series(base_index * np.cumprod(1 + returns), index=dates)
    india_vix = pd.Series(rng.uniform(vix_low, vix_high, n_days), index=dates)
    fii_flows = pd.Series(rng.normal(fii_mu, fii_sigma, n_days), index=dates)

    logger.info("Scenario %s: %s -> %s (%.2f%%)", scenario,
                int(nifty_close.iloc[0]), int(nifty_close.iloc[-1]),
                (nifty_close.iloc[-1] / nifty_close.iloc[0] - 1) * 100)
    return nifty_close, india_vix, fii_flows, symbols, dates

# -------------------------
# eod & features generation
# -------------------------
def generate_eod_and_features(
    symbols: List[str],
    dates: pd.DatetimeIndex,
    nifty_pct: pd.Series,
    seed_base: int = 42,
    feature_cols: List[str] | None = None,
) -> Tuple[Dict[str, Dict[str, dict]], pd.DataFrame]:
    feature_cols = feature_cols or FEATURE_COLS
    eod_by_date: Dict[str, Dict[str, dict]] = {}
    feature_rows: List[dict] = []

    for idx, curr_date in enumerate(dates):
        date_key = str(curr_date.date())
        eod_by_date[date_key] = {}
        for sym in symbols:
            seed = stable_32bit_seed(sym, idx, base=seed_base)
            rng = np.random.default_rng(seed)

            base_price = 400 + rng.integers(0, 2500)
            drift = float(nifty_pct.iloc[idx]) if idx < len(nifty_pct) else 0.0
            close = base_price * (1 + drift * rng.uniform(0.85, 1.25)) ** (idx + 1)

            eod_by_date[date_key][sym] = {
                "close": float(close),
                "open": float(close * 0.999),
                "high": float(close * 1.005),
                "low": float(close * 0.995),
                "volume": int(rng.uniform(800_000, 2_500_000)),
                "ema_50": float(close * 0.97),
                "ema_200": float(close * 0.92),
                "rsi_14": float(rng.uniform(40, 70)),
                "atr_14": float(close * 0.02),
                "swing_low_20d": float(close * 0.95),
                "avg_volume_20d": 1_200_000,
                "avg_value_20d": float(close * 1_200_000),
            }

            feat = {"date": curr_date, "symbol": sym, "close": float(close), "target": 1}
            for c in feature_cols:
                feat[c] = float(rng.normal())
            feature_rows.append(feat)

    feature_df = pd.DataFrame(feature_rows).set_index("date")
    return eod_by_date, feature_df

# -------------------------
# run backtest
# -------------------------
def run_backtest(
    pm: PortfolioManager,
    eod_by_date: Dict[str, Dict[str, dict]],
    nifty_close: pd.Series,
    india_vix: pd.Series,
    fii_flows: pd.Series,
    feature_df: pd.DataFrame,
) -> Tuple[pd.DataFrame, List[dict]]:
    records = []
    trades: List[dict] = []

    for date_str in sorted(eod_by_date.keys()):
        features = feature_df[feature_df.index == pd.Timestamp(date_str)]
        if features.empty:
            symbols = list(eod_by_date[date_str].keys())
            features = pd.DataFrame({"symbol": symbols})
            features.index = pd.DatetimeIndex([pd.Timestamp(date_str)] * len(symbols))

        report = pm.run_eod(eod_by_date[date_str], nifty_close, india_vix, fii_flows, features, date_str)
        nav = report.get("portfolio_summary", {}).get("nav", float("nan"))
        regime = report.get("regime", {}).get("regime", "unknown")
        entries_today = report.get("decisions", {}).get("entries_today", 0)
        entries = report.get("decisions", {}).get("entries", [])
        exits = report.get("decisions", {}).get("exits", [])
        positions = report.get("portfolio_summary", {}).get("open_positions", {})

        records.append({"date": date_str, "nav": nav, "regime": regime, "entries_today": entries_today, "positions": positions})
        if isinstance(entries, list):
            trades.extend(entries)
        if isinstance(exits, list):
            trades.extend(exits)

    nav_df = pd.DataFrame(records)
    return nav_df, trades

# -------------------------
# plotting utilities
# -------------------------
def plot_nav(nav_df: pd.DataFrame, out_path: Path, title: str = "NAV over time"):
    plt.figure()
    x = pd.to_datetime(nav_df["date"])
    y = nav_df["nav"]
    plt.plot(x, y)
    plt.title(title)
    plt.xlabel("Date")
    plt.ylabel("NAV")
    plt.grid(True)
    plt.tight_layout()
    plt.savefig(out_path)
    plt.close()

def plot_entries(nav_df: pd.DataFrame, out_path: Path, title: str = "Entries per day"):
    plt.figure()
    x = pd.to_datetime(nav_df["date"])
    y = nav_df["entries_today"]
    plt.bar(x, y)
    plt.title(title)
    plt.xlabel("Date")
    plt.ylabel("Entries")
    plt.tight_layout()
    plt.savefig(out_path)
    plt.close()

def plot_regime_distribution(nav_df: pd.DataFrame, out_path: Path, title: str = "Regime distribution"):
    plt.figure()
    counts = nav_df["regime"].value_counts()
    counts.plot(kind="bar")
    plt.title(title)
    plt.xlabel("Regime")
    plt.ylabel("Days")
    plt.tight_layout()
    plt.savefig(out_path)
    plt.close()

# -------------------------
# runner / CLI
# -------------------------
def parse_args():
    p = argparse.ArgumentParser(description="Multi-regime backtest suite with plotting")
    p.add_argument("--scenarios", type=str, default="bull", help="Comma list: bull,bear,sideways")
    p.add_argument("--days", "-d", type=int, default=30)
    p.add_argument("--symbols", "-s", type=int, default=80)
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--start", type=str, default="2025-06-01")
    p.add_argument("--out", type=str, default="reporting")
    p.add_argument("--plot", type=lambda s: s.lower() in ("1", "true", "yes"), default=True, help="Produce PNG plots (true/false)")
    return p.parse_args()

def main():
    args = parse_args()
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    scenarios = [s.strip() for s in args.scenarios.split(",") if s.strip()]
    logger.info("Running scenarios: %s", scenarios)

    summary = []

    for scenario in scenarios:
        nifty_close, india_vix, fii_flows, symbols, dates = generate_market_scenario(
            scenario, args.days, args.symbols, args.start, seed=args.seed
        )

        eod_by_date, feature_df = generate_eod_and_features(
            symbols, dates, nifty_close.pct_change().fillna(0), seed_base=args.seed
        )

        pm = PortfolioManager()  # new PM instance per scenario
        nav_df, trades = run_backtest(pm, eod_by_date, nifty_close, india_vix, fii_flows, feature_df)

        # Save CSV & trades JSON
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        csv_path = out_dir / f"{scenario}_nav_{timestamp}.csv"
        json_path = out_dir / f"{scenario}_trades_{timestamp}.json"
        nav_df.to_csv(csv_path, index=False)
        with json_path.open("w", encoding="utf-8") as fh:
            json.dump(trades, fh, default=str, indent=2)

        # Plots
        if args.plot and not nav_df.empty:
            plot_nav(nav_df, out_dir / f"{scenario}_nav_{timestamp}.png", title=f"NAV - {scenario}")
            plot_entries(nav_df, out_dir / f"{scenario}_entries_{timestamp}.png", title=f"Entries - {scenario}")
            plot_regime_distribution(nav_df, out_dir / f"{scenario}_regime_{timestamp}.png", title=f"Regime - {scenario}")

        # summary stats
        final_nav = nav_df["nav"].iloc[-1] if not nav_df.empty else float("nan")
        total_entries = int(nav_df["entries_today"].sum()) if "entries_today" in nav_df.columns else 0
        regime_counts = nav_df["regime"].value_counts().to_dict() if "regime" in nav_df.columns else {}
        summary.append({
            "scenario": scenario,
            "csv": str(csv_path),
            "trades": str(json_path),
            "final_nav": final_nav,
            "total_entries": total_entries,
            "regime_counts": regime_counts
        })

        logger.info("Scenario %s complete. Final NAV: %s, Entries: %s", scenario, final_nav, total_entries)

    # print compact summary
    print("\n" + "="*80)
    print("RUN SUMMARY")
    print("="*80)
    for s in summary:
        print(f"Scenario: {s['scenario']}")
        print(f"  CSV:    {s['csv']}")
        print(f"  Trades: {s['trades']}")
        print(f"  Final NAV: {s['final_nav']}")
        print(f"  Entries:   {s['total_entries']}")
        print(f"  Regime counts: {s['regime_counts']}")
        print("-"*80)

if __name__ == "__main__":
    main()