from portfolio_manager import PortfolioManager
from tests.mocks import MockBroker
import pandas as pd
from datetime import date
import numpy as np

# 1. Init PM
pm = PortfolioManager()
# FORCE regime to allow trading
class DummyRegime:
    def __init__(self):
        self.regime = type("x", (), {"value": "SIDEWAYS"})
        self.allocation_mult = 1.0
        self.allow_new_longs = True

pm.regime_detector.detect = lambda *args, **kwargs: DummyRegime()


# 2. Inject MockBroker (force deterministic fills)
pm.broker = MockBroker(accept_orders=True, fill_ratio=1.0)

# 3. Force model to generate signals
pm.direction_model._trained = True

def fake_predict(df):
    return pd.DataFrame([
        {"symbol": "ABC", "model_prob": 0.75, "model_rank": 1},
        {"symbol": "XYZ", "model_prob": 0.72, "model_rank": 2},
    ])

pm.direction_model.predict = fake_predict

# 4. Create valid EOD data (must pass filters)
eod_prices = {
    "ABC": {
        "close": 120, "ema_50": 115, "ema_200": 100,
        "rsi_14": 60, "atr_14": 2,
        "swing_low_20d": 110,
        "avg_volume_20d": 600000,
        "avg_value_20d": 6000000
    },
    "XYZ": {
        "close": 220, "ema_50": 210, "ema_200": 190,
        "rsi_14": 58, "atr_14": 3,
        "swing_low_20d": 200,
        "avg_volume_20d": 800000,
        "avg_value_20d": 9000000
    }
}

feature_df = pd.DataFrame([
    {"symbol": "ABC"},
    {"symbol": "XYZ"}
])




# Create realistic historical data
dates = pd.date_range(end=pd.Timestamp.today(), periods=100, freq="B")

nifty_close = pd.Series(
    18000 * np.cumprod(1 + np.random.normal(0.0005, 0.01, len(dates))),
    index=dates
)

india_vix = pd.Series(
    np.clip(15 + np.random.normal(0, 3, len(dates)), 8, 40),
    index=dates
)

fii_flows = pd.Series(
    np.random.normal(500, 2000, len(dates)),
    index=dates
)

# 5. Run EOD
# report = pm.run_eod(
#     eod_prices=eod_prices,
#     nifty_close=pd.Series([18000]),
#     india_vix=pd.Series([15]),
#     fii_flows=pd.Series([500]),
#     feature_df=feature_df,
#     trade_date=date.today().isoformat()
# )

report = pm.run_eod(
    eod_prices=eod_prices,
    nifty_close=nifty_close,
    india_vix=india_vix,
    fii_flows=fii_flows,
    feature_df=feature_df,
    trade_date=date.today().isoformat()
)


# 6. Assertions (manual prints)
print("\n--- RESULTS ---")
print("Positions:", pm.positions.keys())
print("Cash:", pm.cash)
print("Entries:", len(report["orders_placed"]))
print("Top Signals:", report["top_signals"][:2])

print("\n--- DAY 2: STOP TEST ---")

# Force price crash → trigger stop
eod_prices["ABC"]["close"] = 90   # below stop (116)

report2 = pm.run_eod(
    eod_prices=eod_prices,
    nifty_close=nifty_close,
    india_vix=india_vix,
    fii_flows=fii_flows,
    feature_df=feature_df,
    trade_date=date.today().isoformat()
)

print("\n--- AFTER STOP ---")
print("Positions:", pm.positions.keys())
print("Realised PnL:", pm.realised_pnl)
