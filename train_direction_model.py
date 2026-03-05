#!/usr/bin/env python3
"""
=============================================================================
DIRECTION MODEL TRAINING PIPELINE
=============================================================================
Trains the XGBoost direction model using either:
- Real features from parquet/CSV (--features-path)
- Synthetic features generated with fixed seed (--synth)

Saves model and metrics to models/saved/ and reporting/
"""

import argparse
import sys
import os
import json
import logging
from pathlib import Path
from datetime import datetime
import numpy as np
import pandas as pd

# Add repo root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from models.direction_model import DirectionModel
from data.features import FEATURE_COLS, build_features
from config.settings import MODEL_VERSION, MODEL_DIR

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"
)
log = logging.getLogger("train_pipeline")


def generate_synthetic_features(n_symbols: int = 200, n_days: int = 400, seed: int = 42) -> pd.DataFrame:
    """
    Generate synthetic feature data for training when real data not available.
    
    Args:
        n_symbols: Number of stocks in universe
        n_days: Number of trading days
        seed: Random seed for reproducibility
        
    Returns:
        DataFrame with all FEATURE_COLS and target column
    """
    np.random.seed(seed)
    log.info(f"Generating synthetic features: {n_symbols} symbols × {n_days} days")
    
    dates = pd.date_range("2022-01-01", periods=n_days, freq="B")
    symbols = [f"SYM_{i:03d}" for i in range(n_symbols)]
    
    all_data = []
    
    for sym_idx, symbol in enumerate(symbols):
        # Generate realistic-looking synthetic features
        # Trend features
        base_trend = np.random.randn() * 0.1  # stock-specific trend
        price_to_ema50 = base_trend + np.random.randn(n_days) * 0.05
        price_to_ema200 = base_trend + np.random.randn(n_days) * 0.08
        ema50_to_200 = np.random.randn(n_days) * 0.03
        
        # Momentum features (autocorrelated)
        ret_5d = np.cumsum(np.random.randn(n_days) * 0.01)
        ret_10d = np.cumsum(np.random.randn(n_days) * 0.015)
        ret_20d = np.cumsum(np.random.randn(n_days) * 0.02)
        ret_60d = np.cumsum(np.random.randn(n_days) * 0.04)
        
        # RSI (mean-reverting around 0.5, then scaled)
        rsi_14 = np.clip(0.5 + np.random.randn(n_days) * 0.15, 0, 1)
        rsi_28 = np.clip(0.5 + np.random.randn(n_days) * 0.12, 0, 1)
        
        # MACD
        macd = np.random.randn(n_days) * 0.02
        macd_sig = macd + np.random.randn(n_days) * 0.005
        macd_hist = macd - macd_sig
        macd_cross = (macd > macd_sig).astype(int)
        
        # Volatility
        atr_pct = np.abs(np.random.randn(n_days) * 0.01 + 0.02)
        vol_20d = np.abs(np.random.randn(n_days) * 0.05 + 0.15)
        
        # Bollinger
        bb_pctb = np.random.uniform(0, 1, n_days)
        bb_bw = np.abs(np.random.randn(n_days) * 0.02 + 0.05)
        
        # Trend strength
        adx_14 = np.abs(np.random.randn(n_days) * 0.15 + 0.3)
        
        # Volume
        vol_ratio_20d = np.abs(np.random.randn(n_days) * 0.3 + 1.0)
        obv_normalized = np.cumsum(np.random.randn(n_days) * 0.1)
        obv_normalized = (obv_normalized - obv_normalized.mean()) / (obv_normalized.std() + 1e-10)
        
        # Gap
        gap_pct = np.random.randn(n_days) * 0.005
        
        # Target (30-day forward return > benchmark)
        # Create some predictive signal: stocks with positive trend + momentum tend to outperform
        signal_strength = (price_to_ema50 + ret_20d + rsi_14 - 0.5) / 3
        noise = np.random.randn(n_days)
        future_return = signal_strength * 0.3 + noise * 0.7
        
        # Binary target with some realistic class balance (40-60%)
        threshold = np.percentile(future_return, 55)
        target = (future_return > threshold).astype(int)
        
        # Create DataFrame for this symbol
        df = pd.DataFrame({
            'symbol': symbol,
            'date': dates,
            'price_to_ema50': price_to_ema50,
            'price_to_ema200': price_to_ema200,
            'ema50_to_200': ema50_to_200,
            'ret_5d': ret_5d,
            'ret_10d': ret_10d,
            'ret_20d': ret_20d,
            'ret_60d': ret_60d,
            'rsi_14': rsi_14,
            'rsi_28': rsi_28,
            'macd': macd,
            'macd_sig': macd_sig,
            'macd_hist': macd_hist,
            'macd_cross': macd_cross,
            'atr_pct': atr_pct,
            'vol_20d': vol_20d,
            'bb_pctb': bb_pctb,
            'bb_bw': bb_bw,
            'adx_14': adx_14,
            'vol_ratio_20d': vol_ratio_20d,
            'obv_normalized': obv_normalized,
            'gap_pct': gap_pct,
            'target': target
        })
        
        all_data.append(df)
    
    combined = pd.concat(all_data, ignore_index=True)
    combined = combined.set_index('date')
    
    log.info(f"Generated {len(combined):,} samples with {len(FEATURE_COLS)} features")
    log.info(f"Target distribution: {combined['target'].mean():.1%} positive class")
    
    return combined


def load_real_features(path: str) -> pd.DataFrame:
    """
    Load features from parquet or CSV file.
    
    Args:
        path: Path to feature file
        
    Returns:
        DataFrame with features and target
    """
    log.info(f"Loading features from {path}")
    
    if path.endswith('.parquet'):
        df = pd.read_parquet(path)
    elif path.endswith('.csv'):
        df = pd.read_csv(path, parse_dates=['date'])
        if 'date' in df.columns:
            df = df.set_index('date')
    else:
        raise ValueError(f"Unsupported file format: {path}")
    
    # Validate required columns
    missing = set(FEATURE_COLS) - set(df.columns)
    if missing:
        log.warning(f"Missing features: {missing}. Filling with zeros.")
        for col in missing:
            df[col] = 0.0
    
    if 'target' not in df.columns:
        raise ValueError("Feature file must contain 'target' column")
    
    log.info(f"Loaded {len(df):,} samples from {path}")
    return df[FEATURE_COLS + ['target', 'symbol']]


def main():
    parser = argparse.ArgumentParser(description="Train direction model")
    parser.add_argument("--features-path", type=str, help="Path to features parquet/CSV")
    parser.add_argument("--synth", action="store_true", help="Use synthetic features")
    parser.add_argument("--n-symbols", type=int, default=200, help="Synthetic: number of symbols")
    parser.add_argument("--n-days", type=int, default=400, help="Synthetic: number of days")
    parser.add_argument("--seed", type=int, default=42, help="Random seed")
    parser.add_argument("--no-cv", action="store_true", help="Skip walk-forward CV (faster)")
    
    args = parser.parse_args()
    
    log.info("="*70)
    log.info(f"DIRECTION MODEL TRAINING — {MODEL_VERSION}")
    log.info("="*70)
    
    # Load or generate features
    if args.features_path:
        if not os.path.exists(args.features_path):
            log.error(f"Feature file not found: {args.features_path}")
            return 1
        feature_df = load_real_features(args.features_path)
    elif args.synth:
        feature_df = generate_synthetic_features(
            n_symbols=args.n_symbols,
            n_days=args.n_days,
            seed=args.seed
        )
    else:
        log.error("Must specify either --features-path or --synth")
        parser.print_help()
        return 1
    
    # Initialize model
    model = DirectionModel()
    
    # Train with walk-forward CV
    log.info(f"Training with walk-forward CV: {not args.no_cv}")
    results = model.train(feature_df, run_wf_cv=not args.no_cv)
    
    # Save model
    log.info(f"Saving model to {MODEL_DIR}/")
    model.save()
    
    # Save metrics
    os.makedirs("reporting", exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    metrics_path = f"reporting/model_metrics_{timestamp}.json"
    
    with open(metrics_path, "w") as f:
        json.dump(results, f, indent=2, default=str)
    
    log.info(f"Metrics saved to {metrics_path}")
    
    # Print summary
    print("\n" + "="*70)
    print("TRAINING COMPLETE")
    print("="*70)
    
    if 'aggregate_oos_auc' in results:
        print(f"OOS AUC:       {results['aggregate_oos_auc']:.4f}")
        print(f"OOS Precision: {results['aggregate_oos_precision']:.4f}")
        print(f"OOS Recall:    {results['aggregate_oos_recall']:.4f}")
        print(f"OOS F1:        {results['aggregate_oos_f1']:.4f}")
    
    metadata = results.get('metadata', {})
    print(f"Model Version: {metadata.get('version', MODEL_VERSION)}")
    print(f"Train Samples: {metadata.get('n_train_samples', 'N/A'):,}")
    print(f"Features:      {metadata.get('n_features', len(FEATURE_COLS))}")
    print(f"Model saved:   {MODEL_DIR}/model_{MODEL_VERSION.replace('.', '_')}.pkl")
    print(f"Metrics saved: {metrics_path}")
    print("="*70)
    
    return 0


if __name__ == "__main__":
    sys.exit(main())
