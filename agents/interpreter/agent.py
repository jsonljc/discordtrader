from __future__ import annotations

from decimal import Decimal

from audit.heartbeat import AgentHeartbeat
from audit.hasher import stamp
from audit.logger import bind_correlation_id, get_logger
from bus.queue import PipelineBus
from config.settings import Settings
from schemas.signal_event import SignalEvent
from schemas.trade_intent import AssetClass, Direction, OptionType, TradeIntent

from .confidence import assign_confidence
from .llm_confidence import assign_llm_confidence
from .llm_parser import LLMParseResult, llm_parse
from .templates import try_parse
from .validator import is_tradable, validate_ticker_format


class InterpreterAgent:
    """
    Agent 2 — Interpreter.

    Lifecycle:
        agent = InterpreterAgent(settings, bus)
        await agent.run()   # blocks; consumes bus.signals indefinitely

    Responsibilities:
        - Consume SignalEvents from bus.signals
        - Try the regex fast-path first (zero cost, deterministic)
        - Fall back to LLM parser for narrative signals the regex cannot handle
        - Stamp the TradeIntent and enqueue it onto bus.intents
        - Drop (log + skip) events that cannot be parsed by either path

    LLM path:
        - Only called when regex returns None AND settings.llm_enabled is True
        - Produces TradeIntent with llm_clarity_score and llm_summary populated
        - Options fields (option_type, strike, expiry) populated when signal
          specifies a contract
        - template_name is set to "llm_parsed" to distinguish from regex intents
        - requires_manual_approval is always True — LLM intents NEVER auto-execute;
          the Risk Officer will force NEEDS_APPROVAL on any intent with this flag
    """

    def __init__(self, settings: Settings, bus: PipelineBus) -> None:
        self._settings = settings
        self._bus = bus
        self._log = get_logger("interpreter")
        self._heartbeat = AgentHeartbeat(
            "interpreter",
            interval_seconds=getattr(settings, "heartbeat_interval_seconds", 30.0),
        )

    async def run(self) -> None:
        """Consume bus.signals indefinitely, emitting TradeIntents."""
        self._heartbeat.start()
        try:
            await self._run_loop()
        finally:
            self._heartbeat.stop()

    async def _run_loop(self) -> None:
        """Inner loop — consumes signals and emits intents."""
        self._log.info(
            "interpreter_started",
            profile=self._settings.profile,
            llm_enabled=self._settings.llm_enabled,
            llm_model=self._settings.llm_model,
        )
        while True:
            event = await self._bus.signals.get()
            try:
                intent = await self._interpret(event)
                if intent is not None:
                    await self._bus.intents.put(intent)
            except Exception as exc:  # noqa: BLE001 — keep running on any error
                self._log.error(
                    "interpreter_unexpected_error",
                    error=str(exc),
                    correlation_id=str(event.correlation_id),
                )
            finally:
                self._bus.signals.task_done()

    async def _interpret(self, event: SignalEvent) -> TradeIntent | None:
        """
        Try regex first; fall back to LLM if regex returns None.
        Returns a stamped TradeIntent or None if both paths fail/drop.
        """
        bind_correlation_id(event.correlation_id)

        # ── Fast path: regex ────────────────────────────────────────────────
        result = try_parse(event.raw_text)
        if result is not None:
            return self._from_regex(event, result)

        # ── Slow path: LLM ──────────────────────────────────────────────────
        if not self._settings.llm_enabled:
            self._log.warning(
                "regex_failed_llm_disabled",
                raw_text=event.raw_text[:120],
                source_message_id=event.source_message_id,
            )
            return None

        return await self._from_llm(event)

    # ── Regex path ──────────────────────────────────────────────────────────

    def _from_regex(self, event: SignalEvent, result: object) -> TradeIntent | None:
        """Build a TradeIntent from a successful regex parse."""
        from .templates import ParseResult  # local to avoid circular at module level

        assert isinstance(result, ParseResult)

        if not validate_ticker_format(result.ticker):
            self._log.warning(
                "invalid_ticker_format",
                ticker=result.ticker,
                raw_text=event.raw_text[:80],
            )
            return None

        tradable = is_tradable(result.ticker)
        confidence = assign_confidence(
            entry_price=result.entry_price,
            stop_price=result.stop_price,
            take_profit_price=result.take_profit_price,
            ticker=result.ticker,
            is_tradable=tradable,
        )

        self._log.info(
            "trade_intent_parsed_regex",
            ticker=result.ticker,
            direction=result.direction.value,
            template=result.template_name,
            confidence=confidence.value,
            entry=str(result.entry_price),
            stop=str(result.stop_price),
            target=str(result.take_profit_price),
        )

        intent = TradeIntent(
            correlation_id=event.correlation_id,
            source_signal_id=event.event_id,
            ticker=result.ticker,
            asset_class=result.asset_class,
            direction=result.direction,
            entry_price=result.entry_price,
            stop_price=result.stop_price,
            take_profit_price=result.take_profit_price,
            confidence=confidence,
            template_name=result.template_name,
            profile=event.profile,
        )
        return stamp(intent)  # type: ignore[return-value]

    # ── LLM path ────────────────────────────────────────────────────────────

    async def _from_llm(self, event: SignalEvent) -> TradeIntent | None:
        """Call LLM parser, score confidence, and build a TradeIntent."""
        llm_result = await llm_parse(event.raw_text, self._settings)

        if llm_result is None:
            self._log.warning(
                "llm_parse_failed",
                raw_text=event.raw_text[:120],
                source_message_id=event.source_message_id,
            )
            return None

        confidence = assign_llm_confidence(
            llm_result, min_clarity=self._settings.llm_min_clarity
        )

        if confidence is None:
            self._log.info(
                "llm_signal_dropped",
                clarity_score=llm_result.clarity_score,
                ticker=llm_result.ticker,
                direction=llm_result.direction,
                summary=llm_result.summary[:80],
                source_message_id=event.source_message_id,
            )
            return None

        # Validate ticker format even for LLM-parsed signals
        ticker = llm_result.ticker or ""
        if not validate_ticker_format(ticker):
            self._log.warning(
                "llm_invalid_ticker_format",
                ticker=ticker,
                raw_text=event.raw_text[:80],
            )
            return None

        self._log.info(
            "trade_intent_parsed_llm",
            ticker=ticker,
            direction=llm_result.direction,
            asset_class=llm_result.asset_class,
            option_type=llm_result.option_type,
            strike=llm_result.strike,
            expiry=str(llm_result.expiry) if llm_result.expiry else None,
            confidence=confidence.value,
            clarity_score=llm_result.clarity_score,
            summary=llm_result.summary[:80],
        )

        if llm_result.direction is None:
            # _from_llm only runs after assign_llm_confidence returned non-None,
            # which requires direction to be present; this guard satisfies mypy.
            self._log.warning("llm_direction_missing_post_confidence_check")
            return None

        intent = TradeIntent(
            correlation_id=event.correlation_id,
            source_signal_id=event.event_id,
            ticker=ticker,
            asset_class=_to_asset_class(llm_result),
            direction=Direction(llm_result.direction),
            entry_price=_to_decimal(llm_result.entry_price),
            stop_price=_to_decimal(llm_result.stop_price),
            take_profit_price=_to_decimal(llm_result.take_profit_price),
            confidence=confidence,
            template_name="llm_parsed",
            # LLM-produced intents NEVER auto-execute — forced NEEDS_APPROVAL.
            requires_manual_approval=True,
            # Options fields
            option_type=_to_option_type(llm_result),
            strike=_to_decimal(llm_result.strike),
            expiry=llm_result.expiry,
            # LLM metadata
            llm_clarity_score=llm_result.clarity_score,
            llm_summary=llm_result.summary,
            profile=event.profile,
        )
        return stamp(intent)  # type: ignore[return-value]


# ── Conversion helpers ────────────────────────────────────────────────────────


def _to_decimal(val: float | None) -> Decimal | None:
    if val is None:
        return None
    return Decimal(str(val))


def _to_asset_class(result: LLMParseResult) -> AssetClass:
    if result.asset_class == "OPTION":
        return AssetClass.OPTION
    return AssetClass.EQUITY


def _to_option_type(result: LLMParseResult) -> OptionType | None:
    if result.option_type == "CALL":
        return OptionType.CALL
    if result.option_type == "PUT":
        return OptionType.PUT
    return None
