# =====================================================================
# tests/unit/test_mcp_server.py —— MCP 层（TS-07，ACC-M1.5-002）
#
# 覆盖：
# - ARCH-MCP-001：工具白名单（M1.5 子集 ⊆ 冻结 28；无清单外工具）
# - 包络五要素（request_id/as_of/data/quality/provenance）+ 业务错误路径
# - JSON-RPC 全链路（initialize → tools/list → tools/call）经 Starlette app
# =====================================================================
from __future__ import annotations

import json
from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

import app.models  # noqa: F401
from app.common.enums import DataQualityStatus
from app.mcp.server import (
    ADR006_MCP_TOOLS,
    FROZEN_MCP_TOOLS,
    M1_5_MCP_TOOLS,
    MCP_ALLOWED_TOOLS,
    MCPDomainError,
    _adjust_price_bars,
    build_mcp_server,
    envelope,
)

NOW = datetime(2026, 8, 21, tzinfo=UTC)


# ---- ARCH-MCP-001：白名单 ----

def test_m1_5_tools_subset_of_frozen_28() -> None:
    """M1.5 实现子集 ⊆ ts07 冻结 28 工具（全量对齐是 M5 验收）。"""
    assert M1_5_MCP_TOOLS <= FROZEN_MCP_TOOLS
    assert len(FROZEN_MCP_TOOLS) == 28
    assert len(ADR006_MCP_TOOLS) == 3


def test_no_tool_outside_frozen_list(server) -> None:
    """已注册工具 == M1.5 子集，无清单外工具（白名单是唯一事实来源）。"""
    import asyncio

    names = asyncio.run(server.list_tools())
    registered = {t.name for t in names}
    assert registered == MCP_ALLOWED_TOOLS
    assert registered <= MCP_ALLOWED_TOOLS


# ---- 包络 ----

def test_envelope_five_elements() -> None:
    env = envelope({"x": 1}, quality_status=DataQualityStatus.VERIFIED,
                   quality_score=Decimal("0.97"), quality_flags=["FALLBACK_USED"])
    assert set(env) == {"request_id", "as_of", "data", "quality", "provenance", "freshness"}
    assert env["quality"]["status"] == "VERIFIED"
    assert env["quality"]["score"] == "0.97"
    assert env["provenance"] == []


def test_envelope_error_path() -> None:
    env = envelope(error={"code": "NOT_FOUND", "message": "无匹配"})
    assert env["error"]["code"] == "NOT_FOUND"
    assert "data" not in env


def test_domain_error_mapping() -> None:
    MCPDomainError("INVALID_ARGUMENT", "bad", field="query")
    err = {"code": "INVALID_ARGUMENT", "message": "bad", "field": "query"}
    assert envelope(error=err)["error"] == err


def test_price_history_adjustment_keeps_raw_and_selects_contract_value() -> None:
    bars = [
        {"trade_date": date(2026, 8, 20), "close": 100, "adj_factor": 2, "adjusted_close": 200},
        {"trade_date": date(2026, 8, 21), "close": 120, "adj_factor": 3, "adjusted_close": 360},
    ]
    qfq = _adjust_price_bars(bars, "qfq")
    hfq = _adjust_price_bars(bars, "hfq")
    assert qfq[0]["raw_close"] == 100
    assert qfq[0]["close"] == pytest.approx(66.6666667)
    assert qfq[1]["close"] == pytest.approx(120)
    assert hfq[0]["close"] == 200
    assert hfq[1]["close"] == 360


# ---- 工具直调（用 stub 服务）----


class _Stub:
    pass


@pytest.fixture(scope="module")
def server():
    """无 DB 依赖的 MCP server（resolve 走 stub）。"""
    from app.briefing.service import BriefingService
    from app.market_data.parquet import ParquetStore
    from app.market_data.service import MarketDataService
    from app.thesis.service import ThesisService
    from app.valuation.service import ValuationService

    class StubMarket(MarketDataService):
        def get_ohlcva(self, session, instrument_id, **kw):
            return [{
                "trade_date": date(2026, 8, 21), "close": 1272.83,
                "pct_change": 0.5, "volume": 1, "amount": 2, "provider": "tushare",
            }]

    class FakeSession:
        def close(self) -> None:
            pass

    def sf():
        return FakeSession()

    return build_mcp_server(
        sf,
        parquet_store=ParquetStore("/tmp/mcp-stub"),
        market_service=StubMarket(None),
        valuation_service=ValuationService(None),
        thesis_service=ThesisService(),
        briefing_service=BriefingService(StubMarket(None)),
        sync_runner=_Stub(),
    )


def test_get_market_snapshot_tool(server) -> None:
    import asyncio

    result = asyncio.run(server.call_tool(
        "get_market_snapshot", {"instrument_ids": ["11111111-1111-1111-1111-111111111111"],
                                "as_of": "2026-08-21"}))
    text = result.content[0].text
    payload = json.loads(text)
    assert payload["data"]["snapshots"][0]["close"] == 1272.83
    assert "request_id" in payload and "quality" in payload


# ---- JSON-RPC 全链路（Starlette app）----

def _sse_json(resp) -> dict:
    """StreamableHTTP 响应是 SSE 格式（data: {...}），提取 JSON。"""
    for line in resp.text.splitlines():
        if line.startswith("data: "):
            return json.loads(line[6:])
    return json.loads(resp.text)


def test_jsonrpc_list_and_call(tmp_path, db_session, instrument) -> None:
    """经 Starlette app：initialize → tools/list → tools/call（resolve_instrument）。"""

    # 用真实 session_factory 指向测试库
    import os

    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine as ce
    from sqlalchemy.orm import Session as SAS

    from app.common.config import settings
    from app.jobs.sync_jobs import build_sync_runner
    from app.market_data.parquet import ParquetStore
    from app.providers.capability_matrix import load_capability_matrix
    from app.providers.factory import ProviderFactory
    from app.providers.raw_store import RawEvidenceStore
    from app.providers.registry import ProviderRegistry
    from app.providers.runtime_config import RuntimeProviderConfigs

    test_url = os.environ.get("HERMES_TEST_DB_URL") or (
        settings.db_url.rsplit("/", 1)[0] + "/hermes_test"
    )
    engine = ce(test_url, pool_pre_ping=True)

    def sf():
        return SAS(bind=engine)

    matrix = load_capability_matrix(settings.provider_capability_path)
    reg = ProviderRegistry(matrix)
    from app.providers.bootstrap import register_all_providers
    register_all_providers(reg)
    runtime = RuntimeProviderConfigs(providers={})
    factory = ProviderFactory(reg, runtime, matrix, session_factory=sf)
    parquet = ParquetStore(tmp_path / "parquet")
    raw = RawEvidenceStore(tmp_path / "data")
    from app.briefing.service import BriefingService
    from app.calendar.service import CalendarService
    from app.market_data.service import MarketDataService
    from app.thesis.service import ThesisService
    from app.valuation.service import ValuationService

    server = build_mcp_server(
        sf, parquet_store=parquet, market_service=MarketDataService(parquet),
        valuation_service=ValuationService(MarketDataService(parquet)),
        thesis_service=ThesisService(),
        briefing_service=BriefingService(MarketDataService(parquet), CalendarService()),
        sync_runner=build_sync_runner(sf, factory, reg, parquet, raw),
    )
    # host="testserver"：transport_security 校验 TestClient 的 Host 头
    app = server.streamable_http_app(streamable_http_path="/", stateless_http=True,
                                     host="testserver")

    # 先入库一个标的（独立提交会话；db_session fixture 的事务不对外可见）
    from uuid import uuid4

    from app.instruments.models import Instrument, ProviderSymbol

    session = sf()
    inst = Instrument(instrument_type="CN_EQUITY", symbol=f"M{uuid4().hex[:6]}",
                      name="mcp测试", market="SSE", currency="CNY")
    session.add(inst)
    session.flush()
    session.add(ProviderSymbol(
        instrument_id=inst.instrument_id, provider="tushare",
        symbol=f"M{inst.symbol}.SH", valid_from=date(2020, 1, 1)))
    session.commit()
    symbol = inst.symbol          # close 前取值（session 关闭后属性过期）
    session.close()

    with TestClient(app) as client:
        r = client.post("/", json={"jsonrpc": "2.0", "id": 1, "method": "tools/list",
                                   "params": {}},
                        headers={"Accept": "application/json, text/event-stream",
                                 "Content-Type": "application/json"})
        assert r.status_code == 200
        listed = _sse_json(r)
        names = {t["name"] for t in listed["result"]["tools"]}
        assert names == MCP_ALLOWED_TOOLS

        r2 = client.post("/", json={"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                                    "params": {"name": "resolve_instrument",
                                               "arguments": {"symbol": symbol}}},
                         headers={"Accept": "application/json, text/event-stream",
                                  "Content-Type": "application/json"})
        assert r2.status_code == 200
        result = _sse_json(r2)["result"]
        content = result["content"][0]["text"]
        payload = json.loads(content)
        assert payload["data"]["total"] == 1
        assert payload["data"]["matches"][0]["instrument_id"] == str(inst.instrument_id)
        # 未知工具 → 拒绝（mcp 2.0 SDK：工具级 isError 结果，绝不执行任何逻辑）
        r3 = client.post("/", json={"jsonrpc": "2.0", "id": 3, "method": "tools/call",
                                    "params": {"name": "raw_sql", "arguments": {}}},
                         headers={"Accept": "application/json, text/event-stream",
                                  "Content-Type": "application/json"})
        err3 = _sse_json(r3)["result"]
        assert err3.get("isError") is True
        assert "Unknown tool" in err3["content"][0]["text"]
