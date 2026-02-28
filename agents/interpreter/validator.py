"""
Ticker validation — format check and static tradability screen.

This is NOT a live exchange lookup.  It catches obvious non-equity symbols
(currencies, crypto, index names) and format errors before they reach the
risk gate or IBKR.  A live contract resolve happens in the IBKR executor.
"""
from __future__ import annotations

import re

# Valid equity/ETF ticker: 1–5 uppercase ASCII letters
_TICKER_FORMAT_RE = re.compile(r"^[A-Z]{1,5}$")

# Symbols that pass format validation but are NOT directly tradable as IBKR
# equity orders.  ETFs like SPY, QQQ, IWM are tradable and intentionally absent.
_NON_TRADABLE: frozenset[str] = frozenset({
    # Major fiat currencies
    "USD", "EUR", "GBP", "JPY", "CHF", "AUD", "CAD", "NZD",
    # Crypto tickers that appear in mixed-asset channels
    "BTC", "ETH", "SOL", "DOGE", "XRP", "LTC", "ADA", "BNB",
    "USDT", "USDC", "BUSD",
    # Cash-settled index symbols (not tradable directly)
    "SPX", "NDX", "VIX", "RUT", "DJI", "COMP",
    # Generic non-ticker words
    "ETF", "FUND", "CASH", "BOND", "NOTE", "CALL", "PUT",
})


def validate_ticker_format(ticker: str) -> bool:
    """Return True if ticker is 1–5 uppercase ASCII letters."""
    return bool(_TICKER_FORMAT_RE.match(ticker))


def is_tradable(ticker: str) -> bool:
    """
    Return True if the ticker passes format validation AND is not in the
    known non-tradable exclusion list.

    This is a fast static check only.  Live tradability is verified by the
    IBKR executor's contract resolver.
    """
    return validate_ticker_format(ticker) and ticker not in _NON_TRADABLE
