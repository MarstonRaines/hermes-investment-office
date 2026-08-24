"""ETF-facing adapter over DataGateway.

The ETF domain receives this adapter through application assembly. It never
imports provider implementations or calls them directly.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import date
from typing import Any
from uuid import UUID

from app.common.gateway import GatewayFetch
from app.providers.contracts.base import ProviderCapability
from app.providers.gateway import DataGateway

__all__ = ["ETFDataGateway"]


class ETFDataGateway:
    """Translate ETF domain operations into DataGateway capability calls."""

    def __init__(self, gateway: DataGateway) -> None:
        self._gateway = gateway

    async def _fetch(
        self,
        capability: ProviderCapability,
        fetcher: Callable[[Any], Awaitable[list[Any]]],
        instrument_id: UUID | None = None,
    ) -> GatewayFetch[Any]:
        rows, decision = await self._gateway.fetch_with_fallback(
            capability, fetcher, instrument_id=instrument_id
        )
        return GatewayFetch(
            rows=rows,
            actual_provider=decision.actual_provider,
            requested_provider=decision.requested_provider,
            fallback_used=decision.fallback_used,
            fallback_reason=decision.fallback_reason,
        )

    async def fetch_market_price(
        self, instrument_id: UUID, start: date, end: date
    ) -> GatewayFetch[Any]:
        return await self._fetch(
            ProviderCapability.CN_ETF_QUOTE,
            lambda provider: provider.get_price_history(instrument_id, start, end),
            instrument_id,
        )

    async def fetch_nav_history(self, instrument_id: UUID) -> GatewayFetch[Any]:
        return await self._fetch(
            ProviderCapability.FUND_NAV,
            lambda provider: provider.get_nav_history(instrument_id),
            instrument_id,
        )

    async def fetch_holdings(self, instrument_id: UUID) -> GatewayFetch[Any]:
        return await self._fetch(
            ProviderCapability.FUND_HOLDINGS,
            lambda provider: provider.get_holding_snapshots(instrument_id),
            instrument_id,
        )

    async def fetch_quota(self, instrument_id: UUID) -> GatewayFetch[Any]:
        rows, decision = await self._gateway.fetch_with_fallback(
            ProviderCapability.QUOTA_STATUS,
            lambda provider: provider.get_quota_status(instrument_id),
            instrument_id=instrument_id,
        )
        # ETFProvider exposes quota as one current event, while the shared
        # Gateway result is intentionally list-shaped for job batching.
        return GatewayFetch(
            rows=rows if isinstance(rows, list) else [rows],
            actual_provider=decision.actual_provider,
            requested_provider=decision.requested_provider,
            fallback_used=decision.fallback_used,
            fallback_reason=decision.fallback_reason,
        )

    async def fetch_index_history(
        self, index_id: UUID, start: date, end: date
    ) -> GatewayFetch[Any]:
        return await self._fetch(
            ProviderCapability.INDEX_QUOTE,
            lambda provider: provider.get_index_history(index_id, start, end),
            index_id,
        )

    async def fetch_fx_rates(
        self, start: date, end: date
    ) -> GatewayFetch[Any]:
        return await self._fetch(
            ProviderCapability.FX_RATES,
            lambda provider: provider.get_fx_rates("USD", "CNY", start, end),
        )

    async def fetch_index_valuation(
        self, index_id: UUID, start: date, end: date
    ) -> GatewayFetch[Any]:
        return await self._fetch(
            ProviderCapability.INDEX_VALUATION,
            lambda provider: provider.get_index_valuation(index_id, start, end),
            index_id,
        )
