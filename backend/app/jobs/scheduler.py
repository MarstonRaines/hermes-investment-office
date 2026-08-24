"""Backend-owned deterministic compute scheduling (TS-06 §9 / M6).

This module records every compute stage in ``job_runs``.  Hermes cron is not
used to trigger these stages.  The implementation is deliberately callable
from a worker, a local CLI, or APScheduler without coupling the domain engines
to a scheduler library.
"""

from __future__ import annotations

import hashlib
import json
import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any
from uuid import UUID, uuid4

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.briefing.attention import AttentionEngine
from app.briefing.service import BriefingService
from app.calendar.service import CalendarService
from app.common.enums import JobStatus, JobType, MarketCode
from app.etf.models import ETFProfile
from app.etf.service import ETFDataService
from app.jobs.models import JobRun
from app.portfolio.models import Portfolio, PortfolioSnapshot, PositionSnapshot
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
    ) -> None:
        self.session_factory = session_factory
        self.briefing_service = briefing_service
        self.attention_engine = attention_engine
        self.etf_service = etf_service
        self.calendar = calendar or CalendarService()
        self.risk_thresholds = risk_thresholds or {}
        self.valuation_runner = valuation_runner

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
                engine_versions={"context_builder": "briefing-service/0.1.0"},
            )
            return str(context.daily_context_id)

        return self._run(session, "context_builder", params, handler, output_version="briefing-service/0.1.0")

    def run_etf_metric_job(self, market_date: date, instruments: list[UUID]) -> ComputeJobResult:
        session = self.session_factory()
        params = {"market_date": market_date.isoformat(), "instruments": sorted(map(str, instruments))}

        def handler(db: Session) -> int:
            if self.etf_service is None:
                return 0
            count = 0
            as_of = datetime.combine(market_date, datetime.max.time(), tzinfo=UTC)
            profiles = list(db.scalars(select(ETFProfile).where(
                ETFProfile.instrument_id.in_(instruments) if instruments else False,
            )).all())
            for profile in profiles:
                self.etf_service.refresh_metrics(db, profile.instrument_id, as_of=as_of)
                count += 1
            return count

        return self._run(session, "etf_metric_job", params, handler, output_version="etf-engine/0.1.0")

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
                result = compute_risk(
                    positions=values, nav=snapshot.nav_cny,
                    nav_history=[snapshot.nav_cny], thresholds=self.risk_thresholds,
                )
                snapshot.risk_summary = _json_safe(result)
                snapshot.exposures = _json_safe(result.get("exposure", {}))
                snapshot.engine_version = RISK_ENGINE_VERSION
                written += 1
            return written

        return self._run(session, "risk_job", params, handler, output_version=RISK_ENGINE_VERSION)

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
                "valuation_job": skipped, "etf_metric_job": skipped, "risk_job": skipped,
                "anomaly_job": skipped, "context_builder": skipped,
            }
        valuation = self.run_valuation_job(market_date, instruments, valuation_requests)
        etf = self.run_etf_metric_job(market_date, instruments)
        risk = self.run_risk_job(market_date, portfolio_ids)
        anomaly = self.run_anomaly_job(market_date, instruments, facts or [])
        context = self.run_context_builder(market_date, instruments)
        return {
            "valuation_job": valuation, "etf_metric_job": etf, "risk_job": risk,
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
            self.run_daily_pipeline(market_date_provider(), instruments)

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
        input_version = _fingerprint(job_name, params)
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
