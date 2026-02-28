from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum
from uuid import UUID, uuid4

from pydantic import BaseModel, Field


class OrderStatus(StrEnum):
    SUBMITTED = "SUBMITTED"
    FILLED = "FILLED"
    PARTIAL = "PARTIAL"
    CANCELLED = "CANCELLED"
    ERROR = "ERROR"


class ExecutionReceipt(BaseModel):
    """
    Confirmation record produced after IBKR order placement and tracking.
    Produced by: IBKR Executor Agent.
    Consumed by: Audit trail / Governor.

    is_paper MUST be true for any order placed via a paper account.
    This field is set from config, not derived from order metadata.
    """

    event_id: UUID = Field(default_factory=uuid4)
    correlation_id: UUID             # copied from source RiskDecision
    event_hash: str = Field(default="")
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    source_decision_id: UUID         # links back to RiskDecision.event_id

    # IBKR order identifiers (None until order is placed)
    ibkr_order_id: int | None = None
    ibkr_perm_id: int | None = None  # IBKR permanent order ID (survives reconnect)

    status: OrderStatus
    filled_quantity: Decimal = Decimal("0")
    avg_fill_price: Decimal | None = None
    commission: Decimal | None = None

    # Bracket legs (None if not used)
    stop_order_id: int | None = None
    take_profit_order_id: int | None = None

    error_message: str | None = None

    is_paper: bool = True    # CRITICAL: must be True for paper accounts
    profile: str = "discord_equities"
