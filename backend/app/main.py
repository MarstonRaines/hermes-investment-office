"""Hermes Investment Office — Investment Backend 入口。

- 模块化单体（冻结规范 §7）：本文件只负责组装，不包含业务逻辑；
- 所有路由挂载在 api/ 薄适配层；
- 端口/绑定来自 settings（ADR-004 D5：默认 127.0.0.1:8000）。
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import date

from fastapi import FastAPI

from app.api.v1.router import api_router
from app.bootstrap import build_mcp_app
from app.common.config import settings
from app.common.logging import setup_logging


@asynccontextmanager
async def lifespan(app: FastAPI):
    setup_logging()
    runtime_scheduler = None
    if settings.scheduler_enabled:
        runtime_scheduler = MCP_APP.state.backend_scheduler.build_apscheduler(
            instruments_provider=MCP_APP.state.scheduler_universe_provider,
            market_date_provider=date.today,
            timezone=settings.scheduler_timezone,
            hour=settings.scheduler_hour,
            minute=settings.scheduler_minute,
        )
        runtime_scheduler.start()
    # MCP 子 app 的 lifespan（task group 初始化）：Starlette 挂载不传播子 app lifespan，
    # 必须在根 app lifespan 中手动进入（mcp 2.0 streamable_http 要求）。
    try:
        async with MCP_APP.router.lifespan_context(MCP_APP):
            yield
    finally:
        if runtime_scheduler is not None:
            runtime_scheduler.shutdown(wait=False)


app = FastAPI(
    title="Hermes Investment Office Backend",
    version="0.1.0",
    description="Investment Backend —— Facts + Calculation + Persistent State（冻结规范 v1.0）",
    lifespan=lifespan,
)

app.include_router(api_router, prefix="/v1")

# MCP 端点（冻结规范 §31.1：FastAPI 内嵌 StreamableHTTP，绑定 127.0.0.1）
MCP_APP = build_mcp_app()
app.mount("/mcp", MCP_APP)


@app.get("/healthz", tags=["system"])
def healthz() -> dict:
    """存活探针（docker healthcheck / 运维）。"""
    return {"status": "ok", "service": "hermes-backend", "version": "0.1.0"}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app.main:app", host=settings.bind_host, port=settings.bind_port)
