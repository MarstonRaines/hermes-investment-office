# =====================================================================
# tests/unit/test_valuation_service.py —— Valuation 服务集成（DB 层）
#
# 覆盖：run_valuation 全流程（PIT 输入 → DCF → 落库事务）、CN_ETF 拒绝
# （ACC-M3-003 前置）、BLOCKED_MISSING_INPUT、COMPLETED 不可变。
# =====================================================================
from __future__ import annotations

import json
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

import pytest

import app.models  # noqa: F401
from app.audit.models import ProvenanceRecord
from app.common.enums import ValuationRunStatus
from app.instruments.models import Instrument
from app.market_data.parquet import ParquetStore
from app.market_data.repository import persist_market_bars
from app.providers.contracts.base import ProvenanceEnvelope
from app.providers.contracts.market_data import MarketBarResult
from app.valuation.engine import ValuationAssumptionInput
from app.valuation.errors import (
    MissingValuationInputError,
    UnsupportedModelError,
)
from app.valuation.models import ValuationAssumption, ValuationInputRef, ValuationRun
from app.valuation.service import ValuationRequest, ValuationService

GOLDEN = json.loads(
    (Path(__file__).resolve().parents[1] / "golden" / "valuation_golden.json").read_text()
)["cases"][0]
NOW = datetime(2026, 8, 21, 12, 0, tzinfo=UTC)


def _assumptions() -> list[ValuationAssumptionInput]:
    return [ValuationAssumptionInput(**a) for a in GOLDEN["input"]["assumptions"]]


def _env() -> ProvenanceEnvelope:
    return ProvenanceEnvelope(
        source="cn_financial_statements", provider="tushare",
        source_record_id="tushare@2025-12-31",
        observed_at=NOW, retrieved_at=NOW,
        quality_score=Decimal("0.96"), quality_status="VERIFIED",
        transform_version="fundamental-normalizer/0.1.0",
    )


def _seed_instrument(session, instrument_type="CN_EQUITY") -> Instrument:
    inst = Instrument(
        instrument_type=instrument_type, symbol=f"V{uuid4hex()}",
        name="估值测试", market="SSE", currency="CNY",
    )
    session.add(inst)
    session.flush()
    return inst


def uuid4hex() -> str:
    from uuid import uuid4

    return uuid4().hex[:8]


def _seed_facts(session, inst) -> None:
    from app.fundamentals.models import FinancialFact

    def fact(metric: str, value: str, period="2025-12-31", statement="INCOME") -> FinancialFact:
        return FinancialFact(
            instrument_id=inst.instrument_id, metric_code=metric,
            period_end=date.fromisoformat(period), period_type="FY",
            statement_type=statement,
            published_at=datetime(2026, 4, 16, tzinfo=UTC),
            retrieved_at=NOW,
            original_value=Decimal(value), original_unit="元",
            value=Decimal(value), unit="CNY",
            provider="tushare", provenance_id=_seed_prov(session),
            quality_status="VERIFIED",
        )

    session.add(fact("NET_INCOME", "100", statement="INCOME"))
    session.add(fact("SHARES_OUTSTANDING", "10", statement="OTHER"))
    session.add(fact("TOTAL_EQUITY", "200", statement="BALANCE"))
    session.flush()


def _seed_prov(session):
    from app.audit.models import ProvenanceRecord

    prov = ProvenanceRecord(
        source_kind="PROVIDER", source="cn_financial_statements", provider="tushare",
        source_record_id="seed", observed_at=NOW, retrieved_at=NOW,
        quality_score=Decimal("0.96"), quality_status="VERIFIED",
        transform_version="v1",
    )
    session.add(prov)
    session.flush()
    prov.provenance_id = prov.provenance_id or _uuid()
    return prov.provenance_id


def _uuid():
    from uuid import uuid4

    return uuid4()


def _seed_price(session, inst, tmp_path) -> ParquetStore:
    store = ParquetStore(tmp_path / "parquet")
    bar = MarketBarResult(
        instrument_id=inst.instrument_id, trade_date=date(2026, 8, 21),
        close=Decimal("100"), provider="tushare",
        provenance=ProvenanceEnvelope(
            source="cn_daily_market", provider="tushare",
            source_record_id="tushare@2026-08-21",
            observed_at=NOW, retrieved_at=NOW, as_of_date=date(2026, 8, 21),
            quality_score=Decimal("0.96"), quality_status="VERIFIED",
            transform_version="market-normalizer/0.1.0",
        ),
    )
    persist_market_bars(session, [bar], parquet_store=store)
    session.flush()
    return store


def _service(tmp_path) -> ValuationService:
    from app.market_data.service import MarketDataService

    return ValuationService(MarketDataService(ParquetStore(tmp_path / "parquet")))


def test_run_valuation_full_flow(db_session, tmp_path) -> None:
    """run_valuation：PIT 输入 → DCF → 原子落库（run+assumptions+refs+provenance+audit）。"""
    inst = _seed_instrument(db_session)
    _seed_facts(db_session, inst)
    _seed_price(db_session, inst, tmp_path)
    db_session.flush()

    req = ValuationRequest(
        instrument_id=inst.instrument_id, model_type="DCF",
        as_of=date(2026, 8, 21), assumptions=_assumptions(),
        fcf_forecast=[Decimal(str(f)) for f in GOLDEN["input"]["fcf_forecast"]],
    )
    run = _service(tmp_path).run_valuation(db_session, req)

    assert run.status == ValuationRunStatus.COMPLETED.value
    assert abs(run.base_value - Decimal("3529.1555")) < Decimal("0.01")
    assert run.margin_of_safety == Decimal("0.971665")
    assert run.engine_version == "valuation-engine/0.1.0"
    assert run.input_snapshot_hash.startswith("sha256:")
    assert run.provenance_id is not None
    derived = db_session.get(ProvenanceRecord, run.provenance_id)
    assert derived is not None
    assert derived.source_kind == "DERIVED_ENGINE"
    assert run.result_json["provenance_id"] == str(run.provenance_id)
    # 原子落库
    assert db_session.query(ValuationAssumption).filter_by(
        valuation_run_id=run.valuation_run_id).count() == 11
    assert db_session.query(ValuationInputRef).filter_by(
        valuation_run_id=run.valuation_run_id).count() == 2
    # result_json 与列一致（ts06 §3.6.2 #1；JSONB 序列化后为 str，数值比较）
    assert Decimal(str(run.result_json["intrinsic"]["values"]["base"])) == run.base_value
    assert Decimal(str(run.result_json["summary"]["margin_of_safety"])) == run.margin_of_safety
    assert run.result_json["summary"]["currency"] == "CNY"
    assert run.result_json["inputs"]["engine_version"] == "valuation-engine/0.1.0"


def test_run_valuation_etf_rejected(db_session, tmp_path) -> None:
    """CN_ETF → UnsupportedModelError（ETF 走 ETF Engine，ACC-M3-003 前置）。"""
    inst = _seed_instrument(db_session, instrument_type="CN_ETF")
    db_session.flush()
    req = ValuationRequest(
        instrument_id=inst.instrument_id, model_type="DCF",
        as_of=date(2026, 8, 21), assumptions=_assumptions(),
        fcf_forecast=[Decimal(100)],
    )
    with pytest.raises(UnsupportedModelError):
        _service(tmp_path).run_valuation(db_session, req)


def test_run_valuation_blocked_missing_input(db_session, tmp_path) -> None:
    """缺 wacc → BLOCKED_MISSING_INPUT（携带字段清单，绝不自动补 8%）。"""
    inst = _seed_instrument(db_session)
    _seed_facts(db_session, inst)
    _seed_price(db_session, inst, tmp_path)
    db_session.flush()

    assumptions = [a for a in _assumptions() if a.name != "wacc_base"]
    req = ValuationRequest(
        instrument_id=inst.instrument_id, model_type="DCF",
        as_of=date(2026, 8, 21), assumptions=assumptions,
        fcf_forecast=[Decimal(100)],
    )
    with pytest.raises(MissingValuationInputError) as ei:
        _service(tmp_path).run_valuation(db_session, req)
    assert "wacc_base" in ei.value.missing_fields
    db_session.expire_all()
    run = (db_session.query(ValuationRun)
           .filter(ValuationRun.instrument_id == inst.instrument_id).one())
    assert run.status == ValuationRunStatus.BLOCKED_MISSING_INPUT.value
    assert run.result_json["error"]["code"] == "MISSING_VALUATION_INPUT"


def test_completed_run_immutable_by_trigger(db_session, tmp_path) -> None:
    """COMPLETED 后 valuation_runs UPDATE 被 append-only 触发器拒绝。"""
    inst = _seed_instrument(db_session)
    _seed_facts(db_session, inst)
    _seed_price(db_session, inst, tmp_path)
    db_session.flush()
    req = ValuationRequest(
        instrument_id=inst.instrument_id, model_type="DCF",
        as_of=date(2026, 8, 21), assumptions=_assumptions(),
        fcf_forecast=[Decimal(str(f)) for f in GOLDEN["input"]["fcf_forecast"]],
    )
    run = _service(tmp_path).run_valuation(db_session, req)
    from sqlalchemy import text

    with pytest.raises(Exception):
        db_session.execute(
            text("UPDATE valuation_runs SET base_value = 1 WHERE valuation_run_id = :id"),
            {"id": run.valuation_run_id},
        )
