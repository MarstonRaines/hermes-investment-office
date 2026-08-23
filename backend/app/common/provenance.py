# =====================================================================
# backend/app/common/provenance.py —— 全局 ProvenanceEnvelope（共享契约类型）
#
# 依据：
# - ts01 §1（provenance 一级对象）/ TS-05 §2.0.4（落库映射）
# - 2026-08-24 架构修复：ProvenanceEnvelope 从 app.providers.contracts.base
#   上提到 app.common（引擎域反向 import providers 违反 ARCH-DEP 规则；
#   provenance 是全局领域概念，不属于 Provider 层私有）。
# - providers/contracts/base.py 从此模块 re-export（保持既有 import 兼容）。
# =====================================================================
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, Field

from app.common.enums import DataQualityStatus, SourceKind


class ProvenanceEnvelope(BaseModel):
    """与 TS-02 provenance_records 逐列对齐（映射见 TS-05 §2.0.4）。

    硬性一致性约束：本类型的所有信息必须能无损落到 ts02 已冻结的
    provenance_records 表结构上；任何需要新增列的需求必须先走 ADR 改 TS-02，
    禁止在 Provider 侧自造存储。
    """

    source_kind: SourceKind = SourceKind.PROVIDER
    source: str                                 # 来源名/数据集名（规范见各接口）
    provider: str                               # tushare / akshare_sina / yahoo / fred / ...
    source_uri: str | None = None
    source_record_id: str | None = None         # Provider 原始记录标识（§2.0.3 命名规范）
    published_at: datetime | None = None        # 原始信息发布时点
    observed_at: datetime                       # 该数值代表的市场/事实时点
    retrieved_at: datetime                      # 系统取得数据时间
    as_of_date: date | None = None              # PIT 语义日期
    quality_score: Decimal = Field(ge=0, le=1)
    quality_status: DataQualityStatus
    quality_flags: list[str] = Field(default_factory=list)   # 缺失/异常/fallback/时间错配
    fallback_used: bool = False
    requested_provider: str | None = None       # fallback 时：请求的 primary
    fallback_reason: str | None = None          # PRIMARY_TIMEOUT / PRIMARY_RATE_LIMITED / ...
    raw_hash: str | None = None                 # sha256（§7）
    raw_object_key: str | None = None           # data/raw/... 相对路径
    ingestion_run_id: UUID | None = None        # 回溯 ingestion job
    actor_id: str | None = None                 # source_kind=HERMES/HUMAN 时必填
    evidence_ids: list[str] = Field(default_factory=list)
    transform_version: str                      # 规范化/计算版本
