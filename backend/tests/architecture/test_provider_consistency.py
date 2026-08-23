# =====================================================================
# tests/architecture/test_provider_consistency.py —— 架构测试 A4（TS-05 §4.3）
#
# 三方一致：provider-capability.yaml（权威源）↔ registry.py（注册表）
# ↔ 实现类 capabilities（行为）逐域校验。任何不一致 = 架构违规
# （"报告写一份、实现按另一套"是施工纪律违规，冻结规范 §47）。
# =====================================================================
from __future__ import annotations

import pytest

from app.common.config import settings
from app.providers.bootstrap import register_all_providers
from app.providers.capability_matrix import (
    IMPLEMENTED_STATUSES,
    load_capability_matrix,
)
from app.providers.registry import ProviderRegistry

MATRIX_PATH = settings.provider_capability_path


@pytest.fixture(scope="module")
def registry() -> ProviderRegistry:
    matrix = load_capability_matrix(MATRIX_PATH)
    reg = ProviderRegistry(matrix)
    register_all_providers(reg)
    return reg


def test_matrix_loads_without_error() -> None:
    load_capability_matrix(MATRIX_PATH)


def test_every_registered_provider_declared_in_yaml(registry: ProviderRegistry) -> None:
    """实现侧 → YAML：注册的 provider 必须在 provider-capability.yaml providers 节声明。"""
    matrix = registry.matrix()
    assert matrix is not None
    declared = set(matrix.providers)
    registered = set(registry.all())
    assert registered <= declared, f"未在 YAML 声明的实现: {registered - declared}"


def test_every_yaml_implemented_provider_registered(registry: ProviderRegistry) -> None:
    """YAML → 实现侧：VERIFIED/PARTIAL 域的 provider 必须已注册。"""
    matrix = registry.matrix()
    for d in matrix.domains:
        if d.spike_status in IMPLEMENTED_STATUSES:
            for name in d.provider_names():
                assert name in registry.all(), f"{d.id.value}: 矩阵声明 {name} 但未注册"


def test_capability_declared_by_implementation(registry: ProviderRegistry) -> None:
    """YAML 域 → 实现能力：域声明的 provider 必须声明该能力（行为一致性）。"""
    matrix = registry.matrix()
    for d in matrix.domains:
        if d.spike_status not in IMPLEMENTED_STATUSES:
            continue
        for name in d.provider_names():
            cls = registry.get(name)
            assert d.id in cls.capabilities, (
                f"{name} 声明能力 {sorted(c.value for c in cls.capabilities)} 但矩阵域 {d.id.value} 需要"
            )


def test_deferred_domains_have_no_implemented_chain(registry: ProviderRegistry) -> None:
    """DEFERRED 域（INDEX_WEIGHT）不允许悄悄实现并绕过 ADR。"""
    matrix = registry.matrix()
    for d in matrix.domains:
        if d.spike_status == "DEFERRED":
            for name in d.provider_names():
                assert name not in registry.all(), f"{d.id.value}: {name} 不应注册（DEFERRED）"


def test_every_impl_class_capabilities_covered_by_matrix(registry: ProviderRegistry) -> None:
    """实现侧 → YAML：实现类声明的每个能力必须存在于矩阵某域（无游离能力）。"""
    matrix = registry.matrix()
    domain_ids = {d.id for d in matrix.domains}
    for name, cls in registry.all().items():
        for cap in cls.capabilities:
            assert cap in domain_ids, f"{name} 声明能力 {cap.value} 但矩阵无此域"


def test_fallback_chain_resolves_for_all_verified_domains(registry: ProviderRegistry) -> None:
    """运行时：VERIFIED 域的 fallback 链必须可解析（primary → fallback 全注册）。"""
    matrix = registry.matrix()
    for d in matrix.domains:
        if d.spike_status == "VERIFIED":
            chain = registry.fallback_chain(d.id)
            assert chain, f"{d.id.value}: fallback 链为空"
            if d.primary:
                assert chain[0].provider_name == d.primary
