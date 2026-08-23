# =====================================================================
# backend/app/providers/capability_matrix.py —— provider-capability.yaml 加载与校验
#
# 依据：
# - TS-05 §4（YAML 权威源，冻结结构；变更必须走 ADR）
# - ADR-005 D1（网络三态配置进入 provider-capability.yaml 的 providers 节）
# - ADR-006（M0.5 Spike 回流矩阵修订：FX/INDEX_VALUATION 冻结、INDEX_WEIGHT 下调、
#   akshare 按源拆分、FUND_NAV/HOLDINGS/QUOTA 实测结果）
#
# spike_status 语义（供架构测试 A4 判定"已声明必须实现" vs "声明但允许缺省"）：
#   VERIFIED            —— 实测锁定且 M1 实现（primary/fallback/auxiliary 必须在注册表注册）
#   PARTIAL             —— 部分实现（如 QUOTA_STATUS：返回 UNKNOWN 语义，人工入口后续）
#   DEFERRED            —— 明确下调/推迟（不要求实现）
#   PLANNED_NOT_IN_M1   —— 契约冻结，M1 不实现
#   NOT_IN_V0_1         —— v0.1 明确不做（如 NEWS）
# =====================================================================
from __future__ import annotations

from datetime import date
from pathlib import Path

import yaml
from pydantic import BaseModel, Field, field_validator

from app.providers.contracts.base import ProviderCapability

__all__ = [
    "NetworkConfig",
    "RateLimitConfig",
    "ProviderRuntimeConfig",
    "DomainEntry",
    "CapabilityMatrix",
    "load_capability_matrix",
]

# spike_status 值集合（架构测试 A4 的判定依据）
IMPLEMENTED_STATUSES = frozenset({"VERIFIED", "PARTIAL"})   # 要求 YAML 声明的 provider 已注册
DEFERRED_STATUSES = frozenset({"DEFERRED", "PLANNED_NOT_IN_M1", "NOT_IN_V0_1"})


class NetworkConfig(BaseModel):
    """ADR-005 D1：per-provider 网络三态。

    direct        —— 强制直连（忽略环境代理）
    env           —— 跟随系统/环境代理
    http(s)://..  —— 显式代理地址
    """

    proxy: str = "direct"

    @field_validator("proxy")
    @classmethod
    def _valid_proxy(cls, v: str) -> str:
        if v in ("direct", "env") or v.startswith(("http://", "https://")):
            return v
        raise ValueError(f"invalid proxy mode: {v!r}（direct / env / http(s)://host:port）")


class RateLimitConfig(BaseModel):
    qps: float = 1.0
    burst: int = 3
    daily_quota: int = 3000


class ProviderRuntimeConfig(BaseModel):
    """provider 节（ADR-005 D1：网络 + 能力矩阵展示元数据）。

    运行参数（timeout/retry/rate_limit/score）在 providers.yaml（TS-05 §3.5），
    由 RuntimeProviderConfigs 加载；本模型只含矩阵语义字段。
    """

    display_name: str
    quality_tier: str
    network: NetworkConfig = Field(default_factory=NetworkConfig)


class DomainEntry(BaseModel):
    """数据域（TS-05 §4.1 模板 + ADR-006 修订）。"""

    id: ProviderCapability
    label: str
    primary: str | None = None
    fallback: list[str] = Field(default_factory=list)
    auxiliary: list[str] = Field(default_factory=list)   # ADR-006：交叉验证/辅助源（如 fred 验证 FX）
    authority: str = ""
    quality_tier: str = ""
    freshness: str = ""
    limits: list[str] = Field(default_factory=list)
    spike_status: str = "VERIFIED"

    def provider_names(self) -> list[str]:
        """本域声明参与取数的 provider 名单（primary → fallback → auxiliary）。"""
        out: list[str] = []
        if self.primary:
            out.append(self.primary)
        out.extend(self.fallback)
        out.extend(self.auxiliary)
        return out


class CapabilityMatrix(BaseModel):
    """provider-capability.yaml 权威源（冻结结构 + ADR-005/006 修订）。"""

    version: str
    updated_at: date
    source: str
    providers: dict[str, ProviderRuntimeConfig]
    domains: list[DomainEntry]

    @field_validator("version", mode="before")
    @classmethod
    def _coerce_version(cls, v: object) -> str:
        """冻结模板写 `version: 0.1`（YAML 解析为 float），统一转 str。"""
        return str(v) if v is not None else ""

    def domain(self, capability: ProviderCapability) -> DomainEntry | None:
        for d in self.domains:
            if d.id == capability:
                return d
        return None

    def chain_for(self, capability: ProviderCapability) -> list[str]:
        """primary → fallback 链（不含 auxiliary）。"""
        d = self.domain(capability)
        if d is None or not d.primary:
            return []
        return [d.primary, *d.fallback]


def load_capability_matrix(path: str | Path) -> CapabilityMatrix:
    """加载并校验 provider-capability.yaml。失败抛 ValueError（架构测试 A4 依赖）。"""
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"provider-capability.yaml 不存在: {p}")
    raw = yaml.safe_load(p.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"{p}: 顶层必须是映射")
    matrix = CapabilityMatrix.model_validate(raw)
    # 交叉校验：域声明的 provider 必须存在于 providers 节（ADR-005 D1 一致性）
    for d in matrix.domains:
        for name in d.provider_names():
            if name not in matrix.providers:
                raise ValueError(
                    f"domain {d.id.value}: provider {name!r} 未在 providers 节声明"
                )
    return matrix
