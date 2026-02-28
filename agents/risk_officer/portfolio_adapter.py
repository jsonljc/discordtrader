"""
Portfolio adapter — fetches a PortfolioSnapshot from IBKR via ib_insync.

Uses a dedicated client_id (ibkr_client_id + 10) to avoid colliding with
the executor's primary connection.

For Batch 4 tests, mock this class entirely:
    portfolio = PortfolioSnapshot(correlation_id=..., ...)
    decision  = evaluate_trade(intent, portfolio, ...)
"""
from __future__ import annotations

import contextlib
from decimal import Decimal
from uuid import UUID

import ib_insync as ib

from audit.hasher import stamp
from audit.logger import get_logger
from config.settings import Settings
from schemas.portfolio_snapshot import PortfolioSnapshot, PositionSnapshot

# Account value tags we care about
_ACCT_TAGS: frozenset[str] = frozenset({
    "NetLiquidation",
    "AvailableFunds",
    "UnrealizedPnL",
    "RealizedPnL",
})

# Client ID offset: risk officer uses a separate connection from the executor
_CLIENT_ID_OFFSET: int = 10


class PortfolioAdapter:
    """
    Fetches current IBKR account state and returns a stamped PortfolioSnapshot.

    Maintains a persistent async connection; reconnects automatically on failure.
    Call close() on shutdown to release the TWS connection slot.
    """

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._ib: ib.IB | None = None
        self._log = get_logger("portfolio_adapter")

    async def _ensure_connected(self) -> ib.IB:
        if self._ib is None or not self._ib.isConnected():
            self._ib = ib.IB()  # type: ignore[no-untyped-call]
            client_id = self._settings.ibkr_client_id + _CLIENT_ID_OFFSET
            await self._ib.connectAsync(
                host=self._settings.ibkr_host,
                port=self._settings.ibkr_port,
                clientId=client_id,
                timeout=10,
            )
            self._log.info(
                "portfolio_adapter_connected",
                host=self._settings.ibkr_host,
                port=self._settings.ibkr_port,
                client_id=client_id,
                paper=self._settings.paper_mode,
            )
        return self._ib

    async def close(self) -> None:
        if self._ib and self._ib.isConnected():
            self._ib.disconnect()  # type: ignore[no-untyped-call]
        self._ib = None

    async def get_snapshot(self, correlation_id: UUID) -> PortfolioSnapshot:
        """
        Connect (or reuse connection), pull account data + positions,
        and return a stamped PortfolioSnapshot.
        """
        ib_client = await self._ensure_connected()
        account_id = self._settings.ibkr_account_id

        # ── Parse account values ──────────────────────────────────────────────
        tag_map: dict[str, Decimal] = {}
        for av in ib_client.accountValues(account_id):
            if av.tag in _ACCT_TAGS and av.currency == "USD":
                with contextlib.suppress(Exception):
                    tag_map[av.tag] = Decimal(av.value)

        net_liq = tag_map.get("NetLiquidation", Decimal("0"))
        cash_available = tag_map.get("AvailableFunds", Decimal("0"))
        unrealized_pnl = tag_map.get("UnrealizedPnL", Decimal("0"))
        realized_pnl = tag_map.get("RealizedPnL", Decimal("0"))

        daily_pnl = unrealized_pnl + realized_pnl
        daily_pnl_pct = (daily_pnl / net_liq) if net_liq > 0 else Decimal("0")

        # ── Parse positions ───────────────────────────────────────────────────
        position_snapshots: list[PositionSnapshot] = []
        for item in ib_client.portfolio(account_id):
            if item.position == 0:
                continue
            try:
                position_snapshots.append(
                    PositionSnapshot(
                        ticker=item.contract.symbol,
                        quantity=Decimal(str(item.position)),
                        market_value=Decimal(str(item.marketValue)),
                        avg_cost=Decimal(str(item.averageCost)),
                        unrealized_pnl=Decimal(str(item.unrealizedPNL)),
                    )
                )
            except Exception:  # noqa: BLE001
                self._log.warning(
                    "portfolio_position_parse_error",
                    symbol=getattr(getattr(item, "contract", None), "symbol", "?"),
                )

        snapshot = PortfolioSnapshot(
            correlation_id=correlation_id,
            account_id=account_id,
            net_liquidation=net_liq,
            sleeve_value=self._settings.sleeve_value,
            cash_available=cash_available,
            positions=position_snapshots,
            open_position_count=len(position_snapshots),
            daily_pnl=daily_pnl,
            daily_pnl_pct=daily_pnl_pct,
        )
        return stamp(snapshot)  # type: ignore[return-value]
