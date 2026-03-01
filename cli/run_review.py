"""
CLI entrypoint for the Review Dispatcher.

Usage:
    python -m cli.run_review
    python -m cli.run_review --profile discord_equities

Consumes ExecutionReceipts from bus.receipts and dispatches CANCELLED+NEEDS_APPROVAL
to the configured backend (log or webhook). In production, the bus is shared with
the executor via an external broker (Redis Streams, etc.). For single-process runs,
use run_paper which includes the review dispatcher as a 5th task.
"""
from __future__ import annotations

import argparse
import asyncio

from agents.review import ReviewDispatcher
from audit.logger import configure_logging, get_logger
from bus.queue import PipelineBus
from config.settings import load_settings


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="OpenClaw — Review Dispatcher (NEEDS_APPROVAL delivery)",
    )
    parser.add_argument(
        "--profile",
        default=None,
        help="Config profile to load from config/profiles/<name>.toml",
    )
    return parser.parse_args()


async def _main(profile: str | None) -> None:
    settings = load_settings(profile)
    configure_logging(settings.log_level, settings.log_format, settings.log_file)
    log = get_logger("run_review")

    log.info(
        "review_dispatcher_startup",
        profile=settings.profile,
        backend=getattr(settings, "review_backend", "log"),
    )

    bus = PipelineBus()
    dispatcher = ReviewDispatcher(bus, settings)
    await dispatcher.run()


def main() -> None:
    args = _parse_args()
    asyncio.run(_main(args.profile))


if __name__ == "__main__":
    main()
