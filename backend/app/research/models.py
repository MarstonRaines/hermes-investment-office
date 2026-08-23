# backend/app/research/models.py（evidence 部分）—— 模块归属：research（Evidence Service）
from datetime import datetime
from uuid import UUID

from sqlalchemy import CheckConstraint, ForeignKey, Index, Text, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.common.base import Base
from app.common.db import enum_ck
from app.common.enums import (
    ClaimType,
    Confidence,
    EvidenceDirection,
    EvidenceSourceType,
    ThreadStatus,
    ThreadType,
    WorkspaceStatus,
    WorkspaceSubjectType,
)
from app.common.mixins import CreatedAtMixin, TimestampMixin, UUIDPrimaryKeyMixin
from app.common.types import TIMESTAMPTZ

pk = UUIDPrimaryKeyMixin.pk


class EvidenceItem(Base, CreatedAtMixin):
    """证据：获取即写 provenance（provenance_id NOT NULL，ts02 §5.7）。

    支持/反驳由 direction 表达；metadata 允许 supersede，内容行不可覆盖。
    """
    __tablename__ = "evidence_items"
    __table_args__ = (
        enum_ck("evidence_items", "claim_type", ClaimType),
        enum_ck("evidence_items", "direction", EvidenceDirection),
        enum_ck("evidence_items", "confidence", Confidence),
        enum_ck("evidence_items", "source_type", EvidenceSourceType),
    )
    evidence_id: Mapped[UUID] = pk("evidence_id")
    title: Mapped[str] = mapped_column(Text, nullable=False)
    claim: Mapped[str] = mapped_column(Text, nullable=False)
    claim_type: Mapped[ClaimType | None] = mapped_column(Text)
    direction: Mapped[EvidenceDirection] = mapped_column(Text, nullable=False)
    confidence: Mapped[Confidence | None] = mapped_column(Text)
    source_type: Mapped[EvidenceSourceType] = mapped_column(Text, nullable=False)
    source_ref: Mapped[str | None] = mapped_column(Text)              # url / document_id
    instrument_id: Mapped[UUID | None] = mapped_column(ForeignKey("instruments.instrument_id"))
    observed_at: Mapped[datetime | None] = mapped_column(TIMESTAMPTZ)  # 事实发生时点
    published_at: Mapped[datetime | None] = mapped_column(TIMESTAMPTZ)
    retrieved_at: Mapped[datetime] = mapped_column(TIMESTAMPTZ, nullable=False)
    content_hash: Mapped[str | None] = mapped_column(Text)
    raw_object_key: Mapped[str | None] = mapped_column(Text)          # 原始快照路径
    provenance_id: Mapped[UUID] = mapped_column(ForeignKey("provenance_records.provenance_id"), nullable=False)


class EvidenceLink(Base, CreatedAtMixin):
    """Evidence ↔ ThesisRevision / Assumption 桥接（M:N，不用 evidence_json——ts01 刻意选择）。

    唯一索引用 COALESCE 哨兵 UUID 处理"可空列去重"（ts02 §5.8）：
    同一证据不得重复挂到同一目标。
    """
    __tablename__ = "evidence_links"
    __table_args__ = (
        CheckConstraint("thesis_revision_id IS NOT NULL OR assumption_id IS NOT NULL",
                        name="target"),
        Index(
            "uq_evidence_links_target",
            "evidence_id",
            text("COALESCE(thesis_revision_id, '00000000-0000-0000-0000-000000000000')"),
            text("COALESCE(assumption_id, '00000000-0000-0000-0000-000000000000')"),
            unique=True),
    )
    evidence_link_id: Mapped[UUID] = pk("evidence_link_id")
    evidence_id: Mapped[UUID] = mapped_column(ForeignKey("evidence_items.evidence_id"), nullable=False)
    thesis_revision_id: Mapped[UUID | None] = mapped_column(ForeignKey("thesis_revisions.thesis_revision_id"))
    assumption_id: Mapped[UUID | None] = mapped_column(ForeignKey("thesis_assumptions.assumption_id"))
# backend/app/research/models.py（workspace 部分）—— 模块归属：research（Research Service）
class ResearchWorkspace(Base, CreatedAtMixin):
    """研究容器（支持域，不是投资状态 SoT）。删除不级联任何 Thesis/Evidence/ValuationRun。"""
    __tablename__ = "research_workspaces"
    __table_args__ = (
        enum_ck("research_workspaces", "subject_type", WorkspaceSubjectType),
        enum_ck("research_workspaces", "status", WorkspaceStatus),
    )
    workspace_id: Mapped[UUID] = pk("workspace_id")
    subject_type: Mapped[WorkspaceSubjectType] = mapped_column(Text, nullable=False)
    subject_id: Mapped[UUID | None] = mapped_column(PgUUID(as_uuid=True))   # 多态 subject，无 FK
    title: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[WorkspaceStatus] = mapped_column(Text, nullable=False)   # OPEN / ARCHIVED
    archived_at: Mapped[datetime | None] = mapped_column(TIMESTAMPTZ)


class ResearchThread(Base, TimestampMixin):
    __tablename__ = "research_threads"
    __table_args__ = (
        enum_ck("research_threads", "thread_type", ThreadType),
        enum_ck("research_threads", "status", ThreadStatus),
    )
    thread_id: Mapped[UUID] = pk("thread_id")
    workspace_id: Mapped[UUID] = mapped_column(ForeignKey("research_workspaces.workspace_id"), nullable=False)
    thread_type: Mapped[ThreadType] = mapped_column(Text, nullable=False)   # RESEARCH / ANALYSIS / REVIEW
    status: Mapped[ThreadStatus] = mapped_column(Text, nullable=False)      # THREAD_OPEN / PAUSED / CLOSED
    title: Mapped[str | None] = mapped_column(Text)


class ResearchEvent(Base, CreatedAtMixin):
    """研究过程事件；artifact_ref 引用但不拥有 thesis/evidence/valuation_run。"""
    __tablename__ = "research_events"
    research_event_id: Mapped[UUID] = pk("research_event_id")
    thread_id: Mapped[UUID] = mapped_column(ForeignKey("research_threads.thread_id"), nullable=False)
    event_type: Mapped[str] = mapped_column(Text, nullable=False)
    actor: Mapped[str] = mapped_column(Text, nullable=False)
    artifact_ref: Mapped[dict | None] = mapped_column(JSONB)                # 指向域对象的引用
    payload: Mapped[dict | None] = mapped_column(JSONB)


class ResearchNote(Base, TimestampMixin):
    """研究笔记。v0.1 允许 workspace_id 为空（快速笔记，ts02 §8.1 / 开放问题 3）。"""
    __tablename__ = "research_notes"
    research_note_id: Mapped[UUID] = pk("research_note_id")
    workspace_id: Mapped[UUID | None] = mapped_column(ForeignKey("research_workspaces.workspace_id"))
    thread_id: Mapped[UUID | None] = mapped_column(ForeignKey("research_threads.thread_id"))
    instrument_id: Mapped[UUID | None] = mapped_column(ForeignKey("instruments.instrument_id"))
    title: Mapped[str] = mapped_column(Text, nullable=False)
    body_md: Mapped[str] = mapped_column(Text, nullable=False)
    provenance_id: Mapped[UUID | None] = mapped_column(ForeignKey("provenance_records.provenance_id"))
    created_by: Mapped[str] = mapped_column(Text, nullable=False)
