# backend/app/valuation/schemas.py —— valuation 域
from datetime import datetime
from decimal import Decimal
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field

from app.common.enums import ValuationInputType, ValuationModelType, ValuationRunStatus
from app.common.schemas import ORMModel


class ValuationAssumptionRead(ORMModel):
    valuation_assumption_id: UUID
    valuation_run_id: UUID
    name: str
    value: Decimal
    unit: str
    basis: str
    source_tags: list[str]


class ValuationInputRefRead(ORMModel):
    valuation_input_ref_id: UUID
    valuation_run_id: UUID
    input_type: ValuationInputType
    object_id: UUID | None
    object_version: str | None
    object_hash: str | None


class ValuationRunRead(ORMModel):
    valuation_run_id: UUID
    instrument_id: UUID
    model_type: ValuationModelType
    status: ValuationRunStatus
    as_of: datetime
    engine_version: str
    input_snapshot_hash: str | None
    bear_value: Decimal | None
    base_value: Decimal | None
    bull_value: Decimal | None
    current_price: Decimal | None
    margin_of_safety: Decimal | None
    result_json: dict[str, Any] | None
    created_by: str
    created_at: datetime
    completed_at: datetime | None
    provenance_id: UUID | None
    assumptions: list[ValuationAssumptionRead] = Field(default_factory=list)
    input_refs: list[ValuationInputRefRead] = Field(default_factory=list)


class ValuationAssumptionInput(BaseModel):
    """run_valuation 入参：name + value + unit + basis + source_tags（TS-01 冻结：拒绝裸 float）。"""
    name: str
    value: Decimal
    unit: Literal["ratio", "cny", "percent", "years"]
    basis: str = Field(min_length=1)           # 假设依据必填
    source_tags: list[str] = Field(default_factory=list)


class RunValuationRequest(BaseModel):
    instrument_id: UUID
    model_type: ValuationModelType
    as_of: datetime
    assumptions: list[ValuationAssumptionInput] = Field(min_length=1)
    # 缺参数 ⇒ BLOCKED_MISSING_INPUT；绝不自动补 wacc=8%（ts01 冻结）
