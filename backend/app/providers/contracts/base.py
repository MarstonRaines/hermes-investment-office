# =====================================================================
# backend/app/providers/contracts/base.py —— 冻结：接口公共契约（TS-05 §2.0）
#
# 依据：
# - TS-05 §2.0（六接口共享类型，冻结）
# - 一致性约束：DataQualityStatus / SourceKind / QuotaStatus 从
#   app.common.enums 导入（全库唯一事实来源），禁止出现第二份定义
#   （TS-05 §2.0："实际代码从领域共享模块导入，禁止出现两份定义"）。
# - ProviderCapability / ProviderRole / QualityTier 是 Provider 层新增
#   枚举（TS-05 §2.0 明确"新增枚举，不与 TS-01 冲突"），定义在本模块。
# =====================================================================
from __future__ import annotations

import abc
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from typing import ClassVar
from uuid import UUID

from pydantic import BaseModel, Field

from app.common.enums import (  # noqa: F401 —— QuotaStatus 为再导出（contracts/__init__ 引用）
    DataQualityStatus,
    QuotaStatus,
    SourceKind,
)

from app.common.provenance import ProvenanceEnvelope  # noqa: F401 —— 全局共享（2026-08-24 上提）

# ---------------------------------------------------------------------
# Provider 层枚举（TS-05 §2.0，新增；不与 TS-01 冲突）
# ---------------------------------------------------------------------


class ProviderCapability(StrEnum):
    """能力声明：一个 provider 能提供哪些数据域（与 provider-capability.yaml domains 对齐）。"""

    CN_DAILY_QUOTE = "CN_DAILY_QUOTE"        # A 股股票日行情
    CN_ETF_QUOTE = "CN_ETF_QUOTE"            # A 股场内 ETF 行情（含 QDII ETF）
    ADJ_FACTOR = "ADJ_FACTOR"                # 复权因子
    INDEX_QUOTE = "INDEX_QUOTE"              # 指数点位（^GSPC / ^NDX / A 股指数）
    INDEX_WEIGHT = "INDEX_WEIGHT"            # 指数成分与权重
    FINANCIAL_STATEMENTS = "FINANCIAL_STATEMENTS"
    FUND_NAV = "FUND_NAV"
    FUND_HOLDINGS = "FUND_HOLDINGS"
    QUOTA_STATUS = "QUOTA_STATUS"            # QDII 额度 / 限购状态
    FX_RATES = "FX_RATES"
    INDEX_VALUATION = "INDEX_VALUATION"      # 指数 PE/PB 历史
    MACRO_SERIES = "MACRO_SERIES"
    FILINGS = "FILINGS"                      # 公告 / 定期报告文档
    NEWS = "NEWS"


class ProviderRole(StrEnum):
    PRIMARY = "PRIMARY"          # 运营主源
    FALLBACK = "FALLBACK"        # 备用源
    AUTHORITY = "AUTHORITY"      # 权威冲突源（正式披露 / 交易所），冲突裁决时优先
    AUXILIARY = "AUXILIARY"      # 辅助/验证用


class QualityTier(StrEnum):
    """Provider 质量分级（与 quality_score 互补，用于 Capability Matrix）。"""

    TIER_1 = "TIER_1"            # 官方 / 交易所 / 正式披露：高权威，冲突裁决最高优先
    TIER_2 = "TIER_2"            # 授权/规范化聚合接口（如 TuShare 标准化接口）
    TIER_3 = "TIER_3"            # 免费聚合 / 爬虫（AkShare、Yahoo 非官方），需交叉验证
    TIER_4 = "TIER_4"            # 未验证 / 一次性


# ---------------------------------------------------------------------
# 健康与质量报告
# ---------------------------------------------------------------------


class ProviderHealth(BaseModel):
    provider: str
    status: str                  # HEALTHY / DEGRADED / DOWN / UNKNOWN
    checked_at: datetime
    detail: dict[str, str] = Field(default_factory=dict)


class ProviderQualityReport(BaseModel):
    provider: str
    tier: QualityTier
    quality_score: Decimal = Field(ge=0, le=1)   # 近期数据质量评分（0-1），≠ 权威度
    last_success_at: datetime | None = None
    last_failure_at: datetime | None = None
    consecutive_failures: int = 0
    error_rate_1d: Decimal | None = None
    latency_p95_ms: int | None = None


# ---------------------------------------------------------------------
# ProvenanceEnvelope（TS-05 §2.0 / §2.0.4 落库映射）
# ---------------------------------------------------------------------


class ProviderError(Exception):
    """Provider 层 typed 错误基类。"""


class ProviderAuthError(ProviderError):
    """token 缺失/无效/积分不足（403）。不重试、不 fallback，写 Audit + 告警。"""


class ProviderRateLimited(ProviderError):
    """限流 / 积分不足。按退避策略重试；多次限流可 fallback（fallback_reason=RATE_LIMITED）。"""

    def __init__(self, message: str, retry_after: float | None = None) -> None:
        super().__init__(message)
        self.retry_after = retry_after


class ProviderTimeout(ProviderError):
    """网络超时（connect/read）。可重试。"""


class ProviderUnavailable(ProviderError):
    """网络不可达 / 上游 5xx / 超时（重试耗尽后）。"""


class ProviderDataError(ProviderError):
    """上游返回了但内容非法（字段缺失、单位异常、日期乱序）。不重试；raw 落盘后失败。"""


class ProviderConfigError(ProviderError):
    """配置缺失（token 未配置等），启动/健康检查时暴露。不重试。"""


# ---------------------------------------------------------------------
# BaseProvider（TS-05 §2.0.2，所有 Provider 必须实现）
# ---------------------------------------------------------------------


class BaseProvider(abc.ABC):
    provider_name: ClassVar[str]                       # 'tushare' / 'akshare_sina' / ...
    display_name: ClassVar[str]                        # 'TuShare Pro'
    capabilities: ClassVar[frozenset[ProviderCapability]]
    default_role: ClassVar[ProviderRole]
    quality_tier: ClassVar[QualityTier]
    known_limits: ClassVar[list[str]]                  # 积分门槛/限流/停更风险

    @abc.abstractmethod
    async def health_check(self) -> ProviderHealth:
        """健康检查。token 缺失 → status=DOWN + ProviderConfigError（启动告警不阻塞其他 provider）。"""

    @abc.abstractmethod
    async def quality_report(self) -> ProviderQualityReport:
        """质量报告（按最近调用统计）。"""
