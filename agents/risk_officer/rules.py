"""
Risk gate — pure, stateless sizing and evaluation functions.

No I/O.  No side effects.  100% unit-testable with synthetic inputs.

Confidence-tier sizing
----------------------
Position sizing is driven by the signal's confidence bucket, not by stop
distance.  Each tier allocates a flat fraction of the trading sleeve:

    HIGH   → 7.5 %   (calls eligible on LONG direction)
    MEDIUM → 7.5 %   (shares only)
    LOW    →  5.0 %   (shares only)

For options (premium-based sizing):
    position_value = sleeve_value × tier_pct
    contracts      = floor(position_value / (premium_per_share × 100))

For shares:
    position_value = sleeve_value × tier_pct
    quantity       = floor(position_value / entry_price)

Instrument routing
------------------
HIGH LONG + no explicit option contract  → SmartOptionsSelector (executor)
Explicit option contract (any confidence) → trade that contract
All other cases                          → shares (equity)

SHORT signals never auto-select puts.  If the signal includes an explicit
put contract, that contract is honoured.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_DOWN, Decimal

from schemas.portfolio_snapshot import PortfolioSnapshot, PositionSnapshot
from schemas.risk_decision import RiskDecision, RiskOutcome
from schemas.trade_intent import AssetClass, ConfidenceBucket, Direction, TradeIntent

# ── Module-level constants ────────────────────────────────────────────────────

#: Flat position-size fraction per confidence tier.
CONFIDENCE_TIER_PCT: dict[ConfidenceBucket, Decimal] = {
    ConfidenceBucket.HIGH: Decimal("0.075"),
    ConfidenceBucket.MEDIUM: Decimal("0.075"),
    ConfidenceBucket.LOW: Decimal("0.050"),
}

MAX_TOTAL_EXPOSURE_PCT: Decimal = Decimal("0.80")  # max 80% of sleeve deployed


# ── Sizing result ─────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class SizingResult:
    """Output of calculate_position_size."""

    quantity: int                         # shares or contracts
    position_size_pct: Decimal            # fraction of sleeve, e.g. Decimal("0.075")
    position_value: Decimal               # dollars deployed
    risk_reward_ratio: Decimal | None     # set when both stop and target present


# ── Pure check functions ──────────────────────────────────────────────────────


def check_drawdown_ok(daily_pnl_pct: Decimal, max_drawdown_pct: Decimal) -> bool:
    """
    Return True if today's P&L is above the halt threshold.
    daily_pnl_pct is negative when the account is losing money.
    """
    return daily_pnl_pct >= -abs(max_drawdown_pct)


def check_position_count(current_count: int, max_positions: int) -> bool:
    """Return True if another position can be opened."""
    return current_count < max_positions


def check_total_exposure(
    positions: list[PositionSnapshot],
    new_position_value: Decimal,
    sleeve_value: Decimal,
    max_exposure_pct: Decimal = MAX_TOTAL_EXPOSURE_PCT,
) -> bool:
    """
    Return True if adding new_position_value keeps total exposure ≤ limit.
    Uses absolute market value to handle both long and short positions.
    """
    current_exposure = sum(abs(p.market_value) for p in positions)
    return (current_exposure + new_position_value) <= sleeve_value * max_exposure_pct


# ── Tier helper ───────────────────────────────────────────────────────────────


def tier_position_pct(confidence: ConfidenceBucket) -> Decimal:
    """Return the flat allocation fraction for a given confidence tier."""
    return CONFIDENCE_TIER_PCT[confidence]


# ── Sizing ────────────────────────────────────────────────────────────────────


def calculate_position_size(
    entry_price: Decimal,
    tier_pct: Decimal,
    sleeve_value: Decimal,
    is_option: bool = False,
    stop_price: Decimal | None = None,
    take_profit_price: Decimal | None = None,
) -> SizingResult | None:
    """
    Calculate quantity and position size using flat tier-based allocation.

    For options (is_option=True):
        entry_price is the premium per share.
        One contract covers 100 shares, so:
            contracts = floor(budget / (premium × 100))
            actual_value = contracts × premium × 100

    For shares (is_option=False):
        quantity = floor(budget / entry_price)
        actual_value = quantity × entry_price

    Returns None when:
        - entry_price ≤ 0
        - sleeve_value ≤ 0
        - resulting quantity rounds to 0

    stop_price and take_profit_price are only used to compute risk_reward_ratio.
    """
    if entry_price <= 0 or sleeve_value <= 0:
        return None

    budget = sleeve_value * tier_pct

    if is_option:
        contract_cost = entry_price * 100  # premium × 100 shares per contract
        if contract_cost <= 0:
            return None
        quantity = int((budget / contract_cost).to_integral_value(rounding=ROUND_DOWN))
        actual_value = Decimal(str(quantity)) * contract_cost
    else:
        quantity = int((budget / entry_price).to_integral_value(rounding=ROUND_DOWN))
        actual_value = Decimal(str(quantity)) * entry_price

    if quantity == 0:
        return None

    # ── R/R ratio ─────────────────────────────────────────────────────────────
    rr: Decimal | None = None
    if stop_price is not None and take_profit_price is not None:
        stop_dist = abs(entry_price - stop_price)
        profit_dist = abs(take_profit_price - entry_price)
        if stop_dist > 0:
            rr = (profit_dist / stop_dist).quantize(Decimal("0.01"))

    return SizingResult(
        quantity=quantity,
        position_size_pct=tier_pct,
        position_value=actual_value,
        risk_reward_ratio=rr,
    )


# ── Instrument routing ────────────────────────────────────────────────────────


def _route_instrument(intent: TradeIntent) -> tuple[AssetClass, bool]:
    """
    Determine the target asset class and whether to use the smart selector.

    Returns (asset_class, use_smart_options_selector).

    Routing logic:
        1. Explicit option contract (option_type set by parser) → OPTION, no selector
        2. HIGH confidence LONG without explicit option            → OPTION, smart selector
        3. All other cases                                         → EQUITY, no selector
    """
    if intent.option_type is not None:
        return AssetClass.OPTION, False

    if intent.direction == Direction.LONG and intent.confidence == ConfidenceBucket.HIGH:
        return AssetClass.OPTION, True

    return AssetClass.EQUITY, False


# ── Top-level evaluator ───────────────────────────────────────────────────────


def evaluate_trade(
    intent: TradeIntent,
    portfolio: PortfolioSnapshot,
    max_open_positions: int,
    max_daily_drawdown_pct: Decimal,
    is_manually_halted: bool = False,
) -> RiskDecision:
    """
    Evaluate a TradeIntent against the current portfolio and configured limits.

    All confidence levels (HIGH, MEDIUM, LOW) produce APPROVED decisions when
    portfolio checks pass.  Only hard circuit-breaker conditions produce
    REJECTED outcomes.

    Evaluation order (first failure wins):
        1. Manual circuit-breaker halt            → REJECTED
        2. Daily drawdown circuit-breaker         → REJECTED
        3. Max open positions                     → REJECTED
        4. Determine instrument route             → OPTION (smart/explicit) | EQUITY
        5. Calculate position size or budget      → REJECTED if quantity=0
        6. Total exposure cap                     → REJECTED
        → APPROVED

    Pure function: no I/O, no side effects.
    """
    correlation_id = intent.correlation_id
    source_intent_id = intent.event_id
    profile = intent.profile
    sleeve_value = portfolio.sleeve_value
    tier_pct = tier_position_pct(intent.confidence)

    def _rejected(reasons: list[str]) -> RiskDecision:
        return RiskDecision(
            correlation_id=correlation_id,
            source_intent_id=source_intent_id,
            outcome=RiskOutcome.REJECTED,
            rejection_reasons=reasons,
            profile=profile,
        )

    # ── 1. Manual halt ────────────────────────────────────────────────────────
    if is_manually_halted:
        return _rejected(["circuit_breaker_manually_halted"])

    # ── 2. Daily drawdown ─────────────────────────────────────────────────────
    if not check_drawdown_ok(portfolio.daily_pnl_pct, max_daily_drawdown_pct):
        return _rejected([
            f"daily_drawdown_breached:"
            f"actual={portfolio.daily_pnl_pct:.4f} "
            f"limit={-max_daily_drawdown_pct:.4f}"
        ])

    # ── 3. Position count ─────────────────────────────────────────────────────
    if not check_position_count(portfolio.open_position_count, max_open_positions):
        return _rejected([
            f"max_positions_reached:"
            f"current={portfolio.open_position_count} "
            f"max={max_open_positions}"
        ])

    # ── 4. Instrument routing ─────────────────────────────────────────────────
    asset_class, use_smart_selector = _route_instrument(intent)

    # ── 5. Position sizing ────────────────────────────────────────────────────
    budget = sleeve_value * tier_pct    # always computed for exposure check

    approved_quantity: int | None = None
    approved_budget: Decimal | None = None

    if use_smart_selector:
        # Executor will find the option price and compute contracts from budget.
        approved_budget = budget
        approved_quantity = None
        position_value_for_exposure = budget

    elif asset_class == AssetClass.OPTION and intent.option_type is not None:
        # Explicit option contract specified.
        if intent.entry_price is not None:
            is_option = True
            sizing = calculate_position_size(
                entry_price=intent.entry_price,
                tier_pct=tier_pct,
                sleeve_value=sleeve_value,
                is_option=is_option,
                stop_price=intent.stop_price,
                take_profit_price=intent.take_profit_price,
            )
            if sizing is None:
                return _rejected([
                    f"position_size_zero:"
                    f"entry={intent.entry_price} "
                    f"sleeve={sleeve_value}"
                ])
            approved_quantity = sizing.quantity
            position_value_for_exposure = sizing.position_value
        else:
            # Market option order — executor sizes from budget
            approved_budget = budget
            position_value_for_exposure = budget

    else:
        # Equity (shares) path
        if intent.entry_price is not None:
            sizing = calculate_position_size(
                entry_price=intent.entry_price,
                tier_pct=tier_pct,
                sleeve_value=sleeve_value,
                is_option=False,
                stop_price=intent.stop_price,
                take_profit_price=intent.take_profit_price,
            )
            if sizing is None:
                return _rejected([
                    f"position_size_zero:"
                    f"entry={intent.entry_price} "
                    f"sleeve={sleeve_value}"
                ])
            approved_quantity = sizing.quantity
            position_value_for_exposure = sizing.position_value
        else:
            # Market equity order — executor places at market, budget-bounded
            approved_budget = budget
            position_value_for_exposure = budget

    # ── 6. Total exposure cap ─────────────────────────────────────────────────
    if not check_total_exposure(
        portfolio.positions, position_value_for_exposure, sleeve_value
    ):
        return _rejected([
            f"exposure_limit_exceeded:"
            f"new_value={position_value_for_exposure:.0f} "
            f"sleeve={sleeve_value}"
        ])

    # ── APPROVED ──────────────────────────────────────────────────────────────
    rr: Decimal | None = None
    if (
        not use_smart_selector
        and intent.entry_price is not None
        and intent.stop_price is not None
        and intent.take_profit_price is not None
    ):
        stop_dist = abs(intent.entry_price - intent.stop_price)
        profit_dist = abs(intent.take_profit_price - intent.entry_price)
        if stop_dist > 0:
            rr = (profit_dist / stop_dist).quantize(Decimal("0.01"))

    return RiskDecision(
        correlation_id=correlation_id,
        source_intent_id=source_intent_id,
        outcome=RiskOutcome.APPROVED,
        approved_ticker=intent.ticker,
        approved_direction=intent.direction,
        approved_asset_class=asset_class,
        approved_quantity=approved_quantity,
        approved_budget=approved_budget,
        approved_entry_price=intent.entry_price,
        approved_stop_price=intent.stop_price,
        approved_take_profit=intent.take_profit_price,
        # Option contract fields
        approved_option_type=intent.option_type,
        approved_strike=intent.strike,
        approved_expiry=intent.expiry,
        use_smart_options_selector=use_smart_selector,
        position_size_pct=tier_pct,
        risk_reward_ratio=rr,
        profile=profile,
    )
