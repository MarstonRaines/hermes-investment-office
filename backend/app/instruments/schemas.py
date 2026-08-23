# backend/app/instruments/schemas.py —— instruments 域
from datetime import date, datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field

from app.common.enums import InstrumentMarket, InstrumentStatus, InstrumentType
from app.common.schemas import ORMModel


class InstrumentCreate(BaseModel):
    """API 入参（严格校验；无 from_attributes）。"""
    instrument_type: InstrumentType          # 枚举约束：非法值直接 422
    symbol: str = Field(min_length=1, max_length=32)
    name: str = Field(min_length=1)
    market: InstrumentMarket
    currency: Literal["CNY"] = "CNY"
    status: InstrumentStatus = InstrumentStatus.ACTIVE
    lot_size: Decimal | None = None
    isin: str | None = None


class InstrumentUpdate(BaseModel):
    """属性演进（versioned update）：必须携带 version 做乐观锁。"""
    name: str | None = None
    status: InstrumentStatus | None = None
    isin: str | None = None
    version: int                              # WHERE version = $version，冲突 → 409


class ProviderSymbolRead(ORMModel):
    provider_symbol_id: UUID
    instrument_id: UUID
    provider: str
    symbol: str
    valid_from: date
    valid_to: date | None


class InstrumentRead(ORMModel):
    """领域/出参 schema：与 ORM 一一对应。"""
    instrument_id: UUID
    instrument_type: InstrumentType
    symbol: str
    name: str
    market: InstrumentMarket
    exchange: str | None
    currency: str
    lot_size: Decimal | None
    status: InstrumentStatus
    isin: str | None
    version: int
    created_at: datetime
    updated_at: datetime


# 跨模块聚合视图（Instrument + provider_symbols + etf_profile）放在 api/ 层组装，
# 避免 instruments/schemas.py import etf/schemas.py 形成模块耦合（§6.1.3）。
