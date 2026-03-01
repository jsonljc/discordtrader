"""
Unit tests for discord_listener.parser and DiscordListenerAgent._handle_message.

Discord Message objects are mocked with MagicMock — no live bot required.
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from agents.discord_listener.agent import DiscordListenerAgent
from agents.discord_listener.parser import parse_message
from audit.hasher import verify
from bus.queue import PipelineBus
from config.settings import Settings
from schemas.signal_event import SignalEvent

# ── Mock helpers ──────────────────────────────────────────────────────────────

def _make_role(role_id: int) -> MagicMock:
    role = MagicMock()
    role.id = role_id
    return role


def _make_message(
    content: str = "BUY AAPL @ 175.50 stop 172.00 target 181.00",
    guild_id: int = 123456789,
    channel_id: int = 111222333,
    message_id: int = 999888777,
    author_id: int = 444555666,
    author_role_ids: list[int] | None = None,
    is_bot: bool = False,
    has_guild: bool = True,
) -> MagicMock:
    """Build a mock discord.Message with the given attributes."""
    if author_role_ids is None:
        author_role_ids = [777888999]

    msg = MagicMock()
    msg.id = message_id
    msg.content = content
    msg.author.id = author_id
    msg.author.bot = is_bot
    msg.author.roles = [_make_role(rid) for rid in author_role_ids]

    if has_guild:
        guild = MagicMock()
        guild.id = guild_id
        msg.guild = guild
    else:
        msg.guild = None

    channel = MagicMock()
    channel.id = channel_id
    msg.channel = channel

    return msg


def _open_settings(**kwargs: object) -> Settings:
    """Settings with all whitelists empty (open — accepts everything)."""
    defaults: dict[str, object] = {
        "discord_bot_token": "fake_token",
        "discord_allowed_guild_ids": [],
        "discord_allowed_channel_ids": [],
        "discord_allowed_role_ids": [],
    }
    defaults.update(kwargs)
    return Settings.model_validate(defaults)


# ── parse_message ─────────────────────────────────────────────────────────────

class TestParseMessage:
    def test_returns_signal_event(self) -> None:
        event = parse_message(_make_message())
        assert isinstance(event, SignalEvent)

    def test_guild_id_extracted(self) -> None:
        event = parse_message(_make_message(guild_id=123456789))
        assert event.source_guild_id == "123456789"

    def test_channel_id_extracted(self) -> None:
        event = parse_message(_make_message(channel_id=111222333))
        assert event.source_channel_id == "111222333"

    def test_message_id_extracted(self) -> None:
        event = parse_message(_make_message(message_id=999888777))
        assert event.source_message_id == "999888777"

    def test_author_id_extracted(self) -> None:
        event = parse_message(_make_message(author_id=444555666))
        assert event.source_author_id == "444555666"

    def test_author_roles_extracted(self) -> None:
        event = parse_message(_make_message(author_role_ids=[111, 222, 333]))
        assert set(event.source_author_roles) == {"111", "222", "333"}

    def test_raw_text_preserved(self) -> None:
        text = "BUY AAPL @ 175.50 stop 172.00 target 181.00"
        event = parse_message(_make_message(content=text))
        assert event.raw_text == text

    def test_profile_set(self) -> None:
        event = parse_message(_make_message(), profile="paper")
        assert event.profile == "paper"

    def test_event_hash_populated(self) -> None:
        """Parser calls stamp() — event_hash must be non-empty and valid."""
        event = parse_message(_make_message())
        assert event.event_hash != ""
        assert verify(event) is True

    def test_correlation_id_is_uuid4(self) -> None:
        from uuid import UUID
        event = parse_message(_make_message())
        assert isinstance(event.correlation_id, UUID)
        assert event.correlation_id.version == 4

    def test_no_guild_dm_uses_empty_string(self) -> None:
        """Direct messages have no guild — guild_id should be ''."""
        event = parse_message(_make_message(has_guild=False))
        assert event.source_guild_id == ""

    def test_no_roles_uses_empty_list(self) -> None:
        """Users without roles (e.g. in DMs) get an empty roles list."""
        msg = _make_message()
        del msg.author.roles  # simulate User object without .roles
        event = parse_message(msg)
        assert event.source_author_roles == []

    def test_each_parse_gets_unique_event_id(self) -> None:
        """Every call to parse_message must produce a fresh event_id."""
        msg = _make_message()
        e1 = parse_message(msg)
        e2 = parse_message(msg)
        assert e1.event_id != e2.event_id

    def test_each_parse_gets_unique_correlation_id(self) -> None:
        msg = _make_message()
        e1 = parse_message(msg)
        e2 = parse_message(msg)
        assert e1.correlation_id != e2.correlation_id


# ── DiscordListenerAgent._handle_message ─────────────────────────────────────

class TestHandleMessage:
    def _make_agent(self, **setting_overrides: object) -> tuple[DiscordListenerAgent, PipelineBus]:
        settings = _open_settings(**setting_overrides)
        bus = PipelineBus()
        agent = DiscordListenerAgent(settings, bus)
        return agent, bus

    @pytest.mark.asyncio
    async def test_valid_message_enqueued(self) -> None:
        agent, bus = self._make_agent()
        await agent._handle_message(_make_message())
        assert not bus.signals.empty()

    @pytest.mark.asyncio
    async def test_enqueued_event_has_correct_message_id(self) -> None:
        agent, bus = self._make_agent()
        await agent._handle_message(_make_message(message_id=112233445566))
        event = await bus.signals.get()
        assert event.source_message_id == "112233445566"

    @pytest.mark.asyncio
    async def test_bot_message_not_enqueued(self) -> None:
        agent, bus = self._make_agent()
        await agent._handle_message(_make_message(is_bot=True))
        assert bus.signals.empty()

    @pytest.mark.asyncio
    async def test_filtered_message_not_enqueued(self) -> None:
        agent, bus = self._make_agent(discord_allowed_guild_ids=["999"])
        await agent._handle_message(_make_message(guild_id=111))
        assert bus.signals.empty()

    @pytest.mark.asyncio
    async def test_duplicate_message_not_enqueued_twice(self) -> None:
        agent, bus = self._make_agent()
        msg = _make_message(message_id=777777)
        await agent._handle_message(msg)
        await agent._handle_message(msg)    # duplicate
        assert bus.signals.qsize() == 1

    @pytest.mark.asyncio
    async def test_event_is_stamped(self) -> None:
        agent, bus = self._make_agent()
        await agent._handle_message(_make_message())
        event = await bus.signals.get()
        assert event.event_hash != ""
        assert verify(event)

    @pytest.mark.asyncio
    async def test_multiple_messages_all_enqueued(self) -> None:
        agent, bus = self._make_agent()
        for msg_id in [100, 200, 300]:
            await agent._handle_message(_make_message(message_id=msg_id))
        assert bus.signals.qsize() == 3

    @pytest.mark.asyncio
    async def test_empty_content_not_enqueued(self) -> None:
        """Embed-only / sticker messages with no text content are dropped."""
        agent, bus = self._make_agent()
        await agent._handle_message(_make_message(content=""))
        assert bus.signals.empty()

    @pytest.mark.asyncio
    async def test_whitespace_only_content_not_enqueued(self) -> None:
        """Whitespace-only message content is treated as empty and dropped."""
        agent, bus = self._make_agent()
        await agent._handle_message(_make_message(content="   \n\t  "))
        assert bus.signals.empty()
