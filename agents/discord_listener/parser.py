from __future__ import annotations

from typing import TYPE_CHECKING, Any

from audit.hasher import stamp
from schemas.signal_event import SignalEvent

if TYPE_CHECKING:
    pass


def parse_message(message: Any, profile: str = "discord_equities") -> SignalEvent:
    """
    Convert a discord.Message into a stamped SignalEvent.

    The correlation_id is generated fresh here (uuid4 default on SignalEvent).
    It is the caller's responsibility to call audit.logger.bind_correlation_id()
    after receiving this event so all downstream log lines carry the same ID.

    Args:
        message: A discord.Message instance (typed as Any to avoid hard
                 dependency on discord stubs at import time).
        profile: OpenClaw strategy profile name.

    Returns:
        A SignalEvent with event_hash populated (Blake2b-stamped).
    """
    guild_id = str(message.guild.id) if message.guild is not None else ""
    channel_id = str(message.channel.id)
    author_id = str(message.author.id)

    # Member has .roles; User (DM) does not — graceful fallback
    raw_roles: list[Any] = getattr(message.author, "roles", [])
    author_role_ids = [str(role.id) for role in raw_roles]

    event = SignalEvent(
        source_guild_id=guild_id,
        source_channel_id=channel_id,
        source_message_id=str(message.id),
        source_author_id=author_id,
        source_author_roles=author_role_ids,
        raw_text=message.content,
        profile=profile,
    )

    # Stamp immediately so the event is tamper-evident from the moment of capture
    return stamp(event)  # type: ignore[return-value]
