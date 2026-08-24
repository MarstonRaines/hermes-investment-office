from __future__ import annotations

from decimal import Decimal

from app.valuation.engine import (
    ValuationAssumptionInput,
    compute_comparable,
    compute_ddm,
    compute_objective,
    compute_owner_earnings,
    compute_scenario,
)


def _a(name: str, value: str, *, basis: str = "explicit") -> ValuationAssumptionInput:
    return ValuationAssumptionInput(name=name, value=Decimal(value), basis=basis)


def test_ddm_produces_three_scenarios_without_hidden_defaults() -> None:
    result = compute_ddm(
        [Decimal("1"), Decimal("1.1")],
        {"discount_rate": _a("discount_rate", "0.10"), "terminal_growth": _a("terminal_growth", "0.02")},
    )
    assert result["method"] == "DDM"
    assert result["values"]["base"] == result["per_share"]["base"]


def test_owner_earnings_and_comparable_are_explicit() -> None:
    owner = compute_owner_earnings(
        Decimal("100"), {"wacc": _a("wacc", "0.10"), "terminal_growth": _a("terminal_growth", "0.02")},
    )
    comparable = compute_comparable(
        Decimal("100"), Decimal("10"), {
            "target_multiple": _a("target_multiple", "15"),
            "target_multiple_basis": _a("target_multiple_basis", "0", basis="peer_median_pe"),
        },
    )
    assert owner["values"]["base"] > 0
    assert comparable["values"]["base"] == Decimal("150")
    assert comparable["target_multiple_basis"] == "peer_median_pe"


def test_scenario_requires_probabilities_or_explicit_equal_weight() -> None:
    result = compute_scenario(
        {"bear": Decimal("80"), "base": Decimal("100"), "bull": Decimal("140")},
        {"probability_basis": _a("probability_basis", "0", basis="equal_weight")},
    )
    assert abs(result["weighted_value"] - Decimal("106.6667")) < Decimal("0.0001")


def test_objective_layer_exposes_extended_metrics() -> None:
    result = compute_objective(
        Decimal("10"), Decimal("20"), Decimal("10"), Decimal("50"),
        debt=Decimal("30"), cash=Decimal("5"), operating_income=Decimal("12"),
        free_cash_flow=Decimal("8"), dividend_12m_cny=Decimal("1"),
        pe_history=[Decimal("10"), Decimal("20")], percentile_min_obs=2,
    )
    assert result["ev_ebitda"] is not None
    assert result["fcf_yield"] == Decimal("0.080")
    assert result["dividend_yield"] == Decimal("0.010")
    assert result["pe_percentile"] == Decimal("0.0000")
