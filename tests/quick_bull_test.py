
################################################################################
# QUICK BULL TEST - One Command
################################################################################
# Fastest way to test BULL trend - just run: ./quick_bull_test.sh
################################################################################

import sys, os, io
from pathlib import Path

def main():
    # Wrap stdout for UTF-8 safety on Windows
    if hasattr(sys.stdout, 'buffer'):
        try:
            sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
        except Exception:
            pass

    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

    import numpy as np
    import pandas as pd
    from portfolio_manager import PortfolioManager
    from data.features import FEATURE_COLS
    from config.settings import CAPITAL

    print("Quick BULL Test - 30 days")
    print("="*60)

    # Clean state
    for f in ["portfolio_state.json", "order_book.jsonl"]:
        if os.path.exists(f): os.remove(f)

    # Generate 30-day BULL market
    np.random.seed(42)
    N = 30
    dates = pd.date_range("2025-06-01", periods=N, freq="B")
    symbols = [f"STOCK_{i:03d}" for i in range(80)]

    # BULL conditions (steep upward drift to ensure bull trend)
    nifty = pd.Series(18000 * np.cumprod(1 + np.random.normal(0.005, 0.005, N)), index=dates)
    vix = pd.Series(np.full(N, 13.0), index=dates)
    fii = pd.Series(np.full(N, 2500.0), index=dates)

    print(f"Nifty: {nifty.iloc[0]:.0f} -> {nifty.iloc[-1]:.0f} (+{(nifty.iloc[-1]/nifty.iloc[0]-1)*100:.1f}%)")
    print(f"VIX: {vix.mean():.1f} (low fear)")
    print(f"FII: +Rs{fii.mean():.0f}cr/day (buying)")
    print()

    eod = {}
    import hashlib

    def stable_seed(sym: str, idx: int, base: int = 42) -> int:
        # Use first 4 bytes of sha256 to make a stable 32-bit integer
        h = hashlib.sha256(sym.encode('utf-8')).digest()
        h_int = int.from_bytes(h[:4], 'big')
        return (base + h_int + idx) % (2**32)

    eod = {}
    for idx, date in enumerate(dates):
        eod[str(date.date())] = {}
        for sym in symbols:
            seed = stable_seed(sym, idx)
            rng = np.random.default_rng(seed)
            close = 1000 * (1.01 ** idx) * rng.uniform(0.98, 1.02)
            eod[str(date.date())][sym] = {
                'close': close,
                'open': close * 0.999,
                'high': close * 1.005,
                'low': close * 0.995,
                'volume': 1_500_000,
                'ema_50': close * 0.97,
                'ema_200': close * 0.92,
                'rsi_14': 60,
                'atr_14': close * 0.02,
                'swing_low_20d': close * 0.95,
                'avg_volume_20d': 1_500_000,
                'avg_value_20d': close * 1_500_000
            }
    features = pd.DataFrame([
        {'date': d, 'symbol': s, **{c: np.random.randn() for c in FEATURE_COLS}, 'target': 1}
        for d in dates for s in symbols[:10]
    ]).set_index('date')

    # Run
    pm = PortfolioManager()
    results = []

    for date_str in sorted(eod.keys()):
        df = features[features.index == pd.Timestamp(date_str)]
        if df.empty:
            df = pd.DataFrame([{'symbol': s} for s in eod[date_str].keys()])
            df.index = pd.DatetimeIndex([pd.Timestamp(date_str)]*len(df))
        
        # Slice macro indicators up to the current date (avoid lookahead bias)
        current_date = pd.Timestamp(date_str)
        nifty_up_to_date = nifty.loc[:current_date]
        vix_up_to_date = vix.loc[:current_date]
        fii_up_to_date = fii.loc[:current_date]
        
        r = pm.run_eod(eod[date_str], nifty_up_to_date, vix_up_to_date, fii_up_to_date, df, date_str)
        results.append({
            'date': date_str,
            'nav': r['portfolio_summary']['nav'],
            'regime': r['regime']['regime'],
            'entries': r['decisions']['entries_today']
        })

    # Summary
    df = pd.DataFrame(results)
    ret = (df['nav'].iloc[-1] - CAPITAL) / CAPITAL * 100
    entries = df['entries'].sum()

    print("RESULTS")
    print("="*60)
    print(f"Return:  {ret:+.2f}%")
    print(f"Entries: {entries}")
    print(f"Regimes: {dict(df['regime'].value_counts())}")
    print("="*60)

    if entries > 0:
        print("✅ SUCCESS - System traded in BULL market!")
    else:
        print("⚠️  No trades - possible regime detection issue")


if __name__ == '__main__':
    main()

