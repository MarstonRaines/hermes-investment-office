"""Backend-owned deterministic compute scheduling (TS-06 §9 / M6).

This module records every compute stage in ``job_runs``.  Hermes cron is not
used to trigger these stages.  The implementation is deliberately callable
from a worker, a local CLI, or APScheduler without coupling the domain engines
to a scheduler library.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.briefing.attention import AttentionEngine
from app.briefing.service import BriefingService
from app.calendar.service import CalendarService
from app.calendar.source import fetch_sina_trade_dates
from app.common.enums import JobStatus, JobType, MarketCode
from app.corporate_actions.service import CorporateActionsService
from app.etf.models import ETFProfile
from app.etf.service import ETFDataService
from app.fundamentals.models import FROZEN_METRIC_CODES
from app.instruments.models import Instrument
from app.jobs.models import JobRun
from app.jobs.sync_jobs import ETF_JOB, FUNDAMENTAL_JOB, MARKET_JOB, SyncJobRunner
from app.market_data.service import MarketDataService
from app.portfolio.models import Portfolio, PortfolioSnapshot, PositionSnapshot
from app.portfolio.service import PortfolioService
from app.providers.contracts.base import ProviderCapability
from app.risk.engine import ENGINE_VERSION as RISK_ENGINE_VERSION
from app.risk.engine import compute_risk

logger = logging.getLogger(__name__)

__all__ = ["BackendScheduler", "ComputeJobResult", "SchedulerJobError"]


class SchedulerJobError(RuntimeError):
    code = "COMPUTE_JOB_FAILED"


@dataclass(frozen=True)
class ComputeJobResult:
    job_run_id: UUID | None
    status: str
    output_version: str | None = None
    skipped: bool = False


class BackendScheduler:
    """Small synchronous worker facade; APScheduler may call these methods."""

    def __init__(
        self,
        session_factory: Callable[[], Session],
        *,
        briefing_service: BriefingService,
        attention_engine: AttentionEngine,
        etf_service: ETFDataService | None = None,
        calendar: CalendarService | None = None,
        risk_thresholds: dict[str, Any] | None = None,
        valuation_runner: Callable[[Session, date, list[Any]], Any] | None = None,
        sync_runner: SyncJobRunner | None = None,
    ) -> None:
        self.session_factory = session_factory
        self.briefing_service = briefing_service
        self.attention_engine = attention_engine
        self.etf_service = etf_service
        self.calendar = calendar or CalendarService()
        self.risk_thresholds = risk_thresholds or {}
        self.valuation_runner = valuation_runner
        self.sync_runner = sync_runner

    def run_market_sync_job(
        self,
        market_date: date,
        instruments: list[UUID],
        *,
        lookback_days: int = 400,
        force: bool = False,
    ) -> ComputeJobResult:
        if self.sync_runner is None:
            return ComputeJobResult(None, "SKIPPED_NOT_CONFIGURED", skipped=True)
        session = self.session_factory()
        try:
            params = {
                "universe": sorted(map(str, instruments)),
                "start_date": (market_date - timedelta(days=lookback_days)).isoformat(),
                "end_date": market_date.isoformat(),
                "data_type": "OHLCVA",
            }
            job, created = self.sync_runner.create_sync_job(
                session, MARKET_JOB, params, check_idempotent=not force,
            )
            session.commit()
            job_id = job.job_run_id
            if not created:
                return ComputeJobResult(job_id, str(job.status), job.output_version)
        finally:
            _close_session(session)
        asyncio.run(self.sync_runner.run_job(job_id))
        verify = self.session_factory()
        try:
            row = verify.get(JobRun, job_id)
            return ComputeJobResult(
                job_id,
                str(row.status) if row else "FAILED",
                row.output_version if row else None,
            )
        finally:
            _close_session(verify)

    def run_fundamental_sync_job(
        self,
        market_date: date,
        instruments: list[UUID],
        *,
        lookback_years: int = 6,
        metrics: list[str] | None = None,
        force: bool = False,
    ) -> ComputeJobResult:
        """同步股票研究所需的冻结财务指标；空指标不得变成空跑任务。"""

        if self.sync_runner is None:
            return ComputeJobResult(None, "SKIPPED_NOT_CONFIGURED", skipped=True)
        requested = sorted(set(metrics or FROZEN_METRIC_CODES))
        session = self.session_factory()
        try:
            params = {
                "universe": sorted(map(str, instruments)),
                "metrics": requested,
                "start_period": (market_date - timedelta(days=365 * lookback_years)).isoformat(),
                "end_period": market_date.isoformat(),
            }
            job, created = self.sync_runner.create_sync_job(
                session, FUNDAMENTAL_JOB, params, check_idempotent=not force,
            )
            session.commit()
            job_id = job.job_run_id
            if not created:
                return ComputeJobResult(job_id, str(job.status), job.output_version)
        finally:
            _close_session(session)
        asyncio.run(self.sync_runner.run_job(job_id))
        verify = self.session_factory()
        try:
            row = verify.get(JobRun, job_id)
            return ComputeJobResult(
                job_id,
                str(row.status) if row else "FAILED",
                row.output_version if row else None,
            )
        finally:
            _close_session(verify)

    def run_etf_sync_job(
        self,
        market_date: date,
        instruments: list[UUID],
        *,
        lookback_days: int = 1095,
        force: bool = False,
    ) -> ComputeJobResult:
        """同步 ETF 净值、披露持仓、额度状态并生成指标快照。"""

        if self.sync_runner is None:
            return ComputeJobResult(None, "SKIPPED_NOT_CONFIGURED", skipped=True)
        session = self.session_factory()
        try:
            params = {
                "universe": sorted(map(str, instruments)),
                "start_date": (market_date - timedelta(days=lookback_days)).isoformat(),
                "end_date": market_date.isoformat(),
                "data_type": "ETF",
            }
            job, created = self.sync_runner.create_sync_job(
                session, ETF_JOB, params, check_idempotent=not force,
            )
            session.commit()
            job_id = job.job_run_id
            if not created:
                return ComputeJobResult(job_id, str(job.status), job.output_version)
        finally:
            _close_session(session)
        asyncio.run(self.sync_runner.run_job(job_id))
        verify = self.session_factory()
        try:
            row = verify.get(JobRun, job_id)
            return ComputeJobResult(
                job_id,
                str(row.status) if row else "FAILED",
                row.output_version if row else None,
            )
        finally:
            _close_session(verify)

    def run_corporate_action_sync_job(
        self,
        market_date: date,
        instruments: list[UUID],
        *,
        lookback_years: int = 8,
        force: bool = False,
    ) -> ComputeJobResult:
        """同步复权因子与已实施分红/送转，并落入 corporate_actions。"""

        if self.sync_runner is None:
            return ComputeJobResult(None, "SKIPPED_NOT_CONFIGURED", skipped=True)
        session = self.session_factory()
        params = {
            "market_date": market_date.isoformat(),
            "instruments": sorted(map(str, instruments)),
            "start_date": (market_date - timedelta(days=365 * lookback_years)).isoformat(),
        }
        if force:
            params["retry"] = str(uuid4())

        def handler(db: Session) -> int:
            service = CorporateActionsService(self.sync_runner.gateway)

            async def sync_all() -> int:
                written = 0
                successful_sources = 0
                provider_errors: list[str] = []
                start = market_date - timedelta(days=365 * lookback_years)
                for instrument_id in instruments:
                    try:
                        factors, decision = await self.sync_runner.gateway.fetch_with_fallback(
                            ProviderCapability.ADJ_FACTOR,
                            lambda provider, iid=instrument_id: provider.get_adj_factors(
                                iid, start, market_date,
                            ),
                            instrument_id=instrument_id,
                            max_retries=1,
                            backoff_base=1.0,
                        )
                    except Exception as exc:  # noqa: BLE001 - 单一来源失败不能阻断其他数据源
                        provider_errors.append(f"adj_factor:{type(exc).__name__}")
                        logger.warning("adj factor sync unavailable for %s: %s", instrument_id, exc)
                    else:
                        summary = await service.sync_adj_factors(
                            db, instrument_id, factors, source=decision.actual_provider,
                        )
                        written += int(summary["written"])
                        successful_sources += 1
                    try:
                        dividends = await self.sync_runner.gateway.fetch_extension(
                            "tushare",
                            lambda provider, iid=instrument_id: provider.get_dividends(iid),
                        )
                    except Exception as exc:  # noqa: BLE001 - 复权因子仍可独立完成
                        provider_errors.append(f"dividend:{type(exc).__name__}")
                        logger.warning("dividend sync unavailable for %s: %s", instrument_id, exc)
                    else:
                        written += await service.sync_dividends(db, instrument_id, dividends)
                        successful_sources += 1
                if instruments and successful_sources == 0:
                    errors = ", ".join(provider_errors[:4]) or "no provider response"
                    raise RuntimeError(f"公司行动数据源均不可用（{errors}）")
                db.commit()
                return written

            return asyncio.run(sync_all())

        return self._run(
            session,
            "corporate_action_sync_job",
            params,
            handler,
            output_version="corporate-actions-sync/0.1.0",
        )

    def consume_pending_sync_jobs(self) -> None:
        if self.sync_runner is None:
            return
        asyncio.run(self.sync_runner.run_pending_jobs(limit=5))

    def run_calendar_sync_job(self, market_date: date) -> ComputeJobResult:
        """刷新交易日历；外部源失败会记录 Job，不会覆盖已有日历。"""

        session = self.session_factory()
        params = {"market_date": market_date.isoformat(), "source": "sina_calendar"}

        def handler(db: Session) -> int:
            return self.calendar.sync_dates(db, fetch_sina_trade_dates())

        return self._run(
            session,
            "calendar_sync_job",
            params,
            handler,
            output_version="calendar-source/0.1.0",
        )

    def run_valuation_job(
        self, market_date: date, instruments: list[UUID],
        requests: list[Any] | None = None,
    ) -> ComputeJobResult:
        session = self.session_factory()
        params = {
            "market_date": market_date.isoformat(),
            "instruments": sorted(map(str, instruments)),
            "requests": _json_safe(requests or []),
        }

        def handler(db: Session) -> int:
            if self.valuation_runner is None or not requests:
                return 0
            result = self.valuation_runner(db, market_date, requests)
            return int(result) if isinstance(result, int) else len(result or [])

        return self._run(session, "valuation_job", params, handler, output_version="valuation-engine/0.1.0")

    def run_context_builder(
        self, market_date: date, instruments: list[UUID],
    ) -> ComputeJobResult:
        session = self.session_factory()
        if not self.calendar.is_trading_day(session, market_date, MarketCode.CN):
            _close_session(session)
            return ComputeJobResult(None, "SKIPPED_NON_TRADING_DAY", skipped=True)
        params = {"market_date": market_date.isoformat(), "instruments": sorted(map(str, instruments))}

        def handler(db: Session) -> str:
            context = self.briefing_service.build_daily_context(
                db, market_date, instruments=instruments,
                engine_versions={"context_builder": "context-builder/0.1.2"},
            )
            return str(context.daily_context_id)

        return self._run(session, "context_builder", params, handler, output_version="context-builder/0.1.2")

    def run_etf_metric_job(self, market_date: date, instruments: list[UUID]) -> ComputeJobResult:
        session = self.session_factory()
        params = {"market_date": market_date.isoformat(), "instruments": sorted(map(str, instruments))}

        def handler(db: Session) -> int:
            if self.etf_service is None:
                return 0
            count = 0
            as_of = min(
                datetime.now(UTC),
                datetime.combine(market_date, datetime.max.time(), tzinfo=UTC),
            )
            profiles = list(db.scalars(select(ETFProfile).where(
                ETFProfile.instrument_id.in_(instruments) if instruments else False,
            )).all())
            for profile in profiles:
                asyncio.run(
                    self.etf_service.refresh_metrics(
                        db, profile.instrument_id, as_of=as_of
                    )
                )
                count += 1
            return count

        return self._run(session, "etf_metric_job", params, handler, output_version="etf-metric-job/0.1.1")

    def run_risk_job(self, market_date: date, portfolio_ids: list[UUID] | None = None) -> ComputeJobResult:
        session = self.session_factory()
        params = {"market_date": market_date.isoformat(), "portfolios": sorted(map(str, portfolio_ids or []))}

        def handler(db: Session) -> int:
            stmt = select(Portfolio)
            if portfolio_ids:
                stmt = stmt.where(Portfolio.portfolio_id.in_(portfolio_ids))
            portfolios = list(db.scalars(stmt).all())
            written = 0
            for portfolio in portfolios:
                snapshot = db.scalar(select(PortfolioSnapshot).where(
                    PortfolioSnapshot.portfolio_id == portfolio.portfolio_id,
                    PortfolioSnapshot.snapshot_date <= market_date,
                ).order_by(PortfolioSnapshot.snapshot_date.desc()).limit(1))
                if snapshot is None or snapshot.nav_cny is None:
                    continue
                positions = list(db.scalars(select(PositionSnapshot).where(
                    PositionSnapshot.portfolio_id == portfolio.portfolio_id,
                    PositionSnapshot.snapshot_date == snapshot.snapshot_date,
                )).all())
                values = {str(row.instrument_id): row.market_value_cny or 0 for row in positions}
                history = list(db.scalars(select(PortfolioSnapshot).where(
                    PortfolioSnapshot.portfolio_id == portfolio.portfolio_id,
                    PortfolioSnapshot.snapshot_date <= market_date,
                ).order_by(PortfolioSnapshot.snapshot_date.asc())).all())
                asset_classes = {
                    str(row.instrument_id): row.instrument_type
                    for row in db.scalars(select(Instrument).where(
                        Instrument.instrument_id.in_([item.instrument_id for item in positions])
                    )).all()
                } if positions else {}
                result = compute_risk(
                    positions=values, nav=snapshot.nav_cny,
                    nav_history=[row.nav_cny for row in history],
                    snapshot_dates=[row.snapshot_date for row in history],
                    cash_cny=snapshot.cash_cny,
                    asset_classes=asset_classes,
                    thresholds=self.risk_thresholds,
                    as_of=snapshot.as_of,
                )
                snapshot.risk_summary = _json_safe(result)
                snapshot.exposures = _json_safe(result.get("exposure", {}))
                snapshot.engine_version = RISK_ENGINE_VERSION
                written += 1
            return written

        return self._run(session, "risk_job", params, handler, output_version=RISK_ENGINE_VERSION)

    def run_portfolio_snapshot_job(
        self, market_date: date, portfolio_ids: list[UUID] | None = None,
    ) -> ComputeJobResult:
        session = self.session_factory()
        params = {
            "market_date": market_date.isoformat(),
            "portfolios": sorted(map(str, portfolio_ids or [])),
        }

        def handler(db: Session) -> int:
            stmt = select(Portfolio)
            if portfolio_ids:
                stmt = stmt.where(Portfolio.portfolio_id.in_(portfolio_ids))
            portfolios = list(db.scalars(stmt).all())
            market = MarketDataService.from_settings()
            service = PortfolioService()
            written = 0
            for portfolio in portfolios:
                replayed = service.replay_portfolio(db, portfolio.portfolio_id, as_of=market_date)
                prices: dict[UUID, Decimal] = {}
                for instrument_id, position in replayed.positions.items():
                    bars = market.get_ohlcva(db, instrument_id, as_of=market_date)
                    close = bars[-1].get("close") if bars else None
                    if close is not None:
                        prices[instrument_id] = Decimal(str(close))
                    elif position.avg_cost > 0:
                        prices[instrument_id] = position.avg_cost
                service.snapshot(db, portfolio.portfolio_id, market_date, prices)
                written += 1
            return written

        return self._run(
            session,
            "portfolio_snapshot_job",
            params,
            handler,
            output_version="portfolio-engine/0.1.0",
        )

    def run_anomaly_job(
        self, market_date: date, instruments: list[UUID], facts: list[dict[str, Any]],
    ) -> ComputeJobResult:
        session = self.session_factory()
        params = {"market_date": market_date.isoformat(), "instruments": sorted(map(str, instruments))}

        def handler(db: Session) -> int:
            context = self.briefing_service.get_daily_context(db, market_date)
            if context is None:
                context = self.briefing_service.build_daily_context(
                    db, market_date, instruments=instruments,
                )
            result = self.attention_engine.evaluate(db, context, facts)
            self._trigger_thesis_reviews(db, facts)
            return len(result.items)

        return self._run(session, "anomaly_job", params, handler, output_version=self.attention_engine.ENGINE_VERSION)

    @staticmethod
    def _trigger_thesis_reviews(db: Session, facts: list[dict[str, Any]]) -> None:
        """Move an active thesis to UNDER_REVIEW when a normalized red flag fires."""
        from app.common.enums import ThesisLifecycleStatus
        from app.thesis.models import Thesis
        from app.thesis.service import ThesisService

        service = ThesisService()
        for fact in facts:
            if not fact.get("red_flag_triggered") and fact.get("red_flag_status") != "TRIGGERED":
                continue
            thesis_id = fact.get("thesis_id")
            if not thesis_id:
                continue
            thesis = db.get(Thesis, UUID(str(thesis_id)))
            if thesis is not None and thesis.lifecycle_status == ThesisLifecycleStatus.ACTIVE.value:
                service.transition_lifecycle(
                    db, thesis.thesis_id, ThesisLifecycleStatus.UNDER_REVIEW,
                    actor="JOB", reason="red flag triggered by anomaly pipeline",
                )

    def run_daily_pipeline(
        self, market_date: date, instruments: list[UUID], *, facts: list[dict[str, Any]] | None = None,
        portfolio_ids: list[UUID] | None = None, valuation_requests: list[Any] | None = None,
    ) -> dict[str, ComputeJobResult]:
        """Run the local EOD compute chain with bounded, idempotent stages."""
        if not self._is_trading_day_for_pipeline(market_date):
            skipped = ComputeJobResult(None, "SKIPPED_NON_TRADING_DAY", skipped=True)
            return {
                "valuation_job": skipped, "etf_metric_job": skipped,
                "portfolio_snapshot_job": skipped, "risk_job": skipped,
                "anomaly_job": skipped, "context_builder": skipped,
            }

        def isolated(name: str, operation: Callable[[], ComputeJobResult]) -> ComputeJobResult:
            try:
                return operation()
            except Exception as exc:  # noqa: BLE001
                logger.exception("daily pipeline stage failed: %s", name)
                return ComputeJobResult(
                    None, "FAILED", output_version=f"{type(exc).__name__}: {exc}"
                )

        valuation = isolated(
            "valuation_job",
            lambda: self.run_valuation_job(market_date, instruments, valuation_requests),
        )
        etf = isolated(
            "etf_metric_job", lambda: self.run_etf_metric_job(market_date, instruments)
        )
        snapshots = isolated(
            "portfolio_snapshot_job",
            lambda: self.run_portfolio_snapshot_job(market_date, portfolio_ids),
        )
        risk = isolated("risk_job", lambda: self.run_risk_job(market_date, portfolio_ids))
        anomaly = isolated(
            "anomaly_job", lambda: self.run_anomaly_job(market_date, instruments, facts or [])
        )
        context = isolated(
            "context_builder", lambda: self.run_context_builder(market_date, instruments)
        )
        return {
            "valuation_job": valuation, "etf_metric_job": etf,
            "portfolio_snapshot_job": snapshots, "risk_job": risk,
            "anomaly_job": anomaly, "context_builder": context,
        }

    def _is_trading_day_for_pipeline(self, market_date: date) -> bool:
        """Use the persisted calendar before any EOD stage is opened."""
        session = self.session_factory()
        if session is None:
            # Unit-test stubs may intentionally omit a database.  A real
            # scheduler session never takes this branch.
            return True
        try:
            return self.calendar.is_trading_day(session, market_date, MarketCode.CN)
        finally:
            _close_session(session)

    def build_apscheduler(
        self,
        *,
        instruments_provider: Callable[[], list[UUID]],
        market_date_provider: Callable[[], date] = date.today,
        timezone: str = "Asia/Shanghai",
        hour: int = 18,
        minute: int = 0,
    ) -> BackgroundScheduler:
        """Register the explicit daily EOD pipeline without starting it.

        The caller owns ``start``/``shutdown``.  An empty configured universe is
        a no-op, so enabling this cannot implicitly seed or overwrite data.
        """
        scheduler = BackgroundScheduler(timezone=timezone)

        def run_pipeline() -> None:
            instruments = list(instruments_provider())
            if not instruments:
                logger.info("daily pipeline skipped: no configured watchlist universe")
                return
            market_date = market_date_provider()
            try:
                self.run_calendar_sync_job(market_date)
            except Exception:  # noqa: BLE001
                logger.exception("calendar sync failed; persisted calendar remains authoritative")
            if not self._is_trading_day_for_pipeline(market_date):
                logger.info("daily pipeline skipped: %s is not a CN trading day", market_date)
                return
            try:
                self.run_market_sync_job(market_date, instruments)
            except Exception:  # noqa: BLE001
                logger.exception("market sync stage failed; deterministic stages continue")
            session = self.session_factory()
            try:
                instrument_types = list(session.execute(select(
                    Instrument.instrument_id,
                    Instrument.instrument_type,
                ).where(Instrument.instrument_id.in_(instruments))).all())
            finally:
                _close_session(session)
            equity_ids = [row.instrument_id for row in instrument_types if row.instrument_type == "CN_EQUITY"]
            etf_ids = [row.instrument_id for row in instrument_types if row.instrument_type == "CN_ETF"]
            if equity_ids:
                try:
                    self.run_fundamental_sync_job(
                        market_date, equity_ids, lookback_years=2,
                    )
                except Exception:  # noqa: BLE001
                    logger.exception("fundamental sync stage failed; deterministic stages continue")
            if etf_ids:
                try:
                    self.run_etf_sync_job(market_date, etf_ids, lookback_days=1095)
                except Exception:  # noqa: BLE001
                    logger.exception("ETF sync stage failed; deterministic stages continue")
            try:
                self.run_corporate_action_sync_job(
                    market_date, instruments, lookback_years=3,
                )
            except Exception:  # noqa: BLE001
                logger.exception("corporate action sync stage failed; deterministic stages continue")
            self.run_daily_pipeline(market_date, instruments)

        scheduler.add_job(
            run_pipeline,
            trigger=CronTrigger(
                day_of_week="mon-fri", hour=hour, minute=minute, timezone=timezone,
            ),
            id="hermes_daily_pipeline",
            replace_existing=True,
            coalesce=True,
            max_instances=1,
            misfire_grace_time=3600,
        )
        if self.sync_runner is not None:
            scheduler.add_job(
                self.consume_pending_sync_jobs,
                trigger=IntervalTrigger(minutes=1, timezone=timezone),
                id="hermes_sync_worker",
                replace_existing=True,
                coalesce=True,
                max_instances=1,
                misfire_grace_time=120,
            )
        return scheduler

    def _run(
        self, session: Session, job_name: str, params: dict[str, Any],
        handler: Callable[[Session], Any], *, output_version: str,
    ) -> ComputeJobResult:
        try:
            return self._run_open(session, job_name, params, handler, output_version=output_version)
        finally:
            _close_session(session)

    def _run_open(
        self, session: Session, job_name: str, params: dict[str, Any],
        handler: Callable[[Session], Any], *, output_version: str,
    ) -> ComputeJobResult:
        # 相同输入只有在相同实现版本下才可复用；升级计算逻辑后必须产生新结果。
        input_version = _fingerprint(job_name, {**params, "_output_version": output_version})
        existing = session.scalar(select(JobRun).where(
            JobRun.job_name == job_name, JobRun.input_version == input_version,
            JobRun.status.in_([JobStatus.PENDING.value, JobStatus.RUNNING.value, JobStatus.SUCCEEDED.value]),
        ).order_by(JobRun.created_at.desc()).limit(1))
        if existing is not None:
            return ComputeJobResult(existing.job_run_id, existing.status, existing.output_version)
        job = JobRun(
            job_run_id=uuid4(), job_name=job_name, job_type=JobType.COMPUTE_JOB.value,
            status=JobStatus.RUNNING.value, input_version=input_version,
            params=params, started_at=datetime.now(UTC),
        )
        session.add(job)
        session.commit()
        try:
            output = handler(session)
            job = session.get(JobRun, job.job_run_id)
            job.status = JobStatus.SUCCEEDED.value
            job.finished_at = datetime.now(UTC)
            job.output_version = output_version
            job.params = {**params, "output": str(output)}
            session.commit()
            return ComputeJobResult(job.job_run_id, job.status, job.output_version)
        except Exception as exc:  # noqa: BLE001
            session.rollback()
            failed = session.get(JobRun, job.job_run_id)
            if failed is not None:
                failed.status = JobStatus.FAILED.value
                failed.finished_at = datetime.now(UTC)
                failed.error = f"{type(exc).__name__}: {exc}"[:2000]
                session.commit()
            raise SchedulerJobError(f"{job_name} failed: {exc}") from exc


def _fingerprint(job_name: str, params: dict[str, Any]) -> str:
    canonical = json.dumps(params, sort_keys=True, default=str, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(f"{job_name}:{canonical}".encode()).hexdigest()


def _json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    if hasattr(value, "isoformat"):
        return value.isoformat()
    if hasattr(value, "value"):
        return value.value
    return str(value) if not isinstance(value, (str, int, float, bool, type(None))) else value


def _close_session(session: Any) -> None:
    close = getattr(session, "close", None)
    if close is not None:
        close()
