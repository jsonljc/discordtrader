from __future__ import annotations

from typing import Any

import discord

from audit.logger import bind_correlation_id, get_logger
from bus.queue import PipelineBus
from config.settings import Settings
from schemas.signal_event import SignalEvent

from .deduper import Deduper
from .filter import MessageFilter
from .parser import parse_message


class _DiscordClient(discord.Client):
    """
    Internal discord.Client subclass.  Routes on_message to the parent agent
    so all handling logic stays in DiscordListenerAgent (easier to test).
    """

    def __init__(self, agent: DiscordListenerAgent, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._agent = agent

    async def on_ready(self) -> None:
        self._agent._log.info(
            "discord_listener_ready",
            user=str(self.user),
            guilds=len(self.guilds),
        )

    async def on_message(self, message: discord.Message) -> None:
        await self._agent._handle_message(message)

    async def on_disconnect(self) -> None:
        self._agent._log.warning("discord_disconnected")

    async def on_resumed(self) -> None:
        self._agent._log.info("discord_session_resumed")


class DiscordListenerAgent:
    """
    Agent 1 — Discord Listener.

    Lifecycle:
        agent = DiscordListenerAgent(settings, bus)
        await agent.start()   # blocks; reconnects automatically

    Responsibilities:
        - Connect to Discord using an official bot token (no self-bot / scraping)
        - Filter messages by guild / channel / role whitelist
        - Deduplicate messages by Discord snowflake ID (1-hour TTL window)
        - Parse each passing message into a stamped SignalEvent
        - Enqueue the SignalEvent onto bus.signals for the Interpreter
    """

    def __init__(self, settings: Settings, bus: PipelineBus) -> None:
        self._settings = settings
        self._bus = bus
        self._filter = MessageFilter(settings)
        self._deduper = Deduper()
        self._log = get_logger("discord_listener")

        intents = discord.Intents.default()
        intents.message_content = True  # privileged intent — enable in Dev Portal
        intents.guilds = True

        self._client = _DiscordClient(agent=self, intents=intents)

    async def _handle_message(self, message: discord.Message) -> None:
        """Core per-message handler — tested directly without a live bot."""
        if message.author.bot:
            return

        guild_id = str(message.guild.id) if message.guild is not None else ""
        channel_id = str(message.channel.id)
        author_role_ids: list[str] = [
            str(r.id) for r in getattr(message.author, "roles", [])
        ]

        if not self._filter.is_allowed(guild_id, channel_id, author_role_ids):
            self._log.debug(
                "message_filtered",
                guild_id=guild_id,
                channel_id=channel_id,
            )
            return

        msg_id = str(message.id)
        if self._deduper.check_and_mark(msg_id):
            self._log.warning("duplicate_message_skipped", message_id=msg_id)
            return

        event: SignalEvent = parse_message(message, self._settings.profile)

        # Bind correlation ID to all log calls that follow in this async context
        bind_correlation_id(event.correlation_id)

        self._log.info(
            "signal_enqueued",
            message_id=msg_id,
            guild_id=guild_id,
            channel_id=channel_id,
            profile=self._settings.profile,
            preview=event.raw_text[:80],
        )

        await self._bus.signals.put(event)

    async def start(self) -> None:
        """
        Connect to Discord and run the event loop indefinitely.
        discord.py handles reconnects automatically.
        Raises ValueError if discord_bot_token is not configured.
        """
        token = self._settings.discord_bot_token
        if not token:
            raise ValueError(
                "discord_bot_token is not set. "
                "Add DISCORD_BOT_TOKEN to your .env file."
            )
        await self._client.start(token)

    async def close(self) -> None:
        """Gracefully close the Discord connection."""
        await self._client.close()

    @property
    def client(self) -> _DiscordClient:
        return self._client
