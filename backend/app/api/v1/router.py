"""api/ 薄适配层（冻结规范 §9 / TS-03：api 层不得包含业务计算逻辑）。

依赖方向：api → services/schemas/common；禁止 api → engines/providers 内部实现。
架构测试（ARCH-API-*）强制本层纯度。
"""

from fastapi import APIRouter

from app.api.v1 import instruments

api_router = APIRouter()
api_router.include_router(instruments.router, tags=["instruments"])
