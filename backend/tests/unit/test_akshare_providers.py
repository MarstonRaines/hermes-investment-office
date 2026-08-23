# =====================================================================
# tests/unit/test_akshare_providers.py —— AkShare 分源 provider（S2 实测口径）
# =====================================================================
from __future__ import annotations

import asyncio
import json
from datetime import date
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

import pandas as pd
import pytest

from app.common.enums import QuotaStatus
from app.providers.akshare.etf import AkShareEastmoneyEtfProvider, _parse_holdings_html
from app.providers.akshare.fundamentals import AkShareThsFundamentalProvider
from app.providers.akshare.market import AkShareSinaMarketProvider
from app.providers.contracts.base import ProviderDataError

INST = uuid4()


def cfg(proxy="direct"):
    return SimpleNamespace(name="x", network_proxy=proxy, timeout_seconds=5.0, token=None)


# ---------------------------------------------------------------------
# akshare_sina
# ---------------------------------------------------------------------


def _sina_df() -> pd.DataFrame:
    return pd.DataFrame([{
        "date": pd.Timestamp("2026-08-21"), "open": 138.0, "high": 139.5,
        "low": 137.5, "close": 138.5, "volume": 3124500.0, "amount": 432101000.0,
        "pct_change": 1.095, "hfq_factor": 3.1415,
    }])


def test_sina_price_history_with_symbol_conversion() -> None:
    async def run() -> None:
        p = AkShareSinaMarketProvider(config=cfg(), symbol_resolver=lambda iid: "600519.SH")
        with patch("app.providers.akshare.market.ak.stock_zh_a_daily",
                   return_value=_sina_df()) as mock:
            bars = await p.get_price_history(INST, date(2026, 8, 1), date(2026, 8, 31))
        assert mock.call_args.kwargs["symbol"] == "sh600519"
        assert bars[0].close == Decimal("138.5")
        assert bars[0].provenance.provider == "akshare_sina"
        assert bars[0].provenance.source == "cn_daily_market"

    asyncio.run(run())


def test_sina_adj_factor_uses_hfq() -> None:
    async def run() -> None:
        p = AkShareSinaMarketProvider(config=cfg(), symbol_resolver=lambda iid: "600519.SH")
        with patch("app.providers.akshare.market.ak.stock_zh_a_daily", return_value=_sina_df()):
            factors = await p.get_adj_factors(INST, date(2026, 8, 1), date(2026, 8, 31))
        assert factors[0].adj_factor == Decimal("3.1415")

    asyncio.run(run())


def test_sina_adj_factor_missing_column_raises() -> None:
    """hfq_factor 缺失 → ProviderDataError（诚实缺口，链终态 CONFLICT/人工校准）。"""
    async def run() -> None:
        p = AkShareSinaMarketProvider(config=cfg(), symbol_resolver=lambda iid: "600519.SH")
        df = _sina_df().drop(columns=["hfq_factor"])
        with patch("app.providers.akshare.market.ak.stock_zh_a_daily", return_value=df):
            with pytest.raises(ProviderDataError):
                await p.get_adj_factors(INST, date(2026, 8, 1), date(2026, 8, 31))

    asyncio.run(run())


# ---------------------------------------------------------------------
# akshare_ths
# ---------------------------------------------------------------------


def _ths_df() -> pd.DataFrame:
    return pd.DataFrame([{
        "报告期": "2025-12-31",
        "营业总收入": 150000000000.0, "归母净利润": 75000000000.0,
        "经营活动产生的现金流量净额": 90000000000.0, "总资产": 250000000000.0,
        "总负债": 50000000000.0, "股东权益": 190000000000.0,
    }])


def test_ths_financial_facts_mapping() -> None:
    async def run() -> None:
        p = AkShareThsFundamentalProvider(config=cfg(), symbol_resolver=lambda iid: "600519.SH")
        with patch("app.providers.akshare.fundamentals.ak.stock_financial_abstract_ths",
                   return_value=_ths_df()):
            facts = await p.get_financial_facts(
                INST, ["REVENUE", "NET_INCOME", "TOTAL_ASSETS"], [date(2025, 12, 31)])
        by = {f.metric_code: f for f in facts}
        assert by["REVENUE"].value == Decimal("150000000000")
        assert by["NET_INCOME"].value == Decimal("75000000000")
        assert by["TOTAL_ASSETS"].statement_type == "BALANCE"
        assert by["REVENUE"].published_at is None
        assert "NO_PUBLISHED_AT" in by["REVENUE"].provenance.quality_flags

    asyncio.run(run())


# ---------------------------------------------------------------------
# akshare_eastmoney（直接 HTTP + 显式代理）
# ---------------------------------------------------------------------


def _kline_json() -> dict:
    return {"data": {"klines": [
        "2026-08-21,1.3700,1.3820,1.3920,1.3760,123450,1706069000.0,1.6,0.876,0.012,1.23",
    ]}}


def _nav_js() -> str:
    return (
        "var Data_netWorthTrend = "
        + json.dumps([{"x": 1724169600000, "y": 1.234, "equityReturn": 0.1, "unitMoney": ""}])
        + ";var Data_ACWorthTrend = [];"
    )


_HOLD_HTML = (
    "var apidata={content:\""
    "<table class='w782 comm jjcx'><thead>..</thead><tbody>"
    "<tr><td class='tb'>1</td><td class='tb'><a href='/sh600519.html'>600519</a></td>"
    "<td class='tb'>贵州茅台</td><td class='td'>9.88</td><td class='td'>123456</td>"
    "<td class='td'>123456789.00</td>"
    "</tr></tbody></table>\";arryear:[2026,2025];};"
)


def make_eastmoney(proxy="http://127.0.0.1:7892"):
    return AkShareEastmoneyEtfProvider(
        config=SimpleNamespace(name="akshare_eastmoney", network_proxy=proxy,
                               timeout_seconds=5.0, token=None),
        symbol_resolver=lambda iid: "513100.SH",
    )


def test_eastmoney_session_proxy_injected() -> None:
    """ADR-005 D2：显式代理注入 session.proxies，且 trust_env=False。"""
    p = make_eastmoney("http://127.0.0.1:7892")
    assert p._session.proxies == {"http": "http://127.0.0.1:7892", "https": "http://127.0.0.1:7892"}
    assert p._session.trust_env is False


def test_eastmoney_etf_quote_parsing() -> None:
    async def run() -> None:
        p = make_eastmoney()
        with patch("app.providers.akshare.etf.http_get_json", return_value=_kline_json()) as mock:
            bars = await p.get_price_history(INST, date(2026, 8, 1), date(2026, 8, 31))
        # secid: SH → market 1
        assert mock.call_args.kwargs["params"]["secid"] == "1.513100"
        assert mock.call_args.kwargs["params"]["fqt"] == "0"
        b = bars[0]
        assert b.trade_date == date(2026, 8, 21)
        assert b.close == Decimal("1.3820")
        assert b.volume == Decimal("12345000")        # 手→股 ×100
        assert b.amount == Decimal("1706069000.0")
        assert b.pct_change == Decimal("0.876")
        assert b.turnover_rate == Decimal("1.23")
        assert b.provenance.source == "cn_etf_daily_market"

    asyncio.run(run())


def test_eastmoney_nav_parsing() -> None:
    async def run() -> None:
        p = make_eastmoney()
        with patch("app.providers.akshare.etf.http_get_text", return_value=_nav_js()):
            navs = await p.get_nav_history(INST)
        assert navs[0].nav == Decimal("1.234")
        assert navs[0].provenance.source == "cn_fund_nav"

    asyncio.run(run())


def test_eastmoney_holdings_parsing() -> None:
    async def run() -> None:
        p = make_eastmoney()
        with patch("app.providers.akshare.etf.http_get_text", return_value=_HOLD_HTML):
            snaps = await p.get_holding_snapshots(INST)
        # 3 年 × 2 期 = 6 次请求；只有返回内容的期数产生快照
        assert len(snaps) >= 1
        snap = snaps[0]
        assert snap.holdings[0].provider_symbol == "600519"
        assert snap.holdings[0].weight_pct == Decimal("9.88")
        assert snap.holdings[0].shares == Decimal("123456")
        assert "DISCLOSURE_DATE_APPROX" in snap.provenance.quality_flags

    asyncio.run(run())


def test_parse_holdings_html_direct() -> None:
    snap = _parse_holdings_html(_HOLD_HTML, INST, "513100", 2026, "6")
    assert snap is not None
    assert snap.report_period == date(2026, 6, 30)
    assert snap.source == "HALF_YEAR"
    assert snap.holdings[0].security_name == "贵州茅台"


def test_eastmoney_quota_returns_unknown() -> None:
    """S8：无结构化渠道 → 恒 UNKNOWN，禁止假装 OPEN。"""
    async def run() -> None:
        p = make_eastmoney()
        result = await p.get_quota_status(INST)
        assert result.quota_status is QuotaStatus.UNKNOWN
        assert "QUOTA_MANUAL_REQUIRED" in result.provenance.quality_flags

    asyncio.run(run())


def test_health_check_reports_proxy() -> None:
    async def run() -> None:
        p = make_eastmoney()
        h = await p.health_check()
        assert h.status == "HEALTHY"
        assert h.detail["proxy"] == "http://127.0.0.1:7892"

    asyncio.run(run())
