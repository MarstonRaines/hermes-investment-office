# =====================================================================
# backend/app/providers/tushare/provider.py —— TuShareProvider（唯一实现类）
#
# 冻结契约对齐（TS-05 §3.1）："一个 provider 包可以承载多个接口实现（如
# tushare/ 同时实现 MarketDataProvider 与 FundamentalProvider），但每个实现类
# 的 provider_name 唯一"——本包以单一 TuShareProvider 同时实现
# MarketDataProvider / FundamentalProvider / ETFProvider / MacroProvider，
# provider_name='tushare'（矩阵、provenance、provider_symbols 共用该身份）。
# （冻结目录示例拆分为 market.py/fundamentals.py 分文件；合并为实现"每类
# 唯一名"的最小改动，client.py/metadata.py 保持独立。）
#
# S1/S5 实测（2026-08-23，2000 积分档）：
# - daily（A 股日线）、fund_daily（ETF 行情——ETF 不走 daily）、adj_factor、
#   income/balancesheet/cashflow/fina_indicator（单位恒为元）、fund_nav
#   （nav_date/ann_date 双日期）、index_daily（A 股指数）
# =====================================================================
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import ClassVar
from uuid import UUID

from app.providers.common import pct_or_none, shanghai_15
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
from app.providers.contracts.etf import ETFProvider, NavResult
from app.providers.contracts.filings import FilingMeta
from app.providers.contracts.fundamentals import FinancialFactResult, FundamentalProvider
from app.providers.contracts.macro import (
    FxRateResult,
    IndexBarResult,
    IndexValuationResult,
    MacroProvider,
)
from app.providers.contracts.market_data import (
    AdjFactorResult,
    AdjustType,
    MarketBarResult,
    MarketDataProvider,
    MarketSnapshotResult,
)
from app.providers.tushare.client import TushareClient
from app.providers.tushare.metadata import TuShareMeta

__all__ = ["TuShareProvider"]

TRANSFORM_VERSION = "market-normalizer/0.1.0"
FUND_TRANSFORM_VERSION = "fund-nav-normalizer/0.1.0"
FIN_TRANSFORM_VERSION = "fundamental-normalizer/0.1.0"

# metric_code 映射（TS-02 §4.3 冻结清单；TuShare 合并报表口径 report_type='1'）
_INCOME_MAP = {
    "REVENUE": ("total_revenue",),
    "OPERATING_INCOME": ("operate_profit",),
    "NET_INCOME": ("n_income_attr_p",),     # 归母
}
_BALANCE_MAP = {
    "TOTAL_ASSETS": ("total_assets",),
    "TOTAL_LIABILITIES": ("total_liab",),
    "TOTAL_EQUITY": ("total_hldr_eqy_exc_min_int",),   # 归母
    "CASH": ("money_cap",),
}
_CASHFLOW_MAP = {
    "OPERATING_CASH_FLOW": ("n_cashflow_oper",),
    "CAPEX": ("c_pay_acq_const_fiolta",),
}
_DEBT_COMPONENTS = ("short_loan", "long_loan", "bond_payable")


def _period_type_of(end: date) -> str:
    return {
        (3, 31): "Q1", (6, 30): "H1", (9, 30): "Q3", (12, 31): "FY",
    }.get((end.month, end.day), "FY")


def _dec(v) -> Decimal | None:
    f = pct_or_none(v)
    return Decimal(str(f)) if f is not None else None


def _col(row, *names) -> Decimal | None:
    """行内按候选列名取数值（防御式）。"""
    for n in names:
        if n in row and row[n] is not None:
            f = pct_or_none(row[n])
            if f is not None:
                return Decimal(str(f))
    return None


class TuShareProvider(MarketDataProvider, FundamentalProvider, ETFProvider, MacroProvider):
    provider_name: ClassVar[str] = "tushare"
    display_name: ClassVar[str] = "TuShare Pro"
    capabilities = frozenset(
        {
            ProviderCapability.CN_DAILY_QUOTE,
            ProviderCapability.CN_ETF_QUOTE,
            ProviderCapability.ADJ_FACTOR,
            ProviderCapability.FINANCIAL_STATEMENTS,
            ProviderCapability.FUND_NAV,
            ProviderCapability.INDEX_QUOTE,        # A 股指数（auxiliary，ADR-006）
        }
    )
    default_role = ProviderRole.PRIMARY
    quality_tier = QualityTier.TIER_2
    known_limits = TuShareMeta.known_limits

    def __init__(self, config=None, symbol_resolver=None, client: TushareClient | None = None) -> None:
        self.config = config
        self._resolve = symbol_resolver
        if client is not None:
            self._client = client
        else:
            token = (config.token if config else None) or ""
            self._client = TushareClient(token, timeout=getattr(config, "timeout_seconds", 20.0)) if token else None

    # ---- BaseProvider ----

    async def health_check(self) -> ProviderHealth:
        if self._client is None:
            raise ProviderConfigError("tushare token 未配置（HERMES_TUSHARE_TOKEN）")
        return ProviderHealth(
            provider=self.provider_name, status="HEALTHY",
            checked_at=datetime.now(), detail={"score": "2000"},
        )

    async def quality_report(self) -> ProviderQualityReport:
        return ProviderQualityReport(
            provider=self.provider_name, tier=self.quality_tier,
            quality_score=Decimal(str(TuShareMeta.quality_score)),
        )

    # ---- 内部 ----

    def _symbol(self, instrument_id: UUID) -> str:
        if self._resolve is None:
            raise ProviderConfigError("tushare: symbol_resolver 未注入")
        symbol = self._resolve(instrument_id)
        if not symbol:
            raise ProviderConfigError(f"tushare: instrument {instrument_id} 无 symbol 映射")
        return symbol

    @staticmethod
    def _bar_from_row(row, instrument_id: UUID) -> MarketBarResult:
        """TuShare 行情行 → MarketBarResult（vol 手→股 ×100；amount 千元→元 ×1000）。"""
        trade_date = date.fromisoformat(str(row["trade_date"]))
        vol_raw = pct_or_none(row.get("vol"))
        amount_raw = pct_or_none(row.get("amount"))
        return MarketBarResult(
            instrument_id=instrument_id,
            trade_date=trade_date,
            open=_dec(row.get("open")),
            high=_dec(row.get("high")),
            low=_dec(row.get("low")),
            close=_dec(row.get("close")),
            volume=Decimal(str(vol_raw * 100)) if vol_raw is not None else None,      # 手→股
            amount=Decimal(str(amount_raw * 1000)) if amount_raw is not None else None,  # 千元→元
            pre_close=_dec(row.get("pre_close")),
            pct_change=_dec(row.get("pct_chg")),
            currency="CNY",
            provider="tushare",
            provenance=ProvenanceEnvelope(
                source="cn_daily_market", provider="tushare",
                source_record_id=f"tushare@{trade_date.isoformat()}",
                observed_at=shanghai_15(trade_date),
                retrieved_at=datetime.now(),
                as_of_date=trade_date,
                quality_score=Decimal(str(TuShareMeta.quality_score)),
                quality_status="VERIFIED",
                transform_version=TRANSFORM_VERSION,
            ),
        )

    def _env(self, source: str, td: date, *, published_at=None, quality: str = "VERIFIED",
             score: str | None = None, transform: str = TRANSFORM_VERSION,
             flags: list[str] | None = None) -> ProvenanceEnvelope:
        return ProvenanceEnvelope(
            source=source, provider="tushare",
            source_record_id=f"tushare@{td.isoformat()}",
            published_at=published_at,
            observed_at=shanghai_15(td), retrieved_at=datetime.now(),
            as_of_date=td,
            quality_score=Decimal(score or str(TuShareMeta.quality_score)),
            quality_status=quality, quality_flags=flags or [],
            transform_version=transform,
        )

    # ---- MarketDataProvider ----

    async def get_price_history(
        self,
        instrument_id: UUID,
        start: date,
        end: date,
        adjust: AdjustType = AdjustType.NONE,
    ) -> list[MarketBarResult]:
        if self._client is None:
            raise ProviderConfigError("tushare token 未配置")
        symbol = self._symbol(instrument_id)
        start_s, end_s = start.strftime("%Y%m%d"), end.strftime("%Y%m%d")
        # S1 关键发现：ETF 行情必须走 fund_daily，daily 对 ETF 返回空。
        # 同 provider 内先 daily 后 fund_daily 属取数口径选择（非换源，TS-05 §5.3 合规）。
        df = await self._client.call("daily", ts_code=symbol, start_date=start_s, end_date=end_s)
        source = "cn_daily_market"
        if df is None or df.empty:
            df = await self._client.call("fund_daily", ts_code=symbol, start_date=start_s, end_date=end_s)
            source = "cn_etf_daily_market"
        if df is None or df.empty:
            return []
        bars = [self._bar_from_row(r, instrument_id) for _, r in df.iterrows()]
        for b in bars:
            b.provenance.source = source
        if adjust != AdjustType.NONE:
            factors = {f.trade_date: f.adj_factor for f in await self.get_adj_factors(instrument_id, start, end)}
            for b in bars:
                if b.trade_date in factors:
                    b.adj_factor = factors[b.trade_date]
                    b.adjusted_close = (b.close * b.adj_factor) if b.close is not None else None
                    if "ADJUSTED" not in b.provenance.quality_flags:
                        b.provenance.quality_flags.append("ADJUSTED")
        return bars

    async def get_market_snapshot(
        self,
        instrument_ids: list[UUID],
        as_of: date,
    ) -> list[MarketSnapshotResult]:
        from datetime import timedelta

        results: list[MarketSnapshotResult] = []
        window_start = as_of - timedelta(days=45)
        for inst in instrument_ids:
            bars = await self.get_price_history(inst, window_start, as_of)
            if not bars:
                results.append(MarketSnapshotResult(
                    instrument_id=inst, as_of=as_of, trade_date=None,
                    provenance=self._env("cn_daily_market", as_of, quality="STALE",
                                         score="0.0", flags=["NO_BAR"]),
                ))
                continue
            last = bars[-1]
            results.append(MarketSnapshotResult(
                instrument_id=inst, as_of=as_of, trade_date=last.trade_date,
                close=last.close, pct_change=last.pct_change,
                volume=last.volume, amount=last.amount, provenance=last.provenance,
            ))
        return results

    async def get_adj_factors(
        self,
        instrument_id: UUID,
        start: date,
        end: date,
    ) -> list[AdjFactorResult]:
        if self._client is None:
            raise ProviderConfigError("tushare token 未配置")
        symbol = self._symbol(instrument_id)
        df = await self._client.call(
            "adj_factor", ts_code=symbol,
            start_date=start.strftime("%Y%m%d"), end_date=end.strftime("%Y%m%d"),
        )
        if df is None or df.empty:
            return []
        out: list[AdjFactorResult] = []
        for _, r in df.iterrows():
            td = date.fromisoformat(str(r["trade_date"]))
            out.append(AdjFactorResult(
                instrument_id=instrument_id, trade_date=td,
                adj_factor=Decimal(str(r["adj_factor"])),
                provenance=self._env("cn_adj_factor", td),
            ))
        return out

    # ---- FundamentalProvider ----

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
            results.extend(await self._fetch_period(symbol, instrument_id, period, set(metrics)))
        return results

    async def get_financial_history(
        self,
        instrument_id: UUID,
        metrics: list[str],
        start_period: date,
        end_period: date,
    ) -> list[FinancialFactResult]:
        periods: list[date] = []
        for year in range(start_period.year, end_period.year + 1):
            for md in ("0331", "0630", "0930", "1231"):
                p = date(year, int(md[0:2]), int(md[2:4]))
                if start_period <= p <= end_period:
                    periods.append(p)
        return await self.get_financial_facts(instrument_id, metrics, periods)

    async def get_latest_filings(self, instrument_id: UUID) -> list[FilingMeta]:
        if self._client is None:
            raise ProviderConfigError("tushare token 未配置")
        symbol = self._symbol(instrument_id)
        df = await self._client.call("income", ts_code=symbol, report_type="1")
        if df is None or df.empty:
            return []
        row = df.iloc[0]
        ann, end = row.get("ann_date"), row.get("end_date")
        if not ann or not end:
            return []
        return [FilingMeta(
            document_id=f"tushare:{symbol}:{end}",
            instrument_id=instrument_id,
            title=f"定期报告 {end}",
            document_type="ANNUAL" if str(end).endswith("1231") else "QUARTERLY",
            publish_date=date(int(str(ann)[0:4]), int(str(ann)[4:6]), int(str(ann)[6:8])),
            source="cn_financial_statements", provider="tushare",
            retrieved_at=datetime.now(),
        )]

    async def _fetch_period(
        self, symbol: str, instrument_id: UUID, period: date, want: set[str],
    ) -> list[FinancialFactResult]:
        """拉取单期三大报表 + fina_indicator，映射为 FinancialFactResult 列表。"""
        from datetime import time
        from zoneinfo import ZoneInfo

        period_s = period.strftime("%Y%m%d")
        facts: dict[str, FinancialFactResult] = {}
        published: datetime | None = None

        def add_fact(metric: str, statement_type: str, value: Decimal | None, ann: str | None) -> None:
            nonlocal published
            if metric not in want or value is None:
                return
            if ann:
                try:
                    published = datetime(
                        int(ann[0:4]), int(ann[4:6]), int(ann[6:8]),
                        tzinfo=ZoneInfo("Asia/Shanghai"),
                    )
                except ValueError:
                    published = None
            facts[metric] = FinancialFactResult(
                instrument_id=instrument_id,
                metric_code=metric, period_end=period,
                period_type=_period_type_of(period), statement_type=statement_type,
                published_at=published, retrieved_at=datetime.now(),
                original_value=value, original_unit="元",
                value=value, unit="CNY",
                provenance=ProvenanceEnvelope(
                    source="cn_financial_statements", provider="tushare",
                    source_record_id=f"{symbol}@{period.isoformat()}@{(published.date() if published else '')}",
                    published_at=published,
                    observed_at=datetime.combine(period, time(0, 0), tzinfo=ZoneInfo("Asia/Shanghai")),
                    retrieved_at=datetime.now(), as_of_date=period,
                    quality_score=Decimal(str(TuShareMeta.quality_score)),
                    quality_status="VERIFIED", transform_version=FIN_TRANSFORM_VERSION,
                ),
            )

        if any(m in _INCOME_MAP or m == "GROSS_PROFIT" for m in want):
            df = await self._client.call("income", ts_code=symbol, period=period_s, report_type="1")
            if df is not None and not df.empty:
                row = df.iloc[0]
                ann = row.get("ann_date") or row.get("f_ann_date")
                for metric, cols in _INCOME_MAP.items():
                    add_fact(metric, "INCOME", _col(row, *cols), ann)
                tr, tc = _col(row, "total_revenue"), _col(row, "total_cogs")
                if "GROSS_PROFIT" in want and tr is not None and tc is not None:
                    add_fact("GROSS_PROFIT", "INCOME", tr - tc, ann)

        if any(m in _BALANCE_MAP or m == "DEBT" for m in want):
            df = await self._client.call("balancesheet", ts_code=symbol, period=period_s, report_type="1")
            if df is not None and not df.empty:
                row = df.iloc[0]
                ann = row.get("ann_date") or row.get("f_ann_date")
                for metric, cols in _BALANCE_MAP.items():
                    add_fact(metric, "BALANCE", _col(row, *cols), ann)
                if "DEBT" in want:
                    comps = [_col(row, c) for c in _DEBT_COMPONENTS]
                    if all(c is not None for c in comps):
                        add_fact("DEBT", "BALANCE", sum(comps, Decimal("0")), ann)

        if any(m in _CASHFLOW_MAP for m in want):
            df = await self._client.call("cashflow", ts_code=symbol, period=period_s, report_type="1")
            if df is not None and not df.empty:
                row = df.iloc[0]
                ann = row.get("ann_date") or row.get("f_ann_date")
                for metric, cols in _CASHFLOW_MAP.items():
                    add_fact(metric, "CASH_FLOW", _col(row, *cols), ann)

        if "SHARES_OUTSTANDING" in want:
            # 实测（2026-08-24）：fina_indicator 无股本列；daily_basic.total_share
            # 2000 积分档可用（单位万股）。取报告期止当日/之前最近一行，×10000 → 股。
            df = await self._client.call(
                "daily_basic", ts_code=symbol,
                start_date=f"{period.year}0101", end_date=period_s,
            )
            if df is not None and not df.empty:
                row = df.iloc[-1]     # ≤ period_end 的最近交易日
                total_share = _col(row, "total_share")
                if total_share is not None:
                    add_fact("SHARES_OUTSTANDING", "OTHER",
                             total_share * Decimal("10000"), period_s)

        return list(facts.values())

    # ---- ETFProvider（FUND_NAV）----

    async def get_nav_history(self, instrument_id: UUID) -> list[NavResult]:
        if self._client is None:
            raise ProviderConfigError("tushare token 未配置")
        symbol = self._symbol(instrument_id)
        df = await self._client.call("fund_nav", ts_code=symbol)
        if df is None or df.empty:
            return []
        out: list[NavResult] = []
        for _, r in df.iterrows():
            nav_date_raw, nav = r.get("nav_date"), pct_or_none(r.get("unit_nav"))
            if not nav_date_raw or nav is None:
                continue
            nd = date.fromisoformat(str(nav_date_raw))
            ann = r.get("ann_date")
            published_at = None
            if ann:
                try:
                    published_at = datetime(
                        int(ann[0:4]), int(ann[4:6]), int(ann[6:8]),
                        tzinfo=__import__("zoneinfo").ZoneInfo("Asia/Shanghai"),
                    )
                except ValueError:
                    published_at = None
            out.append(NavResult(
                instrument_id=instrument_id, nav_date=nd, nav=Decimal(str(nav)),
                published_at=published_at, retrieved_at=datetime.now(),
                provenance=self._env("cn_fund_nav", nd, published_at=published_at,
                                     transform=FUND_TRANSFORM_VERSION),
            ))
        return out

    async def get_holding_snapshots(self, instrument_id: UUID):
        raise ProviderDataError("tushare 不支持 FUND_HOLDINGS（akshare_eastmoney primary）")

    async def get_quota_status(self, instrument_id: UUID):
        raise ProviderDataError("tushare 不支持 QUOTA_STATUS（事件状态，人工/半自动录入）")

    # ---- MacroProvider（A 股指数，auxiliary）----

    async def get_index_history(
        self,
        index_id: UUID,
        start: date,
        end: date,
    ) -> list[IndexBarResult]:
        if self._client is None:
            raise ProviderConfigError("tushare token 未配置")
        symbol = self._symbol(index_id)
        df = await self._client.call(
            "index_daily", ts_code=symbol,
            start_date=start.strftime("%Y%m%d"), end_date=end.strftime("%Y%m%d"),
        )
        if df is None or df.empty:
            return []
        out: list[IndexBarResult] = []
        for _, r in df.iterrows():
            td = date.fromisoformat(str(r["trade_date"]))
            out.append(IndexBarResult(
                index_id=index_id, trade_date=td,
                open=_dec(r.get("open")), high=_dec(r.get("high")),
                low=_dec(r.get("low")), close=_dec(r.get("close")),
                volume=_dec(r.get("vol")), currency="CNY",
                provenance=self._env("cn_index_quote", td),
            ))
        return out

    # ---- 扩展 feed（六接口之外）：分红/送转（corporate_actions 输入）----

    async def get_dividends(self, instrument_id: UUID) -> list[dict]:
        """TuShare dividend 接口（2000 积分档实测可用，2026-08-24）。

        扩展方法（非六接口契约）：corporate_actions 同步 job 经 gateway.fetch_extension
        调用。返回已实施（含 ex_date）的分红/送转行。
        """
        if self._client is None:
            raise ProviderConfigError("tushare token 未配置")
        symbol = self._symbol(instrument_id)
        df = await self._client.call("dividend", ts_code=symbol)
        if df is None or df.empty:
            return []
        out = []
        for _, r in df.iterrows():
            ex_date = r.get("ex_date")
            if ex_date is None or str(ex_date) in ("nan", "None", ""):
                continue   # 仅已实施（除权除息日已知）的行动
            out.append({
                "announce_date": r.get("ann_date"),
                "ex_date": ex_date,
                "record_date": r.get("record_date"),
                "cash_div": r.get("cash_div"),      # 每 10 股现金（元）
                "stk_div": r.get("stk_div"),        # 每 10 股送股
                "stk_bo_rate": r.get("stk_bo_rate"),  # 每 10 股转增
                "div_proc": r.get("div_proc"),
            })
        return out

    async def get_fx_rates(
        self, base_currency: str, quote_currency: str, start: date, end: date,
    ) -> list[FxRateResult]:
        raise ProviderDataError("tushare 不支持 FX_RATES（yahoo primary / fred 交叉验证）")

    async def get_index_valuation(
        self, index_id: UUID, start: date, end: date,
    ) -> list[IndexValuationResult]:
        raise ProviderDataError("tushare 不支持 INDEX_VALUATION（legulegu primary）")
