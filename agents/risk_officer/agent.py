from __future__ import annotations

from audit.heartbeat import AgentHeartbeat
from audit.hasher import stamp
from audit.logger import bind_correlation_id, get_logger
from bus.queue import PipelineBus
from config.settings import Settings
from schemas.risk_decision import RiskDecision
from schemas.trade_intent import TradeIntent

from .circuit_breaker import CircuitBreaker
from .portfolio_adapter import PortfolioAdapter
from .rules import evaluate_trade


class RiskOfficerAgent:
    """
    Agent 3 — Risk Officer.

    Lifecycle:
        agent = RiskOfficerAgent(settings, bus)
        await agent.run()   # blocks; consumes bus.intents indefinitely

    Responsibilities:
        - Consume TradeIntents from bus.intents
        - Fetch a live PortfolioSnapshot via PortfolioAdapter (IBKR)
        - Evaluate the intent against all configured risk constraints
        - Emit a stamped RiskDecision (APPROVED / NEEDS_APPROVAL / REJECTED)
          onto bus.decisions
        - Check circuit breaker state before each evaluation

    The evaluation logic is a pure function in rules.py — this agent is
    purely orchestration + I/O.
    """

    def __init__(self, settings: Settings, bus: PipelineBus) -> None:
        self._settings = settings
        self._bus = bus
        self._adapter = PortfolioAdapter(settings)
        self._circuit_breaker = CircuitBreaker()
        self._log = get_logger("risk_officer")
        self._heartbeat = AgentHeartbeat(
            "risk_officer",
            interval_seconds=getattr(settings, "heartbeat_interval_seconds", 30.0),
        )

    async def run(self) -> None:
        """Consume bus.intents indefinitely, emitting RiskDecisions."""
        self._heartbeat.start()
        try:
            await self._run_loop()
        finally:
            self._heartbeat.stop()

    async def _run_loop(self) -> None:
        """Inner loop — consumes intents and emits decisions."""
        self._log.info(
            "risk_officer_started",
            profile=self._settings.profile,
            sleeve_value=str(self._settings.sleeve_value),
            max_positions=self._settings.max_open_positions,
        )
        while True:
            intent: TradeIntent = await self._bus.intents.get()
            try:
                decision = await self._evaluate(intent)
                await self._bus.decisions.put(decision)
            except Exception as exc:  # noqa: BLE001
                self._log.error(
                    "risk_officer_unexpected_error",
                    error=str(exc),
                    correlation_id=str(intent.correlation_id),
                )
            finally:
                self._bus.intents.task_done()

    async def _evaluate(self, intent: TradeIntent) -> RiskDecision:
        bind_correlation_id(intent.correlation_id)

        portfolio = await self._adapter.get_snapshot(intent.correlation_id)

        decision = evaluate_trade(
            intent=intent,
            portfolio=portfolio,
            max_open_positions=self._settings.max_open_positions,
            max_daily_drawdown_pct=self._settings.max_daily_drawdown_pct,
            is_manually_halted=self._circuit_breaker.is_halted,
            min_position_pct=self._settings.min_position_pct,
            max_position_pct=self._settings.max_position_pct,
        )

        stamped: RiskDecision = stamp(decision)  # type: ignore[assignment]

        self._log.info(
            "risk_decision_emitted",
            outcome=decision.outcome.value,
            ticker=decision.approved_ticker,
            quantity=decision.approved_quantity,
            position_pct=str(decision.position_size_pct),
            reasons=decision.rejection_reasons,
        )

        return stamped

    def halt(self, reason: str) -> None:
        """Manually halt all trading via the circuit breaker."""
        self._circuit_breaker.halt(reason)

    def resume(self) -> None:
        """Lift a manual halt."""
        self._circuit_breaker.resume()

    async def close(self) -> None:
        """Close the IBKR portfolio adapter connection."""
        await self._adapter.close()

    @property
    def circuit_breaker(self) -> CircuitBreaker:
        return self._circuit_breaker
