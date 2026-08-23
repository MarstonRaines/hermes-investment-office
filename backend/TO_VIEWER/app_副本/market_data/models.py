# backend/app/market_data/models.py —— 模块归属：market_data（Market Data Engine）
from datetime import date, datetime
from uuid import UUID

from sqlalchemy import Date, ForeignKey, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.common.base import Base
from app.common.db import enum_ck
from app.common.enums import DataQualityStatus
from app.common.mixins import CreatedAtMixin, UUIDPrimaryKeyMixin
from app.common.types import TIMESTAMPTZ

pk = UUIDPrimaryKeyMixin.pk


class MarketBarIndex(Base, CreatedAtMixin):
    """OHLCVA 元数据索引（数值在 Parquet，PG 只存指针与质量，ts02 §4.2 冻结）。

    物理分工：禁止在 PG 新增行情数值列；查询路径 = PG 索引定位 parquet_path → DuckDB。
    """
    __tablename__ = "market_bar_index"
    __table_args__ = (
        UniqueConstraint("instrument_id", "trade_date", "provider",
                         name="uq_market_bar_index_inst_date"),
        enum_ck("market_bar_index", "quality_status", DataQualityStatus),
    )
    bar_id: Mapped[UUID] = pk("bar_id")
    instrument_id: Mapped[UUID] = mapped_column(ForeignKey("instruments.instrument_id"), nullable=False)
    trade_date: Mapped[date] = mapped_column(Date, nullable=False)     # A 股交易日
    provider: Mapped[str] = mapped_column(Text, nullable=False)        # 实际取数 provider
    source_timestamp: Mapped[datetime | None] = mapped_column(TIMESTAMPTZ)
    ingested_at: Mapped[datetime] = mapped_column(TIMESTAMPTZ, nullable=False)
    quality_status: Mapped[DataQualityStatus] = mapped_column(Text, nullable=False)
    provenance_id: Mapped[UUID] = mapped_column(ForeignKey("provenance_records.provenance_id"), nullable=False)
    parquet_path: Mapped[str] = mapped_column(Text, nullable=False)    # ohlcva/v1/<date>/<inst>.parquet
