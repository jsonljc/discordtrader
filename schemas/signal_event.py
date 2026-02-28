from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

from pydantic import BaseModel, Field


class SignalEvent(BaseModel):
    """
    Raw signal ingested from Discord.
    Produced by: Discord Listener Agent.
    Consumed by: Interpreter Agent.

    event_id        — idempotency key; deduplication uses source_message_id
                      but event_id is the canonical pipeline key.
    correlation_id  — set once at ingestion; propagated unchanged through
                      every downstream event (TradeIntent → RiskDecision →
                      ExecutionReceipt).  Every log line includes it.
    event_hash      — Blake2b-32 hex digest of the event body (excluding
                      this field).  Populated by audit.hasher.stamp().
    """

    event_id: UUID = Field(default_factory=uuid4)
    correlation_id: UUID = Field(default_factory=uuid4)
    event_hash: str = Field(default="")
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    # Discord provenance
    source_guild_id: str
    source_channel_id: str
    source_message_id: str       # Discord snowflake; primary dedupe key
    source_author_id: str
    source_author_roles: list[str] = Field(default_factory=list)

    # Raw payload — never modified after capture
    raw_text: str

    # OpenClaw profile that ingested this signal
    profile: str = "discord_equities"
