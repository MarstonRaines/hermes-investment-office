"""Hermes Investment Office — Investment Backend 入口。

- 模块化单体（冻结规范 §7）：本文件只负责组装，不包含业务逻辑；
- 所有路由挂载在 api/ 薄适配层；
- 端口/绑定来自 settings（ADR-004 D5：默认 127.0.0.1:8000）。
"""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.v1.router import api_router
from app.common.config import settings
from app.common.logging import setup_logging
from app.mcp.bootstrap import build_mcp_app


@asynccontextmanager
async def lifespan(app: FastAPI):
    setup_logging()
    yield


app = FastAPI(
    title="Hermes Investment Office Backend",
    version="0.1.0",
    description="Investment Backend —— Facts + Calculation + Persistent State（冻结规范 v1.0）",
    lifespan=lifespan,
)

app.include_router(api_router, prefix="/v1")

# MCP 端点（冻结规范 §31.1：FastAPI 内嵌 StreamableHTTP，绑定 127.0.0.1）
app.mount("/mcp", build_mcp_app())


@app.get("/healthz", tags=["system"])
def healthz() -> dict:
    """存活探针（docker healthcheck / 运维）。"""
    return {"status": "ok", "service": "hermes-backend", "version": "0.1.0"}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app.main:app", host=settings.bind_host, port=settings.bind_port)
