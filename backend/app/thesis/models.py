# backend/app/thesis/models.py —— 模块归属：thesis（Thesis Service）
from datetime import datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import Boolean, ForeignKey, Index, Integer, Text, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.common.base import Base
from app.common.db import enum_ck
from app.common.enums import (
    AssumptionCategory,
    Conviction,
    RedFlagSeverity,
    RedFlagStatus,
    ReviewConclusion,
    ReviewType,
    ThesisEventType,
    ThesisHealthStatus,
    ThesisLifecycleStatus,
)
from app.common.mixins import CreatedAtMixin, TimestampMixin, UUIDPrimaryKeyMixin
from app.common.types import MONEY, TIMESTAMPTZ

pk = UUIDPrimaryKeyMixin.pk


class Thesis(Base, TimestampMixin):
    """Thesis 稳定身份。lifecycle 与 health 正交（双状态机，ts01 §4 冻结）。

    current_revision_id 循环 FK：两步事务（先建 revision，再 UPDATE 指向），
    不引入 DEFERRABLE 约束（ts02 §5.1 冻结）。
    """
    __tablename__ = "theses"
    __table_args__ = (
        enum_ck("theses", "lifecycle_status", ThesisLifecycleStatus),
        enum_ck("theses", "health_status", ThesisHealthStatus),
        enum_ck("theses", "conviction", Conviction),
    )
    thesis_id: Mapped[UUID] = pk("thesis_id")
    instrument_id: Mapped[UUID] = mapped_column(ForeignKey("instruments.instrument_id"), nullable=False)
    lifecycle_status: Mapped[ThesisLifecycleStatus] = mapped_column(Text, nullable=False)
    health_status: Mapped[ThesisHealthStatus] = mapped_column(Text, nullable=False)
    conviction: Mapped[Conviction | None] = mapped_column(Text)
    fair_value_low: Mapped[Decimal | None] = mapped_column(MONEY)     # 最新估值区间（冗余展示）
    fair_value_base: Mapped[Decimal | None] = mapped_column(MONEY)
    fair_value_high: Mapped[Decimal | None] = mapped_column(MONEY)
    current_revision_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("thesis_revisions.thesis_revision_id"))            # 延迟设置

    revisions: Mapped[list["ThesisRevision"]] = relationship(
        back_populates="thesis",
        foreign_keys="ThesisRevision.thesis_id",   # 消除与 current_revision_id 的 FK 路径歧义
    )


class ThesisRevision(Base, CreatedAtMixin):
    """不可变 Thesis 版本（append-only，migration 配 trg_thesis_revisions_no_update）。

    冻结不变量（ts01 §生命周期 / ts02 §5.2）：
      - version 单调递增，UNIQUE(thesis_id, version)；
      - 无任何 UPDATE 业务路径；唯一"状态变化" = theses.current_revision_id 移动；
      - base_revision_id 过期（≠ 当前 head）→ 409 DOMAIN_CONFLICT（乐观并发）。
    """
    __tablename__ = "thesis_revisions"
    __table_args__ = (
        UniqueConstraint("thesis_id", "version", name="uq_thesis_revisions_thesis_version"),
        Index("ix_thesis_revisions_thesis", "thesis_id", text("version DESC")),  # 版本链
    )
    thesis_revision_id: Mapped[UUID] = pk("thesis_revision_id")
    thesis_id: Mapped[UUID] = mapped_column(ForeignKey("theses.thesis_id"), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    thesis_body: Mapped[dict] = mapped_column(JSONB, nullable=False)  # 结构化论文（结构由 TS-04 定义）
    summary: Mapped[str | None] = mapped_column(Text)
    change_reason: Mapped[str] = mapped_column(Text, nullable=False)  # 变更原因（审计必需）
    authored_by: Mapped[str] = mapped_column(Text, nullable=False)    # HERMES / HUMAN / SYSTEM
    base_revision_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("thesis_revisions.thesis_revision_id"))            # 父版本（链式）
    provenance_id: Mapped[UUID | None] = mapped_column(ForeignKey("provenance_records.provenance_id"))

    thesis: Mapped[Thesis] = relationship(
        back_populates="revisions",
        foreign_keys=[thesis_id],                 # 消除与 theses.current_revision_id 的路径歧义
    )
    assumptions: Mapped[list["ThesisAssumption"]] = relationship(back_populates="revision")


class ThesisAssumption(Base, CreatedAtMixin):
    """结构化核心假设。

    状态用 ThesisHealthStatus（冻结规范 §27.1：UNKNOWN/HEALTHY/WARNING/BROKEN）——
    AT_RISK 不是合法值，Pydantic 层天然拒绝（枚举无此成员）。
    状态迁移 + supersede 链（superseded_by）：假设可独立于 revision 演进（M4 复核）。
    """
    __tablename__ = "thesis_assumptions"
    __table_args__ = (
        enum_ck("thesis_assumptions", "status", ThesisHealthStatus),
        enum_ck("thesis_assumptions", "category", AssumptionCategory),
    )
    assumption_id: Mapped[UUID] = pk("assumption_id")
    thesis_id: Mapped[UUID] = mapped_column(ForeignKey("theses.thesis_id"), nullable=False)
    thesis_revision_id: Mapped[UUID] = mapped_column(
        ForeignKey("thesis_revisions.thesis_revision_id"), nullable=False)  # 声明于哪个版本
    statement: Mapped[str] = mapped_column(Text, nullable=False)
    category: Mapped[AssumptionCategory | None] = mapped_column(Text)
    status: Mapped[ThesisHealthStatus] = mapped_column(Text, nullable=False)
    test_condition: Mapped[str | None] = mapped_column(Text)          # 可验证条件
    verification_frequency: Mapped[str | None] = mapped_column(Text)  # QUARTERLY 等
    is_red_line: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    superseded_at: Mapped[datetime | None] = mapped_column(TIMESTAMPTZ)
    superseded_by: Mapped[UUID | None] = mapped_column(
        ForeignKey("thesis_assumptions.assumption_id"))               # 新假设

    revision: Mapped[ThesisRevision] = relationship(back_populates="assumptions")


class ThesisReview(Base, CreatedAtMixin):
    """周期/事件复核。health_before / health_after 记录健康度迁移证据（禁止无来源状态切换）。"""
    __tablename__ = "thesis_reviews"
    __table_args__ = (
        enum_ck("thesis_reviews", "review_type", ReviewType),
        enum_ck("thesis_reviews", "conclusion", ReviewConclusion),
    )
    review_id: Mapped[UUID] = pk("review_id")
    thesis_id: Mapped[UUID] = mapped_column(ForeignKey("theses.thesis_id"), nullable=False)
    review_type: Mapped[ReviewType] = mapped_column(Text, nullable=False)
    conclusion: Mapped[ReviewConclusion] = mapped_column(Text, nullable=False)
    health_before: Mapped[ThesisHealthStatus | None] = mapped_column(Text)
    health_after: Mapped[ThesisHealthStatus | None] = mapped_column(Text)
    notes: Mapped[str | None] = mapped_column(Text)
    reviewed_at: Mapped[datetime] = mapped_column(TIMESTAMPTZ, nullable=False)
    actor_id: Mapped[str] = mapped_column(Text, nullable=False)
    provenance_id: Mapped[UUID | None] = mapped_column(ForeignKey("provenance_records.provenance_id"))


class ThesisRedFlag(Base, CreatedAtMixin):
    """风险红线。ARMED → TRIGGERED → RESOLVED；RED_LINE 触发 = 必须重新评估（冻结规范 §27.2）。"""
    __tablename__ = "thesis_red_flags"
    __table_args__ = (
        enum_ck("thesis_red_flags", "severity", RedFlagSeverity),
        enum_ck("thesis_red_flags", "status", RedFlagStatus),
    )
    red_flag_id: Mapped[UUID] = pk("red_flag_id")
    thesis_id: Mapped[UUID] = mapped_column(ForeignKey("theses.thesis_id"), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    severity: Mapped[RedFlagSeverity] = mapped_column(Text, nullable=False)
    trigger_condition: Mapped[str] = mapped_column(Text, nullable=False)  # 可验证条件
    status: Mapped[RedFlagStatus] = mapped_column(Text, nullable=False)
    triggered_at: Mapped[datetime | None] = mapped_column(TIMESTAMPTZ)
    resolved_at: Mapped[datetime | None] = mapped_column(TIMESTAMPTZ)
    provenance_id: Mapped[UUID | None] = mapped_column(ForeignKey("provenance_records.provenance_id"))


class ThesisEvent(Base, CreatedAtMixin):
    """Thesis 事件流（append-only 审计性事件）。"""
    __tablename__ = "thesis_events"
    __table_args__ = (
        Index("ix_thesis_events_thesis", "thesis_id", "created_at"),
        enum_ck("thesis_events", "event_type", ThesisEventType),
    )
    thesis_event_id: Mapped[UUID] = pk("thesis_event_id")
    thesis_id: Mapped[UUID] = mapped_column(ForeignKey("theses.thesis_id"), nullable=False)
    event_type: Mapped[ThesisEventType] = mapped_column(Text, nullable=False)
    event_data: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default=text("'{}'"))
    actor_id: Mapped[str | None] = mapped_column(Text)
