# =====================================================================
# backend/app/providers/contracts/market_data.py —— 冻结：MarketDataProvider（TS-05 §2.1）
#
# 数据范围：A 股股票与 A 股场内 ETF 的日线 OHLCVA、复权因子、时点快照。
# v0.1 明确不包含分钟级 / tick 行情（冻结规范 §44 Non-goals）。
# =====================================================================
from __future__ import annotations

import abc
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel

from app.providers.contracts.base import (
    BaseProvider,
    ProvenanceEnvelope,
    ProviderCapability,
)

__all__ = [
    "AdjustType",
    "MarketBarResult",
    "MarketSnapshotResult",
    "AdjFactorResult",
    "MarketDataProvider",
]


class AdjustType(StrEnum):
    NONE = "NONE"            # 不复权（raw price）
    FORWARD = "FORWARD"      # 前复权
    BACKWARD = "BACKWARD"    # 后复权


class MarketBarResult(BaseModel):
    """对齐 TS-02 ohlcva/v1/ parquet schema（§4.2 + 冻结规范 §19）。"""

    instrument_id: UUID
    trade_date: date
    open: Decimal | None = None
    high: Decimal | None = None
    low: Decimal | None = None
    close: Decimal | None = None
    volume: Decimal | None = None
    amount: Decimal | None = None
    pre_close: Decimal | None = None
    pct_change: Decimal | None = None
    turnover_rate: Decimal | None = None
    adj_factor: Decimal | None = None
    adjusted_close: Decimal | None = None
    currency: str = "CNY"
    provider: str                                  # 实际取数 provider（fallback 时=actual）
    source_timestamp: datetime | None = None       # Provider 时间戳
    provenance: ProvenanceEnvelope


class MarketSnapshotResult(BaseModel):
    """get_market_snapshot 返回 as_of 时点可得的最新已完成交易日行情。"""

    instrument_id: UUID
    as_of: date
    trade_date: date | None
    close: Decimal | None = None
    pct_change: Decimal | None = None
    volume: Decimal | None = None
    amount: Decimal | None = None
    currency: str = "CNY"
    provenance: ProvenanceEnvelope


class AdjFactorResult(BaseModel):
    instrument_id: UUID
    trade_date: date
    adj_factor: Decimal
    provenance: ProvenanceEnvelope


class MarketDataProvider(BaseProvider):
    capabilities = frozenset(
        {
            ProviderCapability.CN_DAILY_QUOTE,
            ProviderCapability.CN_ETF_QUOTE,
            ProviderCapability.ADJ_FACTOR,
        }
    )

    @abc.abstractmethod
    async def get_price_history(
        self,
        instrument_id: UUID,
        start: date,
        end: date,
        adjust: AdjustType = AdjustType.NONE,
    ) -> list[MarketBarResult]:
        """日线 OHLCVA。instrument_id 由调用方（Data Gateway）先经 provider_symbols 解析。

        返回空列表 = 该区间无数据（合法缺口），不抛异常。
        """

    @abc.abstractmethod
    async def get_market_snapshot(
        self,
        instrument_ids: list[UUID],
        as_of: date,
    ) -> list[MarketSnapshotResult]:
        """时点快照：as_of 及之前最近已完成交易日行情。"""

    @abc.abstractmethod
    async def get_adj_factors(
        self,
        instrument_id: UUID,
        start: date,
        end: date,
    ) -> list[AdjFactorResult]:
        """复权因子序列。落库由 corporate_actions 模块负责（冻结规范 §20），
        provider 只提供原始因子。"""
