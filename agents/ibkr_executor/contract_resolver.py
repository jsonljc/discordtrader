"""
Contract resolution — ticker string → qualified ib_insync Contract.

`resolve_contract` calls IBKR's symbol lookup to populate conId and exchange
details so subsequent order placement succeeds.  Raises `ValueError` on
unknown tickers.

`resolve_option_contract` qualifies an explicit option contract from
ticker + expiry + strike + option_type fields provided by the signal parser.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

import ib_insync as ib


async def resolve_contract(
    ib_client: Any,
    ticker: str,
    exchange: str = "SMART",
    currency: str = "USD",
) -> Any:
    """
    Return a fully-qualified ib_insync Stock contract for *ticker*.

    Args:
        ib_client:  Connected `ib_insync.IB` instance.
        ticker:     Exchange ticker symbol, e.g. "AAPL".
        exchange:   Routing exchange (default "SMART").
        currency:   Quote currency (default "USD").

    Raises:
        ValueError:  If IBKR returns no matching contract.
    """
    contract = ib.Stock(ticker, exchange, currency)
    qualified: list[Any] = await ib_client.qualifyContractsAsync(contract)
    if not qualified:
        raise ValueError(f"No IBKR contract found for ticker '{ticker}'")
    return qualified[0]


async def resolve_option_contract(
    ib_client: Any,
    ticker: str,
    expiry: date,
    strike: Decimal,
    option_type: str,          # "CALL" or "PUT"
    exchange: str = "SMART",
    currency: str = "USD",
) -> Any:
    """
    Return a fully-qualified ib_insync Option contract.

    Args:
        ib_client:    Connected `ib_insync.IB` instance.
        ticker:       Underlying symbol, e.g. "IRDM".
        expiry:       Option expiration date.
        strike:       Strike price.
        option_type:  "CALL" or "PUT".
        exchange:     Routing exchange.
        currency:     Quote currency.

    Raises:
        ValueError:  If IBKR returns no matching contract.
    """
    right = "C" if option_type.upper() == "CALL" else "P"
    expiry_str = expiry.strftime("%Y%m%d")
    contract = ib.Option(ticker, expiry_str, float(strike), right, exchange, currency=currency)
    qualified: list[Any] = await ib_client.qualifyContractsAsync(contract)
    if not qualified:
        raise ValueError(
            f"No IBKR option contract found for "
            f"{ticker} {expiry_str} {strike} {option_type}"
        )
    return qualified[0]
