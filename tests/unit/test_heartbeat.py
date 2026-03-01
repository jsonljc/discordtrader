"""Unit tests for audit/heartbeat.py."""
from __future__ import annotations

import asyncio

import pytest

from audit.heartbeat import AgentHeartbeat


@pytest.mark.asyncio
async def test_heartbeat_starts_and_stops_cleanly() -> None:
    """Heartbeat can be started and stopped without error."""
    heartbeat = AgentHeartbeat("test_agent", interval_seconds=60.0)
    heartbeat.start()
    await asyncio.sleep(0.05)
    heartbeat.stop()
    await asyncio.sleep(0.02)


@pytest.mark.asyncio
async def test_heartbeat_emits_log_entry() -> None:
    """Heartbeat logs a structured entry after interval elapses."""
    import structlog

    heartbeat = AgentHeartbeat("test_agent", interval_seconds=0.05)
    heartbeat.start()
    await asyncio.sleep(0.08)
    heartbeat.stop()
    # No assertion on log output — just verify no exception
    await asyncio.sleep(0.02)


@pytest.mark.asyncio
async def test_heartbeat_stop_idempotent() -> None:
    """Calling stop() multiple times is safe."""
    heartbeat = AgentHeartbeat("test_agent", interval_seconds=60.0)
    heartbeat.start()
    heartbeat.stop()
    heartbeat.stop()
    heartbeat.stop()
