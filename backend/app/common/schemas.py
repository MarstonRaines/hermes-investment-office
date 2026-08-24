# backend/app/common/schemas.py
from datetime import datetime
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.common.enums import ActorType, DataQualityStatus


class ORMModel(BaseModel):
    """出参基类：允许从 ORM 实例构造（from_attributes）。

    这是 ORM → Schema 的单向桥；Schema 绝不反向产生 ORM 状态（§1 分工）。
    """
    model_config = ConfigDict(from_attributes=True)


class QualityInfo(BaseModel):
    """统一数据质量块（ts01 MCP 包络：quality.status / score / flags）。"""
    status: DataQualityStatus
    score: Decimal = Field(ge=0, le=1)                       # quality_score ∈ [0,1] 冻结
    flags: list[str] = Field(default_factory=list)


class ProvenanceSummary(ORMModel):
    """包络内嵌 provenance 摘要（不泄漏完整记录）。"""
    provenance_id: UUID
    source: str
    provider: str
    observed_at: datetime
    retrieved_at: datetime
    quality_score: Decimal = Field(ge=0, le=1)


class ActorRef(BaseModel):
    """MCP 作者引用（ts01 create_thesis_revision 契约）。"""
    type: ActorType
    id: str


class ResponseEnvelope[T](BaseModel):
    """MCP/API 统一响应包络（TS-01 冻结五要素：request_id / as_of / data / quality / provenance）。"""
    request_id: UUID
    as_of: datetime
    data: T
    quality: QualityInfo
    provenance: list[ProvenanceSummary] = Field(default_factory=list)
