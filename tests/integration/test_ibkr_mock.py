"""
Integration tests for the IBKR Executor Agent using a fake connection.

No real IBKR connection is needed.  _FakeConnection / _FakeIB simulate
an already-connected client that produces instant fills, allowing us to
verify the full execution pipeline end-to-end.

Test coverage:
    - APPROVED decision → FILLED receipt
    - NEEDS_APPROVAL decision → CANCELLED receipt (not auto-executed)
    - REJECTED decision → CANCELLED receipt
    - Correlation ID propagated into receipt
    - Both bracket leg order IDs recorded (stop + take-profit)
    - is_paper flag set from settings
    - ERROR receipt when contract resolution fails
    - SUBMITTED receipt when order times out (no fill)
"""
from __future__ import annotations

import asyncio
import contextlib
from decimal import Decimal
from typing import Any
from unittest.mock import MagicMock

import pytest

from agents.ibkr_executor.agent import IBKRExecutorAgent
from bus.queue import PipelineBus
from config.settings import Settings
from schemas.execution_receipt import ExecutionReceipt, OrderStatus
from schemas.risk_decision import RiskDecision, RiskOutcome
from schemas.trade_intent import Direction

# ── fakes ────────────────────────────────────────────────────────────────────


class _FakeIB:
    """
    Minimal stub that simulates a connected IB client with instant fills.

    bracketOrder() returns a 3-element list [parent, take_profit, stop_loss],
    matching ib_insync's BracketOrder(parent, takeProfit, stopLoss) layout.
    placeOrder() returns a mock Trade already in "Filled" status.
    """

    def __init__(self, fill_price: float = 175.52, fail_qualify: bool = False) -> None:
        self._fill_price = fill_price
        self._fail_qualify = fail_qualify
        self._order_counter = 1000

    def isConnected(self) -> bool:  # noqa: N802
        return True

    async def qualifyContractsAsync(self, *contracts: Any) -> list[Any]:  # noqa: N802
        if self._fail_qualify:
            return []  # simulate unresolvable ticker
        for c in contracts:
            c.conId = 265598
        return list(contracts)

    def bracketOrder(  # noqa: N802
        self,
        action: str,
        quantity: int,
        limitPrice: float = 0.0,  # noqa: N803
        takeProfitPrice: float = 0.0,  # noqa: N803
        stopLossPrice: float = 0.0,  # noqa: N803
        **kwargs: Any,
    ) -> list[Any]:
        parent = MagicMock()
        parent.orderId = self._order_counter
        parent.totalQuantity = quantity
        parent.transmit = False
        self._order_counter += 1

        take_profit = MagicMock()
        take_profit.orderId = self._order_counter
        take_profit.parentId = parent.orderId
        take_profit.totalQuantity = quantity
        self._order_counter += 1

        stop_loss = MagicMock()
        stop_loss.orderId = self._order_counter
        stop_loss.parentId = parent.orderId
        stop_loss.totalQuantity = quantity
        self._order_counter += 1

        return [parent, take_profit, stop_loss]

    def placeOrder(self, contract: Any, order: Any) -> Any:  # noqa: N802
        """Return a trade mock with immediate fill status."""
        trade = MagicMock()
        trade.order = order
        trade.orderStatus.orderId = order.orderId
        trade.orderStatus.permId = 987654321
        trade.orderStatus.status = "Filled"
        trade.orderStatus.filled = float(order.totalQuantity)
        trade.orderStatus.avgFillPrice = self._fill_price
        trade.fills = []
        return trade

    async def reqOpenOrdersAsync(self) -> list[Any]:  # noqa: N802
        return []

    async def reqExecutionsAsync(self, execFilter: Any = None) -> list[Any]:  # noqa: N802
        return []


class _FakeIBTimeout(_FakeIB):
    """Variant that never fills — produces SUBMITTED on timeout."""

    def placeOrder(self, contract: Any, order: Any) -> Any:  # noqa: N802
        trade = MagicMock()
        trade.order = order
        trade.orderStatus.orderId = order.orderId
        trade.orderStatus.permId = 0
        trade.orderStatus.status = "Submitted"  # never transitions to Filled
        trade.orderStatus.filled = 0.0
        trade.orderStatus.avgFillPrice = 0.0
        trade.fills = []
        return trade


class _FakeConnection:
    """Drop-in replacement for IBKRConnection; skips real network I/O."""

    def __init__(self, fake_ib: _FakeIB | None = None) -> None:
        self._ib = fake_ib or _FakeIB()

    async def connect(self) -> None:
        pass  # already "connected"

    def disconnect(self) -> None:
        pass

    @property
    def ib(self) -> _FakeIB:
        return self._ib

    @property
    def is_connected(self) -> bool:
        return True


# ── test helpers ─────────────────────────────────────────────────────────────


def _make_settings(**overrides: Any) -> Settings:
    """Build minimal Settings without loading a .env file."""
    defaults: dict[str, Any] = {
        "discord_bot_token": "fake",
        "ibkr_account_id": "DU999999",
        "paper_mode": True,
        "profile": "discord_equities",
    }
    defaults.update(overrides)
    return Settings.model_validate(defaults)


def _make_approved_decision(**overrides: Any) -> RiskDecision:
    import uuid

    cid = uuid.uuid4()
    fields: dict[str, Any] = {
        "correlation_id": cid,
        "source_intent_id": uuid.uuid4(),
        "outcome": RiskOutcome.APPROVED,
        "approved_ticker": "AAPL",
        "approved_direction": Direction.LONG,
        "approved_quantity": 33,
        "approved_entry_price": Decimal("175.50"),
        "approved_stop_price": Decimal("172.00"),
        "approved_take_profit": Decimal("181.00"),
        "position_size_pct": Decimal("0.058"),
        "profile": "discord_equities",
    }
    fields.update(overrides)
    return RiskDecision(**fields)


async def _run_one_cycle(
    agent: IBKRExecutorAgent,
    bus: PipelineBus,
    decision: RiskDecision,
    timeout: float = 2.0,
) -> ExecutionReceipt:
    """
    Enqueue a decision, run the agent for one cycle, return the receipt.
    Cancels the agent task cleanly after the receipt is retrieved.
    """
    await bus.decisions.put(decision)
    task = asyncio.create_task(agent.run())
    receipt = await asyncio.wait_for(bus.receipts.get(), timeout=timeout)
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task
    return receipt


# ── happy path ───────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_approved_decision_produces_filled_receipt() -> None:
    settings = _make_settings()
    bus = PipelineBus()
    agent = IBKRExecutorAgent(settings, bus, connection=_FakeConnection())
    decision = _make_approved_decision()

    receipt = await _run_one_cycle(agent, bus, decision)

    assert receipt.status == OrderStatus.FILLED
    assert receipt.filled_quantity == Decimal("33")


@pytest.mark.asyncio
async def test_receipt_correlation_id_matches_decision() -> None:
    settings = _make_settings()
    bus = PipelineBus()
    agent = IBKRExecutorAgent(settings, bus, connection=_FakeConnection())
    decision = _make_approved_decision()

    receipt = await _run_one_cycle(agent, bus, decision)

    assert receipt.correlation_id == decision.correlation_id


@pytest.mark.asyncio
async def test_receipt_source_decision_id_matches() -> None:
    settings = _make_settings()
    bus = PipelineBus()
    agent = IBKRExecutorAgent(settings, bus, connection=_FakeConnection())
    decision = _make_approved_decision()

    receipt = await _run_one_cycle(agent, bus, decision)

    assert receipt.source_decision_id == decision.event_id


@pytest.mark.asyncio
async def test_is_paper_flag_set_from_settings() -> None:
    settings = _make_settings(paper_mode=True)
    bus = PipelineBus()
    agent = IBKRExecutorAgent(settings, bus, connection=_FakeConnection())

    receipt = await _run_one_cycle(agent, bus, _make_approved_decision())

    assert receipt.is_paper is True


@pytest.mark.asyncio
async def test_ibkr_order_id_populated() -> None:
    settings = _make_settings()
    bus = PipelineBus()
    agent = IBKRExecutorAgent(settings, bus, connection=_FakeConnection())

    receipt = await _run_one_cycle(agent, bus, _make_approved_decision())

    assert receipt.ibkr_order_id is not None
    assert isinstance(receipt.ibkr_order_id, int)


@pytest.mark.asyncio
async def test_avg_fill_price_matches_fake_fill() -> None:
    fake_ib = _FakeIB(fill_price=175.52)
    settings = _make_settings()
    bus = PipelineBus()
    agent = IBKRExecutorAgent(settings, bus, connection=_FakeConnection(fake_ib))

    receipt = await _run_one_cycle(agent, bus, _make_approved_decision())

    assert receipt.avg_fill_price == pytest.approx(Decimal("175.52"), abs=Decimal("0.01"))


@pytest.mark.asyncio
async def test_bracket_leg_order_ids_populated() -> None:
    settings = _make_settings()
    bus = PipelineBus()
    agent = IBKRExecutorAgent(settings, bus, connection=_FakeConnection())

    receipt = await _run_one_cycle(agent, bus, _make_approved_decision())

    assert receipt.stop_order_id is not None
    assert receipt.take_profit_order_id is not None
    # Bracket legs should be different order IDs
    assert receipt.stop_order_id != receipt.take_profit_order_id
    assert receipt.ibkr_order_id != receipt.stop_order_id
    assert receipt.ibkr_order_id != receipt.take_profit_order_id


@pytest.mark.asyncio
async def test_receipt_is_stamped_with_hash() -> None:
    from audit.hasher import verify

    settings = _make_settings()
    bus = PipelineBus()
    agent = IBKRExecutorAgent(settings, bus, connection=_FakeConnection())

    receipt = await _run_one_cycle(agent, bus, _make_approved_decision())

    assert receipt.event_hash != ""
    assert verify(receipt)


# ── non-approved decisions ───────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_needs_approval_decision_produces_cancelled_receipt() -> None:
    import uuid

    settings = _make_settings()
    bus = PipelineBus()
    agent = IBKRExecutorAgent(settings, bus, connection=_FakeConnection())
    decision = RiskDecision(
        correlation_id=uuid.uuid4(),
        source_intent_id=uuid.uuid4(),
        outcome=RiskOutcome.NEEDS_APPROVAL,
        rejection_reasons=["manual_review_required"],
        position_size_pct=Decimal("0"),
        profile="discord_equities",
    )

    receipt = await _run_one_cycle(agent, bus, decision)

    assert receipt.status == OrderStatus.CANCELLED
    assert receipt.error_message is not None
    assert "NEEDS_APPROVAL" in receipt.error_message


@pytest.mark.asyncio
async def test_rejected_decision_produces_cancelled_receipt() -> None:
    import uuid

    settings = _make_settings()
    bus = PipelineBus()
    agent = IBKRExecutorAgent(settings, bus, connection=_FakeConnection())
    decision = RiskDecision(
        correlation_id=uuid.uuid4(),
        source_intent_id=uuid.uuid4(),
        outcome=RiskOutcome.REJECTED,
        rejection_reasons=["drawdown_exceeded"],
        position_size_pct=Decimal("0"),
        profile="discord_equities",
    )

    receipt = await _run_one_cycle(agent, bus, decision)

    assert receipt.status == OrderStatus.CANCELLED
    assert "REJECTED" in (receipt.error_message or "")


@pytest.mark.asyncio
async def test_non_approved_receipt_has_correct_correlation_id() -> None:
    import uuid

    cid = uuid.uuid4()
    settings = _make_settings()
    bus = PipelineBus()
    agent = IBKRExecutorAgent(settings, bus, connection=_FakeConnection())
    decision = RiskDecision(
        correlation_id=cid,
        source_intent_id=uuid.uuid4(),
        outcome=RiskOutcome.REJECTED,
        rejection_reasons=["max_positions_exceeded"],
        position_size_pct=Decimal("0"),
        profile="discord_equities",
    )

    receipt = await _run_one_cycle(agent, bus, decision)

    assert receipt.correlation_id == cid


# ── error paths ──────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_unresolvable_ticker_produces_error_receipt() -> None:
    fake_ib = _FakeIB(fail_qualify=True)
    settings = _make_settings()
    bus = PipelineBus()
    agent = IBKRExecutorAgent(settings, bus, connection=_FakeConnection(fake_ib))

    receipt = await _run_one_cycle(agent, bus, _make_approved_decision())

    assert receipt.status == OrderStatus.ERROR
    assert receipt.error_message is not None


# ── timeout / partial fill ───────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_timeout_produces_submitted_receipt() -> None:
    """
    When TWS never sends a fill confirmation the executor should emit a
    SUBMITTED receipt rather than blocking forever.

    We patch `track_fill` at the import site in agent.py so the agent uses
    our stub, which returns SUBMITTED immediately without real polling.
    """
    from unittest.mock import AsyncMock, patch

    from agents.ibkr_executor.order_tracker import TrackResult

    submitted_result = TrackResult(
        ibkr_order_id=1000,
        ibkr_perm_id=None,
        status=OrderStatus.SUBMITTED,
        filled_quantity=Decimal("0"),
        avg_fill_price=None,
        commission=None,
    )

    settings = _make_settings()
    bus = PipelineBus()
    agent = IBKRExecutorAgent(
        settings, bus, connection=_FakeConnection(_FakeIBTimeout())
    )

    with patch(
        "agents.ibkr_executor.agent.track_fill",
        AsyncMock(return_value=submitted_result),
    ):
        receipt = await _run_one_cycle(agent, bus, _make_approved_decision(), timeout=5.0)

    assert receipt.status == OrderStatus.SUBMITTED


# ── profile propagation ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_profile_propagated_to_receipt() -> None:
    settings = _make_settings()
    bus = PipelineBus()
    agent = IBKRExecutorAgent(settings, bus, connection=_FakeConnection())
    decision = _make_approved_decision(profile="paper")

    receipt = await _run_one_cycle(agent, bus, decision)

    assert receipt.profile == "paper"


# ── short order ──────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_short_approved_decision_fills() -> None:
    import uuid

    settings = _make_settings()
    bus = PipelineBus()
    agent = IBKRExecutorAgent(settings, bus, connection=_FakeConnection(_FakeIB(fill_price=99.80)))
    decision = RiskDecision(
        correlation_id=uuid.uuid4(),
        source_intent_id=uuid.uuid4(),
        outcome=RiskOutcome.APPROVED,
        approved_ticker="TSLA",
        approved_direction=Direction.SHORT,
        approved_quantity=10,
        approved_entry_price=Decimal("100.00"),
        approved_stop_price=Decimal("103.00"),
        approved_take_profit=Decimal("95.00"),
        position_size_pct=Decimal("0.04"),
        profile="discord_equities",
    )

    receipt = await _run_one_cycle(agent, bus, decision)

    assert receipt.status == OrderStatus.FILLED
    assert receipt.filled_quantity == Decimal("10")


# ── _retry_place helper ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_retry_place_succeeds_on_first_attempt() -> None:
    """_retry_place returns result immediately when factory succeeds first time."""
    import structlog

    from agents.ibkr_executor.agent import _retry_place

    call_count = 0

    async def _factory() -> str:
        nonlocal call_count
        call_count += 1
        return "ok"

    log = structlog.get_logger()
    result = await _retry_place(_factory, log=log, correlation_id="cid", label="test")
    assert result == "ok"
    assert call_count == 1


@pytest.mark.asyncio
async def test_retry_place_retries_on_transient_failure() -> None:
    """_retry_place retries after a transient error and returns on subsequent success."""
    import structlog

    from agents.ibkr_executor.agent import _retry_place

    call_count = 0

    async def _factory() -> str:
        nonlocal call_count
        call_count += 1
        if call_count < 2:
            raise ConnectionError("transient")
        return "ok"

    log = structlog.get_logger()
    result = await _retry_place(_factory, log=log, correlation_id="cid", label="test")
    assert result == "ok"
    assert call_count == 2


@pytest.mark.asyncio
async def test_duplicate_decision_id_produces_cancelled_receipt() -> None:
    """Re-enqueued decision with same event_id is skipped and emits CANCELLED."""
    settings = _make_settings()
    bus = PipelineBus()
    agent = IBKRExecutorAgent(settings, bus, connection=_FakeConnection())
    decision = _make_approved_decision()

    receipt1 = await _run_one_cycle(agent, bus, decision)
    assert receipt1.status == OrderStatus.FILLED

    # Re-enqueue same decision (same event_id)
    receipt2 = await _run_one_cycle(agent, bus, decision, timeout=2.0)
    assert receipt2.status == OrderStatus.CANCELLED
    assert receipt2.error_message == "duplicate_decision_id"


@pytest.mark.asyncio
async def test_executor_emits_error_receipt_when_placement_fails() -> None:
    """Executor must emit an ERROR receipt (not crash) when order placement exhausts retries."""
    from uuid import uuid4

    class _FailIB(_FakeIB):
        def bracketOrder(self, *args: Any, **kwargs: Any) -> list[Any]:  # noqa: N802
            raise RuntimeError("simulated order failure")

    settings = _make_settings()
    bus = PipelineBus()

    class _FailConn(_FakeConnection):
        @property
        def ib(self) -> _FailIB:  # type: ignore[override]
            return _FailIB()

    agent = IBKRExecutorAgent(settings, bus, connection=_FailConn())

    decision = RiskDecision(
        correlation_id=uuid4(),
        source_intent_id=uuid4(),
        outcome=RiskOutcome.APPROVED,
        approved_ticker="AAPL",
        approved_direction=Direction.LONG,
        approved_quantity=10,
        approved_entry_price=Decimal("175.50"),
        approved_stop_price=Decimal("172.00"),
        approved_take_profit=Decimal("181.00"),
        position_size_pct=Decimal("0.07"),
        profile="discord_equities",
    )

    # timeout must exceed retry backoff: 1s + 2s = 3s of sleep, plus execution overhead
    receipt = await _run_one_cycle(agent, bus, decision, timeout=8.0)

    assert receipt.status == OrderStatus.ERROR
    assert receipt.error_message is not None
