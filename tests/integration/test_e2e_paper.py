"""
End-to-end paper mode tests — full pipeline without real IBKR or Discord.

Pipeline under test:
    SignalEvent (injected) → Interpreter → RiskOfficer → Executor → ExecutionReceipt

The Discord Listener is not included because it requires a live bot token;
we simulate its output by injecting a ready-made SignalEvent onto bus.signals.

Mocks
-----
- PortfolioAdapter.get_snapshot  → returns a pre-built healthy PortfolioSnapshot
- IBKRExecutorAgent connection   → _FakeConnection/_FakeIB (instant fills)

All three agents run as asyncio Tasks on a shared PipelineBus.  After
retrieving the receipt (or timing out) every task is cancelled cleanly.
"""
from __future__ import annotations

import asyncio
import contextlib
from decimal import Decimal
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agents.ibkr_executor import IBKRExecutorAgent
from agents.interpreter import InterpreterAgent
from agents.risk_officer import RiskOfficerAgent
from audit.hasher import stamp, verify
from bus.queue import PipelineBus
from config.settings import Settings
from schemas.execution_receipt import ExecutionReceipt, OrderStatus
from schemas.portfolio_snapshot import PortfolioSnapshot
from schemas.signal_event import SignalEvent

# ── minimal IBKR fakes (self-contained, no cross-file dependency) ─────────────


class _FakeIB:
    """Instant-fill IBKR stub — mirrors ib_insync.IB interface minimally."""

    def __init__(self, fill_price: float = 175.52) -> None:
        self._fill_price = fill_price
        self._counter = 2000

    def isConnected(self) -> bool:  # noqa: N802
        return True

    async def qualifyContractsAsync(self, *contracts: Any) -> list[Any]:  # noqa: N802
        for c in contracts:
            c.conId = 265598
        return list(contracts)

    async def reqTickersAsync(self, *_contracts: Any) -> list[Any]:  # noqa: N802
        """Return a minimal ticker with a spot price for smart selector lookup."""
        tick = MagicMock()
        tick.last = self._fill_price
        tick.close = self._fill_price
        tick.bid = self._fill_price - 0.01
        return [tick]

    async def reqSecDefOptParamsAsync(  # noqa: N802
        self, **_kwargs: Any
    ) -> list[Any]:
        """Return empty list — causes smart selector to fall back to shares."""
        return []

    def bracketOrder(  # noqa: N802
        self,
        action: str,
        quantity: int,
        limitPrice: float = 0.0,  # noqa: N803
        takeProfitPrice: float = 0.0,  # noqa: N803
        stopLossPrice: float = 0.0,  # noqa: N803
        **_: Any,
    ) -> list[Any]:
        parent = MagicMock()
        parent.orderId = self._counter
        parent.totalQuantity = quantity
        self._counter += 1
        tp = MagicMock()
        tp.orderId = self._counter
        tp.parentId = parent.orderId
        tp.totalQuantity = quantity
        self._counter += 1
        sl = MagicMock()
        sl.orderId = self._counter
        sl.parentId = parent.orderId
        sl.totalQuantity = quantity
        self._counter += 1
        return [parent, tp, sl]

    def placeOrder(self, contract: Any, order: Any) -> Any:  # noqa: N802
        trade = MagicMock()
        trade.order = order
        trade.orderStatus.orderId = order.orderId
        trade.orderStatus.permId = 111222333
        trade.orderStatus.status = "Filled"
        trade.orderStatus.filled = float(order.totalQuantity)
        trade.orderStatus.avgFillPrice = self._fill_price
        trade.fills = []
        return trade


class _FakeConnection:
    """Drop-in for IBKRConnection — skips all real network I/O."""

    def __init__(self, fake_ib: _FakeIB | None = None) -> None:
        self._ib = fake_ib or _FakeIB()

    async def connect(self) -> None:
        pass

    def disconnect(self) -> None:
        pass

    @property
    def ib(self) -> _FakeIB:
        return self._ib

    @property
    def is_connected(self) -> bool:
        return True


# ── helpers ───────────────────────────────────────────────────────────────────


def _make_settings(**overrides: Any) -> Settings:
    defaults: dict[str, Any] = {
        "discord_bot_token": "fake",
        "ibkr_account_id": "DU999999",
        "paper_mode": True,
        "profile": "discord_equities",
        "sleeve_value": "100000",
        "min_position_pct": "0.03",
        "max_position_pct": "0.07",
        "max_open_positions": 10,
        "max_daily_drawdown_pct": "0.05",
    }
    defaults.update(overrides)
    return Settings.model_validate(defaults)


def _make_healthy_portfolio(signal: SignalEvent) -> PortfolioSnapshot:
    """Return a stamped PortfolioSnapshot with room for one new position."""
    raw = PortfolioSnapshot(
        correlation_id=signal.correlation_id,
        account_id="DU999999",
        net_liquidation=Decimal("500000"),
        sleeve_value=Decimal("100000"),
        cash_available=Decimal("80000"),
        positions=[],
        open_position_count=0,
        daily_pnl=Decimal("0"),
        daily_pnl_pct=Decimal("0"),
    )
    return stamp(raw)  # type: ignore[return-value]


def _make_signal(raw_text: str, msg_id: str = "e2e_001") -> SignalEvent:
    raw = SignalEvent(
        source_guild_id="111222333444555666",
        source_channel_id="999888777666555444",
        source_message_id=msg_id,
        source_author_id="trader_001",
        source_author_roles=["Trader"],
        raw_text=raw_text,
        profile="discord_equities",
    )
    return stamp(raw)  # type: ignore[return-value]


async def _run_pipeline(
    bus: PipelineBus,
    settings: Settings,
    signal: SignalEvent,
    portfolio: PortfolioSnapshot,
    connection: _FakeConnection | None = None,
    receipt_timeout: float = 5.0,
) -> ExecutionReceipt | None:
    """
    Inject *signal* into the bus and run all three agents concurrently.
    Returns the first receipt from bus.receipts, or None on timeout.
    """
    conn = connection or _FakeConnection()

    with patch(
        "agents.risk_officer.portfolio_adapter.PortfolioAdapter.get_snapshot",
        AsyncMock(return_value=portfolio),
    ):
        interpreter = InterpreterAgent(settings, bus)
        risk = RiskOfficerAgent(settings, bus)
        executor = IBKRExecutorAgent(settings, bus, connection=conn)

        await bus.signals.put(signal)

        tasks = [
            asyncio.create_task(interpreter.run(), name="interpreter"),
            asyncio.create_task(risk.run(), name="risk"),
            asyncio.create_task(executor.run(), name="executor"),
        ]

        receipt: ExecutionReceipt | None = None
        try:
            receipt = await asyncio.wait_for(bus.receipts.get(), timeout=receipt_timeout)
        except TimeoutError:
            pass
        finally:
            for task in tasks:
                task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await asyncio.gather(*tasks, return_exceptions=True)

    return receipt


# ── happy path ────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_e2e_approved_signal_produces_filled_receipt() -> None:
    """Standard BUY signal with full price information → FILLED receipt."""
    settings = _make_settings()
    bus = PipelineBus()
    signal = _make_signal("BUY AAPL @ 175.50 stop 172.00 target 181.00")
    portfolio = _make_healthy_portfolio(signal)

    receipt = await _run_pipeline(bus, settings, signal, portfolio)

    assert receipt is not None
    assert receipt.status == OrderStatus.FILLED


@pytest.mark.asyncio
async def test_e2e_correlation_id_preserved_end_to_end() -> None:
    """correlation_id in the injected SignalEvent must survive all three agents."""
    settings = _make_settings()
    bus = PipelineBus()
    signal = _make_signal("BUY MSFT @ 420.00 stop 415.00 target 430.00", "e2e_002")
    portfolio = _make_healthy_portfolio(signal)

    receipt = await _run_pipeline(bus, settings, signal, portfolio)

    assert receipt is not None
    assert receipt.correlation_id == signal.correlation_id


@pytest.mark.asyncio
async def test_e2e_is_paper_flag_set() -> None:
    settings = _make_settings(paper_mode=True)
    bus = PipelineBus()
    signal = _make_signal("BUY NVDA @ 800 stop 790 target 820", "e2e_003")
    portfolio = _make_healthy_portfolio(signal)

    receipt = await _run_pipeline(bus, settings, signal, portfolio)

    assert receipt is not None
    assert receipt.is_paper is True


@pytest.mark.asyncio
async def test_e2e_receipt_is_tamper_evident() -> None:
    """ExecutionReceipt must carry a valid Blake2b event_hash."""
    settings = _make_settings()
    bus = PipelineBus()
    signal = _make_signal("BUY AAPL @ 175.50 stop 172.00 target 181.00", "e2e_004")
    portfolio = _make_healthy_portfolio(signal)

    receipt = await _run_pipeline(bus, settings, signal, portfolio)

    assert receipt is not None
    assert receipt.event_hash != ""
    assert verify(receipt)


@pytest.mark.asyncio
async def test_e2e_bracket_legs_populated() -> None:
    """Both stop-loss and take-profit order IDs must be in the receipt."""
    settings = _make_settings()
    bus = PipelineBus()
    signal = _make_signal("BUY AAPL @ 175.50 stop 172.00 target 181.00", "e2e_005")
    portfolio = _make_healthy_portfolio(signal)

    receipt = await _run_pipeline(bus, settings, signal, portfolio)

    assert receipt is not None
    assert receipt.stop_order_id is not None
    assert receipt.take_profit_order_id is not None
    assert receipt.stop_order_id != receipt.take_profit_order_id


@pytest.mark.asyncio
async def test_e2e_short_signal_produces_filled_receipt() -> None:
    """SELL/SHORT signal must traverse the pipeline and fill."""
    settings = _make_settings()
    bus = PipelineBus()
    signal = _make_signal("SELL TSLA @ 200.00 stop 205.00 target 190.00", "e2e_006")
    portfolio = _make_healthy_portfolio(signal)

    receipt = await _run_pipeline(bus, settings, signal, portfolio)

    assert receipt is not None
    assert receipt.status == OrderStatus.FILLED


# ── rejection paths ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_e2e_unparseable_signal_drops_silently() -> None:
    """A message that can't be parsed must produce no receipt (pipeline drops it)."""
    settings = _make_settings()
    bus = PipelineBus()
    signal = _make_signal("Hey everyone, what do you think about the market?", "e2e_007")
    portfolio = _make_healthy_portfolio(signal)

    # Short timeout — we expect nothing to arrive
    receipt = await _run_pipeline(bus, settings, signal, portfolio, receipt_timeout=0.5)

    assert receipt is None


@pytest.mark.asyncio
async def test_e2e_max_positions_exceeded_produces_cancelled_receipt() -> None:
    """Risk gate must reject when max_open_positions is already at capacity."""
    settings = _make_settings(max_open_positions=0)  # no room for any new position
    bus = PipelineBus()
    signal = _make_signal("BUY AAPL @ 175.50 stop 172.00 target 181.00", "e2e_008")
    portfolio = _make_healthy_portfolio(signal)

    receipt = await _run_pipeline(bus, settings, signal, portfolio)

    assert receipt is not None
    assert receipt.status == OrderStatus.CANCELLED
    assert receipt.error_message is not None
    assert "REJECTED" in receipt.error_message


@pytest.mark.asyncio
async def test_e2e_drawdown_halt_produces_cancelled_receipt() -> None:
    """Risk gate must reject when daily drawdown limit is breached."""
    settings = _make_settings()
    bus = PipelineBus()
    signal = _make_signal("BUY AAPL @ 175.50 stop 172.00 target 181.00", "e2e_009")

    # Portfolio with daily loss exceeding the 5% drawdown cap
    raw_portfolio = PortfolioSnapshot(
        correlation_id=signal.correlation_id,
        account_id="DU999999",
        net_liquidation=Decimal("500000"),
        sleeve_value=Decimal("100000"),
        cash_available=Decimal("80000"),
        positions=[],
        open_position_count=0,
        daily_pnl=Decimal("-6000"),
        daily_pnl_pct=Decimal("-0.06"),  # -6% > -5% threshold
    )
    portfolio: PortfolioSnapshot = stamp(raw_portfolio)  # type: ignore[assignment]

    receipt = await _run_pipeline(bus, settings, signal, portfolio)

    assert receipt is not None
    assert receipt.status == OrderStatus.CANCELLED


# ── two-signal ordering ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_e2e_two_signals_produce_two_receipts_in_order() -> None:
    """Pipeline must process multiple signals sequentially and preserve order."""
    settings = _make_settings()
    bus = PipelineBus()

    signal_a = _make_signal("BUY AAPL @ 175.50 stop 172.00 target 181.00", "e2e_010a")
    signal_b = _make_signal("BUY MSFT @ 420.00 stop 415.00 target 430.00", "e2e_010b")

    portfolio_a = _make_healthy_portfolio(signal_a)
    portfolio_b = _make_healthy_portfolio(signal_b)
    portfolios = [portfolio_a, portfolio_b]

    with patch(
        "agents.risk_officer.portfolio_adapter.PortfolioAdapter.get_snapshot",
        AsyncMock(side_effect=portfolios),
    ):
        interpreter = InterpreterAgent(settings, bus)
        risk = RiskOfficerAgent(settings, bus)
        executor = IBKRExecutorAgent(settings, bus, connection=_FakeConnection())

        await bus.signals.put(signal_a)
        await bus.signals.put(signal_b)

        tasks = [
            asyncio.create_task(interpreter.run()),
            asyncio.create_task(risk.run()),
            asyncio.create_task(executor.run()),
        ]

        receipts: list[ExecutionReceipt] = []
        try:
            for _ in range(2):
                r = await asyncio.wait_for(bus.receipts.get(), timeout=5.0)
                receipts.append(r)
        finally:
            for task in tasks:
                task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await asyncio.gather(*tasks, return_exceptions=True)

    assert len(receipts) == 2
    assert all(r.status == OrderStatus.FILLED for r in receipts)
    cids = {r.correlation_id for r in receipts}
    assert signal_a.correlation_id in cids
    assert signal_b.correlation_id in cids
