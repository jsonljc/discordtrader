"""
Order builder — pure conversion from RiskDecision → order parameters.

Two dataclasses cover the two order types:
    BracketParams   — entry + stop-loss + take-profit bracket (equity shares)
    OptionOrderParams — simple limit buy/sell (options contracts)

Options orders do not use a stop-loss bracket because:
    - Options risk is inherently capped at the premium paid.
    - Stop-loss orders on options are unreliable due to wide bid/ask spreads.
    - IBKR options stops can trigger on the bid, causing premature exits.

Neither class contains I/O; both are fully testable without IBKR connections.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from schemas.risk_decision import RiskDecision
from schemas.trade_intent import Direction


@dataclass(frozen=True)
class BracketParams:
    """
    Immutable parameters for a bracket order (equity shares).
    Passed directly to `ib.bracketOrder(action, quantity, limitPrice, ...)`.
    """

    action: str           # "BUY" (long) or "SELL" (short)
    quantity: int
    entry_price: float | None       # None → market order for parent
    stop_price: float | None
    take_profit_price: float | None
    order_ref: str        # truncated correlation_id for IBKR orderRef (≤ 20 chars)


@dataclass(frozen=True)
class OptionOrderParams:
    """
    Immutable parameters for a simple options limit order.
    Passed to `ib.LimitOrder(action, quantity, limitPrice)`.
    """

    action: str           # "BUY" (calls / long puts) or "SELL" (to close)
    quantity: int         # number of contracts (1 contract = 100 shares)
    limit_price: float    # premium per share (IBKR multiplies by 100 internally)
    order_ref: str


def _order_ref(decision: RiskDecision) -> str:
    """IBKR orderRef max 20 chars; use first 20 chars of correlation_id UUID."""
    return str(decision.correlation_id).replace("-", "")[:20]


def build_bracket_params(decision: RiskDecision) -> BracketParams:
    """
    Convert an APPROVED equity RiskDecision into BracketParams.

    Pure function — no side effects, no I/O.

    Raises:
        ValueError: if direction or quantity are missing.
    """
    if decision.approved_direction is None or decision.approved_quantity is None:
        raise ValueError(
            f"Cannot build bracket params from decision {decision.event_id}: "
            "approved_direction and approved_quantity must be set"
        )

    action = "BUY" if decision.approved_direction == Direction.LONG else "SELL"

    entry = (
        float(decision.approved_entry_price)
        if decision.approved_entry_price is not None
        else None
    )
    stop = (
        float(decision.approved_stop_price)
        if decision.approved_stop_price is not None
        else None
    )
    tp = (
        float(decision.approved_take_profit)
        if decision.approved_take_profit is not None
        else None
    )

    return BracketParams(
        action=action,
        quantity=decision.approved_quantity,
        entry_price=entry,
        stop_price=stop,
        take_profit_price=tp,
        order_ref=_order_ref(decision),
    )


def build_option_order_params(
    decision: RiskDecision,
    limit_price: Decimal,
    quantity: int,
) -> OptionOrderParams:
    """
    Build a simple limit order for an options contract.

    Args:
        decision:    Approved RiskDecision (used for direction and audit ref).
        limit_price: Premium per share to use as the limit price.
        quantity:    Number of contracts (resolved by executor from budget).

    Raises:
        ValueError: if direction is missing.
    """
    if decision.approved_direction is None:
        raise ValueError(
            f"Cannot build option order from decision {decision.event_id}: "
            "approved_direction must be set"
        )

    # Always BUY for long calls; SELL for short puts-to-close or short signals
    action = "BUY" if decision.approved_direction == Direction.LONG else "SELL"

    return OptionOrderParams(
        action=action,
        quantity=quantity,
        limit_price=float(limit_price),
        order_ref=_order_ref(decision),
    )
