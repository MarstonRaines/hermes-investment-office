# =====================================================================
# backend/app/providers/akshare/fundamentals.py —— AkShareThsFundamentalProvider
#
# S2 实测：同花顺财务摘要 stock_financial_abstract_ths（直连正常）作财务
# fallback。THS 摘要是"按报告期"的横截面（行=报告期，列=指标名），单位=元。
# 列名映射为 best-effort（爬虫字段漂移常见）；无法映射的指标跳过（合法缺口）。
# =====================================================================
from __future__ import annotations

import asyncio
from datetime import date, datetime
from decimal import Decimal
from typing import ClassVar
from uuid import UUID

from app.providers.common import pct_or_none
from app.providers.contracts.base import (
    ProvenanceEnvelope,
    ProviderCapability,
    ProviderConfigError,
    ProviderDataError,
    ProviderHealth,
    ProviderQualityReport,
    ProviderRole,
    QualityTier,
)
from app.providers.contracts.filings import FilingMeta
from app.providers.contracts.fundamentals import FinancialFactResult, FundamentalProvider

__all__ = ["AkShareThsFundamentalProvider"]

try:
    import akshare as ak
except ImportError:  # pragma: no cover
    ak = None  # type: ignore[assignment]

# THS 摘要列名（中文）→ metric_code（best-effort 映射）
_THS_COLUMN_MAP = {
    "营业收入": "REVENUE",
    "营业总收入": "REVENUE",
    "归母净利润": "NET_INCOME",
    "净利润": "NET_INCOME",
    "归属母公司股东的净利润": "NET_INCOME",
    "经营活动产生的现金流量净额": "OPERATING_CASH_FLOW",
    "总资产": "TOTAL_ASSETS",
    "资产总计": "TOTAL_ASSETS",
    "总负债": "TOTAL_LIABILITIES",
    "负债合计": "TOTAL_LIABILITIES",
    "股东权益": "TOTAL_EQUITY",
    "所有者权益合计": "TOTAL_EQUITY",
    "货币资金": "CASH",
    "营业利润": "OPERATING_INCOME",
}
_STATEMENT_OF = {
    "REVENUE": "INCOME",
    "NET_INCOME": "INCOME",
    "OPERATING_INCOME": "INCOME",
    "OPERATING_CASH_FLOW": "CASH_FLOW",
    "TOTAL_ASSETS": "BALANCE",
    "TOTAL_LIABILITIES": "BALANCE",
    "TOTAL_EQUITY": "BALANCE",
    "CASH": "BALANCE",
}


def _period_type_of(end: date) -> str:
    return {"(3, 31)": "Q1", "(6, 30)": "H1", "(9, 30)": "Q3"}.get(str((end.month, end.day)), "FY")


class AkShareThsFundamentalProvider(FundamentalProvider):
    provider_name: ClassVar[str] = "akshare_ths"
    display_name: ClassVar[str] = "AkShare（同花顺源）"
    capabilities = frozenset({ProviderCapability.FINANCIAL_STATEMENTS})
    default_role = ProviderRole.FALLBACK
    quality_tier = QualityTier.TIER_3
    known_limits = [
        "财务摘要列名漂移风险高（S2 实测），映射为 best-effort",
        "无披露时点（published_at=None）：PIT 可见性按 retrieved_at 近似，quality_status=ACCEPTABLE",
    ]

    TRANSFORM_VERSION = "fundamental-normalizer/0.1.0"

    def __init__(self, config=None, symbol_resolver=None) -> None:
        self.config = config
        self._resolve = symbol_resolver

    async def health_check(self) -> ProviderHealth:
        return ProviderHealth(provider=self.provider_name, status="HEALTHY", checked_at=datetime.now())

    async def quality_report(self) -> ProviderQualityReport:
        return ProviderQualityReport(
            provider=self.provider_name, tier=self.quality_tier, quality_score=Decimal("0.80"),
        )

    def _symbol(self, instrument_id: UUID) -> str:
        if self._resolve is None:
            raise ProviderConfigError("akshare_ths: symbol_resolver 未注入")
        symbol = self._resolve(instrument_id)
        if not symbol:
            raise ProviderConfigError(f"akshare_ths: instrument {instrument_id} 无 symbol 映射")
        return symbol.split(".")[0]   # THS 用纯数字代码

    async def get_financial_facts(
        self, instrument_id: UUID, metrics: list[str], periods: list[date],
    ) -> list[FinancialFactResult]:
        if ak is None:
            raise ProviderConfigError("akshare 未安装")
        symbol = self._symbol(instrument_id)
        df = await asyncio.to_thread(ak.stock_financial_abstract_ths, symbol=symbol, indicator="按报告期")
        if df is None or df.empty:
            return []
        want = set(metrics)
        # 报告期列名：'报告期'（akshare 新版本）或 index 名
        period_col = "报告期" if "报告期" in df.columns else df.columns[0]
        out: list[FinancialFactResult] = []
        for _, r in df.iterrows():
            period_str = str(r[period_col])[:10]
            try:
                period_end = date.fromisoformat(period_str)
            except ValueError:
                continue
            if period_end not in periods:
                continue
            for col, metric in _THS_COLUMN_MAP.items():
                if metric not in want or col not in df.columns:
                    continue
                value = pct_or_none(r.get(col))
                if value is None:
                    continue
                out.append(FinancialFactResult(
                    instrument_id=instrument_id, metric_code=metric,
                    period_end=period_end, period_type=_period_type_of(period_end),
                    statement_type=_STATEMENT_OF.get(metric, "OTHER"),
                    published_at=None, retrieved_at=datetime.now(),
                    original_value=Decimal(str(value)), original_unit="元",
                    value=Decimal(str(value)), unit="CNY",
                    provenance=ProvenanceEnvelope(
                        source="cn_financial_statements", provider="akshare_ths",
                        source_record_id=f"akshare_ths@{symbol}@{period_end.isoformat()}",
                        observed_at=datetime.now(), retrieved_at=datetime.now(),
                        as_of_date=period_end, quality_score=Decimal("0.80"),
                        quality_status="ACCEPTABLE",
                        quality_flags=["NO_PUBLISHED_AT"], transform_version=self.TRANSFORM_VERSION,
                    ),
                ))
        return out

    async def get_financial_history(
        self, instrument_id: UUID, metrics: list[str], start_period: date, end_period: date,
    ) -> list[FinancialFactResult]:
        periods: list[date] = []
        year = start_period.year
        while year <= end_period.year:
            for md in ("0331", "0630", "0930", "1231"):
                p = date(year, int(md[0:2]), int(md[2:4]))
                if start_period <= p <= end_period:
                    periods.append(p)
            year += 1
        return await self.get_financial_facts(instrument_id, metrics, periods)

    async def get_latest_filings(self, instrument_id: UUID) -> list[FilingMeta]:
        raise ProviderDataError("akshare_ths 不提供公告元数据（走 tushare primary）")
