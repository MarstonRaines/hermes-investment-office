# =====================================================================
# tests/unit/test_tushare_provider.py —— TuShareProvider 映射与单位换算（S1/S5 实测口径）
# =====================================================================
from __future__ import annotations

import asyncio
from datetime import date
from decimal import Decimal
from uuid import uuid4

import pandas as pd
import pytest

from app.providers.contracts.base import ProviderConfigError, ProviderRateLimited
from app.providers.tushare.client import map_tushare_error
from app.providers.tushare.provider import TuShareProvider

INST = uuid4()


class FakeClient:
    """模拟 TushareClient：按 api_name 返回预设 DataFrame。"""

    def __init__(self, responses: dict) -> None:
        self.responses = responses
        self.calls: list[tuple[str, dict]] = []

    async def call(self, api_name: str, **params):
        self.calls.append((api_name, params))
        return self.responses.get(api_name, pd.DataFrame())


def make_provider(client) -> TuShareProvider:
    return TuShareProvider(
        config=type("Cfg", (), {"token": "tk", "timeout_seconds": 20})(),
        symbol_resolver=lambda iid: "600519.SH" if iid == INST else None,
        client=client,
    )


def _daily_df() -> pd.DataFrame:
    return pd.DataFrame([{
        "ts_code": "600519.SH", "trade_date": "20260821",
        "open": 138.0, "high": 139.5, "low": 137.5, "close": 138.5,
        "pre_close": 137.0, "change": 1.5, "pct_chg": 1.095,
        "vol": 31245.0, "amount": 432101.0,
    }])


def test_price_history_units_and_provenance() -> None:
    async def run() -> None:
        client = FakeClient({"daily": _daily_df()})
        p = make_provider(client)
        bars = await p.get_price_history(INST, date(2026, 8, 1), date(2026, 8, 31))
        assert len(bars) == 1
        b = bars[0]
        assert b.trade_date == date(2026, 8, 21)
        # 单位换算：vol 手→股（×100），amount 千元→元（×1000）
        assert b.volume == Decimal("3124500")
        assert b.amount == Decimal("432101000")
        assert b.close == Decimal("138.5")
        assert b.pct_change == Decimal("1.095")
        assert b.currency == "CNY"
        assert b.provenance.source == "cn_daily_market"
        assert b.provenance.provider == "tushare"
        assert b.provenance.source_record_id == "tushare@2026-08-21"
        assert b.provenance.as_of_date == date(2026, 8, 21)
        assert b.provenance.transform_version == "market-normalizer/0.1.0"

    asyncio.run(run())


def test_etf_uses_fund_daily_when_daily_empty() -> None:
    """S1 关键发现：ETF 行情必须走 fund_daily（daily 对 ETF 返回空）。"""
    async def run() -> None:
        client = FakeClient({"daily": pd.DataFrame(), "fund_daily": _daily_df()})
        p = make_provider(client)
        bars = await p.get_price_history(INST, date(2026, 8, 1), date(2026, 8, 31))
        assert len(bars) == 1
        assert bars[0].provenance.source == "cn_etf_daily_market"
        api_names = [c[0] for c in client.calls]
        assert api_names == ["daily", "fund_daily"]

    asyncio.run(run())


def test_adj_factor() -> None:
    async def run() -> None:
        client = FakeClient({"adj_factor": pd.DataFrame([
            {"ts_code": "600519.SH", "trade_date": "20260821", "adj_factor": 3.1415},
        ])})
        p = make_provider(client)
        factors = await p.get_adj_factors(INST, date(2026, 8, 1), date(2026, 8, 31))
        assert factors[0].adj_factor == Decimal("3.1415")
        assert factors[0].provenance.source == "cn_adj_factor"

    asyncio.run(run())


def test_price_history_adjust_computes_adjusted_close() -> None:
    async def run() -> None:
        client = FakeClient({
            "daily": _daily_df(),
            "adj_factor": pd.DataFrame([
                {"ts_code": "600519.SH", "trade_date": "20260821", "adj_factor": 2.0},
            ]),
        })
        p = make_provider(client)
        from app.providers.contracts.market_data import AdjustType

        bars = await p.get_price_history(INST, date(2026, 8, 1), date(2026, 8, 31),
                                         adjust=AdjustType.BACKWARD)
        assert bars[0].adj_factor == Decimal("2.0")
        assert bars[0].adjusted_close == Decimal("277.0")
        assert "ADJUSTED" in bars[0].provenance.quality_flags

    asyncio.run(run())


def test_financial_facts_mapping_and_units() -> None:
    """S5：TuShare 报表单位恒为元，base_unit=CNY 直接映射。"""
    async def run() -> None:
        income = pd.DataFrame([{
            "ts_code": "600519.SH", "ann_date": "20260328", "end_date": "20251231",
            "total_revenue": 150000000000.0, "total_cogs": 30000000000.0,
            "operate_profit": 100000000000.0, "n_income_attr_p": 75000000000.0,
        }])
        balance = pd.DataFrame([{
            "ts_code": "600519.SH", "ann_date": "20260328", "end_date": "20251231",
            "total_assets": 250000000000.0, "total_liab": 50000000000.0,
            "total_hldr_eqy_exc_min_int": 190000000000.0, "money_cap": 60000000000.0,
            "short_loan": 0.0, "long_loan": 0.0, "bond_payable": 1000000000.0,
        }])
        cashflow = pd.DataFrame([{
            "ts_code": "600519.SH", "ann_date": "20260328", "end_date": "20251231",
            "n_cashflow_oper": 90000000000.0, "c_pay_acq_const_fiolta": 2000000000.0,
        }])
        client = FakeClient({
            "income": income, "balancesheet": balance, "cashflow": cashflow,
            "daily_basic": pd.DataFrame([{
                "ts_code": "600519.SH", "trade_date": "20251231",
                "total_share": 125619.78,     # 万股 → ×10000 = 1,256,197,800 股
            }]),
        })
        p = make_provider(client)
        metrics = ["REVENUE", "GROSS_PROFIT", "OPERATING_INCOME", "NET_INCOME",
                   "OPERATING_CASH_FLOW", "CAPEX", "TOTAL_ASSETS", "TOTAL_LIABILITIES",
                   "TOTAL_EQUITY", "CASH", "DEBT", "SHARES_OUTSTANDING"]
        facts = await p.get_financial_facts(INST, metrics, [date(2025, 12, 31)])
        by_metric = {f.metric_code: f for f in facts}
        assert len(by_metric) == 12
        assert by_metric["REVENUE"].value == Decimal("150000000000")
        assert by_metric["GROSS_PROFIT"].value == Decimal("120000000000")
        assert by_metric["NET_INCOME"].value == Decimal("75000000000")
        assert by_metric["DEBT"].value == Decimal("1000000000")
        assert by_metric["SHARES_OUTSTANDING"].unit == "CNY"   # 金额域单位统一 CNY
        for f in facts:
            assert f.original_unit == "元"
            assert f.unit == "CNY"
            assert f.period_type == "FY"
            assert f.period_end == date(2025, 12, 31)
            assert f.published_at is not None          # ann_date → PIT 可见性
            assert f.provenance.source == "cn_financial_statements"
            assert f.provenance.source_record_id.startswith("600519.SH@2025-12-31@")

    asyncio.run(run())


def test_financial_facts_skip_missing_columns() -> None:
    """缺字段 → 该 metric 跳过（合法缺口，不抛错）。"""
    async def run() -> None:
        client = FakeClient({"income": pd.DataFrame([{
            "ts_code": "600519.SH", "ann_date": "20260328", "end_date": "20251231",
            "total_revenue": 1.0,
        }])})
        p = make_provider(client)
        facts = await p.get_financial_facts(INST, ["REVENUE", "OPERATING_INCOME"], [date(2025, 12, 31)])
        assert [f.metric_code for f in facts] == ["REVENUE"]

    asyncio.run(run())


def test_nav_history() -> None:
    async def run() -> None:
        client = FakeClient({"fund_nav": pd.DataFrame([
            {"ts_code": "513100.SH", "ann_date": "20260822", "nav_date": "20260820",
             "unit_nav": 1.234, "accum_nav": 1.234},
        ])})
        p = make_provider(client)
        navs = await p.get_nav_history(INST)
        assert navs[0].nav_date == date(2026, 8, 20)
        assert navs[0].nav == Decimal("1.234")
        assert navs[0].published_at is not None     # ann_date
        assert navs[0].provenance.source == "cn_fund_nav"

    asyncio.run(run())


def test_index_history() -> None:
    async def run() -> None:
        client = FakeClient({"index_daily": pd.DataFrame([
            {"ts_code": "000300.SH", "trade_date": "20260821", "close": 4200.5,
             "open": 4190.0, "high": 4210.0, "low": 4185.0, "vol": 123456.0},
        ])})
        p = make_provider(client)
        bars = await p.get_index_history(INST, date(2026, 8, 1), date(2026, 8, 31))
        assert bars[0].close == Decimal("4200.5")
        assert bars[0].currency == "CNY"
        assert bars[0].provenance.source == "cn_index_quote"

    asyncio.run(run())


def test_health_check_requires_token() -> None:
    async def run() -> None:
        p = TuShareProvider(config=type("Cfg", (), {"token": ""})())
        with pytest.raises(ProviderConfigError):
            await p.health_check()

    asyncio.run(run())


def test_error_mapping() -> None:
    assert isinstance(map_tushare_error(ValueError("每分钟最多访问该接口120次"), "daily"),
                      ProviderRateLimited)
    from app.providers.contracts.base import ProviderAuthError, ProviderDataError

    assert isinstance(map_tushare_error(ValueError("积分不足"), "daily"), ProviderAuthError)
    assert isinstance(map_tushare_error(ValueError("某处异常"), "daily"), ProviderDataError)
