"""Unit tests for agents/ibkr_executor/order_tracker.py."""
from __future__ import annotations

from decimal import Decimal
from unittest.mock import MagicMock

import pytest

from agents.ibkr_executor.order_tracker import track_fill
from schemas.execution_receipt import OrderStatus


def _make_trade(
    status: str = "Submitted",
    filled: float = 0.0,
    avg_fill_price: float = 0.0,
    order_id: int = 1000,
) -> MagicMock:
    trade = MagicMock()
    trade.order = MagicMock()
    trade.order.orderId = order_id
    trade.order.permId = 98765
    trade.orderStatus = MagicMock()
    trade.orderStatus.status = status
    trade.orderStatus.filled = filled
    trade.orderStatus.avgFillPrice = avg_fill_price
    trade.fills = []
    return trade


@pytest.mark.asyncio
async def test_track_fill_returns_submitted_on_timeout() -> None:
    """When trade never fills, track_fill returns SUBMITTED after timeout."""
    trade = _make_trade(status="Submitted", filled=0.0)

    result = await track_fill(
        trade,
        timeout_seconds=0.05,
        poll_interval=0.01,
    )

    assert result.status == OrderStatus.SUBMITTED
    assert result.filled_quantity == Decimal("0")
    assert result.ibkr_order_id == 1000


@pytest.mark.asyncio
async def test_track_fill_returns_partial_on_timeout_with_fill() -> None:
    """When trade has partial fill at timeout, returns PARTIAL."""
    trade = _make_trade(status="Submitted", filled=5.0, avg_fill_price=175.50)

    result = await track_fill(
        trade,
        timeout_seconds=0.05,
        poll_interval=0.01,
    )

    assert result.status == OrderStatus.PARTIAL
    assert result.filled_quantity == Decimal("5")
    assert result.avg_fill_price == Decimal("175.5")


@pytest.mark.asyncio
async def test_track_fill_returns_filled_immediately() -> None:
    """When trade is already Filled, returns immediately with zero delay."""
    trade = _make_trade(status="Filled", filled=10.0, avg_fill_price=100.0)

    result = await track_fill(
        trade,
        timeout_seconds=30.0,
        poll_interval=0.25,
    )

    assert result.status == OrderStatus.FILLED
    assert result.filled_quantity == Decimal("10")
    assert result.avg_fill_price == Decimal("100")


@pytest.mark.asyncio
async def test_track_fill_returns_cancelled_when_cancelled() -> None:
    """When trade status is Cancelled, returns CANCELLED."""
    trade = _make_trade(status="Cancelled", filled=0.0)

    result = await track_fill(
        trade,
        timeout_seconds=30.0,
        poll_interval=0.0,
    )

    assert result.status == OrderStatus.CANCELLED
    assert result.filled_quantity == Decimal("0")
