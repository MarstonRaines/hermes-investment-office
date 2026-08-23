# =====================================================================
# backend/app/providers/contracts/fundamentals.py —— 冻结：FundamentalProvider（TS-05 §2.2）
#
# 数据范围：三大报表（利润表 / 资产负债表 / 现金流量表）与财务指标，
# metric_code 使用 TS-02 §4.3 冻结清单。兼容中国 GAAP；
# 避免固化任何 Provider schema（冻结规范 §16）。
#
# 口径规则（冻结）：同一 period_end 的多次披露（重述/更正）是多个 observation
# （唯一约束 (instrument_id, metric_code, period_end, statement_type, published_at, provider)），
# is_restated 标记重述；禁止覆盖旧值。
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
from app.providers.contracts.filings import FilingMeta

__all__ = [
    "FinancialFactResult",
    "FundamentalProvider",
]


class FinancialFactResult(BaseModel):
    """对齐 TS-02 financial_facts（§4.3）。

    单位四元组必填（冻结规范 §14.2）：original_value / original_unit /
    value（归一化，base_unit=CNY）/ unit。
    """

    instrument_id: UUID
    metric_code: str                 # TS-02 冻结清单
    period_start: date | None = None
    period_end: date                 # 报告期止（PIT 语义之一）
    period_type: str | None = None   # Q1 / H1 / Q3 / FY
    statement_type: str              # INCOME / BALANCE / CASH_FLOW / OTHER
    report_date: date | None = None
    published_at: datetime | None = None   # 正式披露时点（PIT 关键）
    retrieved_at: datetime
    original_value: Decimal | None = None  # 原始值（单位归一化四元组）
    original_unit: str | None = None       # 元 / 万元 / 亿元
    value: Decimal                          # 归一化值（base_unit=CNY）
    currency: str = "CNY"
    unit: str = "CNY"                       # 归一化单位
    is_restated: bool = False
    source_document_id: str | None = None   # 原始文档标识
    provenance: ProvenanceEnvelope


class FundamentalProvider(BaseProvider):
    capabilities = frozenset({ProviderCapability.FINANCIAL_STATEMENTS})

    @abc.abstractmethod
    async def get_financial_facts(
        self,
        instrument_id: UUID,
        metrics: list[str],
        periods: list[date],          # period_end 列表
    ) -> list[FinancialFactResult]:
        """按 (metric, period_end) 拉取标准化财务事实。"""

    @abc.abstractmethod
    async def get_financial_history(
        self,
        instrument_id: UUID,
        metrics: list[str],
        start_period: date,
        end_period: date,
    ) -> list[FinancialFactResult]:
        """财务时间序列（供 financial_history/v1/ parquet 全量构建）。"""

    @abc.abstractmethod
    async def get_latest_filings(
        self,
        instrument_id: UUID,
    ) -> list[FilingMeta]:
        """最新定期报告（年报/半年报/季报）元数据，用于驱动财务同步增量；
        完整公告列表与文档下载见 FilingProvider。"""
