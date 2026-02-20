# order_book.py
import os
import json
import logging
from datetime import datetime
from typing import Dict, List, Optional
from dataclasses import dataclass, asdict, field

log = logging.getLogger("order_book")


@dataclass
class OrderBookEntry:
    client_order_id: str
    symbol: str
    side: str
    requested_qty: int
    entry_price: float

    filled_qty: int = 0
    remaining_qty: int = 0
    status: str = "PENDING"

    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())
    last_update: str = field(default_factory=lambda: datetime.now().isoformat())

    avg_fill_price: float = 0.0

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "OrderBookEntry":
        d = dict(d)
        d.setdefault("filled_qty", 0)
        d.setdefault("remaining_qty", d.get("requested_qty", 0) - d.get("filled_qty", 0))
        d.setdefault("status", "PENDING")
        d.setdefault("avg_fill_price", 0.0)
        d.setdefault("timestamp", datetime.now().isoformat())
        d.setdefault("last_update", d["timestamp"])
        return cls(**d)


class OrderBook:
    """
    Persistent order registry using append-only JSONL.
    """

    def __init__(self, path: str = "order_book.jsonl"):
        self.path = path
        self._orders: Dict[str, OrderBookEntry] = {}
        self._load()

    def _load(self):
        if not os.path.exists(self.path):
            log.info(f"[ORDER_BOOK] No existing order book at {self.path}, starting fresh")
            return

        try:
            with open(self.path, "r", encoding="utf-8") as f:
                for line_num, line in enumerate(f, 1):
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        record = json.loads(line)
                        action = record.get("action")
                        if action == "NEW":
                            entry = OrderBookEntry.from_dict(record["entry"])
                            self._orders[entry.client_order_id] = entry
                        elif action == "UPDATE":
                            cid = record["client_order_id"]
                            if cid in self._orders:
                                entry = self._orders[cid]
                                entry.filled_qty = record.get("filled_qty", entry.filled_qty)
                                entry.remaining_qty = record.get("remaining_qty", entry.remaining_qty)
                                entry.status = record.get("status", entry.status)
                                entry.last_update = record.get("timestamp", datetime.now().isoformat())
                                entry.avg_fill_price = record.get("avg_fill_price", entry.avg_fill_price)
                        else:
                            log.warning(f"[ORDER_BOOK] Unknown action '{action}' at line {line_num}")
                    except Exception as e:
                        log.error(f"[ORDER_BOOK] Parse error at line {line_num}: {e}")
            log.info(f"[ORDER_BOOK] Loaded {len(self._orders)} orders from {self.path}")
        except Exception as e:
            log.error(f"[ORDER_BOOK] Failed to load {self.path}: {e}")

    def add_order(self, client_order_id: str, symbol: str, side: str,
                  requested_qty: int, entry_price: float) -> OrderBookEntry:
        timestamp = datetime.now().isoformat()
        entry = OrderBookEntry(
            client_order_id=client_order_id,
            symbol=symbol,
            side=side,
            requested_qty=requested_qty,
            filled_qty=0,
            remaining_qty=requested_qty,
            status="PENDING",
            entry_price=entry_price,
            timestamp=timestamp,
            last_update=timestamp
        )

        self._orders[client_order_id] = entry
        self._append({"action": "NEW", "entry": entry.to_dict()})

        log.info(f"[ORDER_BOOK] Added order {client_order_id}: {side} {requested_qty} {symbol}")
        return entry

    def update_fill(self, client_order_id: str, filled_qty: int, avg_fill_price: float = None, status: str = "PARTIAL"):
        """
        Update fill status for an order.

        Args:
            client_order_id: Order to update
            filled_qty: Total filled quantity (cumulative)
            avg_fill_price: average fill price (optional)
            status: New status ("PARTIAL" | "FILLED")
        """
        if client_order_id not in self._orders:
            log.warning(f"[ORDER_BOOK] Cannot update unknown order {client_order_id}")
            return

        entry = self._orders[client_order_id]
        entry.filled_qty = filled_qty
        entry.remaining_qty = entry.requested_qty - filled_qty
        entry.status = status if entry.remaining_qty > 0 else "FILLED"
        if avg_fill_price is not None:
            try:
                entry.avg_fill_price = float(avg_fill_price)
            except Exception:
                pass

        entry.last_update = datetime.now().isoformat()

        rec = {
            "action": "UPDATE",
            "client_order_id": client_order_id,
            "filled_qty": filled_qty,
            "remaining_qty": entry.remaining_qty,
            "status": entry.status,
            "timestamp": entry.last_update
        }
        if avg_fill_price is not None:
            rec["avg_fill_price"] = round(float(avg_fill_price), 2)

        self._append(rec)

        log.info(
            f"[ORDER_BOOK] Updated {client_order_id}: filled={filled_qty}/{entry.requested_qty}, "
            f"status={entry.status}" + (f", avg_fill_price={rec.get('avg_fill_price')}" if "avg_fill_price" in rec else "")
        )

    def cancel_order(self, client_order_id: str):
        if client_order_id not in self._orders:
            return

        entry = self._orders[client_order_id]
        entry.status = "CANCELLED"
        entry.last_update = datetime.now().isoformat()

        self._append({
            "action": "UPDATE",
            "client_order_id": client_order_id,
            "status": "CANCELLED",
            "timestamp": entry.last_update
        })

        log.info(f"[ORDER_BOOK] Cancelled {client_order_id}")

    def get_order(self, client_order_id: str) -> Optional[OrderBookEntry]:
        return self._orders.get(client_order_id)

    def has_order(self, client_order_id: str) -> bool:
        return client_order_id in self._orders

    def get_outstanding_orders(self) -> List[OrderBookEntry]:
        return [e for e in self._orders.values() if e.remaining_qty > 0 and e.status not in ["FILLED", "CANCELLED", "REJECTED"]]

    def get_all_orders(self) -> List[OrderBookEntry]:
        return list(self._orders.values())

    def _append(self, record: dict):
        """
        Append a record to the JSONL file with fsync to ensure durability.
        """
        # Ensure the directory exists
        d = os.path.dirname(self.path)
        if d and not os.path.exists(d):
            try:
                os.makedirs(d, exist_ok=True)
            except Exception:
                pass

        # open file descriptor and write atomically
        fd = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_APPEND)
        try:
            line = json.dumps(record, default=str) + "\n"
            os.write(fd, line.encode("utf-8"))
            try:
                os.fsync(fd)
            except Exception:
                # fsync may fail on some platforms (or in tests); ignore but continue
                pass
        finally:
            os.close(fd)
