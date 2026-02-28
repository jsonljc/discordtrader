"""
CLI entrypoint for the Discord Listener Agent.

Usage:
    python -m cli.run_listener [--profile <name>]
    oct-listener [--profile <name>]
"""
from __future__ import annotations

import argparse
import asyncio
import sys

from agents.discord_listener import DiscordListenerAgent
from audit.logger import configure_logging, get_logger
from bus.queue import PipelineBus
from config.settings import load_settings


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="OpenClaw Discord Listener Agent — ingest signals from Discord",
    )
    parser.add_argument(
        "--profile",
        default=None,
        metavar="NAME",
        help="Strategy profile (maps to config/profiles/<name>.toml). "
        "Defaults to PROFILE env var or 'discord_equities'.",
    )
    return parser.parse_args()


async def _run(profile: str | None) -> None:
    settings = load_settings(profile)
    configure_logging(settings.log_level, settings.log_format, settings.log_file)
    log = get_logger("cli.run_listener")

    log.info(
        "listener_starting",
        profile=settings.profile,
        paper_mode=settings.paper_mode,
        allowed_guilds=list(settings.discord_allowed_guild_ids),
        allowed_channels=list(settings.discord_allowed_channel_ids),
    )

    bus = PipelineBus()
    agent = DiscordListenerAgent(settings, bus)

    try:
        await agent.start()
    except KeyboardInterrupt:
        log.info("listener_shutdown_requested")
    finally:
        await agent.close()
        log.info("listener_stopped")


def main() -> None:
    args = _parse_args()
    try:
        asyncio.run(_run(args.profile))
    except ValueError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)  # noqa: T201
        sys.exit(1)


if __name__ == "__main__":
    main()
