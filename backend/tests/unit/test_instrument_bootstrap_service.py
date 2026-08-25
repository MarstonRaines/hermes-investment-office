"""标的初始化编排回归测试。"""

from __future__ import annotations

from app.jobs.scheduler import ComputeJobResult
from app.operations.service import InstrumentBootstrapService
from app.thesis.models import Thesis, ThesisRevision


class _SuccessfulScheduler:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def run_market_sync_job(self, *_args, **_kwargs) -> ComputeJobResult:
        self.calls.append("market")
        return ComputeJobResult(None, "SUCCEEDED", "0")

    def run_fundamental_sync_job(self, *_args, **_kwargs) -> ComputeJobResult:
        self.calls.append("fundamentals")
        return ComputeJobResult(None, "SUCCEEDED", "0")

    def run_corporate_action_sync_job(self, *_args, **_kwargs) -> ComputeJobResult:
        self.calls.append("corporate_actions")
        return ComputeJobResult(None, "SUCCEEDED", "0")


def test_bootstrap_creates_research_skeleton_and_reports_every_stage(
    db_session,
    instrument,
) -> None:
    scheduler = _SuccessfulScheduler()

    result = InstrumentBootstrapService().run(db_session, scheduler, instrument)

    assert result["status"] == "PARTIAL"
    assert scheduler.calls == ["market", "fundamentals", "corporate_actions"]
    assert [stage["code"] for stage in result["stages"]] == [
        "market",
        "fundamentals",
        "corporate_actions",
        "filings",
        "thesis",
    ]
    assert result["stages"][0]["status"] == "EMPTY"
    assert result["stages"][1]["status"] == "EMPTY"
    thesis = db_session.query(Thesis).filter_by(
        instrument_id=instrument.instrument_id,
    ).one()
    assert thesis.lifecycle_status == "DRAFT"
    revision = db_session.get(ThesisRevision, thesis.current_revision_id)
    assert revision is not None
    assert "基础研究档案" in revision.summary


def test_bootstrap_is_idempotent_for_the_baseline_thesis(db_session, instrument) -> None:
    scheduler = _SuccessfulScheduler()
    service = InstrumentBootstrapService()

    first = service.run(db_session, scheduler, instrument)
    second = service.run(db_session, scheduler, instrument)

    assert first["stages"][-1]["message"] == "已建立研究骨架"
    assert second["stages"][-1]["message"] == "沿用现有研究档案"
    assert db_session.query(Thesis).filter_by(
        instrument_id=instrument.instrument_id,
    ).count() == 1
