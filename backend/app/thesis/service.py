# =====================================================================
# backend/app/thesis/service.py —— Thesis 服务最小版（M1.5 Vertical Slice）
#
# 覆盖（ts08 §6.1 STM-THS + GOLD-PIT-002）：
# - 创建（DRAFT + revision v1）；revision 追加（版本单调，不可变，乐观并发）
# - lifecycle 状态机：[*]→DRAFT；DRAFT→ACTIVE|ARCHIVED；ACTIVE→UNDER_REVIEW|
#   INVALIDATED|ARCHIVED；UNDER_REVIEW→ACTIVE|INVALIDATED；INVALIDATED→ARCHIVED
# - health 状态机（与 lifecycle 正交）：UNKNOWN→HEALTHY→WARNING→BROKEN（可回退）
# - 状态迁移必须带 actor/reason（STM-THS-005）；非法迁移 → INVALID_THESIS_TRANSITION
# - red flag：ARMED→TRIGGERED→RESOLVED（STM-THS-009）
# - get_thesis(as_of)：created_at <= as_of 的最近版本（GOLD-PIT-002，禁止永远返回今天 revision）
# =====================================================================
from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.audit.service import write_internal_provenance
from app.common.enums import (
    RedFlagSeverity,
    RedFlagStatus,
    SourceKind,
    ThesisEventType,
    ThesisHealthStatus,
    ThesisLifecycleStatus,
)
from app.common.freshness import require_freshness
from app.thesis.models import (
    Thesis,
    ThesisAssumption,
    ThesisEvent,
    ThesisRedFlag,
    ThesisReview,
    ThesisRevision,
)

__all__ = [
    "ThesisService", "InvalidThesisTransitionError", "RevisionConflictError", "ThesisDomainError",
]


class ThesisDomainError(Exception):
    code = "THESIS_DOMAIN_ERROR"


class InvalidThesisTransitionError(ThesisDomainError):
    code = "INVALID_THESIS_TRANSITION"


class RevisionConflictError(ThesisDomainError):
    code = "DOMAIN_CONFLICT"


LIFECYCLE_TRANSITIONS: dict[ThesisLifecycleStatus, set[ThesisLifecycleStatus]] = {
    ThesisLifecycleStatus.DRAFT: {ThesisLifecycleStatus.ACTIVE, ThesisLifecycleStatus.ARCHIVED},
    ThesisLifecycleStatus.ACTIVE: {
        ThesisLifecycleStatus.UNDER_REVIEW,
        ThesisLifecycleStatus.INVALIDATED,
        ThesisLifecycleStatus.ARCHIVED,
    },
    ThesisLifecycleStatus.UNDER_REVIEW: {
        ThesisLifecycleStatus.ACTIVE,
        ThesisLifecycleStatus.INVALIDATED,
    },
    ThesisLifecycleStatus.INVALIDATED: {ThesisLifecycleStatus.ARCHIVED},
    ThesisLifecycleStatus.ARCHIVED: set(),
}

HEALTH_TRANSITIONS: dict[ThesisHealthStatus, set[ThesisHealthStatus]] = {
    ThesisHealthStatus.UNKNOWN: {ThesisHealthStatus.HEALTHY},
    ThesisHealthStatus.HEALTHY: {ThesisHealthStatus.WARNING, ThesisHealthStatus.BROKEN},
    ThesisHealthStatus.WARNING: {ThesisHealthStatus.HEALTHY, ThesisHealthStatus.BROKEN},
    ThesisHealthStatus.BROKEN: {ThesisHealthStatus.WARNING, ThesisHealthStatus.HEALTHY},  # evidence recovery
}

RED_FLAG_TRANSITIONS: dict[RedFlagStatus, set[RedFlagStatus]] = {
    RedFlagStatus.ARMED: {RedFlagStatus.TRIGGERED},
    RedFlagStatus.TRIGGERED: {RedFlagStatus.RESOLVED},
    RedFlagStatus.RESOLVED: set(),
}


class ThesisService:
    # ---- 创建与版本 ----

    def create_thesis(
        self,
        session: Session,
        instrument_id: UUID,
        title: str,
        body: dict,
        *,
        authored_by: str = "HERMES",
        change_reason: str = "initial",
    ) -> Thesis:
        """创建 Thesis（DRAFT）+ 首个 revision（v1）。两步事务（current_revision_id 循环 FK）。"""
        thesis = Thesis(
            thesis_id=uuid4(),
            instrument_id=instrument_id,
            lifecycle_status=ThesisLifecycleStatus.DRAFT.value,
            health_status=ThesisHealthStatus.UNKNOWN.value,
        )
        session.add(thesis)
        session.flush()
        rev = ThesisRevision(
            thesis_revision_id=uuid4(),
            thesis_id=thesis.thesis_id,
            version=1,
            thesis_body=body,
            summary=title,
            change_reason=change_reason,
            authored_by=authored_by,
            provenance_id=self._provenance(session, authored_by, "thesis_revision"),
        )
        session.add(rev)
        session.flush()
        thesis.current_revision_id = rev.thesis_revision_id
        self._event(session, thesis.thesis_id, ThesisEventType.CREATED,
                    payload={"revision": 1, "authored_by": authored_by})
        session.flush()
        return thesis

    def create_revision(
        self,
        session: Session,
        thesis_id: UUID,
        body: dict,
        *,
        base_revision_id: UUID,
        authored_by: str = "HERMES",
        change_reason: str,
        freshness: dict | str = "OK",
    ) -> ThesisRevision:
        """追加不可变 revision（版本单调；base 过期 → 409 DOMAIN_CONFLICT，STM-THS-006/007）。"""
        require_freshness(freshness)
        thesis = session.get(Thesis, thesis_id)
        if thesis is None:
            raise ThesisDomainError(f"thesis {thesis_id} 不存在")
        if thesis.current_revision_id != base_revision_id:
            raise RevisionConflictError("base_revision_id 过期（乐观并发，head 已移动）")
        next_version = 1
        if thesis.current_revision_id is not None:
            head = session.get(ThesisRevision, thesis.current_revision_id)
            next_version = head.version + 1 if head else 1
        rev = ThesisRevision(
            thesis_revision_id=uuid4(),
            thesis_id=thesis_id,
            version=next_version,
            thesis_body=body,
            change_reason=change_reason,
            authored_by=authored_by,
            base_revision_id=base_revision_id,
            provenance_id=self._provenance(session, authored_by, "thesis_revision"),
        )
        session.add(rev)
        session.flush()
        thesis.current_revision_id = rev.thesis_revision_id
        self._event(session, thesis_id, ThesisEventType.REVISION,
                    payload={"version": next_version, "change_reason": change_reason})
        session.flush()
        return rev

    def record_review(
        self, session: Session, thesis_id: UUID, review_type, conclusion, *,
        actor_id: str, notes: str | None = None,
        health_after: ThesisHealthStatus | None = None,
        freshness: dict | str = "OK",
    ) -> ThesisReview:
        require_freshness(freshness)
        thesis = session.get(Thesis, thesis_id)
        if thesis is None:
            raise ThesisDomainError("thesis 不存在")
        from app.common.enums import ReviewConclusion, ReviewType
        review_type = ReviewType(review_type)
        conclusion = ReviewConclusion(conclusion)
        before = ThesisHealthStatus(thesis.health_status)
        after = health_after or before
        review = ThesisReview(
            review_id=uuid4(), thesis_id=thesis_id, review_type=review_type.value,
            conclusion=conclusion.value, health_before=before.value,
            health_after=after.value, notes=notes, reviewed_at=datetime.now(UTC),
            actor_id=actor_id, provenance_id=self._provenance(session, actor_id, "thesis_review"),
        )
        session.add(review)
        if conclusion is ReviewConclusion.INVALIDATE:
            self.transition_lifecycle(
                session, thesis_id, ThesisLifecycleStatus.INVALIDATED,
                actor=actor_id, reason=notes or "review invalidated thesis",
            )
        elif conclusion is ReviewConclusion.REVISE and ThesisLifecycleStatus.UNDER_REVIEW in LIFECYCLE_TRANSITIONS.get(
            ThesisLifecycleStatus(thesis.lifecycle_status), set()
        ):
            self.transition_lifecycle(
                session, thesis_id, ThesisLifecycleStatus.UNDER_REVIEW,
                actor=actor_id, reason=notes or "review requires revision",
            )
        if after is not before:
            self.transition_health(session, thesis_id, after, actor=actor_id,
                                   reason=notes or "review health update")
        session.flush()
        return review

    def update_assumption(
        self, session: Session, assumption_id: UUID, status: ThesisHealthStatus, *,
        actor_id: str, test_condition: str | None = None, note: str | None = None,
        freshness: dict | str = "OK",
    ) -> ThesisAssumption:
        require_freshness(freshness)
        assumption = session.get(ThesisAssumption, assumption_id)
        if assumption is None:
            raise ThesisDomainError("assumption 不存在")
        status = ThesisHealthStatus(status)
        current = ThesisHealthStatus(assumption.status)
        if status is not current and status not in HEALTH_TRANSITIONS.get(current, set()):
            raise InvalidThesisTransitionError(f"assumption {current.value} → {status.value} 非法")
        assumption.status = status.value
        if test_condition is not None:
            assumption.test_condition = test_condition
        self._event(session, assumption.thesis_id, ThesisEventType.HEALTH_CHANGED, payload={
            "assumption_id": str(assumption_id), "from": current.value, "to": status.value,
            "actor": actor_id, "reason": note or "assumption update",
        })
        session.flush()
        return assumption

    def get_thesis(self, session: Session, thesis_id: UUID, as_of: datetime | None = None) -> ThesisRevision | None:
        """PIT 版本（GOLD-PIT-002）：created_at <= as_of 的最近版本；缺省 = head。"""
        stmt = (
            select(ThesisRevision)
            .where(ThesisRevision.thesis_id == thesis_id)
        )
        if as_of is not None:
            stmt = stmt.where(ThesisRevision.created_at <= as_of)
        return session.execute(
            stmt.order_by(ThesisRevision.version.desc()).limit(1)
        ).scalars().first()

    def public_view(self, session: Session, thesis_id: UUID, as_of: datetime | None = None) -> dict | None:
        """Stable read shape for REST/MCP without exposing ORM ownership to adapters."""
        thesis = session.get(Thesis, thesis_id)
        revision = self.get_thesis(session, thesis_id, as_of=as_of)
        if thesis is None or revision is None:
            return None
        return {
            "thesis_id": str(thesis.thesis_id), "instrument_id": str(thesis.instrument_id),
            "lifecycle_status": thesis.lifecycle_status, "health_status": thesis.health_status,
            "conviction": thesis.conviction,
            "current_revision": {
                "revision_id": str(revision.thesis_revision_id), "version": revision.version,
                "thesis_body": revision.thesis_body, "summary": revision.summary,
                "created_at": revision.created_at.isoformat(),
                "provenance_id": str(revision.provenance_id) if revision.provenance_id else None,
            },
        }

    def list_for_instrument(
        self,
        session: Session,
        instrument_id: UUID,
        *,
        as_of: datetime | None = None,
    ) -> list[dict]:
        stmt = select(Thesis).where(Thesis.instrument_id == instrument_id)
        if as_of is not None:
            stmt = stmt.where(Thesis.created_at <= as_of)
        rows = session.scalars(stmt.order_by(Thesis.updated_at.desc())).all()
        return [
            view
            for row in rows
            if (view := self.public_view(session, row.thesis_id, as_of=as_of)) is not None
        ]

    # ---- lifecycle 状态机 ----

    def transition_lifecycle(
        self,
        session: Session,
        thesis_id: UUID,
        to: ThesisLifecycleStatus,
        *,
        actor: str,
        reason: str,
    ) -> Thesis:
        thesis = session.get(Thesis, thesis_id)
        if thesis is None:
            raise ThesisDomainError(f"thesis {thesis_id} 不存在")
        current = ThesisLifecycleStatus(thesis.lifecycle_status)
        if to not in LIFECYCLE_TRANSITIONS.get(current, set()):
            raise InvalidThesisTransitionError(f"{current.value} → {to.value} 非法")
        if not actor or not reason:
            raise ThesisDomainError("状态迁移必须携带 actor 与 reason（STM-THS-005）")
        thesis.lifecycle_status = to.value
        self._event(session, thesis_id, ThesisEventType.STATUS_CHANGED,
                    payload={"from": current.value, "to": to.value, "actor": actor, "reason": reason})
        session.flush()
        return thesis

    def transition_health(
        self,
        session: Session,
        thesis_id: UUID,
        to: ThesisHealthStatus,
        *,
        actor: str,
        reason: str,
    ) -> Thesis:
        """health 与 lifecycle 正交（STM-THS-003）。"""
        thesis = session.get(Thesis, thesis_id)
        if thesis is None:
            raise ThesisDomainError(f"thesis {thesis_id} 不存在")
        current = ThesisHealthStatus(thesis.health_status)
        if to not in HEALTH_TRANSITIONS.get(current, set()):
            raise InvalidThesisTransitionError(f"health {current.value} → {to.value} 非法")
        if not actor or not reason:
            raise ThesisDomainError("health 迁移必须携带 actor 与 reason（STM-THS-005）")
        thesis.health_status = to.value
        self._event(session, thesis_id, ThesisEventType.HEALTH_CHANGED,
                    payload={"from": current.value, "to": to.value, "actor": actor, "reason": reason})
        session.flush()
        return thesis

    # ---- red flags（STM-THS-009）----

    def arm_red_flag(
        self,
        session: Session,
        thesis_id: UUID,
        description: str,
        trigger_condition: str,
        severity: RedFlagSeverity = RedFlagSeverity.HIGH,
    ) -> ThesisRedFlag:
        flag = ThesisRedFlag(
            red_flag_id=uuid4(),
            thesis_id=thesis_id,
            description=description,
            severity=severity.value,
            trigger_condition=trigger_condition,
            status=RedFlagStatus.ARMED.value,
        )
        session.add(flag)
        session.flush()
        return flag

    def trigger_red_flag(self, session: Session, red_flag_id: UUID, *, actor: str, evidence: str) -> ThesisRedFlag:
        flag = session.get(ThesisRedFlag, red_flag_id)
        if flag is None:
            raise ThesisDomainError(f"red flag {red_flag_id} 不存在")
        current = RedFlagStatus(flag.status)
        if RedFlagStatus.TRIGGERED not in RED_FLAG_TRANSITIONS.get(current, set()):
            raise InvalidThesisTransitionError(f"red flag {current.value} → TRIGGERED 非法")
        flag.status = RedFlagStatus.TRIGGERED.value
        flag.triggered_at = datetime.now(UTC)
        self._event(session, flag.thesis_id, ThesisEventType.RED_FLAG_TRIGGERED,
                    payload={"red_flag_id": str(red_flag_id), "actor": actor, "evidence": evidence})
        session.flush()
        return flag

    def resolve_red_flag(self, session: Session, red_flag_id: UUID, *, actor: str, resolution: str) -> ThesisRedFlag:
        flag = session.get(ThesisRedFlag, red_flag_id)
        if flag is None:
            raise ThesisDomainError(f"red flag {red_flag_id} 不存在")
        current = RedFlagStatus(flag.status)
        if RedFlagStatus.RESOLVED not in RED_FLAG_TRANSITIONS.get(current, set()):
            raise InvalidThesisTransitionError(f"red flag {current.value} → RESOLVED 非法")
        flag.status = RedFlagStatus.RESOLVED.value
        flag.resolved_at = datetime.now(UTC)
        session.flush()
        return flag

    # ---- 内部 ----

    def _provenance(self, session: Session, actor: str, source: str) -> UUID:
        row = write_internal_provenance(
            session,
            source_kind=SourceKind.HERMES if actor.startswith("HERMES") else SourceKind.HUMAN,
            source=source, actor_id=actor, as_of_date=datetime.now(UTC).date(),
            transform_version="thesis-service/0.1.0",
        )
        return row.provenance_id

    def _event(self, session: Session, thesis_id: UUID, event_type: ThesisEventType, payload: dict) -> None:
        session.add(ThesisEvent(
            thesis_event_id=uuid4(),
            thesis_id=thesis_id,
            event_type=event_type.value,
            event_data=payload,
        ))
