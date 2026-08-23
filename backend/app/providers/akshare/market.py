# =====================================================================
# backend/app/providers/akshare/market.py —— AkShareSinaMarketProvider
#
# ADR-005 D3：A 股日线 fallback 首选 = 新浪源 stock_zh_a_daily（含 qfq 前复权），
# 不再依赖 eastmoney。network=direct（S2 实测直连正常）。
# 复权因子：新浪源提供 hfq_factor（后复权因子）/ qfq_factor；缺失 → ProviderDataError
# （诚实缺口，触发链终态 CONFLICT → 人工校准，TS-05 §5.1 ADJ_FACTOR 链）。
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
from app.providers.contracts.market_data import (
    AdjFactorResult,
    AdjustType,
    MarketBarResult,
    MarketDataProvider,
    MarketSnapshotResult,
)

__all__ = ["AkShareSinaMarketProvider"]

try:
    import akshare as ak
except ImportError:  # pragma: no cover
    ak = None  # type: ignore[assignment]


class AkShareSinaMarketProvider(MarketDataProvider):
    provider_name: ClassVar[str] = "akshare_sina"
    display_name: ClassVar[str] = "AkShare（新浪源）"
    capabilities = frozenset(
        {
            ProviderCapability.CN_DAILY_QUOTE,
            ProviderCapability.CN_ETF_QUOTE,
            ProviderCapability.ADJ_FACTOR,
        }
    )
    default_role = ProviderRole.FALLBACK
    quality_tier = QualityTier.TIER_3
    known_limits = [
        "新浪源直连正常（S2）；接口字段以 akshare 版本为准",
        "复权因子依赖 hfq_factor 列；缺失时按 ProviderDataError 走链终态",
    ]

    TRANSFORM_VERSION = "market-normalizer/0.1.0"

    def __init__(self, config=None, symbol_resolver=None) -> None:
        self.config = config
        self._resolve = symbol_resolver

    async def health_check(self) -> ProviderHealth:
        return ProviderHealth(
            provider=self.provider_name, status="HEALTHY", checked_at=datetime.now(),
        )

    async def quality_report(self) -> ProviderQualityReport:
        return ProviderQualityReport(
            provider=self.provider_name, tier=self.quality_tier,
            quality_score=Decimal("0.85"),
        )

    def _symbol(self, instrument_id: UUID) -> str:
        if self._resolve is None:
            raise ProviderConfigError("akshare_sina: symbol_resolver 未注入")
        symbol = self._resolve(instrument_id)
        if not symbol:
            raise ProviderConfigError(f"akshare_sina: instrument {instrument_id} 无 symbol 映射")
        # 新浪源格式：sh600519 / sz000001
        if "." in symbol:
            code, mkt = symbol.split(".")
            return ("sh" if mkt == "SH" else "sz") + code
        return symbol

    async def get_price_history(
        self,
        instrument_id: UUID,
        start: date,
        end: date,
        adjust: AdjustType = AdjustType.NONE,
    ) -> list[MarketBarResult]:
        if ak is None:
            raise ProviderConfigError("akshare 未安装")
        import asyncio

        symbol = self._symbol(instrument_id)
        adjust_map = {AdjustType.NONE: "", AdjustType.FORWARD: "qfq", AdjustType.BACKWARD: "hfq"}
        df = await asyncio.to_thread(
            ak.stock_zh_a_daily, symbol=symbol,
            start_date=start.strftime("%Y%m%d"), end_date=end.strftime("%Y%m%d"),
            adjust=adjust_map[adjust],
        )
        if df is None or df.empty:
            return []
        bars: list[MarketBarResult] = []
        for _, r in df.iterrows():
            td = r["date"]
            if isinstance(td, str):
                td = date.fromisoformat(td)
            elif hasattr(td, "date"):
                td = td.date()
            close = pct_or_none(r.get("close"))
            bars.append(MarketBarResult(
                instrument_id=instrument_id, trade_date=td,
                open=_dec(r.get("open")), high=_dec(r.get("high")),
                low=_dec(r.get("low")), close=_dec(close),
                volume=_dec(r.get("volume")), amount=_dec(r.get("amount")),
                pct_change=_dec(r.get("pct_change")) if "pct_change" in r.index else None,
                currency="CNY", provider="akshare_sina",
                provenance=ProvenanceEnvelope(
                    source="cn_daily_market", provider="akshare_sina",
                    source_record_id=f"akshare_sina@{td.isoformat()}",
                    observed_at=shanghai_15(td), retrieved_at=datetime.now(),
                    as_of_date=td, quality_score=Decimal("0.85"),
                    quality_status="ACCEPTABLE", transform_version=self.TRANSFORM_VERSION,
                    quality_flags=(["ADJUSTED"] if adjust != AdjustType.NONE else []),
                ),
            ))
        return bars

    async def get_market_snapshot(
        self,
        instrument_ids: list[UUID],
        as_of: date,
    ) -> list[MarketSnapshotResult]:
        from datetime import timedelta

        results = []
        for inst in instrument_ids:
            bars = await self.get_price_history(inst, as_of - timedelta(days=45), as_of)
            if not bars:
                results.append(MarketSnapshotResult(
                    instrument_id=inst, as_of=as_of, trade_date=None,
                    provenance=ProvenanceEnvelope(
                        source="cn_daily_market", provider="akshare_sina",
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

    async def get_adj_factors(
        self,
        instrument_id: UUID,
        start: date,
        end: date,
    ) -> list[AdjFactorResult]:
        """新浪复权因子：hfq_factor（后复权因子）优先，缺失 → ProviderDataError。"""
        if ak is None:
            raise ProviderConfigError("akshare 未安装")
        import asyncio

        symbol = self._symbol(instrument_id)
        df = await asyncio.to_thread(
            ak.stock_zh_a_daily, symbol=symbol,
            start_date=start.strftime("%Y%m%d"), end_date=end.strftime("%Y%m%d"),
            adjust="",
        )
        if df is None or df.empty:
            return []
        if "hfq_factor" not in df.columns:
            raise ProviderDataError(
                f"akshare_sina: {symbol} 无 hfq_factor 列（复权因子不可得，走链终态）"
            )
        out: list[AdjFactorResult] = []
        for _, r in df.iterrows():
            td = r["date"]
            if isinstance(td, str):
                td = date.fromisoformat(td)
            elif hasattr(td, "date"):
                td = td.date()
            factor = pct_or_none(r.get("hfq_factor"))
            if factor is None:
                continue
            out.append(AdjFactorResult(
                instrument_id=instrument_id, trade_date=td,
                adj_factor=Decimal(str(factor)),
                provenance=ProvenanceEnvelope(
                    source="cn_adj_factor", provider="akshare_sina",
                    source_record_id=f"akshare_sina@{td.isoformat()}",
                    observed_at=shanghai_15(td), retrieved_at=datetime.now(),
                    as_of_date=td, quality_score=Decimal("0.85"),
                    quality_status="ACCEPTABLE", transform_version=self.TRANSFORM_VERSION,
                ),
            ))
        return out


def _dec(v) -> Decimal | None:
    f = pct_or_none(v)
    return Decimal(str(f)) if f is not None else None
