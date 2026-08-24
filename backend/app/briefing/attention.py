"""Deterministic Attention Filtering engine (TS-04 §8 / M6).

The engine is the only writer for ``attention_items``.  It consumes already
normalized facts supplied by the scheduler; it never calls a Provider and it
never asks an LLM to choose thresholds.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import yaml
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.audit.service import write_audit_event
from app.briefing.models import AttentionItem, DailyContext
from app.common.enums import ActorType, AttentionItemType, AuditAction

__all__ = [
    "AttentionConfigError",
    "AttentionEngine",
    "AttentionEvaluation",
    "AttentionRule",
    "load_attention_rules",
]


class AttentionConfigError(ValueError):
    code = "INVALID_ATTENTION_CONFIG"


class AttentionRule(BaseModel):
    name: str = Field(min_length=1)
    data_type: str
    rule_type: str
    metric: str | None = None
    operator: str | None = None
    threshold: Decimal | str | None = None
    unit: str = "none"
    window: str = "current_day"
    scope: str | list[str] = "all"
    severity: str = "INFO"
    description: str = ""
    enabled: bool = True
    dedupe: str = "daily"

    @model_validator(mode="after")
    def validate_rule(self) -> AttentionRule:
        data_types = {
            "market_bar", "index_point", "financial_fact", "etf_metric",
            "filing_event", "corporate_action", "state_change",
        }
        rule_types = {"NUMERIC_THRESHOLD", "EVENT_EXISTS", "STATE_CHANGE", "COMPOSITE"}
        operators = {"le", "lt", "ge", "gt", "eq", "neq", "exists", "changed"}
        if self.data_type not in data_types or self.rule_type not in rule_types:
            raise ValueError("attention data_type/rule_type 不受支持")
        if self.rule_type == "NUMERIC_THRESHOLD":
            if self.metric is None or self.operator not in operators - {"exists", "changed"}:
                raise ValueError("NUMERIC_THRESHOLD 必须声明 metric 与比较 operator")
            try:
                Decimal(str(self.threshold))
            except (InvalidOperation, TypeError, ValueError) as exc:
                raise ValueError("NUMERIC_THRESHOLD threshold 必须为数字") from exc
        elif self.rule_type == "EVENT_EXISTS" and self.operator not in {None, "exists"}:
            raise ValueError("EVENT_EXISTS 仅支持 exists")
        elif self.rule_type == "STATE_CHANGE" and self.operator not in {None, "changed"}:
            raise ValueError("STATE_CHANGE 仅支持 changed")
        if self.dedupe not in {"daily", "per_period"}:
            raise ValueError("dedupe 必须为 daily 或 per_period")
        if self.severity not in {"INFO", "LOW", "MEDIUM", "HIGH", "CRITICAL"}:
            raise ValueError("attention severity 非法")
        return self


@dataclass(frozen=True)
class AttentionEvaluation:
    items: list[AttentionItem]
    skipped: list[dict[str, str]]


def load_attention_rules(path: str | Path) -> tuple[list[AttentionRule], str]:
    config_path = Path(path)
    if not config_path.exists():
        raise AttentionConfigError(f"attention config 不存在: {config_path}")
    try:
        raw = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
        if raw.get("schema_version") != 1 or not isinstance(raw.get("rules"), list):
            raise ValueError("schema_version 必须为 1 且 rules 必须为列表")
        rules = [AttentionRule.model_validate(rule) for rule in raw["rules"]]
    except (OSError, TypeError, ValueError) as exc:
        if isinstance(exc, AttentionConfigError):
            raise
        raise AttentionConfigError(str(exc)) from exc
    canonical = json.dumps(raw, sort_keys=True, separators=(",", ":"), default=str)
    return rules, "sha256:" + hashlib.sha256(canonical.encode()).hexdigest()


class AttentionEngine:
    ENGINE_VERSION = "attention-engine/0.1.0"

    def __init__(self, config: Mapping[str, Any] | str | Path) -> None:
        if isinstance(config, (str, Path)):
            self.rules, self.config_hash = load_attention_rules(config)
            return
        try:
            if config.get("schema_version") != 1:
                raise ValueError("schema_version 必须为 1")
            self.rules = [AttentionRule.model_validate(value) for value in config.get("rules", [])]
            if not isinstance(config.get("rules"), list):
                raise ValueError("rules 必须为列表")
        except (TypeError, ValueError) as exc:
            raise AttentionConfigError(str(exc)) from exc
        canonical = json.dumps(config, sort_keys=True, separators=(",", ":"), default=str)
        self.config_hash = "sha256:" + hashlib.sha256(canonical.encode()).hexdigest()

    def evaluate(
        self,
        session: Session,
        context: DailyContext,
        facts: list[Mapping[str, Any]],
    ) -> AttentionEvaluation:
        """Evaluate all rules, then write a complete non-partial result set."""
        candidates: list[dict[str, Any]] = []
        skipped: list[dict[str, str]] = []
        for rule in self.rules:
            if not rule.enabled:
                continue
            domain = _freshness_domain(rule.data_type)
            domain_status = _domain_status(context, domain)
            matching = sorted(
                (fact for fact in facts if fact.get("data_type") == rule.data_type),
                key=lambda fact: str(fact.get("instrument_id") or ""),
            )
            for fact in matching:
                if domain_status in {"STALE", "FAILED"}:
                    skipped.append({"rule_name": rule.name, "reason": "INPUT_STALE"})
                    continue
                if not _matches(rule, fact):
                    continue
                instrument_id = _uuid_or_none(fact.get("instrument_id"))
                if self._is_duplicate(session, context.daily_context_id, rule, instrument_id, fact):
                    continue
                candidates.append({"rule": rule, "fact": fact, "instrument_id": instrument_id})

        rows: list[AttentionItem] = []
        for candidate in candidates:
            rule: AttentionRule = candidate["rule"]
            fact: Mapping[str, Any] = candidate["fact"]
            row = AttentionItem(
                attention_item_id=uuid4(), daily_context_id=context.daily_context_id,
                item_type=_item_type(rule), rule_name=rule.name,
                instrument_id=candidate["instrument_id"], severity=rule.severity,
                detail=_detail(rule, fact, self.config_hash, context), is_processed=False,
            )
            rows.append(row)
        for row in rows:
            session.add(row)
            write_audit_event(
                session, action=AuditAction.CREATE, entity_type="attention_item",
                entity_id=row.attention_item_id, actor_type=ActorType.JOB,
                actor_id="attention-engine", payload={"rule_name": row.rule_name},
            )
        if rows:
            session.flush()
        return AttentionEvaluation(items=rows, skipped=skipped)

    def _is_duplicate(
        self, session: Session, context_id: UUID, rule: AttentionRule,
        instrument_id: UUID | None, fact: Mapping[str, Any],
    ) -> bool:
        stmt = select(AttentionItem).where(
            AttentionItem.daily_context_id == context_id,
            AttentionItem.rule_name == rule.name,
        )
        if instrument_id is None:
            stmt = stmt.where(AttentionItem.instrument_id.is_(None))
        else:
            stmt = stmt.where(AttentionItem.instrument_id == instrument_id)
        if rule.dedupe == "per_period":
            period = str(fact.get("period_end") or fact.get("report_period") or "")
            if period:
                stmt = stmt.where(AttentionItem.detail["period"].astext == period)
        return session.scalar(stmt.limit(1)) is not None


def _freshness_domain(data_type: str) -> str:
    return {
        "market_bar": "market", "index_point": "index", "financial_fact": "fundamental",
        "etf_metric": "etf_nav", "filing_event": "fundamental",
        "corporate_action": "market", "state_change": "quota",
    }.get(data_type, data_type)


def _domain_status(context: DailyContext, domain: str) -> str:
    value = (context.data_freshness or {}).get(domain, {})
    if isinstance(value, Mapping):
        return str(value.get("status", "OK"))
    return str(value)


def _matches(rule: AttentionRule, fact: Mapping[str, Any]) -> bool:
    if rule.rule_type == "EVENT_EXISTS":
        return bool(fact.get("exists", True))
    if rule.rule_type == "STATE_CHANGE":
        return bool(fact.get("changed", fact.get("status_before") != fact.get("status_after")))
    if rule.rule_type != "NUMERIC_THRESHOLD" or rule.metric is None:
        return False
    value = fact.get(rule.metric)
    if value is None:
        return False
    left, right = Decimal(str(value)), Decimal(str(rule.threshold))
    return {
        "le": left <= right, "lt": left < right, "ge": left >= right,
        "gt": left > right, "eq": left == right, "neq": left != right,
    }[rule.operator or "eq"]


def _item_type(rule: AttentionRule) -> str:
    if "price_drop" in rule.name:
        return AttentionItemType.PRICE_DROP.value
    if "pe_percentile" in rule.name:
        return AttentionItemType.PE_PERCENTILE.value
    if rule.data_type == "filing_event":
        return AttentionItemType.FILING.value
    if rule.data_type in {"corporate_action", "state_change"}:
        return AttentionItemType.EVENT.value
    return AttentionItemType.ANOMALY.value


def _uuid_or_none(value: Any) -> UUID | None:
    if value is None:
        return None
    return value if isinstance(value, UUID) else UUID(str(value))


def _detail(rule: AttentionRule, fact: Mapping[str, Any], config_hash: str, context: DailyContext) -> dict:
    value = fact.get(rule.metric) if rule.metric else fact.get("status_after", fact.get("exists", True))
    detail = {
        "trigger_value": str(value), "threshold": str(rule.threshold) if rule.threshold is not None else None,
        "triggered_at": context.generated_at.isoformat(),
        "provenance_id": str(fact.get("provenance_id")) if fact.get("provenance_id") else None,
        "config_hash": config_hash, "description": rule.description,
    }
    period = fact.get("period_end") or fact.get("report_period")
    if period is not None:
        detail["period"] = str(period)
    return detail
