# =====================================================================
# backend/app/providers/contracts/macro.py —— 冻结：MacroProvider（TS-05 §2.5）
#
# 数据范围：
# - 指数点位：美股指数（^GSPC / ^NDX，仅 INDEX 标的，不抓 SPY/VOO/QQQ）
#   与 A 股指数；
# - FX：USD/CNY（仅 QDII 分析使用）；
# - 指数估值：Index PE/PB 历史（供 ETF Engine 历史分位计算）；
# - 宏观序列：FRED（利率、CPI 等，v0.1 可选支持）。
#
# Spike 回流（2026-08-23 实测，ADR-005/006）：
# - INDEX_VALUATION 首选来源 = 乐咕乐股（legulegu），免自聚合；
# - FX primary = yahoo（USDCNY=X），fred（DEXCHUS）作交叉验证；
# - MACRO_SERIES primary = fred。
# =====================================================================
from __future__ import annotations

import abc
from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel

from app.providers.contracts.base import (
    BaseProvider,
    ProvenanceEnvelope,
    ProviderCapability,
)

__all__ = [
    "IndexBarResult",
    "FxRateResult",
    "IndexValuationResult",
    "MacroProvider",
]


class IndexBarResult(BaseModel):
    """指数点位。index_id 是 Instrument(INDEX) 的 instrument_id（TS-01 §5 刻意选择）。"""

    index_id: UUID
    trade_date: date
    open: Decimal | None = None
    high: Decimal | None = None
    low: Decimal | None = None
    close: Decimal | None = None
    volume: Decimal | None = None
    currency: str = "USD"          # ^GSPC/^NDX 为 USD
    provenance: ProvenanceEnvelope


class FxRateResult(BaseModel):
    """对齐 TS-02 fx_observations（§4.7）。"""

    base_currency: str = "USD"
    quote_currency: str = "CNY"
    rate: Decimal                  # 1 base = rate quote
    as_of: datetime
    trade_date: date | None = None
    provenance: ProvenanceEnvelope


class IndexValuationResult(BaseModel):
    """Index PE/PB 历史（Level 2 输入）。来源 = legulegu（S6 实测锁定，ADR-006）。"""

    index_id: UUID
    as_of_date: date
    pe: Decimal | None = None
    pb: Decimal | None = None
    source: str                    # 具体序列来源（spike 后写入 matrix）
    provenance: ProvenanceEnvelope


class MacroProvider(BaseProvider):
    capabilities = frozenset(
        {
            ProviderCapability.INDEX_QUOTE,
            ProviderCapability.INDEX_VALUATION,
            ProviderCapability.FX_RATES,
            ProviderCapability.MACRO_SERIES,
        }
    )

    @abc.abstractmethod
    async def get_index_history(
        self,
        index_id: UUID,
        start: date,
        end: date,
    ) -> list[IndexBarResult]:
        """指数点位历史。"""

    @abc.abstractmethod
    async def get_fx_rates(
        self,
        base_currency: str,
        quote_currency: str,
        start: date,
        end: date,
    ) -> list[FxRateResult]:
        """汇率观察序列（v0.1 至少 USD/CNY）。"""

    @abc.abstractmethod
    async def get_index_valuation(
        self,
        index_id: UUID,
        start: date,
        end: date,
    ) -> list[IndexValuationResult]:
        """Index PE/PB 历史。来源 = legulegu（S6 实测冻结）；禁止伪造历史分位。"""
