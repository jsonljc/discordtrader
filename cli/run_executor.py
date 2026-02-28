"""
CLI entrypoint for the IBKR Executor Agent.

Usage:
    python -m cli.run_executor
    python -m cli.run_executor --profile discord_equities

This process:
    1. Loads typed settings (with optional profile overlay)
    2. Configures structured logging
    3. Creates the shared PipelineBus
    4. Starts the IBKRExecutorAgent (connects to TWS/Gateway, then loops)

In a production deployment each agent runs in its own process / OpenClaw
workspace.  The PipelineBus here is a stub — replace the queue with an
external broker (Redis Streams, etc.) when scaling beyond a single process.
"""
from __future__ import annotations

import argparse
import asyncio

from agents.ibkr_executor import IBKRExecutorAgent
from audit.logger import configure_logging, get_logger
from bus.queue import PipelineBus
from config.settings import load_settings


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="OpenClaw — IBKR Executor Agent")
    parser.add_argument(
        "--profile",
        default=None,
        help="Config profile to load from config/profiles/<name>.toml",
    )
    return parser.parse_args()


async def _main() -> None:
    args = _parse_args()
    settings = load_settings(args.profile)
    configure_logging(level=settings.log_level, fmt=settings.log_format)
    log = get_logger("run_executor")

    log.info(
        "executor_startup",
        profile=settings.profile,
        paper_mode=settings.paper_mode,
        ibkr_host=settings.ibkr_host,
        ibkr_port=settings.ibkr_port,
    )

    bus = PipelineBus()
    agent = IBKRExecutorAgent(settings, bus)
    try:
        await agent.run()
    finally:
        await agent.close()


if __name__ == "__main__":
    asyncio.run(_main())
