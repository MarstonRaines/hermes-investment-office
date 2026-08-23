# =====================================================================
# tests/unit/test_retry.py —— 退避重试（TS-05 §5.4）
# =====================================================================
from __future__ import annotations

import asyncio

import pytest

from app.providers.contracts.base import (
    ProviderAuthError,
    ProviderConfigError,
    ProviderDataError,
    ProviderRateLimited,
    ProviderTimeout,
)
from app.providers.retry import RetryExhausted, is_retryable, retry_with_backoff


def test_is_retryable_classification() -> None:
    assert is_retryable(ProviderTimeout("t"))
    assert is_retryable(ProviderRateLimited("r"))
    assert not is_retryable(ProviderAuthError("a"))
    assert not is_retryable(ProviderConfigError("c"))
    assert not is_retryable(ProviderDataError("d"))
    assert not is_retryable(ValueError("unknown"))


def test_retry_succeeds_after_transient_failures() -> None:
    async def run() -> None:
        calls = {"n": 0}

        async def flaky() -> str:
            calls["n"] += 1
            if calls["n"] < 3:
                raise ProviderTimeout("boom")
            return "ok"

        result = await retry_with_backoff(flaky, max_retries=3, backoff_base=0.01)
        assert result == "ok"
        assert calls["n"] == 3

    asyncio.run(run())


def test_retry_exhausted_raises() -> None:
    async def run() -> None:
        async def always_fail() -> None:
            raise ProviderRateLimited("limited", retry_after=0.01)

        with pytest.raises(RetryExhausted) as ei:
            await retry_with_backoff(always_fail, max_retries=2, backoff_base=0.01)
        assert isinstance(ei.value.last_error, ProviderRateLimited)
        assert ei.value.attempts == 3   # 初次 + 2 次重试

    asyncio.run(run())


def test_non_retryable_fails_immediately() -> None:
    async def run() -> None:
        async def auth_fail() -> None:
            raise ProviderAuthError("bad token")

        with pytest.raises(ProviderAuthError):
            await retry_with_backoff(auth_fail, max_retries=5, backoff_base=0.01)

    asyncio.run(run())


def test_cancel_event_stops_retry() -> None:
    async def run() -> None:
        cancel = asyncio.Event()
        calls = {"n": 0}

        async def failing() -> None:
            calls["n"] += 1
            raise ProviderTimeout("t")

        cancel.set()   # 首次失败后重试前检查
        with pytest.raises(ProviderTimeout):
            await retry_with_backoff(failing, max_retries=5, backoff_base=0.01,
                                     cancel_event=cancel)
        assert calls["n"] == 1

    asyncio.run(run())
