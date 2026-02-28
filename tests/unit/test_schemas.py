"""Unit tests for all Pydantic schemas."""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from uuid import UUID

import pytest
from pydantic import ValidationError

from schemas.execution_receipt import ExecutionReceipt, OrderStatus
from schemas.portfolio_snapshot import PortfolioSnapshot, PositionSnapshot
from schemas.risk_decision import RiskDecision, RiskOutcome
from schemas.signal_event import SignalEvent
from schemas.trade_intent import AssetClass, ConfidenceBucket, Direction, TradeIntent

# ── SignalEvent ───────────────────────────────────────────────────────────────

class TestSignalEvent:
    def test_auto_fields_populated(self, sample_signal_event: SignalEvent) -> None:
        assert isinstance(sample_signal_event.event_id, UUID)
        assert isinstance(sample_signal_event.correlation_id, UUID)
        assert isinstance(sample_signal_event.created_at, datetime)
        assert sample_signal_event.created_at.tzinfo is not None

    def test_event_hash_empty_by_default(self, sample_signal_event: SignalEvent) -> None:
        assert sample_signal_event.event_hash == ""

    def test_distinct_events_have_distinct_ids(self) -> None:
        e1 = SignalEvent(
            source_guild_id="1", source_channel_id="2",
            source_message_id="3", source_author_id="4", raw_text="x",
        )
        e2 = SignalEvent(
            source_guild_id="1", source_channel_id="2",
            source_message_id="3", source_author_id="4", raw_text="x",
        )
        assert e1.event_id != e2.event_id

    def test_serialization_round_trip(self, sample_signal_event: SignalEvent) -> None:
        data = sample_signal_event.model_dump()
        restored = SignalEvent.model_validate(data)
        assert restored.event_id == sample_signal_event.event_id
        assert restored.raw_text == sample_signal_event.raw_text
        assert restored.source_author_roles == sample_signal_event.source_author_roles

    def test_required_fields_enforced(self) -> None:
        with pytest.raises(ValidationError):
            SignalEvent()  # type: ignore[call-arg]


# ── TradeIntent ───────────────────────────────────────────────────────────────

class TestTradeIntent:
    def test_direction_enum(self, sample_trade_intent: TradeIntent) -> None:
        assert sample_trade_intent.direction == Direction.LONG

    def test_asset_class_enum(self, sample_trade_intent: TradeIntent) -> None:
        assert sample_trade_intent.asset_class == AssetClass.EQUITY

    def test_confidence_bucket(self, sample_trade_intent: TradeIntent) -> None:
        assert sample_trade_intent.confidence == ConfidenceBucket.HIGH

    def test_decimal_prices(self, sample_trade_intent: TradeIntent) -> None:
        assert isinstance(sample_trade_intent.entry_price, Decimal)
        assert isinstance(sample_trade_intent.stop_price, Decimal)
        assert isinstance(sample_trade_intent.take_profit_price, Decimal)

    def test_optional_prices_default_none(self) -> None:
        from uuid import uuid4
        intent = TradeIntent(
            correlation_id=uuid4(),
            source_signal_id=uuid4(),
            ticker="SPY",
            asset_class=AssetClass.EQUITY,
            direction=Direction.SHORT,
            confidence=ConfidenceBucket.MEDIUM,
            template_name="market_short",
        )
        assert intent.entry_price is None
        assert intent.stop_price is None
        assert intent.take_profit_price is None

    def test_ticker_stored_as_given(self, sample_trade_intent: TradeIntent) -> None:
        assert sample_trade_intent.ticker == "AAPL"

    def test_correlation_id_propagated(
        self, sample_signal_event: SignalEvent, sample_trade_intent: TradeIntent
    ) -> None:
        assert sample_trade_intent.correlation_id == sample_signal_event.correlation_id


# ── PortfolioSnapshot ─────────────────────────────────────────────────────────

class TestPortfolioSnapshot:
    def test_position_list(self, sample_portfolio_snapshot: PortfolioSnapshot) -> None:
        assert len(sample_portfolio_snapshot.positions) == 1
        pos = sample_portfolio_snapshot.positions[0]
        assert isinstance(pos, PositionSnapshot)
        assert pos.ticker == "MSFT"

    def test_decimal_fields(self, sample_portfolio_snapshot: PortfolioSnapshot) -> None:
        assert isinstance(sample_portfolio_snapshot.net_liquidation, Decimal)
        assert isinstance(sample_portfolio_snapshot.sleeve_value, Decimal)

    def test_daily_pnl_pct_default(self) -> None:
        from uuid import uuid4
        snap = PortfolioSnapshot(
            correlation_id=uuid4(),
            account_id="DU000001",
            net_liquidation=Decimal("100000"),
            sleeve_value=Decimal("50000"),
            cash_available=Decimal("50000"),
        )
        assert snap.daily_pnl_pct == Decimal("0")
        assert snap.positions == []


# ── RiskDecision ──────────────────────────────────────────────────────────────

class TestRiskDecision:
    def test_approved_outcome(self, sample_risk_decision: RiskDecision) -> None:
        assert sample_risk_decision.outcome == RiskOutcome.APPROVED
        assert sample_risk_decision.approved_ticker == "AAPL"
        assert sample_risk_decision.approved_quantity == 33

    def test_rejected_decision_has_reasons(self) -> None:
        from uuid import uuid4
        decision = RiskDecision(
            correlation_id=uuid4(),
            source_intent_id=uuid4(),
            outcome=RiskOutcome.REJECTED,
            rejection_reasons=["position_size_exceeds_max", "drawdown_limit_breached"],
            position_size_pct=Decimal("0.12"),
        )
        assert len(decision.rejection_reasons) == 2
        assert decision.approved_ticker is None
        assert decision.approved_quantity is None

    def test_risk_reward_ratio(self, sample_risk_decision: RiskDecision) -> None:
        assert sample_risk_decision.risk_reward_ratio == Decimal("1.94")


# ── ExecutionReceipt ──────────────────────────────────────────────────────────

class TestExecutionReceipt:
    def test_is_paper_default_true(self) -> None:
        from uuid import uuid4
        receipt = ExecutionReceipt(
            correlation_id=uuid4(),
            source_decision_id=uuid4(),
            status=OrderStatus.SUBMITTED,
        )
        assert receipt.is_paper is True

    def test_filled_receipt(self, sample_execution_receipt: ExecutionReceipt) -> None:
        assert sample_execution_receipt.status == OrderStatus.FILLED
        assert sample_execution_receipt.filled_quantity == Decimal("33")
        assert sample_execution_receipt.avg_fill_price == Decimal("175.52")

    def test_bracket_order_ids(self, sample_execution_receipt: ExecutionReceipt) -> None:
        assert sample_execution_receipt.stop_order_id == 10002
        assert sample_execution_receipt.take_profit_order_id == 10003

    def test_error_receipt(self) -> None:
        from uuid import uuid4
        receipt = ExecutionReceipt(
            correlation_id=uuid4(),
            source_decision_id=uuid4(),
            status=OrderStatus.ERROR,
            error_message="Connection timeout",
        )
        assert receipt.error_message == "Connection timeout"
        assert receipt.avg_fill_price is None


# ── Cross-schema correlation ID chain ─────────────────────────────────────────

class TestCorrelationIdChain:
    def test_correlation_id_flows_through_pipeline(
        self,
        sample_signal_event: SignalEvent,
        sample_trade_intent: TradeIntent,
        sample_risk_decision: RiskDecision,
        sample_execution_receipt: ExecutionReceipt,
    ) -> None:
        cid = sample_signal_event.correlation_id
        assert sample_trade_intent.correlation_id == cid
        assert sample_risk_decision.correlation_id == cid
        assert sample_execution_receipt.correlation_id == cid

    def test_source_id_chain(
        self,
        sample_signal_event: SignalEvent,
        sample_trade_intent: TradeIntent,
        sample_risk_decision: RiskDecision,
        sample_execution_receipt: ExecutionReceipt,
    ) -> None:
        assert sample_trade_intent.source_signal_id == sample_signal_event.event_id
        assert sample_risk_decision.source_intent_id == sample_trade_intent.event_id
        assert sample_execution_receipt.source_decision_id == sample_risk_decision.event_id
