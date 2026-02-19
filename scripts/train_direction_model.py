# scripts/train_direction_model.py
"""
Train the direction model for QuantPM.
- If you have historical features saved (CSV/parquet), provide --features-path.
- Otherwise this script builds a reproducible synthetic dataset with the expected columns
  and trains a demonstration model (useful for integration/paper testing).
"""
import argparse
import os
import logging
import pandas as pd
import numpy as np
from pathlib import Path

# Ensure repo root importability
ROOT = Path(__file__).resolve().parents[1]
import sys
sys.path.insert(0, str(ROOT))

from portfolio_manager import PortfolioManager
from data.features import FEATURE_COLS

log = logging.getLogger("train_direction")
logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s")

def synthesize_feature_df(n_symbols=200, n_days=800, seed=42):
    """Create a synthetic feature DF with columns in FEATURE_COLS + symbol + date + target."""
    np.random.seed(seed)
    dates = pd.date_range(end=pd.Timestamp.today(), periods=n_days, freq="B")
    symbols = [f"S{i:04d}" for i in range(n_symbols)]
    rows = []
    for d in dates:
        for s in symbols:
            row = {"date": d, "symbol": s}
            # generate plausible-looking features
            for c in FEATURE_COLS:
                # small random numbers around different scales
                if "ret" in c or c.startswith("price_to"):
                    row[c] = np.random.normal(0, 0.05)
                elif "rsi" in c or c == "adx_14":
                    row[c] = np.clip(50 + np.random.normal(0, 12), 1, 99)
                elif "vol" in c or "atr" in c or "obv" in c:
                    row[c] = abs(np.random.normal(1, 0.5))
                else:
                    row[c] = np.random.normal(0, 1)
            # synthetic target: +1 if future 5-day return > threshold (randomized)
            fut_ret = np.random.normal(0.002, 0.05)
            row["target_up_5d"] = 1 if fut_ret > 0.01 else 0
            rows.append(row)
    df = pd.DataFrame(rows)
    # ensure columns include FEATURE_COLS
    df = df[["date", "symbol"] + FEATURE_COLS + ["target_up_5d"]]
    return df

def load_features_from_path(path: str):
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(path)
    if p.suffix in (".parquet", ".pq"):
        return pd.read_parquet(p)
    return pd.read_csv(p, parse_dates=["date"])

def main(args):
    pm = PortfolioManager()
    if args.features_path:
        log.info(f"Loading features from {args.features_path}")
        feature_df = load_features_from_path(args.features_path)
    else:
        log.info("No features path provided — synthesizing a training dataset")
        feature_df = synthesize_feature_df(n_symbols=args.n_symbols, n_days=args.n_days, seed=args.seed)

    # If the feature DF doesn't have the exact FEATURE_COLS, attempt to align
    missing = [c for c in FEATURE_COLS if c not in feature_df.columns]
    if missing:
        log.warning(f"Feature DF missing columns: {missing}. Filling with zeros.")
        for c in missing:
            feature_df[c] = 0.0

    # Ensure a target column exists - DirectionModel.train expects whatever label it expects.
    # The repo examples used .train(feature_df, run_wf_cv=True) — the model implementation should
    # detect the label column. If it expects "target_up_5d" we provided it; adjust if your model uses a different name.
    if "target_up_5d" not in feature_df.columns:
        # fallback: create weak target
        feature_df["target_up_5d"] = (np.random.rand(len(feature_df)) > 0.98).astype(int)

    # delegate to PortfolioManager.train_model which calls DirectionModel.train
    log.info("Starting model training (may take a while).")
    results = pm.train_model(feature_df)
    log.info("Training completed. Results:\n%s", results)
    log.info("Saved model should be available for PortfolioManager._load_model_if_available()")
    return 0

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--features-path", type=str, default=None, help="Path to prebuilt feature DF (csv/parquet)")
    parser.add_argument("--n-symbols", type=int, default=200)
    parser.add_argument("--n-days", type=int, default=800)
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()
    raise SystemExit(main(args))
