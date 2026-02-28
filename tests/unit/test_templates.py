"""
Unit tests for the deterministic signal text parser (templates.py).

Every test uses a plain string input — no mocks, no I/O.
All assertions are against ParseResult fields or None.
"""
from __future__ import annotations

from decimal import Decimal

from agents.interpreter.templates import ParseResult, try_parse
from schemas.trade_intent import AssetClass, Direction


def _parse(text: str) -> ParseResult:
    """Helper: parse and assert non-None in one call."""
    result = try_parse(text)
    assert result is not None, f"Expected a parse result for: {text!r}"
    return result


# ── Direction detection ───────────────────────────────────────────────────────


class TestDirectionDetection:
    def test_buy_keyword(self) -> None:
        assert _parse("BUY AAPL @ 175").direction == Direction.LONG

    def test_long_keyword(self) -> None:
        assert _parse("LONG AAPL @ 175").direction == Direction.LONG

    def test_buying_keyword(self) -> None:
        assert _parse("Buying AAPL @ 175").direction == Direction.LONG

    def test_bull_keyword(self) -> None:
        assert _parse("BULL AAPL @ 175").direction == Direction.LONG

    def test_sell_keyword(self) -> None:
        assert _parse("SELL TSLA @ 260").direction == Direction.SHORT

    def test_short_keyword(self) -> None:
        assert _parse("SHORT QQQ @ 360").direction == Direction.SHORT

    def test_selling_keyword(self) -> None:
        assert _parse("Selling MSFT @ 380").direction == Direction.SHORT

    def test_bear_keyword(self) -> None:
        assert _parse("BEAR NVDA @ 800").direction == Direction.SHORT

    def test_case_insensitive_direction(self) -> None:
        assert _parse("buy AAPL @ 175").direction == Direction.LONG
        assert _parse("Buy AAPL @ 175").direction == Direction.LONG

    def test_no_direction_returns_none(self) -> None:
        assert try_parse("AAPL 175.50 172.00 181.00") is None

    def test_empty_string_returns_none(self) -> None:
        assert try_parse("") is None

    def test_whitespace_only_returns_none(self) -> None:
        assert try_parse("   ") is None


# ── Ticker extraction ─────────────────────────────────────────────────────────


class TestTickerExtraction:
    def test_simple_ticker(self) -> None:
        assert _parse("BUY AAPL @ 175").ticker == "AAPL"

    def test_dollar_prefix_stripped(self) -> None:
        assert _parse("BUY $AAPL @ 175").ticker == "AAPL"

    def test_three_letter_ticker(self) -> None:
        assert _parse("BUY SPY @ 450").ticker == "SPY"

    def test_four_letter_ticker(self) -> None:
        assert _parse("BUY TSLA @ 260").ticker == "TSLA"

    def test_five_letter_ticker(self) -> None:
        assert _parse("BUY GOOGL @ 175").ticker == "GOOGL"

    def test_ticker_before_direction(self) -> None:
        """e.g. '$AAPL buy @ 175' — ticker precedes direction word."""
        result = _parse("$AAPL buy @ 175 stop 172")
        assert result.ticker == "AAPL"
        assert result.direction == Direction.LONG

    def test_no_ticker_returns_none(self) -> None:
        assert try_parse("buy @ 175 stop 172") is None

    def test_direction_word_not_used_as_ticker(self) -> None:
        """'BUY' itself must not be extracted as the ticker."""
        result = _parse("BUY AAPL @ 175")
        assert result.ticker != "BUY"

    def test_stop_keyword_not_used_as_ticker(self) -> None:
        result = _parse("BUY AAPL @ 175 stop 172")
        assert result.ticker == "AAPL"

    def test_asset_class_always_equity(self) -> None:
        assert _parse("BUY AAPL @ 175").asset_class == AssetClass.EQUITY


# ── Entry price extraction ────────────────────────────────────────────────────


class TestEntryPrice:
    def test_at_symbol_entry(self) -> None:
        assert _parse("BUY AAPL @ 175.50").entry_price == Decimal("175.50")

    def test_at_no_space(self) -> None:
        assert _parse("BUY AAPL @175.50").entry_price == Decimal("175.50")

    def test_entry_keyword(self) -> None:
        assert _parse("BUY AAPL entry 175.50 stop 172").entry_price == Decimal("175.50")

    def test_entering_keyword(self) -> None:
        assert _parse("BUY AAPL entering 175.50 stop 172").entry_price == Decimal("175.50")

    def test_at_keyword(self) -> None:
        assert _parse("BUY AAPL at 175.50 stop 172").entry_price == Decimal("175.50")

    def test_bare_price_after_ticker(self) -> None:
        """'BUY AAPL 175.50 stop 172' — no keyword before price."""
        assert _parse("BUY AAPL 175.50 stop 172").entry_price == Decimal("175.50")

    def test_integer_price(self) -> None:
        assert _parse("BUY SPY @ 450 stop 445").entry_price == Decimal("450")

    def test_no_entry_price(self) -> None:
        assert _parse("BUY AAPL").entry_price is None

    def test_price_precision_preserved(self) -> None:
        assert _parse("BUY AAPL @ 175.1234 stop 172").entry_price == Decimal("175.1234")


# ── Stop price extraction ─────────────────────────────────────────────────────


class TestStopPrice:
    def test_stop_keyword(self) -> None:
        assert _parse("BUY AAPL @ 175 stop 172").stop_price == Decimal("172")

    def test_sl_keyword(self) -> None:
        assert _parse("LONG AAPL @ 175 sl 172").stop_price == Decimal("172")

    def test_sl_slash_keyword(self) -> None:
        assert _parse("BUY AAPL @ 175 s/l 172").stop_price == Decimal("172")

    def test_stop_loss_hyphen(self) -> None:
        assert _parse("BUY AAPL @ 175 stop-loss 172").stop_price == Decimal("172")

    def test_stop_loss_space(self) -> None:
        assert _parse("BUY AAPL @ 175 stop loss 172").stop_price == Decimal("172")

    def test_no_stop_price(self) -> None:
        assert _parse("BUY AAPL @ 175").stop_price is None

    def test_decimal_stop(self) -> None:
        assert _parse("BUY AAPL @ 175.50 stop 172.00").stop_price == Decimal("172.00")


# ── Take-profit price extraction ──────────────────────────────────────────────


class TestTakeProfitPrice:
    def test_target_keyword(self) -> None:
        assert _parse("BUY AAPL @ 175 stop 172 target 181").take_profit_price == Decimal("181")

    def test_tp_keyword(self) -> None:
        assert _parse("LONG AAPL @ 175 sl 172 tp 181").take_profit_price == Decimal("181")

    def test_tp_slash_keyword(self) -> None:
        assert _parse("BUY AAPL @ 175 stop 172 t/p 181").take_profit_price == Decimal("181")

    def test_take_profit_hyphen(self) -> None:
        assert _parse("BUY AAPL @ 175 stop 172 take-profit 181").take_profit_price == Decimal("181")

    def test_take_profit_space(self) -> None:
        assert _parse("BUY AAPL @ 175 stop 172 take profit 181").take_profit_price == Decimal("181")

    def test_tgt_keyword(self) -> None:
        assert _parse("BUY AAPL @ 175 stop 172 tgt 181").take_profit_price == Decimal("181")

    def test_pt_keyword(self) -> None:
        assert _parse("BUY AAPL @ 175 stop 172 pt 181").take_profit_price == Decimal("181")

    def test_no_take_profit(self) -> None:
        assert _parse("BUY AAPL @ 175 stop 172").take_profit_price is None


# ── Full signal strings ───────────────────────────────────────────────────────


class TestFullSignals:
    def test_full_long_at_stop_target(self) -> None:
        r = _parse("BUY AAPL @ 175.50 stop 172.00 target 181.00")
        assert r.direction == Direction.LONG
        assert r.ticker == "AAPL"
        assert r.entry_price == Decimal("175.50")
        assert r.stop_price == Decimal("172.00")
        assert r.take_profit_price == Decimal("181.00")

    def test_full_long_sl_tp(self) -> None:
        r = _parse("LONG SPY @ 450 sl 445 tp 460")
        assert r.direction == Direction.LONG
        assert r.ticker == "SPY"
        assert r.entry_price == Decimal("450")
        assert r.stop_price == Decimal("445")
        assert r.take_profit_price == Decimal("460")

    def test_full_short_at_stop_target(self) -> None:
        r = _parse("SELL TSLA @ 260.00 stop 265.00 target 250.00")
        assert r.direction == Direction.SHORT
        assert r.ticker == "TSLA"
        assert r.entry_price == Decimal("260.00")
        assert r.stop_price == Decimal("265.00")
        assert r.take_profit_price == Decimal("250.00")

    def test_full_short_sl_tp(self) -> None:
        r = _parse("SHORT QQQ @ 360 sl 365 tp 350")
        assert r.direction == Direction.SHORT
        assert r.ticker == "QQQ"
        assert r.entry_price == Decimal("360")
        assert r.stop_price == Decimal("365")
        assert r.take_profit_price == Decimal("350")

    def test_entry_keyword_style(self) -> None:
        r = _parse("BUY AAPL entry 175.50 stop 172.00 target 181.00")
        assert r.entry_price == Decimal("175.50")
        assert r.stop_price == Decimal("172.00")

    def test_market_long_no_prices(self) -> None:
        r = _parse("BUY AAPL")
        assert r.ticker == "AAPL"
        assert r.direction == Direction.LONG
        assert r.entry_price is None
        assert r.stop_price is None
        assert r.take_profit_price is None

    def test_market_short_no_prices(self) -> None:
        r = _parse("SELL MSFT")
        assert r.ticker == "MSFT"
        assert r.direction == Direction.SHORT
        assert r.entry_price is None

    def test_mixed_case_keywords(self) -> None:
        r = _parse("Buy AAPL @ 175 Stop 172 Target 181")
        assert r.entry_price == Decimal("175")
        assert r.stop_price == Decimal("172")
        assert r.take_profit_price == Decimal("181")

    def test_dollar_prefix_full_signal(self) -> None:
        r = _parse("BUY $AAPL @ 175.50 stop 172.00 target 181.00")
        assert r.ticker == "AAPL"
        assert r.entry_price == Decimal("175.50")

    def test_extra_text_ignored(self) -> None:
        r = _parse("🚀 ALERT: BUY AAPL @ 175.50 stop 172.00 target 181.00 🎯")
        assert r.ticker == "AAPL"
        assert r.entry_price == Decimal("175.50")


# ── Template name ─────────────────────────────────────────────────────────────


class TestTemplateName:
    def test_market_long(self) -> None:
        assert _parse("BUY AAPL").template_name == "long_market"

    def test_market_short(self) -> None:
        assert _parse("SELL TSLA").template_name == "short_market"

    def test_entry_only(self) -> None:
        assert _parse("BUY AAPL @ 175").template_name == "long_entry"

    def test_entry_stop(self) -> None:
        assert _parse("BUY AAPL @ 175 stop 172").template_name == "long_entry_stop"

    def test_entry_stop_target(self) -> None:
        name = _parse("BUY AAPL @ 175 stop 172 target 181").template_name
        assert name == "long_entry_stop_target"

    def test_short_entry_stop_target(self) -> None:
        name = _parse("SHORT QQQ @ 360 sl 365 tp 350").template_name
        assert name == "short_entry_stop_target"


# ── Determinism ───────────────────────────────────────────────────────────────


class TestDeterminism:
    def test_same_input_same_output(self) -> None:
        """Parsing must be purely deterministic — no randomness."""
        text = "BUY AAPL @ 175.50 stop 172.00 target 181.00"
        results = [try_parse(text) for _ in range(10)]
        assert all(r == results[0] for r in results)

    def test_different_inputs_different_outputs(self) -> None:
        r1 = _parse("BUY AAPL @ 175 stop 172 target 181")
        r2 = _parse("SELL TSLA @ 260 stop 265 target 250")
        assert r1.ticker != r2.ticker
        assert r1.direction != r2.direction
