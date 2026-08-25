from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.common.enums import JobStatus, JobType, PortfolioMode, TransactionType
from app.common.freshness import FreshnessGateError, aggregate_freshness, require_freshness
from app.common.schemas import ResponseEnvelope
from app.instruments.models import Instrument
from app.jobs.models import JobRun
from app.mcp.server import FROZEN_MCP_TOOLS, MCP_ALLOWED_TOOLS
from app.office.service import OfficeService
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


def test_get_list_parameters_are_query_parameters() -> None:
    from app.main import app

    schema = app.openapi()
    for path in ("/v1/market/snapshot", "/v1/fundamentals", "/v1/fundamentals/history"):
        assert "requestBody" not in schema["paths"][path]["get"]


def test_today_points_accept_watchlist_items_without_latest_quote() -> None:
    points = OfficeService._today_points(
        freshness={"overall": "FAILED"},
        watchlist={"items": [{"latest": None}]},
        portfolio=None,
        attention=[],
    )

    assert "观察池尚无可用行情" in points[1]


def test_office_market_history_exposes_ohlc_and_moving_averages() -> None:
    start = date(2026, 7, 1)
    bars = [
        {
            "trade_date": start + timedelta(days=offset),
            "open": offset + 0.5,
            "high": offset + 1.5,
            "low": offset,
            "close": offset + 1,
        }
        for offset in range(30)
    ]

    history = OfficeService._market_history(bars)

    assert {history[0][key] for key in ("ma5", "ma20", "ma30")} == {None}
    assert history[0]["open"] == 0.5
    assert history[0]["high"] == 1.5
    assert history[0]["low"] == 0
    assert history[4]["ma5"] == 3
    assert history[19]["ma20"] == 10.5
    assert history[29]["ma30"] == 15.5


def test_system_status_uses_latest_result_per_job_name(db_session) -> None:
    now = datetime.now(UTC)
    job_name = f"recovery_test_{uuid4().hex}"
    db_session.add_all([
        JobRun(
            job_name=job_name, job_type=JobType.SYNC_JOB.value,
            status=JobStatus.FAILED.value, created_at=now,
        ),
        JobRun(
            job_name=job_name, job_type=JobType.SYNC_JOB.value,
            status=JobStatus.SUCCEEDED.value,
            created_at=now + timedelta(microseconds=1),
        ),
    ])
    db_session.flush()

    status = OfficeService().system_status(db_session, date(2026, 8, 24))

    statuses = [row["status"] for row in status["jobs"] if row["name"] == job_name]
    assert statuses == ["SUCCEEDED", "FAILED"]


def test_manual_ledger_rejects_amount_mismatch_and_oversell(db_session) -> None:
    instrument = Instrument(
        instrument_type="CN_ETF", symbol=f"T{uuid4().hex[:7]}", name="账本校验标的",
        market="SSE", currency="CNY",
    )
    db_session.add(instrument)
    db_session.flush()
    service = PortfolioService()
    portfolio = service.create_portfolio(db_session, "手工组合", mode=PortfolioMode.REAL)
    service.record_opening_position(
        db_session, portfolio.portfolio_id, instrument.instrument_id,
        quantity=Decimal("10"), average_cost_cny=Decimal("10"),
        holding_date=date(2026, 8, 24),
    )

    with pytest.raises(PortfolioDomainError, match="数量乘以成交价"):
        service.post_transaction(
            db_session, portfolio.portfolio_id, TransactionType.BUY,
            instrument_id=instrument.instrument_id, quantity=Decimal("1"),
            price_cny=Decimal("10"), amount_cny=Decimal("-9"),
            trade_date=date(2026, 8, 24),
        )
    with pytest.raises(PortfolioDomainError, match="超过当日可用持仓"):
        service.post_transaction(
            db_session, portfolio.portfolio_id, TransactionType.SELL,
            instrument_id=instrument.instrument_id, quantity=Decimal("11"),
            price_cny=Decimal("10"), amount_cny=Decimal("110"),
            trade_date=date(2026, 8, 24),
        )
