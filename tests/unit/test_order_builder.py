"""
Unit tests for agents/ibkr_executor/order_builder.py

BracketParams, build_bracket_params(), OptionOrderParams and
build_option_order_params() are pure functions — no IBKR connection required.
"""
from __future__ import annotations

import uuid
from decimal import Decimal

import pytest

from agents.ibkr_executor.order_builder import (
    build_bracket_params,
    build_option_order_params,
)
from schemas.risk_decision import RiskDecision, RiskOutcome
from schemas.trade_intent import AssetClass, Direction, OptionType

# ── helpers ─────────────────────────────────────────────────────────────────


def _approved(
    direction: Direction = Direction.LONG,
    quantity: int = 33,
    entry: Decimal | None = Decimal("175.50"),
    stop: Decimal | None = Decimal("172.00"),
    tp: Decimal | None = Decimal("181.00"),
) -> RiskDecision:
    """Build a minimal APPROVED RiskDecision with controllable fields."""
    import uuid

    cid = uuid.uuid4()
    return RiskDecision(
        correlation_id=cid,
        source_intent_id=uuid.uuid4(),
        outcome=RiskOutcome.APPROVED,
        approved_ticker="AAPL",
        approved_direction=direction,
        approved_quantity=quantity,
        approved_entry_price=entry,
        approved_stop_price=stop,
        approved_take_profit=tp,
        position_size_pct=Decimal("0.058"),
        profile="discord_equities",
    )


# ── action / direction ───────────────────────────────────────────────────────


class TestAction:
    def test_long_direction_yields_buy(self) -> None:
        params = build_bracket_params(_approved(direction=Direction.LONG))
        assert params.action == "BUY"

    def test_short_direction_yields_sell(self) -> None:
        params = build_bracket_params(_approved(direction=Direction.SHORT))
        assert params.action == "SELL"


# ── quantity ─────────────────────────────────────────────────────────────────


class TestQuantity:
    def test_quantity_propagated(self) -> None:
        params = build_bracket_params(_approved(quantity=50))
        assert params.quantity == 50

    def test_quantity_one(self) -> None:
        params = build_bracket_params(_approved(quantity=1))
        assert params.quantity == 1


# ── prices ───────────────────────────────────────────────────────────────────


class TestPrices:
    def test_entry_price_converted_to_float(self) -> None:
        params = build_bracket_params(_approved(entry=Decimal("175.50")))
        assert params.entry_price == pytest.approx(175.50)

    def test_stop_price_converted_to_float(self) -> None:
        params = build_bracket_params(_approved(stop=Decimal("172.00")))
        assert params.stop_price == pytest.approx(172.00)

    def test_take_profit_converted_to_float(self) -> None:
        params = build_bracket_params(_approved(tp=Decimal("181.00")))
        assert params.take_profit_price == pytest.approx(181.00)

    def test_none_entry_stays_none(self) -> None:
        params = build_bracket_params(_approved(entry=None))
        assert params.entry_price is None

    def test_none_stop_stays_none(self) -> None:
        params = build_bracket_params(_approved(stop=None))
        assert params.stop_price is None

    def test_none_take_profit_stays_none(self) -> None:
        params = build_bracket_params(_approved(tp=None))
        assert params.take_profit_price is None

    def test_all_none_prices(self) -> None:
        params = build_bracket_params(_approved(entry=None, stop=None, tp=None))
        assert params.entry_price is None
        assert params.stop_price is None
        assert params.take_profit_price is None


# ── order_ref ────────────────────────────────────────────────────────────────


class TestOrderRef:
    def test_order_ref_max_20_chars(self) -> None:
        params = build_bracket_params(_approved())
        assert len(params.order_ref) <= 20

    def test_order_ref_derived_from_correlation_id(self) -> None:
        decision = _approved()
        params = build_bracket_params(decision)
        # order_ref is first 20 chars of correlation_id with hyphens removed
        expected = str(decision.correlation_id).replace("-", "")[:20]
        assert params.order_ref == expected

    def test_order_ref_no_hyphens(self) -> None:
        params = build_bracket_params(_approved())
        assert "-" not in params.order_ref


# ── validation / errors ──────────────────────────────────────────────────────


class TestValidation:
    def test_raises_when_direction_is_none(self) -> None:
        import uuid

        decision = RiskDecision(
            correlation_id=uuid.uuid4(),
            source_intent_id=uuid.uuid4(),
            outcome=RiskOutcome.APPROVED,
            approved_ticker="AAPL",
            approved_direction=None,  # missing
            approved_quantity=10,
            position_size_pct=Decimal("0.05"),
            profile="discord_equities",
        )
        with pytest.raises(ValueError, match="approved_direction"):
            build_bracket_params(decision)

    def test_raises_when_quantity_is_none(self) -> None:
        import uuid

        decision = RiskDecision(
            correlation_id=uuid.uuid4(),
            source_intent_id=uuid.uuid4(),
            outcome=RiskOutcome.APPROVED,
            approved_ticker="AAPL",
            approved_direction=Direction.LONG,
            approved_quantity=None,  # missing
            position_size_pct=Decimal("0.05"),
            profile="discord_equities",
        )
        with pytest.raises(ValueError, match="approved_quantity"):
            build_bracket_params(decision)


# ── immutability ─────────────────────────────────────────────────────────────


class TestImmutability:
    def test_bracket_params_is_frozen(self) -> None:
        params = build_bracket_params(_approved())
        with pytest.raises((AttributeError, TypeError)):
            params.action = "SELL"  # type: ignore[misc]

    def test_two_calls_same_decision_produce_equal_params(self) -> None:
        decision = _approved()
        assert build_bracket_params(decision) == build_bracket_params(decision)


# ── short entry/stop examples ────────────────────────────────────────────────


class TestShortBracket:
    def test_short_long_entry_above_stop(self) -> None:
        """For a short, entry is above stop (stop is a buy-stop above entry)."""
        params = build_bracket_params(
            _approved(
                direction=Direction.SHORT,
                entry=Decimal("100.00"),
                stop=Decimal("103.00"),
                tp=Decimal("95.00"),
            )
        )
        assert params.action == "SELL"
        assert params.entry_price == pytest.approx(100.00)
        assert params.stop_price == pytest.approx(103.00)
        assert params.take_profit_price == pytest.approx(95.00)


# ── build_option_order_params ─────────────────────────────────────────────────


def _option_decision(
    direction: Direction = Direction.LONG,
    option_type: OptionType = OptionType.CALL,
) -> RiskDecision:
    from datetime import date
    return RiskDecision(
        correlation_id=uuid.uuid4(),
        source_intent_id=uuid.uuid4(),
        outcome=RiskOutcome.APPROVED,
        approved_ticker="IRDM",
        approved_direction=direction,
        approved_asset_class=AssetClass.OPTION,
        approved_option_type=option_type,
        approved_strike=Decimal("22.5"),
        approved_expiry=date(2025, 7, 17),
        approved_entry_price=Decimal("3.35"),
        position_size_pct=Decimal("0.075"),
        profile="discord_equities",
    )


class TestBuildOptionOrderParams:
    def test_long_call_is_buy(self) -> None:
        params = build_option_order_params(
            _option_decision(direction=Direction.LONG), Decimal("3.35"), 22
        )
        assert params.action == "BUY"

    def test_short_is_sell(self) -> None:
        params = build_option_order_params(
            _option_decision(direction=Direction.SHORT), Decimal("3.35"), 5
        )
        assert params.action == "SELL"

    def test_quantity_propagated(self) -> None:
        params = build_option_order_params(
            _option_decision(), Decimal("3.35"), 22
        )
        assert params.quantity == 22

    def test_limit_price_converted_to_float(self) -> None:
        params = build_option_order_params(
            _option_decision(), Decimal("3.35"), 22
        )
        assert params.limit_price == pytest.approx(3.35)

    def test_order_ref_max_20_chars(self) -> None:
        params = build_option_order_params(
            _option_decision(), Decimal("3.35"), 22
        )
        assert len(params.order_ref) <= 20

    def test_raises_when_direction_none(self) -> None:
        decision = RiskDecision(
            correlation_id=uuid.uuid4(),
            source_intent_id=uuid.uuid4(),
            outcome=RiskOutcome.APPROVED,
            approved_direction=None,
            position_size_pct=Decimal("0.075"),
            profile="discord_equities",
        )
        with pytest.raises(ValueError, match="approved_direction"):
            build_option_order_params(decision, Decimal("3.35"), 5)

    def test_option_order_params_is_frozen(self) -> None:
        params = build_option_order_params(
            _option_decision(), Decimal("3.35"), 22
        )
        with pytest.raises((AttributeError, TypeError)):
            params.action = "SELL"  # type: ignore[misc]
