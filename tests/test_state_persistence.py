"""
=============================================================================
TESTS — State Persistence, Atomic Write, Broker Reconciliation
=============================================================================
Tests cover save/load round-trip idempotence, atomic write safety,
and broker reconciliation detecting mismatches.

Run: pytest tests/test_state_persistence.py -v --tb=short
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import json
import pytest
import tempfile
from unittest.mock import MagicMock, patch

from portfolio_manager import PortfolioManager, OpenPosition
from utils.fs_atomic import atomic_write_json
from audit.logger import AuditEventType


# ─────────────────────────────────────────────────────────────────────────────
# ATOMIC WRITE
# ─────────────────────────────────────────────────────────────────────────────

class TestAtomicWrite:
    """atomic_write_json must produce a valid, complete JSON file."""

    def test_atomic_write_creates_file(self):
        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, "test_state.json")
            data = {"cash": 85000.0, "positions": {"INFY": {"qty": 10}}}
            atomic_write_json(path, data)

            assert os.path.exists(path)
            with open(path) as f:
                loaded = json.load(f)
            assert loaded == data

    def test_atomic_write_overwrites_existing(self):
        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, "test_state.json")
            atomic_write_json(path, {"v": 1})
            atomic_write_json(path, {"v": 2})

            with open(path) as f:
                loaded = json.load(f)
            assert loaded["v"] == 2

    def test_atomic_write_creates_parent_dirs(self):
        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, "sub", "dir", "state.json")
            atomic_write_json(path, {"ok": True})
            assert os.path.exists(path)


# ─────────────────────────────────────────────────────────────────────────────
# SAVE / LOAD STATE — IDEMPOTENCE
# ─────────────────────────────────────────────────────────────────────────────

class TestSaveLoadState:
    """save_state → load_state must produce identical portfolio state."""

    def test_save_load_state_idempotence(self):
        pm1 = PortfolioManager()
        pm1.cash = 72000.50
        pm1.gold_value = 10000.0
        pm1.realised_pnl = -450.75

        # Add some positions
        pm1.positions["INFY"] = OpenPosition(
            symbol="INFY", quantity=10, entry_price=1500.0,
            initial_stop=1400.0, model_prob=0.80,
            entry_date="2025-01-10", order_id="oid_infy",
            atr_at_entry=55.0,
        )
        pm1.positions["INFY"].current_stop = 1420.0
        pm1.positions["INFY"].stop_status = "TRAILING"
        pm1.positions["INFY"].avg_cost = 1500.0

        pm1.positions["RELIANCE"] = OpenPosition(
            symbol="RELIANCE", quantity=5, entry_price=2600.0,
            initial_stop=2500.0, model_prob=0.72,
            entry_date="2025-02-01", order_id="oid_rel",
            atr_at_entry=80.0,
        )
        pm1.positions["RELIANCE"].avg_cost = 2600.0

        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, "portfolio_state.json")
            pm1.save_state(path)

            # Load into fresh PM
            pm2 = PortfolioManager()
            pm2.load_state(path)

            assert pm2.cash == pytest.approx(pm1.cash, abs=1e-6)
            assert pm2.gold_value == pytest.approx(pm1.gold_value, abs=1e-6)
            assert pm2.realised_pnl == pytest.approx(pm1.realised_pnl, abs=1e-6)
            assert set(pm2.positions.keys()) == set(pm1.positions.keys())

            for sym in pm1.positions:
                p1 = pm1.positions[sym]
                p2 = pm2.positions[sym]
                assert p2.quantity == p1.quantity
                assert p2.avg_cost == pytest.approx(p1.avg_cost, abs=1e-6)
                assert p2.current_stop == pytest.approx(p1.current_stop, abs=1e-6)
                assert p2.initial_stop == pytest.approx(p1.initial_stop, abs=1e-6)
                assert p2.model_prob == pytest.approx(p1.model_prob, abs=1e-6)
                assert p2.stop_status == p1.stop_status
                assert p2.entry_date == p1.entry_date

    def test_load_state_nonexistent_file(self):
        """Loading a non-existent file should not crash."""
        pm = PortfolioManager()
        initial_cash = pm.cash
        pm.load_state("__nonexistent_state_file_12345.json")
        assert pm.cash == initial_cash  # unchanged


# ─────────────────────────────────────────────────────────────────────────────
# BROKER RECONCILIATION
# ─────────────────────────────────────────────────────────────────────────────

class TestReconcileWithBroker:
    """reconcile_with_broker must detect and audit mismatches."""

    def test_reconcile_matching_state(self):
        """No discrepancies when local matches broker."""
        pm = PortfolioManager()
        pm.positions["INFY"] = OpenPosition(
            symbol="INFY", quantity=10, entry_price=1500.0,
            initial_stop=1400.0, model_prob=0.80,
            entry_date="2025-01-10", order_id="oid_1",
            atr_at_entry=55.0,
        )

        pm.broker = MagicMock()
        pm.broker.get_open_positions.return_value = [
            {"symbol": "INFY", "quantity": 10, "avg_price": 1500.0}
        ]

        # Should complete without error, no audit events for mismatches
        audit_calls_before = pm.audit.write if hasattr(pm.audit, 'write') else None
        pm.reconcile_with_broker()
        # No assertion on audit calls for matching — just verifying no crash

    def test_reconcile_missing_in_local(self):
        """Broker has a position not in local state → RECONCILE_MISSING_POSITION."""
        pm = PortfolioManager()
        pm.audit = MagicMock()

        pm.broker = MagicMock()
        pm.broker.get_open_positions.return_value = [
            {"symbol": "WIPRO", "quantity": 20, "avg_price": 400.0}
        ]

        pm.reconcile_with_broker()

        # Should have written a RECONCILE_MISSING_POSITION event
        calls = [c for c in pm.audit.write.call_args_list
                 if c[0][0] == AuditEventType.RECONCILE_MISSING_POSITION]
        assert len(calls) == 1
        assert calls[0][0][1]["symbol"] == "WIPRO"

    def test_reconcile_stale_in_local(self):
        """Local has a position not in broker → RECONCILE_STALE_POSITION."""
        pm = PortfolioManager()
        pm.positions["GHOST"] = OpenPosition(
            symbol="GHOST", quantity=10, entry_price=100.0,
            initial_stop=90.0, model_prob=0.65,
            entry_date="2025-01-01", order_id="ghost_oid",
            atr_at_entry=5.0,
        )
        pm.audit = MagicMock()
        pm.broker = MagicMock()
        pm.broker.get_open_positions.return_value = []

        pm.reconcile_with_broker()

        calls = [c for c in pm.audit.write.call_args_list
                 if c[0][0] == AuditEventType.RECONCILE_STALE_POSITION]
        assert len(calls) == 1
        assert calls[0][0][1]["symbol"] == "GHOST"

    def test_reconcile_qty_mismatch(self):
        """Quantity mismatch between local and broker → audit event."""
        pm = PortfolioManager()
        pm.positions["INFY"] = OpenPosition(
            symbol="INFY", quantity=10, entry_price=1500.0,
            initial_stop=1400.0, model_prob=0.80,
            entry_date="2025-01-10", order_id="oid_1",
            atr_at_entry=55.0,
        )
        pm.audit = MagicMock()
        pm.broker = MagicMock()
        pm.broker.get_open_positions.return_value = [
            {"symbol": "INFY", "quantity": 7, "avg_price": 1500.0}
        ]

        pm.reconcile_with_broker()

        calls = [c for c in pm.audit.write.call_args_list
                 if c[0][0] == AuditEventType.RECONCILE_STALE_POSITION]
        assert len(calls) == 1
        payload = calls[0][0][1]
        assert payload["local_qty"] == 10
        assert payload["broker_qty"] == 7

    def test_reconcile_broker_error_no_crash(self):
        """If broker.get_open_positions raises, reconcile should not crash."""
        pm = PortfolioManager()
        pm.broker = MagicMock()
        pm.broker.get_open_positions.side_effect = RuntimeError("network error")
        # Should not raise
        pm.reconcile_with_broker()
