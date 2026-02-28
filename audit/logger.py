from __future__ import annotations

import logging
from contextvars import ContextVar
from typing import Any
from uuid import UUID

import structlog
from structlog.types import EventDict, WrappedLogger

# Context variable for correlation ID — survives async task switches
_correlation_id_var: ContextVar[str] = ContextVar("correlation_id", default="")


def _add_correlation_id(
    _logger: WrappedLogger,
    _method_name: str,
    event_dict: EventDict,
) -> EventDict:
    """Structlog processor: inject correlation_id into every log event."""
    cid = _correlation_id_var.get()
    if cid:
        event_dict.setdefault("correlation_id", cid)
    return event_dict


def bind_correlation_id(correlation_id: UUID | str) -> None:
    """
    Set the correlation ID for all log calls that follow in this async context.
    Call once per pipeline entry (at Discord message receipt).
    """
    _correlation_id_var.set(str(correlation_id))


def get_correlation_id() -> str:
    """Return the active correlation ID, or empty string if not set."""
    return _correlation_id_var.get()


def configure_logging(
    level: str = "INFO",
    fmt: str = "json",
    log_file: str | None = None,
) -> None:
    """
    Configure structlog globally.  Call once at agent startup.

    Args:
        level:    Logging level string ("DEBUG", "INFO", "WARNING", "ERROR").
        fmt:      Output format — "json" for production, "console" for dev.
        log_file: Optional path to append logs to (in addition to stdout).
    """
    log_level = getattr(logging, level.upper(), logging.INFO)

    shared_processors: list[Any] = [
        structlog.contextvars.merge_contextvars,
        _add_correlation_id,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.ExceptionRenderer(),
    ]

    renderer: Any
    if fmt == "console":
        renderer = structlog.dev.ConsoleRenderer()
    else:
        renderer = structlog.processors.JSONRenderer()

    structlog.configure(
        processors=[*shared_processors, renderer],
        wrapper_class=structlog.make_filtering_bound_logger(log_level),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )


def get_logger(name: str | None = None) -> Any:
    """
    Return a structlog logger.  The logger automatically includes
    correlation_id on every event when bind_correlation_id() has been called.
    """
    return structlog.get_logger(name) if name else structlog.get_logger()
