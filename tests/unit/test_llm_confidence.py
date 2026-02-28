"""
Unit tests for agents/interpreter/llm_confidence.py.

These are pure-logic tests with no external dependencies.
"""
from __future__ import annotations

from datetime import date

from agents.interpreter.llm_confidence import (
    _clarity_tier,
    _completeness_tier,
    assign_llm_confidence,
)
from agents.interpreter.llm_parser import LLMParseResult
from schemas.trade_intent import ConfidenceBucket

# ── _clarity_tier ─────────────────────────────────────────────────────────────


def test_clarity_tier_high_at_85() -> None:
    assert _clarity_tier(85, 50) == ConfidenceBucket.HIGH


def test_clarity_tier_high_at_100() -> None:
    assert _clarity_tier(100, 50) == ConfidenceBucket.HIGH


def test_clarity_tier_medium_at_60() -> None:
    assert _clarity_tier(60, 50) == ConfidenceBucket.MEDIUM


def test_clarity_tier_medium_at_84() -> None:
    assert _clarity_tier(84, 50) == ConfidenceBucket.MEDIUM


def test_clarity_tier_low_at_50() -> None:
    assert _clarity_tier(50, 50) == ConfidenceBucket.LOW


def test_clarity_tier_low_at_59() -> None:
    assert _clarity_tier(59, 50) == ConfidenceBucket.LOW


def test_clarity_tier_none_below_min() -> None:
    assert _clarity_tier(49, 50) is None


def test_clarity_tier_none_at_zero() -> None:
    assert _clarity_tier(0, 50) is None


def test_clarity_tier_custom_min_boundary() -> None:
    # 70 >= 60, so it resolves to MEDIUM regardless of min_clarity=70
    assert _clarity_tier(70, 70) == ConfidenceBucket.MEDIUM
    assert _clarity_tier(69, 70) is None


# ── _completeness_tier ────────────────────────────────────────────────────────


def _equity_result(**kwargs: object) -> LLMParseResult:
    defaults: dict[str, object] = {
        "ticker": "AAPL",
        "direction": "LONG",
        "asset_class": "EQUITY",
        "clarity_score": 80,
    }
    defaults.update(kwargs)
    return LLMParseResult(**defaults)  # type: ignore[arg-type]


def _option_result(**kwargs: object) -> LLMParseResult:
    defaults: dict[str, object] = {
        "ticker": "IRDM",
        "direction": "LONG",
        "asset_class": "OPTION",
        "option_type": "CALL",
        "strike": 22.5,
        "expiry": date(2025, 7, 17),
        "entry_price": 3.35,
        "clarity_score": 80,
    }
    defaults.update(kwargs)
    return LLMParseResult(**defaults)  # type: ignore[arg-type]


def test_completeness_equity_high_with_entry_price() -> None:
    r = _equity_result(entry_price=175.0)
    assert _completeness_tier(r) == ConfidenceBucket.HIGH


def test_completeness_equity_medium_without_entry_price() -> None:
    r = _equity_result()
    assert _completeness_tier(r) == ConfidenceBucket.MEDIUM


def test_completeness_equity_none_no_ticker() -> None:
    r = _equity_result(ticker=None)
    assert _completeness_tier(r) is None


def test_completeness_equity_none_no_direction() -> None:
    r = _equity_result(direction=None)
    assert _completeness_tier(r) is None


def test_completeness_option_high_full_contract_with_entry() -> None:
    r = _option_result()
    assert _completeness_tier(r) == ConfidenceBucket.HIGH


def test_completeness_option_medium_entry_no_full_contract() -> None:
    r = _option_result(strike=None, entry_price=3.35)
    assert _completeness_tier(r) == ConfidenceBucket.MEDIUM


def test_completeness_option_low_no_entry_price() -> None:
    r = _option_result(entry_price=None)
    assert _completeness_tier(r) == ConfidenceBucket.LOW


def test_completeness_option_none_no_ticker() -> None:
    r = _option_result(ticker=None)
    assert _completeness_tier(r) is None


# ── assign_llm_confidence ─────────────────────────────────────────────────────


def test_assign_high_clarity_full_equity() -> None:
    r = LLMParseResult(
        ticker="AAPL",
        direction="LONG",
        asset_class="EQUITY",
        entry_price=175.0,
        clarity_score=90,
    )
    assert assign_llm_confidence(r, min_clarity=50) == ConfidenceBucket.HIGH


def test_assign_medium_clamp_high_clarity_no_entry() -> None:
    """Clarity is HIGH tier but completeness is MEDIUM → result is MEDIUM."""
    r = LLMParseResult(
        ticker="AAPL",
        direction="LONG",
        asset_class="EQUITY",
        clarity_score=90,
    )
    assert assign_llm_confidence(r, min_clarity=50) == ConfidenceBucket.MEDIUM


def test_assign_low_from_option_no_entry() -> None:
    """Full option contract details but no entry price → LOW."""
    r = LLMParseResult(
        ticker="IRDM",
        direction="LONG",
        asset_class="OPTION",
        option_type="CALL",
        strike=22.5,
        expiry=date(2025, 7, 17),
        entry_price=None,
        clarity_score=75,
    )
    assert assign_llm_confidence(r, min_clarity=50) == ConfidenceBucket.LOW


def test_assign_none_when_clarity_below_minimum() -> None:
    r = LLMParseResult(
        ticker="AAPL",
        direction="LONG",
        asset_class="EQUITY",
        entry_price=175.0,
        clarity_score=40,
    )
    assert assign_llm_confidence(r, min_clarity=50) is None


def test_assign_none_when_ticker_missing() -> None:
    r = LLMParseResult(
        ticker=None,
        direction="LONG",
        asset_class="EQUITY",
        clarity_score=80,
    )
    assert assign_llm_confidence(r, min_clarity=50) is None


def test_assign_none_when_direction_missing() -> None:
    r = LLMParseResult(
        ticker="AAPL",
        direction=None,
        asset_class="EQUITY",
        clarity_score=80,
    )
    assert assign_llm_confidence(r, min_clarity=50) is None


def test_assign_min_boundary_exactly_at_minimum_clarity() -> None:
    """Score exactly at min_clarity passes the drop gate, but 50 < 60 → LOW tier."""
    r = LLMParseResult(
        ticker="AAPL",
        direction="LONG",
        asset_class="EQUITY",
        clarity_score=50,
    )
    assert assign_llm_confidence(r, min_clarity=50) == ConfidenceBucket.LOW


def test_assign_short_direction() -> None:
    r = LLMParseResult(
        ticker="SPY",
        direction="SHORT",
        asset_class="EQUITY",
        entry_price=520.0,
        clarity_score=88,
    )
    assert assign_llm_confidence(r, min_clarity=50) == ConfidenceBucket.HIGH


def test_assign_clamps_high_clarity_low_completeness_option() -> None:
    """
    Clarity = HIGH (score 90) but option has no entry price → LOW completeness.
    Final should be LOW.
    """
    r = LLMParseResult(
        ticker="IRDM",
        direction="LONG",
        asset_class="OPTION",
        option_type="CALL",
        strike=22.5,
        expiry=date(2025, 7, 17),
        entry_price=None,
        clarity_score=90,
    )
    assert assign_llm_confidence(r, min_clarity=50) == ConfidenceBucket.LOW
