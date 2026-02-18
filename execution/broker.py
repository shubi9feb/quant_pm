"""
=============================================================================
EXECUTION LAYER — Bracket Orders, Idempotent IDs, Broker Adapter
=============================================================================
Abstracts over live broker (Zerodha/Kite) and paper trading simulator.
All orders use idempotent client_order_ids to prevent duplicate fills.

Broker Response Contract
------------------------
All broker adapters' ``place_order()`` MUST return a ``BrokerOrderResponse``
with at minimum:
  - accepted       : bool
  - filled_qty     : int
  - avg_fill_price : float
  - client_order_id: str
  - reason         : Optional[str]
  - to_dict()      : callable (returns dict)
"""

import uuid
import hashlib
import json
import logging
import os
from datetime import datetime
from dataclasses import dataclass, field, asdict
from typing import Optional, Dict, List
from enum import Enum

from config.settings import PAPER_MODE, BROKER_API_KEY, BROKER_API_SECRET

log = logging.getLogger("execution")


# ─────────────────────────────────────────────────────────────────────────────
# ORDER DATA CLASSES
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class BrokerOrderResponse:
    accepted: bool
    client_order_id: str
    broker_order_id: Optional[str] = None
    reason: Optional[str] = None
    filled_qty: int = 0
    avg_fill_price: float = 0.0
    status: str = ""
    raw: dict = None

    def to_dict(self):
        return {
            "accepted": self.accepted,
            "client_order_id": self.client_order_id,
            "broker_order_id": self.broker_order_id,
            "filled_qty": self.filled_qty,
            "avg_fill_price": self.avg_fill_price,
            "status": self.status,
        }

class OrderSide(Enum):
    BUY  = "BUY"
    SELL = "SELL"

class OrderType(Enum):
    BRACKET = "BRACKET"
    MARKET  = "MARKET"
    LIMIT   = "LIMIT"
    SL      = "SL"

class OrderStatus(Enum):
    PENDING   = "PENDING"
    PLACED    = "PLACED"
    FILLED    = "FILLED"
    REJECTED  = "REJECTED"
    CANCELLED = "CANCELLED"


@dataclass
class BracketOrder:
    """
    Bracket order: entry + stop-loss + (optional) trailing trigger.
    Idempotent: same inputs always produce same client_order_id.
    """
    symbol:           str
    side:             OrderSide
    quantity:         int
    entry_price:      float         # limit entry price (or 0 for market)
    stop_loss_price:  float         # absolute stop price
    target_price:     float         # optional profit target (0 = no target)
    trailing_trigger: float         # percentage gain to activate trailing (0 = off)
    order_type:       OrderType     = OrderType.BRACKET
    product:          str           = "CNC"           # Cash-and-carry (delivery)
    exchange:         str           = "NSE"
    client_order_id:  str           = field(default="")
    status:           OrderStatus   = OrderStatus.PENDING
    placed_at:        str           = field(default="")
    filled_price:     float         = 0.0
    filled_at:        str           = field(default="")
    broker_order_id:  str           = field(default="")
    reject_reason:    str           = field(default="")
    model_version:    str           = field(default="")
    decision_meta:    Dict          = field(default_factory=dict)

    def __post_init__(self):
        if not self.client_order_id:
            self.client_order_id = self._generate_idempotent_id()
        if not self.placed_at:
            self.placed_at = datetime.now().isoformat()

    def _generate_idempotent_id(self) -> str:
        """
        Deterministic order ID from (symbol + side + quantity + entry + date).
        Placing the same order twice always gets the same ID → idempotent.
        """
        date_str = datetime.now().strftime("%Y%m%d")
        seed = f"{self.symbol}_{self.side.value}_{self.quantity}_{self.entry_price:.2f}_{date_str}"
        hash8 = hashlib.sha256(seed.encode()).hexdigest()[:8].upper()
        return f"QPM_{date_str}_{hash8}"

    def to_dict(self) -> dict:
        d = asdict(self)
        d["side"]   = self.side.value
        d["type"]   = self.order_type.value
        d["status"] = self.status.value
        return d


# ─────────────────────────────────────────────────────────────────────────────
# PAPER TRADING SIMULATOR
# ─────────────────────────────────────────────────────────────────────────────

class PaperBroker:
    """
    End-of-day paper trading simulator.
    Fills BUY at next-day open (with slippage), SELL at next-day open.
    Records all fills for performance tracking.
    """

    def __init__(self, slippage_bps: float = 10):
        self.slippage_bps   = slippage_bps
        self._orders: Dict[str, BracketOrder] = {}
        self._fills:  List[Dict]              = []
        self._positions: Dict[str, Dict]      = {}   # symbol -> position dict

    def place_order(self, order: BracketOrder) -> BrokerOrderResponse:
        """
        Paper trading implementation:
        - Always accepted
        - Immediate full fill at requested entry price
        """
        # Track the order
        self._orders[order.client_order_id] = order
        order.status = OrderStatus.FILLED
        order.broker_order_id = f"PAPER_{order.client_order_id}"

        # Track positions for reconciliation
        sym = order.symbol
        if order.side == OrderSide.BUY:
            existing = self._positions.get(sym, {"symbol": sym, "quantity": 0, "avg_price": 0.0})
            total_qty = existing["quantity"] + order.quantity
            if total_qty > 0:
                existing["avg_price"] = (
                    (existing["quantity"] * existing["avg_price"] + order.quantity * order.entry_price)
                    / total_qty
                )
            existing["quantity"] = total_qty
            existing["symbol"] = sym
            self._positions[sym] = existing
        elif order.side == OrderSide.SELL:
            existing = self._positions.get(sym)
            if existing:
                existing["quantity"] -= order.quantity
                if existing["quantity"] <= 0:
                    del self._positions[sym]

        log.info(
            f"[PAPER] ORDER FILLED | {order.client_order_id} | "
            f"{order.side.value} {order.quantity} {order.symbol} @ ₹{order.entry_price:.2f}"
        )

        return BrokerOrderResponse(
            accepted=True,
            client_order_id=order.client_order_id,
            broker_order_id=f"PAPER_{order.client_order_id}",
            filled_qty=order.quantity,
            avg_fill_price=order.entry_price,
            status="FILLED",
            raw={"mode": "paper"}
        )


    def simulate_eod_fills(self, eod_prices: Dict[str, float]) -> List[Dict]:
        """
        Called after market close. Fills pending orders at EOD price with slippage.
        In production, this would be triggered by actual broker callbacks.
        """
        newly_filled = []
        slippage_mult = 1 + (self.slippage_bps / 10000)

        for cid, order in self._orders.items():
            if order.status != OrderStatus.PLACED:
                continue
            if order.symbol not in eod_prices:
                continue

            market_price = eod_prices[order.symbol]

            # Apply slippage: buy higher, sell lower
            if order.side == OrderSide.BUY:
                fill_price = market_price * slippage_mult
            else:
                fill_price = market_price / slippage_mult

            order.filled_price = round(fill_price, 2)
            order.filled_at    = datetime.now().isoformat()
            order.status       = OrderStatus.FILLED

            fill_record = {
                "client_order_id": cid,
                "symbol":          order.symbol,
                "side":            order.side.value,
                "quantity":        order.quantity,
                "fill_price":      order.filled_price,
                "fill_value":      round(order.filled_price * order.quantity, 2),
                "filled_at":       order.filled_at,
                "model_version":   order.model_version,
            }
            self._fills.append(fill_record)
            newly_filled.append(fill_record)

            log.info(f"[PAPER] FILL | {order.symbol} {order.side.value} "
                     f"{order.quantity}@₹{fill_price:.2f} | {cid}")

        return newly_filled

    def cancel_order(self, client_order_id: str) -> bool:
        if client_order_id in self._orders:
            self._orders[client_order_id].status = OrderStatus.CANCELLED
            log.info(f"[PAPER] CANCELLED: {client_order_id}")
            return True
        return False

    def get_fills(self) -> List[Dict]:
        return self._fills.copy()

    def get_order(self, client_order_id: str) -> Optional[BracketOrder]:
        return self._orders.get(client_order_id)

    def get_open_positions(self) -> List[Dict]:
        """Return current broker-side positions for reconciliation."""
        return [dict(p) for p in self._positions.values() if p.get("quantity", 0) > 0]

    def get_open_orders(self) -> List[Dict]:
        """Return outstanding (non-filled, non-cancelled) orders."""
        return [
            o.to_dict() for o in self._orders.values()
            if o.status in (OrderStatus.PENDING, OrderStatus.PLACED)
        ]


# ─────────────────────────────────────────────────────────────────────────────
# LIVE BROKER ADAPTER (Zerodha Kite — template)
# ─────────────────────────────────────────────────────────────────────────────

class ZerodhaBrokerAdapter:
    """
    Live broker adapter for Zerodha Kite Connect API.
    Only activated when PAPER_MODE=false and valid API credentials present.

    NOTE: Requires kiteconnect library and valid API tokens.
    """

    def __init__(self):
        self._kite = None
        self._connected = False

    def connect(self, api_key: str, access_token: str):
        try:
            from kiteconnect import KiteConnect
            self._kite      = KiteConnect(api_key=api_key)
            self._kite.set_access_token(access_token)
            self._connected = True
            log.info("[LIVE] Connected to Zerodha Kite")
        except ImportError:
            raise RuntimeError("kiteconnect not installed. pip install kiteconnect")
        except Exception as e:
            raise RuntimeError(f"Zerodha connection failed: {e}")

    def place_order(self, order: BracketOrder) -> BrokerOrderResponse:
        """Place a bracket order on NSE via Kite Connect and return a structured response."""
        if not self._connected:
            return BrokerOrderResponse(
                accepted=False,
                client_order_id=getattr(order, "client_order_id", None),
                reason="broker_not_connected",
                status="ERROR"
            )

        try:
            # Map to Kite parameters
            kite_resp = self._kite.place_order(
                tradingsymbol    = order.symbol,
                exchange         = order.exchange,
                transaction_type = order.side.value,
                quantity         = order.quantity,
                order_type       = "LIMIT" if order.entry_price > 0 else "MARKET",
                product          = order.product,
                price            = order.entry_price if order.entry_price > 0 else None,
                stoploss         = (order.entry_price - order.stop_loss_price) if getattr(order, "stop_loss_price", None) else None,
                squareoff        = (order.target_price - order.entry_price) if getattr(order, "target_price", None) else None,
                trailing_stoploss= None,
                variety          = "bo",
                tag              = order.client_order_id[:20]
            )

            # kite_resp can be either an order id (int/str) or a dict depending on adapter
            broker_order_id = None
            status = "UNKNOWN"
            raw = None

            # Normalise kite response
            if isinstance(kite_resp, (int, str)):
                broker_order_id = str(kite_resp)
                status = "PLACED"
                raw = {"order_id": broker_order_id}
            elif isinstance(kite_resp, dict):
                raw = kite_resp
                broker_order_id = str(kite_resp.get("order_id") or kite_resp.get("order_id_str") or "")
                status = kite_resp.get("status", "PLACED")
            else:
                raw = {"raw_response": str(kite_resp)}
                status = "PLACED"

            # Mark the original order fields for traceability (optional)
            order.status = OrderStatus.PLACED
            order.broker_order_id = broker_order_id
            order.placed_at = datetime.now().isoformat()

            log.info(f"[LIVE] ORDER PLACED | broker_id={broker_order_id} | client_id={order.client_order_id}")

            # NOTE: Kite Connect does not always return fill info immediately for BO orders.
            # We return accepted=True and zero fills by default; fills are reported later via websocket/webhook.
            return BrokerOrderResponse(
                accepted=True,
                client_order_id=order.client_order_id,
                broker_order_id=broker_order_id,
                filled_qty=0,
                avg_fill_price=0.0,
                status=status,
                raw=raw
            )

        except Exception as e:
            log.error(f"[LIVE] ORDER REJECTED | {order.symbol}: {e}")
            order.status = OrderStatus.REJECTED
            order.reject_reason = str(e)

            return BrokerOrderResponse(
                accepted=False,
                client_order_id=order.client_order_id,
                reason=str(e),
                status="REJECTED",
                raw={"exception": str(e)}
            )

    def get_open_positions(self) -> List[Dict]:
        """Query open positions from Kite Connect."""
        if not self._connected:
            return []
        try:
            positions = self._kite.positions()
            net = positions.get("net", [])
            return [
                {"symbol": p["tradingsymbol"], "quantity": p["quantity"],
                 "avg_price": p.get("average_price", 0)}
                for p in net if p.get("quantity", 0) != 0
            ]
        except Exception as e:
            log.error(f"[LIVE] Failed to fetch positions: {e}")
            return []

    def get_open_orders(self) -> List[Dict]:
        """Query open/pending orders from Kite Connect."""
        if not self._connected:
            return []
        try:
            orders = self._kite.orders()
            return [
                o for o in orders
                if o.get("status") in ("OPEN", "TRIGGER PENDING", "AMO REQ RECEIVED")
            ]
        except Exception as e:
            log.error(f"[LIVE] Failed to fetch orders: {e}")
            return []

    def get_order_status(self, broker_order_id: str) -> Dict:
        """Poll order status from Kite."""
        if not self._connected:
            return {}
        try:
            orders = self._kite.orders()
            for o in orders:
                if str(o["order_id"]) == str(broker_order_id):
                    return o
        except Exception as e:
            log.error(f"[LIVE] Status check error: {e}")
        return {}


# ─────────────────────────────────────────────────────────────────────────────
# BROKER FACTORY
# ─────────────────────────────────────────────────────────────────────────────

def get_broker():
    """
    Returns appropriate broker based on environment.
    Paper mode is default and safe — requires explicit env flag to go live.
    """
    if PAPER_MODE or BROKER_API_KEY == "PAPER_MODE":
        log.info("[BROKER] PAPER MODE active — no live orders will be placed")
        return PaperBroker(slippage_bps=10)
    else:
        log.warning("[BROKER] LIVE MODE — real orders will be placed!")
        return ZerodhaBrokerAdapter()


# ─────────────────────────────────────────────────────────────────────────────
# ORDER BUILDER UTILITY
# ─────────────────────────────────────────────────────────────────────────────

def build_entry_order(
    symbol:         str,
    quantity:       int,
    entry_price:    float,
    stop_price:     float,
    model_prob:     float,
    model_version:  str,
    decision_meta:  Dict = None
) -> BracketOrder:
    """
    Construct a standardised entry bracket order with all metadata attached.
    """
    # Target: 3×risk for 1:3 R/R minimum (optional, can use trailing instead)
    risk       = entry_price - stop_price
    target     = entry_price + (3 * risk) if risk > 0 else 0

    return BracketOrder(
        symbol          = symbol,
        side            = OrderSide.BUY,
        quantity        = quantity,
        entry_price     = round(entry_price, 2),
        stop_loss_price = round(stop_price, 2),
        target_price    = round(target, 2),
        trailing_trigger= TRAIL_START if True else 0,
        model_version   = model_version,
        decision_meta   = decision_meta or {
            "model_prob": model_prob,
            "placed_by":  "QPM_auto"
        }
    )


def build_exit_order(
    symbol:        str,
    quantity:      int,
    current_price: float,
    exit_reason:   str,
    model_version: str
) -> BracketOrder:
    """Construct an exit (sell) market order."""
    return BracketOrder(
        symbol          = symbol,
        side            = OrderSide.SELL,
        quantity        = quantity,
        entry_price     = 0,   # market order
        stop_loss_price = 0,
        target_price    = 0,
        trailing_trigger= 0,
        order_type      = OrderType.MARKET,
        model_version   = model_version,
        decision_meta   = {
            "exit_reason":     exit_reason,
            "current_price":   current_price,
            "placed_by":       "QPM_auto"
        }
    )

# Import for trailing trigger
from config.settings import TRAIL_START
