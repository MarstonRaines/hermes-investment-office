# =====================================================================
# backend/app/providers/yahoo/macro.py —— YahooMacroProvider
#
# S3/S7 实测：^GSPC/^NDX 指数点位 + USDCNY=X 汇率（依赖代理环境，network=env）。
# 用 yfinance 库（v0.1 已装）；同步调用经 asyncio.to_thread。
# =====================================================================
from __future__ import annotations

import asyncio
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import ClassVar
from uuid import UUID

from app.providers.common import ny_close, pct_or_none
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
from app.providers.contracts.macro import (
    FxRateResult,
    IndexBarResult,
    IndexValuationResult,
    MacroProvider,
)
from app.providers.yahoo.metadata import YahooMeta

__all__ = ["YahooMacroProvider"]

try:
    import yfinance as yf
except ImportError:  # pragma: no cover
    yf = None  # type: ignore[assignment]


class YahooMacroProvider(MacroProvider):
    provider_name: ClassVar[str] = "yahoo"
    display_name: ClassVar[str] = "Yahoo Finance"
    capabilities = frozenset(
        {
            ProviderCapability.INDEX_QUOTE,   # ^GSPC / ^NDX
            ProviderCapability.FX_RATES,      # USDCNY=X
        }
    )
    default_role = ProviderRole.PRIMARY
    quality_tier = QualityTier.TIER_3
    known_limits = YahooMeta.known_limits

    TRANSFORM_VERSION = "macro-normalizer/0.1.0"

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
            raise ProviderConfigError("yahoo: symbol_resolver 未注入")
        symbol = self._resolve(instrument_id)
        if not symbol:
            raise ProviderConfigError(f"yahoo: instrument {instrument_id} 无 symbol 映射")
        return symbol

    def _fetch(self, symbol: str, start: date, end: date):
        """yfinance 同步拉取（to_thread 包裹）。"""
        if yf is None:
            raise ProviderConfigError("yfinance 未安装")
        ticker = yf.Ticker(symbol)
        return ticker.history(start=start.isoformat(), end=(end + __import__("datetime").timedelta(days=1)).isoformat())

    async def get_index_history(
        self,
        index_id: UUID,
        start: date,
        end: date,
    ) -> list[IndexBarResult]:
        symbol = self._symbol(index_id)
        try:
            df = await asyncio.to_thread(self._fetch, symbol, start, end)
        except Exception as exc:  # noqa: BLE001 —— yfinance 异常类型不稳定
            raise ProviderDataError(f"yahoo: {symbol} 拉取失败: {exc}") from exc
        if df is None or df.empty:
            return []
        out: list[IndexBarResult] = []
        for ts, r in df.iterrows():
            td = ts.date() if hasattr(ts, "date") else date.fromisoformat(str(ts)[:10])
            if not (start <= td <= end):
                continue
            out.append(IndexBarResult(
                index_id=index_id, trade_date=td,
                open=_dec(r.get("Open")), high=_dec(r.get("High")),
                low=_dec(r.get("Low")), close=_dec(r.get("Close")),
                volume=_dec(r.get("Volume")), currency="USD",
                provenance=ProvenanceEnvelope(
                    source="us_index_quote", provider="yahoo",
                    source_record_id=f"yahoo@{td.isoformat()}",
                    observed_at=ny_close(td), retrieved_at=datetime.now(UTC),
                    as_of_date=td, quality_score=Decimal("0.80"),
                    quality_status="ACCEPTABLE", transform_version=self.TRANSFORM_VERSION,
                ),
            ))
        return out

    async def get_fx_rates(
        self,
        base_currency: str,
        quote_currency: str,
        start: date,
        end: date,
    ) -> list[FxRateResult]:
        if (base_currency, quote_currency) != ("USD", "CNY"):
            raise ProviderDataError(f"yahoo: v0.1 仅支持 USD/CNY，收到 {base_currency}/{quote_currency}")
        symbol = "USDCNY=X"
        try:
            df = await asyncio.to_thread(self._fetch, symbol, start, end)
        except Exception as exc:  # noqa: BLE001
            raise ProviderDataError(f"yahoo: {symbol} 拉取失败: {exc}") from exc
        if df is None or df.empty:
            return []
        out: list[FxRateResult] = []
        for ts, r in df.iterrows():
            td = ts.date() if hasattr(ts, "date") else date.fromisoformat(str(ts)[:10])
            rate = pct_or_none(r.get("Close"))
            if rate is None:
                continue
            as_of = datetime.combine(td, datetime.min.time(), tzinfo=UTC)
            out.append(FxRateResult(
                base_currency="USD", quote_currency="CNY", rate=Decimal(str(rate)),
                as_of=as_of, trade_date=td,
                provenance=ProvenanceEnvelope(
                    source="fx_rates", provider="yahoo",
                    source_record_id=f"USDCNY@{td.isoformat()}",
                    observed_at=ny_close(td), retrieved_at=datetime.now(UTC),
                    as_of_date=td, quality_score=Decimal("0.80"),
                    quality_status="ACCEPTABLE", transform_version=self.TRANSFORM_VERSION,
                ),
            ))
        return out

    async def get_index_valuation(
        self, index_id: UUID, start: date, end: date,
    ) -> list[IndexValuationResult]:
        raise ProviderDataError(
            "yahoo 不支持 INDEX_VALUATION（美股指数估值源 spike 后 PENDING，v0.1 不实现）"
        )


def _dec(v) -> Decimal | None:
    f = pct_or_none(v)
    return Decimal(str(f)) if f is not None else None
