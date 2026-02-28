"""Unit tests for interpreter validator and confidence modules."""
from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING

import pytest

from agents.interpreter.confidence import assign_confidence
from agents.interpreter.validator import is_tradable, validate_ticker_format
from schemas.trade_intent import ConfidenceBucket

if TYPE_CHECKING:
    from agents.interpreter.agent import InterpreterAgent
    from schemas.signal_event import SignalEvent

# ── validate_ticker_format ────────────────────────────────────────────────────


class TestTickerFormat:
    def test_single_letter(self) -> None:
        assert validate_ticker_format("A") is True

    def test_two_letters(self) -> None:
        assert validate_ticker_format("GM") is True

    def test_three_letters(self) -> None:
        assert validate_ticker_format("SPY") is True

    def test_four_letters(self) -> None:
        assert validate_ticker_format("TSLA") is True

    def test_five_letters(self) -> None:
        assert validate_ticker_format("GOOGL") is True

    def test_six_letters_invalid(self) -> None:
        assert validate_ticker_format("TOOLNG") is False

    def test_lowercase_invalid(self) -> None:
        assert validate_ticker_format("aapl") is False

    def test_mixed_case_invalid(self) -> None:
        assert validate_ticker_format("Aapl") is False

    def test_digits_invalid(self) -> None:
        assert validate_ticker_format("AAPL1") is False

    def test_empty_invalid(self) -> None:
        assert validate_ticker_format("") is False

    def test_dollar_prefix_invalid(self) -> None:
        assert validate_ticker_format("$AAPL") is False


# ── is_tradable ───────────────────────────────────────────────────────────────


class TestIsTradable:
    def test_common_equity(self) -> None:
        for ticker in ("AAPL", "MSFT", "TSLA", "NVDA", "AMZN"):
            assert is_tradable(ticker) is True, f"{ticker} should be tradable"

    def test_common_etfs_tradable(self) -> None:
        # SPY, QQQ, IWM are ETFs — tradable directly
        for ticker in ("SPY", "QQQ", "IWM", "DIA", "GLD"):
            assert is_tradable(ticker) is True, f"{ticker} should be tradable"

    def test_index_symbols_not_tradable(self) -> None:
        # Cash-settled indices cannot be traded as equity orders
        for ticker in ("SPX", "NDX", "VIX", "RUT"):
            assert is_tradable(ticker) is False, f"{ticker} should not be tradable"

    def test_crypto_not_tradable(self) -> None:
        for ticker in ("BTC", "ETH", "SOL", "DOGE"):
            assert is_tradable(ticker) is False

    def test_currencies_not_tradable(self) -> None:
        for ticker in ("USD", "EUR", "GBP", "JPY"):
            assert is_tradable(ticker) is False

    def test_lowercase_not_tradable(self) -> None:
        assert is_tradable("aapl") is False

    def test_too_long_not_tradable(self) -> None:
        assert is_tradable("TOOLNG") is False


# ── assign_confidence ─────────────────────────────────────────────────────────


class TestAssignConfidence:
    E = Decimal("175.50")
    S = Decimal("172.00")
    T = Decimal("181.00")

    def test_entry_and_stop_is_high(self) -> None:
        c = assign_confidence(self.E, self.S, None, "AAPL", True)
        assert c == ConfidenceBucket.HIGH

    def test_entry_stop_target_is_high(self) -> None:
        c = assign_confidence(self.E, self.S, self.T, "AAPL", True)
        assert c == ConfidenceBucket.HIGH

    def test_entry_only_is_medium(self) -> None:
        c = assign_confidence(self.E, None, None, "AAPL", True)
        assert c == ConfidenceBucket.MEDIUM

    def test_entry_and_target_no_stop_is_medium(self) -> None:
        """Entry + target but no stop — can't size without stop."""
        c = assign_confidence(self.E, None, self.T, "AAPL", True)
        assert c == ConfidenceBucket.MEDIUM

    def test_no_prices_is_low(self) -> None:
        c = assign_confidence(None, None, None, "AAPL", True)
        assert c == ConfidenceBucket.LOW

    def test_stop_only_no_entry_is_low(self) -> None:
        c = assign_confidence(None, self.S, None, "AAPL", True)
        assert c == ConfidenceBucket.LOW

    def test_target_only_no_entry_is_low(self) -> None:
        c = assign_confidence(None, None, self.T, "AAPL", True)
        assert c == ConfidenceBucket.LOW

    def test_non_tradable_ticker_is_low_regardless_of_prices(self) -> None:
        c = assign_confidence(self.E, self.S, self.T, "BTC", False)
        assert c == ConfidenceBucket.LOW

    def test_non_tradable_overrides_high_prices(self) -> None:
        """Even with all three prices, non-tradable ticker → LOW."""
        c = assign_confidence(self.E, self.S, self.T, "SPX", False)
        assert c == ConfidenceBucket.LOW


# ── InterpreterAgent._interpret integration ───────────────────────────────────


class TestInterpreterInterpret:
    """Light integration: full pipeline from SignalEvent → TradeIntent."""

    def _make_event(self, text: str) -> SignalEvent:
        from schemas.signal_event import SignalEvent
        return SignalEvent(
            source_guild_id="1",
            source_channel_id="2",
            source_message_id="3",
            source_author_id="4",
            raw_text=text,
            profile="discord_equities",
        )

    def _make_agent(self) -> InterpreterAgent:
        from agents.interpreter.agent import InterpreterAgent
        from bus.queue import PipelineBus
        from config.settings import Settings
        settings = Settings.model_validate({"discord_bot_token": "x"})
        bus = PipelineBus()
        return InterpreterAgent(settings, bus)

    @pytest.mark.asyncio
    async def test_high_confidence_signal_produces_intent(self) -> None:
        agent: InterpreterAgent = self._make_agent()
        event: SignalEvent = self._make_event("BUY AAPL @ 175.50 stop 172.00 target 181.00")
        intent = await agent._interpret(event)
        assert intent is not None
        assert intent.ticker == "AAPL"
        assert intent.event_hash != ""

    @pytest.mark.asyncio
    async def test_unparseable_signal_returns_none(self) -> None:
        agent: InterpreterAgent = self._make_agent()
        event: SignalEvent = self._make_event("Just a random message with no signal")
        assert await agent._interpret(event) is None

    @pytest.mark.asyncio
    async def test_non_tradable_ticker_still_produces_intent(self) -> None:
        """Interpreter produces the intent; risk gate makes the rejection call."""
        from schemas.trade_intent import ConfidenceBucket
        agent: InterpreterAgent = self._make_agent()
        event: SignalEvent = self._make_event("BUY BTC @ 50000 stop 48000")
        intent = await agent._interpret(event)
        assert intent is not None
        assert intent.confidence == ConfidenceBucket.LOW

    @pytest.mark.asyncio
    async def test_correlation_id_preserved(self) -> None:
        agent: InterpreterAgent = self._make_agent()
        event: SignalEvent = self._make_event("BUY AAPL @ 175 stop 172")
        intent = await agent._interpret(event)
        assert intent is not None
        assert intent.correlation_id == event.correlation_id

    @pytest.mark.asyncio
    async def test_source_signal_id_set(self) -> None:
        agent: InterpreterAgent = self._make_agent()
        event: SignalEvent = self._make_event("BUY AAPL @ 175 stop 172")
        intent = await agent._interpret(event)
        assert intent is not None
        assert intent.source_signal_id == event.event_id
