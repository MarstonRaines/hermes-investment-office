# =====================================================================
# backend/app/valuation/engine.py —— Valuation Engine 纯计算核心（TS-06 §3，冻结）
#
# 确定性：同输入 → 同输出（黄金值锁定）。无 I/O、无 LLM、无隐式默认参数。
#
# v0.1 口径声明（2026-08-24 施工记录，M1.5 Vertical Slice）：
# - DCF 采用 FCFE 口径：预测期 FCF 序列 = 权益自由现金流（股东可得），
#   折现率 wacc 作为权益要求回报率（wacc_weight_basis=EQUITY_CASH_FLOW，
#   v0.1 简化口径；FCFF + 净债务调整路径留待 M3 经 ADR 细化）；
# - bear/base/bull 三情景全部显式假设（wacc_s / terminal_growth_s /
#   exit_multiple_s / fcf_multiplier_s），无任何隐式情景推导；
# - 终值：Gordon（永续）为基准值，退出倍数法交叉验证（tol=0.20，
#   超差 → WARNING 降级 + TERMINAL_VALUE_CROSSCHECK_FAILED flag，v0.1 配置）。
# =====================================================================
from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal

from app.valuation.errors import (
    InvalidAssumptionError,
    MissingValuationInputError,
    TerminalValueUndefinedError,
)

__all__ = [
    "ENGINE_VERSION",
    "ValuationAssumptionInput",
    "validate_assumptions",
    "compute_dcf",
    "compute_ddm",
    "compute_owner_earnings",
    "compute_comparable",
    "compute_scenario",
    "compute_objective",
    "margin_of_safety",
    "input_snapshot_hash",
]

ENGINE_VERSION = "valuation-engine/0.1.0"
TERMINAL_CROSSCHECK_TOLERANCE = Decimal("0.20")   # 隐含值与显式值偏差 >20% → 不通过

ALLOWED_UNITS = frozenset({"ratio", "cny", "percent", "years", "cny_per_share"})

# DCF 必需假设（name → (unit, 校验)）
DCF_REQUIRED_ASSUMPTIONS = [
    "wacc_base", "terminal_growth_base", "exit_multiple_base",
    "wacc_bear", "terminal_growth_bear", "exit_multiple_bear",
    "wacc_bull", "terminal_growth_bull", "exit_multiple_bull",
    "fcf_multiplier_bear", "fcf_multiplier_bull",
]

from pydantic import BaseModel, Field  # noqa: E402


class ValuationAssumptionInput(BaseModel):
    """结构化假设（ts06 §3.4）：basis NOT NULL —— 拒绝裸 float（GOLD-VAL-013）。"""

    name: str
    value: Decimal
    unit: str = "ratio"
    basis: str = Field(min_length=1)          # 空 basis → 422（GOLD-VAL-013）
    source_tags: list[str] = Field(default_factory=list)


def validate_assumptions(assumptions: dict[str, ValuationAssumptionInput], required: list[str]) -> dict:
    """校验 + 补齐必需假设清单。缺失 → MissingValuationInputError（绝不补默认值）。"""
    missing = [name for name in required if name not in assumptions]
    if missing:
        raise MissingValuationInputError(missing)
    for name, a in assumptions.items():
        if a.unit not in ALLOWED_UNITS:
            raise InvalidAssumptionError(f"{name}: unit={a.unit!r} 非法（{sorted(ALLOWED_UNITS)}）")
        if a.unit == "ratio" and not (Decimal("0") < a.value <= Decimal("100")):
            # ratio 允许倍数（exit_multiple>1）；仅 wacc/terminal_growth 做 ≤0 拒绝（下方专项）
            pass
    # 专项数值校验（ts06 §3.4 #3）
    for scenario in ("base", "bear", "bull"):
        wacc = assumptions.get(f"wacc_{scenario}")
        g = assumptions.get(f"terminal_growth_{scenario}")
        if wacc is not None and wacc.value <= 0:
            raise InvalidAssumptionError(f"wacc_{scenario} <= 0")
        if g is not None and wacc is not None and g.value >= wacc.value:
            raise TerminalValueUndefinedError(
                f"terminal_growth_{scenario} >= wacc_{scenario}（终值未定义，绝非'很大'）"
            )
        if g is not None and g.value < 0:
            raise InvalidAssumptionError(f"terminal_growth_{scenario} < 0")
        em = assumptions.get(f"exit_multiple_{scenario}")
        if em is not None and em.value <= 0:
            raise InvalidAssumptionError(f"exit_multiple_{scenario} <= 0")
    for name in ("fcf_multiplier_bear", "fcf_multiplier_bull"):
        m = assumptions.get(name)
        if m is not None and m.value <= 0:
            raise InvalidAssumptionError(f"{name} <= 0")
    return assumptions


def compute_dcf(
    fcf_forecast: list[Decimal],          # base 情景预测期 FCF 序列（FCFE，cny），长度 n
    assumptions: dict[str, ValuationAssumptionInput],
    shares_outstanding: Decimal,
) -> dict:
    """DCF 三情景计算。返回完整 result 结构（ts06 §3.6.2 intrinsic 节）。

    per-scenario：
      EV = Σ FCF_i/(1+wacc)^i + TV_gordon
      TV_gordon = FCF_n × (1+g) / (wacc − g)
      TV_multiple = FCF_n × exit_multiple（交叉验证）
      per_share = EV / shares_outstanding
    """
    n = len(fcf_forecast)
    if n == 0:
        raise InvalidAssumptionError("fcf_forecast 为空（预测期必须有现金流）")

    scenarios = {}
    for scenario in ("bear", "base", "bull"):
        wacc = assumptions[f"wacc_{scenario}"].value
        g = assumptions[f"terminal_growth_{scenario}"].value
        exit_multiple = assumptions[f"exit_multiple_{scenario}"].value
        mult = (assumptions[f"fcf_multiplier_{scenario}"].value
                if f"fcf_multiplier_{scenario}" in assumptions else Decimal("1.0"))
        fcf = [f * mult for f in fcf_forecast]

        # Gordon（基准）
        if wacc - g <= 0:
            raise TerminalValueUndefinedError(f"{scenario}: wacc−g ≤ 0")
        tv_gordon = fcf[-1] * (1 + g) / (wacc - g)
        pv = sum(fcf[i] / (1 + wacc) ** (i + 1) for i in range(n))
        equity_value = pv + tv_gordon

        # 退出倍数法（交叉验证）
        tv_multiple = fcf[-1] * exit_multiple
        implied_exit_multiple = tv_gordon / fcf[-1]
        # 隐含永续增长率反解：TV_multiple = FCF_n(1+ĝ)/(wacc−ĝ)
        #   → TV_m×wacc − TV_m×ĝ = FCF_n + FCF_n×ĝ → ĝ = (TV_m×wacc − FCF_n)/(TV_m + FCF_n)
        implied_g = (tv_multiple * wacc - fcf[-1]) / (tv_multiple + fcf[-1])

        dev = abs(implied_exit_multiple - exit_multiple) / exit_multiple
        crosscheck = "PASS" if dev <= TERMINAL_CROSSCHECK_TOLERANCE else "FAIL"
        flags = ["TERMINAL_VALUE_CROSSCHECK_FAILED"] if crosscheck == "FAIL" else []

        scenarios[scenario] = {
            "value": equity_value,
            "fcf": fcf,
            "tv_gordon": tv_gordon,
            "tv_multiple": tv_multiple,
            "implied_exit_multiple": implied_exit_multiple,
            "implied_g": implied_g,
            "crosscheck": crosscheck,
            "flags": flags,
        }

    per_share = {
        s: (v["value"] / shares_outstanding).quantize(Decimal("0.0001"), ROUND_HALF_UP)
        for s, v in scenarios.items()
    }
    return {
        "method": "DCF",
        "values": {s: v["value"] for s, v in scenarios.items()},
        "per_share": per_share,
        "shares_outstanding": shares_outstanding,
        "terminal": {
            s: {
                "gordon_value": v["tv_gordon"],
                "multiple_value": v["tv_multiple"],
                "implied_exit_multiple": v["implied_exit_multiple"],
                "implied_g": v["implied_g"],
                "crosscheck_result": v["crosscheck"],
                "crosscheck_tolerance_used": TERMINAL_CROSSCHECK_TOLERANCE,
            }
            for s, v in scenarios.items()
        },
        "quality_flags": sorted({f for v in scenarios.values() for f in v["flags"]}),
    }


def compute_ddm(
    dividend_forecast: list[Decimal],
    assumptions: dict[str, ValuationAssumptionInput],
) -> dict:
    """Dividend discount model using an explicit per-share dividend path."""
    required = ("discount_rate", "terminal_growth")
    _require(assumptions, required)
    if not dividend_forecast:
        raise InvalidAssumptionError("dividend_forecast 不能为空")
    rate = assumptions["discount_rate"].value
    growth = assumptions["terminal_growth"].value
    if rate <= 0 or growth < 0:
        raise InvalidAssumptionError("discount_rate/terminal_growth 非法")
    if growth >= rate:
        raise TerminalValueUndefinedError("terminal_growth >= discount_rate")
    pv = sum(value / (1 + rate) ** (index + 1) for index, value in enumerate(dividend_forecast))
    terminal = dividend_forecast[-1] * (1 + growth) / (rate - growth)
    base = pv + terminal / (1 + rate) ** len(dividend_forecast)
    values = {scenario: base for scenario in ("bear", "base", "bull")}
    return {
        "method": "DDM", "values": values, "per_share": values,
        "terminal": {"gordon_value": terminal, "crosscheck_result": "NOT_APPLICABLE"},
        "dividend_forecast": dividend_forecast,
        "quality_flags": [],
    }


def compute_owner_earnings(
    owner_earnings: Decimal,
    assumptions: dict[str, ValuationAssumptionInput],
) -> dict:
    """Owner earnings perpetuity with no implicit growth or discount defaults."""
    _require(assumptions, ("wacc", "terminal_growth"))
    wacc = assumptions["wacc"].value
    growth = assumptions["terminal_growth"].value
    if owner_earnings <= 0 or wacc <= 0 or growth < 0:
        raise InvalidAssumptionError("owner_earnings/wacc/terminal_growth 非法")
    if growth >= wacc:
        raise TerminalValueUndefinedError("terminal_growth >= wacc")
    value = owner_earnings * (1 + growth) / (wacc - growth)
    values = {scenario: value for scenario in ("bear", "base", "bull")}
    return {
        "method": "OWNER_EARNINGS", "values": values, "per_share": values,
        "terminal": {"gordon_value": value, "crosscheck_result": "NOT_APPLICABLE"},
        "quality_flags": [],
    }


def compute_comparable(
    target_metric: Decimal,
    shares_outstanding: Decimal,
    assumptions: dict[str, ValuationAssumptionInput],
) -> dict:
    """Comparable-company multiple valuation with an explicit basis."""
    _require(assumptions, ("target_multiple", "target_multiple_basis"))
    multiple = assumptions["target_multiple"].value
    if target_metric <= 0 or shares_outstanding <= 0 or multiple <= 0:
        raise InvalidAssumptionError("comparable target_metric/target_multiple 非法")
    premium = assumptions.get("premium_discount")
    premium_value = premium.value if premium is not None else Decimal("0")
    if premium_value <= Decimal("-1"):
        raise InvalidAssumptionError("premium_discount <= -1")
    per_share = target_metric * multiple * (1 + premium_value) / shares_outstanding
    values = {scenario: per_share for scenario in ("bear", "base", "bull")}
    return {
        "method": "COMPARABLE", "values": values, "per_share": values,
        "target_multiple_basis": assumptions["target_multiple_basis"].basis,
        "premium_discount": premium_value, "quality_flags": [],
    }


def compute_scenario(
    scenario_values: dict[str, Decimal],
    assumptions: dict[str, ValuationAssumptionInput],
) -> dict:
    """Probability-weighted scenario values; probabilities are never inferred."""
    _require(scenario_values, ("bear", "base", "bull"))
    probability_names = ("p_bear", "p_base", "p_bull")
    supplied = [assumptions.get(name) for name in probability_names]
    if any(value is not None for value in supplied):
        if any(value is None for value in supplied):
            raise MissingValuationInputError(list(probability_names))
        probabilities = [value.value for value in supplied if value is not None]
        if abs(sum(probabilities, Decimal("0")) - Decimal("1")) > Decimal("0.000001"):
            raise InvalidAssumptionError("scenario probabilities must sum to 1")
    elif assumptions.get("probability_basis") is not None and assumptions["probability_basis"].basis == "equal_weight":
        probabilities = [Decimal("1") / Decimal("3")] * 3
    else:
        raise MissingValuationInputError(list(probability_names))
    values = {key: Decimal(scenario_values[key]) for key in ("bear", "base", "bull")}
    weighted = sum(values[key] * probabilities[index] for index, key in enumerate(values))
    return {
        "method": "SCENARIO", "values": values, "per_share": values,
        "weighted_value": weighted, "probabilities": dict(zip(values, probabilities)),
        "quality_flags": [],
    }


def _require(values: dict, names: tuple[str, ...] | list[str]) -> None:
    missing = [name for name in names if name not in values]
    if missing:
        raise MissingValuationInputError(missing)


def compute_objective(
    close: Decimal,
    net_income: Decimal,
    shares_outstanding: Decimal,
    total_equity: Decimal | None = None,
    *,
    period_type: str | None = None,
    debt: Decimal | None = None,
    cash: Decimal | None = None,
    operating_income: Decimal | None = None,
    depreciation_amortization: Decimal | None = None,
    free_cash_flow: Decimal | None = None,
    dividend_12m_cny: Decimal | None = None,
    pe_history: list[Decimal] | None = None,
    pb_history: list[Decimal] | None = None,
    percentile_min_obs: int = 1,
) -> dict:
    """客观层最小集（TS-06 §3.2.1）：PE/PB + 口径声明。

    v0.1：EPS 取最新可得报告期；FY → basis=FY；
    非 FY → 按期间年化 + ANNUALIZED flag（口径显式，不静默）。
    """
    eps = net_income / shares_outstanding
    if period_type and period_type != "FY":
        factor = {"Q1": 4, "H1": 2, "Q3": Decimal(4) / 3}.get(period_type, 1)
        eps = eps * Decimal(str(factor))
        pe_basis = f"ANNUALIZED_{period_type}"
    else:
        pe_basis = "FY"
    objective: dict = {
        "pe": close / eps if eps else None,
        "pe_basis": pe_basis,
        "eps": eps,
    }
    if total_equity is not None:
        bvps = total_equity / shares_outstanding
        objective["pb"] = close / bvps if bvps else None
        objective["bvps"] = bvps
    market_cap = close * shares_outstanding
    if operating_income is not None and debt is not None and cash is not None:
        ebitda = operating_income + (depreciation_amortization or Decimal("0"))
        ev = market_cap + debt - cash
        objective.update({"ev": ev, "ebitda": ebitda, "ev_ebitda": ev / ebitda if ebitda else None})
        if depreciation_amortization is None:
            objective.setdefault("quality_flags", []).append("EBITDA_APPROX_EBIT")
    if free_cash_flow is not None:
        objective["fcf"] = free_cash_flow
        objective["fcf_yield"] = free_cash_flow / market_cap if market_cap else None
    if dividend_12m_cny is not None:
        objective["dividend_12m_cny"] = dividend_12m_cny
        objective["dividend_yield"] = dividend_12m_cny / market_cap if market_cap else None
    if pe_history is not None:
        objective["pe_percentile"] = _percentile(objective.get("pe"), pe_history, percentile_min_obs)
        objective["percentile_window"] = {"min_obs": percentile_min_obs, "obs_used": len(pe_history)}
    if pb_history is not None and "pb" in objective:
        objective["pb_percentile"] = _percentile(objective["pb"], pb_history, percentile_min_obs)
    if period_type and period_type != "FY":
        objective["quality_flags"] = ["PE_ANNUALIZED_NON_FY"]
    return objective


def _percentile(value: Decimal | None, history: list[Decimal], minimum: int) -> Decimal | None:
    if value is None or len(history) < minimum or not history:
        return None
    below = sum(1 for item in history if item <= value)
    return (Decimal(below) / Decimal(len(history))).quantize(Decimal("0.0001"))


def margin_of_safety(base_value: Decimal, current_price: Decimal) -> Decimal:
    """(base − price) / base（ts06 §3.6.1 冻结公式，黄金值 GOLD-VAL-011）。"""
    if base_value == 0:
        raise InvalidAssumptionError("base_value=0 无法计算安全边际")
    return ((base_value - current_price) / base_value).quantize(Decimal("0.000001"), ROUND_HALF_UP)


def input_snapshot_hash(payload: dict) -> str:
    """输入快照 sha256（可复现证明，ts06 §3.6.4）。"""
    import hashlib
    import json

    canonical = json.dumps(payload, sort_keys=True, default=str)
    return hashlib.sha256(canonical.encode()).hexdigest()
