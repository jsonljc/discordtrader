"""
Rule-based confidence bucketing for parsed trade intents.

No LLM calls.  The bucket is derived purely from which fields were
successfully extracted and whether the ticker is statically tradable.

Confidence controls the risk gate:
  HIGH   → proceeds to sizing + execution automatically
  MEDIUM → risk gate may hold for review (configurable)
  LOW    → risk gate rejects automatically
"""
from __future__ import annotations

from decimal import Decimal

from schemas.trade_intent import ConfidenceBucket


def assign_confidence(
    entry_price: Decimal | None,
    stop_price: Decimal | None,
    take_profit_price: Decimal | None,
    ticker: str,  # noqa: ARG001 — reserved for future per-ticker rules
    is_tradable: bool,
) -> ConfidenceBucket:
    """
    Assign a confidence bucket based on parsed fields.

    Rules (evaluated in order, first match wins):
        LOW    if ticker is not tradable
        HIGH   if entry + stop are both present
        MEDIUM if entry is present but stop is missing
        LOW    otherwise (market order with no prices, or only target)

    A stop price is mandatory for HIGH confidence because the risk gate
    needs it to compute position sizing and the R/R ratio.  Signals without
    a stop cannot be auto-sized safely.
    """
    if not is_tradable:
        return ConfidenceBucket.LOW

    has_entry = entry_price is not None
    has_stop = stop_price is not None

    if has_entry and has_stop:
        return ConfidenceBucket.HIGH

    if has_entry:
        # Entry without stop: can place an order but cannot size it properly
        return ConfidenceBucket.MEDIUM

    # No entry price at all (market order, or parse too sparse)
    return ConfidenceBucket.LOW
