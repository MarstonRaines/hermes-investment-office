# =====================================================================
# tests/unit/test_capability_matrix.py —— provider-capability.yaml 权威源校验
#
# 对应：TS-05 §4（YAML 权威源）、ADR-005 D1（网络三态）、ADR-006（Spike 回流）
# =====================================================================
from __future__ import annotations

import pytest

from app.common.config import settings
from app.providers.capability_matrix import (
    IMPLEMENTED_STATUSES,
    CapabilityMatrix,
    load_capability_matrix,
)
from app.providers.contracts.base import ProviderCapability

MATRIX_PATH = settings.provider_capability_path


@pytest.fixture(scope="module")
def matrix() -> CapabilityMatrix:
    return load_capability_matrix(MATRIX_PATH)


def test_matrix_loads(matrix: CapabilityMatrix) -> None:
    assert matrix.version == "0.1"
    assert matrix.updated_at is not None


def test_matrix_covers_all_14_domains(matrix: CapabilityMatrix) -> None:
    """TS-05 §4.2：矩阵覆盖全部 14 个数据域（含 FILINGS/NEWS 契约域）。"""
    ids = {d.id for d in matrix.domains}
    assert ids == set(ProviderCapability)


def test_every_domain_has_status(matrix: CapabilityMatrix) -> None:
    for d in matrix.domains:
        assert d.spike_status, f"domain {d.id.value} 缺少 spike_status"


def test_domains_providers_declared_in_providers_section(matrix: CapabilityMatrix) -> None:
    """交叉校验：域声明的 provider 必须存在于 providers 节（load 已强制，此处显式复检）。"""
    for d in matrix.domains:
        for name in d.provider_names():
            assert name in matrix.providers, f"{d.id.value}: provider {name} 未声明"


def test_network_proxy_modes_valid(matrix: CapabilityMatrix) -> None:
    """ADR-005 D1：proxy 三态（direct / env / http(s)://host:port）。"""
    for name, p in matrix.providers.items():
        proxy = p.network.proxy
        assert proxy in ("direct", "env") or proxy.startswith(("http://", "https://")), (
            f"provider {name}: invalid proxy {proxy!r}"
        )


def test_eastmoney_uses_explicit_proxy(matrix: CapabilityMatrix) -> None:
    """ADR-005：eastmoney 直连被阻，必须显式代理。"""
    assert matrix.providers["akshare_eastmoney"].network.proxy.startswith("http://")


def test_cn_daily_fallback_is_sina(matrix: CapabilityMatrix) -> None:
    """ADR-005 D3：A 股日线 fallback = 新浪源。"""
    d = matrix.domain(ProviderCapability.CN_DAILY_QUOTE)
    assert d is not None
    assert d.primary == "tushare"
    assert d.fallback == ["akshare_sina"]


def test_index_weight_deferred(matrix: CapabilityMatrix) -> None:
    """ADR-006：INDEX_WEIGHT 下调（乐咕锁定后免自聚合）。"""
    d = matrix.domain(ProviderCapability.INDEX_WEIGHT)
    assert d is not None
    assert d.spike_status == "DEFERRED"
    assert d.primary is None


def test_index_valuation_locked_to_legulegu(matrix: CapabilityMatrix) -> None:
    """S6 实测：INDEX_VALUATION primary = legulegu。"""
    d = matrix.domain(ProviderCapability.INDEX_VALUATION)
    assert d is not None
    assert d.primary == "legulegu"
    assert d.spike_status == "VERIFIED"


def test_fx_primary_yahoo_aux_fred(matrix: CapabilityMatrix) -> None:
    """S7 实测：FX primary = yahoo，fred 作交叉验证。"""
    d = matrix.domain(ProviderCapability.FX_RATES)
    assert d is not None
    assert d.primary == "yahoo"
    assert "fred" in d.auxiliary


def test_implemented_domains_have_nonempty_chain(matrix: CapabilityMatrix) -> None:
    """VERIFIED/PARTIAL 域必须有可取数链（架构测试 A4 前置）。"""
    for d in matrix.domains:
        if d.spike_status in IMPLEMENTED_STATUSES:
            assert d.provider_names(), f"{d.id.value}: implemented 域缺少 provider"
