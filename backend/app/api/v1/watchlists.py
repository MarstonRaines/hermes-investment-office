"""Watchlist REST adapter (ADR-006)."""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.common.database import get_db
from app.instruments.schemas import (
    InstrumentRead,
    WatchlistMemberCreate,
    WatchlistMemberRead,
    WatchlistRead,
)
from app.instruments.service import (
    InstrumentNotFoundError,
    InstrumentService,
    InvalidInstrumentSymbolError,
    SymbolConflictError,
    WatchlistArchivedError,
    WatchlistMemberNotFoundError,
    WatchlistNotFoundError,
    WatchlistPermissionError,
    WatchlistService,
)
from app.operations.service import InstrumentBootstrapService

router = APIRouter(prefix="/watchlists")


class WatchlistCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: str | None = None


class WatchlistInstrumentCreate(BaseModel):
    symbol: str = Field(min_length=1, max_length=32)
    name: str = Field(min_length=1, max_length=200)


def _svc(db: Session = Depends(get_db)) -> WatchlistService:
    return WatchlistService(db)


def _read(row) -> WatchlistRead:
    return WatchlistRead.model_validate(row)


@router.post("", response_model=WatchlistRead, status_code=201)
def create_watchlist(req: WatchlistCreate, svc: WatchlistService = Depends(_svc)) -> WatchlistRead:
    row = svc.create(req.name, req.description)
    svc.commit()
    return _read(row)


@router.get("", response_model=WatchlistRead)
def get_watchlist(
    watchlist_id: UUID | None = Query(default=None),
    include_removed: bool = False,
    svc: WatchlistService = Depends(_svc),
) -> WatchlistRead:
    try:
        row = svc.get(watchlist_id) if watchlist_id else svc.get_default()
        if row is None:
            raise WatchlistNotFoundError("没有 ACTIVE 默认观察池")
        # Load through the service so the read path respects the temporal flag.
        row.members = svc.list_members(
            row.watchlist_id, include_removed=include_removed, permission="READ"
        )
        return _read(row)
    except WatchlistNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/{watchlist_id}/members", response_model=WatchlistMemberRead, status_code=201)
def add_watchlist_member(
    watchlist_id: UUID,
    req: WatchlistMemberCreate,
    svc: WatchlistService = Depends(_svc),
) -> WatchlistMemberRead:
    try:
        row = svc.add_member(
            watchlist_id, req.instrument_id, note=req.note, permission="RESEARCH_WRITE"
        )
        svc.commit()
        return WatchlistMemberRead.model_validate(row)
    except WatchlistNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except InstrumentNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except (WatchlistArchivedError, WatchlistPermissionError) as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc


@router.post("/{watchlist_id}/instruments")
def register_watchlist_instrument(
    watchlist_id: UUID,
    req: WatchlistInstrumentCreate,
    request: Request,
    db: Session = Depends(get_db),
) -> dict:
    """用代码和名称登记/复用标的，并完成可重试的研究初始化。"""

    watchlist_service = WatchlistService(db)
    try:
        watchlist_service.get(watchlist_id)
        instrument, created = InstrumentService(db).ensure_cn_instrument(req.symbol, req.name)
        member = watchlist_service.add_member(
            watchlist_id,
            instrument.instrument_id,
            note="Dashboard 添加",
            permission="RESEARCH_WRITE",
        )
        watchlist_service.commit()
    except (WatchlistNotFoundError, InstrumentNotFoundError) as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except InvalidInstrumentSymbolError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except SymbolConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except (WatchlistArchivedError, WatchlistPermissionError) as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc

    bootstrap = InstrumentBootstrapService().run(
        db,
        request.app.state.backend_scheduler,
        instrument,
    )
    market_sync = next(
        (stage for stage in bootstrap["stages"] if stage["code"] == "market"),
        {"status": "FAILED", "message": "行情初始化未执行"},
    )

    return {
        "instrument": InstrumentRead.model_validate(instrument),
        "member": WatchlistMemberRead.model_validate(member),
        "created": created,
        "market_sync": market_sync,
        "bootstrap": bootstrap,
    }


@router.delete(
    "/{watchlist_id}/members/{instrument_id}",
    response_model=WatchlistMemberRead,
)
def remove_watchlist_member(
    watchlist_id: UUID,
    instrument_id: UUID,
    svc: WatchlistService = Depends(_svc),
) -> WatchlistMemberRead:
    try:
        row = svc.remove_member(watchlist_id, instrument_id, permission="RESEARCH_WRITE")
        svc.commit()
        return WatchlistMemberRead.model_validate(row)
    except (WatchlistNotFoundError, WatchlistMemberNotFoundError) as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except (WatchlistArchivedError, WatchlistPermissionError) as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
