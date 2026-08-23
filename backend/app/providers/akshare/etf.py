# =====================================================================
# backend/app/providers/akshare/etf.py —— AkShareEastmoneyEtfProvider
#
# ADR-005 D2/D3：eastmoney 源直连被阻、显式代理可通 —— 本实现不复用 akshare
# 内部 requests（无法注入 proxies），改为直接调用 eastmoney HTTP 端点并
# 用 make_session(proxy_mode) 显式注入 session.proxies（ADR-005 D2 强制）。
#
# 端点：
# - ETF 行情：push2his.eastmoney.com/api/qt/stock/kline/get（klt=101 日线，fqt=0 不复权）
# - NAV：fund.eastmoney.com/pingzhongdata/{code}.js（Data_netWorthTrend）
# - 持仓：fundf10.eastmoney.com/FundArchivesDatas.aspx?type=jjcc（季报持仓，Level 1）
# - 额度：无结构化渠道 → UNKNOWN（S8：事件状态，人工/半自动录入，禁止推断）
# =====================================================================
from __future__ import annotations

import json
import re
from datetime import date, datetime
from decimal import Decimal
from typing import ClassVar
from uuid import UUID

from app.common.enums import QuotaStatus
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
from app.providers.contracts.etf import (
    ETFProvider,
    HoldingItem,
    HoldingSnapshotResult,
    NavResult,
    QuotaStatusResult,
)
from app.providers.contracts.market_data import (
    AdjustType,
    MarketBarResult,
    MarketDataProvider,
    MarketSnapshotResult,
)
from app.providers.http import http_get_json, http_get_text, make_session

__all__ = ["AkShareEastmoneyEtfProvider"]

_KLINE_URL = "https://push2his.eastmoney.com/api/qt/stock/kline/get"
_NAV_JS_URL = "https://fund.eastmoney.com/pingzhongdata/{code}.js"
_HOLD_URL = "https://fundf10.eastmoney.com/FundArchivesDatas.aspx"

_MARKET_OF = {"SH": "1", "SZ": "0"}


class AkShareEastmoneyEtfProvider(ETFProvider, MarketDataProvider):
    provider_name: ClassVar[str] = "akshare_eastmoney"
    display_name: ClassVar[str] = "AkShare（东方财富源）"
    capabilities = frozenset(
        {
            ProviderCapability.CN_ETF_QUOTE,
            ProviderCapability.FUND_NAV,
            ProviderCapability.FUND_HOLDINGS,
            ProviderCapability.QUOTA_STATUS,
        }
    )
    default_role = ProviderRole.FALLBACK
    quality_tier = QualityTier.TIER_3
    known_limits = [
        "eastmoney 直连被阻，必须显式代理 127.0.0.1:7892（ADR-005）",
        "持仓披露日不可得 → disclosure_date 用报告期止近似 + DISCLOSURE_DATE_APPROX flag",
        "QUOTA_STATUS 无结构化渠道 → 恒 UNKNOWN（S8，人工/半自动录入）",
    ]

    TRANSFORM_VERSION = "etf-normalizer/0.1.0"

    def __init__(self, config=None, symbol_resolver=None) -> None:
        self.config = config
        self._resolve = symbol_resolver
        proxy = getattr(config, "network_proxy", "direct")
        self._session = make_session(proxy)
        self._timeout = getattr(config, "timeout_seconds", 20.0)

    async def health_check(self) -> ProviderHealth:
        return ProviderHealth(
            provider=self.provider_name, status="HEALTHY", checked_at=datetime.now(),
            detail={"proxy": str(getattr(self.config, "network_proxy", "direct"))},
        )

    async def quality_report(self) -> ProviderQualityReport:
        return ProviderQualityReport(
            provider=self.provider_name, tier=self.quality_tier,
            quality_score=Decimal("0.85"),
        )

    def _symbol(self, instrument_id: UUID) -> str:
        if self._resolve is None:
            raise ProviderConfigError("akshare_eastmoney: symbol_resolver 未注入")
        symbol = self._resolve(instrument_id)
        if not symbol:
            raise ProviderConfigError(f"akshare_eastmoney: instrument {instrument_id} 无 symbol 映射")
        return symbol  # 形如 513100.SH / 159915.SZ

    def _code_and_market(self, symbol: str) -> tuple[str, str]:
        if "." not in symbol:
            raise ProviderDataError(f"akshare_eastmoney: symbol 格式错误 {symbol!r}")
        code, mkt = symbol.split(".")
        if mkt not in _MARKET_OF:
            raise ProviderDataError(f"akshare_eastmoney: 不支持市场 {mkt!r}")
        return code, _MARKET_OF[mkt]

    # ---- MarketDataProvider（CN_ETF_QUOTE fallback）----

    async def get_price_history(
        self,
        instrument_id: UUID,
        start: date,
        end: date,
        adjust: AdjustType = AdjustType.NONE,
    ) -> list[MarketBarResult]:
        symbol = self._symbol(instrument_id)
        code, market = self._code_and_market(symbol)
        params = {
            "secid": f"{market}.{code}",
            "fields1": "f1,f2,f3,f4,f5,f6",
            "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61",
            "klt": "101", "fqt": "0",
            "beg": start.strftime("%Y%m%d"), "end": end.strftime("%Y%m%d"),
            "lmt": "1000000",
        }
        data = await http_get_json(self._session, _KLINE_URL, params=params, timeout=self._timeout)
        klines = (data or {}).get("data") or {}
        klines = klines.get("klines") or []
        bars: list[MarketBarResult] = []
        for line in klines:
            parts = str(line).split(",")
            if len(parts) < 11:
                continue
            # f51 date, f52 open, f53 close, f54 high, f55 low, f56 volume(手), f57 amount(元),
            # f58 amplitude, f59 pct_chg, f60 change, f61 turnover
            td = date.fromisoformat(parts[0])
            volume_hand = pct_or_none(parts[5])
            bars.append(MarketBarResult(
                instrument_id=instrument_id, trade_date=td,
                open=_dec(parts[1]), close=_dec(parts[2]),
                high=_dec(parts[3]), low=_dec(parts[4]),
                volume=Decimal(str(volume_hand * 100)) if volume_hand is not None else None,  # 手→股
                amount=_dec(parts[6]),
                pct_change=_dec(parts[8]), turnover_rate=_dec(parts[10]),
                currency="CNY", provider="akshare_eastmoney",
                provenance=ProvenanceEnvelope(
                    source="cn_etf_daily_market", provider="akshare_eastmoney",
                    source_record_id=f"akshare_eastmoney@{td.isoformat()}",
                    observed_at=shanghai_15(td), retrieved_at=datetime.now(),
                    as_of_date=td, quality_score=Decimal("0.85"),
                    quality_status="ACCEPTABLE", transform_version=self.TRANSFORM_VERSION,
                ),
            ))
        return bars

    async def get_market_snapshot(
        self, instrument_ids: list, as_of: date,
    ) -> list[MarketSnapshotResult]:
        from datetime import timedelta

        results = []
        for inst in instrument_ids:
            bars = await self.get_price_history(inst, as_of - timedelta(days=45), as_of)
            if not bars:
                results.append(MarketSnapshotResult(
                    instrument_id=inst, as_of=as_of, trade_date=None,
                    provenance=ProvenanceEnvelope(
                        source="cn_etf_daily_market", provider="akshare_eastmoney",
                        observed_at=datetime.now(), retrieved_at=datetime.now(),
                        quality_score=Decimal("0.0"), quality_status="STALE",
                        quality_flags=["NO_BAR"], transform_version=self.TRANSFORM_VERSION,
                    ),
                ))
                continue
            last = bars[-1]
            results.append(MarketSnapshotResult(
                instrument_id=inst, as_of=as_of, trade_date=last.trade_date,
                close=last.close, pct_change=last.pct_change,
                volume=last.volume, amount=last.amount, provenance=last.provenance,
            ))
        return results

    async def get_adj_factors(self, instrument_id: UUID, start: date, end: date):
        raise ProviderDataError(
            "akshare_eastmoney 不支持 ADJ_FACTOR（链：tushare → akshare_sina → 人工校准 → CONFLICT）"
        )

    # ---- ETFProvider ----

    async def get_nav_history(self, instrument_id: UUID) -> list[NavResult]:
        symbol = self._symbol(instrument_id)
        code, _ = self._code_and_market(symbol)
        js = await http_get_text(
            self._session, _NAV_JS_URL.format(code=code),
            headers={"Referer": f"https://fund.eastmoney.com/{code}.html"},
            timeout=self._timeout,
        )
        m = re.search(r"Data_netWorthTrend\s*=\s*(\[.*?\]);", js, re.S)
        if not m:
            raise ProviderDataError(f"akshare_eastmoney: {code} 无 Data_netWorthTrend")
        try:
            trend = json.loads(m.group(1))
        except json.JSONDecodeError as exc:
            raise ProviderDataError(f"akshare_eastmoney: {code} Data_netWorthTrend 解析失败") from exc
        out: list[NavResult] = []
        for item in trend:
            x_ms = item.get("x")
            y = item.get("y")
            if x_ms is None or y is None:
                continue
            nd = datetime.fromtimestamp(x_ms / 1000).date()
            out.append(NavResult(
                instrument_id=instrument_id, nav_date=nd, nav=Decimal(str(y)),
                retrieved_at=datetime.now(),
                provenance=ProvenanceEnvelope(
                    source="cn_fund_nav", provider="akshare_eastmoney",
                    source_record_id=f"akshare_eastmoney@{nd.isoformat()}",
                    observed_at=shanghai_15(nd), retrieved_at=datetime.now(),
                    as_of_date=nd, quality_score=Decimal("0.85"),
                    quality_status="ACCEPTABLE", transform_version=self.TRANSFORM_VERSION,
                ),
            ))
        return out

    async def get_holding_snapshots(self, instrument_id: UUID) -> list[HoldingSnapshotResult]:
        symbol = self._symbol(instrument_id)
        code, _ = self._code_and_market(symbol)
        # 最近 3 年季报（当年 + 前 2 年 × 2 个披露期：Q2(6月)/Q4(12月) 持仓披露）

        out: list[HoldingSnapshotResult] = []
        this_year = datetime.now().year
        for year in range(this_year - 2, this_year + 1):
            for month in ("6", "12"):
                text = await http_get_text(
                    self._session, _HOLD_URL,
                    params={"type": "jjcc", "code": code, "topline": "10", "year": year, "month": month},
                    headers={"Referer": f"https://fundf10.eastmoney.com/ccmx_{code}.html"},
                    timeout=self._timeout,
                )
                snapshot = _parse_holdings_html(text, instrument_id, code, year, month)
                if snapshot is not None:
                    out.append(snapshot)
        return out

    async def get_quota_status(self, instrument_id: UUID) -> QuotaStatusResult:
        """无结构化渠道（S8）→ 恒 UNKNOWN；事件状态由人工/半自动录入（M2）。"""
        return QuotaStatusResult(
            instrument_id=instrument_id,
            quota_status=QuotaStatus.UNKNOWN,
            provenance=ProvenanceEnvelope(
                source="cn_fund_quota_announcement", provider="akshare_eastmoney",
                observed_at=datetime.now(), retrieved_at=datetime.now(),
                quality_score=Decimal("0.0"), quality_status="STALE",
                quality_flags=["QUOTA_MANUAL_REQUIRED"], transform_version=self.TRANSFORM_VERSION,
            ),
        )


def _dec(v) -> Decimal | None:
    f = pct_or_none(v)
    return Decimal(str(f)) if f is not None else None


def _parse_holdings_html(
    text: str, instrument_id: UUID, code: str, year: int, month: str,
) -> HoldingSnapshotResult | None:
    """解析 FundArchivesDatas.aspx 的 jjcc HTML table（<td class='tb'>序号</td>…）。

    eastmoney 返回的是 JS 字面量（键无引号，非标准 JSON），直接正则提取
    content 字符串（到 ',arryear' 截止），不做 json.loads。
    """
    if "暂无数据" in text or "apidata" not in text:
        return None
    m = re.search(r'content:"(.*?)";arryear', text, re.S)
    if not m:
        return None
    content = m.group(1).replace('\\"', '"')
    rows = re.findall(
        r"<td[^>]*>(.*?)</td><td[^>]*>(.*?)</td><td[^>]*>(.*?)</td>"
        r"<td[^>]*>(.*?)</td><td[^>]*>(.*?)</td><td[^>]*>(.*?)</td>",
        content, re.S,
    )
    items: list[HoldingItem] = []
    for i, row in enumerate(rows):
        cells = [_strip_tags(c) for c in row]
        if len(cells) < 6:
            continue
        rank = pct_or_none(cells[0])
        stock_code = cells[1]
        stock_name = cells[2]
        weight = pct_or_none(cells[3])
        shares = pct_or_none(cells[4])
        market_value = pct_or_none(cells[5])
        items.append(HoldingItem(
            rank=int(rank) if rank is not None else None,
            provider_symbol=stock_code or None,
            security_name=stock_name or None,
            weight_pct=Decimal(str(weight)) if weight is not None else None,  # 披露为百分比
            shares=Decimal(str(shares)) if shares is not None else None,
            market_value=Decimal(str(market_value)) if market_value is not None else None,
        ))
    if not items:
        return None
    # 报告期止 = year-month（6 → 0630，12 → 1231）；披露日不可得 → 近似 + flag
    period_end = date(year, 6, 30) if month == "6" else date(year, 12, 31)
    return HoldingSnapshotResult(
        instrument_id=instrument_id,
        report_period=period_end,
        disclosure_date=period_end,
        source="HALF_YEAR" if month == "6" else "ANNUAL",
        holdings=items,
        holding_count=len(items),
        provenance=ProvenanceEnvelope(
            source="cn_fund_holdings", provider="akshare_eastmoney",
            source_record_id=f"akshare_eastmoney@{period_end.isoformat()}@{'HALF_YEAR' if month == '6' else 'ANNUAL'}",
            observed_at=shanghai_15(period_end), retrieved_at=datetime.now(),
            as_of_date=period_end, quality_score=Decimal("0.85"),
            quality_status="ACCEPTABLE",
            quality_flags=["DISCLOSURE_DATE_APPROX"],
            transform_version=AkShareEastmoneyEtfProvider.TRANSFORM_VERSION,
        ),
    )


def _strip_tags(html: str) -> str:
    text = re.sub(r"<[^>]+>", "", html or "")
    text = text.replace("&nbsp;", " ").replace("&#160;", " ")
    return text.strip()
