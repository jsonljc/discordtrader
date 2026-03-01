"""Unit tests for config/settings validation (e.g. validate_live_mode)."""
from __future__ import annotations

import pytest

from config.settings import Settings, validate_live_mode


def test_validate_live_mode_passes_when_paper_mode_true() -> None:
    """Paper mode with any port is valid."""
    settings = Settings.model_validate({
        "discord_bot_token": "x",
        "paper_mode": True,
        "ibkr_port": 7497,
    })
    validate_live_mode(settings)  # no raise


def test_validate_live_mode_passes_when_live_port() -> None:
    """Live mode with live port (7496 or 4001) is valid."""
    for port in (7496, 4001):
        settings = Settings.model_validate({
            "discord_bot_token": "x",
            "paper_mode": False,
            "ibkr_port": port,
        })
        validate_live_mode(settings)  # no raise


def test_validate_live_mode_raises_when_paper_port() -> None:
    """Live mode with paper port raises ValueError."""
    for port in (7497, 4002):
        settings = Settings.model_validate({
            "discord_bot_token": "x",
            "paper_mode": False,
            "ibkr_port": port,
        })
        with pytest.raises(ValueError) as exc_info:
            validate_live_mode(settings)
        assert "paper" in str(exc_info.value).lower()
        assert str(port) in str(exc_info.value)
