"""Unit tests for agents/risk_officer/portfolio_adapter.py retry logic."""
from __future__ import annotations

import asyncio
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from agents.risk_officer.portfolio_adapter import (
    MAX_CONNECT_ATTEMPTS,
    MAX_FETCH_ATTEMPTS,
    PortfolioAdapter,
)
from config.settings import Settings
from schemas.portfolio_snapshot import PortfolioSnapshot


def _make_settings(**overrides: object) -> Settings:
    defaults = {
        "discord_bot_token": "x",
        "ibkr_host": "127.0.0.1",
        "ibkr_port": 7497,
        "ibkr_client_id": 1,
        "ibkr_account_id": "DU123",
        "sleeve_value": Decimal("100000"),
    }
    defaults.update(overrides)
    return Settings.model_validate(defaults)


@pytest.mark.asyncio
async def test_connect_succeeds_on_first_attempt() -> None:
    """Connect succeeds immediately when IB responds."""
    settings = _make_settings()

    def make_ib() -> MagicMock:
        mock = MagicMock()
        mock.isConnected.return_value = True
        mock.accountValues.return_value = []
        mock.portfolio.return_value = []
        mock.connectAsync = AsyncMock(return_value=None)
        return mock

    with patch("agents.risk_officer.portfolio_adapter.ib") as mock_ib_module:
        mock_ib_module.IB.side_effect = make_ib

        adapter = PortfolioAdapter(settings)
        snapshot = await adapter.get_snapshot(uuid4())

        assert isinstance(snapshot, PortfolioSnapshot)
        assert snapshot.account_id == "DU123"
        await adapter.close()


@pytest.mark.asyncio
async def test_connect_retries_then_succeeds() -> None:
    """Connect fails twice then succeeds on third attempt."""
    settings = _make_settings()
    call_count = 0

    def make_ib() -> MagicMock:
        nonlocal call_count
        mock = MagicMock()
        mock.isConnected.return_value = True
        mock.accountValues.return_value = []
        mock.portfolio.return_value = []

        async def connect_maybe_fail(*args: object, **kwargs: object) -> None:
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise ConnectionError("simulated")

        mock.connectAsync = AsyncMock(side_effect=connect_maybe_fail)
        return mock

    with patch("agents.risk_officer.portfolio_adapter.ib") as mock_ib_module:
        mock_ib_module.IB.side_effect = make_ib

        adapter = PortfolioAdapter(settings)
        snapshot = await adapter.get_snapshot(uuid4())

        assert isinstance(snapshot, PortfolioSnapshot)
        assert call_count == 3
        await adapter.close()


@pytest.mark.asyncio
async def test_connect_raises_after_all_retries() -> None:
    """Connect raises ConnectionError after MAX_CONNECT_ATTEMPTS failures."""
    settings = _make_settings()

    def make_ib() -> MagicMock:
        mock = MagicMock()

        async def connect_fail(*args: object, **kwargs: object) -> None:
            raise ConnectionError("persistent failure")

        mock.connectAsync = AsyncMock(side_effect=connect_fail)
        return mock

    with patch("agents.risk_officer.portfolio_adapter.ib") as mock_ib_module:
        mock_ib_module.IB.side_effect = make_ib

        adapter = PortfolioAdapter(settings)

        with pytest.raises(ConnectionError) as exc_info:
            await adapter.get_snapshot(uuid4())

        assert "failed to connect" in str(exc_info.value).lower()
        assert str(MAX_CONNECT_ATTEMPTS) in str(exc_info.value)


@pytest.mark.asyncio
async def test_fetch_retries_then_succeeds() -> None:
    """Fetch fails once (e.g. connection drop) then succeeds on retry."""
    settings = _make_settings()
    fetch_count = 0

    def make_ib() -> MagicMock:
        nonlocal fetch_count
        mock = MagicMock()
        mock.isConnected.return_value = True

        def account_values(acct: str) -> list:
            nonlocal fetch_count
            fetch_count += 1
            if fetch_count == 1:
                raise RuntimeError("simulated fetch failure")
            return []

        mock.accountValues = account_values
        mock.portfolio = lambda acct: []
        mock.connectAsync = AsyncMock(return_value=None)
        return mock

    with patch("agents.risk_officer.portfolio_adapter.ib") as mock_ib_module:
        mock_ib_module.IB.side_effect = make_ib

        adapter = PortfolioAdapter(settings)
        snapshot = await adapter.get_snapshot(uuid4())

        assert isinstance(snapshot, PortfolioSnapshot)
        assert fetch_count == 2
        await adapter.close()


@pytest.mark.asyncio
async def test_fetch_raises_after_all_retries() -> None:
    """Fetch raises RuntimeError after MAX_FETCH_ATTEMPTS failures."""
    settings = _make_settings()

    def make_ib() -> MagicMock:
        mock = MagicMock()
        mock.isConnected.return_value = True
        mock.accountValues.side_effect = RuntimeError("persistent fetch failure")
        mock.portfolio.return_value = []
        mock.connectAsync = AsyncMock(return_value=None)
        return mock

    with patch("agents.risk_officer.portfolio_adapter.ib") as mock_ib_module:
        mock_ib_module.IB.side_effect = make_ib

        adapter = PortfolioAdapter(settings)

        with pytest.raises(RuntimeError) as exc_info:
            await adapter.get_snapshot(uuid4())

        assert "failed to fetch" in str(exc_info.value).lower()
        assert str(MAX_FETCH_ATTEMPTS) in str(exc_info.value)
