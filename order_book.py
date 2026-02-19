"""
=============================================================================
ORDER BOOK — Persistent Outstanding Order Registry
=============================================================================
Tracks all accepted orders with their fill status in a JSONL append-only log.
Used for reconciliation and recovery after restarts.
"""

import os
import json
import logging
from datetime import datetime
from typing import Dict, List, Optional
from dataclasses import dataclass, asdict

log = logging.getLogger("order_book")


@dataclass
class OrderBookEntry:
    """Single order book entry tracking an order's lifecycle."""
    client_order_id: str
    symbol: str
    side: str                 # "BUY" | "SELL"
    requested_qty: int
    filled_qty: int
    remaining_qty: int
    status: str               # "PENDING" | "PARTIAL" | "FILLED" | "REJECTED" | "CANCELLED"
    entry_price: float
    timestamp: str
    last_update: str
    
    def to_dict(self) -> dict:
        return asdict(self)
    
    @classmethod
    def from_dict(cls, d: dict) -> "OrderBookEntry":
        return cls(**d)


class OrderBook:
    """
    Persistent order registry using append-only JSONL.
    
    Each line is either:
    - NEW: {"action": "NEW", "entry": {...}}
    - UPDATE: {"action": "UPDATE", "client_order_id": "...", "filled_qty": ..., "status": "..."}
    
    On initialization, the full state is reconstructed by replaying the log.
    """
    
    def __init__(self, path: str = "order_book.jsonl"):
        self.path = path
        self._orders: Dict[str, OrderBookEntry] = {}
        self._load()
    
    def _load(self):
        """Replay the JSONL log to rebuild current state."""
        if not os.path.exists(self.path):
            log.info(f"[ORDER_BOOK] No existing order book at {self.path}, starting fresh")
            return
        
        try:
            with open(self.path, "r") as f:
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
                        
                        else:
                            log.warning(f"[ORDER_BOOK] Unknown action '{action}' at line {line_num}")
                    
                    except Exception as e:
                        log.error(f"[ORDER_BOOK] Parse error at line {line_num}: {e}")
            
            log.info(f"[ORDER_BOOK] Loaded {len(self._orders)} orders from {self.path}")
        
        except Exception as e:
            log.error(f"[ORDER_BOOK] Failed to load {self.path}: {e}")
    
    def add_order(self, client_order_id: str, symbol: str, side: str, 
                  requested_qty: int, entry_price: float) -> OrderBookEntry:
        """
        Add a new order to the book (called when broker accepts an order).
        
        Returns:
            The created OrderBookEntry
        """
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
    
    def update_fill(self, client_order_id: str, filled_qty: int, status: str = "PARTIAL"):
        """
        Update fill status for an order.
        
        Args:
            client_order_id: Order to update
            filled_qty: Total filled quantity (cumulative)
            status: New status ("PARTIAL" | "FILLED")
        """
        if client_order_id not in self._orders:
            log.warning(f"[ORDER_BOOK] Cannot update unknown order {client_order_id}")
            return
        
        entry = self._orders[client_order_id]
        entry.filled_qty = filled_qty
        entry.remaining_qty = entry.requested_qty - filled_qty
        entry.status = status if entry.remaining_qty > 0 else "FILLED"
        entry.last_update = datetime.now().isoformat()
        
        self._append({
            "action": "UPDATE",
            "client_order_id": client_order_id,
            "filled_qty": filled_qty,
            "remaining_qty": entry.remaining_qty,
            "status": entry.status,
            "timestamp": entry.last_update
        })
        
        log.info(f"[ORDER_BOOK] Updated {client_order_id}: filled={filled_qty}/{entry.requested_qty}, status={entry.status}")
    
    def cancel_order(self, client_order_id: str):
        """Mark an order as cancelled."""
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
        """Retrieve order by client_order_id."""
        return self._orders.get(client_order_id)
    
    def get_outstanding_orders(self) -> List[OrderBookEntry]:
        """Return all orders with remaining quantity > 0."""
        return [e for e in self._orders.values() if e.remaining_qty > 0 and e.status not in ["FILLED", "CANCELLED", "REJECTED"]]
    
    def get_all_orders(self) -> List[OrderBookEntry]:
        """Return all orders in the book."""
        return list(self._orders.values())
    
    def _append(self, record: dict):
        """Append a record to the JSONL file with fsync."""
        fd = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_APPEND)
        try:
            line = json.dumps(record, default=str) + "\n"
            os.write(fd, line.encode())
            os.fsync(fd)
        finally:
            os.close(fd)
