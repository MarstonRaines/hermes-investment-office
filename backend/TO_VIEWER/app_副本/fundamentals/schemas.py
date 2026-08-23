# backend/app/fundamentals/schemas.py —— fundamentals 域（PIT 出参）
from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

from app.common.enums import DataQualityStatus, PeriodType, StatementType
from app.common.schemas import ORMModel


class FinancialFactRead(ORMModel):
    financial_fact_id: UUID
    instrument_id: UUID
    metric_code: str
    period_start: date | None
    period_end: date
    period_type: PeriodType | None
    statement_type: StatementType
    report_date: date | None
    published_at: datetime | None              # PIT：查询时用 published_at <= as_of 过滤（§9.3）
    retrieved_at: datetime
    original_value: Decimal | None
    original_unit: str | None
    value: Decimal
    currency: str
    unit: str
    is_restated: bool
    provider: str
    source_document_id: str | None
    provenance_id: UUID
    quality_status: DataQualityStatus
