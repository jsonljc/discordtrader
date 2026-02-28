"""
Deterministic signal text parser.

No LLM calls. Pure regex + rule-based field extraction.
All public functions are stateless (no I/O, no global state mutations).
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal

from schemas.trade_intent import AssetClass, Direction

# ── Price pattern ─────────────────────────────────────────────────────────────

_PRICE_PAT = r"\d{1,6}(?:\.\d{1,4})?"

# ── Direction vocabulary ──────────────────────────────────────────────────────

_LONG_WORDS: frozenset[str] = frozenset({"BUY", "LONG", "BUYING", "BULL"})
_SHORT_WORDS: frozenset[str] = frozenset({"SELL", "SHORT", "SELLING", "BEAR"})
_ALL_DIR_WORDS: frozenset[str] = _LONG_WORDS | _SHORT_WORDS

# Words that match the ticker regex but should never be treated as tickers
_TICKER_EXCLUSIONS: frozenset[str] = _ALL_DIR_WORDS | frozenset({
    "STOP", "LOSS", "ENTRY", "ENTER", "TARGET", "TAKE",
    "AT", "SL", "TP", "TGT", "PT", "PROFIT", "ALERT",
    "SIGNAL", "TRADE", "OR", "AND", "THE", "FOR",
})

# ── Compiled patterns ─────────────────────────────────────────────────────────

_DIR_RE = re.compile(
    r"\b(" + "|".join(sorted(_ALL_DIR_WORDS, key=len, reverse=True)) + r")\b",
)

# Ticker: 1–5 UPPERCASE letters (as written in the original text, not after
# uppercasing everything).  Optional leading $ for "$AAPL" style.
# Negative lookahead (?!\w) prevents partial matches like "AAPLS".
_TICKER_RE = re.compile(r"\$?([A-Z]{1,5})(?!\w)")

# Entry price — after "@", "entry", "enter/entering/entered", or "at <n>"
# NOTE: "entry"=e-n-t-r-y vs "entering"=e-n-t-e-r-ing — two distinct stems
_ENTRY_RE = re.compile(
    rf"(?:@\s*|(?:entry|enter(?:ing|ed)?|\bat\b)\s+)({_PRICE_PAT})",
    re.IGNORECASE,
)

# Stop-loss price — after "stop", "stop-loss", "stop loss", "sl", "s/l"
_STOP_RE = re.compile(
    rf"\b(?:stop(?:[-\s]loss)?|sl|s/l)\s+({_PRICE_PAT})",
    re.IGNORECASE,
)

# Take-profit price — after "target", "tp", "t/p", "take-profit", "take profit",
# "tgt", "pt"
_TARGET_RE = re.compile(
    rf"\b(?:take[-\s]?profit|target|tgt|t/p|tp|pt)\s+({_PRICE_PAT})",
    re.IGNORECASE,
)

# Bare price immediately after the ticker symbol (no keyword):
# "BUY AAPL 175.50 stop …"  →  group(1)=ticker, group(2)=price
_BARE_ENTRY_RE = re.compile(
    rf"([A-Z]{{1,5}})\s+({_PRICE_PAT})",
    re.IGNORECASE,
)

# ── Public result type ────────────────────────────────────────────────────────


@dataclass(frozen=True)
class ParseResult:
    """
    Fields successfully extracted from a raw signal string.

    entry_price, stop_price, take_profit_price are None when not found.
    template_name encodes which combination of fields was extracted and is
    used by the confidence bucketer and downstream observability.
    """

    template_name: str
    ticker: str
    direction: Direction
    asset_class: AssetClass
    entry_price: Decimal | None
    stop_price: Decimal | None
    take_profit_price: Decimal | None


# ── Internal helpers ──────────────────────────────────────────────────────────


def _find_price(pattern: re.Pattern[str], text: str) -> Decimal | None:
    m = pattern.search(text)
    return Decimal(m.group(1)) if m else None


def _build_template_name(
    direction: Direction,
    entry: Decimal | None,
    stop: Decimal | None,
    target: Decimal | None,
) -> str:
    """Encode which fields were found into a human-readable template name."""
    parts = [direction.value.lower()]
    if entry is not None:
        parts.append("entry")
    if stop is not None:
        parts.append("stop")
    if target is not None:
        parts.append("target")
    return "_".join(parts) if len(parts) > 1 else f"{direction.value.lower()}_market"


# ── Public API ────────────────────────────────────────────────────────────────


def try_parse(text: str) -> ParseResult | None:
    """
    Extract a structured trade intent from free-form signal text.

    Strategy:
        1. Find the direction word (BUY/LONG/SELL/SHORT and variants) in the
           uppercased text — case-insensitive.
        2. Find the ticker by scanning the *original* text for runs of 1–5
           UPPERCASE letters.  This distinguishes genuine tickers (always ALL-
           CAPS in Discord signals) from direction/keyword words that were
           originally lowercase.
        3. Extract each price field independently using keyword-anchored
           patterns on the uppercased text.

    Returns:
        ParseResult if direction + ticker are both found; None otherwise.
        Never raises.
    """
    if not text or not text.strip():
        return None

    upper = text.upper().strip()

    # ── Step 1: direction ────────────────────────────────────────────────────
    dir_match = _DIR_RE.search(upper)
    if not dir_match:
        return None

    dir_word = dir_match.group(1)
    direction = Direction.LONG if dir_word in _LONG_WORDS else Direction.SHORT

    # ── Step 2: ticker (from original casing) ────────────────────────────────
    ticker: str | None = None
    for m in _TICKER_RE.finditer(text):
        candidate = m.group(1)
        if candidate not in _TICKER_EXCLUSIONS:
            ticker = candidate
            break

    if ticker is None:
        return None

    # ── Step 3: prices (from uppercased text) ────────────────────────────────
    entry_price = _find_price(_ENTRY_RE, upper)

    # Fallback: bare number immediately after the ticker ("BUY AAPL 175.50 …")
    if entry_price is None:
        bare = _BARE_ENTRY_RE.search(upper)
        if bare and bare.group(1) == ticker:
            entry_price = Decimal(bare.group(2))

    stop_price = _find_price(_STOP_RE, upper)
    take_profit_price = _find_price(_TARGET_RE, upper)

    return ParseResult(
        template_name=_build_template_name(direction, entry_price, stop_price, take_profit_price),
        ticker=ticker,
        direction=direction,
        asset_class=AssetClass.EQUITY,
        entry_price=entry_price,
        stop_price=stop_price,
        take_profit_price=take_profit_price,
    )
