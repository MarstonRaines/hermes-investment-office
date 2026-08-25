"""Instrument API（薄适配层：只做参数校验与响应组装，无业务计算）。

端点（ADR-004 D4：REST Contract 面向未来原生客户端）：
- POST   /v1/instruments                       创建 Instrument
- GET    /v1/instruments/{id}                  按 instrument_id 查询
- GET    /v1/instruments/resolve               按 (provider, symbol) 解析（MCP resolve_instrument 的 REST 等价）
- PATCH  /v1/instruments/{id}                  属性演进（乐观锁 version）
- POST   /v1/instruments/{id}/provider-symbols 追加时态映射
"""

from __future__ import annotations

from datetime import date
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.common.database import get_db
from app.instruments.schemas import (
    InstrumentCreate,
    InstrumentRead,
    InstrumentUpdate,
    ProviderSymbolRead,
)
from app.instruments.service import (
    InstrumentNotFoundError,
    InstrumentService,
    SymbolConflictError,
    VersionConflictError,
)
from app.operations.service import InstrumentBootstrapService

router = APIRouter(prefix="/instruments")


class ProviderSymbolCreate(BaseModel):
    provider: str = Field(min_length=1, max_length=32)
    symbol: str = Field(min_length=1, max_length=64)
    valid_from: date | None = None


class InstrumentBootstrapRequest(BaseModel):
    force: bool = True


def _svc(db: Session = Depends(get_db)) -> InstrumentService:
    return InstrumentService(db)


@router.post("", response_model=InstrumentRead, status_code=201)
def create_instrument(
    req: InstrumentCreate,
    svc: InstrumentService = Depends(_svc),
) -> InstrumentRead:
    try:
        inst = svc.create(req)
    except SymbolConflictError as e:
        raise HTTPException(status_code=409, detail=str(e)) from e
    return InstrumentRead.model_validate(inst)


@router.get("/resolve", response_model=InstrumentRead | None)
def resolve_instrument(
    provider: str = Query(...),
    symbol: str = Query(...),
    svc: InstrumentService = Depends(_svc),
) -> InstrumentRead | None:
    """必须先于 /{instrument_id} 声明，避免路径捕获（FastAPI 按声明顺序匹配）。"""
    inst = svc.resolve(provider, symbol)
    return InstrumentRead.model_validate(inst) if inst else None


@router.get("", response_model=list[InstrumentRead])
def list_instruments(
    query: str | None = Query(default=None, max_length=100),
    instrument_type: str | None = Query(default=None),
    market: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=100),
    svc: InstrumentService = Depends(_svc),
) -> list[InstrumentRead]:
    """供本地操作界面选择标的；不触发 Provider 查询。"""

    criteria: dict = {
        "instrument_type": instrument_type,
        "market": market,
        "limit": limit,
    }
    if query:
        criteria["symbol" if query.replace(".", "").isdigit() else "name"] = query
    return [InstrumentRead.model_validate(row) for row in svc.search(**criteria)]


@router.get("/{instrument_id}", response_model=InstrumentRead)
def get_instrument(instrument_id: UUID, svc: InstrumentService = Depends(_svc)) -> InstrumentRead:
    try:
        inst = svc.get(instrument_id)
    except InstrumentNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    return InstrumentRead.model_validate(inst)


@router.post("/{instrument_id}/bootstrap")
def bootstrap_instrument(
    instrument_id: UUID,
    req: InstrumentBootstrapRequest,
    request: Request,
    db: Session = Depends(get_db),
) -> dict:
    """重试行情、财务事实、公告索引、公司行动与基础 Thesis 初始化。"""

    try:
        instrument = InstrumentService(db).get(instrument_id)
    except InstrumentNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return InstrumentBootstrapService().run(
        db,
        request.app.state.backend_scheduler,
        instrument,
        force=req.force,
    )


@router.patch("/{instrument_id}", response_model=InstrumentRead)
def update_instrument(
    instrument_id: UUID,
    req: InstrumentUpdate,
    svc: InstrumentService = Depends(_svc),
) -> InstrumentRead:
    try:
        inst = svc.update(instrument_id, req)
    except InstrumentNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except VersionConflictError as e:
        raise HTTPException(status_code=409, detail=str(e)) from e
    return InstrumentRead.model_validate(inst)


@router.post("/{instrument_id}/provider-symbols", response_model=ProviderSymbolRead, status_code=201)
def add_provider_symbol(
    instrument_id: UUID,
    req: ProviderSymbolCreate,
    svc: InstrumentService = Depends(_svc),
) -> ProviderSymbolRead:
    try:
        ps = svc.add_provider_symbol(instrument_id, req.provider, req.symbol, req.valid_from)
    except InstrumentNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    return ProviderSymbolRead.model_validate(ps)
