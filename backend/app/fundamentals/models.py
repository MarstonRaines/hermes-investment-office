# backend/app/fundamentals/models.py —— 模块归属：fundamentals（Fundamental Service）
from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import Boolean, Date, ForeignKey, Index, Text, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column

from app.common.base import Base
from app.common.db import enum_ck
from app.common.enums import DataQualityStatus, PeriodType, StatementType
from app.common.mixins import CreatedAtMixin, UUIDPrimaryKeyMixin
from app.common.types import FACT, TIMESTAMPTZ

pk = UUIDPrimaryKeyMixin.pk

# metric_code 冻结清单（ts02 §4.3 / 冻结规范 §16）——服务层校验，DB 不加 CHECK（ts02 未冻结）：
FROZEN_METRIC_CODES = frozenset({
    "REVENUE", "GROSS_PROFIT", "OPERATING_INCOME", "NET_INCOME",
    "OPERATING_CASH_FLOW", "CAPEX", "FREE_CASH_FLOW",
    "TOTAL_ASSETS", "TOTAL_LIABILITIES", "TOTAL_EQUITY",
    "CASH", "DEBT", "SHARES_OUTSTANDING",
})


class FinancialFact(Base, CreatedAtMixin):
    """标准化财务事实（PIT 核心，append-only / supersede）。

    PIT 不变量（ts01 §财务事实 PIT 规则 / ts02 §4.3）：
      - period_end（报告期）≠ published_at（市场可知时点）≠ retrieved_at（取得时点）；
        回测/历史 Thesis 审计禁止 look-ahead；
      - 唯一键 (instrument, metric, period_end, statement_type, published_at, provider)
        ⇒ 重述/更正 = 新行，禁止 UPDATE 覆盖；
      - "财报该披露尚未披露" = 无行，是正常状态，不是错误（缺口语义冻结）。
    """
    __tablename__ = "financial_facts"
    __table_args__ = (
        UniqueConstraint(
            "instrument_id", "metric_code", "period_end", "statement_type",
            "published_at", "provider",
            name="uq_financial_facts_inst_metric_period"),
        Index("ix_financial_facts_pit",
              "instrument_id", "metric_code", "period_end", "published_at"),  # as_of 查询主路径
        enum_ck("financial_facts", "statement_type", StatementType),
        enum_ck("financial_facts", "quality_status", DataQualityStatus),
        enum_ck("financial_facts", "period_type", PeriodType),
    )
    financial_fact_id: Mapped[UUID] = pk("financial_fact_id")
    instrument_id: Mapped[UUID] = mapped_column(ForeignKey("instruments.instrument_id"), nullable=False)
    metric_code: Mapped[str] = mapped_column(Text, nullable=False)     # 冻结清单见 FROZEN_METRIC_CODES
    period_start: Mapped[date | None] = mapped_column(Date)
    period_end: Mapped[date] = mapped_column(Date, nullable=False)
    period_type: Mapped[PeriodType | None] = mapped_column(Text)       # Q1/H1/Q3/FY
    statement_type: Mapped[StatementType] = mapped_column(Text, nullable=False)
    report_date: Mapped[date | None] = mapped_column(Date)             # 财报本身日期
    published_at: Mapped[datetime | None] = mapped_column(TIMESTAMPTZ)  # 正式披露时点（PIT 关键）
    retrieved_at: Mapped[datetime] = mapped_column(TIMESTAMPTZ, nullable=False)
    original_value: Mapped[Decimal | None] = mapped_column(FACT)       # 原始单位值
    original_unit: Mapped[str | None] = mapped_column(Text)            # 元/万元/亿元
    value: Mapped[Decimal] = mapped_column(FACT, nullable=False)       # 归一化值
    currency: Mapped[str] = mapped_column(Text, nullable=False, server_default="CNY")
    unit: Mapped[str] = mapped_column(Text, nullable=False, server_default="CNY")  # base_unit=CNY
    is_restated: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    provider: Mapped[str] = mapped_column(Text, nullable=False)
    source_document_id: Mapped[str | None] = mapped_column(Text)
    provenance_id: Mapped[UUID] = mapped_column(ForeignKey("provenance_records.provenance_id"), nullable=False)
    quality_status: Mapped[DataQualityStatus] = mapped_column(Text, nullable=False)
