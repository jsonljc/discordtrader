"""Shared pytest fixtures used across unit and integration test suites."""
from __future__ import annotations

from decimal import Decimal

import pytest

from schemas.execution_receipt import ExecutionReceipt, OrderStatus
from schemas.portfolio_snapshot import PortfolioSnapshot, PositionSnapshot
from schemas.risk_decision import RiskDecision, RiskOutcome
from schemas.signal_event import SignalEvent
from schemas.trade_intent import AssetClass, ConfidenceBucket, Direction, TradeIntent


@pytest.fixture
def sample_signal_event() -> SignalEvent:
    return SignalEvent(
        source_guild_id="123456789012345678",
        source_channel_id="111222333444555666",
        source_message_id="999888777666555444",
        source_author_id="444555666777888999",
        source_author_roles=["Trader", "Member"],
        raw_text="BUY AAPL @ 175.50 stop 172.00 target 181.00",
        profile="discord_equities",
    )


@pytest.fixture
def sample_trade_intent(sample_signal_event: SignalEvent) -> TradeIntent:
    return TradeIntent(
        correlation_id=sample_signal_event.correlation_id,
        source_signal_id=sample_signal_event.event_id,
        ticker="AAPL",
        asset_class=AssetClass.EQUITY,
        direction=Direction.LONG,
        entry_price=Decimal("175.50"),
        stop_price=Decimal("172.00"),
        take_profit_price=Decimal("181.00"),
        confidence=ConfidenceBucket.HIGH,
        template_name="standard_equity_buy",
        profile="discord_equities",
    )


@pytest.fixture
def sample_portfolio_snapshot(sample_trade_intent: TradeIntent) -> PortfolioSnapshot:
    return PortfolioSnapshot(
        correlation_id=sample_trade_intent.correlation_id,
        account_id="DU123456",
        net_liquidation=Decimal("500000"),
        sleeve_value=Decimal("100000"),
        cash_available=Decimal("80000"),
        positions=[
            PositionSnapshot(
                ticker="MSFT",
                quantity=Decimal("50"),
                market_value=Decimal("20000"),
                avg_cost=Decimal("380.00"),
                unrealized_pnl=Decimal("1000"),
            )
        ],
        open_position_count=1,
        daily_pnl=Decimal("500"),
        daily_pnl_pct=Decimal("0.005"),
    )


@pytest.fixture
def sample_risk_decision(sample_trade_intent: TradeIntent) -> RiskDecision:
    return RiskDecision(
        correlation_id=sample_trade_intent.correlation_id,
        source_intent_id=sample_trade_intent.event_id,
        outcome=RiskOutcome.APPROVED,
        approved_ticker="AAPL",
        approved_direction=Direction.LONG,
        approved_quantity=33,
        approved_entry_price=Decimal("175.50"),
        approved_stop_price=Decimal("172.00"),
        approved_take_profit=Decimal("181.00"),
        position_size_pct=Decimal("0.058"),
        risk_reward_ratio=Decimal("1.94"),
        profile="discord_equities",
    )


@pytest.fixture
def sample_execution_receipt(sample_risk_decision: RiskDecision) -> ExecutionReceipt:
    return ExecutionReceipt(
        correlation_id=sample_risk_decision.correlation_id,
        source_decision_id=sample_risk_decision.event_id,
        ibkr_order_id=10001,
        ibkr_perm_id=987654321,
        status=OrderStatus.FILLED,
        filled_quantity=Decimal("33"),
        avg_fill_price=Decimal("175.52"),
        commission=Decimal("1.00"),
        stop_order_id=10002,
        take_profit_order_id=10003,
        is_paper=True,
        profile="discord_equities",
    )
