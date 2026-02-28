"""
Smart Options Selector — automatically finds the best long-dated call option.

Used exclusively for HIGH-confidence LONG signals that do not specify an explicit
option contract.  SHORT signals never use this module (no auto-put selection).

Selection algorithm
-------------------
1. Fetch the option chain parameters for the underlying stock via IBKR.
2. Filter to expirations ≥ (today + min_expiry_months) months out.
3. Pick the nearest qualifying expiration (minimises time-value drain).
4. Among the available strikes, find the one closest to (spot × OTM_FACTOR)
   where OTM_FACTOR ≈ 1.02 (≈2 % out-of-the-money).
5. Try up to MAX_STRIKE_ATTEMPTS strikes (alternating out/in from the target)
   to find one with a non-zero bid (liquid market).
6. Return the selected SelectedOption, or None if no liquid option is found.

Caller responsibility:
    - Call with a connected ib_client.
    - Handle the None case (fall back to shares order).
    - Cache the result within a session to avoid repeated chain fetches.

Performance:
    Typical latency is 0.5–2 s for the IBKR reqSecDefOptParams call plus
    one reqTickers call.  The executor runs this while the IBKR network is
    already warm, so the impact on the overall pipeline is bounded.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any

import ib_insync as ib

from audit.logger import get_logger

_log = get_logger("smart_options_selector")

# ── Tuning constants ──────────────────────────────────────────────────────────

OTM_FACTOR: float = 1.02          # target 2 % OTM call strike
MAX_STRIKE_ATTEMPTS: int = 5      # max strikes to try for liquidity


# ── Result dataclass ─────────────────────────────────────────────────────────


@dataclass(frozen=True)
class SelectedOption:
    """A fully-resolved, liquid call option contract."""

    ticker: str
    expiry: date
    strike: Decimal
    last_price: Decimal       # last traded premium per share (for sizing)
    bid: Decimal              # current bid (>0 confirms liquidity)
    ask: Decimal              # current ask (use for limit order)
    contract: Any             # qualified ib_insync Option object


# ── Internal helpers ─────────────────────────────────────────────────────────


def _min_expiry_date(min_months: int) -> date:
    today = date.today()
    month = today.month + min_months
    year = today.year + (month - 1) // 12
    month = ((month - 1) % 12) + 1
    return today.replace(year=year, month=month)


def _parse_ibkr_expiry(expiry_str: str) -> date | None:
    """Parse YYYYMMDD string → date, or None on failure."""
    try:
        return date(int(expiry_str[:4]), int(expiry_str[4:6]), int(expiry_str[6:8]))
    except (ValueError, IndexError):
        return None


def _closest_strikes(
    strikes: list[float],
    target: float,
    n: int = MAX_STRIKE_ATTEMPTS,
) -> list[float]:
    """
    Return up to n strikes sorted by proximity to target, starting with
    strikes at or above target (prefer OTM for calls).
    """
    above = sorted((s for s in strikes if s >= target), key=lambda s: s - target)
    below = sorted((s for s in strikes if s < target), key=lambda s: target - s)
    candidates: list[float] = []
    for pair in zip(above, below, strict=False):
        candidates.extend(pair)
    candidates.extend(above[len(below):])
    candidates.extend(below[len(above):])
    return candidates[:n]


# ── Public API ────────────────────────────────────────────────────────────────


async def select_best_call(
    ib_client: Any,
    ticker: str,
    spot_price: Decimal,
    stock_con_id: int,
    min_expiry_months: int = 6,
    exchange: str = "SMART",
) -> SelectedOption | None:
    """
    Find and return the best liquid long-dated call option for *ticker*.

    Args:
        ib_client:         Connected ib_insync.IB instance.
        ticker:            Underlying symbol, e.g. "AAPL".
        spot_price:        Current underlying price (for OTM strike calculation).
        stock_con_id:      conId of the qualified stock contract (needed for chain lookup).
        min_expiry_months: Minimum months to expiry (default 6).
        exchange:          Routing exchange for option contracts.

    Returns:
        SelectedOption if a liquid call is found, else None.

    Never raises — all IBKR errors are caught and logged.
    """
    try:
        return await _select(
            ib_client, ticker, spot_price, stock_con_id, min_expiry_months, exchange
        )
    except Exception as exc:  # noqa: BLE001
        _log.error(
            "smart_selector_unexpected_error",
            ticker=ticker,
            error=str(exc),
        )
        return None


async def _select(
    ib_client: Any,
    ticker: str,
    spot_price: Decimal,
    stock_con_id: int,
    min_expiry_months: int,
    exchange: str,
) -> SelectedOption | None:
    min_exp = _min_expiry_date(min_expiry_months)

    # ── Step 1: fetch option chain parameters ─────────────────────────────────
    chains: list[Any] = await ib_client.reqSecDefOptParamsAsync(
        underSymbol=ticker,
        futFopExchange="",
        underSecType="STK",
        underConId=stock_con_id,
    )

    if not chains:
        _log.warning("smart_selector_no_chains", ticker=ticker)
        return None

    # ── Step 2: filter to exchanges we care about and qualifying expirations ───
    candidate_chain: Any | None = None
    for chain in chains:
        # Prefer the SMART-routed exchange (same as stock) when available
        if chain.exchange in ("SMART", exchange, "CBOE", "AMEX", "BOX", "C2", "ISE"):
            candidate_chain = chain
            break
    if candidate_chain is None:
        candidate_chain = chains[0]

    qualifying_expiries = sorted(
        exp
        for raw in candidate_chain.expirations
        if (exp := _parse_ibkr_expiry(raw)) is not None and exp >= min_exp
    )

    if not qualifying_expiries:
        _log.info(
            "smart_selector_no_qualifying_expiries",
            ticker=ticker,
            min_expiry=str(min_exp),
        )
        return None

    nearest_expiry = qualifying_expiries[0]
    expiry_str = nearest_expiry.strftime("%Y%m%d")

    # ── Step 3: find candidate strikes ───────────────────────────────────────
    target_strike = float(spot_price) * OTM_FACTOR
    candidate_strikes = _closest_strikes(
        list(candidate_chain.strikes), target_strike
    )

    if not candidate_strikes:
        _log.info("smart_selector_no_strikes", ticker=ticker)
        return None

    # ── Step 4: check liquidity for each candidate strike ────────────────────
    for strike in candidate_strikes:
        option = ib.Option(ticker, expiry_str, strike, "C", exchange)
        try:
            qualified: list[Any] = await ib_client.qualifyContractsAsync(option)
        except Exception as exc:  # noqa: BLE001
            _log.debug(
                "smart_selector_qualify_failed",
                ticker=ticker, strike=strike, error=str(exc)
            )
            continue

        if not qualified:
            continue

        qualified_contract = qualified[0]

        # Request market data tick
        try:
            tickers_list: list[Any] = await ib_client.reqTickersAsync(qualified_contract)
        except Exception as exc:  # noqa: BLE001
            _log.debug(
                "smart_selector_tick_failed",
                ticker=ticker, strike=strike, error=str(exc)
            )
            continue

        if not tickers_list:
            continue

        tick = tickers_list[0]
        bid = Decimal(str(tick.bid)) if tick.bid and tick.bid > 0 else Decimal("0")
        ask = Decimal(str(tick.ask)) if tick.ask and tick.ask > 0 else Decimal("0")
        last = (
            Decimal(str(tick.last))
            if tick.last and tick.last > 0
            else (ask if ask > 0 else bid)
        )

        if bid <= 0:
            # No liquid market on this strike; try next
            _log.debug(
                "smart_selector_illiquid_strike",
                ticker=ticker,
                expiry=str(nearest_expiry),
                strike=strike,
            )
            # Small delay before next IBKR call
            await asyncio.sleep(0.05)
            continue

        _log.info(
            "smart_selector_found_option",
            ticker=ticker,
            expiry=str(nearest_expiry),
            strike=strike,
            bid=str(bid),
            ask=str(ask),
            last=str(last),
        )

        return SelectedOption(
            ticker=ticker,
            expiry=nearest_expiry,
            strike=Decimal(str(strike)),
            last_price=last,
            bid=bid,
            ask=ask,
            contract=qualified_contract,
        )

    _log.info(
        "smart_selector_no_liquid_option",
        ticker=ticker,
        expiry=str(nearest_expiry),
        strikes_tried=len(candidate_strikes),
    )
    return None
