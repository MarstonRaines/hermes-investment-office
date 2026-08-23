# =====================================================================
# tests/unit/test_rate_limiter.py —— 令牌桶 + 日额度（TS-05 §6.2）
# =====================================================================
from __future__ import annotations

import asyncio

import pytest

from app.providers.rate_limiter import (
    ProviderRateLimiter,
    RateLimitExhausted,
    TokenBucket,
)


def test_token_bucket_allows_burst() -> None:
    async def run() -> None:
        bucket = TokenBucket(qps=1000.0, burst=5)
        for _ in range(5):
            await bucket.acquire()   # 突发容量内不等待

    asyncio.run(run())


def test_token_bucket_refills() -> None:
    async def run() -> None:
        bucket = TokenBucket(qps=1000.0, burst=2)
        await bucket.acquire()
        await bucket.acquire()
        await asyncio.sleep(0.01)    # 快速补充
        await bucket.acquire()       # 不阻塞即证明 refill 生效

    asyncio.run(run())


def test_token_bucket_invalid_params() -> None:
    with pytest.raises(ValueError):
        TokenBucket(qps=0, burst=1)
    with pytest.raises(ValueError):
        TokenBucket(qps=1, burst=0)


def test_daily_quota_exhausted() -> None:
    limiter = ProviderRateLimiter("tushare", qps=1000.0, burst=10, daily_quota=3)
    limiter.mark_used(3)
    assert limiter.remaining_today() == 0
    with pytest.raises(RateLimitExhausted) as ei:
        asyncio.run(limiter.acquire())
    assert ei.value.provider_name == "tushare"
    assert ei.value.daily_quota == 3


def test_acquire_respects_quota() -> None:
    async def run() -> None:
        limiter = ProviderRateLimiter("tushare", qps=1000.0, burst=10, daily_quota=2)
        await limiter.acquire()
        await limiter.acquire()
        with pytest.raises(RateLimitExhausted):
            await limiter.acquire()

    asyncio.run(run())
