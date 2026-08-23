# backend/app/providers/__init__.py —— Provider 层（TS-05 冻结目录）

from app.providers.contracts import *  # noqa: F401,F403 —— 六接口契约汇总
from app.providers.factory import ProviderFactory, ProviderInstanceConfig
from app.providers.gateway import DataGateway, DataUnavailable, FallbackDecision
from app.providers.rate_limiter import ProviderRateLimiter, RateLimitExhausted, TokenBucket
from app.providers.registry import ProviderRegistry

__all__ = [
    "DataGateway",
    "DataUnavailable",
    "FallbackDecision",
    "ProviderFactory",
    "ProviderInstanceConfig",
    "ProviderRateLimiter",
    "ProviderRegistry",
    "RateLimitExhausted",
    "TokenBucket",
]
