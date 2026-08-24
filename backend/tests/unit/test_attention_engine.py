from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import uuid4

import pytest

from app.briefing.attention import AttentionConfigError, AttentionEngine
from app.briefing.models import DailyContext


def _context(db_session, *, freshness: str = "OK") -> DailyContext:
    row = DailyContext(
        daily_context_id=uuid4(), market_date=date(2026, 8, 24),
        generated_at=datetime(2026, 8, 24, tzinfo=UTC), freshness_status=freshness,
        data_freshness={"market": {"status": freshness}},
        markets={"CN": {"date": "2026-08-24"}}, engine_versions={}, source_status={},
    )
    db_session.add(row)
    db_session.flush()
    return row


def _config(threshold: str = "-8") -> dict:
    return {
        "schema_version": 1,
        "defaults": {"scope": "all", "enabled": True},
        "rules": [{
            "name": "price_drop_test", "data_type": "market_bar",
            "rule_type": "NUMERIC_THRESHOLD", "metric": "pct_change",
            "operator": "le", "threshold": threshold, "unit": "percent",
            "window": "current_day", "scope": "all", "severity": "HIGH",
            "description": "test", "enabled": True, "dedupe": "daily",
        }],
    }


def test_attention_rule_is_deterministic_and_deduplicated(db_session, instrument) -> None:
    context = _context(db_session)
    engine = AttentionEngine(_config())
    facts = [{
        "data_type": "market_bar", "instrument_id": instrument.instrument_id,
        "pct_change": Decimal("-9"), "provenance_id": str(uuid4()),
    }]

    first = engine.evaluate(db_session, context, facts)
    second = engine.evaluate(db_session, context, facts)

    assert len(first.items) == 1
    assert first.items[0].rule_name == "price_drop_test"
    assert first.items[0].item_type == "PRICE_DROP"
    assert first.items[0].detail["trigger_value"] == "-9"
    assert second.items == []


def test_stale_input_is_skipped_without_attention_row(db_session, instrument) -> None:
    context = _context(db_session, freshness="STALE")
    result = AttentionEngine(_config()).evaluate(db_session, context, [{
        "data_type": "market_bar", "instrument_id": instrument.instrument_id,
        "pct_change": Decimal("-9"), "provenance_id": str(uuid4()),
    }])

    assert result.items == []
    assert result.skipped == [{"rule_name": "price_drop_test", "reason": "INPUT_STALE"}]


def test_invalid_attention_config_is_rejected() -> None:
    bad = _config()
    bad["rules"][0]["operator"] = "contains"
    with pytest.raises(AttentionConfigError):
        AttentionEngine(bad)
