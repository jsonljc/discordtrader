from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from enum import StrEnum
from uuid import UUID, uuid4

from pydantic import BaseModel, Field


class Direction(StrEnum):
    LONG = "LONG"
    SHORT = "SHORT"


class AssetClass(StrEnum):
    EQUITY = "EQUITY"
    OPTION = "OPTION"


class OptionType(StrEnum):
    CALL = "CALL"
    PUT = "PUT"


class ConfidenceBucket(StrEnum):
    HIGH = "HIGH"       # strong signal; full execution with tier-appropriate sizing
    MEDIUM = "MEDIUM"   # partial signal; execution with reduced sizing
    LOW = "LOW"         # sparse signal; minimal execution, shares only


class TradeIntent(BaseModel):
    """
    Structured trade intent derived from a SignalEvent.
    Produced by: Interpreter Agent via regex templates (fast path) or
                 LLM parser (narrative path).
    Consumed by: Risk Officer Agent.

    For regex-parsed intents, option_type / strike / expiry are always None.
    For LLM-parsed intents, llm_clarity_score and llm_summary are populated.
    use_smart_options_selector is set by the Risk Officer in Batch 7B.
    """

    event_id: UUID = Field(default_factory=uuid4)
    correlation_id: UUID             # copied from source SignalEvent
    event_hash: str = Field(default="")
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    source_signal_id: UUID           # links back to SignalEvent.event_id

    ticker: str                      # normalized uppercase, e.g. "AAPL"
    asset_class: AssetClass
    direction: Direction

    entry_price: Decimal | None = None       # None → market order
    stop_price: Decimal | None = None
    take_profit_price: Decimal | None = None
    quantity_hint: int | None = None         # optional override from raw signal

    confidence: ConfidenceBucket
    template_name: str               # "regex_<name>" or "llm_parsed"

    # ── Options-specific fields (None for pure equity trades) ──────────────
    option_type: OptionType | None = None    # CALL or PUT
    strike: Decimal | None = None            # option strike price
    expiry: date | None = None               # option expiry date

    # ── Smart selector flag (set by Risk Officer in Batch 7B) ──────────────
    use_smart_options_selector: bool = False

    # ── Critical-path gate ─────────────────────────────────────────────────
    # True when this intent was produced by the LLM parser (template_name ==
    # "llm_parsed").  The Risk Officer must force NEEDS_APPROVAL for any intent
    # with this flag set — LLM-parsed signals NEVER auto-execute.
    requires_manual_approval: bool = False

    # ── LLM parse metadata (None when produced by regex path) ──────────────
    llm_clarity_score: int | None = None     # 0-100 self-reported by LLM
    llm_summary: str | None = None           # one-sentence trade summary

    profile: str = "discord_equities"
