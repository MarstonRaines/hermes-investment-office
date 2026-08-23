# =====================================================================
# backend/app/providers/rate_limiter.py —— 冻结：令牌桶 + 日额度（TS-05 §6.2）
#
# - 每个 provider 一个令牌桶 + 日额度计数，配置来自 providers.yaml（§3.5）；
# - 单机单进程限流即可满足 v0.1（模块化单体，Redis 非必需）；
# - 日额度耗尽 → 调用方把任务标记 DEFERRED，下一调度窗口继续（job 幂等）。
# =====================================================================
from __future__ import annotations

import asyncio
import time
from datetime import date

__all__ = ["TokenBucket", "ProviderRateLimiter"]


class TokenBucket:
    """令牌桶：以 qps 速率补充令牌，容量 burst；容量不足时等待。"""

    def __init__(self, qps: float, burst: int) -> None:
        if qps <= 0:
            raise ValueError("qps must be > 0")
        if burst <= 0:
            raise ValueError("burst must be > 0")
        self._qps = qps
        self._burst = burst
        self._tokens = float(burst)
        self._updated = time.monotonic()

    def _refill(self) -> None:
        now = time.monotonic()
        elapsed = now - self._updated
        self._tokens = min(float(self._burst), self._tokens + elapsed * self._qps)
        self._updated = now

    async def acquire(self) -> None:
        """取一个令牌；容量不足时异步等待（可被取消）。"""
        while True:
            self._refill()
            if self._tokens >= 1.0:
                self._tokens -= 1.0
                return
            deficit = (1.0 - self._tokens) / self._qps
            await asyncio.sleep(min(deficit, 0.25))


class ProviderRateLimiter:
    """每个 provider 一个令牌桶 + 日额度计数。"""

    def __init__(
        self,
        provider_name: str,
        qps: float,
        burst: int,
        daily_quota: int,
    ) -> None:
        self.provider_name = provider_name
        self._bucket = TokenBucket(qps, burst)
        self._daily_quota = daily_quota
        self._used_today: dict[date, int] = {}

    async def acquire(self) -> None:
        """限流闸门：日额度耗尽抛 RateLimitExhausted（调用方标记 DEFERRED）。"""
        today = date.today()
        if self._used_today.get(today, 0) >= self._daily_quota:
            raise RateLimitExhausted(self.provider_name, self._daily_quota)
        await self._bucket.acquire()
        self._used_today[today] = self._used_today.get(today, 0) + 1

    def remaining_today(self) -> int:
        today = date.today()
        return max(0, self._daily_quota - self._used_today.get(today, 0))

    def mark_used(self, n: int = 1) -> None:
        """供批量调用/测试手动记账（与 acquire 互斥使用）。"""
        today = date.today()
        self._used_today[today] = self._used_today.get(today, 0) + n


class RateLimitExhausted(Exception):
    """日额度耗尽。任务应标记 DEFERRED 而非失败（TS-05 §6.2）。"""

    def __init__(self, provider_name: str, daily_quota: int) -> None:
        super().__init__(f"provider {provider_name}: daily quota {daily_quota} exhausted")
        self.provider_name = provider_name
        self.daily_quota = daily_quota
