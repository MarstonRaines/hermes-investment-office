# =====================================================================
# backend/app/providers/gateway.py —— 冻结：DataGateway（TS-05 §5.2，编排层）
#
# 架构强制点（冻结）：
# 1. Fallback 决策只发生在 Data Gateway；Provider 实现内部绝不自行换源；
# 2. 所有数据服务（market_data / fundamentals / etf / fx）必须经由 gateway 取数；
# 3. fallback 发生时：provenance.quality_flags 含 FALLBACK_USED、fallback_used=true、
#    audit_events 必有一行 PROVIDER_FALLBACK（audit_sink 回调，job 层落库）；
# 4. 终态 DATA_UNAVAILABLE / CONFLICT 也是显式结果，不允许假装数据存在。
# =====================================================================
from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from decimal import Decimal
from typing import Any, TypeVar
from uuid import UUID

from pydantic import BaseModel, Field

from app.providers.contracts.base import (
    ProvenanceEnvelope,
    ProviderCapability,
    ProviderError,
    ProviderRateLimited,
    ProviderTimeout,
    ProviderUnavailable,
)
from app.providers.rate_limiter import ProviderRateLimiter
from app.providers.registry import ProviderRegistry
from app.providers.retry import RetryExhausted, retry_with_backoff

__all__ = [
    "FallbackDecision",
    "DataUnavailable",
    "DataGateway",
    "FALLBACK_QUALITY_ADJUSTMENT",
]

logger = logging.getLogger(__name__)

T = TypeVar("T")

# TS-05 §5.2：fallback 质量衰减系数（quality_score 按原因衰减）
FALLBACK_QUALITY_ADJUSTMENT: dict[str, Decimal] = {
    "PRIMARY_TIMEOUT": Decimal("0.95"),
    "PRIMARY_RATE_LIMITED": Decimal("0.93"),
    "PRIMARY_DATA_ERROR": Decimal("0.90"),
    "PRIMARY_UNAVAILABLE": Decimal("0.95"),
}

# RetryExhausted.last_error 类型 → fallback_reason 映射（TS-05 §5.1/§5.4）
_ERROR_TO_REASON: dict[type[Exception], str] = {
    ProviderTimeout: "PRIMARY_TIMEOUT",
    ProviderRateLimited: "PRIMARY_RATE_LIMITED",
    ProviderUnavailable: "PRIMARY_UNAVAILABLE",
}


class FallbackDecision(BaseModel):
    """一次取数请求的 fallback 决策记录（TS-05 §5.2）。"""

    requested_provider: str          # primary
    actual_provider: str             # 实际取数的 provider
    fallback_used: bool
    fallback_reason: str | None = None  # PRIMARY_TIMEOUT / PRIMARY_RATE_LIMITED / PRIMARY_DATA_ERROR / PRIMARY_UNAVAILABLE / NO_FALLBACK
    attempts: list[str] = Field(default_factory=list)
    quality_adjustment: Decimal = Decimal("1.0")   # 质量衰减系数


class DataUnavailable(Exception):
    """终态：该 capability 在本次请求无可用数据（DATA_UNAVAILABLE）。

    显式结果，不是错误吞掉；调用方（job/服务层）按缺口语义处理：
    写 provenance（quality_status=STALE/REJECTED 或标记缺口）+ job FAILED/DEFERRED + 告警。
    """

    def __init__(self, capability: ProviderCapability, decision: FallbackDecision) -> None:
        super().__init__(
            f"{capability.value}: DATA_UNAVAILABLE after {decision.attempts} (reason={decision.fallback_reason})"
        )
        self.capability = capability
        self.decision = decision


class DataGateway:
    """resolve → fetch（带 fallback）→ normalize → persist + provenance + audit。

    取数函数由调用方注入（fetcher），gateway 负责：链解析 → 限流 → 重试 → fallback
    决策 → 质量衰减 → 审计回调。
    """

    def __init__(
        self,
        registry: ProviderRegistry,
        *,
        provider_factory: Callable[[type[Any]], Any] | None = None,
        limiter_factory: Callable[[str], ProviderRateLimiter | None] | None = None,
        audit_sink: Callable[[ProviderCapability, FallbackDecision, UUID | None], Awaitable[None]] | None = None,
    ) -> None:
        self.registry = registry
        # 实例装配必须经 provider_factory（token/代理等配置注入，TS-05 §3.5）；
        # 默认裸实例化仅用于测试/无状态 provider。
        self._provider_factory = provider_factory or (lambda cls: cls())
        self._limiter_factory = limiter_factory
        self._audit_sink = audit_sink

    # ---- 公开入口 ----

    async def fetch_with_fallback(
        self,
        capability: ProviderCapability,
        fetcher: Callable[[Any], Awaitable[T]],
        instrument_id: UUID | None = None,
        *,
        max_retries: int = 2,
        backoff_base: float = 1.0,
    ) -> tuple[T, FallbackDecision]:
        """按矩阵链取数：primary 成功即返回；失败按原因进入 fallback，终态抛 DataUnavailable。

        max_retries / backoff_base 由调用方（job 层）按 providers.yaml 运行参数传入。
        """
        chain = self.registry.fallback_chain(capability)
        if not chain:
            decision = FallbackDecision(
                requested_provider="",
                actual_provider="",
                fallback_used=False,
                fallback_reason="NO_FALLBACK",
            )
            raise DataUnavailable(capability, decision)

        primary_name = chain[0].provider_name
        decision = FallbackDecision(
            requested_provider=primary_name,
            actual_provider=primary_name,
            fallback_used=False,
            fallback_reason=None,
        )
        last_error: Exception | None = None

        for idx, provider_cls in enumerate(chain):
            provider = self._provider_factory(provider_cls)
            decision.attempts.append(provider.provider_name)
            decision.actual_provider = provider.provider_name

            limiter = self._limiter_factory(provider.provider_name) if self._limiter_factory else None
            try:
                if limiter is not None:
                    await limiter.acquire()
                result = await retry_with_backoff(
                    lambda p=provider: fetcher(p),
                    max_retries=max_retries,
                    backoff_base=backoff_base,
                )
            except RetryExhausted as exc:
                last_error = exc.last_error
                decision.fallback_used = True
                decision.fallback_reason = _ERROR_TO_REASON.get(
                    type(exc.last_error), "PRIMARY_UNAVAILABLE"
                )
                if idx < len(chain) - 1:
                    continue
                break
            except ProviderError:
                # 不可重试 typed 错误（auth/config/data）：直接失败，不进入 fallback（TS-05 §2.0.1）
                raise
            except Exception as exc:  # noqa: BLE001 —— 未知错误按 PRIMARY_UNAVAILABLE 处理
                last_error = exc
                decision.fallback_used = True
                decision.fallback_reason = "PRIMARY_UNAVAILABLE"
                if idx < len(chain) - 1:
                    continue
                break

            # 成功：若发生过 fallback，做质量衰减 + flag 标记（TS-05 §5.2）
            if decision.fallback_used:
                result = self._apply_fallback_mark(result, decision)
                await self._emit_audit(capability, decision, instrument_id)
            return result, decision

        # 终态：链上所有 provider 均失败
        if not decision.fallback_used:
            decision.fallback_used = True
            decision.fallback_reason = decision.fallback_reason or "NO_FALLBACK"
        decision.quality_adjustment = FALLBACK_QUALITY_ADJUSTMENT.get(
            decision.fallback_reason or "", Decimal("1.0")
        )
        logger.warning(
            "DATA_UNAVAILABLE capability=%s attempts=%s reason=%s last=%s",
            capability.value, decision.attempts, decision.fallback_reason, last_error,
        )
        await self._emit_audit(capability, decision, instrument_id)
        raise DataUnavailable(capability, decision)

    async def fetch_extension(
        self,
        provider_name: str,
        fetcher: Callable[[Any], Awaitable[T]],
        *,
        max_retries: int = 1,
        backoff_base: float = 1.0,
    ) -> T:
        """扩展 feed 取数（六接口之外的数据域，如 corporate_actions 分红输入）。

        仍经 gateway 限流/重试（架构意图：provider 访问不绕过 gateway），
        但无 fallback 语义（扩展 feed 不是矩阵域）。
        """
        provider_cls = self.registry.get(provider_name)
        provider = self._provider_factory(provider_cls)
        limiter = self._limiter_factory(provider_name) if self._limiter_factory else None
        if limiter is not None:
            await limiter.acquire()
        return await retry_with_backoff(
            lambda: fetcher(provider),
            max_retries=max_retries, backoff_base=backoff_base,
        )

    # ---- 内部 ----

    def _apply_fallback_mark(self, result: T, decision: FallbackDecision) -> T:
        """对取数结果中的 ProvenanceEnvelope 施加 fallback 标记与质量衰减。"""
        adjustment = FALLBACK_QUALITY_ADJUSTMENT.get(
            decision.fallback_reason or "", Decimal("1.0")
        )
        decision.quality_adjustment = adjustment
        if isinstance(result, ProvenanceEnvelope):
            return self._mark_envelope(result, decision, adjustment)  # type: ignore[return-value]
        if isinstance(result, list):
            out = []
            for item in result:
                env = getattr(item, "provenance", None)
                if isinstance(env, ProvenanceEnvelope):
                    item.provenance = self._mark_envelope(env, decision, adjustment)
                out.append(item)
            return out  # type: ignore[return-value]
        return result

    @staticmethod
    def _mark_envelope(
        env: ProvenanceEnvelope, decision: FallbackDecision, adjustment: Decimal
    ) -> ProvenanceEnvelope:
        flags = list(env.quality_flags)
        if "FALLBACK_USED" not in flags:
            flags.append("FALLBACK_USED")
        return env.model_copy(
            update={
                "quality_score": (env.quality_score * adjustment).quantize(Decimal("0.0001")),
                "quality_flags": flags,
                "fallback_used": True,
                "requested_provider": decision.requested_provider,
                "fallback_reason": decision.fallback_reason,
            }
        )

    async def _emit_audit(
        self,
        capability: ProviderCapability,
        decision: FallbackDecision,
        instrument_id: UUID | None,
    ) -> None:
        if self._audit_sink is None:
            return
        try:
            await self._audit_sink(capability, decision, instrument_id)
        except Exception:  # noqa: BLE001 —— 审计失败不阻断取数主流程
            logger.exception("audit_sink failed for %s", capability.value)
