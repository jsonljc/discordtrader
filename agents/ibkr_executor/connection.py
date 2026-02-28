"""
IBKRConnection — manages the persistent ib_insync IB client.

Responsibilities:
    - Connect to TWS/Gateway with retry + exponential backoff
    - Subscribe to disconnectedEvent and auto-reconnect
    - Expose `.ib` for other executor components to use
    - Support clean shutdown via `.disconnect()`

All ib_insync calls require `# type: ignore[no-untyped-call]` because
the library ships no type stubs.
"""
from __future__ import annotations

import asyncio
from typing import Any, Protocol, runtime_checkable

import ib_insync as ib

from audit.logger import get_logger
from config.settings import Settings

_MAX_RETRIES = 3
_BASE_DELAY_S = 5.0


@runtime_checkable
class IBKRConnectionProtocol(Protocol):
    """
    Structural protocol for an IBKR connection.

    Both IBKRConnection and test fakes satisfy this without inheritance,
    enabling clean dependency injection in IBKRExecutorAgent.
    """

    async def connect(self) -> None: ...
    def disconnect(self) -> None: ...

    @property
    def ib(self) -> Any: ...

    @property
    def is_connected(self) -> bool: ...


class IBKRConnection:
    """
    Wraps an `ib_insync.IB` instance with connection lifecycle management.

    Usage:
        conn = IBKRConnection(settings)
        await conn.connect()           # raises on permanent failure
        ib_client = conn.ib            # ready-to-use IB object
        conn.disconnect()              # clean shutdown
    """

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._ib: ib.IB = ib.IB()  # type: ignore[no-untyped-call]
        self._log = get_logger("ibkr_connection")
        # Register reconnect handler — fires synchronously from ib_insync's event system
        self._ib.disconnectedEvent += self._on_disconnect

    # ── public ──────────────────────────────────────────────────────────────

    async def connect(self) -> None:
        """Connect to IBKR.  Raises `ConnectionError` after all retries exhausted."""
        await self._connect_with_retry()

    def disconnect(self) -> None:
        """Cleanly disconnect.  Safe to call even when already disconnected."""
        if self._ib.isConnected():
            self._ib.disconnect()  # type: ignore[no-untyped-call]
            self._log.info("ibkr_disconnected_by_request")

    @property
    def ib(self) -> ib.IB:
        """The underlying ib_insync IB instance."""
        return self._ib

    @property
    def is_connected(self) -> bool:
        return bool(self._ib.isConnected())

    # ── internal ────────────────────────────────────────────────────────────

    async def _connect_with_retry(self) -> None:
        for attempt in range(1, _MAX_RETRIES + 1):
            try:
                await self._ib.connectAsync(
                    host=self._settings.ibkr_host,
                    port=self._settings.ibkr_port,
                    clientId=self._settings.ibkr_client_id,
                    timeout=20,
                )
                self._log.info(
                    "ibkr_connected",
                    host=self._settings.ibkr_host,
                    port=self._settings.ibkr_port,
                    client_id=self._settings.ibkr_client_id,
                    paper_mode=self._settings.paper_mode,
                )
                return
            except Exception as exc:  # noqa: BLE001
                self._log.warning(
                    "ibkr_connect_attempt_failed",
                    attempt=attempt,
                    max_retries=_MAX_RETRIES,
                    error=str(exc),
                )
                if attempt == _MAX_RETRIES:
                    raise ConnectionError(
                        f"Failed to connect to IBKR after {_MAX_RETRIES} attempts: {exc}"
                    ) from exc
                await asyncio.sleep(_BASE_DELAY_S * attempt)

    def _on_disconnect(self) -> None:
        """Sync callback registered with ib_insync.  Schedules async reconnect."""
        self._log.warning("ibkr_disconnected_scheduling_reconnect")
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(self._reconnect())
        except RuntimeError:
            pass  # no event loop running; shutdown in progress

    async def _reconnect(self) -> None:
        await asyncio.sleep(_BASE_DELAY_S)
        try:
            await self._connect_with_retry()
        except Exception as exc:  # noqa: BLE001
            self._log.error("ibkr_reconnect_failed_giving_up", error=str(exc))
