# =====================================================================
# tests/unit/test_valuation_engine.py —— Valuation Engine（TS-06 §3 冻结）
#
# 覆盖：GOLD-VAL-001（DCF 黄金值，读黄金值文件）、GOLD-VAL-009（g>=wacc 拒绝）、
# GOLD-VAL-010（BLOCKED_MISSING_INPUT）、GOLD-VAL-011（margin_of_safety 公式）、
# GOLD-VAL-013（裸 float 拒绝）、STM-VAL（状态机合法/非法迁移）。
# =====================================================================
from __future__ import annotations

import json
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import pytest

import app.models  # noqa: F401

from app.valuation.engine import (
    ENGINE_VERSION,
    ValuationAssumptionInput,
    compute_dcf,
    compute_objective,
    input_snapshot_hash,
    margin_of_safety,
    validate_assumptions,
)
from app.valuation.errors import (
    InvalidAssumptionError,
    MissingValuationInputError,
    TerminalValueUndefinedError,
    UnsupportedModelError,
    ValuationError,
)

GOLDEN_FILE = Path(__file__).resolve().parents[1] / "golden" / "valuation_golden.json"


def _load_golden() -> dict:
    return json.loads(GOLDEN_FILE.read_text(encoding="utf-8"))["cases"][0]


def _assumptions_from_case(case: dict) -> dict[str, ValuationAssumptionInput]:
    return {
        a["name"]: ValuationAssumptionInput(**a)
        for a in case["input"]["assumptions"]
    }


# ---- GOLD-VAL-001：DCF 黄金值 ----

def test_gold_val_001_dcf_golden() -> None:
    """黄金值文件驱动：三情景值 / per_share / margin_of_safety / objective / 终值双方法 PASS。"""
    case = _load_golden()
    inp = case["input"]
    a_map = _assumptions_from_case(case)
    result = compute_dcf(
        [Decimal(str(f)) for f in inp["fcf_forecast"]],
        a_map, Decimal(str(inp["facts"]["shares_outstanding"])),
    )
    exp = case["expected"]

    for s in ("bear", "base", "bull"):
        assert abs(result["values"][s] - Decimal(str(exp["values"][s]))) < Decimal("0.001"), (
            f"{s}: {result['values'][s]} != {exp['values'][s]}"
        )
        assert abs(result["per_share"][s] - Decimal(str(exp["per_share"][s]))) < Decimal("0.0001")
        assert result["terminal"][s]["crosscheck_result"] == exp["crosscheck"][s]

    # 终值双方法明细（§3.5 硬性契约）
    t = result["terminal"]["base"]
    assert abs(t["gordon_value"] - Decimal(str(exp["terminal_gordon_base"]))) < Decimal("0.01")
    assert "implied_exit_multiple" in t and "implied_g" in t
    assert t["crosscheck_tolerance_used"] == Decimal("0.20")

    # objective 层（GOLD-VAL-001 附带的 PE/PB）
    obj = compute_objective(
        Decimal(str(inp["facts"]["close"])),
        Decimal(str(inp["facts"]["net_income"])),
        Decimal(str(inp["facts"]["shares_outstanding"])),
        Decimal(str(inp["facts"]["total_equity"])),
    )
    assert obj["pe"] == Decimal(str(exp["objective"]["pe"]))
    assert obj["pe_basis"] == "FY"
    assert obj["pb"] == Decimal(str(exp["objective"]["pb"]))

    # margin_of_safety（GOLD-VAL-011 双重断言）
    mos = margin_of_safety(result["values"]["base"], Decimal(str(inp["facts"]["close"])))
    assert mos == Decimal(str(exp["margin_of_safety"]))

    # input_snapshot_hash 存在（可复现证明）
    h = input_snapshot_hash({"as_of": case["as_of"], "x": 1})
    assert len(h) == 64


def test_gold_val_013_basis_required() -> None:
    """裸 float 拒绝：缺 basis → 422（Pydantic 校验）。"""
    with pytest.raises(Exception):
        ValuationAssumptionInput(name="wacc", value=Decimal("0.091"))


def test_gold_val_009_growth_ge_wacc_rejected() -> None:
    """g >= wacc → TerminalValueUndefinedError（绝非'很大'的数值结果）。"""
    a_map = _assumptions_from_case(_load_golden())
    a_map["terminal_growth_base"] = ValuationAssumptionInput(
        name="terminal_growth_base", value=Decimal("0.10"), basis="bad")
    a_map["wacc_base"] = ValuationAssumptionInput(
        name="wacc_base", value=Decimal("0.10"), basis="bad")
    with pytest.raises(TerminalValueUndefinedError):
        validate_assumptions(a_map, ["wacc_base", "terminal_growth_base"])


def test_gold_val_010_missing_wacc_blocked() -> None:
    """缺 wacc → MissingValuationInputError 携带字段清单（绝不自动补 8%）。"""
    a_map = _assumptions_from_case(_load_golden())
    del a_map["wacc_base"]
    with pytest.raises(MissingValuationInputError) as ei:
        validate_assumptions(a_map, ["wacc_base", "terminal_growth_base"])
    assert "wacc_base" in ei.value.missing_fields


def test_invalid_wacc_rejected() -> None:
    a_map = _assumptions_from_case(_load_golden())
    a_map["wacc_base"] = ValuationAssumptionInput(name="wacc_base", value=Decimal("0"), basis="x")
    with pytest.raises(InvalidAssumptionError):
        validate_assumptions(a_map, ["wacc_base"])


def test_crosscheck_fail_flag() -> None:
    """exit_multiple 与隐含值偏差 >20% → WARNING 降级 + flag（v0.1 配置）。"""
    a_map = _assumptions_from_case(_load_golden())
    a_map["exit_multiple_base"] = ValuationAssumptionInput(
        name="exit_multiple_base", value=Decimal("8"), basis="way_off")
    result = compute_dcf([Decimal(100), Decimal(110)], a_map, Decimal(10))
    assert result["terminal"]["base"]["crosscheck_result"] == "FAIL"
    assert "TERMINAL_VALUE_CROSSCHECK_FAILED" in result["quality_flags"]


# ---- STM-VAL 状态机 ----

def test_stm_val_legal_transitions() -> None:
    from app.common.enums import ValuationRunStatus
    from app.valuation.service import ValuationService

    t = ValuationService._TRANSITIONS
    assert ValuationRunStatus.VALIDATING in t[ValuationRunStatus.CREATED]
    assert {ValuationRunStatus.BLOCKED_MISSING_INPUT, ValuationRunStatus.RUNNING,
            ValuationRunStatus.FAILED} <= t[ValuationRunStatus.VALIDATING]
    assert {ValuationRunStatus.COMPLETED, ValuationRunStatus.FAILED} <= t[ValuationRunStatus.RUNNING]
    assert ValuationRunStatus.SUPERSEDED in t[ValuationRunStatus.COMPLETED]


def test_stm_val_illegal_transitions_rejected() -> None:
    """非法迁移（跳步/回退）→ typed error。"""
    from app.common.enums import ValuationRunStatus
    from app.valuation.service import ValuationService

    svc = ValuationService(market_service=None)
    run = type("R", (), {"status": ValuationRunStatus.CREATED.value})()
    for illegal in (ValuationRunStatus.COMPLETED, ValuationRunStatus.RUNNING,
                    ValuationRunStatus.BLOCKED_MISSING_INPUT):
        with pytest.raises(ValuationError):
            svc._transition(None, run, illegal)
    run.status = ValuationRunStatus.FAILED.value
    with pytest.raises(ValuationError):
        svc._transition(None, run, ValuationRunStatus.COMPLETED)


def test_engine_version_frozen() -> None:
    assert ENGINE_VERSION == "valuation-engine/0.1.0"
