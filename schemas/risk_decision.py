from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from enum import StrEnum
from uuid import UUID, uuid4

from pydantic import BaseModel, Field

from schemas.trade_intent import AssetClass, Direction, OptionType


class RiskOutcome(StrEnum):
    APPROVED = "APPROVED"
    NEEDS_APPROVAL = "NEEDS_APPROVAL"   # reserved; not produced by current rules
    REJECTED = "REJECTED"


class RiskDecision(BaseModel):
    """
    Output of the Risk Officer after evaluating a TradeIntent against the
    current portfolio and all configured constraints.
    Produced by: Risk Officer Agent.
    Consumed by: IBKR Executor Agent.

    APPROVED orders proceed to execution automatically.
    REJECTED orders are discarded with reasons logged.

    Instrument routing (set by risk rules, consumed by executor):
        use_smart_options_selector = True
            HIGH-confidence LONG signals without an explicit option contract.
            Executor calls SmartOptionsSelector to find the best call and uses
            approved_budget to size the position.

        approved_option_type / approved_strike / approved_expiry set
            Explicit option contract specified by the signal (LLM-parsed or
            otherwise).  Executor qualifies and trades this contract directly.

        Neither flag set → equity (shares) order.

    Budget vs quantity:
        approved_budget is set when the exact position size cannot be determined
        at risk evaluation time (smart selector, or market orders where the
        current price is unknown).  The executor resolves the final quantity.
    """

    event_id: UUID = Field(default_factory=uuid4)
    correlation_id: UUID             # copied from source TradeIntent
    event_hash: str = Field(default="")
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    source_intent_id: UUID           # links back to TradeIntent.event_id

    outcome: RiskOutcome
    rejection_reasons: list[str] = Field(default_factory=list)

    # ── Approved order parameters (None when REJECTED) ────────────────────────
    approved_ticker: str | None = None
    approved_direction: Direction | None = None
    approved_asset_class: AssetClass | None = None

    # Quantity (None when executor must size at execution time, e.g. smart selector)
    approved_quantity: int | None = None
    # Dollar budget (set when quantity cannot be pre-computed)
    approved_budget: Decimal | None = None

    approved_entry_price: Decimal | None = None
    approved_stop_price: Decimal | None = None
    approved_take_profit: Decimal | None = None

    # ── Option contract fields (None for equity orders) ───────────────────────
    approved_option_type: OptionType | None = None
    approved_strike: Decimal | None = None
    approved_expiry: date | None = None

    # ── Smart options selector flag ───────────────────────────────────────────
    use_smart_options_selector: bool = False

    # ── Sizing metadata (always populated for observability) ──────────────────
    position_size_pct: Decimal = Decimal("0")    # tier percentage allocated
    risk_reward_ratio: Decimal | None = None

    profile: str = "discord_equities"
