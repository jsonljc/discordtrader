"""
Unit tests for agents/interpreter/llm_parser.py.

All OpenAI network calls are mocked — no real API key needed.
"""
from __future__ import annotations

import json
from datetime import date
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agents.interpreter.llm_parser import (
    _build_result,
    _normalise_ticker,
    _parse_expiry,
    _safe_float,
    _safe_int,
    llm_parse,
)
from config.settings import Settings

# ── Fixtures ─────────────────────────────────────────────────────────────────


def _settings(**overrides: Any) -> Settings:
    base: dict[str, Any] = {
        "discord_bot_token": "x",
        "openai_api_key": "sk-test",
        "llm_model": "gpt-4o-mini",
        "llm_enabled": True,
        "llm_min_clarity": 50,
        "llm_timeout_seconds": 8.0,
    }
    base.update(overrides)
    return Settings(**base)


def _mock_response(payload: dict[str, Any]) -> MagicMock:
    """Build a minimal AsyncOpenAI response mock."""
    choice = MagicMock()
    choice.message.content = json.dumps(payload)
    resp = MagicMock()
    resp.choices = [choice]
    return resp


# ── _normalise_ticker ─────────────────────────────────────────────────────────


def test_normalise_ticker_strips_dollar() -> None:
    assert _normalise_ticker("$AAPL") == "AAPL"


def test_normalise_ticker_strips_hyphens() -> None:
    assert _normalise_ticker("I-R-D-M") == "IRDM"


def test_normalise_ticker_strips_spaces() -> None:
    assert _normalise_ticker("A A P L") == "AAPL"


def test_normalise_ticker_lowercase() -> None:
    assert _normalise_ticker("aapl") == "AAPL"


def test_normalise_ticker_none() -> None:
    assert _normalise_ticker(None) is None


def test_normalise_ticker_empty() -> None:
    assert _normalise_ticker("") is None


def test_normalise_ticker_only_symbols() -> None:
    assert _normalise_ticker("$$$") is None


# ── _parse_expiry ─────────────────────────────────────────────────────────────


def test_parse_expiry_valid_iso() -> None:
    assert _parse_expiry("2025-07-17") == date(2025, 7, 17)


def test_parse_expiry_none() -> None:
    assert _parse_expiry(None) is None


def test_parse_expiry_invalid_string() -> None:
    assert _parse_expiry("July 17") is None


def test_parse_expiry_empty() -> None:
    assert _parse_expiry("") is None


# ── _safe_float ───────────────────────────────────────────────────────────────


def test_safe_float_number() -> None:
    assert _safe_float(22.5) == 22.5


def test_safe_float_string() -> None:
    assert _safe_float("175.50") == 175.50


def test_safe_float_none() -> None:
    assert _safe_float(None) is None


def test_safe_float_invalid() -> None:
    assert _safe_float("abc") is None


# ── _safe_int ─────────────────────────────────────────────────────────────────


def test_safe_int_normal() -> None:
    assert _safe_int(75) == 75


def test_safe_int_clamps_high() -> None:
    assert _safe_int(150) == 100


def test_safe_int_clamps_low() -> None:
    assert _safe_int(-10) == 0


def test_safe_int_invalid() -> None:
    assert _safe_int("bad") == 0


# ── _build_result ─────────────────────────────────────────────────────────────


def test_build_result_full_equity() -> None:
    raw = {
        "ticker": "AAPL",
        "direction": "LONG",
        "asset_class": "EQUITY",
        "option_type": None,
        "strike": None,
        "expiry": None,
        "entry_price": 175.50,
        "stop_price": 172.00,
        "take_profit_price": 181.00,
        "position_size_pct": None,
        "clarity_score": 90,
        "summary": "Buy AAPL at 175.50",
        "extraction_notes": "All fields present",
    }
    result = _build_result(raw)
    assert result is not None
    assert result.ticker == "AAPL"
    assert result.direction == "LONG"
    assert result.entry_price == 175.50
    assert result.stop_price == 172.00
    assert result.clarity_score == 90


def test_build_result_options_contract() -> None:
    raw = {
        "ticker": "IRDM",
        "direction": "LONG",
        "asset_class": "OPTION",
        "option_type": "CALL",
        "strike": 22.5,
        "expiry": "2025-07-17",
        "entry_price": 3.35,
        "stop_price": None,
        "take_profit_price": None,
        "position_size_pct": 0.015,
        "clarity_score": 80,
        "summary": "Buy IRDM $22.5C July 17",
        "extraction_notes": "Option contract explicitly specified",
    }
    result = _build_result(raw)
    assert result is not None
    assert result.ticker == "IRDM"
    assert result.option_type == "CALL"
    assert result.strike == 22.5
    assert result.expiry == date(2025, 7, 17)
    assert result.position_size_pct == 0.015


def test_build_result_normalises_ticker_with_hyphens() -> None:
    raw = {
        "ticker": "I-R-D-M",
        "direction": "LONG",
        "asset_class": "EQUITY",
        "option_type": None,
        "strike": None,
        "expiry": None,
        "entry_price": None,
        "stop_price": None,
        "take_profit_price": None,
        "position_size_pct": None,
        "clarity_score": 60,
        "summary": "Re-entering Iridium",
        "extraction_notes": "",
    }
    result = _build_result(raw)
    assert result is not None
    assert result.ticker == "IRDM"


def test_build_result_drops_invalid_direction() -> None:
    raw = {
        "ticker": "AAPL",
        "direction": "SIDEWAYS",
        "asset_class": "EQUITY",
        "option_type": None,
        "strike": None,
        "expiry": None,
        "entry_price": None,
        "stop_price": None,
        "take_profit_price": None,
        "position_size_pct": None,
        "clarity_score": 40,
        "summary": "",
        "extraction_notes": "",
    }
    result = _build_result(raw)
    # ticker is present so result should not be None, but direction should be None
    assert result is not None
    assert result.direction is None


def test_build_result_returns_none_when_both_ticker_and_direction_missing() -> None:
    raw = {
        "ticker": None,
        "direction": None,
        "asset_class": None,
        "option_type": None,
        "strike": None,
        "expiry": None,
        "entry_price": None,
        "stop_price": None,
        "take_profit_price": None,
        "position_size_pct": None,
        "clarity_score": 10,
        "summary": "Not a trade signal",
        "extraction_notes": "",
    }
    result = _build_result(raw)
    assert result is None


# ── llm_parse ─────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_llm_parse_returns_none_when_no_api_key() -> None:
    settings = _settings(openai_api_key="")
    result = await llm_parse("BUY AAPL", settings)
    assert result is None


@pytest.mark.asyncio
async def test_llm_parse_happy_path_equity() -> None:
    payload = {
        "ticker": "AAPL",
        "direction": "LONG",
        "asset_class": "EQUITY",
        "option_type": None,
        "strike": None,
        "expiry": None,
        "entry_price": 175.50,
        "stop_price": 172.00,
        "take_profit_price": 181.00,
        "position_size_pct": None,
        "clarity_score": 90,
        "summary": "Buy AAPL at 175.50 stop 172 target 181",
        "extraction_notes": "All fields present",
    }
    mock_resp = _mock_response(payload)

    with patch(
        "agents.interpreter.llm_parser.AsyncOpenAI"
    ) as mock_client_cls:
        instance = AsyncMock()
        instance.chat.completions.create = AsyncMock(return_value=mock_resp)
        mock_client_cls.return_value = instance

        result = await llm_parse("BUY AAPL @ 175.50 stop 172 target 181", _settings())

    assert result is not None
    assert result.ticker == "AAPL"
    assert result.direction == "LONG"
    assert result.entry_price == 175.50
    assert result.clarity_score == 90


@pytest.mark.asyncio
async def test_llm_parse_happy_path_options() -> None:
    payload = {
        "ticker": "IRDM",
        "direction": "LONG",
        "asset_class": "OPTION",
        "option_type": "CALL",
        "strike": 22.5,
        "expiry": "2025-07-17",
        "entry_price": 3.35,
        "stop_price": None,
        "take_profit_price": None,
        "position_size_pct": 0.015,
        "clarity_score": 78,
        "summary": "Buy IRDM $22.5C July 17 at $3.35",
        "extraction_notes": "Option contract explicitly stated",
    }
    mock_resp = _mock_response(payload)

    with patch(
        "agents.interpreter.llm_parser.AsyncOpenAI"
    ) as mock_client_cls:
        instance = AsyncMock()
        instance.chat.completions.create = AsyncMock(return_value=mock_resp)
        mock_client_cls.return_value = instance

        raw = (
            "I have been trying to re-enter Iridium (ticker I-R-D-M)... "
            "I have been scaling into some $22.5C for July 17 at $3.35 avg"
        )
        result = await llm_parse(raw, _settings())

    assert result is not None
    assert result.ticker == "IRDM"
    assert result.option_type == "CALL"
    assert result.strike == 22.5
    assert result.expiry == date(2025, 7, 17)
    assert result.position_size_pct == 0.015


@pytest.mark.asyncio
async def test_llm_parse_handles_timeout() -> None:
    from openai import APITimeoutError

    with patch(
        "agents.interpreter.llm_parser.AsyncOpenAI"
    ) as mock_client_cls:
        instance = AsyncMock()
        instance.chat.completions.create = AsyncMock(
            side_effect=APITimeoutError(request=MagicMock())
        )
        mock_client_cls.return_value = instance

        result = await llm_parse("some text", _settings())

    assert result is None


@pytest.mark.asyncio
async def test_llm_parse_handles_api_error() -> None:
    from openai import APIError

    with patch(
        "agents.interpreter.llm_parser.AsyncOpenAI"
    ) as mock_client_cls:
        instance = AsyncMock()
        instance.chat.completions.create = AsyncMock(
            side_effect=APIError(
                message="rate limited",
                request=MagicMock(),
                body=None,
            )
        )
        mock_client_cls.return_value = instance

        result = await llm_parse("some text", _settings())

    assert result is None


@pytest.mark.asyncio
async def test_llm_parse_handles_invalid_json() -> None:
    choice = MagicMock()
    choice.message.content = "not json at all"
    resp = MagicMock()
    resp.choices = [choice]

    with patch(
        "agents.interpreter.llm_parser.AsyncOpenAI"
    ) as mock_client_cls:
        instance = AsyncMock()
        instance.chat.completions.create = AsyncMock(return_value=resp)
        mock_client_cls.return_value = instance

        result = await llm_parse("some text", _settings())

    assert result is None


@pytest.mark.asyncio
async def test_llm_parse_strips_markdown_code_fence() -> None:
    payload = {
        "ticker": "MSFT",
        "direction": "LONG",
        "asset_class": "EQUITY",
        "option_type": None,
        "strike": None,
        "expiry": None,
        "entry_price": 400.0,
        "stop_price": None,
        "take_profit_price": None,
        "position_size_pct": None,
        "clarity_score": 85,
        "summary": "Buy MSFT",
        "extraction_notes": "",
    }
    content_with_fence = f"```json\n{json.dumps(payload)}\n```"
    choice = MagicMock()
    choice.message.content = content_with_fence
    resp = MagicMock()
    resp.choices = [choice]

    with patch(
        "agents.interpreter.llm_parser.AsyncOpenAI"
    ) as mock_client_cls:
        instance = AsyncMock()
        instance.chat.completions.create = AsyncMock(return_value=resp)
        mock_client_cls.return_value = instance

        result = await llm_parse("BUY MSFT at 400", _settings())

    assert result is not None
    assert result.ticker == "MSFT"


@pytest.mark.asyncio
async def test_llm_parse_short_signal() -> None:
    payload = {
        "ticker": "SPY",
        "direction": "SHORT",
        "asset_class": "EQUITY",
        "option_type": None,
        "strike": None,
        "expiry": None,
        "entry_price": 520.0,
        "stop_price": 525.0,
        "take_profit_price": None,
        "position_size_pct": None,
        "clarity_score": 88,
        "summary": "Short SPY at 520 stop 525",
        "extraction_notes": "Clear directional signal",
    }
    mock_resp = _mock_response(payload)

    with patch(
        "agents.interpreter.llm_parser.AsyncOpenAI"
    ) as mock_client_cls:
        instance = AsyncMock()
        instance.chat.completions.create = AsyncMock(return_value=mock_resp)
        mock_client_cls.return_value = instance

        result = await llm_parse("Short SPY here at 520 stop 525", _settings())

    assert result is not None
    assert result.direction == "SHORT"
    assert result.ticker == "SPY"
