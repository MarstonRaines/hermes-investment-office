# =====================================================================
# backend/app/providers/contracts/etf.py —— 冻结：ETFProvider（TS-05 §2.4）
#
# 数据范围：基金 NAV 观测、披露持仓快照、QDII 额度状态。
# 覆盖普通 A 股 ETF 与 QDII ETF（冻结规范 §12.3）。
#
# 关键冻结约束：
# - 持仓 symbol 处理：HoldingItem.provider_symbol 只是原始展示代码；
#   normalizer 经 provider_symbols 解析为 instrument_id。解析失败时保留
#   provider_symbol + quality_flags=['UNRESOLVED_SYMBOL']，不允许静默丢弃。
# - 额度是事件状态，必须来自公告 provenance，禁止从溢价率推断。
# =====================================================================
from __future__ import annotations

import abc
from datetime import date, datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import BaseModel

from app.common.enums import QuotaStatus
from app.providers.contracts.base import (
    BaseProvider,
    ProvenanceEnvelope,
    ProviderCapability,
)

__all__ = [
    "NavResult",
    "HoldingItem",
    "HoldingSnapshotResult",
    "QuotaStatusResult",
    "ETFProvider",
]


class NavResult(BaseModel):
    """对齐 TS-02 etf_nav_observations（§4.4）。"""

    instrument_id: UUID
    nav_date: date                 # 净值对应日（基金估值日）
    nav: Decimal
    currency: str = "CNY"
    published_at: datetime | None = None   # 管理人正式披露时点（T+1 语义）
    retrieved_at: datetime
    provenance: ProvenanceEnvelope


class HoldingItem(BaseModel):
    rank: int | None = None
    provider_symbol: str | None = None     # 原始代码——不是内部主键
    security_name: str | None = None
    instrument_id: UUID | None = None      # 解析成功后的内部身份（normalizer 填充）
    weight_pct: Decimal | None = None      # 占净值比（%）
    market_value: Decimal | None = None
    shares: Decimal | None = None


class HoldingSnapshotResult(BaseModel):
    """对齐 TS-02 etf_holding_snapshots（§4.5）。"""

    instrument_id: UUID
    report_period: date
    disclosure_date: date
    source: str                      # QUARTERLY / HALF_YEAR / ANNUAL / OTHER
    holdings: list[HoldingItem]
    holding_count: int | None = None
    provenance: ProvenanceEnvelope
    # AkShare's current disclosure endpoint returns a top-N slice.  Providers
    # must set FULL only when the payload is explicitly a complete disclosure.
    disclosure_completeness: Literal["TOP_N", "FULL"] = "TOP_N"


class QuotaStatusResult(BaseModel):
    instrument_id: UUID
    quota_status: QuotaStatus        # NOT_APPLICABLE/UNKNOWN/OPEN/RESTRICTED/SUSPENDED
    effective_from: date | None = None
    announcement_date: date | None = None
    source_uri: str | None = None
    provenance: ProvenanceEnvelope


class ETFProvider(BaseProvider):
    capabilities = frozenset(
        {
            ProviderCapability.FUND_NAV,
            ProviderCapability.FUND_HOLDINGS,
            ProviderCapability.QUOTA_STATUS,
        }
    )

    @abc.abstractmethod
    async def get_nav_history(
        self,
        instrument_id: UUID,
    ) -> list[NavResult]:
        """NAV 历史（normalizer 按 nav_date 去重 upsert-by-supersede）。"""

    @abc.abstractmethod
    async def get_holding_snapshots(
        self,
        instrument_id: UUID,
    ) -> list[HoldingSnapshotResult]:
        """披露持仓快照（Level 1，禁止假设实时穿透，冻结规范 §23.1）。
        明细默认落 parquet（etf_holdings/v1/）；短持仓可存 PG holdings_json。"""

    @abc.abstractmethod
    async def get_quota_status(
        self,
        instrument_id: UUID,
    ) -> QuotaStatusResult:
        """最新额度状态（事件状态，必须有公告 provenance；无有效来源时返回 UNKNOWN）。"""
