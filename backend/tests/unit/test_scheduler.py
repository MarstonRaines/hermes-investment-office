from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path
from unittest.mock import AsyncMock

from app.briefing.attention import AttentionEngine
from app.briefing.service import BriefingService
from app.calendar.service import CalendarService
from app.common.enums import ThesisLifecycleStatus
from app.etf.models import ETFProfile
from app.jobs.models import JobRun
from app.jobs.scheduler import BackendScheduler, ComputeJobResult
from app.market_data.service import MarketDataService
from app.thesis.service import ThesisService


def test_context_builder_job_is_idempotent(db_session, instrument) -> None:
    calendar = CalendarService()
    calendar.sync_dates(db_session, [date(2026, 8, 24)])
    instrument_id = instrument.instrument_id
    scheduler = BackendScheduler(
        lambda: db_session,
        briefing_service=BriefingService(MarketDataService(None), calendar),
        attention_engine=AttentionEngine(Path("config/attention_rules.yaml")),
    )

    first = scheduler.run_context_builder(date(2026, 8, 24), [instrument_id])
    second = scheduler.run_context_builder(date(2026, 8, 24), [instrument_id])

    assert first.job_run_id == second.job_run_id
    assert first.status == "SUCCEEDED"
    assert db_session.query(JobRun).filter(JobRun.job_name == "context_builder").count() == 1


def test_compute_job_version_change_invalidates_idempotence(db_session) -> None:
    scheduler = BackendScheduler(
        lambda: db_session,
        briefing_service=BriefingService(MarketDataService(None)),
        attention_engine=AttentionEngine(Path("config/attention_rules.yaml")),
    )

    first = scheduler._run_open(
        db_session, "versioned_compute_test", {"input": "same"},
        lambda _session: 1, output_version="engine/1",
    )
    second = scheduler._run_open(
        db_session, "versioned_compute_test", {"input": "same"},
        lambda _session: 1, output_version="engine/2",
    )

    assert first.job_run_id != second.job_run_id
    assert db_session.query(JobRun).filter(
        JobRun.job_name == "versioned_compute_test"
    ).count() == 2


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


def test_daily_pipeline_orders_valuation_before_context(monkeypatch) -> None:
    scheduler = BackendScheduler(
        lambda: None,
        briefing_service=BriefingService(MarketDataService(None)),
        attention_engine=AttentionEngine(Path("config/attention_rules.yaml")),
    )
    calls = []

    def stage(name):
        def run(*args, **kwargs):
            calls.append(name)
            return ComputeJobResult(None, "SUCCEEDED")
        return run

    for name in ("run_valuation_job", "run_etf_metric_job", "run_portfolio_snapshot_job", "run_risk_job",
                 "run_anomaly_job", "run_context_builder"):
        monkeypatch.setattr(scheduler, name, stage(name))

    scheduler.run_daily_pipeline(date(2026, 8, 24), [])
    assert calls == [
        "run_valuation_job", "run_etf_metric_job", "run_portfolio_snapshot_job", "run_risk_job",
        "run_anomaly_job", "run_context_builder",
    ]


def test_daily_pipeline_skips_all_stages_on_non_trading_day(db_session) -> None:
    scheduler = BackendScheduler(
        lambda: db_session,
        briefing_service=BriefingService(MarketDataService(None)),
        attention_engine=AttentionEngine(Path("config/attention_rules.yaml")),
    )

    result = scheduler.run_daily_pipeline(date(2026, 8, 25), [])

    assert set(result) == {
        "valuation_job", "etf_metric_job", "portfolio_snapshot_job", "risk_job", "anomaly_job", "context_builder",
    }
    assert all(item.status == "SKIPPED_NON_TRADING_DAY" and item.skipped for item in result.values())


def test_valuation_runner_accepts_only_explicit_requests(db_session) -> None:
    calls = []
    scheduler = BackendScheduler(
        lambda: db_session,
        briefing_service=BriefingService(MarketDataService(None)),
        attention_engine=AttentionEngine(Path("config/attention_rules.yaml")),
        valuation_runner=lambda session, market_date, requests: calls.append(
            (market_date, requests)
        ) or len(requests),
    )

    marker = object()
    result = scheduler.run_valuation_job(date(2026, 8, 24), [], [marker])

    assert result.status == "SUCCEEDED"
    assert calls == [(date(2026, 8, 24), [marker])]


def test_etf_metric_job_awaits_async_refresh(db_session, instrument) -> None:
    db_session.add(ETFProfile(
        instrument_id=instrument.instrument_id,
        is_qdii=False,
    ))
    db_session.flush()
    etf_service = type("ETFServiceStub", (), {})()
    etf_service.refresh_metrics = AsyncMock(return_value=None)
    scheduler = BackendScheduler(
        lambda: db_session,
        briefing_service=BriefingService(MarketDataService(None)),
        attention_engine=AttentionEngine(Path("config/attention_rules.yaml")),
        etf_service=etf_service,
    )

    before = datetime.now(UTC)
    result = scheduler.run_etf_metric_job(date.today(), [instrument.instrument_id])

    assert result.status == "SUCCEEDED"
    etf_service.refresh_metrics.assert_awaited_once()
    assert before <= etf_service.refresh_metrics.await_args.kwargs["as_of"] <= datetime.now(UTC)


def test_red_flag_triggers_active_thesis_review(db_session, instrument) -> None:
    thesis = ThesisService().create_thesis(
        db_session, instrument.instrument_id, "触发测试", {"claim": "x"},
    )
    ThesisService().transition_lifecycle(
        db_session, thesis.thesis_id, ThesisLifecycleStatus.ACTIVE,
        actor="HUMAN", reason="验收",
    )

    BackendScheduler._trigger_thesis_reviews(db_session, [{
        "thesis_id": str(thesis.thesis_id), "red_flag_triggered": True,
    }])
    db_session.flush()

    assert db_session.get(type(thesis), thesis.thesis_id).lifecycle_status == "UNDER_REVIEW"
