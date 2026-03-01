"""
Agent heartbeat — periodic liveness pulse for observability.

Each agent starts a heartbeat on run/start and stops on shutdown.
Operators and OpenClaw's watchtower can monitor these log entries
to detect silently-dead agents.
"""
from __future__ import annotations

import asyncio
import time

from audit.logger import get_logger


class AgentHeartbeat:
    """
    Background task that logs a structured heartbeat at regular intervals.

    Usage:
        heartbeat = AgentHeartbeat("listener", interval_seconds=30)
        heartbeat.start()
        try:
            await agent_main_loop()
        finally:
            heartbeat.stop()
    """

    def __init__(
        self,
        agent_name: str,
        interval_seconds: float = 30.0,
    ) -> None:
        self._agent_name = agent_name
        self._interval = interval_seconds
        self._log = get_logger("heartbeat")
        self._task: asyncio.Task[None] | None = None

    def start(self) -> None:
        """Start the background heartbeat task."""
        if self._task is not None:
            return
        self._task = asyncio.create_task(self._run())

    def stop(self) -> None:
        """Cancel the heartbeat task."""
        if self._task is not None:
            self._task.cancel()
            self._task = None

    async def _run(self) -> None:
        """Log heartbeat every interval until cancelled."""
        while True:
            await asyncio.sleep(self._interval)
            self._log.info(
                "agent_heartbeat",
                agent=self._agent_name,
                ts=time.time(),
            )
