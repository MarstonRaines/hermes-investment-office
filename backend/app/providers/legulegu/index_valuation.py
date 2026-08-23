# =====================================================================
# backend/app/providers/legulegu/index_valuation.py —— LeguleguIndexValuationProvider
#
# S6 实测锁定：INDEX_VALUATION primary = 乐咕乐股（经 AkShare stock_index_pe_lg /
# stock_index_pb_lg，直连正常）。PE 取 ttmPe，PB 取 ttmPb（缺失退 static/均值口径，
# 列名按 akshare 版本防御式选取）。禁止伪造历史分位（GOLD-ETF-007/008）。
# =====================================================================
from __future__ import annotations

import asyncio
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
from app.providers.legulegu.metadata import LeguleguMeta

__all__ = ["LeguleguIndexValuationProvider"]

try:
    import akshare as ak
except ImportError:  # pragma: no cover
    ak = None  # type: ignore[assignment]


class LeguleguIndexValuationProvider(MacroProvider):
    provider_name: ClassVar[str] = "legulegu"
    display_name: ClassVar[str] = "乐咕乐股（legulegu）"
    capabilities = frozenset({ProviderCapability.INDEX_VALUATION})
    default_role = ProviderRole.PRIMARY
    quality_tier = QualityTier.TIER_2
    known_limits = LeguleguMeta.known_limits

    TRANSFORM_VERSION = "index-valuation-normalizer/0.1.0"

    def __init__(self, config=None, symbol_resolver=None) -> None:
        self.config = config
        self._resolve = symbol_resolver

    async def health_check(self) -> ProviderHealth:
        return ProviderHealth(provider=self.provider_name, status="HEALTHY", checked_at=datetime.now())

    async def quality_report(self) -> ProviderQualityReport:
        return ProviderQualityReport(
            provider=self.provider_name, tier=self.quality_tier, quality_score=Decimal("0.92"),
        )

    def _symbol(self, index_id: UUID) -> str:
        if self._resolve is None:
            raise ProviderConfigError("legulegu: symbol_resolver 未注入")
        symbol = self._resolve(index_id)
        if not symbol:
            raise ProviderConfigError(f"legulegu: instrument {index_id} 无 symbol 映射（如 '沪深300'）")
        return symbol   # 乐咕用指数中文名（provider_symbols 数据决定）

    async def get_index_valuation(
        self,
        index_id: UUID,
        start: date,
        end: date,
    ) -> list[IndexValuationResult]:
        if ak is None:
            raise ProviderConfigError("akshare 未安装")
        symbol = self._symbol(index_id)
        try:
            pe_df = await asyncio.to_thread(ak.stock_index_pe_lg, symbol=symbol)
            pb_df = await asyncio.to_thread(ak.stock_index_pb_lg, symbol=symbol)
        except Exception as exc:  # noqa: BLE001 —— akshare 异常类型不稳定
            raise ProviderDataError(f"legulegu: {symbol} 拉取失败: {exc}") from exc

        pe_map = _to_date_map(pe_df, ("ttmPe", "平均市盈率", "pe", "静态市盈率"))
        pb_map = _to_date_map(pb_df, ("ttmPb", "平均市净率", "pb", "静态市净率"))

        out: list[IndexValuationResult] = []
        all_dates = sorted(set(pe_map) | set(pb_map))
        for d in all_dates:
            if not (start <= d <= end):
                continue
            out.append(IndexValuationResult(
                index_id=index_id, as_of_date=d,
                pe=pe_map.get(d), pb=pb_map.get(d),
                source="legulegu",
                provenance=ProvenanceEnvelope(
                    source="index_valuation", provider="legulegu",
                    source_record_id=f"legulegu@{d.isoformat()}",
                    observed_at=datetime.combine(d, datetime.min.time(), tzinfo=UTC),
                    retrieved_at=datetime.now(UTC),
                    as_of_date=d, quality_score=Decimal("0.92"),
                    quality_status="ACCEPTABLE", transform_version=self.TRANSFORM_VERSION,
                ),
            ))
        return out

    async def get_index_history(
        self, index_id: UUID, start: date, end: date,
    ) -> list[IndexBarResult]:
        raise ProviderDataError("legulegu 不支持 INDEX_QUOTE（yahoo/tushare）")

    async def get_fx_rates(
        self, base_currency: str, quote_currency: str, start: date, end: date,
    ) -> list[FxRateResult]:
        raise ProviderDataError("legulegu 不支持 FX_RATES（yahoo/fred）")


def _to_date_map(df, candidate_cols: tuple[str, ...]) -> dict[date, Decimal]:
    """把 akshare DataFrame 按日期列 → 数值列映射为 dict；列名防御式选取。"""
    if df is None or df.empty:
        return {}
    date_col = None
    for c in ("日期", "date", "trade_date"):
        if c in df.columns:
            date_col = c
            break
    if date_col is None:
        return {}
    val_col = None
    for c in candidate_cols:
        if c in df.columns:
            val_col = c
            break
    if val_col is None:
        return {}
    out: dict[date, Decimal] = {}
    for _, r in df.iterrows():
        d_raw = r[date_col]
        if isinstance(d_raw, str):
            try:
                d = date.fromisoformat(d_raw[:10])
            except ValueError:
                continue
        elif hasattr(d_raw, "date"):
            d = d_raw.date()
        else:
            continue
        v = pct_or_none(r[val_col])
        if v is not None:
            out[d] = Decimal(str(v))
    return out
