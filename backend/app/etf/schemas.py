# backend/app/etf/schemas.py —— etf 域（QDII 校验核心）
from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, Field, model_validator

from app.common.enums import DataQualityStatus, QuotaStatus
from app.common.schemas import ORMModel


class ETFProfileUpsert(BaseModel):
    """ETF 静态属性写入（1:1 Instrument）。"""
    instrument_id: UUID
    is_qdii: bool = False
    underlying_index_id: UUID | None = None
    fund_manager: str | None = None
    fund_name: str | None = None
    tracking_index_name: str | None = None

    @model_validator(mode="after")
    def _qdii_requires_index(self) -> "ETFProfileUpsert":
        if self.is_qdii and self.underlying_index_id is None:
            raise ValueError("is_qdii=true 时 underlying_index_id 必填（指向 InstrumentType=INDEX）")
        return self


class ETFMetricSnapshotRead(ORMModel):
    """get_etf_metrics 出参：QDII 四日期时序 + premium 对齐证据（ts01 冻结）。"""
    etf_metric_snapshot_id: UUID
    instrument_id: UUID
    as_of: datetime
    market_date: date
    is_qdii: bool
    underlying_index_id: UUID | None
    market_price_cny: Decimal | None
    nav: Decimal | None
    nav_date: date | None
    underlying_session_date: date | None
    premium_discount: Decimal | None
    fx_contribution: Decimal | None
    fx_as_of: datetime | None
    quota_status: QuotaStatus | None
    net_value_t1: Decimal | None
    index_pe: Decimal | None
    index_pb: Decimal | None
    reference_nav_basis: str | None
    valuation_band: str | None
    band_basis: str | None
    band_inputs: dict | None
    band_thresholds_hash: str | None
    details: dict | None
    engine_version: str
    input_hash: str
    quality_score: Decimal = Field(ge=0, le=1)
    quality_status: DataQualityStatus
    quality_flags: list[str]
    provenance_id: UUID
    created_at: datetime

    @model_validator(mode="after")
    def _premium_alignment(self) -> "ETFMetricSnapshotRead":
        # 不变量（ts01/ts02 冻结）：premium 非空 ⇒ 必须有 NAV 日期作为时间对齐证据
        if self.premium_discount is not None and self.nav_date is None:
            raise ValueError("premium_discount 非空时 nav_date 必须存在（NAV_TIME_ALIGNMENT）")
        return self
