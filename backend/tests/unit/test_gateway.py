# =====================================================================
# tests/unit/test_gateway.py —— DataGateway fallback 决策（TS-05 §5.2，冻结）
#
# 覆盖：ACC-M1-004（可追溯 Provider / fallback 记录）与
#       ACC-M1-006（无 silent fallback）的单元层。
# =====================================================================
from __future__ import annotations

import asyncio
from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import uuid4

import pytest

from app.common.config import settings
from app.common.enums import DataQualityStatus
from app.providers.capability_matrix import load_capability_matrix
from app.providers.contracts.base import (
    BaseProvider,
    ProvenanceEnvelope,
    ProviderAuthError,
    ProviderCapability,
    ProviderConfigError,
    ProviderHealth,
    ProviderQualityReport,
    ProviderRole,
    ProviderTimeout,
    QualityTier,
)
from app.providers.contracts.market_data import MarketBarResult
from app.providers.gateway import (
    FALLBACK_QUALITY_ADJUSTMENT,
    DataGateway,
    DataUnavailable,
    FallbackDecision,
)
from app.providers.registry import ProviderRegistry

NOW = datetime(2026, 8, 23, 12, 0, tzinfo=UTC)
INST_ID = uuid4()


def _env(provider: str, score: str = "0.96") -> ProvenanceEnvelope:
    return ProvenanceEnvelope(
        source="cn_daily_market", provider=provider,
        observed_at=NOW, retrieved_at=NOW,
        quality_score=Decimal(score),
        quality_status=DataQualityStatus.VERIFIED,
        transform_version="market-normalizer/0.1.0",
    )


def _bar(provider: str) -> MarketBarResult:
    return MarketBarResult(
        instrument_id=INST_ID, trade_date=date(2026, 8, 21),
        close=Decimal("100"), provider=provider, provenance=_env(provider),
    )


# ---- 可控假 provider ----


class OkTushare(BaseProvider):
    provider_name = "tushare"
    display_name = "TuShare Pro"
    capabilities = frozenset({ProviderCapability.CN_DAILY_QUOTE})
    default_role = ProviderRole.PRIMARY
    quality_tier = QualityTier.TIER_2
    known_limits = []
    fail_with: type[Exception] | None = None

    def __init__(self, config=None) -> None:
        self.config = config

    async def health_check(self) -> ProviderHealth:
        return ProviderHealth(provider=self.provider_name, status="HEALTHY", checked_at=NOW)

    async def quality_report(self) -> ProviderQualityReport:
        return ProviderQualityReport(provider=self.provider_name, tier=self.quality_tier,
                                     quality_score=Decimal("0.96"))


class FailingTushare(OkTushare):
    """primary 失败：用于 fallback 测试。"""

    provider_name = "tushare"


class OkSina(BaseProvider):
    provider_name = "akshare_sina"
    display_name = "AkShare（新浪源）"
    capabilities = frozenset({ProviderCapability.CN_DAILY_QUOTE})
    default_role = ProviderRole.FALLBACK
    quality_tier = QualityTier.TIER_3
    known_limits = []

    def __init__(self, config=None) -> None:
        self.config = config

    async def health_check(self) -> ProviderHealth:
        return ProviderHealth(provider=self.provider_name, status="HEALTHY", checked_at=NOW)

    async def quality_report(self) -> ProviderQualityReport:
        return ProviderQualityReport(provider=self.provider_name, tier=self.quality_tier,
                                     quality_score=Decimal("0.85"))


def _make_registry(primary_cls, fallback_cls) -> ProviderRegistry:
    matrix = load_capability_matrix(settings.provider_capability_path)
    reg = ProviderRegistry(matrix)
    reg.register(primary_cls)
    reg.register(fallback_cls)
    return reg


def _fetcher(fail_on=None):
    """返回一个 fetcher：按 provider_name 决定抛错还是成功。

    fail_on: dict[provider_name, Exception 类型]
    """

    async def fetch(p):
        if fail_on and p.provider_name in fail_on:
            raise fail_on[p.provider_name]("boom")
        return [_bar(p.provider_name)]

    return fetch


def test_primary_success_no_fallback() -> None:
    async def run() -> None:
        reg = _make_registry(OkTushare, OkSina)
        gateway = DataGateway(reg)
        result, decision = await gateway.fetch_with_fallback(
            ProviderCapability.CN_DAILY_QUOTE, _fetcher(None))
        assert decision.fallback_used is False
        assert decision.attempts == ["tushare"]
        assert decision.requested_provider == "tushare"
        assert decision.actual_provider == "tushare"
        assert result[0].provenance.quality_flags == []   # 无 FALLBACK_USED

    asyncio.run(run())


def test_fallback_used_and_marked() -> None:
    async def run() -> None:
        reg = _make_registry(OkTushare, OkSina)
        gateway = DataGateway(reg)
        fail_on = {"tushare": ProviderTimeout}
        result, decision = await gateway.fetch_with_fallback(
            ProviderCapability.CN_DAILY_QUOTE, _fetcher(fail_on),
            max_retries=0, backoff_base=0.01)
        assert decision.fallback_used is True
        assert decision.fallback_reason == "PRIMARY_TIMEOUT"
        assert decision.attempts == ["tushare", "akshare_sina"]
        assert decision.actual_provider == "akshare_sina"
        assert decision.quality_adjustment == FALLBACK_QUALITY_ADJUSTMENT["PRIMARY_TIMEOUT"]
        env = result[0].provenance
        assert "FALLBACK_USED" in env.quality_flags
        assert env.fallback_used is True
        assert env.requested_provider == "tushare"
        assert env.fallback_reason == "PRIMARY_TIMEOUT"
        # 质量衰减：0.96 * 0.95 = 0.912
        assert env.quality_score == Decimal("0.9120")

    asyncio.run(run())


def test_all_providers_fail_raises_data_unavailable() -> None:
    async def run() -> None:
        reg = _make_registry(OkTushare, OkSina)
        gateway = DataGateway(reg)
        fail_on = {"tushare": ProviderTimeout, "akshare_sina": ProviderTimeout}
        with pytest.raises(DataUnavailable) as ei:
            await gateway.fetch_with_fallback(
                ProviderCapability.CN_DAILY_QUOTE, _fetcher(fail_on),
                max_retries=0, backoff_base=0.01)
        d = ei.value.decision
        assert d.attempts == ["tushare", "akshare_sina"]
        assert d.fallback_reason == "PRIMARY_TIMEOUT"
        assert d.fallback_used is True

    asyncio.run(run())


def test_auth_error_no_fallback() -> None:
    """TS-05 §2.0.1：token/鉴权失败不重试、不 fallback，直接上抛。"""
    async def run() -> None:
        reg = _make_registry(OkTushare, OkSina)
        gateway = DataGateway(reg)
        fail_on = {"tushare": ProviderAuthError}
        with pytest.raises(ProviderAuthError):
            await gateway.fetch_with_fallback(
                ProviderCapability.CN_DAILY_QUOTE, _fetcher(fail_on),
                max_retries=3, backoff_base=0.01)

    asyncio.run(run())


def test_audit_sink_called_on_fallback() -> None:
    async def run() -> None:
        reg = _make_registry(OkTushare, OkSina)
        events: list[FallbackDecision] = []

        async def sink(capability, decision, instrument_id):
            events.append(decision)

        gateway = DataGateway(reg, audit_sink=sink)
        fail_on = {"tushare": ProviderTimeout}
        await gateway.fetch_with_fallback(
            ProviderCapability.CN_DAILY_QUOTE, _fetcher(fail_on),
            max_retries=0, backoff_base=0.01)
        assert len(events) == 1
        assert events[0].fallback_used is True
        assert events[0].fallback_reason == "PRIMARY_TIMEOUT"

    asyncio.run(run())


def test_no_fallback_chain_raises() -> None:
    async def run() -> None:
        matrix = load_capability_matrix(settings.provider_capability_path)
        reg = ProviderRegistry(matrix)   # 空注册表：DEFERRED 域链为空
        gateway = DataGateway(reg)
        with pytest.raises(DataUnavailable) as ei:
            await gateway.fetch_with_fallback(
                ProviderCapability.INDEX_WEIGHT, _fetcher(None))
        assert ei.value.decision.fallback_reason == "NO_FALLBACK"

    asyncio.run(run())


def test_gateway_rejects_unregistered_verified_provider() -> None:
    async def run() -> None:
        matrix = load_capability_matrix(settings.provider_capability_path)
        reg = ProviderRegistry(matrix)   # CN_DAILY_QUOTE 是 VERIFIED，但未注册实现
        gateway = DataGateway(reg)
        with pytest.raises(ProviderConfigError):
            await gateway.fetch_with_fallback(
                ProviderCapability.CN_DAILY_QUOTE, _fetcher(None))

    asyncio.run(run())
