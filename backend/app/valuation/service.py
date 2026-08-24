# =====================================================================
# backend/app/valuation/service.py —— Valuation 服务编排（TS-06 §3.8，冻结）
#
# run_valuation 同步语义：
#   CREATED → VALIDATING（校验必需输入）→ RUNNING → COMPLETED
#   缺失假设 → BLOCKED_MISSING_INPUT（携带字段清单，绝不自动补默认值）
#   终值未定义（g>=wacc）→ FAILED + TERMINAL_VALUE_UNDEFINED
#   交叉验证超差 → WARNING 降级（v0.1 配置：结果 + TERMINAL_VALUE_CROSSCHECK_FAILED flag）
#
# 落库事务（ts06 §3.6.3 冻结）：valuation_runs + valuation_assumptions +
# valuation_input_refs + provenance（DERIVED_ENGINE）+ audit 原子提交。
# COMPLETED 后不可变（append-only 触发器兜底）。
# =====================================================================
from __future__ import annotations

import logging
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.audit.service import write_audit_event, write_provenance
from app.common.enums import (
    ActorType,
    AuditAction,
    CorporateActionStatus,
    CorporateActionType,
    DataQualityStatus,
    SourceKind,
    ValuationInputType,
    ValuationModelType,
    ValuationRunStatus,
)
from app.common.provenance import ProvenanceEnvelope
from app.corporate_actions.models import CorporateAction
from app.fundamentals.repository import get_latest_financial_fact_pit
from app.instruments.models import Instrument
from app.market_data.service import MarketDataService
from app.valuation.engine import (
    DCF_REQUIRED_ASSUMPTIONS,
    ENGINE_VERSION,
    compute_comparable,
    compute_dcf,
    compute_ddm,
    compute_objective,
    compute_owner_earnings,
    compute_scenario,
    input_snapshot_hash,
    margin_of_safety,
    validate_assumptions,
)
from app.valuation.errors import (
    MissingValuationInputError,
    UnsupportedModelError,
    ValuationError,
)
from app.valuation.models import (
    ValuationAssumption,
    ValuationInputRef,
    ValuationRun,
)
from app.valuation.schemas import ValuationAssumptionInput

logger = logging.getLogger(__name__)

__all__ = ["ValuationService", "ValuationRequest"]


class ValuationRequest:
    """run_valuation 输入（MCP 层直接构造）。"""

    def __init__(
        self,
        instrument_id: UUID,
        model_type: str,
        as_of: date,
        assumptions: list[ValuationAssumptionInput],
        fcf_forecast: list[Decimal],
        *,
        created_by: str = "HERMES",
        request_id: UUID | None = None,
    ) -> None:
        self.instrument_id = instrument_id
        self.model_type = model_type
        self.as_of = as_of
        self.assumptions = assumptions
        self.fcf_forecast = fcf_forecast
        self.created_by = created_by
        self.request_id = request_id or uuid4()


class ValuationService:
    def __init__(self, market_service: MarketDataService) -> None:
        self.market_service = market_service

    def latest(self, session: Session, instrument_id: UUID, as_of: datetime | None = None):
        stmt = select(ValuationRun).where(
            ValuationRun.instrument_id == instrument_id,
            ValuationRun.status.in_(["COMPLETED", "SUPERSEDED"]),
        )
        if as_of is not None:
            stmt = stmt.where(ValuationRun.as_of <= as_of)
        return session.scalar(stmt.order_by(
            ValuationRun.as_of.desc(), ValuationRun.created_at.desc()
        ).limit(1))

    def history(
        self, session: Session, instrument_id: UUID, *, as_of: datetime | None = None, limit: int = 50,
    ) -> list[ValuationRun]:
        stmt = select(ValuationRun).where(ValuationRun.instrument_id == instrument_id)
        if as_of is not None:
            stmt = stmt.where(ValuationRun.as_of <= as_of)
        return list(session.scalars(stmt.order_by(ValuationRun.as_of.desc()).limit(limit)).all())

    # ---- 状态机（STM-VAL）----

    _TRANSITIONS = {
        ValuationRunStatus.CREATED: {ValuationRunStatus.VALIDATING},
        ValuationRunStatus.VALIDATING: {
            ValuationRunStatus.BLOCKED_MISSING_INPUT,
            ValuationRunStatus.RUNNING,
            ValuationRunStatus.FAILED,
        },
        ValuationRunStatus.BLOCKED_MISSING_INPUT: {ValuationRunStatus.VALIDATING},
        ValuationRunStatus.RUNNING: {ValuationRunStatus.COMPLETED, ValuationRunStatus.FAILED},
        ValuationRunStatus.COMPLETED: {ValuationRunStatus.SUPERSEDED},
    }

    def _transition(self, session: Session, run: ValuationRun, to: ValuationRunStatus) -> None:
        allowed = self._TRANSITIONS.get(ValuationRunStatus(run.status), set())
        if to not in allowed:
            raise ValuationError(f"INVALID_VALUATION_TRANSITION: {run.status} → {to}")
        run.status = to.value

    # ---- 主入口 ----

    def run_valuation(self, session: Session, req: ValuationRequest) -> ValuationRun:
        run = ValuationRun(
            valuation_run_id=uuid4(),
            instrument_id=req.instrument_id,
            model_type=req.model_type,
            status=ValuationRunStatus.CREATED.value,
            as_of=datetime.combine(req.as_of, datetime.min.time(), tzinfo=UTC),
            engine_version=ENGINE_VERSION,
            created_by=req.created_by,
        )
        session.add(run)
        session.flush()

        try:
            self._transition(session, run, ValuationRunStatus.VALIDATING)
            # 1) 标的校验：CN_EQUITY only（CN_ETF 拒绝 → ETF Engine，ts06 §4.2）
            instrument = session.get(Instrument, req.instrument_id)
            if instrument is None:
                raise UnsupportedModelError(f"instrument {req.instrument_id} 不存在")
            if instrument.instrument_type != "CN_EQUITY":
                raise UnsupportedModelError(
                    f"instrument_type={instrument.instrument_type} 不走 Valuation Engine（CN_ETF → ETF Engine）"
                )
            try:
                model_type = ValuationModelType(req.model_type)
            except ValueError as exc:
                raise UnsupportedModelError(f"model_type={req.model_type} 不受支持") from exc

            # 2) 输入采集（PIT 过滤 + 质量门禁）
            facts, prices = self._collect_inputs(session, req)

            # 3) 假设校验（缺失 → BLOCKED_MISSING_INPUT，绝不补默认）
            a_map = {a.name: a for a in req.assumptions}
            try:
                validate_assumptions(
                    a_map, _required_assumptions(model_type, a_map, req.fcf_forecast)
                )
                if model_type is ValuationModelType.SCENARIO:
                    _validate_scenario_probability_inputs(a_map)
            except MissingValuationInputError as exc:
                self._transition(session, run, ValuationRunStatus.BLOCKED_MISSING_INPUT)
                run.result_json = {"error": {"code": "MISSING_VALUATION_INPUT",
                                             "missing_fields": exc.missing_fields}}
                session.commit()
                raise
            except ValuationError:
                self._transition(session, run, ValuationRunStatus.FAILED)
                session.commit()
                raise

            # 4) 计算
            self._transition(session, run, ValuationRunStatus.RUNNING)
            intrinsic = _compute_intrinsic(
                model_type, req.fcf_forecast, a_map, facts["shares_outstanding"]
            )
            objective = compute_objective(
                facts["close"], facts["net_income"], facts["shares_outstanding"],
                facts.get("total_equity"), period_type=facts.get("period_type"),
                debt=facts.get("debt"), cash=facts.get("cash"),
                operating_income=facts.get("operating_income"),
                depreciation_amortization=facts.get("depreciation_amortization"),
                free_cash_flow=facts.get("free_cash_flow"),
                dividend_12m_cny=facts.get("dividend_12m_cny"),
            )
            current_price = facts["close"]
            base_value = intrinsic["values"]["base"]
            # 列与 result_json 一致（ts06 §3.6.2 #1）：values/per_share 量化到 4 位
            intrinsic["values"] = {k: _money(v) for k, v in intrinsic["values"].items()}
            intrinsic["per_share"] = {k: _money(v) for k, v in intrinsic["per_share"].items()}
            result_json = {
                "objective": objective,
                "intrinsic": intrinsic,
                "summary": {
                    "current_price": current_price,
                    "margin_of_safety": margin_of_safety(base_value, current_price),
                    "currency": "CNY",
                },
                "inputs": {
                    "input_snapshot_hash": "sha256:" + input_snapshot_hash(_snapshot(req, facts)),
                    "as_of": req.as_of.isoformat(),
                    "engine_version": ENGINE_VERSION,
                },
            }

            # 5) 原子落库（§3.6.3）
            self._persist_result(session, run, req, facts, intrinsic, objective, result_json)
            return run
        except Exception as exc:  # noqa: BLE001
            if isinstance(exc, ValuationError):
                if run.status in {
                    ValuationRunStatus.CREATED.value,
                    ValuationRunStatus.VALIDATING.value,
                    ValuationRunStatus.RUNNING.value,
                }:
                    run.status = ValuationRunStatus.FAILED.value
                    session.commit()
            else:
                logger.exception("run_valuation 内部错误")
                try:
                    run.status = ValuationRunStatus.FAILED.value
                    session.commit()
                except Exception:  # noqa: BLE001
                    session.rollback()
            raise

    # ---- 输入采集 ----

    def _collect_inputs(self, session: Session, req: ValuationRequest) -> tuple[dict, dict]:
        """PIT 财务事实 + as_of 市价。质量门禁：CONFLICT/REJECTED → 拒绝。"""
        as_of = req.as_of
        net_income = get_latest_financial_fact_pit(session, req.instrument_id, "NET_INCOME", as_of)
        shares = get_latest_financial_fact_pit(session, req.instrument_id, "SHARES_OUTSTANDING", as_of)
        equity = get_latest_financial_fact_pit(session, req.instrument_id, "TOTAL_EQUITY", as_of)
        facts: dict = {}
        if net_income is not None:
            facts["net_income"] = net_income.value
            facts["period_type"] = net_income.period_type
            facts["net_income_published_at"] = net_income.published_at
        else:
            raise MissingValuationInputError(["NET_INCOME"])
        if shares is not None:
            facts["shares_outstanding"] = shares.value
        else:
            raise MissingValuationInputError(["SHARES_OUTSTANDING"])
        if equity is not None:
            facts["total_equity"] = equity.value
        for metric in ("DEBT", "CASH", "OPERATING_INCOME", "DEPRECIATION_AMORTIZATION", "FREE_CASH_FLOW"):
            fact = get_latest_financial_fact_pit(session, req.instrument_id, metric, as_of)
            if fact is not None:
                facts[metric.lower()] = fact.value

        dividend_rows = session.scalars(select(CorporateAction).where(
            CorporateAction.instrument_id == req.instrument_id,
            CorporateAction.action_type == CorporateActionType.DIVIDEND.value,
            CorporateAction.status.in_([
                CorporateActionStatus.IMPLEMENTED.value,
                CorporateActionStatus.ADJUSTED.value,
            ]),
            CorporateAction.ex_date >= as_of - timedelta(days=365),
            CorporateAction.ex_date <= as_of,
        )).all()
        dividends = [
            Decimal(str((row.parameters or {}).get("cash_div_per_10"))) / Decimal("10")
            for row in dividend_rows
            if (row.parameters or {}).get("cash_div_per_10") is not None
        ]
        facts["dividend_12m_cny"] = sum(dividends, Decimal("0"))

        rows = self.market_service.get_ohlcva(session, req.instrument_id, as_of=as_of)
        if rows:
            facts["close"] = Decimal(str(rows[-1]["close"]))
            facts["trade_date"] = rows[-1]["trade_date"]
        else:
            raise MissingValuationInputError(["OHLCVA_CLOSE"])
        return facts, {}

    # ---- 落库 ----

    def _persist_result(self, session, run, req, facts, intrinsic, objective, result_json) -> None:
        import json

        self._transition(session, run, ValuationRunStatus.COMPLETED)
        # result_json 为 JSONB：Decimal → str 序列化（数值列保留定点语义）
        result_json = json.loads(json.dumps(result_json, default=str))
        run.bear_value = _money(intrinsic["values"]["bear"])
        run.base_value = _money(intrinsic["values"]["base"])
        run.bull_value = _money(intrinsic["values"]["bull"])
        run.current_price = _money(facts["close"])
        run.margin_of_safety = result_json["summary"]["margin_of_safety"]
        run.result_json = result_json
        run.completed_at = datetime.now(UTC)
        run.input_snapshot_hash = result_json["inputs"]["input_snapshot_hash"]

        for a in req.assumptions:
            session.add(ValuationAssumption(
                valuation_assumption_id=uuid4(),
                valuation_run_id=run.valuation_run_id,
                name=a.name, value=a.value, unit=a.unit,
                basis=a.basis, source_tags=a.source_tags,
            ))
        # 输入引用（冻结证明）
        refs = [
            ("FINANCIAL_FACT", "net_income", facts.get("net_income_published_at")),
            ("MARKET_PRICE", "ohlcva_close", facts.get("trade_date")),
        ]
        for input_type, label, ver in refs:
            session.add(ValuationInputRef(
                valuation_input_ref_id=uuid4(),
                valuation_run_id=run.valuation_run_id,
                input_type=label_to_input_type(input_type),
                object_id=req.instrument_id,
                object_version=str(ver) if ver else None,
            ))
        # provenance（DERIVED_ENGINE，同事务）
        prov = write_provenance(session, ProvenanceEnvelope(
            source_kind=SourceKind.DERIVED_ENGINE,
            source="valuation_engine", provider="internal",
            observed_at=datetime.now(UTC), retrieved_at=datetime.now(UTC),
            as_of_date=req.as_of, quality_score=Decimal("1.0"),
            quality_status=DataQualityStatus.VERIFIED,
            transform_version=ENGINE_VERSION,
        ))
        prov.provenance_id = prov.provenance_id or uuid4()
        run.provenance_id = prov.provenance_id
        run.result_json = {**result_json, "provenance_id": str(prov.provenance_id)}
        # audit
        write_audit_event(
            session, action=AuditAction.CREATE, entity_type="valuation_runs",
            entity_id=run.valuation_run_id, actor_type=ActorType.HERMES,
            actor_id=req.created_by, payload={"model_type": req.model_type, "status": "COMPLETED"},
        )
        session.commit()


def label_to_input_type(label: str):
    return {"FINANCIAL_FACT": ValuationInputType.FINANCIAL_FACT,
            "MARKET_PRICE": ValuationInputType.MARKET_PRICE}[label]


def _money(v: Decimal) -> Decimal:
    return v.quantize(Decimal("0.0001"))


def _snapshot(req, facts) -> dict:
    return {
        "instrument_id": str(req.instrument_id),
        "model_type": req.model_type,
        "as_of": req.as_of.isoformat(),
        "facts": {k: str(v) for k, v in facts.items()},
        "assumptions": {a.name: str(a.value) for a in req.assumptions},
        "fcf_forecast": [str(f) for f in req.fcf_forecast],
        "engine_version": ENGINE_VERSION,
    }


def _required_assumptions(
    model_type: ValuationModelType,
    assumptions: dict[str, ValuationAssumptionInput],
    forecast: list[Decimal],
) -> list[str]:
    if model_type is ValuationModelType.DCF:
        return DCF_REQUIRED_ASSUMPTIONS
    if model_type is ValuationModelType.DDM:
        return ["discount_rate", "terminal_growth"]
    if model_type is ValuationModelType.OWNER_EARNINGS:
        return ["owner_earnings", "wacc", "terminal_growth"]
    if model_type is ValuationModelType.COMPARABLE:
        return ["target_metric", "target_multiple", "target_multiple_basis"]
    if model_type is ValuationModelType.SCENARIO:
        if len(forecast) != 3:
            raise MissingValuationInputError(["scenario_bear", "scenario_base", "scenario_bull"])
        if not any(name in assumptions for name in ("p_bear", "p_base", "p_bull", "probability_basis")):
            raise MissingValuationInputError(["p_bear", "p_base", "p_bull", "probability_basis"])
        return []
    raise UnsupportedModelError(f"model_type={model_type} 不受支持")


def _validate_scenario_probability_inputs(assumptions: dict[str, ValuationAssumptionInput]) -> None:
    names = ("p_bear", "p_base", "p_bull")
    supplied = [assumptions.get(name) for name in names]
    if any(value is not None for value in supplied) and any(value is None for value in supplied):
        raise MissingValuationInputError(list(names))


def _compute_intrinsic(
    model_type: ValuationModelType,
    forecast: list[Decimal],
    assumptions: dict[str, ValuationAssumptionInput],
    shares_outstanding: Decimal,
) -> dict:
    if model_type is ValuationModelType.DCF:
        return compute_dcf(forecast, assumptions, shares_outstanding)
    if model_type is ValuationModelType.DDM:
        return compute_ddm(forecast, assumptions)
    if model_type is ValuationModelType.OWNER_EARNINGS:
        return compute_owner_earnings(assumptions["owner_earnings"].value, assumptions)
    if model_type is ValuationModelType.COMPARABLE:
        return compute_comparable(assumptions["target_metric"].value, shares_outstanding, assumptions)
    return compute_scenario(
        dict(zip(("bear", "base", "bull"), forecast)), assumptions,
    )
