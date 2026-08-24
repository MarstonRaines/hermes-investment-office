"""Research workspace, notes and evidence write authority (M4/M5)."""

from __future__ import annotations

from datetime import UTC, datetime
from hashlib import sha256
from uuid import UUID, uuid4

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.audit.service import write_audit_event, write_internal_provenance
from app.common.enums import (
    ActorType,
    AuditAction,
    ClaimType,
    Confidence,
    EvidenceDirection,
    EvidenceSourceType,
    SourceKind,
    ThreadStatus,
    ThreadType,
    WorkspaceStatus,
    WorkspaceSubjectType,
)
from app.research.models import (
    EvidenceItem,
    EvidenceLink,
    ResearchEvent,
    ResearchNote,
    ResearchThread,
    ResearchWorkspace,
)

__all__ = ["ResearchDomainError", "ResearchService"]


class ResearchDomainError(Exception):
    code = "RESEARCH_DOMAIN_ERROR"


class ResearchService:
    def _provenance(self, session: Session, *, source: str, actor: str) -> UUID:
        return write_internal_provenance(
            session,
            source_kind=SourceKind.HERMES if actor.startswith("HERMES") else SourceKind.HUMAN,
            source=source,
            actor_id=actor,
            as_of_date=datetime.now(UTC).date(),
            transform_version="research-service/0.1.0",
        ).provenance_id

    def create_workspace(
        self, session: Session, title: str, *, subject_type: WorkspaceSubjectType = WorkspaceSubjectType.GENERAL,
        subject_id: UUID | None = None,
    ) -> ResearchWorkspace:
        row = ResearchWorkspace(
            workspace_id=uuid4(), subject_type=subject_type.value, subject_id=subject_id,
            title=title, status=WorkspaceStatus.OPEN.value,
        )
        session.add(row)
        session.flush()
        return row

    def create_thread(
        self, session: Session, workspace_id: UUID, title: str, *, thread_type: ThreadType = ThreadType.RESEARCH,
    ) -> ResearchThread:
        if session.get(ResearchWorkspace, workspace_id) is None:
            raise ResearchDomainError("研究工作区不存在")
        row = ResearchThread(
            thread_id=uuid4(), workspace_id=workspace_id, thread_type=thread_type.value,
            status=ThreadStatus.THREAD_OPEN.value, title=title,
        )
        session.add(row)
        session.flush()
        return row

    def save_note(
        self, session: Session, title: str, body_md: str, *, workspace_id: UUID | None = None,
        thread_id: UUID | None = None, instrument_id: UUID | None = None,
        created_by: str = "HERMES",
    ) -> ResearchNote:
        if workspace_id is not None and session.get(ResearchWorkspace, workspace_id) is None:
            raise ResearchDomainError("研究工作区不存在")
        provenance_id = self._provenance(session, source="research_note", actor=created_by)
        row = ResearchNote(
            research_note_id=uuid4(), workspace_id=workspace_id, thread_id=thread_id,
            instrument_id=instrument_id, title=title, body_md=body_md,
            provenance_id=provenance_id, created_by=created_by,
        )
        session.add(row)
        session.flush()
        write_audit_event(
            session, action=AuditAction.CREATE, entity_type="research_note",
            entity_id=row.research_note_id, actor_type=ActorType.HERMES,
            actor_id=created_by, payload={"provenance_id": str(provenance_id)},
        )
        return row

    def add_evidence(
        self, session: Session, *, title: str, claim: str,
        source_type: EvidenceSourceType = EvidenceSourceType.DOCUMENT,
        source_ref: str | None = None, instrument_id: UUID | None = None,
        direction: EvidenceDirection = EvidenceDirection.NEUTRAL,
        claim_type: ClaimType = ClaimType.FACT,
        confidence: Confidence = Confidence.MEDIUM,
        observed_at: datetime | None = None, published_at: datetime | None = None,
        created_by: str = "HERMES",
    ) -> EvidenceItem:
        now = datetime.now(UTC)
        provenance_id = self._provenance(session, source="evidence_item", actor=created_by)
        row = EvidenceItem(
            evidence_id=uuid4(), title=title, claim=claim, claim_type=claim_type.value,
            direction=direction.value, confidence=confidence.value, source_type=source_type.value,
            source_ref=source_ref, instrument_id=instrument_id, observed_at=observed_at,
            published_at=published_at, retrieved_at=now,
            content_hash="sha256:" + sha256(claim.encode()).hexdigest(),
            provenance_id=provenance_id,
        )
        session.add(row)
        session.flush()
        write_audit_event(
            session, action=AuditAction.CREATE, entity_type="evidence_item",
            entity_id=row.evidence_id, actor_type=ActorType.HERMES,
            actor_id=created_by, payload={"provenance_id": str(provenance_id)},
        )
        return row

    def link_evidence(
        self, session: Session, evidence_id: UUID, *, thesis_revision_id: UUID | None = None,
        assumption_id: UUID | None = None,
    ) -> EvidenceLink:
        if session.get(EvidenceItem, evidence_id) is None:
            raise ResearchDomainError("证据不存在")
        if thesis_revision_id is None and assumption_id is None:
            raise ResearchDomainError("证据必须链接到 thesis revision 或 assumption")
        row = EvidenceLink(
            evidence_link_id=uuid4(), evidence_id=evidence_id,
            thesis_revision_id=thesis_revision_id, assumption_id=assumption_id,
        )
        session.add(row)
        session.flush()
        return row

    def get_evidence(self, session: Session, *, instrument_id: UUID | None = None,
                     thesis_revision_id: UUID | None = None, limit: int = 50) -> list[EvidenceItem]:
        stmt = select(EvidenceItem)
        if instrument_id is not None:
            stmt = stmt.where(EvidenceItem.instrument_id == instrument_id)
        if thesis_revision_id is not None:
            stmt = stmt.join(EvidenceLink, EvidenceLink.evidence_id == EvidenceItem.evidence_id).where(
                EvidenceLink.thesis_revision_id == thesis_revision_id
            )
        return list(session.scalars(stmt.order_by(EvidenceItem.created_at.desc()).limit(limit)).all())

    def search(self, session: Session, query: str, *, limit: int = 20) -> list[ResearchNote | EvidenceItem]:
        pattern = f"%{query}%"
        notes = list(session.scalars(select(ResearchNote).where(
            or_(ResearchNote.title.ilike(pattern), ResearchNote.body_md.ilike(pattern))
        ).order_by(ResearchNote.created_at.desc()).limit(limit)).all())
        evidence = list(session.scalars(select(EvidenceItem).where(
            or_(EvidenceItem.title.ilike(pattern), EvidenceItem.claim.ilike(pattern))
        ).order_by(EvidenceItem.created_at.desc()).limit(limit)).all())
        return [*notes, *evidence]

    def get_context(self, session: Session, *, instrument_id: UUID | None = None,
                    workspace_id: UUID | None = None, as_of: datetime | None = None) -> dict:
        notes_stmt = select(ResearchNote)
        if instrument_id is not None:
            notes_stmt = notes_stmt.where(ResearchNote.instrument_id == instrument_id)
        if workspace_id is not None:
            notes_stmt = notes_stmt.where(ResearchNote.workspace_id == workspace_id)
        if as_of is not None:
            notes_stmt = notes_stmt.where(ResearchNote.created_at <= as_of)
        notes = list(session.scalars(notes_stmt.order_by(ResearchNote.created_at.desc()).limit(50)).all())
        evidence = self.get_evidence(session, instrument_id=instrument_id, limit=50)
        return {
            "notes": [{"id": str(row.research_note_id), "title": row.title, "body_md": row.body_md,
                       "created_at": row.created_at.isoformat()} for row in notes],
            "evidence": [{"id": str(row.evidence_id), "title": row.title, "claim": row.claim,
                          "direction": row.direction, "confidence": row.confidence,
                          "source_ref": row.source_ref, "provenance_id": str(row.provenance_id)}
                         for row in evidence if as_of is None or row.created_at <= as_of],
        }

    def record_event(self, session: Session, thread_id: UUID, event_type: str, *, actor: str,
                     payload: dict | None = None, artifact_ref: dict | None = None) -> ResearchEvent:
        if session.get(ResearchThread, thread_id) is None:
            raise ResearchDomainError("研究线程不存在")
        row = ResearchEvent(
            research_event_id=uuid4(), thread_id=thread_id, event_type=event_type,
            actor=actor, artifact_ref=artifact_ref, payload=payload,
        )
        session.add(row)
        session.flush()
        return row
