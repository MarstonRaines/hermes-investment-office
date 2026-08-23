# backend/app/briefing/models.py —— 模块归属：briefing（Daily Context Builder）
from datetime import date, datetime
from uuid import UUID

from sqlalchemy import Boolean, Date, ForeignKey, Text, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.common.base import Base
from app.common.db import enum_ck
from app.common.enums import AttentionItemType, BriefStatus, FreshnessStatus
from app.common.mixins import CreatedAtMixin, UUIDPrimaryKeyMixin
from app.common.types import TIMESTAMPTZ

pk = UUIDPrimaryKeyMixin.pk


class DailyContext(Base, CreatedAtMixin):
    """每日一份（market_date UNIQUE）。freshness_status 驱动 Hermes 行为（冻结规范 §36.3）。"""
    __tablename__ = "daily_contexts"
    __table_args__ = (
        UniqueConstraint("market_date", name="uq_daily_contexts_market_date"),
        enum_ck("daily_contexts", "freshness_status", FreshnessStatus),
    )
    daily_context_id: Mapped[UUID] = pk("daily_context_id")
    market_date: Mapped[date] = mapped_column(Date, nullable=False)
    generated_at: Mapped[datetime] = mapped_column(TIMESTAMPTZ, nullable=False)
    freshness_status: Mapped[FreshnessStatus] = mapped_column(Text, nullable=False)
    data_freshness: Mapped[dict] = mapped_column(JSONB, nullable=False)    # 各数据源状态明细
    markets: Mapped[dict] = mapped_column(JSONB, nullable=False)           # {CN:{date,session},US:{...}}
    engine_versions: Mapped[dict] = mapped_column(JSONB, nullable=False)   # 参与引擎版本
    source_status: Mapped[dict] = mapped_column(JSONB, nullable=False)     # provider 状态
    summary: Mapped[str | None] = mapped_column(Text)


class AttentionItem(Base, CreatedAtMixin):
    """Attention 条目：只能由 Backend 确定性规则引擎写入；LLM 只解释，不创建（冻结规范 §38.1）。"""
    __tablename__ = "attention_items"
    attention_item_id: Mapped[UUID] = pk("attention_item_id")
    daily_context_id: Mapped[UUID] = mapped_column(ForeignKey("daily_contexts.daily_context_id"), nullable=False)
    item_type: Mapped[AttentionItemType] = mapped_column(Text, nullable=False)
    rule_name: Mapped[str] = mapped_column(Text, nullable=False)           # attention_rules.yaml 规则标识
    instrument_id: Mapped[UUID | None] = mapped_column(ForeignKey("instruments.instrument_id"))
    severity: Mapped[str | None] = mapped_column(Text)
    detail: Mapped[dict | None] = mapped_column(JSONB)
    is_processed: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))


class DailyBrief(Base, CreatedAtMixin):
    """每日投资简报。model_profile 记 profile 名，禁止记具体模型名（冻结规范 §34）。"""
    __tablename__ = "daily_briefs"
    __table_args__ = (
        UniqueConstraint("market_date", name="uq_daily_briefs_market_date"),
        enum_ck("daily_briefs", "status", BriefStatus),
    )
    daily_brief_id: Mapped[UUID] = pk("daily_brief_id")
    daily_context_id: Mapped[UUID] = mapped_column(ForeignKey("daily_contexts.daily_context_id"), nullable=False)
    market_date: Mapped[date] = mapped_column(Date, nullable=False)
    content_md: Mapped[str] = mapped_column(Text, nullable=False)
    sections: Mapped[list[dict] | None] = mapped_column(JSONB)
    model_profile: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[BriefStatus] = mapped_column(Text, nullable=False)
    generated_by: Mapped[str] = mapped_column(Text, nullable=False)
