# =====================================================================
# backend/app/providers/tushare/fundamentals.py —— TuShareFundamentalProvider
#
# S1/S5 实测：三大报表（income/balancesheet/cashflow）+ fina_indicator 2000
# 积分档可用，数值单位恒为元（base_unit=CNY 直接映射，无需四元组转换）。
#
# metric_code 映射（TS-02 §4.3 冻结清单；TuShare 合并报表口径 report_type='1'）：
#   REVENUE             ← income.total_revenue
#   GROSS_PROFIT        ← total_revenue - total_cogs（缺字段则跳过）
#   OPERATING_INCOME    ← income.operate_profit
#   NET_INCOME          ← income.n_income_attr_p（归母）
#   OPERATING_CASH_FLOW ← cashflow.n_cashflow_oper
#   CAPEX               ← cashflow.c_pay_acq_const_fiolta
#   TOTAL_ASSETS        ← balancesheet.total_assets
#   TOTAL_LIABILITIES   ← balancesheet.total_liab
#   TOTAL_EQUITY        ← balancesheet.total_hldr_eqy_exc_min_int（归母）
#   CASH                ← balancesheet.money_cap
#   DEBT                ← short_loan + long_loan + bond_payable（有息负债，缺字段则跳过）
#   SHARES_OUTSTANDING  ← fina_indicator.total_share（缺字段则跳过）
#   FREE_CASH_FLOW      引擎计算（M3），Provider 不产出
#
# 重述语义：同一 period_end 多次披露 = 多 observation（DB 唯一约束含 published_at），
# is_restated 由 normalizer 判定（M1.4），Provider 一律 False。
# =====================================================================
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import ClassVar
from uuid import UUID

from app.providers.common import pct_or_none
from app.providers.contracts.base import (
    ProvenanceEnvelope,
    ProviderCapability,
    ProviderConfigError,
    ProviderHealth,
    ProviderQualityReport,
    ProviderRole,
    QualityTier,
)
from app.providers.contracts.filings import FilingMeta
from app.providers.contracts.fundamentals import FinancialFactResult, FundamentalProvider
from app.providers.tushare.client import TushareClient
from app.providers.tushare.metadata import TuShareMeta

__all__ = ["TuShareFundamentalProvider"]

_INCOME_MAP = {
    "REVENUE": ("total_revenue",),
    "OPERATING_INCOME": ("operate_profit",),
    "NET_INCOME": ("n_income_attr_p",),
}
_BALANCE_MAP = {
    "TOTAL_ASSETS": ("total_assets",),
    "TOTAL_LIABILITIES": ("total_liab",),
    "TOTAL_EQUITY": ("total_hldr_eqy_exc_min_int",),
    "CASH": ("money_cap",),
}
_CASHFLOW_MAP = {
    "OPERATING_CASH_FLOW": ("n_cashflow_oper",),
    "CAPEX": ("c_pay_acq_const_fiolta",),
}
_DEBT_COMPONENTS = ("short_loan", "long_loan", "bond_payable")


def _period_type_of(end: date) -> str:
    md = (end.month, end.day)
    return {"(3, 31)": "Q1", "(6, 30)": "H1", "(9, 30)": "Q3", "(12, 31)": "FY"}.get(str(md), "FY")


def _as_dt(iso_day: str) -> datetime | None:
    """'YYYYMMDD' → 当日 00:00 Asia/Shanghai → UTC（披露日粒度，PIT 以日计）。"""
    try:
        d = date(int(iso_day[0:4]), int(iso_day[4:6]), int(iso_day[6:8]))
    except (ValueError, IndexError):
        return None
    return datetime(d.year, d.month, d.day, tzinfo=__import__("zoneinfo").ZoneInfo("Asia/Shanghai"))


class TuShareFundamentalProvider(FundamentalProvider):
    provider_name: ClassVar[str] = "tushare"
    display_name: ClassVar[str] = "TuShare Pro"
    capabilities = frozenset({ProviderCapability.FINANCIAL_STATEMENTS})
    default_role = ProviderRole.PRIMARY
    quality_tier = QualityTier.TIER_2
    known_limits = TuShareMeta.known_limits

    TRANSFORM_VERSION = "fundamental-normalizer/0.1.0"

    def __init__(self, config=None, symbol_resolver=None, client: TushareClient | None = None) -> None:
        self.config = config
        self._resolve = symbol_resolver
        if client is not None:
            self._client = client
        else:
            token = (config.token if config else None) or ""
            self._client = TushareClient(token, timeout=getattr(config, "timeout_seconds", 20.0)) if token else None

    async def health_check(self) -> ProviderHealth:
        if self._client is None:
            raise ProviderConfigError("tushare token 未配置（HERMES_TUSHARE_TOKEN）")
        return ProviderHealth(
            provider=self.provider_name, status="HEALTHY", checked_at=datetime.now(),
        )

    async def quality_report(self) -> ProviderQualityReport:
        return ProviderQualityReport(
            provider=self.provider_name, tier=self.quality_tier,
            quality_score=Decimal(str(TuShareMeta.quality_score)),
        )

    def _symbol(self, instrument_id: UUID) -> str:
        if self._resolve is None:
            raise ProviderConfigError("tushare: symbol_resolver 未注入")
        symbol = self._resolve(instrument_id)
        if not symbol:
            raise ProviderConfigError(f"tushare: instrument {instrument_id} 无 symbol 映射")
        return symbol

    async def get_financial_facts(
        self,
        instrument_id: UUID,
        metrics: list[str],
        periods: list[date],
    ) -> list[FinancialFactResult]:
        if self._client is None:
            raise ProviderConfigError("tushare token 未配置")
        symbol = self._symbol(instrument_id)
        results: list[FinancialFactResult] = []
        for period in periods:
            period_s = period.strftime("%Y%m%d")
            rows = await self._fetch_period(symbol, instrument_id, period, period_s, metrics)
            results.extend(rows)
        return results

    async def get_financial_history(
        self,
        instrument_id: UUID,
        metrics: list[str],
        start_period: date,
        end_period: date,
    ) -> list[FinancialFactResult]:
        """财务时间序列：枚举 [start_period, end_period] 内每年的 4 个报告期。"""
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
        """最新定期报告元数据（以 income 最新 ann_date 为准，best-effort）。"""
        if self._client is None:
            raise ProviderConfigError("tushare token 未配置")
        symbol = self._symbol(instrument_id)
        df = await self._client.call("income", ts_code=symbol, report_type="1")
        if df is None or df.empty:
            return []
        row = df.iloc[0]
        ann = row.get("ann_date")
        end = row.get("end_date")
        if not ann or not end:
            return []
        from app.providers.common import as_utc

        return [FilingMeta(
            document_id=f"tushare:{symbol}:{end}",
            instrument_id=instrument_id,
            title=f"定期报告 {end}",
            document_type="ANNUAL" if str(end).endswith("1231") else "QUARTERLY",
            publish_date=date(int(str(ann)[0:4]), int(str(ann)[4:6]), int(str(ann)[6:8])),
            source="cn_financial_statements", provider="tushare",
            retrieved_at=as_utc(datetime.now()),
        )]

    # ---- 内部 ----

    async def _fetch_period(
        self, symbol: str, instrument_id: UUID, period: date, period_s: str, metrics: list[str]
    ) -> list[FinancialFactResult]:
        """拉取单期三大报表 + fina_indicator，映射为 FinancialFactResult 列表。"""
        want = set(metrics)
        facts: dict[str, FinancialFactResult] = {}

        def add_fact(metric: str, statement_type: str, value: Decimal | None,
                     ann_date: str | None) -> None:
            if metric not in want or value is None:
                return
            published_at = _as_dt(ann_date) if ann_date else None
            facts[metric] = FinancialFactResult(
                instrument_id=instrument_id,
                metric_code=metric, period_end=period,
                period_type=_period_type_of(period), statement_type=statement_type,
                published_at=published_at, retrieved_at=datetime.now(),
                original_value=value, original_unit="元",
                value=value, unit="CNY",
                provenance=ProvenanceEnvelope(
                    source="cn_financial_statements", provider="tushare",
                    source_record_id=f"{symbol}@{period.isoformat()}@{(published_at.date() if published_at else '')}",
                    published_at=published_at,
                    observed_at=datetime(period.year, period.month, period.day, tzinfo=__import__("zoneinfo").ZoneInfo("Asia/Shanghai")),
                    retrieved_at=datetime.now(), as_of_date=period,
                    quality_score=Decimal(str(TuShareMeta.quality_score)),
                    quality_status="VERIFIED", transform_version=self.TRANSFORM_VERSION,
                ),
            )

        if any(m in _INCOME_MAP or m in ("GROSS_PROFIT",) for m in want):
            df = await self._client.call("income", ts_code=symbol, period=period_s, report_type="1")
            if df is not None and not df.empty:
                row = df.iloc[0]
                ctx["instrument_id"] = row.get("ts_code")
                ann = row.get("ann_date") or row.get("f_ann_date")
                for metric, cols in _INCOME_MAP.items():
                    add_fact(metric, "INCOME", _val(row, cols), ann)
                tr = _val(row, ("total_revenue",))
                tc = _val(row, ("total_cogs",))
                if "GROSS_PROFIT" in want and tr is not None and tc is not None:
                    add_fact("GROSS_PROFIT", "INCOME", tr - tc, ann)

        if any(m in _BALANCE_MAP or m == "DEBT" for m in want):
            df = await self._client.call("balancesheet", ts_code=symbol, period=period_s, report_type="1")
            if df is not None and not df.empty:
                row = df.iloc[0]
                ann = row.get("ann_date") or row.get("f_ann_date")
                for metric, cols in _BALANCE_MAP.items():
                    add_fact(metric, "BALANCE", _val(row, cols), ann)
                if "DEBT" in want:
                    comps = [_val(row, (c,)) for c in _DEBT_COMPONENTS]
                    if all(c is not None for c in comps):
                        add_fact("DEBT", "BALANCE", sum(comps, Decimal("0")), ann)

        if any(m in _CASHFLOW_MAP for m in want):
            df = await self._client.call("cashflow", ts_code=symbol, period=period_s, report_type="1")
            if df is not None and not df.empty:
                row = df.iloc[0]
                ann = row.get("ann_date") or row.get("f_ann_date")
                for metric, cols in _CASHFLOW_MAP.items():
                    add_fact(metric, "CASH_FLOW", _val(row, cols), ann)

        if "SHARES_OUTSTANDING" in want:
            df = await self._client.call("fina_indicator", ts_code=symbol, period=period_s)
            if df is not None and not df.empty:
                row = df.iloc[0]
                ann = row.get("ann_date")
                add_fact("SHARES_OUTSTANDING", "OTHER", _val(row, ("total_share",)), ann)

        return list(facts.values())


def _val(row, cols: tuple[str, ...]) -> Decimal | None:
    for c in cols:
        if c in row and row[c] is not None:
            f = pct_or_none(row[c])
            if f is not None:
                return Decimal(str(f))
    return None
