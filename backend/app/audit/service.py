# =====================================================================
# backend/app/audit/service.py —— Provenance 落库 + Audit 事件（TS-05 §2.0.4 / §5.2）
#
# - write_provenance：ProvenanceEnvelope → provenance_records 无损映射（§2.0.4 冻结）；
# - write_audit_event：append-only 审计事件；
# - provider_fallback_sink：Data Gateway audit_sink（PROVIDER_FALLBACK 双写）。
#
# 业务不变量：事实写入必有血缘（同事务提交或经 outbox）；本服务只负责写，
# 事务边界由调用方（job/服务层）控制。
# =====================================================================
from __future__ import annotations

from collections.abc import Callable
from uuid import UUID

from sqlalchemy.orm import Session

from app.audit.models import AuditEvent, ProvenanceRecord
from app.common.enums import ActorType, AuditAction
from app.providers.contracts.base import ProvenanceEnvelope, ProviderCapability
from app.providers.gateway import FallbackDecision

__all__ = [
    "write_provenance",
    "write_audit_event",
    "provider_fallback_sink",
]


def write_provenance(session: Session, env: ProvenanceEnvelope) -> ProvenanceRecord:
    """ProvenanceEnvelope → provenance_records（TS-05 §2.0.4 映射表，冻结）。

    requested_provider / fallback_reason 无独立列 → 序列化进 quality_flags
    （fallback.requested_provider / fallback.actual_provider / fallback.reason）。
    """
    flags = list(env.quality_flags)
    if env.fallback_used:
        flags.append(f"fallback.requested_provider={env.requested_provider or ''}")
        flags.append(f"fallback.actual_provider={env.provider}")
        flags.append(f"fallback.reason={env.fallback_reason or ''}")

    rec = ProvenanceRecord(
        source_kind=env.source_kind,
        source=env.source,
        provider=env.provider,
        source_uri=env.source_uri,
        source_record_id=env.source_record_id,
        published_at=env.published_at,
        observed_at=env.observed_at,
        retrieved_at=env.retrieved_at,
        as_of_date=env.as_of_date,
        quality_score=env.quality_score,
        quality_status=env.quality_status,
        quality_flags=flags,
        fallback_used=env.fallback_used,
        raw_hash=env.raw_hash,
        raw_object_key=env.raw_object_key,
        ingestion_run_id=env.ingestion_run_id,
        transform_version=env.transform_version,
    )
    session.add(rec)
    return rec


def write_audit_event(
    session: Session,
    *,
    action: AuditAction,
    entity_type: str,
    entity_id: UUID | None = None,
    actor_type: ActorType = ActorType.SYSTEM,
    actor_id: str | None = None,
    payload: dict | None = None,
    request_id: str | None = None,
) -> AuditEvent:
    """append-only 审计事件（ts02 §8.3）。"""
    event = AuditEvent(
        actor_type=actor_type,
        actor_id=actor_id,
        action=action,
        entity_type=entity_type,
        entity_id=entity_id,
        payload=payload,
        request_id=request_id,
    )
    session.add(event)
    return event


def provider_fallback_sink(
    session_factory: Callable[[], Session],
) -> Callable:
    """Data Gateway audit_sink 工厂：fallback 决策落 audit_events（action=PROVIDER_FALLBACK）。

    每次调用使用独立 session 并立即提交（audit 与业务事务同事务由 job 层实现；
    本 sink 用于 gateway 在取数过程同步落审计，保证失败路径也有记录）。
    """

    async def sink(
        capability: ProviderCapability,
        decision: FallbackDecision,
        instrument_id: UUID | None,
    ) -> None:
        session = session_factory()
        try:
            write_audit_event(
                session,
                action=AuditAction.PROVIDER_FALLBACK,
                entity_type="data_fetch",
                entity_id=instrument_id,
                actor_type=ActorType.JOB,
                actor_id="data-gateway",
                payload={
                    "capability": capability.value,
                    "requested_provider": decision.requested_provider,
                    "actual_provider": decision.actual_provider,
                    "fallback_used": decision.fallback_used,
                    "fallback_reason": decision.fallback_reason,
                    "attempts": decision.attempts,
                    "quality_adjustment": str(decision.quality_adjustment),
                },
            )
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    return sink
