# =====================================================================
# tests/unit/test_macro_providers.py —— Yahoo / FRED / legulegu（S3/S6/S7 实测口径）
# =====================================================================
from __future__ import annotations

import asyncio
from datetime import date
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

import pandas as pd
import pytest

from app.providers.contracts.base import ProviderConfigError, ProviderDataError
from app.providers.fred.macro import FredMacroProvider
from app.providers.legulegu.index_valuation import LeguleguIndexValuationProvider
from app.providers.yahoo.macro import YahooMacroProvider

INST = uuid4()


def cfg(token=None, proxy="env"):
    return SimpleNamespace(name="x", network_proxy=proxy, timeout_seconds=5.0, token=token)


# ---------------------------------------------------------------------
# Yahoo
# ---------------------------------------------------------------------


def _yahoo_df() -> pd.DataFrame:
    idx = pd.DatetimeIndex([pd.Timestamp("2026-08-20")])
    return pd.DataFrame(
        {"Open": [7600.0], "High": [7700.0], "Low": [7590.0], "Close": [7674.37], "Volume": [1.2e9]},
        index=idx,
    )


def test_yahoo_index_history() -> None:
    async def run() -> None:
        p = YahooMacroProvider(config=cfg(), symbol_resolver=lambda iid: "^GSPC")
        with patch.object(p, "_fetch", return_value=_yahoo_df()):
            bars = await p.get_index_history(INST, date(2026, 8, 1), date(2026, 8, 31))
        b = bars[0]
        assert b.close == Decimal("7674.37")
        assert b.currency == "USD"
        assert b.provenance.source == "us_index_quote"
        assert b.provenance.as_of_date == date(2026, 8, 20)

    asyncio.run(run())


def test_yahoo_fx_rates() -> None:
    async def run() -> None:
        p = YahooMacroProvider(config=cfg(), symbol_resolver=lambda iid: "USDCNY=X")
        with patch.object(p, "_fetch", return_value=_yahoo_df()):
            rates = await p.get_fx_rates("USD", "CNY", date(2026, 8, 1), date(2026, 8, 31))
        assert rates[0].rate == Decimal("7674.37")   # 测试数据 Close 即 rate
        assert rates[0].base_currency == "USD"
        assert rates[0].quote_currency == "CNY"
        assert rates[0].provenance.source_record_id == "USDCNY@2026-08-20"

    asyncio.run(run())


def test_yahoo_fx_unsupported_pair() -> None:
    async def run() -> None:
        p = YahooMacroProvider(config=cfg())
        with pytest.raises(ProviderDataError):
            await p.get_fx_rates("EUR", "CNY", date(2026, 8, 1), date(2026, 8, 31))

    asyncio.run(run())


def test_yahoo_index_valuation_not_supported() -> None:
    async def run() -> None:
        p = YahooMacroProvider(config=cfg())
        with pytest.raises(ProviderDataError):
            await p.get_index_valuation(INST, date(2026, 8, 1), date(2026, 8, 31))

    asyncio.run(run())


# ---------------------------------------------------------------------
# FRED
# ---------------------------------------------------------------------


def _fred_observations() -> dict:
    return {"observations": [
        {"date": "2026-08-14", "value": "6.7412"},
        {"date": "2026-08-13", "value": "."},      # 缺失值必须跳过
    ]}


def test_fred_fx_rates() -> None:
    async def run() -> None:
        p = FredMacroProvider(config=cfg(token="k"), symbol_resolver=None)
        with patch("app.providers.fred.macro.http_get_json", return_value=_fred_observations()) as mock:
            rates = await p.get_fx_rates("USD", "CNY", date(2026, 8, 1), date(2026, 8, 31))
        assert mock.call_args.kwargs["params"]["series_id"] == "DEXCHUS"
        assert len(rates) == 1                       # "." 行被跳过
        assert rates[0].rate == Decimal("6.7412")
        assert rates[0].trade_date == date(2026, 8, 14)
        assert rates[0].provenance.quality_status.value == "VERIFIED"
        assert rates[0].provenance.source_record_id == "DEXCHUS@2026-08-14"

    asyncio.run(run())


def test_fred_requires_key() -> None:
    async def run() -> None:
        p = FredMacroProvider(config=cfg(token=""), symbol_resolver=None)
        with pytest.raises(ProviderConfigError):
            await p.health_check()

    asyncio.run(run())


def test_fred_macro_series_extension() -> None:
    async def run() -> None:
        p = FredMacroProvider(config=cfg(token="k"), symbol_resolver=None)
        with patch("app.providers.fred.macro.http_get_json", return_value=_fred_observations()):
            obs = await p.get_macro_series("DEXCHUS", date(2026, 8, 1), date(2026, 8, 31))
        assert len(obs) == 1
        assert obs[0]["value"] == "6.7412"

    asyncio.run(run())


# ---------------------------------------------------------------------
# legulegu
# ---------------------------------------------------------------------


def _pe_df() -> pd.DataFrame:
    return pd.DataFrame([
        {"日期": "2026-08-20", "ttmPe": 12.5},
        {"日期": "2026-08-21", "ttmPe": 12.6},
    ])


def _pb_df() -> pd.DataFrame:
    return pd.DataFrame([
        {"日期": "2026-08-20", "ttmPb": 3.1},
        {"日期": "2026-08-21", "ttmPb": 3.2},
    ])


def test_legulegu_index_valuation() -> None:
    async def run() -> None:
        p = LeguleguIndexValuationProvider(config=cfg(), symbol_resolver=lambda iid: "沪深300")
        with patch("app.providers.legulegu.index_valuation.ak.stock_index_pe_lg",
                   return_value=_pe_df()), \
             patch("app.providers.legulegu.index_valuation.ak.stock_index_pb_lg",
                   return_value=_pb_df()):
            vals = await p.get_index_valuation(INST, date(2026, 8, 1), date(2026, 8, 31))
        assert len(vals) == 2
        assert vals[0].pe == Decimal("12.5")
        assert vals[0].pb == Decimal("3.1")
        assert vals[0].source == "legulegu"
        assert vals[0].provenance.source_record_id == "legulegu@2026-08-20"

    asyncio.run(run())


def test_legulegu_date_filter() -> None:
    async def run() -> None:
        p = LeguleguIndexValuationProvider(config=cfg(), symbol_resolver=lambda iid: "沪深300")
        with patch("app.providers.legulegu.index_valuation.ak.stock_index_pe_lg",
                   return_value=_pe_df()), \
             patch("app.providers.legulegu.index_valuation.ak.stock_index_pb_lg",
                   return_value=_pb_df()):
            vals = await p.get_index_valuation(INST, date(2026, 8, 21), date(2026, 8, 21))
        assert len(vals) == 1
        assert vals[0].as_of_date == date(2026, 8, 21)

    asyncio.run(run())


def test_legulegu_pe_fallback_column() -> None:
    """缺 ttmPe 列 → 退 '平均市盈率'（akshare 版本差异防御）。"""
    async def run() -> None:
        p = LeguleguIndexValuationProvider(config=cfg(), symbol_resolver=lambda iid: "沪深300")
        pe_df = pd.DataFrame([{"日期": "2026-08-20", "平均市盈率": 11.1}])
        with patch("app.providers.legulegu.index_valuation.ak.stock_index_pe_lg",
                   return_value=pe_df), \
             patch("app.providers.legulegu.index_valuation.ak.stock_index_pb_lg",
                   return_value=pd.DataFrame()):
            vals = await p.get_index_valuation(INST, date(2026, 8, 1), date(2026, 8, 31))
        assert vals[0].pe == Decimal("11.1")
        assert vals[0].pb is None

    asyncio.run(run())
