"""
CLI entrypoint for the Risk Officer Agent.

Usage:
    python -m cli.run_risk [--profile <name>]
    oct-risk [--profile <name>]
"""
from __future__ import annotations

import argparse
import asyncio

from agents.risk_officer import RiskOfficerAgent
from audit.logger import configure_logging, get_logger
from bus.queue import PipelineBus
from config.settings import load_settings


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="OpenClaw Risk Officer Agent — evaluate TradeIntents against risk limits",
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
    log = get_logger("cli.run_risk")

    log.info(
        "risk_officer_starting",
        profile=settings.profile,
        paper_mode=settings.paper_mode,
        sleeve_value=str(settings.sleeve_value),
        min_position_pct=str(settings.min_position_pct),
        max_position_pct=str(settings.max_position_pct),
    )

    bus = PipelineBus()
    agent = RiskOfficerAgent(settings, bus)

    try:
        await agent.run()
    except KeyboardInterrupt:
        log.info("risk_officer_shutdown_requested")
    finally:
        await agent.close()
        log.info("risk_officer_stopped")


def main() -> None:
    args = _parse_args()
    asyncio.run(_run(args.profile))


if __name__ == "__main__":
    main()
