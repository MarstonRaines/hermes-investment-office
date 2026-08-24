"""Watchlist REST adapter (ADR-006)."""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.common.database import get_db
from app.instruments.schemas import (
    WatchlistMemberCreate,
    WatchlistMemberRead,
    WatchlistRead,
)
from app.instruments.service import (
    InstrumentNotFoundError,
    WatchlistArchivedError,
    WatchlistMemberNotFoundError,
    WatchlistNotFoundError,
    WatchlistPermissionError,
    WatchlistService,
)

router = APIRouter(prefix="/watchlists")


def _svc(db: Session = Depends(get_db)) -> WatchlistService:
    return WatchlistService(db)


def _read(row) -> WatchlistRead:
    return WatchlistRead.model_validate(row)


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
        row = svc.remove_member(
            watchlist_id, instrument_id, permission="RESEARCH_WRITE"
        )
        svc.commit()
        return WatchlistMemberRead.model_validate(row)
    except (WatchlistNotFoundError, WatchlistMemberNotFoundError) as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except (WatchlistArchivedError, WatchlistPermissionError) as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
