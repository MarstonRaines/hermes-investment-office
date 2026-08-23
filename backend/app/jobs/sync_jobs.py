# =====================================================================
# backend/app/jobs/sync_jobs.py —— 同步 Job 执行器（TS-05 §8，冻结流程）
#
# 冻结流程（§8.3）：
#   1. 取 universe（持仓 + 观察池 instrument_id 列表）
#   2. resolve_instrument：instrument_id → 各 provider 的 symbol（valid_to IS NULL）
#   3. 计算增量区间（上次成功 checkpoint → 现在）
#   4. 经 DataGateway 拉取（限流 + fallback，§5/§6）
#   5. raw artifact 落盘（§7）
#   6. normalize（transform_version）→ 事实 + ProvenanceEnvelope
#   7. 同事务写入：facts + provenance_records + market_bar_index（§12 事务边界）
#   8. audit_events（fallback sink 自动）+ job_runs 状态更新
#
# 幂等触发（§8.2）：同参数指纹（universe+范围+类型）存在 RUNNING/SUCCEEDED
# 且覆盖所需区间 → 返回既有 job，不重复创建。
# =====================================================================
from __future__ import annotations

import hashlib
import json
import logging
from collections.abc import Callable
from datetime import date
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.audit.service import provider_fallback_sink
from app.common.enums import JobStatus, JobType
from app.instruments.models import Instrument
from app.jobs.models import JobRun
from app.market_data.parquet import ParquetStore
from app.market_data.repository import persist_market_bars
from app.market_data.service import MarketDataService
from app.providers.contracts.base import ProviderCapability, ProviderError
from app.providers.factory import ProviderFactory
from app.providers.gateway import DataGateway
from app.providers.raw_store import RawEvidenceStore
from app.providers.resolve import resolve_provider_symbol

logger = logging.getLogger(__name__)

__all__ = ["SyncJobRunner", "SyncResult"]

MARKET_JOB = "market_sync_job"
FUNDAMENTAL_JOB = "fundamental_sync_job"


class SyncResult:
    def __init__(self, job_run_id: UUID, instruments: int, bars: int, raw_artifacts: int) -> None:
        self.job_run_id = job_run_id
        self.instruments = instruments
        self.bars = bars
        self.raw_artifacts = raw_artifacts

    def __repr__(self) -> str:  # pragma: no cover
        return f"SyncResult(job={self.job_run_id}, instruments={self.instruments}, bars={self.bars})"


def param_fingerprint(job_name: str, params: dict) -> str:
    """参数指纹（TS-05 §8.2 幂等三要素之一）：universe+范围+类型。"""
    canonical = json.dumps(params, sort_keys=True, default=str)
    return hashlib.sha256(f"{job_name}:{canonical}".encode()).hexdigest()


class SyncJobRunner:
    def __init__(
        self,
        session_factory: Callable[[], Session],
        gateway: DataGateway,
        factory: ProviderFactory,
        parquet_store: ParquetStore,
        raw_store: RawEvidenceStore,
    ) -> None:
        self.session_factory = session_factory
        self.gateway = gateway
        self.factory = factory
        self.parquet_store = parquet_store
        self.raw_store = raw_store
        self.market_service = MarketDataService(parquet_store)

    # ---- 幂等触发（§8.2）----

    def create_sync_job(
        self,
        session: Session,
        job_name: str,
        params: dict,
        *,
        check_idempotent: bool = True,
    ) -> tuple[JobRun, bool]:
        """创建 job（幂等：同指纹 RUNNING/SUCCEEDED 且覆盖区间 → 返回既有 job）。

        返回 (job, created)；created=False 表示命中既有 job。
        """
        fp = param_fingerprint(job_name, params)
        if check_idempotent:
            existing = session.execute(
                select(JobRun).where(
                    JobRun.job_name == job_name,
                    JobRun.input_version == fp,
                    JobRun.status.in_([JobStatus.PENDING.value, JobStatus.RUNNING.value, JobStatus.SUCCEEDED.value]),
                ).order_by(JobRun.created_at.desc())
            ).scalars().first()
            if existing is not None:
                return existing, False
        job = JobRun(
            job_run_id=uuid4(),
            job_name=job_name,
            job_type=JobType.SYNC_JOB.value,
            status=JobStatus.PENDING.value,
            input_version=fp,
            params=params,
        )
        session.add(job)
        session.flush()
        return job, True

    # ---- market_sync_job（ACC-M1-001）----

    async def run_market_sync(
        self,
        job_run_id: UUID,
        instruments: list[UUID],
        start: date,
        end: date,
    ) -> SyncResult:
        session = self.session_factory()
        try:
            job = self._start_job(session, job_run_id)
            total_bars = 0
            raw_count = 0
            for instrument_id in instruments:
                total_bars += await self._sync_one_market(session, job, instrument_id, start, end)
                raw_count += 1
            self._finish_job(session, job_run_id, total_bars)
            return SyncResult(job_run_id, len(instruments), total_bars, raw_count)
        except Exception as exc:  # noqa: BLE001 —— 失败必须记录，不可静默吞掉（§35.4）
            self._fail_job(session, job_run_id, exc)
            raise
        finally:
            session.close()

    async def _sync_one_market(
        self, session: Session, job: JobRun, instrument_id: UUID, start: date, end: date,
    ) -> int:
        """单标的：resolve → 增量区间 → gateway 拉取 → raw → 同事务落库。"""
        inst = session.get(Instrument, instrument_id)
        if inst is None:
            logger.warning("instrument %s 不存在，跳过", instrument_id)
            return 0
        capability = (ProviderCapability.CN_ETF_QUOTE
                      if inst.instrument_type == "CN_ETF" else ProviderCapability.CN_DAILY_QUOTE)
        # 增量区间（第 3 步：上次成功 checkpoint → 现在）
        last = self.market_service.latest_trade_date(session, instrument_id)
        eff_start = start
        if last is not None:
            eff_start = max(start, date.fromordinal(last.toordinal() + 1))
        if eff_start > end:
            return 0   # 已覆盖

        def make_fetcher(provider_name: str):
            def fetcher(provider):
                symbol = resolve_provider_symbol(session, instrument_id, provider_name)
                if not symbol:
                    raise ProviderError(f"{provider_name}: 无 symbol 映射")
                return provider.get_price_history(instrument_id, eff_start, end)
            return fetcher

        chain = self.gateway.registry.fallback_chain(capability)
        if not chain:
            logger.warning("%s: 无可用 provider 链", instrument_id)
            return 0
        primary = chain[0].provider_name
        bars, decision = await self.gateway.fetch_with_fallback(
            capability, make_fetcher(primary),
            instrument_id=instrument_id,
            max_retries=2, backoff_base=1.0,
        )
        if not bars:
            return 0
        # raw artifact（第 5 步）：拉取结果序列化（SDK provider 无法取原始字节，
        # 以结果 JSON 为 artifact，transform_version 记录版本——TS-05 §7 注）
        label = f"{capability.value.lower()}_{instrument_id}_{eff_start}_{end}.json"
        payload = json.dumps(
            [b.model_dump(mode="json") for b in bars],
            ensure_ascii=False, default=str,
        ).encode("utf-8")
        raw = await self.raw_store.save(decision.actual_provider, MARKET_JOB, label, payload)
        # 第 6-7 步：normalize + 同事务落库（parquet 先写，PG 指针后写）
        summary = persist_market_bars(
            session, bars, raw=raw, ingestion_run_id=job.job_run_id,
            parquet_store=self.parquet_store,
        )
        session.commit()
        logger.info("market_sync %s: +%d bars (insert=%d update=%d)",
                    instrument_id, len(bars), summary.inserted, summary.updated)
        return len(bars)

    # ---- fundamental_sync_job（ACC-M1-003 支撑）----

    async def run_fundamental_sync(
        self,
        job_run_id: UUID,
        instruments: list[UUID],
        metrics: list[str],
        start_period: date,
        end_period: date,
    ) -> SyncResult:
        from app.fundamentals.repository import persist_financial_facts

        session = self.session_factory()
        try:
            self._start_job(session, job_run_id)
            total = 0
            for instrument_id in instruments:
                def fetcher(provider):
                    return provider.get_financial_history(
                        instrument_id, metrics, start_period, end_period)
                facts, decision = await self.gateway.fetch_with_fallback(
                    ProviderCapability.FINANCIAL_STATEMENTS, fetcher,
                    instrument_id=instrument_id,
                )
                if not facts:
                    continue
                label = f"fin_{instrument_id}_{start_period}_{end_period}.json"
                payload = json.dumps(
                    [f.model_dump(mode="json") for f in facts],
                    ensure_ascii=False, default=str,
                ).encode("utf-8")
                raw = await self.raw_store.save(decision.actual_provider, FUNDAMENTAL_JOB, label, payload)
                n = persist_financial_facts(session, facts, raw=raw, ingestion_run_id=job_run_id)
                session.commit()
                total += n
            self._finish_job(session, job_run_id, total)
            return SyncResult(job_run_id, len(instruments), total, total)
        except Exception as exc:  # noqa: BLE001
            self._fail_job(session, job_run_id, exc)
            raise
        finally:
            session.close()

    # ---- job 状态（第 8 步）----

    def _start_job(self, session: Session, job_run_id: UUID) -> JobRun:
        from datetime import datetime, timezone

        job = session.get(JobRun, job_run_id)
        if job is None:
            raise ValueError(f"job {job_run_id} 不存在")
        job.status = JobStatus.RUNNING.value
        job.started_at = datetime.now(timezone.utc)
        session.commit()
        return job

    def _finish_job(self, session: Session, job_run_id: UUID, items: int) -> None:
        from datetime import datetime, timezone

        job = session.get(JobRun, job_run_id)
        job.status = JobStatus.SUCCEEDED.value
        job.finished_at = datetime.now(timezone.utc)
        job.output_version = str(items)
        session.commit()

    def _fail_job(self, session: Session, job_run_id: UUID, exc: Exception) -> None:
        from datetime import datetime, timezone

        try:
            job = session.get(JobRun, job_run_id)
            if job is not None:
                job.status = JobStatus.FAILED.value
                job.finished_at = datetime.now(timezone.utc)
                job.error = f"{type(exc).__name__}: {exc}"[:2000]
                session.commit()
        except Exception:  # noqa: BLE001
            logger.exception("job 失败状态写入失败 %s", job_run_id)


def build_sync_runner(
    session_factory: Callable[[], Session],
    factory: ProviderFactory,
    registry,
    parquet_store: ParquetStore,
    raw_store: RawEvidenceStore,
) -> SyncJobRunner:
    """装配 SyncJobRunner：gateway 带限流 + fallback 审计 sink（双写强制点）。"""
    gateway = DataGateway(
        registry,
        provider_factory=lambda cls: factory.create(cls.provider_name),
        limiter_factory=lambda name: factory.limiter_for(name),
        audit_sink=provider_fallback_sink(session_factory),
    )
    return SyncJobRunner(session_factory, gateway, factory, parquet_store, raw_store)
