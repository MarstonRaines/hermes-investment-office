# =====================================================================
# backend/app/providers/fred/macro.py —— FredMacroProvider
#
# S7 实测：FRED DEXCHUS（美元兑人民币，权威口径）作 FX 交叉验证源（auxiliary，
# ADR-006）；MACRO_SERIES primary（TIER_1）。依赖代理环境（network=env）。
# =====================================================================
from __future__ import annotations

from datetime import UTC, date, datetime
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
from app.providers.contracts.macro import (
    FxRateResult,
    IndexBarResult,
    IndexValuationResult,
    MacroProvider,
)
from app.providers.fred.metadata import FredMeta
from app.providers.http import http_get_json, make_session

__all__ = ["FredMacroProvider"]

_OBS_URL = "https://api.stlouisfed.org/fred/series/observations"


class FredMacroProvider(MacroProvider):
    provider_name: ClassVar[str] = "fred"
    display_name: ClassVar[str] = "FRED（St. Louis Fed）"
    capabilities = frozenset(
        {
            ProviderCapability.FX_RATES,      # DEXCHUS（交叉验证，AUXILIARY）
            ProviderCapability.MACRO_SERIES,  # 宏观序列（v0.1 可选）
        }
    )
    default_role = ProviderRole.AUXILIARY
    quality_tier = QualityTier.TIER_1
    known_limits = FredMeta.known_limits

    TRANSFORM_VERSION = "macro-normalizer/0.1.0"

    def __init__(self, config=None, symbol_resolver=None) -> None:
        self.config = config
        self._resolve = symbol_resolver
        proxy = getattr(config, "network_proxy", "env")
        self._session = make_session(proxy)
        self._timeout = getattr(config, "timeout_seconds", 20.0)
        self._api_key = (config.token if config else None) or ""

    async def health_check(self) -> ProviderHealth:
        if not self._api_key:
            raise ProviderConfigError("FRED API key 未配置（HERMES_FRED_API_KEY）")
        return ProviderHealth(provider=self.provider_name, status="HEALTHY", checked_at=datetime.now())

    async def quality_report(self) -> ProviderQualityReport:
        return ProviderQualityReport(
            provider=self.provider_name, tier=self.quality_tier, quality_score=Decimal("0.98"),
        )

    async def _observations(self, series_id: str, start: date, end: date) -> list[dict]:
        if not self._api_key:
            raise ProviderConfigError("FRED API key 未配置（HERMES_FRED_API_KEY）")
        data = await http_get_json(
            self._session, _OBS_URL,
            params={
                "series_id": series_id, "api_key": self._api_key,
                "file_type": "json",
                "observation_start": start.isoformat(),
                "observation_end": end.isoformat(),
            },
            timeout=self._timeout,
        )
        obs = (data or {}).get("observations") or []
        return [o for o in obs if o.get("value") not in (None, ".")]

    async def get_fx_rates(
        self,
        base_currency: str,
        quote_currency: str,
        start: date,
        end: date,
    ) -> list[FxRateResult]:
        if (base_currency, quote_currency) != ("USD", "CNY"):
            raise ProviderDataError(f"fred: v0.1 仅支持 USD/CNY，收到 {base_currency}/{quote_currency}")
        out: list[FxRateResult] = []
        for o in await self._observations("DEXCHUS", start, end):
            d = date.fromisoformat(o["date"])
            rate = pct_or_none(o["value"])
            if rate is None:
                continue
            as_of = datetime.combine(d, datetime.min.time(), tzinfo=UTC)
            out.append(FxRateResult(
                base_currency="USD", quote_currency="CNY", rate=Decimal(str(rate)),
                as_of=as_of, trade_date=d,
                provenance=ProvenanceEnvelope(
                    source="fx_rates", provider="fred",
                    source_record_id=f"DEXCHUS@{d.isoformat()}",
                    published_at=datetime.combine(d, datetime.min.time(), tzinfo=UTC),
                    observed_at=as_of, retrieved_at=datetime.now(UTC),
                    as_of_date=d, quality_score=Decimal("0.98"),
                    quality_status="VERIFIED", transform_version=self.TRANSFORM_VERSION,
                ),
            ))
        return out

    async def get_macro_series(
        self,
        series_id: str,
        start: date,
        end: date,
    ) -> list[dict]:
        """扩展方法（v0.1 可选）：FRED 宏观序列原始观测（MACRO_SERIES 域）。"""
        return await self._observations(series_id, start, end)

    async def get_index_history(
        self, index_id: UUID, start: date, end: date,
    ) -> list[IndexBarResult]:
        raise ProviderDataError("fred 不支持 INDEX_QUOTE（yahoo primary / tushare A 股）")

    async def get_index_valuation(
        self, index_id: UUID, start: date, end: date,
    ) -> list[IndexValuationResult]:
        raise ProviderDataError("fred 不支持 INDEX_VALUATION（A 股 legulegu；美股 spike 后 PENDING）")
