# backend/app/etf/models.py —— 模块归属：etf（ETF Data Service / ETF Engine）
from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    ForeignKey,
    Index,
    Integer,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.common.base import Base
from app.common.db import enum_ck, range_ck
from app.common.enums import DataQualityStatus, HoldingSource, QuotaStatus
from app.common.mixins import CreatedAtMixin, UUIDPrimaryKeyMixin
from app.common.types import PE_PB, PRICE, QUALITY, TIMESTAMPTZ
from app.instruments.models import Instrument

pk = UUIDPrimaryKeyMixin.pk


class ETFProfile(Base):
    """ETF 静态/准静态属性（1:1 Instrument，PK=FK）。

    QDII = is_qdii=true + underlying_index_id（指向 InstrumentType=INDEX）。
    绝不新增 US_ETF、绝不新增第二套 ETF 表（ts01/ts02 冻结）。
    跨表约束（underlying 必须是 INDEX）由服务层 + 触发器 trg_etf_profile_index_type 兜底。
    注意：ts02 §3.3 只定义 updated_at（无 created_at），故不继承 CreatedAtMixin。
    """
    __tablename__ = "etf_profiles"
    __table_args__ = (
        CheckConstraint("is_qdii = FALSE OR underlying_index_id IS NOT NULL",
                        name="qdii_index"),
    )
    instrument_id: Mapped[UUID] = mapped_column(
        ForeignKey("instruments.instrument_id"), primary_key=True)     # 身份来自 instruments
    is_qdii: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    underlying_index_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("instruments.instrument_id"))                      # 指向 INDEX（跨表类型由服务层/触发器保证）
    fund_manager: Mapped[str | None] = mapped_column(Text)
    fund_name: Mapped[str | None] = mapped_column(Text)
    tracking_index_name: Mapped[str | None] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMPTZ, nullable=False, server_default=func.now(), onupdate=func.now())

    instrument: Mapped[Instrument] = relationship(
        foreign_keys=[instrument_id],                     # 消除多 FK 路径歧义（underlying_index_id 也指向 instruments）
    )


class ETFNavObservation(Base, CreatedAtMixin):
    """基金 NAV 事实。nav_date（估值日）与 published_at（披露时点）必须并存（ts01/ts02 冻结）。"""
    __tablename__ = "etf_nav_observations"
    __table_args__ = (
        UniqueConstraint("instrument_id", "nav_date", "provider",
                         name="uq_etf_nav_inst_date_provider"),
    )
    nav_observation_id: Mapped[UUID] = pk("nav_observation_id")
    instrument_id: Mapped[UUID] = mapped_column(ForeignKey("instruments.instrument_id"), nullable=False)
    nav_date: Mapped[date] = mapped_column(Date, nullable=False)      # 基金估值日
    nav: Mapped[Decimal] = mapped_column(PRICE, nullable=False)       # 单位净值
    currency: Mapped[str] = mapped_column(Text, nullable=False, server_default="CNY")
    published_at: Mapped[datetime | None] = mapped_column(TIMESTAMPTZ)  # 管理人正式披露时点（T+1 语义）
    retrieved_at: Mapped[datetime] = mapped_column(TIMESTAMPTZ, nullable=False)
    provider: Mapped[str] = mapped_column(Text, nullable=False)
    provenance_id: Mapped[UUID] = mapped_column(ForeignKey("provenance_records.provenance_id"), nullable=False)
    parquet_path: Mapped[str | None] = mapped_column(Text)


class ETFHoldingSnapshot(Base, CreatedAtMixin):
    """披露持仓快照（Level 1 穿透；明细在 Parquet）。disclosure_date 是穿透结果 as_of 依据。

    穿透分级（冻结规范 §23.1）：本表即 Level 1；Level 2（估算 exposure）由 ETF Engine
    计算产出，不进本表；禁止假设实时穿透。
    """
    __tablename__ = "etf_holding_snapshots"
    __table_args__ = (
        UniqueConstraint("instrument_id", "report_period", "source",
                         name="uq_etf_holdings_inst_period_source"),
        enum_ck("etf_holding_snapshots", "source", HoldingSource),
    )
    holding_snapshot_id: Mapped[UUID] = pk("holding_snapshot_id")
    instrument_id: Mapped[UUID] = mapped_column(ForeignKey("instruments.instrument_id"), nullable=False)
    report_period: Mapped[date] = mapped_column(Date, nullable=False)   # 季报/半年报/年报
    disclosure_date: Mapped[date] = mapped_column(Date, nullable=False)  # 披露日
    source: Mapped[HoldingSource] = mapped_column(Text, nullable=False)
    holding_count: Mapped[int | None] = mapped_column(Integer)
    holdings_json: Mapped[dict | None] = mapped_column(JSONB)     # 可选：完整性元数据/短持仓直存
    parquet_path: Mapped[str | None] = mapped_column(Text)              # etf_holdings/v1|v2/...
    provenance_id: Mapped[UUID] = mapped_column(ForeignKey("provenance_records.provenance_id"), nullable=False)


class ETFMetricSnapshot(Base, CreatedAtMixin):
    """ETF 分析结果（引擎产出，append-only）。

    QDII 时序建模（ts01 冻结）：market_date / nav_date / underlying_session_date / fx_as_of
    四个日期必须同时保存；premium_discount 无法时间对齐时置 NULL + quality_flags
    标记（如 NAV_TIME_ALIGNMENT_FAILED），禁止返回"看起来精确"的错误值；
    quota_status 是事件状态（来自公告 provenance），禁止从溢价率推断。
    """
    __tablename__ = "etf_metric_snapshots"
    __table_args__ = (
        Index("ix_etf_metric_inst_asof", "instrument_id", text("as_of DESC")),
        enum_ck("etf_metric_snapshots", "quota_status", QuotaStatus),
        enum_ck("etf_metric_snapshots", "quality_status", DataQualityStatus),
        range_ck("etf_metric_snapshots", "quality_score", "0", "1"),
    )
    etf_metric_snapshot_id: Mapped[UUID] = pk("etf_metric_snapshot_id")
    instrument_id: Mapped[UUID] = mapped_column(ForeignKey("instruments.instrument_id"), nullable=False)
    as_of: Mapped[datetime] = mapped_column(TIMESTAMPTZ, nullable=False)  # 计算时点
    market_date: Mapped[date] = mapped_column(Date, nullable=False)       # A 股交易日
    is_qdii: Mapped[bool] = mapped_column(Boolean, nullable=False)        # 冗余快照（防 join）
    underlying_index_id: Mapped[UUID | None] = mapped_column(ForeignKey("instruments.instrument_id"))
    market_price_cny: Mapped[Decimal | None] = mapped_column(PRICE)       # 场内价格
    nav: Mapped[Decimal | None] = mapped_column(PRICE)                    # 配对参考净值
    nav_date: Mapped[date | None] = mapped_column(Date)                   # 净值日期
    underlying_session_date: Mapped[date | None] = mapped_column(Date)    # 美股指数交易日（QDII 关键）
    premium_discount: Mapped[Decimal | None] = mapped_column(PRICE)       # 溢价率（可为负）
    fx_contribution: Mapped[Decimal | None] = mapped_column(PRICE)        # 汇率贡献（QDII）
    fx_as_of: Mapped[datetime | None] = mapped_column(TIMESTAMPTZ)        # FX 时点
    quota_status: Mapped[QuotaStatus | None] = mapped_column(Text)
    net_value_t1: Mapped[Decimal | None] = mapped_column(PRICE)           # 最新可得已发布净值
    index_pe: Mapped[Decimal | None] = mapped_column(PE_PB)               # 指数 PE（Level 2）
    index_pb: Mapped[Decimal | None] = mapped_column(PE_PB)
    reference_nav_basis: Mapped[str | None] = mapped_column(Text)
    valuation_band: Mapped[str | None] = mapped_column(Text)
    band_basis: Mapped[str | None] = mapped_column(Text)
    band_inputs: Mapped[dict | None] = mapped_column(JSONB)
    band_thresholds_hash: Mapped[str | None] = mapped_column(Text)
    details: Mapped[dict | None] = mapped_column(JSONB)
    engine_version: Mapped[str] = mapped_column(Text, nullable=False)     # 计算版本
    input_hash: Mapped[str] = mapped_column(Text, nullable=False)         # 输入冻结 hash
    quality_score: Mapped[Decimal] = mapped_column(QUALITY, nullable=False)
    quality_status: Mapped[DataQualityStatus] = mapped_column(Text, nullable=False)
    quality_flags: Mapped[list[str]] = mapped_column(JSONB, nullable=False, server_default=text("'[]'"))
    provenance_id: Mapped[UUID] = mapped_column(ForeignKey("provenance_records.provenance_id"), nullable=False)
