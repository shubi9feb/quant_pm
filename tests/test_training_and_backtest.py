# tests/test_training_and_backtest.py
import os
import tempfile
import json
import pytest
from pathlib import Path
import pandas as pd
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
import sys
sys.path.insert(0, str(ROOT))

from scripts.train_direction_model import synthesize_feature_df
from scripts.paper_backtest_walkforward import build_synthetic_eod_and_features, main as backtest_main
from portfolio_manager import PortfolioManager

def test_synthetic_features_shape():
    df = synthesize_feature_df(n_symbols=10, n_days=10, seed=1)
    assert "symbol" in df.columns and "date" in df.columns
    assert len(df) == 10 * 10

def test_train_and_save(tmp_path):
    # run a quick train using a tiny synthetic dataset
    df = synthesize_feature_df(n_symbols=10, n_days=50, seed=2)
    pm = PortfolioManager()
    res = pm.train_model(df)
    # expect training returns a dict or similar; at minimum model saved to models/ saved path
    assert hasattr(pm.direction_model, "_trained")
    assert pm.direction_model._trained is True

def test_short_synthetic_backtest(tmp_path):
    # build small synthetic dataset and run a very short backtest (n_days small)
    eod_by_date, feature_df = build_synthetic_eod_and_features(n_symbols=20, n_days=20, seed=3)
    pm = PortfolioManager()
    # If model is not trained, monkeypatch a trivial predictor
    if not pm.direction_model._trained:
        pm.direction_model._trained = True
        def fake_predict(df):
            # produce top 5 symbols with high prob
            df2 = pd.DataFrame([{"symbol": s, "model_prob": 0.8, "model_rank": i+1} for i, s in enumerate(sorted(list({r["symbol"] for _, r in df.iterrows()}))[:5])])
            return df2
        pm.direction_model.predict = fake_predict
    # run one day
    first_date = list(sorted(eod_by_date.keys()))[0]
    report = pm.run_eod(
        eod_prices=eod_by_date[first_date],
        nifty_close=pd.Series([18000]),
        india_vix=pd.Series([12.0]),
        fii_flows=pd.Series([500.0]),
        feature_df=feature_df[feature_df["date"].dt.date == pd.to_datetime(first_date).date()],
        trade_date=first_date
    )
    assert "portfolio_summary" in report
