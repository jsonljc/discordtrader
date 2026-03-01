"""Unit tests for ReviewDispatcher."""
from __future__ import annotations

import asyncio
import uuid
from decimal import Decimal
import pytest

from agents.review.dispatcher import ReviewDispatcher, _is_needs_approval_receipt
from bus.queue import PipelineBus
from config.settings import Settings
from schemas.execution_receipt import ExecutionReceipt, OrderStatus


def _make_settings(**overrides: object) -> Settings:
    defaults = {
        "discord_bot_token": "x",
        "review_backend": "log",
    }
    defaults.update(overrides)
    return Settings.model_validate(defaults)


def _make_receipt(
    status: OrderStatus = OrderStatus.CANCELLED,
    error_message: str | None = None,
) -> ExecutionReceipt:
    return ExecutionReceipt(
        correlation_id=uuid.uuid4(),
        source_decision_id=uuid.uuid4(),
        status=status,
        error_message=error_message,
        is_paper=True,
        profile="discord_equities",
    )


def test_is_needs_approval_receipt_true() -> None:
    r = _make_receipt(
        status=OrderStatus.CANCELLED,
        error_message="not_approved:NEEDS_APPROVAL",
    )
    assert _is_needs_approval_receipt(r) is True


def test_is_needs_approval_receipt_false_when_filled() -> None:
    r = _make_receipt(status=OrderStatus.FILLED, error_message="NEEDS_APPROVAL")
    assert _is_needs_approval_receipt(r) is False


def test_is_needs_approval_receipt_false_when_no_message() -> None:
    r = _make_receipt(status=OrderStatus.CANCELLED, error_message=None)
    assert _is_needs_approval_receipt(r) is False


def test_is_needs_approval_receipt_false_when_different_message() -> None:
    r = _make_receipt(
        status=OrderStatus.CANCELLED,
        error_message="not_approved:REJECTED",
    )
    assert _is_needs_approval_receipt(r) is False


@pytest.mark.asyncio
async def test_dispatcher_logs_needs_approval_receipt() -> None:
    settings = _make_settings(review_backend="log")
    bus = PipelineBus()
    dispatcher = ReviewDispatcher(bus, settings)

    receipt = _make_receipt(
        status=OrderStatus.CANCELLED,
        error_message="not_approved:NEEDS_APPROVAL",
    )

    async def _produce() -> None:
        await bus.receipts.put(receipt)

    task = asyncio.create_task(dispatcher.run())
    prod = asyncio.create_task(_produce())
    await asyncio.wait_for(prod, timeout=1.0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
