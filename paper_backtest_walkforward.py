#!/usr/bin/env python3
"""
=============================================================================
WALK-FORWARD PAPER BACKTEST
=============================================================================
Runs day-by-day simulation using PortfolioManager.run_eod() and computes
performance metrics: annualized return, volatility, Sharpe, max drawdown.

Supports:
- Synthetic data generation (--synth)
- Real EOD data (--eod-path)
"""

import argparse
import sys
import os
import json
import logging
from pathlib import Path
from datetime import datetime, date
import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent.parent))

from portfolio_manager import PortfolioManager
from data.features import build_features, FEATURE_COLS
from config.settings import CAPITAL, MODEL_VERSION

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"
)
log = logging.getLogger("backtest")


def generate_synthetic_universe(n_symbols: int = 150, n_days: int = 120, seed: int = 42):
    """
    Generate synthetic EOD price data and regime indicators.
    
    Returns:
        (eod_prices_by_date, nifty_close, india_vix, fii_flows, feature_df)
    """
    np.random.seed(seed)
    log.info(f"Generating synthetic universe: {n_symbols} symbols × {n_days} days")
    
    dates = pd.date_range("2025-06-01", periods=n_days, freq="B")
    symbols = [f"STOCK_{i:03d}" for i in range(n_symbols)]
    
    # Generate Nifty 50
    nifty_returns = np.random.normal(0.0004, 0.008, n_days)
    nifty_close = pd.Series(18000 * np.cumprod(1 + nifty_returns), index=dates)
    
    # India VIX (mean-reverting)
    vix_base = 15 + np.cumsum(np.random.randn(n_days) * 0.3)
    india_vix = pd.Series(np.clip(vix_base, 8, 50), index=dates)
    
    # FII flows
    fii_flows = pd.Series(np.random.normal(500, 2000, n_days), index=dates)
    
    # Generate stock prices
    eod_prices_by_date = {}
    all_features = []
    
    for date_idx, curr_date in enumerate(dates):
        eod_prices_by_date[str(curr_date.date())] = {}
        
        for sym in symbols:
            # Stock price random walk
            sym_seed = hash(sym) % 10000
            np.random.seed(seed + sym_seed + date_idx)
            
            base_price = 500 + np.random.randint(0, 2000)
            daily_return = np.random.normal(0.0005, 0.015)
            close_price = base_price * (1 + daily_return) ** date_idx
            
            # Generate OHLC
            open_price = close_price * (1 + np.random.normal(0, 0.002))
            high_price = close_price * (1 + abs(np.random.normal(0, 0.005)))
            low_price = close_price * (1 - abs(np.random.normal(0, 0.005)))
            volume = int(abs(np.random.normal(1_000_000, 300_000)))
            
            # Technical indicators (simplified)
            ema_50 = close_price * (1 + np.random.normal(-0.05, 0.03))
            ema_200 = close_price * (1 + np.random.normal(-0.10, 0.05))
            rsi_14 = np.clip(np.random.normal(55, 15), 30, 80)
            atr_14 = close_price * abs(np.random.normal(0.02, 0.01))
            swing_low = low_price * 0.98
            avg_volume = volume * np.random.uniform(0.8, 1.2)
            avg_value = close_price * avg_volume
            
            eod_prices_by_date[str(curr_date.date())][sym] = {
                'close': close_price,
                'open': open_price,
                'high': high_price,
                'low': low_price,
                'volume': volume,
                'ema_50': ema_50,
                'ema_200': ema_200,
                'rsi_14': rsi_14,
                'atr_14': atr_14,
                'swing_low_20d': swing_low,
                'avg_volume_20d': avg_volume,
                'avg_value_20d': avg_value
            }
            
            # Build minimal feature row
            all_features.append({
                'date': curr_date,
                'symbol': sym,
                'close': close_price,
                **{col: np.random.randn() for col in FEATURE_COLS},
                'target': np.random.randint(0, 2)
            })
    
    feature_df = pd.DataFrame(all_features).set_index('date')
    
    log.info(f"Generated {len(eod_prices_by_date)} days of market data")
    
    return eod_prices_by_date, nifty_close, india_vix, fii_flows, feature_df


def run_backtest(pm: PortfolioManager, eod_prices_by_date: dict,
                 nifty_close: pd.Series, india_vix: pd.Series, fii_flows: pd.Series,
                 feature_df: pd.DataFrame):
    """
    Run day-by-day backtest.
    
    Returns:
        (nav_series, trades_log, daily_reports)
    """
    nav_history = []
    trades_log = []
    daily_reports = []
    
    dates = sorted(eod_prices_by_date.keys())
    log.info(f"Running backtest over {len(dates)} days: {dates[0]} to {dates[-1]}")
    
    for date_str in dates:
        eod_prices = eod_prices_by_date[date_str]
        
        # Get feature rows for this date
        date_features = feature_df[feature_df.index == pd.Timestamp(date_str)]
        
        if date_features.empty:
            # Create minimal feature df if needed
            date_features = pd.DataFrame([
                {'symbol': sym} for sym in eod_prices.keys()
            ])
            date_features.index = pd.DatetimeIndex([pd.Timestamp(date_str)] * len(date_features))
        
        # Run EOD cycle
        report = pm.run_eod(
            eod_prices=eod_prices,
            nifty_close=nifty_close,
            india_vix=india_vix,
            fii_flows=fii_flows,
            feature_df=date_features,
            trade_date=date_str
        )
        
        # Record NAV
        nav = report['portfolio_summary']['nav']
        nav_history.append({
            'date': date_str,
            'nav': nav,
            'positions': report['portfolio_summary']['open_positions'],
            'cash': report['portfolio_summary']['cash_value']
        })
        
        # Record trades
        for entry in report['decisions']['entries']:
            trades_log.append({**entry, 'date': date_str, 'type': 'entry'})
        for exit in report['decisions']['exits']:
            trades_log.append({**exit, 'date': date_str, 'type': 'exit'})
        
        daily_reports.append(report)
        
        # Save state after each day (for recovery)
        pm.save_state()
    
    return pd.DataFrame(nav_history), trades_log, daily_reports


def compute_metrics(nav_series: pd.DataFrame, trades_log: list, starting_capital: float = CAPITAL):
    """
    Compute performance metrics from NAV series and trades.
    
    Returns:
        dict with metrics
    """
    if len(nav_series) < 2:
        return {"error": "Insufficient data"}
    
    navs = nav_series['nav'].values
    returns = np.diff(navs) / navs[:-1]
    
    # Annualization factor
    trading_days = len(navs)
    ann_factor = 252 / trading_days if trading_days > 0 else 1
    
    # Returns
    total_return = (navs[-1] - starting_capital) / starting_capital
    ann_return = (1 + total_return) ** ann_factor - 1
    ann_vol = np.std(returns) * np.sqrt(252)
    
    # Sharpe (assuming 6.5% risk-free rate)
    rf_daily = (1.065) ** (1/252) - 1
    excess_returns = returns - rf_daily
    sharpe = (np.mean(excess_returns) / (np.std(excess_returns) + 1e-10)) * np.sqrt(252)
    
    # Sortino
    downside = excess_returns[excess_returns < 0]
    sortino = (np.mean(excess_returns) / (np.std(downside) + 1e-10)) * np.sqrt(252) if len(downside) > 0 else 0
    
    # Max Drawdown
    peak = np.maximum.accumulate(navs)
    drawdown = (navs - peak) / (peak + 1e-10)
    max_dd = float(np.min(drawdown))
    
    # Calmar
    calmar = ann_return / (abs(max_dd) + 1e-10)
    
    # Trade stats
    exits = [t for t in trades_log if t.get('type') == 'exit']
    if exits:
        profitable = [t for t in exits if t.get('realised_pnl', 0) > 0]
        win_rate = len(profitable) / len(exits)
        avg_win = np.mean([t['realised_pnl'] for t in profitable]) if profitable else 0
        losers = [t for t in exits if t.get('realised_pnl', 0) < 0]
        avg_loss = np.mean([t['realised_pnl'] for t in losers]) if losers else 0
        expectancy = win_rate * avg_win + (1 - win_rate) * avg_loss
    else:
        win_rate = avg_win = avg_loss = expectancy = 0
    
    return {
        "start_date": str(nav_series.iloc[0]['date']),
        "end_date": str(nav_series.iloc[-1]['date']),
        "trading_days": trading_days,
        "starting_capital": starting_capital,
        "ending_nav": float(navs[-1]),
        "total_return_pct": round(total_return * 100, 3),
        "ann_return_pct": round(ann_return * 100, 3),
        "ann_volatility_pct": round(ann_vol * 100, 3),
        "sharpe_ratio": round(float(sharpe), 4),
        "sortino_ratio": round(float(sortino), 4),
        "max_drawdown_pct": round(abs(max_dd) * 100, 3),
        "calmar_ratio": round(float(calmar), 4),
        "total_trades": len(trades_log),
        "total_exits": len(exits),
        "win_rate_pct": round(win_rate * 100, 2),
        "avg_win_inr": round(float(avg_win), 2),
        "avg_loss_inr": round(float(avg_loss), 2),
        "expectancy_inr": round(float(expectancy), 2),
    }


def main():
    parser = argparse.ArgumentParser(description="Run walk-forward paper backtest")
    parser.add_argument("--synth", action="store_true", help="Use synthetic data")
    parser.add_argument("--n-days", type=int, default=120, help="Synthetic: number of days")
    parser.add_argument("--n-symbols", type=int, default=150, help="Synthetic: number of symbols")
    parser.add_argument("--seed", type=int, default=42, help="Random seed")
    parser.add_argument("--eod-path", type=str, help="Path to real EOD data")
    
    args = parser.parse_args()
    
    log.info("="*70)
    log.info("WALK-FORWARD PAPER BACKTEST")
    log.info("="*70)
    
    # Clean up old state
    for f in ["portfolio_state.json", "order_book.jsonl"]:
        if os.path.exists(f):
            os.remove(f)
    
    # Generate or load data
    if args.synth:
        data = generate_synthetic_universe(
            n_symbols=args.n_symbols,
            n_days=args.n_days,
            seed=args.seed
        )
        eod_prices_by_date, nifty_close, india_vix, fii_flows, feature_df = data
    elif args.eod_path:
        log.error("Real EOD data loading not yet implemented")
        return 1
    else:
        log.error("Must specify --synth or --eod-path")
        return 1
    
    # Initialize PM
    pm = PortfolioManager()
    
    # Run backtest
    nav_series, trades_log, daily_reports = run_backtest(
        pm, eod_prices_by_date, nifty_close, india_vix, fii_flows, feature_df
    )
    
    # Compute metrics
    metrics = compute_metrics(nav_series, trades_log)
    
    # Save results
    os.makedirs("reporting", exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    
    summary_path = f"reporting/walkforward_summary_{timestamp}.json"
    with open(summary_path, "w") as f:
        json.dump(metrics, f, indent=2)
    
    nav_path = f"reporting/walkforward_nav_{timestamp}.csv"
    nav_series.to_csv(nav_path, index=False)
    
    trades_path = f"reporting/walkforward_trades_{timestamp}.json"
    with open(trades_path, "w") as f:
        json.dump(trades_log, f, indent=2, default=str)
    
    # Print summary
    print("\n" + "="*70)
    print("BACKTEST COMPLETE")
    print("="*70)
    print(f"Period:         {metrics['start_date']} → {metrics['end_date']}")
    print(f"Trading Days:   {metrics['trading_days']}")
    print(f"Total Return:   {metrics['total_return_pct']:.2f}%")
    print(f"Ann. Return:    {metrics['ann_return_pct']:.2f}%")
    print(f"Ann. Vol:       {metrics['ann_volatility_pct']:.2f}%")
    print(f"Sharpe Ratio:   {metrics['sharpe_ratio']:.4f}")
    print(f"Max Drawdown:  -{metrics['max_drawdown_pct']:.2f}%")
    print(f"Calmar Ratio:   {metrics['calmar_ratio']:.4f}")
    print(f"Total Trades:   {metrics['total_trades']}")
    print(f"Win Rate:       {metrics['win_rate_pct']:.2f}%")
    print(f"Expectancy:    ₹{metrics['expectancy_inr']:.2f}")
    print("="*70)
    print(f"Summary:  {summary_path}")
    print(f"NAV CSV:  {nav_path}")
    print(f"Trades:   {trades_path}")
    print("="*70)
    
    return 0


if __name__ == "__main__":
    sys.exit(main())
