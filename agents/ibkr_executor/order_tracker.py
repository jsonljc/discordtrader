"""
Order tracker — polls an ib_insync Trade object until filled, cancelled, or
timeout.

The ib_insync Trade.orderStatus.status field is updated in-place by the library
as TWS/Gateway pushes status messages into the event loop.  Polling is
intentionally simple (no eventkit callbacks) so the same code path works
against both real and mock trades in tests.

Status mapping
--------------
"Filled"                    → OrderStatus.FILLED
"Cancelled" / "ApiCancelled"
/ "Inactive"               → OrderStatus.CANCELLED
anything with filled > 0    → OrderStatus.PARTIAL  (early-exit partial)
timeout (no fill)           → OrderStatus.SUBMITTED (placed but unconfirmed)
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from schemas.execution_receipt import OrderStatus

# IB status strings that mean the order is definitively done
_FILLED_STATUSES = frozenset({"Filled"})
_TERMINAL_CANCEL = frozenset({"Cancelled", "ApiCancelled", "Inactive"})


@dataclass
class TrackResult:
    """Result returned by track_fill after polling a parent trade."""

    ibkr_order_id: int
    ibkr_perm_id: int | None
    status: OrderStatus
    filled_quantity: Decimal
    avg_fill_price: Decimal | None
    commission: Decimal | None


def _check_status(trade: Any) -> TrackResult | None:
    """
    Inspect current trade state.  Returns a TrackResult if the order has
    reached a terminal state, or None if still in progress.
    """
    status_str: str = str(trade.orderStatus.status)
    filled: float = float(trade.orderStatus.filled)
    avg_price: float = float(trade.orderStatus.avgFillPrice)
    order_id: int = int(trade.order.orderId)
    perm_id_raw = trade.order.permId
    perm_id: int | None = int(perm_id_raw) if perm_id_raw else None

    if status_str in _FILLED_STATUSES:
        commission: Decimal | None = None
        fills = getattr(trade, "fills", [])
        if fills:
            commission = Decimal(
                str(
                    sum(
                        f.commissionReport.commission
                        for f in fills
                        if getattr(f, "commissionReport", None)
                        and f.commissionReport.commission
                    )
                )
            )
        return TrackResult(
            ibkr_order_id=order_id,
            ibkr_perm_id=perm_id,
            status=OrderStatus.FILLED,
            filled_quantity=Decimal(str(filled)),
            avg_fill_price=Decimal(str(avg_price)) if avg_price else None,
            commission=commission,
        )

    if status_str in _TERMINAL_CANCEL:
        return TrackResult(
            ibkr_order_id=order_id,
            ibkr_perm_id=perm_id,
            status=OrderStatus.CANCELLED,
            filled_quantity=Decimal("0"),
            avg_fill_price=None,
            commission=None,
        )

    # Partial fill — exit early to record what we have
    if filled > 0:
        return TrackResult(
            ibkr_order_id=order_id,
            ibkr_perm_id=perm_id,
            status=OrderStatus.PARTIAL,
            filled_quantity=Decimal(str(filled)),
            avg_fill_price=Decimal(str(avg_price)) if avg_price else None,
            commission=None,
        )

    return None


async def track_fill(
    trade: Any,
    timeout_seconds: float = 30.0,
    poll_interval: float = 0.25,
) -> TrackResult:
    """
    Poll *trade* until it reaches a terminal state or *timeout_seconds* elapses.

    The check is performed before each sleep so an immediately-filled order
    (e.g. in paper mode or mocks) resolves on the first iteration with zero
    wall-clock delay.

    Args:
        trade:            ib_insync Trade object (typed as Any).
        timeout_seconds:  Maximum time to wait.  Defaults to 30 s.
        poll_interval:    Sleep between status checks.  Defaults to 0.25 s.
                          Tests may pass 0.0 for instant resolution.

    Returns:
        TrackResult with the final status and fill details.
    """
    loop = asyncio.get_event_loop()
    deadline = loop.time() + timeout_seconds

    while True:
        result = _check_status(trade)
        if result is not None:
            return result

        if loop.time() >= deadline:
            break

        await asyncio.sleep(poll_interval)

    # Timeout — report whatever state we have
    filled = float(trade.orderStatus.filled)
    avg = float(trade.orderStatus.avgFillPrice)
    return TrackResult(
        ibkr_order_id=int(trade.order.orderId),
        ibkr_perm_id=None,
        status=OrderStatus.PARTIAL if filled > 0 else OrderStatus.SUBMITTED,
        filled_quantity=Decimal(str(filled)) if filled > 0 else Decimal("0"),
        avg_fill_price=Decimal(str(avg)) if avg > 0 else None,
        commission=None,
    )
