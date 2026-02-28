"""
Single-process paper mode launcher — all four agents in one asyncio event loop.

Usage:
    python -m cli.run_paper
    python -m cli.run_paper --profile discord_equities
    oct-paper --profile paper

This is the recommended way to run a complete paper-mode trial before
considering live deployment.  In production, run each agent in its own
process using the individual entrypoints or the Procfile.

Shutdown
--------
Send SIGINT (Ctrl-C) to trigger a clean shutdown.  All agents finish their
current event (or cancel cleanly), then the IBKR connection is closed.

Live mode gate
--------------
Set PAPER_MODE=false in .env ONLY after completing the Definition of Done
checklist in the README.
"""
from __future__ import annotations

import argparse
import asyncio
import sys

from agents.discord_listener import DiscordListenerAgent
from agents.ibkr_executor import IBKRExecutorAgent
from agents.interpreter import InterpreterAgent
from agents.risk_officer import RiskOfficerAgent
from audit.logger import configure_logging, get_logger
from bus.queue import PipelineBus
from config.settings import load_settings


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="OpenClaw — all agents in one process (paper / dev mode)",
    )
    parser.add_argument(
        "--profile",
        default=None,
        metavar="NAME",
        help="Config profile to load from config/profiles/<name>.toml",
    )
    return parser.parse_args()


async def _run(profile: str | None) -> None:
    settings = load_settings(profile)
    configure_logging(settings.log_level, settings.log_format, settings.log_file)
    log = get_logger("cli.run_paper")

    if not settings.paper_mode:
        log.warning(
            "live_mode_active_in_run_paper",
            message="PAPER_MODE is False — real orders will be placed",
        )

    log.info(
        "all_agents_starting",
        profile=settings.profile,
        paper_mode=settings.paper_mode,
        ibkr_host=settings.ibkr_host,
        ibkr_port=settings.ibkr_port,
        sleeve_value=str(settings.sleeve_value),
    )

    bus = PipelineBus()
    listener = DiscordListenerAgent(settings, bus)
    interpreter = InterpreterAgent(settings, bus)
    risk = RiskOfficerAgent(settings, bus)
    executor = IBKRExecutorAgent(settings, bus)

    # Each agent runs as an independent Task; if one crashes it does not
    # immediately bring down the others (return_exceptions=True), but the
    # error is logged so the operator can investigate.
    tasks = [
        asyncio.create_task(listener.start(), name="listener"),
        asyncio.create_task(interpreter.run(), name="interpreter"),
        asyncio.create_task(risk.run(), name="risk"),
        asyncio.create_task(executor.run(), name="executor"),
    ]

    try:
        results = await asyncio.gather(*tasks, return_exceptions=True)
        for task, result in zip(tasks, results, strict=False):
            if isinstance(result, Exception):
                log.error(
                    "agent_exited_with_error",
                    name=task.get_name(),
                    error=str(result),
                    error_type=type(result).__name__,
                )
    except asyncio.CancelledError:
        log.info("run_paper_cancelled")
    finally:
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await risk.close()
        await executor.close()
        await listener.close()
        log.info("all_agents_stopped")


def main() -> None:
    args = _parse_args()
    try:
        asyncio.run(_run(args.profile))
    except KeyboardInterrupt:
        pass  # clean exit on Ctrl-C; cleanup handled in finally block above
    except ValueError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)  # noqa: T201
        sys.exit(1)


if __name__ == "__main__":
    main()
