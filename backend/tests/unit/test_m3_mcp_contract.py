from __future__ import annotations

import asyncio
import json
from decimal import Decimal
from types import SimpleNamespace
from uuid import uuid4

from app.common.enums import DataQualityStatus, QuotaStatus
from app.mcp.server import MCP_ALLOWED_TOOLS, build_mcp_server


class _Session:
    def close(self) -> None:
        pass

    def commit(self) -> None:
        pass


class _ETFService:
    def read_metric(self, session, instrument_id, *, as_of):
        return SimpleNamespace(
            instrument_id=instrument_id,
            market_date=as_of.date(),
            nav_date=None,
            underlying_session_date=None,
            fx_as_of=None,
            premium_discount=Decimal("0.1"),
            fx_contribution=None,
            quota_status=QuotaStatus.UNKNOWN,
            net_value_t1=None,
            index_pe=None,
            index_pb=None,
            valuation_band=None,
            reference_nav_basis="OFFICIAL_NAV_T1",
            engine_version="etf-engine/0.1.0",
            input_hash="sha256:test",
            provenance_id=uuid4(),
            quality_status=DataQualityStatus.ACCEPTABLE,
            quality_score=Decimal("0.8"),
            quality_flags=["FX_MISSING"],
            as_of=as_of,
            market_price_cny=Decimal("1.1"),
            is_qdii=True,
            underlying_index_id=None,
            details={
                "source": "etf_metrics",
                "freshness": {"status": "OK", "age_days": 0},
                "data_freshness": "OK",
                "level_0": {"status": "OBSERVED", "is_estimate": False},
                "level_1": {
                    "status": "DISCLOSED",
                    "source": "HALF_YEAR",
                    "parquet_path": "parquet/etf_holdings/v1/secret.parquet",
                },
                "level_2": {"status": "NOT_IMPLEMENTED", "is_estimate": False},
                "r_usd": "0.0100",
                "fx_chg": "0.0020",
                "r_cny": "0.0120",
            },
        )

    def read_metric_provenance(self, session, snapshot):
        return [{
            "provenance_id": str(snapshot.provenance_id),
            "source_kind": "DERIVED_ENGINE",
            "source": "etf_metrics",
            "provider": "internal",
            "as_of_date": snapshot.market_date.isoformat(),
            "quality_status": "ACCEPTABLE",
            "quality_flags": snapshot.quality_flags,
        }]


def test_market_metrics_uses_existing_whitelist_name() -> None:
    from app.briefing.service import BriefingService
    from app.market_data.service import MarketDataService
    from app.thesis.service import ThesisService
    from app.valuation.service import ValuationService

    server = build_mcp_server(
        lambda: _Session(),
        parquet_store=None,
        market_service=MarketDataService(None),
        valuation_service=ValuationService(None),
        thesis_service=ThesisService(),
        briefing_service=BriefingService(MarketDataService(None)),
        sync_runner=object(),
        etf_service=_ETFService(),
    )
    names = {tool.name for tool in asyncio.run(server.list_tools())}
    assert "get_market_metrics" in names
    assert names <= MCP_ALLOWED_TOOLS

    result = asyncio.run(server.call_tool(
        "get_market_metrics",
        {"instrument_ids": [str(uuid4())], "as_of": "2026-08-24"},
    ))
    payload = json.loads(result.content[0].text)
    assert payload["data"]["items"][0]["premium_discount"] == "0.1"
    assert payload["data"]["items"][0]["freshness"]["status"] == "OK"
    assert payload["data"]["items"][0]["levels"]["level_2"]["status"] == "NOT_IMPLEMENTED"
    assert payload["data"]["items"][0]["r_cny"] == "0.0120"
    assert payload["quality"]["flags"] == ["FX_MISSING"]
    assert payload["provenance"][0]["source_kind"] == "DERIVED_ENGINE"
    encoded = json.dumps(payload, ensure_ascii=False)
    assert "parquet_path" not in encoded
    assert "/var/lib/hermes" not in encoded
    assert "file://" not in encoded
