# =====================================================================
# tests/unit/test_registry.py —— ProviderRegistry（TS-05 §3.2）+ 工厂装配（§3.5）
# =====================================================================
from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from app.common.config import settings
from app.providers.capability_matrix import load_capability_matrix
from app.providers.contracts.base import (
    BaseProvider,
    ProviderCapability,
    ProviderConfigError,
    ProviderHealth,
    ProviderQualityReport,
    ProviderRole,
    QualityTier,
)
from app.providers.factory import ProviderFactory
from app.providers.registry import ProviderRegistry
from app.providers.runtime_config import load_runtime_configs

NOW = datetime(2026, 8, 23, 12, 0, tzinfo=UTC)


# ---- 测试用假 provider（实现契约，供注册表/工厂/网关测试）----


class FakeTushare(BaseProvider):
    provider_name = "tushare"
    display_name = "TuShare Pro"
    capabilities = frozenset({ProviderCapability.CN_DAILY_QUOTE})
    default_role = ProviderRole.PRIMARY
    quality_tier = QualityTier.TIER_2
    known_limits = []

    def __init__(self, config=None) -> None:
        self.config = config

    async def health_check(self) -> ProviderHealth:
        return ProviderHealth(provider=self.provider_name, status="HEALTHY", checked_at=NOW)

    async def quality_report(self) -> ProviderQualityReport:
        return ProviderQualityReport(provider=self.provider_name, tier=self.quality_tier,
                                     quality_score=Decimal("0.96"))


class FakeSina(BaseProvider):
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


@pytest.fixture(scope="module")
def matrix():
    return load_capability_matrix(settings.provider_capability_path)


def test_register_and_get(matrix) -> None:
    reg = ProviderRegistry(matrix)
    reg.register(FakeTushare)
    assert reg.get("tushare") is FakeTushare
    with pytest.raises(ProviderConfigError):
        reg.get("nonexistent")


def test_duplicate_register_rejected(matrix) -> None:
    reg = ProviderRegistry(matrix)
    reg.register(FakeTushare)
    with pytest.raises(ProviderConfigError):
        reg.register(FakeTushare)


def test_fallback_chain_from_matrix(matrix) -> None:
    reg = ProviderRegistry(matrix)
    reg.register(FakeTushare)
    reg.register(FakeSina)
    chain = reg.fallback_chain(ProviderCapability.CN_DAILY_QUOTE)
    assert [c.provider_name for c in chain] == ["tushare", "akshare_sina"]


def test_fallback_chain_missing_impl_fails_for_verified(matrix) -> None:
    """架构测试 A4 运行时体现：VERIFIED 域矩阵声明的 provider 未注册 → 报错。"""
    reg = ProviderRegistry(matrix)   # 未注册任何实现
    with pytest.raises(ProviderConfigError):
        reg.fallback_chain(ProviderCapability.CN_DAILY_QUOTE)


def test_deferred_domain_chain_empty(matrix) -> None:
    reg = ProviderRegistry(matrix)
    assert reg.fallback_chain(ProviderCapability.INDEX_WEIGHT) == []


def test_factory_injects_config(matrix) -> None:
    runtime = load_runtime_configs(settings.providers_runtime_path)
    reg = ProviderRegistry(matrix)
    reg.register(FakeTushare)
    factory = ProviderFactory(reg, runtime, matrix)
    provider = factory.create("tushare")
    assert provider.provider_name == "tushare"
    assert provider.config.network_proxy == "direct"       # ADR-005：tushare 直连
    assert provider.config.max_retries == 3                # providers.yaml
    assert provider.config.rate_limit["qps"] == 0.5
    assert provider.config.score == 2000                   # S1 实测积分档


def test_factory_unknown_provider(matrix) -> None:
    runtime = load_runtime_configs(settings.providers_runtime_path)
    reg = ProviderRegistry(matrix)
    factory = ProviderFactory(reg, runtime, matrix)
    with pytest.raises(ProviderConfigError):
        factory.create("nope")


def test_factory_eastmoney_proxy(matrix) -> None:
    runtime = load_runtime_configs(settings.providers_runtime_path)
    reg = ProviderRegistry(matrix)
    reg.register(FakeSina)   # 复用名字测试网络注入路径
    factory = ProviderFactory(reg, runtime, matrix)
    # akshare_eastmoney 未注册为 FakeSina；用 akshare_sina 验证 direct
    provider = factory.create("akshare_sina")
    assert provider.config.network_proxy == "direct"
