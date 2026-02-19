"""
=============================================================================
MOCK BROKER — Deterministic Broker for Unit Tests
=============================================================================
Provides controllable order acceptance/rejection and fill behavior.
"""

from typing import Optional, List, Dict
from dataclasses import dataclass
import sys
from pathlib import Path

# Handle imports for both package and flat structure
try:
    from execution.broker import BracketOrder, BrokerOrderResponse, OrderSide
except ImportError:
    # Fall back to flat structure
    sys.path.insert(0, str(Path(__file__).parent.parent))
    from broker import BracketOrder, BrokerOrderResponse, OrderSide


class MockBroker:
    """
    Mock broker adapter for unit tests.
    
    Allows tests to configure:
    - Whether orders are accepted or rejected
    - Fill quantities (full, partial, or zero)
    - Fill prices
    
    Example:
        broker = MockBroker(accept_orders=True, fill_ratio=1.0)  # Full fills
        broker = MockBroker(accept_orders=True, fill_ratio=0.5)  # Half fills
        broker = MockBroker(accept_orders=False, rejection_reason="quota")  # Reject all
    """
    
    def __init__(
        self,
        accept_orders: bool = True,
        fill_ratio: float = 1.0,           # 0.0 = no fill, 0.5 = half, 1.0 = full
        price_slippage_pct: float = 0.001, # 0.1% default slippage
        rejection_reason: str = "test_rejection"
    ):
        self.accept_orders = accept_orders
        self.fill_ratio = fill_ratio
        self.price_slippage_pct = price_slippage_pct
        self.rejection_reason = rejection_reason
        
        self._orders: List[BracketOrder] = []
        self._positions: Dict[str, Dict] = {}
    
    def place_order(self, order: BracketOrder) -> BrokerOrderResponse:
        """
        Mock order placement with configurable behavior.
        """
        self._orders.append(order)
        
        # Rejection scenario
        if not self.accept_orders:
            return BrokerOrderResponse(
                accepted=False,
                client_order_id=order.client_order_id,
                reason=self.rejection_reason,
                filled_qty=0,
                avg_fill_price=0.0,
                status="REJECTED"
            )
        
        # Acceptance with configurable fill
        filled_qty = int(order.quantity * self.fill_ratio)
        
        # Apply slippage
        if order.side == OrderSide.BUY:
            fill_price = order.entry_price * (1 + self.price_slippage_pct)
        else:
            fill_price = order.entry_price * (1 - self.price_slippage_pct)
        
        # Update mock positions
        self._update_position(order.symbol, order.side, filled_qty, fill_price)
        
        return BrokerOrderResponse(
            accepted=True,
            client_order_id=order.client_order_id,
            broker_order_id=f"MOCK_{order.client_order_id}",
            filled_qty=filled_qty,
            avg_fill_price=round(fill_price, 2),
            status="FILLED" if filled_qty == order.quantity else "PARTIAL",
            raw={"mode": "mock", "fill_ratio": self.fill_ratio}
        )
    
    def _update_position(self, symbol: str, side: OrderSide, qty: int, price: float):
        """Update internal position tracking for reconciliation tests."""
        if side == OrderSide.BUY:
            existing = self._positions.get(symbol, {"quantity": 0, "avg_price": 0.0})
            total_qty = existing["quantity"] + qty
            if total_qty > 0:
                new_avg = (existing["quantity"] * existing["avg_price"] + qty * price) / total_qty
                self._positions[symbol] = {"quantity": total_qty, "avg_price": new_avg}
        
        elif side == OrderSide.SELL:
            existing = self._positions.get(symbol)
            if existing:
                existing["quantity"] -= qty
                if existing["quantity"] <= 0:
                    del self._positions[symbol]
    
    def get_open_positions(self) -> List[Dict]:
        """Return mock broker-side positions for reconciliation."""
        return [
            {"symbol": sym, "quantity": pos["quantity"], "avg_price": pos["avg_price"]}
            for sym, pos in self._positions.items()
        ]
    
    def get_open_orders(self) -> List[Dict]:
        """Return unfilled orders (for reconciliation)."""
        return []  # Mock: assume all orders instantly filled or rejected
    
    def set_accept_orders(self, accept: bool):
        """Change acceptance behavior mid-test."""
        self.accept_orders = accept
    
    def set_fill_ratio(self, ratio: float):
        """Change fill ratio mid-test."""
        self.fill_ratio = max(0.0, min(1.0, ratio))
    
    def reset(self):
        """Clear all orders and positions."""
        self._orders.clear()
        self._positions.clear()


class MockBrokerBuilder:
    """
    Fluent builder for MockBroker configuration.
    
    Example:
        broker = (MockBrokerBuilder()
                  .accepts_orders()
                  .fills_fully()
                  .with_slippage(0.002)
                  .build())
    """
    
    def __init__(self):
        self._accept = True
        self._fill_ratio = 1.0
        self._slippage = 0.001
        self._rejection_reason = "test_rejection"
    
    def accepts_orders(self) -> "MockBrokerBuilder":
        self._accept = True
        return self
    
    def rejects_orders(self, reason: str = "test_rejection") -> "MockBrokerBuilder":
        self._accept = False
        self._rejection_reason = reason
        return self
    
    def fills_fully(self) -> "MockBrokerBuilder":
        self._fill_ratio = 1.0
        return self
    
    def fills_partially(self, ratio: float = 0.5) -> "MockBrokerBuilder":
        self._fill_ratio = ratio
        return self
    
    def no_fills(self) -> "MockBrokerBuilder":
        self._fill_ratio = 0.0
        return self
    
    def with_slippage(self, slippage_pct: float) -> "MockBrokerBuilder":
        self._slippage = slippage_pct
        return self
    
    def build(self) -> MockBroker:
        return MockBroker(
            accept_orders=self._accept,
            fill_ratio=self._fill_ratio,
            price_slippage_pct=self._slippage,
            rejection_reason=self._rejection_reason
        )
