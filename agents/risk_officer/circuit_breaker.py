"""
Stateful circuit breaker for global trading halts.

Wraps the pure check_drawdown_ok function from rules.py and adds:
  - Manual halt / resume capability
  - Audit trail of halt events
"""
from __future__ import annotations

from decimal import Decimal

from agents.risk_officer.rules import check_drawdown_ok
from audit.logger import get_logger


class CircuitBreaker:
    """
    Manages global trading halts triggered by:
      - Daily drawdown exceeding the configured threshold
      - Manual operator halt (e.g. via governance tooling)

    The circuit breaker is NOT in the latency path — it is consulted once
    per trade evaluation via is_halted.  Auto-halt on drawdown is applied
    by evaluate_trade(); the CircuitBreaker only tracks the manual halt state.
    """

    def __init__(self) -> None:
        self._manually_halted: bool = False
        self._halt_reason: str = ""
        self._log = get_logger("circuit_breaker")

    def halt(self, reason: str) -> None:
        """Manually halt all trading.  Persists until resume() is called."""
        self._manually_halted = True
        self._halt_reason = reason
        self._log.warning("circuit_breaker_halted", reason=reason)

    def resume(self) -> None:
        """Lift a manual halt."""
        self._manually_halted = False
        prev_reason = self._halt_reason
        self._halt_reason = ""
        self._log.info("circuit_breaker_resumed", was_halted_for=prev_reason)

    def check_portfolio(self, daily_pnl_pct: Decimal, max_drawdown_pct: Decimal) -> bool:
        """
        Return True if trading is safe to proceed (not halted AND drawdown ok).
        Does NOT mutate state — call halt() explicitly if you want to latch.
        """
        if self._manually_halted:
            return False
        return check_drawdown_ok(daily_pnl_pct, max_drawdown_pct)

    @property
    def is_halted(self) -> bool:
        return self._manually_halted

    @property
    def halt_reason(self) -> str:
        return self._halt_reason
