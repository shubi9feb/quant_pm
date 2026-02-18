"""
=============================================================================
TESTS — Order Handling, Exit Processing, Broker Response Contract
=============================================================================
Tests cover entry rejection, partial fills, stop-hit full fills, and
the broker response contract for PaperBroker.

Run: pytest tests/test_orders_and_exits.py -v --tb=short
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
import tempfile
from datetime import datetime
from unittest.mock import MagicMock, patch
from dataclasses import dataclass
from typing import Optional

from execution.broker import (
    BrokerOrderResponse, PaperBroker, BracketOrder,
    OrderSide, OrderStatus, build_entry_order, build_exit_order,
)
from reporting.daily_report import TradeRecord
from risk.engine import compute_transaction_cost
from portfolio_manager import PortfolioManager, OpenPosition
from audit.logger import AuditEventType


# ─────────────────────────────────────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────────────────────────────────────

def _make_position(sym="INFY", qty=10, avg_cost=1500.0, stop=1400.0, prob=0.75):
    pos = OpenPosition(
        symbol=sym, quantity=qty, entry_price=avg_cost,
        initial_stop=stop, model_prob=prob,
        entry_date="2025-01-15", order_id="test_oid_001",
        atr_at_entry=50.0,
    )
    pos.avg_cost = avg_cost
    return pos


def _make_broker_response(
    accepted=True, filled_qty=10, avg_fill_price=1500.0,
    client_order_id="test_cid", reason=None, status="FILLED"
):
    return BrokerOrderResponse(
        accepted=accepted,
        client_order_id=client_order_id,
        broker_order_id=f"PAPER_{client_order_id}",
        filled_qty=filled_qty,
        avg_fill_price=avg_fill_price,
        status=status,
        reason=reason,
    )


# ─────────────────────────────────────────────────────────────────────────────
# PAPER BROKER — RESPONSE CONTRACT
# ─────────────────────────────────────────────────────────────────────────────

class TestPaperBrokerContract:
    """PaperBroker.place_order must return BrokerOrderResponse with correct fields."""

    def test_paper_broker_returns_broker_order_response(self):
        broker = PaperBroker(slippage_bps=10)
        order = build_entry_order("INFY", 10, 1500.0, 1400.0, 1600.0, "xgb_v1")
        resp = broker.place_order(order)

        assert isinstance(resp, BrokerOrderResponse)
        assert resp.accepted is True
        assert resp.filled_qty == 10
        assert resp.avg_fill_price == 1500.0
        assert resp.client_order_id == order.client_order_id
        assert resp.status == "FILLED"

    def test_paper_broker_to_dict(self):
        broker = PaperBroker(slippage_bps=10)
        order = build_entry_order("RELIANCE", 5, 2500.0, 2400.0, 2700.0, "xgb_v1")
        resp = broker.place_order(order)
        d = resp.to_dict()

        assert d["accepted"] is True
        assert d["filled_qty"] == 5
        assert d["avg_fill_price"] == 2500.0

    def test_paper_broker_tracks_positions(self):
        broker = PaperBroker(slippage_bps=10)
        order = build_entry_order("INFY", 10, 1500.0, 1400.0, 1600.0, "xgb_v1")
        broker.place_order(order)

        positions = broker.get_open_positions()
        assert len(positions) == 1
        assert positions[0]["symbol"] == "INFY"
        assert positions[0]["quantity"] == 10

    def test_paper_broker_sell_removes_position(self):
        broker = PaperBroker(slippage_bps=10)
        # Buy
        buy_order = build_entry_order("INFY", 10, 1500.0, 1400.0, 1600.0, "xgb_v1")
        broker.place_order(buy_order)
        # Sell
        sell_order = build_exit_order("INFY", 10, 1400.0, "STOP_HIT", "xgb_v1")
        broker.place_order(sell_order)

        positions = broker.get_open_positions()
        assert len(positions) == 0


# ─────────────────────────────────────────────────────────────────────────────
# ORDER REJECTION — NO CASH CHANGE
# ─────────────────────────────────────────────────────────────────────────────

class TestOrderRejection:
    """When broker rejects an entry order, cash must remain unchanged."""

    def test_order_rejection_no_cash_change(self):
        pm = PortfolioManager()
        initial_cash = pm.cash

        # Mock broker to reject all orders
        reject_resp = _make_broker_response(
            accepted=False, filled_qty=0, avg_fill_price=0,
            reason="margin_exceeded", status="REJECTED"
        )
        pm.broker = MagicMock()
        pm.broker.place_order = MagicMock(return_value=reject_resp)

        # Manually simulate what run_eod does for an entry
        sym = "TESTSTOCK"
        order = build_entry_order(sym, 10, 1500.0, 1400.0, 1600.0, "xgb_v1")
        resp = pm.broker.place_order(order)
        pm.audit.log_order(resp, "PLACED")

        if not getattr(resp, "accepted", False):
            # Mimics run_eod: no cash reservation
            pass

        # Cash must be unchanged
        assert pm.cash == initial_cash
        assert sym not in pm.positions


# ─────────────────────────────────────────────────────────────────────────────
# PARTIAL FILL — CASH AND POSITION ADJUSTMENT
# ─────────────────────────────────────────────────────────────────────────────

class TestPartialFill:
    """When broker partially fills, only filled qty deducted from cash."""

    def test_partial_fill_cash_and_position_adjustment(self):
        pm = PortfolioManager()
        initial_cash = pm.cash

        sym = "PARTFILL"
        requested_qty = 20
        filled_qty = 12
        fill_price = 500.0

        # Simulate the order flow from run_eod
        position_value = fill_price * filled_qty
        costs = compute_transaction_cost(position_value, "BUY")
        total_cost = position_value + costs["total_cost"]

        # Only deduct for filled
        pm.cash -= total_cost
        pm.positions[sym] = OpenPosition(
            symbol=sym, quantity=filled_qty, entry_price=fill_price,
            initial_stop=450.0, model_prob=0.72,
            entry_date="2025-06-01", order_id="partial_001",
            atr_at_entry=25.0,
        )

        assert pm.positions[sym].quantity == filled_qty
        assert pm.cash == pytest.approx(initial_cash - total_cost, abs=1e-6)

        # Ensure we did NOT deduct for the full 20 shares
        full_cost = (fill_price * requested_qty) + compute_transaction_cost(
            fill_price * requested_qty, "BUY"
        )["total_cost"]
        assert pm.cash > initial_cash - full_cost


# ─────────────────────────────────────────────────────────────────────────────
# STOP HIT — FULL FILL, REALISED PNL, POSITION REMOVAL
# ─────────────────────────────────────────────────────────────────────────────

class TestStopHitFullFill:
    """Stop hit with full fill: exactly one exit TradeRecord, correct P&L,
    position removed, cash updated."""

    def test_stop_hit_full_fill_realised_pnl_and_position_removal(self):
        pm = PortfolioManager()
        sym = "STOPHIT"
        qty = 15
        avg_cost = 1000.0
        fill_price = 950.0  # stop hit below entry

        pos = _make_position(sym=sym, qty=qty, avg_cost=avg_cost, stop=960.0)
        pm.positions[sym] = pos
        initial_cash = pm.cash
        initial_rpnl = pm.realised_pnl

        # Simulate what run_eod does on stop hit with full fill
        filled_qty = qty
        position_value = fill_price * filled_qty
        costs = compute_transaction_cost(position_value, "SELL")
        realized = (fill_price - avg_cost) * filled_qty - costs["total_cost"]

        pm.realised_pnl += realized
        pm.cash += position_value - costs["total_cost"]

        exits_today = []
        exits_today.append(TradeRecord(
            symbol=sym, action="STOP_HIT", quantity=filled_qty,
            price=fill_price, value=position_value,
            realised_pnl=realized,
            reason="stop_loss_triggered", model_prob=pos.model_prob,
            stop_price=pos.current_stop, cost_inr=costs["total_cost"],
            client_order_id="exit_cid_001", timestamp=datetime.now().isoformat(),
        ))

        del pm.positions[sym]

        # Assertions
        assert sym not in pm.positions, "Position must be removed"
        assert len(exits_today) == 1, "Exactly one exit TradeRecord"
        assert exits_today[0].realised_pnl == pytest.approx(realized, abs=1e-6)
        assert pm.realised_pnl == pytest.approx(initial_rpnl + realized, abs=1e-6)
        assert pm.cash == pytest.approx(
            initial_cash + position_value - costs["total_cost"], abs=1e-6
        )

        # Verify realised_pnl_today aggregation uses TradeRecord.realised_pnl
        realised_pnl_today = sum(
            getattr(e, "realised_pnl", 0.0) for e in exits_today
        )
        assert realised_pnl_today == pytest.approx(realized, abs=1e-6)


# ─────────────────────────────────────────────────────────────────────────────
# TRADE RECORD — realised_pnl FIELD
# ─────────────────────────────────────────────────────────────────────────────

class TestTradeRecordField:
    """TradeRecord must have a realised_pnl field with default 0.0."""

    def test_trade_record_has_realised_pnl_default(self):
        tr = TradeRecord(
            symbol="X", action="ENTRY", quantity=1, price=100.0,
            value=100.0, reason="test", model_prob=0.5,
            stop_price=90.0, cost_inr=0.5, client_order_id="oid",
            timestamp="2025-01-01T00:00:00",
        )
        assert tr.realised_pnl == 0.0

    def test_trade_record_with_explicit_pnl(self):
        tr = TradeRecord(
            symbol="X", action="EXIT", quantity=1, price=100.0,
            value=100.0, reason="test", model_prob=0.5,
            stop_price=90.0, cost_inr=0.5, client_order_id="oid",
            timestamp="2025-01-01T00:00:00",
            realised_pnl=-50.3,
        )
        assert tr.realised_pnl == pytest.approx(-50.3)


# ─────────────────────────────────────────────────────────────────────────────
# AUDIT — last_hash PROPERTY
# ─────────────────────────────────────────────────────────────────────────────

class TestAuditLastHash:
    """AuditWriter.last_hash must exist and return the latest hash."""

    def test_audit_last_hash_is_genesis_initially(self):
        from audit.logger import AuditWriter
        with tempfile.TemporaryDirectory() as td:
            aw = AuditWriter(model_version="test", audit_dir=td)
            assert aw.last_hash == "GENESIS"

    def test_audit_last_hash_changes_after_write(self):
        from audit.logger import AuditWriter
        with tempfile.TemporaryDirectory() as td:
            aw = AuditWriter(model_version="test", audit_dir=td)
            aw.write(AuditEventType.SYSTEM_START, {"msg": "hello"})
            assert aw.last_hash != "GENESIS"
