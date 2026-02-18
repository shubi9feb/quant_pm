"""
=============================================================================
DATA PIPELINE — EOD Ingestion, Feature Engineering, Technical Indicators
=============================================================================
Produces a standardized feature DataFrame for every symbol in the universe.
All features are cross-sectionally ranked (z-scored) before model input.
"""

import numpy as np
import pandas as pd
from typing import Optional, Tuple, Dict
import warnings
warnings.filterwarnings("ignore")

# ─────────────────────────────────────────────────────────────────────────────
# TECHNICAL INDICATOR LIBRARY
# ─────────────────────────────────────────────────────────────────────────────

def ema(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(span=period, adjust=False).mean()

def atr(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14) -> pd.Series:
    """Average True Range — volatility measure used for position sizing and stops."""
    prev_close = close.shift(1)
    tr = pd.concat([
        high - low,
        (high - prev_close).abs(),
        (low  - prev_close).abs()
    ], axis=1).max(axis=1)
    return tr.ewm(span=period, adjust=False).mean()

def rsi(close: pd.Series, period: int = 14) -> pd.Series:
    """Relative Strength Index."""
    delta = close.diff()
    gain  = delta.clip(lower=0).ewm(com=period - 1, adjust=False).mean()
    loss  = (-delta.clip(upper=0)).ewm(com=period - 1, adjust=False).mean()
    rs    = gain / loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))

def macd(close: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9) -> pd.DataFrame:
    """MACD line, signal, and histogram."""
    macd_line   = ema(close, fast) - ema(close, slow)
    signal_line = ema(macd_line, signal)
    histogram   = macd_line - signal_line
    return pd.DataFrame({"macd": macd_line, "signal": signal_line, "hist": histogram})

def bollinger_bands(close: pd.Series, period: int = 20, n_std: float = 2.0) -> pd.DataFrame:
    """Bollinger Bands — middle, upper, lower, %B, bandwidth."""
    mid   = close.rolling(period).mean()
    std   = close.rolling(period).std()
    upper = mid + n_std * std
    lower = mid - n_std * std
    pct_b = (close - lower) / (upper - lower + 1e-10)
    bw    = (upper - lower) / (mid + 1e-10)
    return pd.DataFrame({"bb_mid": mid, "bb_upper": upper, "bb_lower": lower,
                         "bb_pctb": pct_b, "bb_bw": bw})

def adx(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14) -> pd.Series:
    """Average Directional Index — trend strength (0–100)."""
    up_move   = high.diff()
    down_move = -low.diff()
    plus_dm   = up_move.where((up_move > down_move) & (up_move > 0), 0.0)
    minus_dm  = down_move.where((down_move > up_move) & (down_move > 0), 0.0)
    tr_val    = atr(high, low, close, period)
    plus_di   = 100 * ema(plus_dm, period) / (tr_val + 1e-10)
    minus_di  = 100 * ema(minus_dm, period) / (tr_val + 1e-10)
    dx        = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di + 1e-10)
    return ema(dx, period)

def obv(close: pd.Series, volume: pd.Series) -> pd.Series:
    """On-Balance Volume — cumulative volume momentum."""
    direction = np.sign(close.diff()).fillna(0)
    return (direction * volume).cumsum()

def volume_ratio(volume: pd.Series, period: int = 20) -> pd.Series:
    """Current volume / 20-day average volume."""
    avg = volume.rolling(period).mean()
    return volume / (avg + 1e-10)

def swing_low(low: pd.Series, lookback: int = 20) -> pd.Series:
    """Most recent significant swing low over lookback period."""
    return low.rolling(lookback).min()


# ─────────────────────────────────────────────────────────────────────────────
# FEATURE ENGINEERING
# ─────────────────────────────────────────────────────────────────────────────

def build_features(df: pd.DataFrame, symbol: str) -> pd.DataFrame:
    """
    Build the full standardized feature set for a single symbol.

    Input columns required: open, high, low, close, volume
    Returns DataFrame with one row per date, all features computed.
    """
    df = df.copy().sort_index()

    # ── Price-derived features ──────────────────────────────────────────────
    df["ema_10"]    = ema(df["close"], 10)
    df["ema_20"]    = ema(df["close"], 20)
    df["ema_50"]    = ema(df["close"], 50)
    df["ema_200"]   = ema(df["close"], 200)

    df["price_to_ema50"]  = df["close"] / (df["ema_50"]  + 1e-10) - 1
    df["price_to_ema200"] = df["close"] / (df["ema_200"] + 1e-10) - 1
    df["ema50_to_200"]    = df["ema_50"]  / (df["ema_200"] + 1e-10) - 1

    # ── Momentum features ───────────────────────────────────────────────────
    for days in [5, 10, 20, 60]:
        df[f"ret_{days}d"]  = df["close"].pct_change(days)

    df["rsi_14"]   = rsi(df["close"], 14)
    df["rsi_28"]   = rsi(df["close"], 28)

    macd_df        = macd(df["close"])
    df["macd"]     = macd_df["macd"]
    df["macd_sig"] = macd_df["signal"]
    df["macd_hist"]= macd_df["hist"]
    df["macd_cross"]= (df["macd"] > df["macd_sig"]).astype(int)

    # ── Volatility features ─────────────────────────────────────────────────
    df["atr_14"]   = atr(df["high"], df["low"], df["close"], 14)
    df["atr_pct"]  = df["atr_14"] / (df["close"] + 1e-10)  # ATR as % of price
    df["vol_20d"]  = df["close"].pct_change().rolling(20).std() * np.sqrt(252)

    bb_df          = bollinger_bands(df["close"])
    df["bb_pctb"]  = bb_df["bb_pctb"]
    df["bb_bw"]    = bb_df["bb_bw"]

    # ── Trend strength ──────────────────────────────────────────────────────
    df["adx_14"]   = adx(df["high"], df["low"], df["close"], 14)

    # ── Volume features ─────────────────────────────────────────────────────
    df["vol_ratio_20d"]    = volume_ratio(df["volume"], 20)
    df["obv_normalized"]   = obv(df["close"], df["volume"])
    df["obv_normalized"]   = df["obv_normalized"] / (df["obv_normalized"].abs().rolling(252).max() + 1e-10)

    # ── Liquidity (for filtering, not model) ────────────────────────────────
    df["avg_volume_20d"]   = df["volume"].rolling(20).mean()
    df["avg_value_20d"]    = (df["close"] * df["volume"]).rolling(20).mean()

    # ── Gap / overnight risk ────────────────────────────────────────────────
    df["gap_pct"]  = (df["open"] - df["close"].shift(1)) / (df["close"].shift(1) + 1e-10)

    # ── Swing low for stop placement ────────────────────────────────────────
    df["swing_low_20d"] = swing_low(df["low"], 20)

    # ── Price above key EMAs (binary entry filters) ─────────────────────────
    df["above_ema50"]  = (df["close"] > df["ema_50"]).astype(int)
    df["above_ema200"] = (df["close"] > df["ema_200"]).astype(int)

    # ── Symbol metadata ─────────────────────────────────────────────────────
    df["symbol"] = symbol

    return df


def cross_section_zscore(feature_df: pd.DataFrame, feature_cols: list) -> pd.DataFrame:
    """
    Cross-sectionally Z-score all features on each date.
    This makes model inputs comparable across stocks regardless of price level.
    """
    def zscore_row(group):
        for col in feature_cols:
            m = group[col].mean()
            s = group[col].std()
            group[col] = (group[col] - m) / (s + 1e-10)
        return group

    return feature_df.groupby(feature_df.index, group_keys=False).apply(zscore_row)


def build_target(df: pd.DataFrame, benchmark_returns: pd.Series,
                 horizon: int = 30) -> pd.Series:
    """
    Binary target: 1 if stock outperforms Nifty 50 over next `horizon` days.
    Used for XGBoost training.
    """
    fwd_return       = df["close"].pct_change(horizon).shift(-horizon)
    bench_fwd        = benchmark_returns.pct_change(horizon).shift(-horizon)
    # Align on same date index
    bench_fwd        = bench_fwd.reindex(df.index)
    return (fwd_return > bench_fwd).astype(int).rename("target")


# ─────────────────────────────────────────────────────────────────────────────
# PIPELINE ORCHESTRATOR
# ─────────────────────────────────────────────────────────────────────────────

FEATURE_COLS = [
    "price_to_ema50", "price_to_ema200", "ema50_to_200",
    "ret_5d", "ret_10d", "ret_20d", "ret_60d",
    "rsi_14", "rsi_28",
    "macd", "macd_sig", "macd_hist", "macd_cross",
    "atr_pct", "vol_20d",
    "bb_pctb", "bb_bw",
    "adx_14",
    "vol_ratio_20d", "obv_normalized",
    "gap_pct"
]

def run_feature_pipeline(
    price_data: Dict[str, pd.DataFrame],
    benchmark: pd.Series,
    horizon: int = 30
) -> pd.DataFrame:
    """
    Full pipeline: build features for every symbol, attach target, z-score.

    Args:
        price_data  : dict {symbol: OHLCV DataFrame with DatetimeIndex}
        benchmark   : Nifty 50 close price series
        horizon     : forward return horizon in days

    Returns:
        Combined DataFrame ready for model training or inference
    """
    all_frames = []

    for symbol, df in price_data.items():
        try:
            feat_df = build_features(df, symbol)
            tgt     = build_target(feat_df, benchmark, horizon)
            feat_df["target"] = tgt
            feat_df = feat_df.dropna(subset=FEATURE_COLS)
            all_frames.append(feat_df)
        except Exception as e:
            print(f"[PIPELINE] Skipping {symbol}: {e}")

    if not all_frames:
        return pd.DataFrame()

    combined = pd.concat(all_frames).sort_index()
    combined = cross_section_zscore(combined, FEATURE_COLS)
    return combined
