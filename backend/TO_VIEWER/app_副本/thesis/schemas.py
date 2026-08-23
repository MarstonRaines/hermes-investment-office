# backend/app/thesis/schemas.py —— thesis 域
from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field

from app.common.enums import (
    AssumptionCategory, Conviction, ThesisHealthStatus, ThesisLifecycleStatus,
)
from app.common.schemas import ActorRef, ORMModel


class ThesisAssumptionRead(ORMModel):
    assumption_id: UUID
    thesis_id: UUID
    thesis_revision_id: UUID
    statement: str
    category: AssumptionCategory | None
    status: ThesisHealthStatus                 # UNKNOWN/HEALTHY/WARNING/BROKEN；AT_RISK 无法通过枚举
    test_condition: str | None
    verification_frequency: str | None
    is_red_line: bool
    superseded_at: datetime | None
    superseded_by: UUID | None


class ThesisRevisionRead(ORMModel):
    thesis_revision_id: UUID
    thesis_id: UUID
    version: int
    thesis_body: dict[str, Any]
    summary: str | None
    change_reason: str
    authored_by: str
    base_revision_id: UUID | None
    provenance_id: UUID | None
    created_at: datetime
    assumptions: list[ThesisAssumptionRead] = Field(default_factory=list)   # 服务层组装


class ThesisRead(ORMModel):
    thesis_id: UUID
    instrument_id: UUID
    lifecycle_status: ThesisLifecycleStatus    # 与 health_status 正交（双状态机）
    health_status: ThesisHealthStatus
    conviction: Conviction | None
    fair_value_low: Decimal | None
    fair_value_base: Decimal | None
    fair_value_high: Decimal | None
    current_revision_id: UUID | None
    current_revision: ThesisRevisionRead | None = None   # get_thesis 出参（PIT 可见版本）


class CreateThesisRevisionRequest(BaseModel):
    """API 入参（对应 MCP create_thesis_revision）。"""
    thesis_id: UUID
    base_revision_id: UUID                      # 必须 == 当前 head，否则 409 DOMAIN_CONFLICT
    change_reason: str = Field(min_length=1)
    thesis_body: dict[str, Any]
    assumption_changes: list[dict[str, Any]] = Field(default_factory=list)
    evidence_ids: list[UUID] = Field(default_factory=list)
    author: ActorRef
