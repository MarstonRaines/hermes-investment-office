from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.common.enums import PortfolioMode, TransactionType
from app.common.freshness import FreshnessGateError, aggregate_freshness, require_freshness
from app.common.schemas import ResponseEnvelope
from app.mcp.server import FROZEN_MCP_TOOLS, MCP_ALLOWED_TOOLS
from app.portfolio.engine import replay
from app.portfolio.service import PortfolioDomainError, PortfolioService
from app.risk.engine import compute_risk


def test_freshness_contract_aggregates_and_gates_warning() -> None:
    assert aggregate_freshness({"market": "OK", "quota": "WARNING"}) == "WARNING"
    with pytest.raises(FreshnessGateError) as exc:
        require_freshness({"overall": "WARNING", "domains": {"quota": {"status": "WARNING"}}})
    assert exc.value.code == "FRESHNESS_GATE"
    require_freshness({"overall": "OK", "domains": {}})


def test_response_envelope_includes_freshness_contract() -> None:
    payload = ResponseEnvelope.model_validate({
        "request_id": "00000000-0000-0000-0000-000000000001",
        "as_of": "2026-08-24T00:00:00Z",
        "data": {"ok": True},
        "quality": {"status": "VERIFIED", "score": "1", "flags": []},
        "provenance": [],
        "freshness": {"overall": "OK", "domains": {}},
    })
    assert payload.freshness.overall == "OK"


def test_reversal_is_explicit_and_cancels_original_effect() -> None:
    # The pure engine must understand a persisted REVERSAL row; service-level
    # authorization is tested separately against a real DB.
    original_id = uuid4()
    now = datetime(2026, 8, 24, tzinfo=UTC)
    original = SimpleNamespace(
        transaction_id=original_id, transaction_type="BUY", amount_cny=Decimal("-100"),
        quantity=Decimal("10"), price_cny=Decimal("10"), fees_cny=Decimal("0"),
        instrument_id=uuid4(), trade_date=date(2026, 8, 24), trade_at=now,
        created_at=now, reverses_transaction_id=None,
    )
    reversal = SimpleNamespace(
        transaction_id=uuid4(), transaction_type="REVERSAL", amount_cny=Decimal("100"),
        quantity=Decimal("10"), price_cny=Decimal("10"), fees_cny=Decimal("0"),
        instrument_id=original.instrument_id, trade_date=date(2026, 8, 24),
        trade_at=now, created_at=now, reverses_transaction_id=original_id,
    )
    result = replay([original, reversal])
    assert result.cash_cny == Decimal("0.0000")
    assert result.positions == {}


def test_real_transaction_requires_account_write(db_session) -> None:
    svc = PortfolioService()
    pf = svc.create_portfolio(db_session, "real", mode=PortfolioMode.REAL)
    with pytest.raises(PortfolioDomainError):
        svc.record_transaction(
            db_session, pf.portfolio_id, TransactionType.CASH_IN,
            amount_cny=Decimal("100"), trade_date=date(2026, 8, 24),
        )


def test_risk_engine_is_deterministic_and_threshold_driven() -> None:
    result = compute_risk(
        positions={"a": Decimal("70"), "b": Decimal("30")},
        nav=Decimal("100"),
        nav_history=[Decimal("100"), Decimal("90"), Decimal("95")],
        thresholds={"concentration_warn": Decimal("0.60"), "drawdown_warn": Decimal("0.10")},
    )
    assert result["concentration"]["max_weight"] == Decimal("0.7000")
    assert result["concentration"]["level"] == "WARN"
    assert result["drawdown"]["max_drawdown"] == Decimal("0.1000")
    assert result == compute_risk(
        positions={"a": Decimal("70"), "b": Decimal("30")}, nav=Decimal("100"),
        nav_history=[Decimal("100"), Decimal("90"), Decimal("95")],
        thresholds={"concentration_warn": Decimal("0.60"), "drawdown_warn": Decimal("0.10")},
    )


def test_mcp_contract_has_full_core_allowlist() -> None:
    assert len(FROZEN_MCP_TOOLS) == 28
    assert len(MCP_ALLOWED_TOOLS) == 31
