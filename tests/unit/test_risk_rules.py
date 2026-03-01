"""
Unit tests for risk_officer/rules.py — confidence-tier sizing and evaluation.

No IBKR connection required.  All inputs are synthetic.
"""
from __future__ import annotations

from decimal import Decimal
from uuid import uuid4

from agents.risk_officer.circuit_breaker import CircuitBreaker
from agents.risk_officer.rules import (
    CONFIDENCE_TIER_PCT,
    SizingResult,
    calculate_position_size,
    check_drawdown_ok,
    check_position_count,
    check_total_exposure,
    evaluate_trade,
    tier_position_pct,
)
from schemas.portfolio_snapshot import PortfolioSnapshot, PositionSnapshot
from schemas.risk_decision import RiskOutcome
from schemas.trade_intent import (
    AssetClass,
    ConfidenceBucket,
    Direction,
    OptionType,
    TradeIntent,
)

# ── Test data factories ───────────────────────────────────────────────────────


def _intent(
    ticker: str = "AAPL",
    direction: Direction = Direction.LONG,
    entry: str | None = "175.50",
    stop: str | None = "172.00",
    target: str | None = "181.00",
    confidence: ConfidenceBucket = ConfidenceBucket.HIGH,
    template: str = "long_entry_stop_target",
    option_type: OptionType | None = None,
    strike: str | None = None,
    expiry_str: str | None = None,
) -> TradeIntent:
    from datetime import date
    return TradeIntent(
        correlation_id=uuid4(),
        source_signal_id=uuid4(),
        ticker=ticker,
        asset_class=AssetClass.OPTION if option_type else AssetClass.EQUITY,
        direction=direction,
        entry_price=Decimal(entry) if entry else None,
        stop_price=Decimal(stop) if stop else None,
        take_profit_price=Decimal(target) if target else None,
        confidence=confidence,
        template_name=template,
        option_type=option_type,
        strike=Decimal(strike) if strike else None,
        expiry=date.fromisoformat(expiry_str) if expiry_str else None,
        profile="discord_equities",
    )


def _portfolio(
    sleeve: str = "100000",
    positions: list[PositionSnapshot] | None = None,
    open_count: int = 0,
    daily_pnl_pct: str = "0",
) -> PortfolioSnapshot:
    return PortfolioSnapshot(
        correlation_id=uuid4(),
        account_id="DU123456",
        net_liquidation=Decimal("500000"),
        sleeve_value=Decimal(sleeve),
        cash_available=Decimal("80000"),
        positions=positions or [],
        open_position_count=open_count,
        daily_pnl=Decimal("0"),
        daily_pnl_pct=Decimal(daily_pnl_pct),
    )


def _limits() -> dict[str, object]:
    return {
        "max_open_positions": 10,
        "max_daily_drawdown_pct": Decimal("0.05"),
    }


def _evaluate(**overrides: object) -> object:
    """Convenience: evaluate default HIGH-confidence AAPL LONG intent."""
    kwargs: dict[str, object] = {
        "intent": _intent(),
        "portfolio": _portfolio(),
        **_limits(),
    }
    kwargs.update(overrides)
    return evaluate_trade(**kwargs)  # type: ignore[arg-type]


# ── Compact sizing alias ──────────────────────────────────────────────────────


def _sz(
    entry: str,
    tier: str = "0.075",
    sleeve: str = "100000",
    stop: str | None = None,
    target: str | None = None,
    is_option: bool = False,
) -> SizingResult | None:
    return calculate_position_size(
        entry_price=Decimal(entry),
        tier_pct=Decimal(tier),
        sleeve_value=Decimal(sleeve),
        is_option=is_option,
        stop_price=Decimal(stop) if stop else None,
        take_profit_price=Decimal(target) if target else None,
    )


# ── tier_position_pct ─────────────────────────────────────────────────────────


class TestTierPositionPct:
    def test_high_tier(self) -> None:
        assert tier_position_pct(ConfidenceBucket.HIGH) == Decimal("0.075")

    def test_medium_tier(self) -> None:
        assert tier_position_pct(ConfidenceBucket.MEDIUM) == Decimal("0.075")

    def test_low_tier(self) -> None:
        assert tier_position_pct(ConfidenceBucket.LOW) == Decimal("0.050")

    def test_confidence_tier_pct_dict_contains_all_buckets(self) -> None:
        for bucket in ConfidenceBucket:
            assert bucket in CONFIDENCE_TIER_PCT


# ── calculate_position_size ───────────────────────────────────────────────────


class TestCalculatePositionSize:
    def test_returns_sizing_result(self) -> None:
        r = _sz("175.50")
        assert isinstance(r, SizingResult)

    def test_quantity_is_positive(self) -> None:
        r = _sz("175.50")
        assert r is not None
        assert r.quantity > 0

    def test_position_pct_equals_tier(self) -> None:
        r = _sz("175.50", tier="0.075")
        assert r is not None
        assert r.position_size_pct == Decimal("0.075")

    def test_low_tier_uses_5pct(self) -> None:
        r = _sz("175.50", tier="0.05")
        assert r is not None
        assert r.position_size_pct == Decimal("0.05")

    def test_position_value_consistency_shares(self) -> None:
        r = _sz("100.00")
        assert r is not None
        assert r.position_value == Decimal(str(r.quantity)) * Decimal("100.00")

    def test_option_quantity_uses_100_multiplier(self) -> None:
        """$100k × 7.5% = $7500; premium $3.35 × 100 = $335/contract → 22 contracts."""
        r = _sz("3.35", tier="0.075", is_option=True)
        assert r is not None
        expected_contracts = int(Decimal("7500") / (Decimal("3.35") * 100))
        assert r.quantity == expected_contracts

    def test_option_position_value_uses_contract_cost(self) -> None:
        r = _sz("3.35", tier="0.075", is_option=True)
        assert r is not None
        assert r.position_value == Decimal(str(r.quantity)) * Decimal("3.35") * 100

    def test_rr_ratio_calculated_when_stop_and_target(self) -> None:
        r = _sz("175.50", stop="172.00", target="181.00")
        assert r is not None
        assert r.risk_reward_ratio is not None
        assert r.risk_reward_ratio > 1

    def test_rr_ratio_none_without_target(self) -> None:
        r = _sz("175.50", stop="172.00")
        assert r is not None
        assert r.risk_reward_ratio is None

    def test_returns_none_for_zero_entry(self) -> None:
        assert _sz("0") is None

    def test_returns_none_for_zero_sleeve(self) -> None:
        assert _sz("175.50", sleeve="0") is None

    def test_returns_none_when_quantity_rounds_to_zero(self) -> None:
        """$50k stock, $1k sleeve × 7.5% = $75 budget → 0 shares."""
        assert _sz("50000", sleeve="1000") is None

    def test_short_position_sizing(self) -> None:
        """SHORT: stop above entry; sizing should still work."""
        r = calculate_position_size(
            entry_price=Decimal("260.00"),
            tier_pct=Decimal("0.075"),
            sleeve_value=Decimal("100000"),
            stop_price=Decimal("265.00"),
            take_profit_price=Decimal("250.00"),
        )
        assert r is not None
        assert r.quantity > 0


# ── check_drawdown_ok ─────────────────────────────────────────────────────────


class TestCheckDrawdownOk:
    def test_no_loss_ok(self) -> None:
        assert check_drawdown_ok(Decimal("0"), Decimal("0.05")) is True

    def test_small_gain_ok(self) -> None:
        assert check_drawdown_ok(Decimal("0.02"), Decimal("0.05")) is True

    def test_small_loss_ok(self) -> None:
        assert check_drawdown_ok(Decimal("-0.03"), Decimal("0.05")) is True

    def test_exactly_at_threshold_ok(self) -> None:
        assert check_drawdown_ok(Decimal("-0.05"), Decimal("0.05")) is True

    def test_just_over_threshold_halted(self) -> None:
        assert check_drawdown_ok(Decimal("-0.0501"), Decimal("0.05")) is False

    def test_large_loss_halted(self) -> None:
        assert check_drawdown_ok(Decimal("-0.20"), Decimal("0.05")) is False


# ── check_position_count ──────────────────────────────────────────────────────


class TestCheckPositionCount:
    def test_zero_positions_allowed(self) -> None:
        assert check_position_count(0, 10) is True

    def test_one_below_max_allowed(self) -> None:
        assert check_position_count(9, 10) is True

    def test_at_max_rejected(self) -> None:
        assert check_position_count(10, 10) is False

    def test_above_max_rejected(self) -> None:
        assert check_position_count(15, 10) is False


# ── check_total_exposure ──────────────────────────────────────────────────────


class TestCheckTotalExposure:
    def _pos(self, value: str) -> PositionSnapshot:
        return PositionSnapshot(
            ticker="X", quantity=Decimal("1"),
            market_value=Decimal(value), avg_cost=Decimal("1"),
            unrealized_pnl=Decimal("0"),
        )

    def test_empty_portfolio_allowed(self) -> None:
        assert check_total_exposure([], Decimal("5000"), Decimal("100000")) is True

    def test_within_limit_allowed(self) -> None:
        positions = [self._pos("10000"), self._pos("20000")]
        assert check_total_exposure(positions, Decimal("5000"), Decimal("100000")) is True

    def test_exactly_at_limit_allowed(self) -> None:
        positions = [self._pos("75000")]
        assert check_total_exposure(positions, Decimal("5000"), Decimal("100000")) is True

    def test_just_over_limit_rejected(self) -> None:
        positions = [self._pos("75000")]
        assert check_total_exposure(positions, Decimal("6000"), Decimal("100000")) is False

    def test_uses_absolute_value_for_shorts(self) -> None:
        positions = [self._pos("-50000")]
        assert check_total_exposure(positions, Decimal("35000"), Decimal("100000")) is False


# ── evaluate_trade ────────────────────────────────────────────────────────────


class TestEvaluateTrade:
    # ── APPROVED paths ────────────────────────────────────────────────────────

    def test_high_confidence_approved(self) -> None:
        d = _evaluate()
        # HIGH LONG → smart selector
        assert d.outcome == RiskOutcome.APPROVED  # type: ignore[attr-defined]

    def test_medium_confidence_approved(self) -> None:
        """MEDIUM is now APPROVED with shares (not NEEDS_APPROVAL)."""
        d = evaluate_trade(
            _intent(confidence=ConfidenceBucket.MEDIUM),
            _portfolio(),
            **_limits(),  # type: ignore[arg-type]
        )
        assert d.outcome == RiskOutcome.APPROVED

    def test_low_confidence_approved(self) -> None:
        """LOW is now APPROVED with 5% shares (not REJECTED)."""
        d = evaluate_trade(
            _intent(confidence=ConfidenceBucket.LOW),
            _portfolio(),
            **_limits(),  # type: ignore[arg-type]
        )
        assert d.outcome == RiskOutcome.APPROVED

    def test_approved_has_correct_ticker(self) -> None:
        d = evaluate_trade(_intent(ticker="TSLA"), _portfolio(), **_limits())  # type: ignore[arg-type]
        assert d.approved_ticker == "TSLA"

    def test_approved_preserves_stop(self) -> None:
        d = evaluate_trade(_intent(stop="172.00"), _portfolio(), **_limits())  # type: ignore[arg-type]
        assert d.approved_stop_price == Decimal("172.00")

    def test_approved_preserves_correlation_id(self) -> None:
        intent = _intent()
        d = evaluate_trade(intent, _portfolio(), **_limits())  # type: ignore[arg-type]
        assert d.correlation_id == intent.correlation_id

    # ── Confidence-tier sizing ─────────────────────────────────────────────────

    def test_high_tier_pct_is_7p5(self) -> None:
        d = evaluate_trade(
            _intent(confidence=ConfidenceBucket.HIGH, direction=Direction.SHORT),
            _portfolio(), **_limits()  # type: ignore[arg-type]
        )
        assert d.position_size_pct == Decimal("0.075")

    def test_medium_tier_pct_is_7p5(self) -> None:
        d = evaluate_trade(
            _intent(confidence=ConfidenceBucket.MEDIUM),
            _portfolio(), **_limits()  # type: ignore[arg-type]
        )
        assert d.position_size_pct == Decimal("0.075")

    def test_low_tier_pct_is_5(self) -> None:
        d = evaluate_trade(
            _intent(confidence=ConfidenceBucket.LOW),
            _portfolio(), **_limits()  # type: ignore[arg-type]
        )
        assert d.position_size_pct == Decimal("0.050")

    # ── Instrument routing ─────────────────────────────────────────────────────

    def test_high_long_uses_smart_selector(self) -> None:
        d = evaluate_trade(
            _intent(confidence=ConfidenceBucket.HIGH, direction=Direction.LONG),
            _portfolio(), **_limits()  # type: ignore[arg-type]
        )
        assert d.use_smart_options_selector is True
        assert d.approved_asset_class is not None
        from schemas.trade_intent import AssetClass
        assert d.approved_asset_class == AssetClass.OPTION

    def test_high_short_does_not_use_selector(self) -> None:
        """HIGH SHORT → shares, not auto-puts."""
        d = evaluate_trade(
            _intent(confidence=ConfidenceBucket.HIGH, direction=Direction.SHORT),
            _portfolio(), **_limits()  # type: ignore[arg-type]
        )
        assert d.use_smart_options_selector is False
        from schemas.trade_intent import AssetClass
        assert d.approved_asset_class == AssetClass.EQUITY

    def test_medium_long_is_shares(self) -> None:
        d = evaluate_trade(
            _intent(confidence=ConfidenceBucket.MEDIUM),
            _portfolio(), **_limits()  # type: ignore[arg-type]
        )
        assert d.use_smart_options_selector is False
        from schemas.trade_intent import AssetClass
        assert d.approved_asset_class == AssetClass.EQUITY

    def test_explicit_option_overrides_routing(self) -> None:
        """Explicit option_type → option path, even for LOW confidence."""
        intent = _intent(
            confidence=ConfidenceBucket.LOW,
            entry="3.35",   # option premium, not stock price
            option_type=OptionType.CALL,
            strike="22.5",
            expiry_str="2025-07-17",
        )
        d = evaluate_trade(intent, _portfolio(), **_limits())  # type: ignore[arg-type]
        assert d.outcome == RiskOutcome.APPROVED
        assert d.use_smart_options_selector is False
        from schemas.trade_intent import AssetClass
        assert d.approved_asset_class == AssetClass.OPTION

    def test_smart_selector_sets_budget_not_quantity(self) -> None:
        d = evaluate_trade(
            _intent(confidence=ConfidenceBucket.HIGH, direction=Direction.LONG),
            _portfolio(), **_limits()  # type: ignore[arg-type]
        )
        assert d.approved_budget is not None
        # Budget should be 7.5% of $100k = $7500
        assert d.approved_budget == Decimal("7500")
        assert d.approved_quantity is None

    def test_equity_order_has_quantity_not_budget(self) -> None:
        d = evaluate_trade(
            _intent(confidence=ConfidenceBucket.MEDIUM),
            _portfolio(), **_limits()  # type: ignore[arg-type]
        )
        assert d.approved_quantity is not None
        assert d.approved_quantity > 0
        assert d.approved_budget is None

    def test_low_equity_quantity_at_5pct(self) -> None:
        """LOW confidence: $100k × 5% = $5000; $175.50/share → ~28 shares."""
        d = evaluate_trade(
            _intent(confidence=ConfidenceBucket.LOW),
            _portfolio(), **_limits()  # type: ignore[arg-type]
        )
        assert d.approved_quantity is not None
        expected = int(Decimal("5000") / Decimal("175.50"))
        assert d.approved_quantity == expected

    # ── Rejection paths ───────────────────────────────────────────────────────

    def test_manually_halted_rejected(self) -> None:
        d = evaluate_trade(
            _intent(), _portfolio(), **_limits(), is_manually_halted=True  # type: ignore[arg-type]
        )
        assert d.outcome == RiskOutcome.REJECTED
        assert any("manually_halted" in r for r in d.rejection_reasons)

    def test_drawdown_breached_rejected(self) -> None:
        d = evaluate_trade(
            _intent(), _portfolio(daily_pnl_pct="-0.06"), **_limits()  # type: ignore[arg-type]
        )
        assert d.outcome == RiskOutcome.REJECTED
        assert any("drawdown_breached" in r for r in d.rejection_reasons)

    def test_max_positions_rejected(self) -> None:
        d = evaluate_trade(
            _intent(),
            _portfolio(open_count=10),
            **_limits(),  # type: ignore[arg-type]
        )
        assert d.outcome == RiskOutcome.REJECTED
        assert any("max_positions" in r for r in d.rejection_reasons)

    def test_exposure_exceeded_rejected(self) -> None:
        existing = [
            PositionSnapshot(
                ticker="MSFT", quantity=Decimal("100"),
                market_value=Decimal("79000"),
                avg_cost=Decimal("790"), unrealized_pnl=Decimal("0"),
            )
        ]
        d = evaluate_trade(_intent(), _portfolio(positions=existing), **_limits())  # type: ignore[arg-type]
        assert d.outcome == RiskOutcome.REJECTED
        assert any("exposure_limit_exceeded" in r for r in d.rejection_reasons)

    def test_zero_quantity_equity_rejected(self) -> None:
        """Price too high for sleeve budget → quantity=0 → REJECTED."""
        d = evaluate_trade(
            _intent(entry="50000", stop="49000", confidence=ConfidenceBucket.MEDIUM),
            _portfolio(sleeve="1000"),
            **_limits(),  # type: ignore[arg-type]
        )
        assert d.outcome == RiskOutcome.REJECTED
        assert any("position_size_zero" in r for r in d.rejection_reasons)

    # ── Priority ordering ─────────────────────────────────────────────────────

    def test_manual_halt_beats_drawdown(self) -> None:
        d = evaluate_trade(
            _intent(),
            _portfolio(daily_pnl_pct="-0.06"),
            **_limits(),  # type: ignore[arg-type]
            is_manually_halted=True,
        )
        assert any("manually_halted" in r for r in d.rejection_reasons)

    def test_drawdown_checked_before_position_count(self) -> None:
        d = evaluate_trade(
            _intent(),
            _portfolio(daily_pnl_pct="-0.06", open_count=10),
            **_limits(),  # type: ignore[arg-type]
        )
        assert any("drawdown_breached" in r for r in d.rejection_reasons)

    # ── CircuitBreaker integration ────────────────────────────────────────────

    def test_circuit_breaker_starts_unhalted(self) -> None:
        cb = CircuitBreaker()
        assert cb.is_halted is False

    def test_circuit_breaker_halt_and_resume(self) -> None:
        cb = CircuitBreaker()
        cb.halt("test")
        assert cb.is_halted is True
        cb.resume()
        assert cb.is_halted is False

    def test_circuit_breaker_halt_rejects_trades(self) -> None:
        cb = CircuitBreaker()
        cb.halt("daily_drawdown")
        d = evaluate_trade(
            _intent(), _portfolio(), **_limits(), is_manually_halted=cb.is_halted  # type: ignore[arg-type]
        )
        assert d.outcome == RiskOutcome.REJECTED

    # ── R/R ratio ─────────────────────────────────────────────────────────────

    def test_rr_ratio_computed_for_equity(self) -> None:
        d = evaluate_trade(
            _intent(
                confidence=ConfidenceBucket.MEDIUM,
                entry="175.50", stop="172.00", target="181.00"
            ),
            _portfolio(), **_limits()  # type: ignore[arg-type]
        )
        assert d.risk_reward_ratio is not None
        assert d.risk_reward_ratio > 1

    def test_no_rr_ratio_when_smart_selector(self) -> None:
        d = evaluate_trade(
            _intent(confidence=ConfidenceBucket.HIGH, direction=Direction.LONG),
            _portfolio(), **_limits()  # type: ignore[arg-type]
        )
        # Smart selector: no entry price known → no R/R
        assert d.risk_reward_ratio is None


# ── Sizing clamp ─────────────────────────────────────────────────────────────


class TestSizingClamp:
    """Tier % must be clamped to [min_position_pct, max_position_pct]."""

    def test_high_tier_clamped_to_max(self) -> None:
        """7.5% tier clamped to 7.0% when max_position_pct=0.07."""
        d = evaluate_trade(
            _intent(confidence=ConfidenceBucket.MEDIUM,
                    entry="175.50", stop="172.00", target="181.00"),
            _portfolio(sleeve="100000"),
            max_open_positions=10,
            max_daily_drawdown_pct=Decimal("0.05"),
            max_position_pct=Decimal("0.07"),
        )
        assert d.outcome == RiskOutcome.APPROVED
        # 7.5% clamped to 7.0% → position_size_pct should be 0.07
        assert d.position_size_pct == Decimal("0.07")

    def test_low_tier_clamped_to_min(self) -> None:
        """5.0% tier raised to 6.0% when min_position_pct=0.06."""
        d = evaluate_trade(
            _intent(confidence=ConfidenceBucket.LOW,
                    entry="175.50", stop="172.00", target="181.00"),
            _portfolio(sleeve="100000"),
            max_open_positions=10,
            max_daily_drawdown_pct=Decimal("0.05"),
            min_position_pct=Decimal("0.06"),
        )
        assert d.outcome == RiskOutcome.APPROVED
        assert d.position_size_pct == Decimal("0.06")

    def test_no_clamp_when_bounds_none(self) -> None:
        """When min/max are None the raw tier % passes through unchanged."""
        d = evaluate_trade(
            _intent(confidence=ConfidenceBucket.MEDIUM,
                    entry="175.50", stop="172.00", target="181.00"),
            _portfolio(sleeve="100000"),
            max_open_positions=10,
            max_daily_drawdown_pct=Decimal("0.05"),
        )
        assert d.outcome == RiskOutcome.APPROVED
        # MEDIUM tier is 7.5% — no clamp applied
        assert d.position_size_pct == Decimal("0.075")

    def test_clamp_does_not_exceed_max_position_pct(self) -> None:
        """Position size pct in the decision must never exceed max_position_pct."""
        max_pct = Decimal("0.04")
        d = evaluate_trade(
            _intent(confidence=ConfidenceBucket.HIGH,
                    direction=Direction.SHORT,
                    entry="175.50", stop="178.00", target="170.00"),
            _portfolio(sleeve="100000"),
            max_open_positions=10,
            max_daily_drawdown_pct=Decimal("0.05"),
            max_position_pct=max_pct,
        )
        assert d.outcome == RiskOutcome.APPROVED
        assert d.position_size_pct <= max_pct

