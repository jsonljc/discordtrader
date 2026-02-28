"""
Unit tests for agents/ibkr_executor/smart_options_selector.py.

All IBKR calls are mocked — no live connection required.
"""
from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import pytest

from agents.ibkr_executor.smart_options_selector import (
    SelectedOption,
    _closest_strikes,
    _min_expiry_date,
    _parse_ibkr_expiry,
    select_best_call,
)

# ── Helpers ───────────────────────────────────────────────────────────────────


def _future_expiry(months_ahead: int) -> str:
    today = date.today()
    month = today.month + months_ahead
    year = today.year + (month - 1) // 12
    month = ((month - 1) % 12) + 1
    exp = today.replace(year=year, month=month)
    return exp.strftime("%Y%m%d")


def _tick(bid: float = 2.50, ask: float = 2.55, last: float = 2.52) -> MagicMock:
    """Build a mock IBKR Ticker object."""
    t = MagicMock()
    t.bid = bid
    t.ask = ask
    t.last = last
    return t


def _fake_chain(
    expirations: list[str] | None = None,
    strikes: list[float] | None = None,
    exchange: str = "SMART",
) -> MagicMock:
    chain = MagicMock()
    chain.exchange = exchange
    chain.expirations = expirations or [
        _future_expiry(6),
        _future_expiry(9),
        _future_expiry(12),
    ]
    chain.strikes = strikes or [20.0, 22.5, 25.0, 27.5, 30.0]
    return chain


def _fake_ib(chain: MagicMock | None = None, tick: MagicMock | None = None) -> AsyncMock:
    ib = AsyncMock()
    ib.reqSecDefOptParamsAsync = AsyncMock(return_value=[chain or _fake_chain()])
    ib.qualifyContractsAsync = AsyncMock(return_value=[MagicMock()])
    ib.reqTickersAsync = AsyncMock(return_value=[tick or _tick()])
    return ib


# ── _parse_ibkr_expiry ────────────────────────────────────────────────────────


def test_parse_ibkr_expiry_valid() -> None:
    assert _parse_ibkr_expiry("20250717") == date(2025, 7, 17)


def test_parse_ibkr_expiry_invalid() -> None:
    assert _parse_ibkr_expiry("bad") is None


def test_parse_ibkr_expiry_empty() -> None:
    assert _parse_ibkr_expiry("") is None


# ── _min_expiry_date ──────────────────────────────────────────────────────────


def test_min_expiry_6_months_ahead() -> None:
    today = date.today()
    result = _min_expiry_date(6)
    assert result > today
    # Should be roughly 6 months ahead
    delta = (result - today).days
    assert 150 <= delta <= 200


# ── _closest_strikes ─────────────────────────────────────────────────────────


def test_closest_strikes_returns_n_results() -> None:
    strikes = [20.0, 22.5, 25.0, 27.5, 30.0]
    result = _closest_strikes(strikes, target=22.0, n=3)
    assert len(result) <= 3


def test_closest_strikes_prefers_at_or_above_target() -> None:
    strikes = [20.0, 22.5, 25.0, 27.5, 30.0]
    result = _closest_strikes(strikes, target=22.0)
    # 22.5 is just above target — should appear early
    assert result[0] == 22.5


def test_closest_strikes_empty_returns_empty() -> None:
    assert _closest_strikes([], target=100.0) == []


def test_closest_strikes_single_strike() -> None:
    result = _closest_strikes([50.0], target=48.0)
    assert result == [50.0]


# ── select_best_call — happy path ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_select_best_call_returns_selected_option() -> None:
    ib = _fake_ib()
    result = await select_best_call(
        ib_client=ib,
        ticker="AAPL",
        spot_price=Decimal("175.00"),
        stock_con_id=12345,
    )
    assert result is not None
    assert isinstance(result, SelectedOption)
    assert result.ticker == "AAPL"
    assert result.bid > 0


@pytest.mark.asyncio
async def test_select_best_call_strike_near_spot() -> None:
    ib = _fake_ib()
    result = await select_best_call(
        ib_client=ib,
        ticker="AAPL",
        spot_price=Decimal("22.00"),    # spot $22 → target strike ≈ 22.44
        stock_con_id=12345,
    )
    assert result is not None
    # Should have picked 22.5 (nearest at/above 22 × 1.02 = 22.44)
    assert result.strike == Decimal("22.5")


@pytest.mark.asyncio
async def test_select_best_call_ask_used_for_pricing() -> None:
    ib = _fake_ib(tick=_tick(bid=3.00, ask=3.10, last=3.05))
    result = await select_best_call(
        ib_client=ib,
        ticker="IRDM",
        spot_price=Decimal("22.00"),
        stock_con_id=99999,
    )
    assert result is not None
    assert result.ask == Decimal("3.10")
    assert result.bid == Decimal("3.00")


# ── select_best_call — edge cases ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_select_best_call_no_chains_returns_none() -> None:
    ib = AsyncMock()
    ib.reqSecDefOptParamsAsync = AsyncMock(return_value=[])
    result = await select_best_call(
        ib_client=ib, ticker="AAPL",
        spot_price=Decimal("175.00"), stock_con_id=123,
    )
    assert result is None


@pytest.mark.asyncio
async def test_select_best_call_no_qualifying_expiries_returns_none() -> None:
    # All expirations are in the past (< 6 months)
    past_expiry = (date.today() - timedelta(days=30)).strftime("%Y%m%d")
    chain = _fake_chain(expirations=[past_expiry])
    ib = _fake_ib(chain=chain)
    result = await select_best_call(
        ib_client=ib, ticker="AAPL",
        spot_price=Decimal("175.00"), stock_con_id=123,
    )
    assert result is None


@pytest.mark.asyncio
async def test_select_best_call_illiquid_returns_none() -> None:
    """All strikes have bid=0 (illiquid)."""
    illiquid_tick = _tick(bid=0, ask=0, last=0)
    ib = _fake_ib(tick=illiquid_tick)
    result = await select_best_call(
        ib_client=ib, ticker="AAPL",
        spot_price=Decimal("175.00"), stock_con_id=123,
    )
    assert result is None


@pytest.mark.asyncio
async def test_select_best_call_qualify_fails_returns_none() -> None:
    """qualifyContractsAsync returns empty (contract not found)."""
    ib = AsyncMock()
    ib.reqSecDefOptParamsAsync = AsyncMock(return_value=[_fake_chain()])
    ib.qualifyContractsAsync = AsyncMock(return_value=[])
    result = await select_best_call(
        ib_client=ib, ticker="AAPL",
        spot_price=Decimal("175.00"), stock_con_id=123,
    )
    assert result is None


@pytest.mark.asyncio
async def test_select_best_call_api_error_returns_none() -> None:
    """Unexpected IBKR error — should return None, never raise."""
    ib = AsyncMock()
    ib.reqSecDefOptParamsAsync = AsyncMock(side_effect=RuntimeError("connection lost"))
    result = await select_best_call(
        ib_client=ib, ticker="AAPL",
        spot_price=Decimal("175.00"), stock_con_id=123,
    )
    assert result is None


@pytest.mark.asyncio
async def test_select_best_call_expiry_at_least_6_months() -> None:
    """Verify the nearest qualifying expiry is ≥ 6 months out."""
    ib = _fake_ib()
    result = await select_best_call(
        ib_client=ib, ticker="AAPL",
        spot_price=Decimal("175.00"), stock_con_id=123,
    )
    assert result is not None
    min_exp = _min_expiry_date(6)
    assert result.expiry >= min_exp
