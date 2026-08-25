"""DB-backed REST user paths for portfolio, research, thesis, and briefing domains."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient

import app.models  # noqa: F401
from app.api.v1.router import api_router
from app.briefing.models import DailyContext
from app.common.database import get_db
from app.instruments.models import Instrument


def _api_for_session(session) -> FastAPI:
    app = FastAPI()
    app.include_router(api_router, prefix="/v1")

    def override_db():
        yield session

    app.dependency_overrides[get_db] = override_db
    return app


def test_core_rest_paths_are_db_backed_and_real_write_gated(db_session) -> None:
    instrument = Instrument(
        instrument_type="CN_EQUITY", symbol=f"E{uuid4().hex[:8]}",
        name="REST 验收标的", market="SSE", currency="CNY",
    )
    db_session.add(instrument)
    db_session.flush()
    app = _api_for_session(db_session)
    client = TestClient(app)

    try:
        watchlist = client.post(
            "/v1/watchlists", json={"name": "REST watchlist", "description": "explicit"},
        )
        assert watchlist.status_code == 201
        member = client.post(
            f"/v1/watchlists/{watchlist.json()['watchlist_id']}/members",
            json={"instrument_id": str(instrument.instrument_id)},
        )
        assert member.status_code == 201

        paper = client.post("/v1/portfolios", json={"name": "REST PAPER", "mode": "PAPER"})
        assert paper.status_code == 201
        paper_id = paper.json()["portfolio_id"]

        real = client.post("/v1/portfolios", json={"name": "REST REAL", "mode": "REAL"})
        assert real.status_code == 201
        real_id = real.json()["portfolio_id"]

        denied = client.post(
            f"/v1/portfolios/{real_id}/transactions",
            json={"transaction_type": "CASH_IN", "amount_cny": "100", "trade_date": "2026-08-24"},
        )
        assert denied.status_code == 403

        posted = client.post(
            f"/v1/portfolios/{real_id}/transactions",
            headers={"X-Account-Write": "ACCOUNT_WRITE"},
            json={"transaction_type": "CASH_IN", "amount_cny": "100", "trade_date": "2026-08-24"},
        )
        assert posted.status_code == 200
        transaction_id = posted.json()["transaction_id"]
        reversal = client.post(
            f"/v1/portfolios/{real_id}/transactions/{transaction_id}/reversal",
            params={"trade_date": "2026-08-24"},
            headers={"X-Account-Write": "ACCOUNT_WRITE"},
        )
        assert reversal.status_code == 200
        assert reversal.json()["transaction_type"] == "REVERSAL"

        freshness_denied = client.post(
            f"/v1/portfolios/{real_id}/proposals",
            json={"instrument_id": str(instrument.instrument_id), "proposal_type": "BUY",
                  "quantity": "1", "limit_price_cny": "10", "freshness": "OK"},
        )
        assert freshness_denied.status_code == 409
        db_session.add(DailyContext(
            market_date=datetime.now(UTC).date(),
            generated_at=datetime.now(UTC),
            freshness_status="OK",
            data_freshness={"market": {"status": "OK"}},
            markets={},
            engine_versions={},
            source_status={},
        ))
        db_session.flush()
        proposal = client.post(
            f"/v1/portfolios/{real_id}/proposals",
            json={"instrument_id": str(instrument.instrument_id), "proposal_type": "BUY",
                  "quantity": "1", "limit_price_cny": "10", "freshness": "FAILED"},
        )
        assert proposal.status_code == 201
        proposal_id = proposal.json()["trade_proposal_id"]
        denied_approval = client.post(
            f"/v1/portfolios/{real_id}/proposals/{proposal_id}/transition",
            json={"status": "APPROVED"},
        )
        assert denied_approval.status_code == 403
        approved = client.post(
            f"/v1/portfolios/{real_id}/proposals/{proposal_id}/transition",
            headers={"X-Account-Write": "ACCOUNT_WRITE"},
            json={"status": "APPROVED"},
        )
        assert approved.status_code == 200
        executed = client.post(
            f"/v1/portfolios/{real_id}/proposals/{proposal_id}/transition",
            headers={"X-Account-Write": "ACCOUNT_WRITE"},
            json={"status": "EXECUTED", "trade_date": "2026-08-24", "price_cny": "10",
                  "quantity": "1", "fees_cny": "0"},
        )
        assert executed.status_code == 200
        assert executed.json()["executed_transaction_id"]

        workspace = client.post("/v1/research/workspaces", json={"title": "REST workspace"})
        assert workspace.status_code == 201
        workspace_id = workspace.json()["workspace_id"]
        note = client.post(
            "/v1/research/notes",
            json={"title": "REST note", "body_md": "evidence-backed", "workspace_id": workspace_id,
                  "instrument_id": str(instrument.instrument_id)},
        )
        assert note.status_code == 201
        evidence = client.post(
            "/v1/research/evidence",
            json={"title": "REST evidence", "claim": "claim", "instrument_id": str(instrument.instrument_id)},
        )
        assert evidence.status_code == 201
        context = client.get(
            "/v1/research/context", params={"instrument_id": str(instrument.instrument_id)},
        )
        assert context.status_code == 200
        assert context.json()["notes"][0]["title"] == "REST note"

        thesis = client.post(
            "/v1/theses",
            json={"instrument_id": str(instrument.instrument_id), "title": "REST thesis",
                  "body": {"claim": "durable"}},
        )
        assert thesis.status_code == 201
        thesis_body = thesis.json()
        assert thesis_body["current_revision"]["version"] == 1
        thesis_id = thesis_body["thesis_id"]
        revision = client.post(
            f"/v1/theses/{thesis_id}/revisions",
            json={"base_revision_id": thesis_body["current_revision"]["revision_id"],
                  "change_reason": "REST update", "thesis_body": {"claim": "durable-v2"}},
        )
        assert revision.status_code == 201
        assert revision.json()["version"] == 2
        pit = client.get(f"/v1/theses/{thesis_id}")
        assert pit.status_code == 200
        assert pit.json()["current_revision"]["version"] == 2

        daily_context = client.post(
            "/v1/briefing/contexts",
            json={"market_date": "2026-08-24", "instruments": [str(instrument.instrument_id)]},
        )
        assert daily_context.status_code == 201
        context_id = daily_context.json()["daily_context_id"]
        brief = client.post(
            "/v1/briefing/briefs",
            json={"daily_context_id": context_id, "market_date": "2026-08-24",
                  "content_md": "REST brief", "model_profile": "default"},
        )
        assert brief.status_code == 201
        fetched_brief = client.get("/v1/briefing/briefs/2026-08-24")
        assert fetched_brief.status_code == 200
        assert fetched_brief.json()["content_md"] == "REST brief"

        portfolio = client.get(f"/v1/portfolios/{paper_id}")
        assert portfolio.status_code == 200
        assert portfolio.json()["mode"] == "PAPER"
    finally:
        app.dependency_overrides.clear()
