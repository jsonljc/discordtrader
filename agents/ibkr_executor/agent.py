"""
IBKR Executor Agent — Agent 4.

Lifecycle:
    agent = IBKRExecutorAgent(settings, bus)
    await agent.run()   # blocks; consumes bus.decisions indefinitely

Responsibilities:
    - Consume RiskDecisions from bus.decisions
    - Skip (log + emit CANCELLED receipt) for non-APPROVED decisions
    - Route to one of three execution paths:
        a) Equity bracket order  (shares, entry + stop + optional take-profit)
        b) Explicit options order (contract fully specified by the signal)
        c) Smart options order    (selector finds best call from IBKR chain)
    - Track the order fill; emit a stamped ExecutionReceipt onto bus.receipts

Paper-mode guard:
    settings.paper_mode is propagated into every ExecutionReceipt.
    The port in settings must already point to a paper TWS/Gateway.

Dependency injection:
    The optional `connection` parameter allows tests to inject a fake
    IBKRConnection without patching at the import level.
"""
from __future__ import annotations

from decimal import ROUND_DOWN, Decimal
from typing import Any

from audit.hasher import stamp
from audit.logger import bind_correlation_id, get_logger
from bus.queue import PipelineBus
from config.settings import Settings
from schemas.execution_receipt import ExecutionReceipt, OrderStatus
from schemas.risk_decision import RiskDecision, RiskOutcome
from schemas.trade_intent import AssetClass

from .connection import IBKRConnection, IBKRConnectionProtocol
from .contract_resolver import resolve_contract, resolve_option_contract
from .order_builder import (
    BracketParams,
    OptionOrderParams,
    build_bracket_params,
    build_option_order_params,
)
from .order_tracker import track_fill
from .smart_options_selector import SelectedOption, select_best_call


class IBKRExecutorAgent:
    """
    Agent 4 — IBKR Executor.

    Routes APPROVED RiskDecisions to the correct order path:
        - Equity shares    → bracket order
        - Explicit options → simple limit order on specified contract
        - Smart selector   → find best call, then limit order; fallback to shares
    """

    def __init__(
        self,
        settings: Settings,
        bus: PipelineBus,
        connection: IBKRConnectionProtocol | None = None,
    ) -> None:
        self._settings = settings
        self._bus = bus
        self._conn: IBKRConnectionProtocol = (
            connection if connection is not None else IBKRConnection(settings)
        )
        self._log = get_logger("ibkr_executor")

    # ── public ──────────────────────────────────────────────────────────────

    async def run(self) -> None:
        """Connect to IBKR, then consume bus.decisions indefinitely."""
        await self._conn.connect()
        self._log.info(
            "ibkr_executor_started",
            profile=self._settings.profile,
            paper_mode=self._settings.paper_mode,
            ibkr_host=self._settings.ibkr_host,
            ibkr_port=self._settings.ibkr_port,
        )
        while True:
            decision: RiskDecision = await self._bus.decisions.get()
            try:
                receipt = await self._execute(decision)
                await self._bus.receipts.put(receipt)
            except Exception as exc:  # noqa: BLE001
                self._log.error(
                    "ibkr_executor_unexpected_error",
                    error=str(exc),
                    correlation_id=str(decision.correlation_id),
                )
                err_receipt: ExecutionReceipt = stamp(  # type: ignore[assignment]
                    ExecutionReceipt(
                        correlation_id=decision.correlation_id,
                        source_decision_id=decision.event_id,
                        status=OrderStatus.ERROR,
                        error_message=str(exc),
                        is_paper=self._settings.paper_mode,
                        profile=self._settings.profile,
                    )
                )
                await self._bus.receipts.put(err_receipt)
            finally:
                self._bus.decisions.task_done()

    async def close(self) -> None:
        """Disconnect from IBKR cleanly."""
        self._conn.disconnect()

    # ── internal ────────────────────────────────────────────────────────────

    async def _execute(self, decision: RiskDecision) -> ExecutionReceipt:
        bind_correlation_id(decision.correlation_id)

        if decision.outcome != RiskOutcome.APPROVED:
            self._log.info(
                "skipping_non_approved_decision",
                outcome=decision.outcome.value,
                reasons=decision.rejection_reasons,
                correlation_id=str(decision.correlation_id),
            )
            return stamp(  # type: ignore[return-value]
                ExecutionReceipt(
                    correlation_id=decision.correlation_id,
                    source_decision_id=decision.event_id,
                    status=OrderStatus.CANCELLED,
                    error_message=f"not_approved:{decision.outcome.value}",
                    is_paper=self._settings.paper_mode,
                    profile=decision.profile,
                )
            )

        if decision.approved_ticker is None:
            return stamp(  # type: ignore[return-value]
                ExecutionReceipt(
                    correlation_id=decision.correlation_id,
                    source_decision_id=decision.event_id,
                    status=OrderStatus.ERROR,
                    error_message="approved_decision_missing_ticker",
                    is_paper=self._settings.paper_mode,
                    profile=decision.profile,
                )
            )

        # ── Route by asset class ──────────────────────────────────────────────
        if decision.use_smart_options_selector:
            return await self._execute_smart_options(decision)

        if decision.approved_asset_class == AssetClass.OPTION:
            return await self._execute_explicit_option(decision)

        return await self._execute_equity(decision)

    # ── Equity (shares) path ──────────────────────────────────────────────────

    async def _execute_equity(self, decision: RiskDecision) -> ExecutionReceipt:
        """Place a bracket order for shares."""
        assert decision.approved_ticker is not None

        if decision.approved_quantity is None:
            return stamp(  # type: ignore[return-value]
                ExecutionReceipt(
                    correlation_id=decision.correlation_id,
                    source_decision_id=decision.event_id,
                    status=OrderStatus.ERROR,
                    error_message="equity_order_missing_quantity",
                    is_paper=self._settings.paper_mode,
                    profile=decision.profile,
                )
            )

        contract = await resolve_contract(self._conn.ib, decision.approved_ticker)
        params = build_bracket_params(decision)
        receipt = await _place_bracket_and_track(
            self._conn.ib, contract, params, decision, self._settings.paper_mode
        )
        stamped: ExecutionReceipt = stamp(receipt)  # type: ignore[assignment]
        self._log.info(
            "equity_execution_receipt",
            ticker=decision.approved_ticker,
            status=stamped.status.value,
            ibkr_order_id=stamped.ibkr_order_id,
            quantity=decision.approved_quantity,
        )
        return stamped

    # ── Explicit options path ─────────────────────────────────────────────────

    async def _execute_explicit_option(self, decision: RiskDecision) -> ExecutionReceipt:
        """Place a limit order on an explicitly-specified option contract."""
        assert decision.approved_ticker is not None

        if (
            decision.approved_option_type is None
            or decision.approved_strike is None
            or decision.approved_expiry is None
        ):
            return stamp(  # type: ignore[return-value]
                ExecutionReceipt(
                    correlation_id=decision.correlation_id,
                    source_decision_id=decision.event_id,
                    status=OrderStatus.ERROR,
                    error_message="explicit_option_missing_contract_fields",
                    is_paper=self._settings.paper_mode,
                    profile=decision.profile,
                )
            )

        contract = await resolve_option_contract(
            self._conn.ib,
            decision.approved_ticker,
            decision.approved_expiry,
            decision.approved_strike,
            decision.approved_option_type.value,
        )

        limit_price = (
            decision.approved_entry_price
            if decision.approved_entry_price is not None
            else decision.approved_strike  # fallback: use strike as limit
        )

        quantity = decision.approved_quantity
        if quantity is None:
            # Budget-based sizing
            if decision.approved_budget is not None and limit_price > 0:
                quantity = int(
                    (decision.approved_budget / (limit_price * 100))
                    .to_integral_value(rounding=ROUND_DOWN)
                )
            if not quantity:
                return stamp(  # type: ignore[return-value]
                    ExecutionReceipt(
                        correlation_id=decision.correlation_id,
                        source_decision_id=decision.event_id,
                        status=OrderStatus.ERROR,
                        error_message="option_quantity_zero",
                        is_paper=self._settings.paper_mode,
                        profile=decision.profile,
                    )
                )

        params = build_option_order_params(decision, limit_price, quantity)
        receipt = await _place_option_and_track(
            self._conn.ib, contract, params, decision, self._settings.paper_mode
        )
        stamped: ExecutionReceipt = stamp(receipt)  # type: ignore[assignment]
        self._log.info(
            "explicit_option_execution_receipt",
            ticker=decision.approved_ticker,
            strike=str(decision.approved_strike),
            expiry=str(decision.approved_expiry),
            status=stamped.status.value,
            contracts=quantity,
        )
        return stamped

    # ── Smart selector path ───────────────────────────────────────────────────

    async def _execute_smart_options(self, decision: RiskDecision) -> ExecutionReceipt:
        """Find the best call via IBKR chain, then place limit order; fallback to shares."""
        assert decision.approved_ticker is not None

        # Resolve the underlying stock to get conId and spot price
        stock_contract = await resolve_contract(self._conn.ib, decision.approved_ticker)
        stock_con_id: int = int(stock_contract.conId)

        # Get current spot price
        spot_price = await _get_spot_price(self._conn.ib, stock_contract)
        if spot_price is None or spot_price <= 0:
            self._log.warning(
                "smart_selector_spot_price_unavailable",
                ticker=decision.approved_ticker,
            )
            # Fall back to equity
            return await self._fallback_equity(decision, stock_contract, budget_only=True)

        # Run the smart selector
        selected: SelectedOption | None = await select_best_call(
            ib_client=self._conn.ib,
            ticker=decision.approved_ticker,
            spot_price=spot_price,
            stock_con_id=stock_con_id,
        )

        if selected is None:
            self._log.info(
                "smart_selector_no_option_found_falling_back",
                ticker=decision.approved_ticker,
            )
            return await self._fallback_equity(decision, stock_contract, spot_price=spot_price)

        # Size the options order from budget
        budget = decision.approved_budget or Decimal("0")
        limit_price = selected.ask if selected.ask > 0 else selected.last_price
        if limit_price <= 0:
            return await self._fallback_equity(decision, stock_contract, spot_price=spot_price)

        quantity = int(
            (budget / (limit_price * 100)).to_integral_value(rounding=ROUND_DOWN)
        )
        if quantity == 0:
            return await self._fallback_equity(decision, stock_contract, spot_price=spot_price)

        params = build_option_order_params(decision, limit_price, quantity)
        receipt = await _place_option_and_track(
            self._conn.ib, selected.contract, params, decision, self._settings.paper_mode
        )
        stamped: ExecutionReceipt = stamp(receipt)  # type: ignore[assignment]
        self._log.info(
            "smart_option_execution_receipt",
            ticker=decision.approved_ticker,
            strike=str(selected.strike),
            expiry=str(selected.expiry),
            status=stamped.status.value,
            contracts=quantity,
            ask=str(selected.ask),
        )
        return stamped

    async def _fallback_equity(
        self,
        decision: RiskDecision,
        stock_contract: Any,
        spot_price: Decimal | None = None,
        budget_only: bool = False,
    ) -> ExecutionReceipt:
        """Fall back to a shares order when the smart selector cannot find a call."""
        budget = decision.approved_budget or Decimal("0")
        if spot_price is not None and spot_price > 0 and budget > 0:
            quantity = int(
                (budget / spot_price).to_integral_value(rounding=ROUND_DOWN)
            )
        elif not budget_only and decision.approved_quantity is not None:
            quantity = decision.approved_quantity
        else:
            # Can't size — error receipt
            return stamp(  # type: ignore[return-value]
                ExecutionReceipt(
                    correlation_id=decision.correlation_id,
                    source_decision_id=decision.event_id,
                    status=OrderStatus.ERROR,
                    error_message="smart_selector_fallback_cannot_size",
                    is_paper=self._settings.paper_mode,
                    profile=decision.profile,
                )
            )

        if quantity == 0:
            return stamp(  # type: ignore[return-value]
                ExecutionReceipt(
                    correlation_id=decision.correlation_id,
                    source_decision_id=decision.event_id,
                    status=OrderStatus.ERROR,
                    error_message="smart_selector_fallback_quantity_zero",
                    is_paper=self._settings.paper_mode,
                    profile=decision.profile,
                )
            )

        # Build a synthetic BracketParams for the fallback equity order
        from schemas.trade_intent import Direction

        from .order_builder import BracketParams
        action = "BUY" if decision.approved_direction == Direction.LONG else "SELL"
        params = BracketParams(
            action=action,
            quantity=quantity,
            entry_price=float(spot_price) if spot_price else None,
            stop_price=(
                float(decision.approved_stop_price)
                if decision.approved_stop_price else None
            ),
            take_profit_price=(
                float(decision.approved_take_profit)
                if decision.approved_take_profit else None
            ),
            order_ref=str(decision.correlation_id).replace("-", "")[:20],
        )
        receipt = await _place_bracket_and_track(
            self._conn.ib, stock_contract, params, decision, self._settings.paper_mode
        )
        stamped: ExecutionReceipt = stamp(receipt)  # type: ignore[assignment]
        self._log.info(
            "smart_selector_fallback_equity_receipt",
            ticker=decision.approved_ticker,
            status=stamped.status.value,
            quantity=quantity,
        )
        return stamped


# ── Order placement helpers ─────────────────────────────────────────────────


async def _get_spot_price(ib_client: Any, stock_contract: Any) -> Decimal | None:
    """
    Fetch the last traded price for a stock contract.
    Returns None on any failure or missing data.
    """
    try:
        tickers: list[Any] = await ib_client.reqTickersAsync(stock_contract)
        if not tickers:
            return None
        tick = tickers[0]
        price = tick.last or tick.close or tick.bid
        if price and price > 0:
            return Decimal(str(price))
    except Exception:  # noqa: BLE001
        pass
    return None


async def _place_bracket_and_track(
    ib_client: Any,
    contract: Any,
    params: BracketParams,
    decision: RiskDecision,
    is_paper: bool,
) -> ExecutionReceipt:
    """
    Place a bracket order and track the parent fill.  Returns an *un-stamped*
    ExecutionReceipt (the caller stamps it).
    """
    bracket = ib_client.bracketOrder(
        action=params.action,
        quantity=params.quantity,
        limitPrice=params.entry_price or 0.0,
        takeProfitPrice=params.take_profit_price or 0.0,
        stopLossPrice=params.stop_price or 0.0,
        orderRef=params.order_ref,
    )

    trades = [ib_client.placeOrder(contract, order) for order in bracket]

    parent_trade = trades[0]
    tp_trade = trades[1] if len(trades) > 1 else None
    sl_trade = trades[2] if len(trades) > 2 else None

    track_result = await track_fill(parent_trade)

    stop_order_id: int | None = (
        int(sl_trade.order.orderId)
        if sl_trade is not None and params.stop_price is not None
        else None
    )
    take_profit_order_id: int | None = (
        int(tp_trade.order.orderId)
        if tp_trade is not None and params.take_profit_price is not None
        else None
    )

    return ExecutionReceipt(
        correlation_id=decision.correlation_id,
        source_decision_id=decision.event_id,
        ibkr_order_id=track_result.ibkr_order_id,
        ibkr_perm_id=track_result.ibkr_perm_id,
        status=track_result.status,
        filled_quantity=track_result.filled_quantity,
        avg_fill_price=track_result.avg_fill_price,
        commission=track_result.commission,
        stop_order_id=stop_order_id,
        take_profit_order_id=take_profit_order_id,
        is_paper=is_paper,
        profile=decision.profile,
    )


async def _place_option_and_track(
    ib_client: Any,
    contract: Any,
    params: OptionOrderParams,
    decision: RiskDecision,
    is_paper: bool,
) -> ExecutionReceipt:
    """
    Place a simple limit order for an option and track the fill.
    Returns an *un-stamped* ExecutionReceipt.
    """
    from ib_insync import LimitOrder

    order = LimitOrder(
        action=params.action,
        totalQuantity=params.quantity,
        lmtPrice=params.limit_price,
        orderRef=params.order_ref,
    )
    trade = ib_client.placeOrder(contract, order)
    track_result = await track_fill(trade)

    return ExecutionReceipt(
        correlation_id=decision.correlation_id,
        source_decision_id=decision.event_id,
        ibkr_order_id=track_result.ibkr_order_id,
        ibkr_perm_id=track_result.ibkr_perm_id,
        status=track_result.status,
        filled_quantity=track_result.filled_quantity,
        avg_fill_price=track_result.avg_fill_price,
        commission=track_result.commission,
        stop_order_id=None,
        take_profit_order_id=None,
        is_paper=is_paper,
        profile=decision.profile,
    )
