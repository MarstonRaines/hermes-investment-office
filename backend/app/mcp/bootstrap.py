# =====================================================================
# backend/app/mcp/bootstrap.py —— MCP Server 装配（settings 驱动的完整组装）
#
# main.py 在 lifespan 中挂载：app.mount("/mcp", build_mcp_app())。
# 依赖：registry/factory/gateway/parquet/raw（M1 数据层）+ 各引擎服务。
# =====================================================================
from __future__ import annotations

import logging

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.briefing.service import BriefingService
from app.calendar.service import CalendarService
from app.common.config import settings
from app.jobs.sync_jobs import build_sync_runner
from app.market_data.parquet import ParquetStore
from app.market_data.service import MarketDataService
from app.mcp.server import build_mcp_server
from app.providers.bootstrap import register_all_providers
from app.providers.capability_matrix import load_capability_matrix
from app.providers.factory import ProviderFactory
from app.providers.raw_store import RawEvidenceStore
from app.providers.registry import ProviderRegistry
from app.providers.runtime_config import load_runtime_configs
from app.thesis.service import ThesisService
from app.valuation.service import ValuationService

logger = logging.getLogger(__name__)

__all__ = ["build_mcp_app"]


def _session_factory():
    engine = create_engine(settings.db_url, pool_pre_ping=True)

    def factory() -> Session:
        return Session(bind=engine)

    return factory


def build_mcp_app():
    """完整装配 MCP Starlette app（/mcp 端点，StreamableHTTP）。"""
    session_factory = _session_factory()

    matrix = load_capability_matrix(settings.provider_capability_path)
    registry = ProviderRegistry(matrix)
    register_all_providers(registry)
    runtime = load_runtime_configs(settings.providers_runtime_path)
    factory = ProviderFactory(registry, runtime, matrix, session_factory=session_factory)

    parquet = ParquetStore(f"{settings.data_dir}/parquet")
    raw = RawEvidenceStore(settings.data_dir)
    market_service = MarketDataService(parquet)
    valuation_service = ValuationService(market_service)
    thesis_service = ThesisService()
    briefing_service = BriefingService(market_service, CalendarService())
    sync_runner = build_sync_runner(session_factory, factory, registry, parquet, raw)

    server = build_mcp_server(
        session_factory,
        parquet_store=parquet,
        market_service=market_service,
        valuation_service=valuation_service,
        thesis_service=thesis_service,
        briefing_service=briefing_service,
        sync_runner=sync_runner,
    )
    # stateless_http=True：单请求-单响应（冻结规范 §7：v0.1 不支持 SSE 流式长连接）
    app = server.streamable_http_app(streamable_http_path="/mcp", host="127.0.0.1",
                                     stateless_http=True)
    return app
