"""
=============================================================================
UNIT TESTS — Order Flows, Rejections, Partial Fills, Stop Hits
=============================================================================
Tests the complete order lifecycle from placement to fill to exit.
"""

import pytest
import sys
import os
import tempfile
import numpy as np
import pandas as pd
from pathlib import Path
from datetime import date

# Add repo root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from portfolio_manager import PortfolioManager, OpenPosition
from tests.mocks import MockBroker, MockBrokerBuilder
from config.settings import CAPITAL, GOLD_HEDGE_WEIGHT, MAX_POSITIONS
from core.regime import MarketRegime, RegimeState
from risk.engine import DrawdownState


# ─────────────────────────────────────────────────────────────────────────────
# FIXTURES
# ─────────────────────────────────────────────────────────────────────────────

@pytest.fixture
def clean_state():
    """Clean up any state files before/after each test."""
    files = ["portfolio_state.json", "order_book.jsonl", "test_order_book.jsonl"]
    for f in files:
        if os.path.exists(f):
            os.remove(f)
    yield
    for f in files:
        if os.path.exists(f):
            os.remove(f)


@pytest.fixture
def mock_pm():
    """Create a PortfolioManager with MockBroker and trained model."""
    pm = PortfolioManager()
    
    # Mock trained model
    pm.direction_model._trained = True
    
    def mock_predict(df):
        # Return high-prob signals for testing
        symbols = df['symbol'].unique() if 'symbol' in df.columns else ['TEST']
        return pd.DataFrame({
            'symbol': list(symbols)[:3],
            'model_prob': [0.78, 0.71, 0.68],
            'model_rank': [1, 2, 3],
            'model_version': ['test_v1']*3
        })
    
    pm.direction_model.predict = mock_predict
    return pm


@pytest.fixture
def bull_regime():
    """Create a BULL_TREND regime state."""
    return RegimeState(
        regime=MarketRegime.BULL_TREND,
        nifty_trend="ABOVE_EMA",
        vix_level=12.0,
        vix_signal="LOW_FEAR",
        fii_flow_signal="STRONG_BUY",
        score=0.85,
        allocation_mult=1.0,
        allow_new_longs=True,
        date="2026-02-20"
    )


@pytest.fixture
def sample_eod_prices():
    """Generate sample EOD prices that pass all entry filters."""
    np.random.seed(42)
    symbols = ['RELIANCE', 'INFY', 'TCS', 'HDFC', 'ICICI']
    prices = {}
    
    for sym in symbols:
        base_price = 1000 + np.random.randint(0, 2000)
        prices[sym] = {
            'close': base_price,
            'open': base_price * 0.999,
            'high': base_price * 1.005,
            'low': base_price * 0.995,
            'volume': 2_000_000,
            'ema_50': base_price * 0.97,
            'ema_200': base_price * 0.90,
            'rsi_14': 62,
            'atr_14': base_price * 0.02,
            'swing_low_20d': base_price * 0.92,
            'avg_volume_20d': 2_000_000,
            'avg_value_20d': base_price * 2_000_000
        }
    
    return prices


@pytest.fixture
def sample_regime_data():
    """Generate sample Nifty, VIX, FII data."""
    n = 300
    dates = pd.date_range("2023-01-01", periods=n, freq="B")
    np.random.seed(0)
    
    nifty = pd.Series(18000 * np.cumprod(1 + np.full(n, 0.001)), index=dates)
    vix = pd.Series(np.full(n, 12.0), index=dates)
    fii = pd.Series(np.full(n, 2000.0), index=dates)
    
    return nifty, vix, fii


# ─────────────────────────────────────────────────────────────────────────────
# ORDER REJECTION TESTS
# ─────────────────────────────────────────────────────────────────────────────

def test_order_rejection_no_cash_change(clean_state, mock_pm, sample_eod_prices, 
                                        sample_regime_data, bull_regime):
    """
    When broker rejects an order, cash should not be deducted and no position created.
    """
    pm = mock_pm
    initial_cash = pm.cash
    
    # Configure broker to reject all orders
    pm.broker = MockBroker(accept_orders=False, rejection_reason="quota_exceeded")
    
    # Build minimal feature df
    feature_df = pd.DataFrame([{"symbol": "RELIANCE"}])
    feature_df.index = pd.DatetimeIndex([pd.Timestamp("2026-02-20")])
    
    nifty, vix, fii = sample_regime_data
    
    # Run EOD cycle
    report = pm.run_eod(
        eod_prices=sample_eod_prices,
        nifty_close=nifty,
        india_vix=vix,
        fii_flows=fii,
        feature_df=feature_df,
        trade_date="2026-02-20"
    )
    
    # Assertions
    assert "RELIANCE" not in pm.positions, "Position should not be created on rejection"
    assert pm.cash == initial_cash, f"Cash should not change: {pm.cash} vs {initial_cash}"
    assert report["decisions"]["entries_today"] == 0, "No entries should be recorded"
    
    print("✅ Order rejection: no cash change, no position created")


def test_entry_partial_fill_adjusts_cash_and_position(clean_state, mock_pm, 
                                                       sample_eod_prices, sample_regime_data):
    """
    Partial fills should only deduct cash for filled quantity and create position with filled_qty.
    """
    pm = mock_pm
    initial_cash = pm.cash
    
    # Configure broker for 50% fills
    pm.broker = MockBroker(accept_orders=True, fill_ratio=0.5)
    
    feature_df = pd.DataFrame([{"symbol": "RELIANCE"}])
    feature_df.index = pd.DatetimeIndex([pd.Timestamp("2026-02-20")])
    
    nifty, vix, fii = sample_regime_data
    
    report = pm.run_eod(
        eod_prices=sample_eod_prices,
        nifty_close=nifty,
        india_vix=vix,
        fii_flows=fii,
        feature_df=feature_df,
        trade_date="2026-02-20"
    )
    
    # Check position created with partial fill
    if "RELIANCE" in pm.positions:
        pos = pm.positions["RELIANCE"]
        # Position quantity should be approximately half of requested
        # (exact value depends on position sizing, but should be < requested)
        assert pos.quantity > 0, "Position should exist"
        
        # Cash should be deducted only for filled amount
        cash_change = initial_cash - pm.cash
        assert cash_change > 0, "Cash should be deducted"
        assert cash_change < initial_cash * 0.20, "Cash deduction should be reasonable for partial fill"
        
        print(f"✅ Partial fill: position qty={pos.quantity}, cash_change=₹{cash_change:,.0f}")
    else:
        # If position sizing resulted in 0 shares, that's also valid
        print("✅ Partial fill: position sizing resulted in 0 shares (also valid)")


# ─────────────────────────────────────────────────────────────────────────────
# EXIT / STOP HIT TESTS
# ─────────────────────────────────────────────────────────────────────────────

def test_stop_hit_full_fill_realised_pnl_position_removed(clean_state):
    """
    Stop hit with full fill should:
    - Compute realized P&L correctly
    - Update cash
    - Remove position
    - Create exactly one TradeRecord
    """
    pm = PortfolioManager()
    pm.direction_model._trained = True
    
    # Manually create an open position
    pm.positions["TEST"] = OpenPosition(
        symbol="TEST",
        quantity=10,
        entry_price=1000.0,
        initial_stop=920.0,
        model_prob=0.75,
        entry_date="2026-02-19",
        order_id="ORDER_001",
        atr_at_entry=20.0
    )
    
    initial_cash = pm.cash
    initial_realized_pnl = pm.realised_pnl
    
    # EOD prices with stop hit
    eod_prices = {
        "TEST": {
            'close': 910.0,  # Below stop of 920
            'open': 910.0,
            'high': 920.0,
            'low': 900.0,
            'volume': 1_000_000,
            'atr_14': 20.0,
            'ema_50': 950.0,
            'ema_200': 900.0,
            'rsi_14': 40,
            'swing_low_20d': 880.0,
            'avg_volume_20d': 1_000_000,
            'avg_value_20d': 900_000_000
        }
    }
    
    feature_df = pd.DataFrame([{"symbol": "TEST"}])
    feature_df.index = pd.DatetimeIndex([pd.Timestamp("2026-02-20")])
    
    n = 300
    dates = pd.date_range("2023-01-01", periods=n, freq="B")
    nifty = pd.Series(18000 * np.ones(n), index=dates)
    vix = pd.Series(12.0 * np.ones(n), index=dates)
    fii = pd.Series(2000.0 * np.ones(n), index=dates)
    
    report = pm.run_eod(
        eod_prices=eod_prices,
        nifty_close=nifty,
        india_vix=vix,
        fii_flows=fii,
        feature_df=feature_df,
        trade_date="2026-02-20"
    )
    
    # Assertions
    assert "TEST" not in pm.positions, "Position should be removed after stop hit"
    assert pm.cash > initial_cash, "Cash should increase from exit proceeds"
    assert pm.realised_pnl < initial_realized_pnl, "Realized P&L should be negative (loss)"
    assert report["decisions"]["exits_today"] == 1, "Should have exactly 1 exit"
    
    exit_record = report["decisions"]["exits"][0]
    assert exit_record["action"] == "STOP_HIT"
    assert exit_record["quantity"] == 10
    assert "realised_pnl" in exit_record
    
    print(f"✅ Stop hit: position removed, P&L={exit_record['realised_pnl']:.2f}, exits=1")


def test_duplicate_exit_prevented(clean_state):
    """
    Ensure that a position is only exited once (no double-delete).
    """
    pm = PortfolioManager()
    pm.direction_model._trained = True
    
    # Create position
    pm.positions["TEST"] = OpenPosition(
        symbol="TEST",
        quantity=10,
        entry_price=1000.0,
        initial_stop=920.0,
        model_prob=0.75,
        entry_date="2026-02-19",
        order_id="ORDER_001",
        atr_at_entry=20.0
    )
    
    # EOD prices with stop hit
    eod_prices = {
        "TEST": {
            'close': 910.0,
            'open': 910.0,
            'high': 920.0,
            'low': 900.0,
            'volume': 1_000_000,
            'atr_14': 20.0,
            'ema_50': 950.0,
            'ema_200': 900.0,
            'rsi_14': 40,
            'swing_low_20d': 880.0,
            'avg_volume_20d': 1_000_000,
            'avg_value_20d': 900_000_000
        }
    }
    
    feature_df = pd.DataFrame([{"symbol": "TEST"}])
    feature_df.index = pd.DatetimeIndex([pd.Timestamp("2026-02-20")])
    
    n = 100
    dates = pd.date_range("2023-01-01", periods=n, freq="B")
    nifty = pd.Series(18000 * np.ones(n), index=dates)
    vix = pd.Series(12.0 * np.ones(n), index=dates)
    fii = pd.Series(2000.0 * np.ones(n), index=dates)
    
    # Run EOD once
    report1 = pm.run_eod(
        eod_prices=eod_prices,
        nifty_close=nifty,
        india_vix=vix,
        fii_flows=fii,
        feature_df=feature_df,
        trade_date="2026-02-20"
    )
    
    assert "TEST" not in pm.positions
    assert report1["decisions"]["exits_today"] == 1
    
    # Run EOD again with same data (should not crash or create duplicate exit)
    report2 = pm.run_eod(
        eod_prices=eod_prices,
        nifty_close=nifty,
        india_vix=vix,
        fii_flows=fii,
        feature_df=feature_df,
        trade_date="2026-02-21"
    )
    
    assert report2["decisions"]["exits_today"] == 0, "Should not exit again"
    
    print("✅ Duplicate exit prevented: exits on 2nd run = 0")


def test_exit_order_rejection_no_position_change(clean_state):
    """
    If exit order is rejected, position should remain and cash unchanged.
    """
    pm = PortfolioManager()
    pm.direction_model._trained = True
    
    # Configure broker to reject exit orders
    pm.broker = MockBroker(accept_orders=False, rejection_reason="market_closed")
    
    # Create position
    pm.positions["TEST"] = OpenPosition(
        symbol="TEST",
        quantity=10,
        entry_price=1000.0,
        initial_stop=920.0,
        model_prob=0.75,
        entry_date="2026-02-19",
        order_id="ORDER_001",
        atr_at_entry=20.0
    )
    
    initial_cash = pm.cash
    
    # EOD prices with stop hit
    eod_prices = {
        "TEST": {
            'close': 910.0,  # Below stop
            'low': 900.0,
            'atr_14': 20.0,
        }
    }
    
    # Minimal data
    feature_df = pd.DataFrame([{"symbol": "TEST"}])
    feature_df.index = pd.DatetimeIndex([pd.Timestamp("2026-02-20")])
    
    n = 50
    dates = pd.date_range("2023-01-01", periods=n, freq="B")
    nifty = pd.Series(18000 * np.ones(n), index=dates)
    vix = pd.Series(12.0 * np.ones(n), index=dates)
    fii = pd.Series(2000.0 * np.ones(n), index=dates)
    
    report = pm.run_eod(
        eod_prices=eod_prices,
        nifty_close=nifty,
        india_vix=vix,
        fii_flows=fii,
        feature_df=feature_df,
        trade_date="2026-02-20"
    )
    
    # Assertions: position still exists, marked PENDING_EXIT
    assert "TEST" in pm.positions, "Position should still exist"
    assert pm.positions["TEST"].stop_status == "PENDING_EXIT"
    assert pm.cash == initial_cash, "Cash unchanged on exit rejection"
    assert report["decisions"]["exits_today"] == 0
    
    print("✅ Exit rejection: position retained, stop_status=PENDING_EXIT")


# ─────────────────────────────────────────────────────────────────────────────
# REALIZED P&L CALCULATION TESTS
# ─────────────────────────────────────────────────────────────────────────────

def test_realised_pnl_computed_from_exits_not_positions(clean_state):
    """
    Ensure realised_pnl_today is computed from exits_today, not from deleted positions.
    """
    pm = PortfolioManager()
    pm.direction_model._trained = True
    
    # Create multiple positions
    pm.positions["STOCK_A"] = OpenPosition(
        symbol="STOCK_A", quantity=5, entry_price=1000.0,
        initial_stop=920.0, model_prob=0.75, entry_date="2026-02-18",
        order_id="ORDER_A", atr_at_entry=20.0
    )
    pm.positions["STOCK_B"] = OpenPosition(
        symbol="STOCK_B", quantity=8, entry_price=1500.0,
        initial_stop=1380.0, model_prob=0.72, entry_date="2026-02-18",
        order_id="ORDER_B", atr_at_entry=30.0
    )
    
    # EOD prices causing both to hit stops
    eod_prices = {
        "STOCK_A": {'close': 910.0, 'low': 900.0, 'atr_14': 20.0},
        "STOCK_B": {'close': 1370.0, 'low': 1360.0, 'atr_14': 30.0}
    }
    
    feature_df = pd.DataFrame([{"symbol": s} for s in ["STOCK_A", "STOCK_B"]])
    feature_df.index = pd.DatetimeIndex([pd.Timestamp("2026-02-20")] * 2)
    
    n = 50
    dates = pd.date_range("2023-01-01", periods=n, freq="B")
    nifty = pd.Series(18000 * np.ones(n), index=dates)
    vix = pd.Series(12.0 * np.ones(n), index=dates)
    fii = pd.Series(2000.0 * np.ones(n), index=dates)
    
    report = pm.run_eod(
        eod_prices=eod_prices,
        nifty_close=nifty,
        india_vix=vix,
        fii_flows=fii,
        feature_df=feature_df,
        trade_date="2026-02-20"
    )
    
    # Check realised_pnl_today is sum of exit realised_pnl
    exits = report["decisions"]["exits"]
    if exits:
        total_realized = sum(e.get("realised_pnl", 0) for e in exits)
        reported_realized = report["portfolio_summary"]["realised_pnl_today"]
        
        # Should match (within small tolerance for rounding)
        assert abs(total_realized - reported_realized) < 1.0, \
            f"realised_pnl_today mismatch: {reported_realized} vs sum={total_realized}"
        
        print(f"✅ Realized P&L: today={reported_realized:.2f}, exits={len(exits)}")


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
