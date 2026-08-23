# =====================================================================
# backend/app/providers/tushare/etf.py —— TuShareEtfProvider（FUND_NAV）
#
# S1/S8 实测：fund_nav 2000 积分档可用；nav_date（估值日）与 ann_date（公告日）
# 双日期分离，直接对应 QDII T+1 时序（TS-02 §4.4）。
# =====================================================================
from __future__ import annotations

from datetime import datetime
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
from app.providers.tushare.client import TushareClient
from app.providers.tushare.metadata import TuShareMeta

__all__ = ["TuShareEtfProvider"]


class TuShareEtfProvider(ETFProvider):
    provider_name: ClassVar[str] = "tushare"
    display_name: ClassVar[str] = "TuShare Pro"
    capabilities = frozenset({ProviderCapability.FUND_NAV})
    default_role = ProviderRole.PRIMARY
    quality_tier = QualityTier.TIER_2
    known_limits = TuShareMeta.known_limits

    TRANSFORM_VERSION = "fund-nav-normalizer/0.1.0"

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

    async def get_nav_history(self, instrument_id: UUID) -> list[NavResult]:
        if self._client is None:
            raise ProviderConfigError("tushare token 未配置")
        symbol = self._symbol(instrument_id)
        df = await self._client.call("fund_nav", ts_code=symbol)
        if df is None or df.empty:
            return []
        out: list[NavResult] = []
        for _, r in df.iterrows():
            nav_date = r.get("nav_date")
            nav = pct_or_none(r.get("unit_nav"))
            if not nav_date or nav is None:
                continue   # 缺失关键字段的行跳过（合法缺口不抛错）
            from datetime import date

            nd = date.fromisoformat(str(nav_date))
            ann = r.get("ann_date")
            published_at = None
            if ann:
                published_at = datetime.fromisoformat(f"{ann}T00:00:00+08:00")
            out.append(NavResult(
                instrument_id=instrument_id, nav_date=nd, nav=Decimal(str(nav)),
                published_at=published_at, retrieved_at=datetime.now(),
                provenance=ProvenanceEnvelope(
                    source="cn_fund_nav", provider="tushare",
                    source_record_id=f"tushare@{nd.isoformat()}",
                    published_at=published_at,
                    observed_at=shanghai_15(nd), retrieved_at=datetime.now(),
                    as_of_date=nd, quality_score=Decimal(str(TuShareMeta.quality_score)),
                    quality_status="VERIFIED", transform_version=self.TRANSFORM_VERSION,
                ),
            ))
        return out

    async def get_holding_snapshots(self, instrument_id: UUID):
        raise ProviderDataError("tushare 不支持 FUND_HOLDINGS（akshare_eastmoney primary）")

    async def get_quota_status(self, instrument_id: UUID):
        raise ProviderDataError("tushare 不支持 QUOTA_STATUS（事件状态，人工/半自动录入）")
