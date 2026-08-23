# =====================================================================
# backend/app/providers/factory.py —— create_provider 工厂（TS-05 §3.2/§3.5，冻结）
#
# 三层依赖注入：
#   registry（声明式注册表）→ 实现类
#   providers.yaml（运行参数）→ timeout / retry / rate_limit / score / token_env
#   provider-capability.yaml（ADR-005 D1）→ 网络模式（direct / env / 显式代理）
#
# 规则（冻结）：
# - token 只从环境变量读取（token_env），禁止进代码/进 git；
# - 缺失 token 时 provider 的 health_check() 返回 DOWN + ProviderConfigError，
#   启动告警但不阻塞其他 provider；
# - 配置不进代码：修改 YAML 即可调整行为，重启生效。
# =====================================================================
from __future__ import annotations

import os

from pydantic import BaseModel, Field

from app.providers.capability_matrix import CapabilityMatrix, NetworkConfig
from app.providers.contracts.base import (
    BaseProvider,
    ProviderConfigError,
)
from app.providers.rate_limiter import ProviderRateLimiter
from app.providers.registry import ProviderRegistry
from app.providers.runtime_config import RuntimeProviderConfigs

__all__ = ["ProviderInstanceConfig", "ProviderFactory"]


class ProviderInstanceConfig(BaseModel):
    """注入到每个 provider 实例的完整配置（token 在创建时从环境变量解析）。"""

    name: str
    display_name: str = ""
    network_proxy: str = "direct"      # ADR-005 三态：direct / env / http(s)://host:port
    timeout_seconds: float = 20.0
    max_retries: int = 2
    retry_backoff_base: float = 1.0
    rate_limit: dict = Field(default_factory=lambda: {"qps": 1.0, "burst": 3, "daily_quota": 3000})
    score: int | None = None           # TuShare 积分档位
    token: str | None = None           # 已解析 token（禁止日志输出）

    @property
    def is_token_configured(self) -> bool:
        return bool(self.token)


class ProviderFactory:
    """按名字装配 provider 实例（注册表 → 实现类 + 配置注入）。"""

    def __init__(
        self,
        registry: ProviderRegistry,
        runtime: RuntimeProviderConfigs,
        matrix: CapabilityMatrix | None = None,
    ) -> None:
        self.registry = registry
        self.runtime = runtime
        self.matrix = matrix

    def create(self, provider_name: str) -> BaseProvider:
        cls = self.registry.get(provider_name)   # 未注册 → ProviderConfigError
        rt = self.runtime.get(provider_name)
        if rt is None:
            raise ProviderConfigError(f"providers.yaml 缺少运行参数: {provider_name}")

        network: NetworkConfig | None = None
        if self.matrix is not None and provider_name in self.matrix.providers:
            network = self.matrix.providers[provider_name].network

        token: str | None = None
        if rt.token_env:
            token = os.environ.get(rt.token_env, "") or None

        cfg = ProviderInstanceConfig(
            name=provider_name,
            display_name=(
                self.matrix.providers[provider_name].display_name
                if self.matrix and provider_name in self.matrix.providers
                else provider_name
            ),
            network_proxy=network.proxy if network else "direct",
            timeout_seconds=rt.timeout_seconds,
            max_retries=rt.max_retries,
            retry_backoff_base=rt.retry_backoff_base,
            rate_limit=rt.rate_limit.model_dump(),
            score=rt.score,
            token=token,
        )
        return cls(config=cfg)  # type: ignore[call-arg] —— 所有 Provider 实现约定接受 config 关键字

    def limiter_for(self, provider_name: str) -> ProviderRateLimiter | None:
        """按 providers.yaml rate_limit 构建限流器（gateway limiter_factory 用）。"""
        rt = self.runtime.get(provider_name)
        if rt is None:
            return None
        return ProviderRateLimiter(
            provider_name=provider_name,
            qps=rt.rate_limit.qps,
            burst=rt.rate_limit.burst,
            daily_quota=rt.rate_limit.daily_quota,
        )
