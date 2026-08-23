# backend/app/audit/models.py —— 模块归属：audit（Provenance / Audit Service）
from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import Boolean, CheckConstraint, Date, ForeignKey, Index, Text, text
from sqlalchemy.dialects.postgresql import JSONB, UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.common.base import Base
from app.common.db import enum_ck, range_ck
from app.common.enums import (
    ActorType, AuditAction, DataQualityStatus, OutboxStatus, OutboxTopic, SourceKind,
)
from app.common.mixins import CreatedAtMixin, UUIDPrimaryKeyMixin
from app.common.types import TIMESTAMPTZ, QUALITY

pk = UUIDPrimaryKeyMixin.pk


class ProvenanceRecord(Base, CreatedAtMixin):
    """统一数据血缘（一等公民，append-only）。

    业务不变量（ts01 provenance 契约 / ts02 §4.1）：
      - quality_score 只表示数据质量，不代表来源权威度（冲突解决先看 source authority）；
      - 与业务事务同事务提交（或经 outbox）——"事实写入必有血缘"；
      - quality_status ∈ {CONFLICT, REJECTED} 的记录被 decision-sensitive 引擎引用时，
        引擎必须拒绝产出 VERIFIED 结果（服务层强制，架构测试覆盖）；
      - 本表无 UPDATE / DELETE 业务路径（migration 配 trg_provenance_records_no_update）。
    """
    __tablename__ = "provenance_records"
    __table_args__ = (
        range_ck("provenance_records", "quality_score", "0", "1"),
        enum_ck("provenance_records", "source_kind", SourceKind),
        enum_ck("provenance_records", "quality_status", DataQualityStatus),
        Index("ix_provenance_provider_record", "provider", "source_record_id"),
        Index("ix_provenance_asof_quality", "as_of_date", "quality_status"),
    )
    provenance_id: Mapped[UUID] = pk("provenance_id")
    source_kind: Mapped[SourceKind] = mapped_column(Text, nullable=False)
    source: Mapped[str] = mapped_column(Text, nullable=False)          # 来源名/数据集名
    provider: Mapped[str] = mapped_column(Text, nullable=False)        # 无外部 provider 显式 internal
    source_uri: Mapped[str | None] = mapped_column(Text)
    source_record_id: Mapped[str | None] = mapped_column(Text)         # Provider 原始记录标识
    published_at: Mapped[datetime | None] = mapped_column(TIMESTAMPTZ)  # 原始信息发布时点
    observed_at: Mapped[datetime] = mapped_column(TIMESTAMPTZ, nullable=False)  # 事实/市场时点
    retrieved_at: Mapped[datetime] = mapped_column(TIMESTAMPTZ, nullable=False)  # 系统取得时点
    as_of_date: Mapped[date | None] = mapped_column(Date)              # PIT 语义日期
    quality_score: Mapped[Decimal] = mapped_column(QUALITY, nullable=False)
    quality_status: Mapped[DataQualityStatus] = mapped_column(Text, nullable=False)
    quality_flags: Mapped[list[str]] = mapped_column(JSONB, nullable=False, server_default=text("'[]'"))
    fallback_used: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    raw_hash: Mapped[str | None] = mapped_column(Text)
    raw_object_key: Mapped[str | None] = mapped_column(Text)           # Parquet/raw artifact 地址
    ingestion_run_id: Mapped[UUID | None] = mapped_column(ForeignKey("job_runs.job_run_id"))
    transform_version: Mapped[str] = mapped_column(Text, nullable=False)
    actor_id: Mapped[str | None] = mapped_column(Text)                 # source_kind=HERMES/HUMAN 时必填
# backend/app/audit/models.py（续）—— AuditEvent / OutboxEvent
class AuditEvent(Base, CreatedAtMixin):
    """不可删除审计事件（append-only）。entity_id 为多态引用，故意无 FK（ts02 §8.3）。"""
    __tablename__ = "audit_events"
    __table_args__ = (
        Index("ix_audit_events_entity", "entity_type", "entity_id", "created_at"),
        enum_ck("audit_events", "actor_type", ActorType),
        enum_ck("audit_events", "action", AuditAction),
    )
    audit_event_id: Mapped[UUID] = pk("audit_event_id")
    actor_type: Mapped[ActorType] = mapped_column(Text, nullable=False)   # HERMES / HUMAN / SYSTEM / JOB
    actor_id: Mapped[str | None] = mapped_column(Text)
    action: Mapped[AuditAction] = mapped_column(Text, nullable=False)
    entity_type: Mapped[str] = mapped_column(Text, nullable=False)        # 表/域对象名
    entity_id: Mapped[UUID | None] = mapped_column(PgUUID(as_uuid=True))
    before_hash: Mapped[str | None] = mapped_column(Text)                 # 变更前快照 hash
    after_hash: Mapped[str | None] = mapped_column(Text)
    payload: Mapped[dict | None] = mapped_column(JSONB)
    request_id: Mapped[str | None] = mapped_column(Text)                  # 关联 MCP request_id


class OutboxEvent(Base, CreatedAtMixin):
    """事务性 outbox（v0.1 无 MQ；消费者轮询发布，冻结规范 §7）。"""
    __tablename__ = "outbox_events"
    __table_args__ = (
        enum_ck("outbox_events", "topic", OutboxTopic),
        enum_ck("outbox_events", "status", OutboxStatus),
    )
    outbox_event_id: Mapped[UUID] = pk("outbox_event_id")
    topic: Mapped[OutboxTopic] = mapped_column(Text, nullable=False)      # AUDIT / PROVENANCE / NOTIFICATION
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
    status: Mapped[OutboxStatus] = mapped_column(Text, nullable=False, server_default="PENDING")
    published_at: Mapped[datetime | None] = mapped_column(TIMESTAMPTZ)


