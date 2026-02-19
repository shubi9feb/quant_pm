"""
=============================================================================
ORDER BOOK — Persistent Outstanding Order Registry
=============================================================================
Tracks accepted-but-unfilled (or partially filled) orders in a JSONL file
so that on restart, reconcile_with_broker() can detect unmatched fills.
"""

import json
import os
import logging
from datetime import datetime
from typing import Dict, List, Optional

log = logging.getLogger("order_book")

DEFAULT_ORDER_BOOK_PATH = "order_book.jsonl"


class OutstandingOrderBook:
    """
    Append-only JSONL registry of outstanding orders.

    Each entry records:
      - client_order_id
      - symbol
      - side  (BUY / SELL)
      - requested_qty
      - filled_qty
      - remaining_qty
      - status  (OUTSTANDING / RECONCILED / CANCELLED)
      - timestamp
    """

    def __init__(self, path: str = DEFAULT_ORDER_BOOK_PATH):
        self.path = path
        self._orders: Dict[str, Dict] = {}
        self._load()

    # ── persistence ──────────────────────────────────────────────────────────

    def _load(self):
        """Load existing entries from JSONL file."""
        if not os.path.exists(self.path):
            return
        try:
            with open(self.path, "r") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    entry = json.loads(line)
                    cid = entry.get("client_order_id")
                    if cid:
                        self._orders[cid] = entry
        except Exception as e:
            log.warning(f"[ORDER_BOOK] Failed to load {self.path}: {e}")

    def _flush(self):
        """Rewrite the entire registry (small file, so acceptable)."""
        from utils.fs_atomic import atomic_write_json
        # Write as JSONL (one JSON object per line)
        dirn = os.path.dirname(self.path) or "."
        os.makedirs(dirn, exist_ok=True)
        tmp_path = self.path + ".tmp"
        try:
            with open(tmp_path, "w") as f:
                for entry in self._orders.values():
                    f.write(json.dumps(entry, default=str) + "\n")
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp_path, self.path)
        except BaseException:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
            raise

    # ── public API ───────────────────────────────────────────────────────────

    def record_outstanding(
        self,
        client_order_id: str,
        symbol: str,
        side: str,
        requested_qty: int,
        filled_qty: int,
    ):
        """Record an order that has remaining unfilled quantity."""
        remaining = requested_qty - filled_qty
        if remaining <= 0:
            return  # fully filled, nothing to track
        self._orders[client_order_id] = {
            "client_order_id": client_order_id,
            "symbol": symbol,
            "side": side,
            "requested_qty": requested_qty,
            "filled_qty": filled_qty,
            "remaining_qty": remaining,
            "status": "OUTSTANDING",
            "timestamp": datetime.now().isoformat(),
        }
        self._flush()
        log.info(
            f"[ORDER_BOOK] Recorded outstanding: {client_order_id} "
            f"{symbol} {side} remaining={remaining}"
        )

    def mark_reconciled(self, client_order_id: str):
        """Mark an outstanding order as reconciled."""
        if client_order_id in self._orders:
            self._orders[client_order_id]["status"] = "RECONCILED"
            self._flush()

    def mark_cancelled(self, client_order_id: str):
        """Mark an outstanding order as cancelled."""
        if client_order_id in self._orders:
            self._orders[client_order_id]["status"] = "CANCELLED"
            self._flush()

    def get_outstanding(self) -> List[Dict]:
        """Return all orders with OUTSTANDING status."""
        return [
            dict(o) for o in self._orders.values()
            if o.get("status") == "OUTSTANDING"
        ]

    def get_all(self) -> List[Dict]:
        """Return all recorded orders."""
        return list(self._orders.values())
