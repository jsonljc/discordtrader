"""
CLI entrypoint for the Interpreter Agent.

Usage:
    python -m cli.run_interpreter [--profile <name>]
    oct-interpreter [--profile <name>]
"""
from __future__ import annotations

import argparse
import asyncio

from agents.interpreter import InterpreterAgent
from audit.logger import configure_logging, get_logger
from bus.queue import PipelineBus
from config.settings import load_settings


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="OpenClaw Interpreter Agent — parse SignalEvents into TradeIntents",
    )
    parser.add_argument(
        "--profile",
        default=None,
        metavar="NAME",
        help="Strategy profile (maps to config/profiles/<name>.toml).",
    )
    return parser.parse_args()


async def _run(profile: str | None) -> None:
    settings = load_settings(profile)
    configure_logging(settings.log_level, settings.log_format, settings.log_file)
    log = get_logger("cli.run_interpreter")

    log.info("interpreter_starting", profile=settings.profile)

    bus = PipelineBus()
    agent = InterpreterAgent(settings, bus)

    try:
        await agent.run()
    except KeyboardInterrupt:
        log.info("interpreter_shutdown_requested")
    log.info("interpreter_stopped")


def main() -> None:
    args = _parse_args()
    asyncio.run(_run(args.profile))


if __name__ == "__main__":
    main()
