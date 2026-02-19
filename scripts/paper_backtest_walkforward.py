# scripts/paper_backtest_walkforward.py
"""
Paper backtest driver: iterates date-by-date and calls PortfolioManager.run_eod()
Requirements:
 - A daily feature dataframe with columns: date, symbol, FEATURE_COLS, and optional price fields.
 - A separate per-date eod price mapping is required (close, ema_50, ema_200, rsi_14, atr_14,
   swing_low_20d, avg_volume_20d, avg_value_20d).
If you don't have a clean EOD feed, run the synthetic mode (--synth) to produce a demo backtest.
"""
import argparse
import logging
from pathlib import Path
import pandas as pd
import numpy as np
import json
from datetime import timedelta

ROOT = Path(__file__).resolve().parents[1]
import sys
sys.path.insert(0, str(ROOT))

from portfolio_manager import PortfolioManager
from data.features import FEATURE_COLS

log = logging.getLogger("paper_backtest")
logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s")

def build_synthetic_eod_and_features(n_symbols=150, n_days=365, seed=0):
    np.random.seed(seed)
    dates = pd.date_range(end=pd.Timestamp.today(), periods=n_days, freq="B")
    symbols = [f"S{1000+i}" for i in range(n_symbols)]
    # build simple price series per symbol
    rows_features = []
    eod_by_date = {}
    for d in dates:
        eod_by_date[d.date().isoformat()] = {}
        for s in symbols:
            close = float(100 + np.random.normal(0, 1) + (dates.get_loc(d) * 0.01))
            eod_by_date[d.date().isoformat()][s] = {
                "close": close,
                "ema_50": close * 0.98,
                "ema_200": close * 0.95,
                "rsi_14": float(np.clip(50 + np.random.normal(0, 10), 1, 99)),
                "atr_14": abs(np.random.normal(1.5, 0.5)),
                "swing_low_20d": close * 0.9,
                "avg_volume_20d": int(600_000 + np.random.normal(0, 100_000)),
                "avg_value_20d": int(6_000_000 + np.random.normal(0, 1_000_000)),
            }
            row = {"date": d, "symbol": s}
            for c in FEATURE_COLS:
                row[c] = float(np.random.normal(0, 1))
            row["target_up_5d"] = int(np.random.rand() > 0.985)
            rows_features.append(row)
    feature_df = pd.DataFrame(rows_features)
    return eod_by_date, feature_df

def compute_performance(daily_reports):
    # daily_reports: list of report dicts from pm.run_eod
    # Extract NAV series by date
    navs = pd.Series({r["report_date"]: r["portfolio_summary"]["nav"] for r in daily_reports})
    navs.index = pd.to_datetime(navs.index)
    navs = navs.sort_index()
    returns = navs.pct_change().dropna()
    ann_ret = (1 + returns.mean()) ** 252 - 1
    ann_vol = returns.std() * (252 ** 0.5)
    sharpe = ann_ret / (ann_vol + 1e-12)
    # max drawdown
    running_max = navs.cummax()
    drawdown = (navs - running_max) / running_max
    max_dd = drawdown.min()
    return {"ann_return": ann_ret, "ann_vol": ann_vol, "sharpe": sharpe, "max_drawdown": max_dd}

def main(args):
    pm = PortfolioManager()
    # ensure model is loaded
    if not pm.direction_model._trained:
        log.warning("DirectionModel is not trained; run scripts/train_direction_model.py first for meaningful signals.")
    if args.synth:
        eod_by_date, feature_df = build_synthetic_eod_and_features(n_symbols=args.n_symbols, n_days=args.n_days, seed=args.seed)
    else:
        # Expect a single parquet/csv feature DF with per-day, per-symbol rows
        if not args.features_path:
            raise SystemExit("Either --synth or --features-path must be specified.")
        feature_df = pd.read_parquet(args.features_path) if str(args.features_path).endswith(".parquet") else pd.read_csv(args.features_path, parse_dates=["date"])
        # Expect a companion eod_by_date JSON/parquet describing per-date per-symbol eod fields
        if args.eod_json_path:
            eod_by_date = json.load(open(args.eod_json_path))
        else:
            raise SystemExit("When using real features, pass --eod-json-path with per-date eod mapping.")

    # iterate dates in chronological order
    dates = sorted(list({pd.to_datetime(x["date"]).date() for _, x in feature_df.iterrows()}))
    daily_reports = []
    for d in dates:
        trade_date = d.isoformat()
        # subset feature_df for this date
        feat_today = feature_df[feature_df["date"].dt.date == d] if isinstance(feature_df["date"].dtype, pd.DatetimeTZDtype) or True else feature_df[feature_df["date"] == d]
        # build a minimal feature_df for predict (PortfolioManager passes feature_df as whole; we pass subset)
        predict_df = feat_today.copy()
        # eod_prices for today
        eod_prices = eod_by_date.get(trade_date, {})
        # if eod_prices are missing for many symbols, PortfolioManager will skip them; ok for synthetic demo
        report = pm.run_eod(
            eod_prices=eod_prices,
            nifty_close=pd.Series([18000]),   # minimal support for regime detector
            india_vix=pd.Series([15.0]),
            fii_flows=pd.Series([500.0]),
            feature_df=predict_df,
            trade_date=trade_date
        )
        daily_reports.append(report)
    # compute metrics
    perf = compute_performance(daily_reports)
    log.info("Backtest complete. Perf summary: %s", perf)
    # Save summary
    out_path = Path("reporting") / f"walkforward_summary_{pd.Timestamp.today().date().isoformat()}.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps({"perf": perf}, indent=2))
    print("Saved summary:", out_path)
    return 0

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--synth", action="store_true", help="Run synthetic-mode backtest (no real EOD required)")
    parser.add_argument("--n-symbols", type=int, default=120)
    parser.add_argument("--n-days", type=int, default=180)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--features-path", type=str, default=None, help="Path to historical feature DF (parquet/csv)")
    parser.add_argument("--eod-json-path", type=str, default=None, help="Path to eod_by_date JSON (if using real features)")
    args = parser.parse_args()
    raise SystemExit(main(args))
