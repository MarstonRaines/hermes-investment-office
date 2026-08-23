# =====================================================================
# tests/unit/test_sync_jobs.py —— 同步 Job 端到端（ACC-M1-001/002/003/004/006）
#
# mock provider（真实 registry/gateway/job 流程）→ normalizer →
# market_bar_index + ohlcva/v1 + provenance 同事务（TS-05 §8.3 冻结流程）。
#
# 会话策略：sync job 用独立 job_session（真实提交语义，与生产一致）；
# 数据按 instrument UUID 隔离（残留行不影响断言）。
# =====================================================================
from __future__ import annotations

import asyncio
import os
from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import uuid4

from sqlalchemy import create_engine
from sqlalchemy.orm import Session as SASession

import app.models  # noqa: F401
from app.audit.models import AuditEvent, ProvenanceRecord
from app.common.config import settings
from app.common.enums import AuditAction
from app.jobs.models import JobRun
from app.jobs.sync_jobs import build_sync_runner, param_fingerprint
from app.market_data.models import MarketBarIndex
from app.providers.capability_matrix import load_capability_matrix
from app.providers.contracts.base import (
    BaseProvider,
    ProvenanceEnvelope,
    ProviderCapability,
    ProviderHealth,
    ProviderQualityReport,
    ProviderRole,
    ProviderTimeout,
    QualityTier,
)
from app.providers.contracts.market_data import MarketBarResult
from app.providers.factory import ProviderFactory
from app.providers.raw_store import RawEvidenceStore
from app.providers.registry import ProviderRegistry
from app.providers.runtime_config import RuntimeProviderConfigs

NOW = datetime(2026, 8, 23, 12, 0, tzinfo=UTC)

TEST_DB_URL = os.environ.get(
    "HERMES_TEST_DB_URL",
    "postgresql+psycopg2://hermes:hermes@127.0.0.1:5432/hermes_test",
)
_job_engine = create_engine(TEST_DB_URL, pool_pre_ping=True)


def job_session() -> SASession:
    """同步 job 专用会话：真实提交语义（生产语义），数据按 UUID 隔离。"""
    return SASession(bind=_job_engine)


def _bar(iid, td: date, close: str, provider="tushare") -> MarketBarResult:
    return MarketBarResult(
        instrument_id=iid, trade_date=td, close=Decimal(close), provider=provider,
        provenance=ProvenanceEnvelope(
            source="cn_daily_market", provider=provider,
            source_record_id=f"{provider}@{iid}@{td.isoformat()}",
            observed_at=NOW, retrieved_at=NOW, as_of_date=td,
            quality_score=Decimal("0.96"), quality_status="VERIFIED",
            transform_version="market-normalizer/0.1.0",
        ),
    )


def new_instrument(session) -> object:
    """建一个唯一 Instrument + provider_symbol 映射（UUID 隔离，symbol 唯一）。"""
    from app.instruments.models import Instrument, ProviderSymbol

    suffix = uuid4().hex[:8]
    inst = Instrument(
        instrument_type="CN_EQUITY", symbol=f"J{suffix}",
        name="job测试", market="SSE", currency="CNY",
    )
    session.add(inst)
    session.flush()
    session.add(ProviderSymbol(
        instrument_id=inst.instrument_id, provider="tushare",
        symbol=f"J{suffix}.SH", valid_from=date(2020, 1, 1),
    ))
    session.flush()
    return inst


class FakeMarket(BaseProvider):
    provider_name = "tushare"
    display_name = "TuShare Pro"
    capabilities = frozenset({ProviderCapability.CN_DAILY_QUOTE,
                              ProviderCapability.CN_ETF_QUOTE,
                              ProviderCapability.ADJ_FACTOR,
                              ProviderCapability.FINANCIAL_STATEMENTS})
    default_role = ProviderRole.PRIMARY
    quality_tier = QualityTier.TIER_2
    known_limits = []
    bars: list = []
    facts: list = []
    fail: bool = False

    def __init__(self, config=None, symbol_resolver=None) -> None:
        self.config = config
        self._resolve = symbol_resolver

    async def get_price_history(self, instrument_id, start, end, adjust=None):
        if FakeMarket.fail:
            raise ProviderTimeout("boom")
        return [b for b in FakeMarket.bars if start <= b.trade_date <= end]

    async def get_financial_history(self, instrument_id, metrics, start_period, end_period):
        return [f for f in FakeMarket.facts if start_period <= f.period_end <= end_period]

    async def get_adj_factors(self, instrument_id, start, end):
        from app.providers.contracts.market_data import AdjFactorResult

        return [AdjFactorResult(
            instrument_id=instrument_id, trade_date=date(2026, 8, 21),
            adj_factor=Decimal("1.05"),
            provenance=ProvenanceEnvelope(
                source="cn_adj_factor", provider="tushare",
                source_record_id=f"tushare@{instrument_id}@2026-08-21",
                observed_at=NOW, retrieved_at=NOW, as_of_date=date(2026, 8, 21),
                quality_score=Decimal("0.96"), quality_status="VERIFIED",
                transform_version="market-normalizer/0.1.0",
            ),
        )]

    async def health_check(self) -> ProviderHealth:
        return ProviderHealth(provider="tushare", status="HEALTHY", checked_at=NOW)

    async def quality_report(self) -> ProviderQualityReport:
        return ProviderQualityReport(provider="tushare", tier=self.quality_tier,
                                     quality_score=Decimal("0.96"))


class EmptySina(BaseProvider):
    """矩阵链完整性：akshare_sina fallback（返回空 = 合法缺口）。"""
    provider_name = "akshare_sina"
    display_name = "AkShare（新浪源）"
    capabilities = frozenset({ProviderCapability.CN_DAILY_QUOTE,
                              ProviderCapability.CN_ETF_QUOTE,
                              ProviderCapability.ADJ_FACTOR})
    default_role = ProviderRole.FALLBACK
    quality_tier = QualityTier.TIER_3
    known_limits = []
    fail: bool = False

    def __init__(self, config=None, symbol_resolver=None) -> None:
        self.config = config
        self._resolve = symbol_resolver

    async def get_price_history(self, instrument_id, start, end, adjust=None):
        if EmptySina.fail:
            raise ProviderTimeout("sina down")
        return []

    async def health_check(self) -> ProviderHealth:
        return ProviderHealth(provider="akshare_sina", status="HEALTHY", checked_at=NOW)

    async def quality_report(self) -> ProviderQualityReport:
        return ProviderQualityReport(provider="akshare_sina", tier=self.quality_tier,
                                     quality_score=Decimal("0.85"))


def make_runner(tmp_path):
    matrix = load_capability_matrix(settings.provider_capability_path)
    reg = ProviderRegistry(matrix)
    reg.register(FakeMarket)
    reg.register(EmptySina)
    runtime = RuntimeProviderConfigs(providers={
        "tushare": {
            "timeout_seconds": 5, "max_retries": 1, "retry_backoff_base": 0.01,
            "rate_limit": {"qps": 1000.0, "burst": 10, "daily_quota": 100000},
        },
        "akshare_sina": {
            "timeout_seconds": 5, "max_retries": 1, "retry_backoff_base": 0.01,
            "rate_limit": {"qps": 1000.0, "burst": 10, "daily_quota": 100000},
        }})
    factory = ProviderFactory(reg, runtime, matrix)
    from app.market_data.parquet import ParquetStore

    parquet = ParquetStore(tmp_path / "parquet")
    raw = RawEvidenceStore(tmp_path / "data")
    return build_sync_runner(job_session, factory, reg, parquet, raw)


def test_param_fingerprint() -> None:
    fp1 = param_fingerprint("market_sync_job", {"universe": ["a"], "start": "2026-08-01"})
    fp2 = param_fingerprint("market_sync_job", {"start": "2026-08-01", "universe": ["a"]})
    fp3 = param_fingerprint("market_sync_job", {"universe": ["b"], "start": "2026-08-01"})
    assert fp1 == fp2      # 键序无关
    assert fp1 != fp3


def test_market_sync_job_end_to_end(tmp_path) -> None:
    """ACC-M1-001/002/004：market_sync_job 端到端（provider → normalizer →
    market_bar_index + ohlcva/v1 + provenance 同事务 + raw 落盘 + 血缘反查）。"""
    from app.market_data.parquet import ParquetStore
    from app.market_data.service import MarketDataService

    session = job_session()
    try:
        inst = new_instrument(session)
        runner = make_runner(tmp_path)
        job, created = runner.create_sync_job(
            session, "market_sync_job",
            {"universe": [str(inst.instrument_id)], "start": "2026-08-01", "end": "2026-08-31"})
        assert created is True
        session.commit()
        job_id = job.job_run_id

        FakeMarket.bars = [_bar(inst.instrument_id, date(2026, 8, 20), "100"),
                           _bar(inst.instrument_id, date(2026, 8, 21), "101")]
        result = asyncio.run(runner.run_market_sync(job_id, [inst.instrument_id],
                                                    date(2026, 8, 1), date(2026, 8, 31)))
        assert result.bars == 2

        # job 状态 + PG 指针
        session.expire_all()
        assert session.get(JobRun, job_id).status == "SUCCEEDED"
        idx_rows = (session.query(MarketBarIndex)
                    .filter(MarketBarIndex.instrument_id == inst.instrument_id).all())
        assert len(idx_rows) == 2

        # DuckDB 读取（ACC-M1-002）+ 复权因子附着（CTR-PAR-005 前置）
        service = MarketDataService(ParquetStore(tmp_path / "parquet"))
        rows = service.get_ohlcva(session, inst.instrument_id, as_of=date(2026, 8, 20))
        assert [r["trade_date"] for r in rows] == [date(2026, 8, 20)]
        latest_rows = service.get_ohlcva(session, inst.instrument_id)
        assert latest_rows[-1]["adj_factor"] == 1.05

        # 同事务血缘（ACC-M1-004：provenance 反查）+ raw 校验
        provs = (session.query(ProvenanceRecord)
                 .filter(ProvenanceRecord.ingestion_run_id == job_id).all())
        assert len(provs) == 2
        assert all(p.raw_object_key for p in provs)
        assert all(runner.raw_store.verify(p.raw_hash, p.raw_object_key) for p in provs)
    finally:
        session.close()


def test_market_sync_idempotent_trigger(tmp_path) -> None:
    """TS-05 §8.2：同指纹重复触发 → 返回既有 job，不重复创建。"""
    session = job_session()
    try:
        inst = new_instrument(session)
        runner = make_runner(tmp_path)
        params = {"universe": [str(inst.instrument_id)], "start": "2026-08-01", "end": "2026-08-31"}
        job1, created1 = runner.create_sync_job(session, "market_sync_job", params)
        session.commit()
        job2, created2 = runner.create_sync_job(session, "market_sync_job", params)
        assert created1 is True and created2 is False
        assert job1.job_run_id == job2.job_run_id
    finally:
        session.close()


def test_market_sync_incremental_checkpoint(tmp_path) -> None:
    """增量区间：已同步区间不重复拉取（checkpoint 语义）。"""
    session = job_session()
    try:
        inst = new_instrument(session)
        runner = make_runner(tmp_path)
        job, _ = runner.create_sync_job(session, "market_sync_job",
                                        {"universe": [str(inst.instrument_id)],
                                         "start": "2026-08-01", "end": "2026-08-31"})
        session.commit()
        FakeMarket.bars = [_bar(inst.instrument_id, date(2026, 8, 20), "100"),
                           _bar(inst.instrument_id, date(2026, 8, 21), "101")]
        asyncio.run(runner.run_market_sync(job.job_run_id, [inst.instrument_id],
                                           date(2026, 8, 1), date(2026, 8, 31)))
        # 再跑同区间：全部已覆盖 → 0 bars（不重复拉取）
        job2, _ = runner.create_sync_job(session, "market_sync_job",
                                         {"universe": [str(inst.instrument_id)],
                                          "start": "2026-08-01", "end": "2026-08-31"})
        session.commit()
        result = asyncio.run(runner.run_market_sync(job2.job_run_id, [inst.instrument_id],
                                                    date(2026, 8, 1), date(2026, 8, 31)))
        assert result.bars == 0
        assert (session.query(MarketBarIndex)
                .filter(MarketBarIndex.instrument_id == inst.instrument_id).count()) == 2
    finally:
        session.close()


def test_fallback_writes_audit_and_flags(tmp_path) -> None:
    """ACC-M1-004/006：primary 失败 → fallback 记录 + PROVIDER_FALLBACK audit 行。"""
    from app.instruments.models import ProviderSymbol
    from app.market_data.parquet import ParquetStore
    from app.providers.contracts.base import ProviderCapability as PC

    class FailingPrimary(BaseProvider):
        provider_name = "tushare"
        display_name = "TuShare Pro"
        capabilities = frozenset({PC.CN_DAILY_QUOTE})
        default_role = ProviderRole.PRIMARY
        quality_tier = QualityTier.TIER_2
        known_limits = []

        def __init__(self, config=None, symbol_resolver=None) -> None:
            self.config = config
            self._resolve = symbol_resolver

        async def get_price_history(self, instrument_id, start, end, adjust=None):
            raise ProviderTimeout("boom")

        async def health_check(self) -> ProviderHealth:
            return ProviderHealth(provider="tushare", status="HEALTHY", checked_at=NOW)

        async def quality_report(self) -> ProviderQualityReport:
            return ProviderQualityReport(provider="tushare", tier=self.quality_tier,
                                         quality_score=Decimal("0.96"))

    class FallbackSina(BaseProvider):
        provider_name = "akshare_sina"
        display_name = "AkShare（新浪源）"
        capabilities = frozenset({PC.CN_DAILY_QUOTE})
        default_role = ProviderRole.FALLBACK
        quality_tier = QualityTier.TIER_3
        known_limits = []

        def __init__(self, config=None, symbol_resolver=None) -> None:
            self.config = config
            self._resolve = symbol_resolver

        async def get_price_history(self, instrument_id, start, end, adjust=None):
            return [_bar(instrument_id, date(2026, 8, 21), "99", provider="akshare_sina")]

        async def health_check(self) -> ProviderHealth:
            return ProviderHealth(provider="akshare_sina", status="HEALTHY", checked_at=NOW)

        async def quality_report(self) -> ProviderQualityReport:
            return ProviderQualityReport(provider="akshare_sina", tier=self.quality_tier,
                                         quality_score=Decimal("0.85"))

    session = job_session()
    try:
        inst = new_instrument(session)
        # 给 akshare_sina 也挂映射（fallback 取数需要）
        session.add(ProviderSymbol(
            instrument_id=inst.instrument_id, provider="akshare_sina",
            symbol=f"sh{inst.symbol}", valid_from=date(2020, 1, 1),
        ))
        session.commit()

        matrix = load_capability_matrix(settings.provider_capability_path)
        reg = ProviderRegistry(matrix)
        reg.register(FailingPrimary)
        reg.register(FallbackSina)
        runtime = RuntimeProviderConfigs(providers={
            "tushare": {"max_retries": 0, "retry_backoff_base": 0.01,
                        "rate_limit": {"qps": 1000.0, "burst": 10, "daily_quota": 100000}},
            "akshare_sina": {"max_retries": 0, "retry_backoff_base": 0.01,
                             "rate_limit": {"qps": 1000.0, "burst": 10, "daily_quota": 100000}},
        })
        factory = ProviderFactory(reg, runtime, matrix)
        runner = build_sync_runner(job_session, factory, reg,
                                   ParquetStore(tmp_path / "parquet"),
                                   RawEvidenceStore(tmp_path / "data"))
        job, _ = runner.create_sync_job(session, "market_sync_job",
                                        {"universe": [str(inst.instrument_id)],
                                         "start": "2026-08-01", "end": "2026-08-31"})
        session.commit()
        result = asyncio.run(runner.run_market_sync(job.job_run_id, [inst.instrument_id],
                                                    date(2026, 8, 1), date(2026, 8, 31)))
        assert result.bars == 1
        # fallback 后的 bar provenance：FALLBACK_USED + 质量衰减（按本次 job 收窄）
        prov = (session.query(ProvenanceRecord)
                .filter(ProvenanceRecord.ingestion_run_id == job.job_run_id).one())
        assert prov.fallback_used is True
        assert "FALLBACK_USED" in prov.quality_flags
        assert prov.quality_score < Decimal("0.96")
        # audit 双写（§5.2：PROVIDER_FALLBACK 行，取本次 job 最新一条）
        event = (session.query(AuditEvent)
                 .filter(AuditEvent.action == AuditAction.PROVIDER_FALLBACK.value)
                 .order_by(AuditEvent.created_at.desc()).first())
        assert event is not None
        assert event.payload["requested_provider"] == "tushare"
        assert event.payload["actual_provider"] == "akshare_sina"
    finally:
        session.close()


def test_job_failure_recorded(tmp_path) -> None:
    """失败 → job FAILED + error 记录（不可静默吞掉，§35.4）。"""
    session = job_session()
    try:
        inst = new_instrument(session)
        runner = make_runner(tmp_path)
        job, _ = runner.create_sync_job(session, "market_sync_job",
                                        {"universe": [str(inst.instrument_id)],
                                         "start": "2026-08-01", "end": "2026-08-31"})
        session.commit()
        FakeMarket.bars = []
        FakeMarket.fail = True
        EmptySina.fail = True
        try:
            asyncio.run(runner.run_market_sync(job.job_run_id, [inst.instrument_id],
                                               date(2026, 8, 1), date(2026, 8, 31)))
            raise AssertionError("should raise")
        except Exception:
            pass
        finally:
            FakeMarket.fail = False
            EmptySina.fail = False
        session.expire_all()
        job2 = session.get(JobRun, job.job_run_id)
        assert job2.status == "FAILED"
        assert job2.error
    finally:
        session.close()
