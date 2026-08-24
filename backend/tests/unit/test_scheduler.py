from __future__ import annotations

from datetime import date
from pathlib import Path

from app.briefing.attention import AttentionEngine
from app.briefing.service import BriefingService
from app.calendar.service import CalendarService
from app.jobs.models import JobRun
from app.jobs.scheduler import BackendScheduler
from app.market_data.service import MarketDataService


def test_context_builder_job_is_idempotent(db_session, instrument) -> None:
    calendar = CalendarService()
    calendar.sync_dates(db_session, [date(2026, 8, 24)])
    scheduler = BackendScheduler(
        lambda: db_session,
        briefing_service=BriefingService(MarketDataService(None), calendar),
        attention_engine=AttentionEngine(Path("config/attention_rules.yaml")),
    )

    first = scheduler.run_context_builder(date(2026, 8, 24), [instrument.instrument_id])
    second = scheduler.run_context_builder(date(2026, 8, 24), [instrument.instrument_id])

    assert first.job_run_id == second.job_run_id
    assert first.status == "SUCCEEDED"
    assert db_session.query(JobRun).filter(JobRun.job_name == "context_builder").count() == 1


def test_apscheduler_registers_only_explicit_daily_pipeline() -> None:
    scheduler = BackendScheduler(
        lambda: None,
        briefing_service=BriefingService(MarketDataService(None)),
        attention_engine=AttentionEngine(Path("config/attention_rules.yaml")),
    ).build_apscheduler(
        instruments_provider=lambda: [],
        market_date_provider=lambda: date(2026, 8, 24),
        hour=18,
        minute=5,
    )
    scheduler.start(paused=True)
    try:
        job = scheduler.get_job("hermes_daily_pipeline")
        assert job is not None
        assert job.max_instances == 1
        assert job.coalesce is True
    finally:
        scheduler.shutdown(wait=False)
