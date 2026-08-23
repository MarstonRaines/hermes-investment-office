# =====================================================================
# backend/app/providers/retry.py —— 退避重试（TS-05 §5.4，冻结）
#
# 语义：
# - 重试范围：ProviderTimeout / ProviderUnavailable（网络类）、
#   ProviderRateLimited（限流：优先尊重 retry_after，且等退避）；
# - 不重试：ProviderAuthError / ProviderConfigError / ProviderDataError
#   （重试大概率同样失败，直接失败 + audit + 告警）；
# - 退避 base * 2^n + jitter（base 默认 1.0s），防雪崩；
# - 重试间检查取消信号（job 被 kill 时立即退出）；
# - 重试不改变 observed_at：重试只影响 retrieved_at（以最终成功拉取时点为准）。
# =====================================================================
from __future__ import annotations

import asyncio
import random
from collections.abc import Awaitable, Callable
from typing import TypeVar

from app.providers.contracts.base import (
    ProviderAuthError,
    ProviderConfigError,
    ProviderDataError,
    ProviderRateLimited,
    ProviderTimeout,
    ProviderUnavailable,
)

__all__ = [
    "retry_with_backoff",
    "is_retryable",
    "RetryExhausted",
]

T = TypeVar("T")  # noqa: UP047 —— 保留 TypeVar 以便 Callable 标注复用（3.12 兼容写法）

_RETRYABLE = (ProviderTimeout, ProviderUnavailable, ProviderRateLimited)
_NON_RETRYABLE = (ProviderAuthError, ProviderConfigError, ProviderDataError)


def is_retryable(exc: Exception) -> bool:
    """是否值得重试（typed 错误语义，TS-05 §2.0.1）。"""
    if isinstance(exc, _NON_RETRYABLE):
        return False
    return isinstance(exc, _RETRYABLE)


class RetryExhausted(Exception):
    """重试耗尽。Data Gateway 依据原因进入 fallback 链。"""

    def __init__(self, last_error: Exception, attempts: int) -> None:
        super().__init__(f"retry exhausted after {attempts} attempts: {last_error}")
        self.last_error = last_error
        self.attempts = attempts


async def retry_with_backoff(  # noqa: UP047 —— 保持与 TypeVar 声明一致
    fn: Callable[[], Awaitable[T]],
    *,
    max_retries: int = 2,
    backoff_base: float = 1.0,
    cancel_event: asyncio.Event | None = None,
) -> T:
    """执行 fn，遇可重试错误按 base * 2^n + jitter 退避重试。

    max_retries = 初次调用之外的额外重试次数（total attempts = max_retries + 1）。
    """
    attempts = 0
    while True:
        try:
            return await fn()
        except Exception as exc:  # noqa: BLE001 —— typed 错误分支在 is_retryable 内
            if not is_retryable(exc):
                raise
            if attempts >= max_retries:
                raise RetryExhausted(exc, attempts + 1) from exc
            attempts += 1
            delay = backoff_base * (2 ** (attempts - 1)) + random.uniform(0, backoff_base * 0.2)
            if isinstance(exc, ProviderRateLimited) and exc.retry_after is not None:
                delay = max(delay, exc.retry_after)
            if cancel_event is not None and cancel_event.is_set():
                raise exc
            await asyncio.sleep(delay)
