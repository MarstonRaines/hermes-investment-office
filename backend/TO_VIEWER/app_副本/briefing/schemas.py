# backend/app/briefing/schemas.py —— briefing 域
from datetime import date, datetime
from typing import Any
from uuid import UUID

from pydantic import Field

from app.common.enums import AttentionItemType, FreshnessStatus
from app.common.schemas import ORMModel


class AttentionItemRead(ORMModel):
    attention_item_id: UUID
    daily_context_id: UUID
    item_type: AttentionItemType
    rule_name: str
    instrument_id: UUID | None
    severity: str | None
    detail: dict[str, Any] | None
    is_processed: bool
    created_at: datetime


class DailyContextRead(ORMModel):
    """get_daily_context 出参：含 Freshness Contract 字段（冻结规范 §36.1）。"""
    daily_context_id: UUID
    market_date: date
    generated_at: datetime
    freshness_status: FreshnessStatus
    data_freshness: dict[str, Any]
    markets: dict[str, Any]
    engine_versions: dict[str, Any]
    source_status: dict[str, Any]
    summary: str | None
    attention_items: list[AttentionItemRead] = Field(default_factory=list)
