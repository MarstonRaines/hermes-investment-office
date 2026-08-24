"""api/ 薄适配层（冻结规范 §9 / TS-03：api 层不得包含业务计算逻辑）。

依赖方向：api → services/schemas/common；禁止 api → engines/providers 内部实现。
架构测试（ARCH-API-*）强制本层纯度。
"""

from fastapi import APIRouter

from app.api.v1 import (
    briefing,
    fundamentals,
    instruments,
    market,
    portfolios,
    research,
    theses,
    valuations,
    watchlists,
)

api_router = APIRouter()
api_router.include_router(instruments.router, tags=["instruments"])
api_router.include_router(watchlists.router, tags=["watchlists"])
api_router.include_router(portfolios.router, tags=["portfolios"])
api_router.include_router(research.router, tags=["research"])
api_router.include_router(theses.router, tags=["theses"])
api_router.include_router(briefing.router, tags=["briefing"])
api_router.include_router(market.router, tags=["market"])
api_router.include_router(fundamentals.router, tags=["fundamentals"])
api_router.include_router(valuations.router, tags=["valuations"])
