# =====================================================================
# backend/app/providers/tushare/market.py —— TuShareMarketDataProvider
#
# 覆盖（S1 实测锁定）：
# - CN_DAILY_QUOTE：daily（A 股日线）
# - CN_ETF_QUOTE ：fund_daily（ETF 场内行情，S1 关键发现：ETF 不走 daily）
# - ADJ_FACTOR   ：adj_factor（后复权因子）
# - INDEX_QUOTE  ：index_daily（A 股指数，auxiliary 角色，ADR-006）
#
# 单位换算（TuShare 官方口径）：
# - daily.vol  单位 = 手 → ×100 = 股
# - daily.amount 单位 = 千元 → ×1000 = 元（base_unit=CNY）
# - pct_chg 已是百分比数值（-8.1 表示 -8.1%）
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
from app.providers.contracts.macro import IndexBarResult, MacroProvider
from app.providers.contracts.market_data import (
    AdjFactorResult,
    AdjustType,
    MarketBarResult,
    MarketDataProvider,
    MarketSnapshotResult,
)
from app.providers.tushare.client import TushareClient
from app.providers.tushare.metadata import TuShareMeta

__all__ = ["TuShareMarketDataProvider"]


class TuShareMarketDataProvider(MarketDataProvider, MacroProvider):
    provider_name: ClassVar[str] = "tushare"
    display_name: ClassVar[str] = "TuShare Pro"
    capabilities = frozenset(
        {
            ProviderCapability.CN_DAILY_QUOTE,
            ProviderCapability.CN_ETF_QUOTE,
            ProviderCapability.ADJ_FACTOR,
            ProviderCapability.INDEX_QUOTE,   # A 股指数（auxiliary，ADR-006）
        }
    )
    default_role = ProviderRole.PRIMARY
    quality_tier = QualityTier.TIER_2
    known_limits = TuShareMeta.known_limits

    TRANSFORM_VERSION = "market-normalizer/0.1.0"

    def __init__(self, config=None, symbol_resolver=None, client: TushareClient | None = None) -> None:
        self.config = config
        self._resolve = symbol_resolver
        if client is not None:
            self._client = client
        else:
            token = (config.token if config else None) or ""
            if not token:
                self._client = None
            else:
                self._client = TushareClient(token, timeout=getattr(config, "timeout_seconds", 20.0))

    # ---- BaseProvider ----

    async def health_check(self) -> ProviderHealth:
        if self._client is None:
            raise ProviderConfigError("tushare token 未配置（HERMES_TUSHARE_TOKEN）")
        return ProviderHealth(
            provider=self.provider_name, status="HEALTHY",
            checked_at=datetime.now(), detail={"score": "2000"},
        )

    async def quality_report(self) -> ProviderQualityReport:
        return ProviderQualityReport(
            provider=self.provider_name, tier=self.quality_tier,
            quality_score=Decimal(str(TuShareMeta.quality_score)),
        )

    # ---- 内部 ----

    def _symbol(self, instrument_id: UUID) -> str:
        if self._resolve is None:
            raise ProviderConfigError("tushare: symbol_resolver 未注入")
        symbol = self._resolve(instrument_id)
        if not symbol:
            raise ProviderConfigError(f"tushare: instrument {instrument_id} 无 symbol 映射")
        return symbol

    def _date_range(self, start: date, end: date) -> tuple[str, str]:
        return start.strftime("%Y%m%d"), end.strftime("%Y%m%d")

    @staticmethod
    def _bar_from_row(row, instrument_id: UUID, provider: str, source: str) -> MarketBarResult:
        """TuShare 行情行 → MarketBarResult（单位换算：vol 手→股，amount 千元→元）。"""
        trade_date = date.fromisoformat(str(row["trade_date"]))
        vol_raw = pct_or_none(row.get("vol"))
        amount_raw = pct_or_none(row.get("amount"))
        close = pct_or_none(row.get("close"))
        env = ProvenanceEnvelope(
            source=source,
            provider=provider,
            source_record_id=f"{provider}@{trade_date.isoformat()}",
            observed_at=shanghai_15(trade_date),
            retrieved_at=datetime.now(),
            as_of_date=trade_date,
            quality_score=Decimal(str(TuShareMeta.quality_score)),
            quality_status="VERIFIED",
            transform_version=TuShareMarketDataProvider.TRANSFORM_VERSION,
        )
        return MarketBarResult(
            instrument_id=instrument_id,
            trade_date=trade_date,
            open=_dec(row.get("open")),
            high=_dec(row.get("high")),
            low=_dec(row.get("low")),
            close=_dec(close),
            volume=Decimal(str(vol_raw * 100)) if vol_raw is not None else None,   # 手→股
            amount=Decimal(str(amount_raw * 1000)) if amount_raw is not None else None,  # 千元→元
            pre_close=_dec(row.get("pre_close")),
            pct_change=_dec(row.get("pct_chg")),
            currency="CNY",
            provider=provider,
            provenance=env,
        )

    # ---- MarketDataProvider ----

    async def get_price_history(
        self,
        instrument_id: UUID,
        start: date,
        end: date,
        adjust: AdjustType = AdjustType.NONE,
    ) -> list[MarketBarResult]:
        if self._client is None:
            raise ProviderConfigError("tushare token 未配置")
        symbol = self._symbol(instrument_id)
        start_s, end_s = self._date_range(start, end)
        # S1 关键发现：ETF 行情必须走 fund_daily，daily 对 ETF 返回空。
        # 同 provider 内先 daily 后 fund_daily 属取数口径选择（非换源，TS-05 §5.3 合规）。
        df = await self._client.call("daily", ts_code=symbol, start_date=start_s, end_date=end_s)
        source = "cn_daily_market"
        if df is None or df.empty:
            df = await self._client.call("fund_daily", ts_code=symbol, start_date=start_s, end_date=end_s)
            source = "cn_etf_daily_market"
        if df is None or df.empty:
            return []
        bars = [self._bar_from_row(r, instrument_id, "tushare", source) for _, r in df.iterrows()]
        if adjust != AdjustType.NONE:
            factors = {f.trade_date: f.adj_factor for f in await self.get_adj_factors(instrument_id, start, end)}
            for b in bars:
                if b.trade_date in factors:
                    b.adj_factor = factors[b.trade_date]
                    b.adjusted_close = (b.close * b.adj_factor) if b.close is not None else None
                    if "ADJUSTED" not in b.provenance.quality_flags:
                        b.provenance.quality_flags.append("ADJUSTED")
        return bars

    async def get_market_snapshot(
        self,
        instrument_ids: list[UUID],
        as_of: date,
    ) -> list[MarketSnapshotResult]:
        # 拉近 45 个自然日窗口取最近已完成交易日（v0.1 单机宇宙小，逐标的可接受）
        from datetime import timedelta

        results: list[MarketSnapshotResult] = []
        window_start = as_of - timedelta(days=45)
        for inst in instrument_ids:
            bars = await self.get_price_history(inst, window_start, as_of)
            if not bars:
                results.append(MarketSnapshotResult(
                    instrument_id=inst, as_of=as_of, trade_date=None, provenance=_gap_env(inst),
                ))
                continue
            last = bars[-1]
            results.append(MarketSnapshotResult(
                instrument_id=inst, as_of=as_of, trade_date=last.trade_date,
                close=last.close, pct_change=last.pct_change,
                volume=last.volume, amount=last.amount,
                provenance=last.provenance,
            ))
        return results

    async def get_adj_factors(
        self,
        instrument_id: UUID,
        start: date,
        end: date,
    ) -> list[AdjFactorResult]:
        if self._client is None:
            raise ProviderConfigError("tushare token 未配置")
        symbol = self._symbol(instrument_id)
        start_s, end_s = self._date_range(start, end)
        df = await self._client.call("adj_factor", ts_code=symbol, start_date=start_s, end_date=end_s)
        if df is None or df.empty:
            return []
        out = []
        for _, r in df.iterrows():
            td = date.fromisoformat(str(r["trade_date"]))
            out.append(AdjFactorResult(
                instrument_id=instrument_id, trade_date=td,
                adj_factor=Decimal(str(r["adj_factor"])),
                provenance=ProvenanceEnvelope(
                    source="cn_adj_factor", provider="tushare",
                    source_record_id=f"tushare@{td.isoformat()}",
                    observed_at=shanghai_15(td), retrieved_at=datetime.now(),
                    as_of_date=td, quality_score=Decimal(str(TuShareMeta.quality_score)),
                    quality_status="VERIFIED", transform_version=self.TRANSFORM_VERSION,
                ),
            ))
        return out

    # ---- MacroProvider（A 股指数，auxiliary）----

    async def get_index_history(
        self,
        index_id: UUID,
        start: date,
        end: date,
    ) -> list[IndexBarResult]:
        if self._client is None:
            raise ProviderConfigError("tushare token 未配置")
        symbol = self._symbol(index_id)
        start_s, end_s = self._date_range(start, end)
        df = await self._client.call("index_daily", ts_code=symbol, start_date=start_s, end_date=end_s)
        if df is None or df.empty:
            return []
        out = []
        for _, r in df.iterrows():
            td = date.fromisoformat(str(r["trade_date"]))
            out.append(IndexBarResult(
                index_id=index_id, trade_date=td,
                open=_dec(r.get("open")), high=_dec(r.get("high")),
                low=_dec(r.get("low")), close=_dec(r.get("close")),
                volume=_dec(r.get("vol")), currency="CNY",
                provenance=ProvenanceEnvelope(
                    source="cn_index_quote", provider="tushare",
                    source_record_id=f"tushare@{td.isoformat()}",
                    observed_at=shanghai_15(td), retrieved_at=datetime.now(),
                    as_of_date=td, quality_score=Decimal(str(TuShareMeta.quality_score)),
                    quality_status="VERIFIED", transform_version=self.TRANSFORM_VERSION,
                ),
            ))
        return out

    async def get_fx_rates(self, base_currency: str, quote_currency: str, start: date, end: date):
        raise ProviderDataError("tushare 不支持 FX_RATES（Yahoo primary / FRED 交叉验证）")

    async def get_index_valuation(self, index_id: UUID, start: date, end: date):
        raise ProviderDataError("tushare 不支持 INDEX_VALUATION（legulegu primary）")


def _dec(v) -> Decimal | None:
    f = pct_or_none(v)
    return Decimal(str(f)) if f is not None else None


def _gap_env(instrument_id: UUID) -> ProvenanceEnvelope:
    """无行情（停牌/未上市/无数据）时的显式缺口 provenance（TS-04 §5.3 缺口语义）。"""
    return ProvenanceEnvelope(
        source="cn_daily_market", provider="tushare",
        observed_at=datetime.now(), retrieved_at=datetime.now(),
        quality_score=Decimal("0.0"), quality_status="STALE",
        quality_flags=["NO_BAR"], transform_version=TuShareMarketDataProvider.TRANSFORM_VERSION,
    )
