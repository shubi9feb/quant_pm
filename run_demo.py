# run_demo.py
from portfolio_manager import PortfolioManager
import numpy as np, pandas as pd
from datetime import datetime

# build synthetic market and feature data (matches what main expects)
np.random.seed(42)
n_days = 500
dates = pd.date_range("2023-01-01", periods=n_days, freq="B")
nifty_returns = np.random.normal(0.0004, 0.008, n_days)
nifty_close = pd.Series(18000 * np.cumprod(1 + nifty_returns), index=dates)
india_vix = pd.Series(abs(15 + np.random.normal(0,3,n_days).cumsum()*0.1), index=dates).clip(8,50)
fii_flows = pd.Series(np.random.normal(500,2000,n_days), index=dates)

# minimal eod_prices & feature_df for a couple of fake symbols
symbols = ["ABC", "XYZ"]
last_date = dates[-1].date().isoformat()
eod_prices = {
    "ABC": {"close": 120.0, "atr_14": 2.5, "ema_50": 115.0, "ema_200": 100.0, "rsi_14": 60},
    "XYZ": {"close": 220.0, "atr_14": 4.5, "ema_50": 210.0, "ema_200": 190.0, "rsi_14": 55},
}
# fake feature_df with the same 'symbol' column expected by the model
feature_df = pd.DataFrame([{"symbol": "ABC", "model_prob": 0.7, "model_rank": 1},
                           {"symbol": "XYZ", "model_prob": 0.66, "model_rank": 2}])

pm = PortfolioManager()
# ensure state loaded or saved as needed
pm.load_state()          # will be a no-op if no file
report = pm.run_eod(eod_prices, nifty_close, india_vix, fii_flows, feature_df, trade_date=last_date)
pm.save_state()
print("Report keys:", list(report.keys()))
