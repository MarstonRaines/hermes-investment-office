# =====================================================================
# backend/app/providers/registry.py —— 冻结：ProviderRegistry（TS-05 §3.2）
#
# 声明式注册表：实现模块底部注册（或装饰器），启动时收集。
# 注册即声明能力（capabilities 类属性）。
#
# 架构测试 A4：注册表内容 ↔ provider-capability.yaml ↔ 实现能力 三方一致。
# fallback 链由 provider-capability.yaml 定义（primary → fallback），
# 本注册表负责把 provider 名解析为实现类。
# =====================================================================
from __future__ import annotations

from app.providers.capability_matrix import CapabilityMatrix
from app.providers.contracts.base import (
    BaseProvider,
    ProviderCapability,
    ProviderConfigError,
)

__all__ = ["ProviderRegistry"]


class ProviderRegistry:
    """进程内单例。注册内容必须与 provider-capability.md 一致（架构测试校验）。"""

    def __init__(self, matrix: CapabilityMatrix | None = None) -> None:
        self._impls: dict[str, type[BaseProvider]] = {}
        self._matrix = matrix

    # ---- 矩阵 ----

    def set_matrix(self, matrix: CapabilityMatrix) -> None:
        self._matrix = matrix

    def matrix(self) -> CapabilityMatrix | None:
        return self._matrix

    # ---- 注册 ----

    def register(self, provider_cls: type[BaseProvider]) -> None:
        name = provider_cls.provider_name
        if name in self._impls:
            raise ProviderConfigError(f"duplicate provider: {name}")
        self._impls[name] = provider_cls

    def register_all(self, provider_classes: list[type[BaseProvider]]) -> None:
        for cls in provider_classes:
            self.register(cls)

    # ---- 查询 ----

    def get(self, provider_name: str) -> type[BaseProvider]:
        if provider_name not in self._impls:
            raise ProviderConfigError(f"unknown provider: {provider_name}")
        return self._impls[provider_name]

    def all(self) -> dict[str, type[BaseProvider]]:
        return dict(self._impls)

    def providers_for(self, capability: ProviderCapability) -> list[type[BaseProvider]]:
        """实现该能力的所有 provider（按 matrix 角色顺序；无 matrix 时按注册序）。"""
        if self._matrix is not None:
            domain = self._matrix.domain(capability)
            if domain is not None:
                ordered = [n for n in domain.provider_names() if n in self._impls]
                return [self._impls[n] for n in ordered]
        return [cls for cls in self._impls.values() if capability in cls.capabilities]

    def primary_for(self, capability: ProviderCapability) -> type[BaseProvider]:
        """matrix primary → 实现类。"""
        if self._matrix is not None:
            domain = self._matrix.domain(capability)
            if domain is not None and domain.primary:
                return self.get(domain.primary)
        # 无 matrix 时按注册序取第一个声明能力的
        impls = self.providers_for(capability)
        if not impls:
            raise ProviderConfigError(f"no provider implements {capability.value}")
        return impls[0]

    def fallback_chain(self, capability: ProviderCapability) -> list[type[BaseProvider]]:
        """primary → secondary → ...（由 provider-capability.yaml 定义，TS-05 §5.1）。

        仅返回已注册的实现类；若矩阵链中某 provider 未注册且其域 spike_status 要求
        实现（VERIFIED/PARTIAL），抛 ProviderConfigError（架构测试 A4 的运行时体现）。
        """
        if self._matrix is None:
            return self.providers_for(capability)
        names = self._matrix.chain_for(capability)
        impls: list[type[BaseProvider]] = []
        for name in names:
            if name in self._impls:
                impls.append(self._impls[name])
            else:
                domain = self._matrix.domain(capability)
                status = domain.spike_status if domain else "VERIFIED"
                if status in ("VERIFIED", "PARTIAL"):
                    raise ProviderConfigError(
                        f"capability {capability.value}: 矩阵声明 provider {name!r} 未注册"
                    )
        return impls
