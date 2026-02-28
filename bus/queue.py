from __future__ import annotations

import asyncio
from typing import Generic, TypeVar

from pydantic import BaseModel

from schemas.execution_receipt import ExecutionReceipt
from schemas.risk_decision import RiskDecision
from schemas.signal_event import SignalEvent
from schemas.trade_intent import TradeIntent

T = TypeVar("T", bound=BaseModel)

DEFAULT_MAXSIZE = 256


class TypedQueue(Generic[T]):
    """
    Thin asyncio.Queue[T] wrapper that carries the item type at the class level.
    Drop-in replacement when a Redis or similar transport is added later.
    """

    def __init__(self, maxsize: int = DEFAULT_MAXSIZE) -> None:
        self._q: asyncio.Queue[T] = asyncio.Queue(maxsize=maxsize)

    async def put(self, item: T) -> None:
        """Enqueue an item, blocking if the queue is full."""
        await self._q.put(item)

    async def get(self) -> T:
        """Dequeue an item, blocking until one is available."""
        return await self._q.get()

    def task_done(self) -> None:
        self._q.task_done()

    def qsize(self) -> int:
        return self._q.qsize()

    def empty(self) -> bool:
        return self._q.empty()


class PipelineBus:
    """
    Holds all inter-agent typed queues for one pipeline run.

    Pipeline flow:
        Discord Listener → signals → Interpreter
        Interpreter      → intents  → Risk Officer
        Risk Officer     → decisions → IBKR Executor
        IBKR Executor    → receipts  → Audit / Governor
    """

    def __init__(self, maxsize: int = DEFAULT_MAXSIZE) -> None:
        self.signals: TypedQueue[SignalEvent] = TypedQueue(maxsize)
        self.intents: TypedQueue[TradeIntent] = TypedQueue(maxsize)
        self.decisions: TypedQueue[RiskDecision] = TypedQueue(maxsize)
        self.receipts: TypedQueue[ExecutionReceipt] = TypedQueue(maxsize)
