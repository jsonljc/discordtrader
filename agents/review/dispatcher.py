"""
ReviewDispatcher — delivers CANCELLED+NEEDS_APPROVAL receipts to a review sink.

Consumes ExecutionReceipts from bus.receipts. When status is CANCELLED and
error_message contains "NEEDS_APPROVAL", dispatches to the configured backend:
  - log: structured log entry
  - webhook: HTTP POST to review_webhook_url with receipt JSON
"""
from __future__ import annotations

import asyncio
from typing import Any

from audit.ledger_writer import LedgerWriter
from audit.logger import get_logger
from bus.queue import PipelineBus
from config.settings import Settings
from schemas.execution_receipt import ExecutionReceipt, OrderStatus


def _is_needs_approval_receipt(receipt: ExecutionReceipt) -> bool:
    return (
        receipt.status == OrderStatus.CANCELLED
        and receipt.error_message is not None
        and "NEEDS_APPROVAL" in receipt.error_message
    )


class ReviewDispatcher:
    """
    Consumes bus.receipts and dispatches NEEDS_APPROVAL receipts to log or webhook.
    """

    def __init__(
        self,
        bus: PipelineBus,
        settings: Settings,
        backend: str | None = None,
        webhook_url: str | None = None,
        ledger_writer: LedgerWriter | None = None,
    ) -> None:
        self._bus = bus
        self._settings = settings
        self._backend = backend or getattr(settings, "review_backend", "log") or "log"
        self._webhook_url = webhook_url or getattr(settings, "review_webhook_url", None) or ""
        self._ledger = ledger_writer or LedgerWriter(
            getattr(settings, "ledger_path", None)
        )
        self._log = get_logger("review_dispatcher")

    async def run(self) -> None:
        """Consume bus.receipts indefinitely; dispatch NEEDS_APPROVAL to backend."""
        self._log.info(
            "review_dispatcher_started",
            backend=self._backend,
        )
        while True:
            receipt: ExecutionReceipt = await self._bus.receipts.get()
            try:
                self._ledger.append(receipt)
                if _is_needs_approval_receipt(receipt):
                    await self._dispatch(receipt)
            finally:
                self._bus.receipts.task_done()

    async def _dispatch(self, receipt: ExecutionReceipt) -> None:
        if self._backend == "log":
            self._log.info(
                "needs_approval_receipt",
                correlation_id=str(receipt.correlation_id),
                source_decision_id=str(receipt.source_decision_id),
                error_message=receipt.error_message,
                profile=receipt.profile,
                is_paper=receipt.is_paper,
            )
        elif self._backend == "webhook":
            await self._dispatch_webhook(receipt)
        else:
            self._log.warning(
                "review_dispatcher_unknown_backend",
                backend=self._backend,
            )

    async def _dispatch_webhook(self, receipt: ExecutionReceipt) -> None:
        url = self._webhook_url or ""
        if not url:
            self._log.warning(
                "review_webhook_url_not_configured",
                message="webhook backend selected but review_webhook_url is empty",
            )
            return
        try:
            import httpx

            payload: dict[str, Any] = receipt.model_dump(mode="json")
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.post(
                    url,
                    json=payload,
                )
                resp.raise_for_status()
            self._log.info(
                "needs_approval_webhook_sent",
                correlation_id=str(receipt.correlation_id),
                status_code=resp.status_code,
            )
        except Exception as exc:  # noqa: BLE001
            self._log.error(
                "needs_approval_webhook_failed",
                correlation_id=str(receipt.correlation_id),
                error=str(exc),
            )
