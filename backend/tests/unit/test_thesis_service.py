# =====================================================================
# tests/unit/test_thesis_service.py —— Thesis 最小版（STM-THS-001~009 + GOLD-PIT-002）
# =====================================================================
from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

import pytest

import app.models  # noqa: F401

from app.common.enums import (
    RedFlagSeverity,
    RedFlagStatus,
    ThesisEventType,
    ThesisHealthStatus,
    ThesisLifecycleStatus,
)
from app.thesis.models import ThesisEvent, ThesisRedFlag, ThesisRevision
from app.thesis.service import (
    InvalidThesisTransitionError,
    RevisionConflictError,
    ThesisDomainError,
    ThesisService,
)

NOW = datetime(2026, 8, 21, tzinfo=timezone.utc)


def test_stm_ths_001_legal_lifecycle_transitions(db_session, instrument) -> None:
    """全合法迁移逐一执行 + STATUS_CHANGED 事件带 actor/reason。"""
    svc = ThesisService()
    thesis = svc.create_thesis(db_session, instrument.instrument_id, "茅台", {"t": "v1"})
    db_session.flush()
    assert thesis.lifecycle_status == ThesisLifecycleStatus.DRAFT.value

    svc.transition_lifecycle(db_session, thesis.thesis_id, ThesisLifecycleStatus.ACTIVE,
                             actor="HUMAN", reason="研究完成")
    svc.transition_lifecycle(db_session, thesis.thesis_id, ThesisLifecycleStatus.UNDER_REVIEW,
                             actor="SYSTEM", reason="季报触发")
    svc.transition_lifecycle(db_session, thesis.thesis_id, ThesisLifecycleStatus.ACTIVE,
                             actor="HUMAN", reason="复核通过")
    svc.transition_lifecycle(db_session, thesis.thesis_id, ThesisLifecycleStatus.INVALIDATED,
                             actor="HUMAN", reason="逻辑破裂")
    svc.transition_lifecycle(db_session, thesis.thesis_id, ThesisLifecycleStatus.ARCHIVED,
                             actor="SYSTEM", reason="归档")
    db_session.flush()
    db_session.expire_all()
    assert db_session.get(type(thesis), thesis.thesis_id).lifecycle_status == "ARCHIVED"
    events = db_session.query(ThesisEvent).filter_by(thesis_id=thesis.thesis_id).all()
    status_events = [e for e in events if e.event_type == ThesisEventType.STATUS_CHANGED.value]
    assert len(status_events) == 5
    assert all(e.event_data.get("actor") and e.event_data.get("reason") for e in status_events)


def test_stm_ths_002_illegal_transitions_typed_error(db_session, instrument) -> None:
    svc = ThesisService()
    thesis = svc.create_thesis(db_session, instrument.instrument_id, "t", {"b": 1})
    db_session.flush()
    with pytest.raises(InvalidThesisTransitionError):
        svc.transition_lifecycle(db_session, thesis.thesis_id, ThesisLifecycleStatus.UNDER_REVIEW,
                                 actor="H", reason="跳步")
    # DRAFT → ACTIVE → 再尝试 ACTIVE→DRAFT（非法）
    svc.transition_lifecycle(db_session, thesis.thesis_id, ThesisLifecycleStatus.ACTIVE, actor="H", reason="r")
    with pytest.raises(InvalidThesisTransitionError):
        svc.transition_lifecycle(db_session, thesis.thesis_id, ThesisLifecycleStatus.DRAFT, actor="H", reason="r")
    db_session.flush()
    db_session.expire_all()
    assert db_session.get(type(thesis), thesis.thesis_id).lifecycle_status == "ACTIVE"


def test_stm_ths_003_health_orthogonal(db_session, instrument) -> None:
    """health 独立于 lifecycle 迁移（STM-THS-003/005）。"""
    svc = ThesisService()
    thesis = svc.create_thesis(db_session, instrument.instrument_id, "t", {"b": 1})
    db_session.flush()
    svc.transition_health(db_session, thesis.thesis_id, ThesisHealthStatus.HEALTHY,
                          actor="HUMAN", reason="财报验证")
    svc.transition_health(db_session, thesis.thesis_id, ThesisHealthStatus.WARNING,
                          actor="SYSTEM", reason="毛利下滑")
    svc.transition_health(db_session, thesis.thesis_id, ThesisHealthStatus.HEALTHY,
                          actor="HUMAN", reason="证据恢复")
    db_session.flush()
    db_session.expire_all()
    assert db_session.get(type(thesis), thesis.thesis_id).health_status == "HEALTHY"
    # health 迁移不改变 lifecycle
    assert db_session.get(type(thesis), thesis.thesis_id).lifecycle_status == "DRAFT"
    # 无 actor/reason → 拒绝（STM-THS-005）
    with pytest.raises(ThesisDomainError):
        svc.transition_health(db_session, thesis.thesis_id, ThesisHealthStatus.WARNING, actor="", reason="")


def test_stm_ths_006_007_revision_immutable_and_monotonic(db_session, instrument) -> None:
    """revision 版本单调 + base 过期 → 409 DOMAIN_CONFLICT。"""
    svc = ThesisService()
    thesis = svc.create_thesis(db_session, instrument.instrument_id, "t", {"v": 1})
    db_session.flush()
    rev1 = svc.get_thesis(db_session, thesis.thesis_id)
    rev2 = svc.create_revision(db_session, thesis.thesis_id, {"v": 2},
                               base_revision_id=rev1.thesis_revision_id,
                               change_reason="更新")
    db_session.flush()
    assert rev2.version == 2
    # base 过期 → 冲突
    with pytest.raises(RevisionConflictError):
        svc.create_revision(db_session, thesis.thesis_id, {"v": 3},
                            base_revision_id=rev1.thesis_revision_id, change_reason="旧 base")
    # revision 不可变（append-only 触发器）
    from sqlalchemy import text

    with pytest.raises(Exception):
        db_session.execute(text("UPDATE thesis_revisions SET summary='x' WHERE thesis_revision_id = :id"),
                           {"id": rev1.thesis_revision_id})


def test_gold_pit_002_thesis_asof_version(db_session, instrument) -> None:
    """get_thesis(as_of) 返回 created_at <= as_of 的最近版本（PIT）。

    created_at 是 DB server_default；用显式 created_at 的原始 INSERT 构造
    v1（5 月）与 v2（7 月），验证 6 月时点只能看到 v1。
    """
    from sqlalchemy import text
    from app.thesis.models import Thesis

    thesis = Thesis(
        thesis_id=uuid4(), instrument_id=instrument.instrument_id,
        lifecycle_status="DRAFT", health_status="UNKNOWN",
    )
    db_session.add(thesis)
    db_session.flush()
    rev1_id = uuid4()
    rev2_id = uuid4()
    db_session.execute(text(
        "INSERT INTO thesis_revisions (thesis_revision_id, thesis_id, version, thesis_body, "
        "change_reason, authored_by, created_at) "
        "VALUES (:r1, :t, 1, CAST(:b1 AS jsonb), 'init', 'HUMAN', '2026-05-01T00:00:00+00:00'), "
        "(:r2, :t, 2, CAST(:b2 AS jsonb), 'jul', 'HUMAN', '2026-07-01T00:00:00+00:00')"),
        {"r1": rev1_id, "r2": rev2_id, "t": thesis.thesis_id,
         "b1": '{"v": 1}', "b2": '{"v": 2}'})
    db_session.execute(text("UPDATE theses SET current_revision_id = :r2 WHERE thesis_id = :t"),
                       {"r2": rev2_id, "t": thesis.thesis_id})
    db_session.flush()

    svc = ThesisService()
    head = svc.get_thesis(db_session, thesis.thesis_id)
    assert head.thesis_revision_id == rev2_id
    pit = svc.get_thesis(db_session, thesis.thesis_id, as_of=datetime(2026, 6, 15, tzinfo=timezone.utc))
    assert pit.thesis_revision_id == rev1_id   # 6 月时点只能看到 v1（5 月修订）


def test_stm_ths_009_red_flag_state_machine(db_session, instrument) -> None:
    """ARMED→TRIGGERED→RESOLVED；非法迁移 → typed error。"""
    svc = ThesisService()
    thesis = svc.create_thesis(db_session, instrument.instrument_id, "t", {"b": 1})
    db_session.flush()
    flag = svc.arm_red_flag(db_session, thesis.thesis_id, "红线", "condition",
                            severity=RedFlagSeverity.RED_LINE)
    db_session.flush()
    assert flag.status == RedFlagStatus.ARMED.value
    with pytest.raises(InvalidThesisTransitionError):
        svc.resolve_red_flag(db_session, flag.red_flag_id, actor="H", resolution="未触发不能解决")
    svc.trigger_red_flag(db_session, flag.red_flag_id, actor="SYSTEM", evidence="财报")
    db_session.flush()
    assert db_session.get(ThesisRedFlag, flag.red_flag_id).status == "TRIGGERED"
    # TRIGGERED 事件（RED_LINE 触发必须重新评估的审计痕迹）
    events = db_session.query(ThesisEvent).filter_by(
        thesis_id=thesis.thesis_id,
        event_type=ThesisEventType.RED_FLAG_TRIGGERED.value).all()
    assert len(events) == 1
    svc.resolve_red_flag(db_session, flag.red_flag_id, actor="HUMAN", resolution="处理")
    db_session.flush()
    assert db_session.get(ThesisRedFlag, flag.red_flag_id).status == "RESOLVED"
    # RESOLVED → 无出边
    with pytest.raises(InvalidThesisTransitionError):
        svc.trigger_red_flag(db_session, flag.red_flag_id, actor="H", evidence="x")


def test_thesis_missing_raises(db_session) -> None:
    svc = ThesisService()
    with pytest.raises(ThesisDomainError):
        svc.transition_lifecycle(db_session, uuid4(), ThesisLifecycleStatus.ACTIVE, actor="H", reason="r")
