"""
LLM-based signal parser — narrative Discord messages → LLMParseResult.

Uses OpenAI's chat completion API with response_format=json_object to extract
structured trading intent from free-form analyst commentary.

Design principles:
    - Never raises: all errors are caught; None is returned on any failure
    - Fail-fast timeout: LLM_TIMEOUT_SECONDS prevents pipeline stalling
    - No side effects: pure async function, fully mockable in tests
    - API key never logged (repr=False on settings field)

Called by InterpreterAgent only when the regex fast-path returns None.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from openai import APIError, APITimeoutError, AsyncOpenAI

from audit.logger import get_logger
from config.settings import Settings

_log = get_logger("llm_parser")

# ── Prompt ───────────────────────────────────────────────────────────────────

_SYSTEM_PROMPT = """\
You are a trading signal parser for an automated trading system.
Extract structured trading intent from Discord messages posted by investment analysts.

Rules:
1. Only extract what is explicitly stated. Never infer or assume prices.
2. Ticker may be spelled out with hyphens or spaces (e.g. "I-R-D-M" = "IRDM", \
"A A P L" = "AAPL") or prefixed with $ — normalize to uppercase letters only, \
no punctuation.
3. Options: look for patterns like "$22.5C July 17", "22.5 calls exp 7/17", \
"July 17 22.5 calls", "200p Aug 15" → set asset_class=OPTION, option_type \
appropriately, and parse strike/expiry.
4. Direction: LONG = buy / calls / entering / re-enter / scaling into / bullish / long.
   SHORT = sell / puts / short / bearish / exiting long.
5. position_size_pct: convert percentage to decimal (e.g. "1.5% weighting" = 0.015).
6. expiry: always return as YYYY-MM-DD. If only month+day given (e.g. "July 17"), \
use the nearest future year.
7. clarity_score 0–100: how explicit and complete the trading signal is.
   Score 85+ ONLY when ticker + direction + entry_price are ALL unambiguous and \
   explicitly stated.
   Penalise heavily for: narrative/analysis text, speculative words \
   (might / watching / considering / thesis / interesting), missing entry price, \
   ambiguous or spelled-out ticker.

Return ONLY valid JSON — no markdown fences, no explanation, nothing else:
{
  "ticker": string | null,
  "direction": "LONG" | "SHORT" | null,
  "asset_class": "EQUITY" | "OPTION" | null,
  "option_type": "CALL" | "PUT" | null,
  "strike": number | null,
  "expiry": "YYYY-MM-DD" | null,
  "entry_price": number | null,
  "stop_price": number | null,
  "take_profit_price": number | null,
  "position_size_pct": number | null,
  "clarity_score": integer,
  "summary": string,
  "extraction_notes": string
}\
"""

# Regex to strip any accidental markdown code fences the model might add
_CODE_FENCE_RE = re.compile(r"```(?:json)?(.*?)```", re.DOTALL)


# ── Result dataclass ─────────────────────────────────────────────────────────


@dataclass
class LLMParseResult:
    """
    Raw structured output from the LLM before confidence scoring.
    All fields are optional — the confidence scorer decides what to do with gaps.
    """

    ticker: str | None = None
    direction: str | None = None          # "LONG" | "SHORT"
    asset_class: str | None = None        # "EQUITY" | "OPTION"
    option_type: str | None = None        # "CALL" | "PUT"
    strike: float | None = None
    expiry: date | None = None
    entry_price: float | None = None
    stop_price: float | None = None
    take_profit_price: float | None = None
    position_size_pct: float | None = None
    clarity_score: int = 0
    summary: str = ""
    extraction_notes: str = ""
    raw_response: dict[str, Any] = field(default_factory=dict, repr=False)


# ── Public API ───────────────────────────────────────────────────────────────


async def llm_parse(raw_text: str, settings: Settings) -> LLMParseResult | None:
    """
    Call OpenAI and parse the response into an LLMParseResult.

    Returns None on:
        - API key not configured
        - Network / API error
        - Timeout (LLM_TIMEOUT_SECONDS)
        - Malformed or empty JSON response
        - Missing both ticker and direction in response

    Never raises.
    """
    if not settings.openai_api_key:
        _log.warning("llm_parse_skipped_no_api_key")
        return None

    client = AsyncOpenAI(
        api_key=settings.openai_api_key,
        timeout=settings.llm_timeout_seconds,
    )

    try:
        response = await client.chat.completions.create(
            model=settings.llm_model,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": raw_text},
            ],
            temperature=0,   # deterministic extraction
            max_tokens=512,
        )
    except APITimeoutError:
        _log.warning("llm_parse_timeout", model=settings.llm_model)
        return None
    except APIError as exc:
        _log.error("llm_parse_api_error", error=str(exc), model=settings.llm_model)
        return None
    except Exception as exc:  # noqa: BLE001
        _log.error("llm_parse_unexpected_error", error=str(exc))
        return None

    content = (response.choices[0].message.content or "").strip()

    # Strip accidental code fences
    fence_match = _CODE_FENCE_RE.search(content)
    if fence_match:
        content = fence_match.group(1).strip()

    try:
        raw: dict[str, Any] = json.loads(content)
    except (json.JSONDecodeError, ValueError):
        _log.warning("llm_parse_invalid_json", content_preview=content[:120])
        return None

    return _build_result(raw)


# ── Internal helpers ─────────────────────────────────────────────────────────


def _normalise_ticker(raw: str | None) -> str | None:
    """Strip $, hyphens, spaces, and non-alpha chars; uppercase the result."""
    if not raw:
        return None
    cleaned = re.sub(r"[^A-Za-z]", "", raw).upper()
    return cleaned if cleaned else None


def _parse_expiry(raw: str | None) -> date | None:
    """Parse YYYY-MM-DD string to date, or return None on any failure."""
    if not raw:
        return None
    try:
        return date.fromisoformat(raw)
    except (ValueError, TypeError):
        return None


def _safe_float(val: Any) -> float | None:
    """Coerce a JSON value to float, or return None."""
    if val is None:
        return None
    try:
        return float(val)
    except (TypeError, ValueError):
        return None


def _safe_int(val: Any, default: int = 0) -> int:
    """Coerce a JSON value to int, clamped to [0, 100] for clarity scores."""
    try:
        return max(0, min(100, int(val)))
    except (TypeError, ValueError):
        return default


def _build_result(raw: dict[str, Any]) -> LLMParseResult | None:
    """
    Convert a raw JSON dict from the LLM into a validated LLMParseResult.
    Returns None if both ticker and direction are absent (cannot form any intent).
    """
    ticker = _normalise_ticker(raw.get("ticker"))
    direction_raw = raw.get("direction")
    direction = direction_raw if direction_raw in ("LONG", "SHORT") else None

    if ticker is None and direction is None:
        _log.warning(
            "llm_parse_missing_required_fields",
            ticker=raw.get("ticker"),
            direction=raw.get("direction"),
        )
        return None

    asset_class_raw = raw.get("asset_class")
    asset_class = asset_class_raw if asset_class_raw in ("EQUITY", "OPTION") else None

    option_type_raw = raw.get("option_type")
    option_type = option_type_raw if option_type_raw in ("CALL", "PUT") else None

    return LLMParseResult(
        ticker=ticker,
        direction=direction,
        asset_class=asset_class,
        option_type=option_type,
        strike=_safe_float(raw.get("strike")),
        expiry=_parse_expiry(raw.get("expiry")),
        entry_price=_safe_float(raw.get("entry_price")),
        stop_price=_safe_float(raw.get("stop_price")),
        take_profit_price=_safe_float(raw.get("take_profit_price")),
        position_size_pct=_safe_float(raw.get("position_size_pct")),
        clarity_score=_safe_int(raw.get("clarity_score", 0)),
        summary=str(raw.get("summary", "")),
        extraction_notes=str(raw.get("extraction_notes", "")),
        raw_response=raw,
    )
