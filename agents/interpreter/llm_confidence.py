"""
Rule-based confidence bucketing for LLM-parsed trade intents.

Two inputs are combined to produce the final ConfidenceBucket:

    Clarity tier   — derived from LLMParseResult.clarity_score (self-reported by LLM)
    Completeness   — derived from which fields the LLM successfully extracted

Final confidence = min(clarity_tier, completeness_tier).
This is always the more conservative of the two, preventing an over-confident
LLM score from overriding missing critical fields.

Returns None when the signal should be dropped:
    - clarity_score below the configured minimum (default 50)
    - ticker is None
    - direction is None
    - completeness tier resolves to NONE
"""
from __future__ import annotations

from schemas.trade_intent import ConfidenceBucket

from .llm_parser import LLMParseResult

# ── Tier ordering for min() comparison ───────────────────────────────────────

_TIER_ORDER: dict[ConfidenceBucket, int] = {
    ConfidenceBucket.LOW: 0,
    ConfidenceBucket.MEDIUM: 1,
    ConfidenceBucket.HIGH: 2,
}


def _min_tier(a: ConfidenceBucket, b: ConfidenceBucket) -> ConfidenceBucket:
    return a if _TIER_ORDER[a] <= _TIER_ORDER[b] else b


# ── Clarity tier ─────────────────────────────────────────────────────────────


def _clarity_tier(score: int, min_clarity: int) -> ConfidenceBucket | None:
    """
    Map a 0-100 clarity score to a confidence bucket.
    Returns None (drop signal) if score is below min_clarity.

    Boundaries:
        score ≥ 85          → HIGH
        60 ≤ score < 85     → MEDIUM
        min_clarity ≤ score < 60  → LOW
        score < min_clarity → None (drop)
    """
    if score < min_clarity:
        return None
    if score >= 85:
        return ConfidenceBucket.HIGH
    if score >= 60:
        return ConfidenceBucket.MEDIUM
    return ConfidenceBucket.LOW


# ── Completeness tier ─────────────────────────────────────────────────────────


def _completeness_tier(result: LLMParseResult) -> ConfidenceBucket | None:
    """
    Assess how complete the extracted fields are and return a bucket.
    Returns None when required minimum fields are absent.

    EQUITY (or unknown asset class):
        HIGH   → ticker + direction + entry_price
        MEDIUM → ticker + direction (no entry price)
        NONE   → ticker missing OR direction missing

    OPTION:
        HIGH   → ticker + direction + option_type + strike + expiry + entry_price
        MEDIUM → ticker + direction + entry_price (some option details missing)
        LOW    → ticker + direction (no entry price, regardless of option details)
        NONE   → ticker missing OR direction missing
    """
    if not result.ticker or not result.direction:
        return None

    is_option = result.asset_class == "OPTION"

    if is_option:
        has_full_contract = (
            result.option_type is not None
            and result.strike is not None
            and result.expiry is not None
        )
        has_entry = result.entry_price is not None

        if has_full_contract and has_entry:
            return ConfidenceBucket.HIGH
        if has_entry:
            return ConfidenceBucket.MEDIUM
        return ConfidenceBucket.LOW
    else:
        # EQUITY or unspecified
        if result.entry_price is not None:
            return ConfidenceBucket.HIGH
        return ConfidenceBucket.MEDIUM


# ── Public API ────────────────────────────────────────────────────────────────


def assign_llm_confidence(
    result: LLMParseResult,
    min_clarity: int = 50,
) -> ConfidenceBucket | None:
    """
    Return the final confidence bucket for an LLM-parsed result, or None
    if the signal should be dropped.

    None is returned when:
        - clarity_score < min_clarity
        - ticker is None
        - direction is None

    The final bucket is min(clarity_tier, completeness_tier), ensuring
    a high clarity score cannot compensate for missing critical fields.
    """
    clarity = _clarity_tier(result.clarity_score, min_clarity)
    if clarity is None:
        return None

    completeness = _completeness_tier(result)
    if completeness is None:
        return None

    return _min_tier(clarity, completeness)
