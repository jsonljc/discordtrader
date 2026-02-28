from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID, uuid4

from pydantic import BaseModel, Field


class PositionSnapshot(BaseModel):
    """A single open position within the portfolio."""

    ticker: str
    quantity: Decimal
    market_value: Decimal
    avg_cost: Decimal
    unrealized_pnl: Decimal


class PortfolioSnapshot(BaseModel):
    """
    Point-in-time view of the IBKR account used by the Risk Officer.
    Produced by: Risk Officer Agent (via IBKR portfolio adapter).
    Used internally to evaluate sizing and exposure constraints.
    """

    event_id: UUID = Field(default_factory=uuid4)
    correlation_id: UUID             # copied from in-flight TradeIntent
    event_hash: str = Field(default="")
    fetched_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    account_id: str
    net_liquidation: Decimal         # total account NAV
    sleeve_value: Decimal            # configured subset used for position sizing
    cash_available: Decimal

    positions: list[PositionSnapshot] = Field(default_factory=list)
    open_position_count: int = 0

    daily_pnl: Decimal = Decimal("0")
    daily_pnl_pct: Decimal = Decimal("0")    # for drawdown circuit-breaker check
