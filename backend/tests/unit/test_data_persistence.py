# =====================================================================
# tests/unit/test_data_persistence.py —— 同事务持久化 + PIT 查询（db 集成）
#
# 对应：TS-05 §8.3 第 7 步（facts + provenance + index 同事务）、
#       ts02 §4.3 PIT 冻结 SQL、ACC-M1-003/004/005 的数据库层。
# =====================================================================
from __future__ import annotations

import asyncio
from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import uuid4

import app.models  # noqa: F401 —— 注册全部 ORM 模型（FK 目标表完整性）
from app.audit.models import AuditEvent, ProvenanceRecord
from app.audit.service import (
    provider_fallback_sink,
    write_provenance,
)
from app.common.enums import AuditAction, DataQualityStatus
from app.fundamentals.models import FinancialFact
from app.fundamentals.repository import get_financial_fact_pit, persist_financial_facts
from app.market_data.models import MarketBarIndex
from app.market_data.parquet import ParquetStore
from app.market_data.repository import persist_market_bars
from app.providers.contracts.base import ProvenanceEnvelope
from app.providers.contracts.fundamentals import FinancialFactResult
from app.providers.contracts.market_data import MarketBarResult
from app.providers.gateway import FallbackDecision

NOW = datetime(2026, 8, 23, 12, 0, tzinfo=UTC)


def _env(provider="tushare", source="cn_daily_market", score="0.96") -> ProvenanceEnvelope:
    return ProvenanceEnvelope(
        source=source, provider=provider,
        source_record_id=f"{provider}@2026-08-21",
        observed_at=NOW, retrieved_at=NOW,
        quality_score=Decimal(score),
        quality_status=DataQualityStatus.VERIFIED,
        transform_version="market-normalizer/0.1.0",
    )



def test_provenance_mapping_with_fallback_flags(db_session) -> None:
    """TS-05 §2.0.4：fallback 信息序列化进 quality_flags + fallback_used 列。"""
    env = _env().model_copy(update={
        "fallback_used": True, "requested_provider": "tushare",
        "fallback_reason": "PRIMARY_TIMEOUT",
    })
    rec = write_provenance(db_session, env)
    db_session.flush()
    assert rec.fallback_used is True
    flags = rec.quality_flags
    assert "fallback.requested_provider=tushare" in flags
    assert "fallback.reason=PRIMARY_TIMEOUT" in flags


def test_persist_market_bars_same_transaction(db_session, instrument) -> None:
    """ACC-M1-001：bar + provenance 同事务写入 market_bar_index。"""
    bar = MarketBarResult(
        instrument_id=instrument.instrument_id, trade_date=date(2026, 8, 21),
        close=Decimal("138.5"), provider="tushare", provenance=_env(),
    )
    summary = persist_market_bars(db_session, [bar])
    db_session.flush()
    assert summary.inserted == 1
    idx = (db_session.query(MarketBarIndex)
           .filter(MarketBarIndex.instrument_id == instrument.instrument_id).one())
    assert idx.instrument_id == instrument.instrument_id
    assert idx.provider == "tushare"
    assert idx.parquet_path.startswith("parquet/ohlcva/v1/")
    prov = db_session.get(ProvenanceRecord, idx.provenance_id)
    assert prov is not None
    assert prov.source_record_id == "tushare@2026-08-21"


def test_persist_market_bars_upsert_idempotent(db_session, instrument) -> None:
    """重跑同区间：upsert（updated），不产生重复行。"""
    bar = MarketBarResult(
        instrument_id=instrument.instrument_id, trade_date=date(2026, 8, 21),
        close=Decimal("138.5"), provider="tushare", provenance=_env(),
    )
    persist_market_bars(db_session, [bar])
    db_session.flush()
    summary2 = persist_market_bars(db_session, [bar])
    db_session.flush()
    assert summary2.inserted == 0
    assert summary2.updated == 1
    bars = (db_session.query(MarketBarIndex)
            .filter(MarketBarIndex.instrument_id == instrument.instrument_id).all())
    assert len(bars) == 1
    provs = (db_session.query(ProvenanceRecord)
             .filter(ProvenanceRecord.source_record_id == "tushare@2026-08-21").all())
    assert len(provs) == 2   # 新旧 provenance 均保留（supersede 历史）


def test_persist_financial_facts_idempotent(db_session, instrument) -> None:
    """同键重跑：DO NOTHING 幂等；重述（不同 published_at）= 新行。"""
    fact = FinancialFactResult(
        instrument_id=instrument.instrument_id, metric_code="REVENUE",
        period_end=date(2025, 12, 31), statement_type="INCOME",
        published_at=datetime(2026, 3, 28, tzinfo=UTC),
        retrieved_at=NOW, original_value=Decimal("150000000000"),
        original_unit="元", value=Decimal("150000000000"),
        provenance=_env(source="cn_financial_statements"),
    )
    n1 = persist_financial_facts(db_session, [fact])
    db_session.flush()
    n2 = persist_financial_facts(db_session, [fact])
    db_session.flush()
    assert n1 == 1 and n2 == 0
    assert (db_session.query(FinancialFact)
            .filter(FinancialFact.instrument_id == instrument.instrument_id).count()) == 1

    restated = fact.model_copy(update={
        "published_at": datetime(2026, 7, 15, tzinfo=UTC), "is_restated": True,
    })
    n3 = persist_financial_facts(db_session, [restated])
    db_session.flush()
    assert n3 == 1
    assert (db_session.query(FinancialFact)
            .filter(FinancialFact.instrument_id == instrument.instrument_id).count()) == 2


def test_persist_financial_facts_writes_versioned_parquet_pit_projection(
    db_session, instrument, tmp_path
) -> None:
    """TS-04 financial_history/v1 keeps both disclosures and enforces PIT on read."""
    store = ParquetStore(tmp_path / "parquet")
    first = FinancialFactResult(
        instrument_id=instrument.instrument_id, metric_code="REVENUE",
        period_end=date(2025, 12, 31), statement_type="INCOME",
        published_at=datetime(2026, 3, 28, tzinfo=UTC), retrieved_at=NOW,
        original_value=Decimal("150"), original_unit="万元", value=Decimal("1500000"),
        provenance=_env(source="cn_financial_statements"),
    )
    restated = first.model_copy(update={
        "published_at": datetime(2026, 7, 15, tzinfo=UTC),
        "is_restated": True, "value": Decimal("1600000"),
    })
    assert persist_financial_facts(db_session, [first, restated], parquet_store=store) == 2
    rows = store.read_financial_history(
        str(instrument.instrument_id), as_of=datetime(2026, 7, 1, tzinfo=UTC),
    )
    assert [row["value"] for row in rows] == [1500000.0]
    assert store.schema_json_path("financial_history", 1).exists()


def test_financial_history_writer_accepts_provider_contract(tmp_path, instrument) -> None:
    fact = FinancialFactResult(
        instrument_id=instrument.instrument_id, metric_code="REVENUE",
        period_end=date(2025, 12, 31), statement_type="INCOME",
        published_at=datetime(2026, 4, 16, tzinfo=UTC), retrieved_at=NOW,
        original_value=Decimal("100"), original_unit="元", value=Decimal("100"),
        provenance=ProvenanceEnvelope(
            source="fixture", provider="fixture", source_record_id="fixture-revenue-1",
            observed_at=NOW, retrieved_at=NOW, as_of_date=date(2026, 4, 16),
            quality_score=Decimal("1"), quality_status="VERIFIED", transform_version="fixture/1",
        ),
    )
    store = ParquetStore(tmp_path / "parquet")
    assert store.write_financial_history([fact]) == 1
    rows = store.read_financial_history(str(instrument.instrument_id), as_of=NOW)
    assert rows[0]["provenance_id"] == "fixture-revenue-1"

    later = fact.model_copy(update={
        "period_end": date(2025, 12, 31),
        "provenance": fact.provenance.model_copy(update={"source_record_id": "fixture-revenue-2"}),
    })
    assert store.write_financial_history([later]) == 1
    assert len(store.read_financial_history(str(instrument.instrument_id), as_of=NOW)) == 2


def test_pit_query_visibility(db_session, instrument) -> None:
    """GOLD-PIT-001 数据库层：as_of 只看到当时已披露的值（重述不回写历史）。"""
    first = FinancialFactResult(
        instrument_id=instrument.instrument_id, metric_code="REVENUE",
        period_end=date(2025, 12, 31), statement_type="INCOME",
        published_at=datetime(2026, 3, 28, tzinfo=UTC),
        retrieved_at=NOW, original_value=Decimal("150000000000"),
        original_unit="元", value=Decimal("150000000000"),
        provenance=_env(source="cn_financial_statements"),
    )
    restated = first.model_copy(update={
        "published_at": datetime(2026, 7, 15, tzinfo=UTC),
        "value": Decimal("160000000000"),
    })
    persist_financial_facts(db_session, [first, restated])
    db_session.flush()

    as_of_before = datetime(2026, 7, 1, tzinfo=UTC)
    as_of_after = datetime(2026, 8, 1, tzinfo=UTC)
    v_before = get_financial_fact_pit(db_session, instrument.instrument_id, "REVENUE",
                                      date(2025, 12, 31), as_of_before)
    v_after = get_financial_fact_pit(db_session, instrument.instrument_id, "REVENUE",
                                     date(2025, 12, 31), as_of_after)
    assert v_before.value == Decimal("150000000000")
    assert v_after.value == Decimal("160000000000")
    # 披露前不可见 → None（合法缺口语义，不抛错）
    assert get_financial_fact_pit(db_session, instrument.instrument_id, "REVENUE",
                                  date(2025, 12, 31), datetime(2026, 3, 1, tzinfo=UTC)) is None


def test_audit_fallback_sink_writes_event(db_session) -> None:
    """TS-05 §5.2：gateway audit_sink → audit_events 行（action=PROVIDER_FALLBACK）。"""
    decision = FallbackDecision(
        requested_provider="tushare", actual_provider="akshare_sina",
        fallback_used=True, fallback_reason="PRIMARY_TIMEOUT",
        attempts=["tushare", "akshare_sina"],
    )
    sink = provider_fallback_sink(lambda: db_session)
    entity_id = uuid4()

    async def run() -> None:
        from app.providers.contracts.base import ProviderCapability

        await sink(ProviderCapability.CN_DAILY_QUOTE, decision, entity_id)

    asyncio.run(run())
    db_session.flush()
    event = (db_session.query(AuditEvent)
             .filter(AuditEvent.entity_id == entity_id).one())
    assert event.action == "PROVIDER_FALLBACK"
    assert event.payload["fallback_reason"] == "PRIMARY_TIMEOUT"
    assert event.payload["attempts"] == ["tushare", "akshare_sina"]


def test_audit_action_enum_extended() -> None:
    """迁移一致性：新 action 值存在（ts02 §8.3 + TS-05 §5.2）。"""
    assert AuditAction.PROVIDER_FALLBACK.value == "PROVIDER_FALLBACK"


def test_persist_with_parquet_store(tmp_path, db_session, instrument) -> None:
    """job 端到端前置：repository + parquet 同写，PG 指针与文件一致（ACC-M1-002 前置）。"""
    from app.market_data.parquet import ParquetStore

    store = ParquetStore(tmp_path / "parquet")
    bar = MarketBarResult(
        instrument_id=instrument.instrument_id, trade_date=date(2026, 8, 21),
        close=Decimal("138.5"), provider="tushare", provenance=_env(),
    )
    summary = persist_market_bars(db_session, [bar], parquet_store=store)
    db_session.flush()
    assert summary.inserted == 1
    idx = (db_session.query(MarketBarIndex)
           .filter(MarketBarIndex.instrument_id == instrument.instrument_id).one())
    assert store.read_ohlcva(str(instrument.instrument_id))[0]["close"] == 138.5
    assert idx.parquet_path.startswith("parquet/ohlcva/v1/")


def test_market_data_service_pg_pointer_duckdb(tmp_path, db_session, instrument) -> None:
    """ACC-M1-002：PG 指针 → DuckDB 读取标准路径（ts04 §2.6.3）+ as_of。"""

    from app.market_data.parquet import ParquetStore
    from app.market_data.service import MarketDataService

    store = ParquetStore(tmp_path / "parquet")
    service = MarketDataService(store)
    # 未同步 → 合法缺口（空列表）
    assert service.get_ohlcva(db_session, instrument.instrument_id) == []
    assert service.latest_trade_date(db_session, instrument.instrument_id) is None

    b1 = MarketBarResult(
        instrument_id=instrument.instrument_id, trade_date=date(2026, 8, 20),
        close=Decimal("100"), provider="tushare", provenance=_env(),
    )
    b2 = MarketBarResult(
        instrument_id=instrument.instrument_id, trade_date=date(2026, 8, 21),
        close=Decimal("101"), provider="tushare", provenance=_env(),
    )
    persist_market_bars(db_session, [b1, b2], parquet_store=store)
    db_session.flush()

    rows = service.get_ohlcva(db_session, instrument.instrument_id)
    assert [r["trade_date"] for r in rows] == [date(2026, 8, 20), date(2026, 8, 21)]
    as_of_rows = service.get_ohlcva(db_session, instrument.instrument_id, as_of=date(2026, 8, 20))
    assert [r["trade_date"] for r in as_of_rows] == [date(2026, 8, 20)]
    assert service.latest_trade_date(db_session, instrument.instrument_id) == date(2026, 8, 21)
