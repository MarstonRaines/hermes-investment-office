# backend/app/fx/models.py —— 模块归属：fx（QDII FX 分析专用）
from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import Date, ForeignKey, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.common.base import Base
from app.common.mixins import CreatedAtMixin, UUIDPrimaryKeyMixin
from app.common.types import FX_RATE, TIMESTAMPTZ

pk = UUIDPrimaryKeyMixin.pk


class FXObservation(Base, CreatedAtMixin):
    """汇率观察：仅 QDII 分析使用，绝不进入组合 NAV 折算路径（ts01/ts02 冻结）。

    组合总资产恒 CNY 计价；QDII ETF 场内市值按 CNY 直接计入组合 NAV。
    """
    __tablename__ = "fx_observations"
    __table_args__ = (
        UniqueConstraint("base_currency", "quote_currency", "as_of", "provider",
                         name="uq_fx_inst_pair_asof_provider"),
    )
    fx_observation_id: Mapped[UUID] = pk("fx_observation_id")
    base_currency: Mapped[str] = mapped_column(Text, nullable=False)   # USD
    quote_currency: Mapped[str] = mapped_column(Text, nullable=False)  # CNY
    rate: Mapped[Decimal] = mapped_column(FX_RATE, nullable=False)     # 1 base = rate quote
    as_of: Mapped[datetime] = mapped_column(TIMESTAMPTZ, nullable=False)
    trade_date: Mapped[date | None] = mapped_column(Date)
    provider: Mapped[str] = mapped_column(Text, nullable=False)        # TS-05 冻结后确定
    provenance_id: Mapped[UUID] = mapped_column(ForeignKey("provenance_records.provenance_id"), nullable=False)
