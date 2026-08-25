"""Dashboard 专用只读聚合 API。"""

from __future__ import annotations

from datetime import date
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.common.database import get_db
from app.office.service import OfficeService

router = APIRouter(prefix="/office")


@router.get("/today")
def today(as_of: date | None = None, db: Session = Depends(get_db)) -> dict:
    return OfficeService().today(db, as_of or date.today())


@router.get("/watchlist")
def watchlist(as_of: date | None = None, db: Session = Depends(get_db)) -> dict:
    return OfficeService().watchlist(db, as_of or date.today())


@router.get("/instruments/{instrument_id}")
def instrument(
    instrument_id: UUID, as_of: date | None = None, db: Session = Depends(get_db),
) -> dict:
    payload = OfficeService().instrument(db, instrument_id, as_of or date.today())
    if payload is None:
        raise HTTPException(status_code=404, detail="标的不存在")
    return payload


@router.get("/portfolios")
def portfolios(
    as_of: date | None = None, portfolio_id: UUID | None = None,
    db: Session = Depends(get_db),
) -> dict:
    return OfficeService().portfolios(db, as_of or date.today(), portfolio_id)


@router.get("/review")
def review(as_of: date | None = None, db: Session = Depends(get_db)) -> dict:
    return OfficeService().review(db, as_of or date.today())


@router.get("/system")
def system(as_of: date | None = None, db: Session = Depends(get_db)) -> dict:
    return OfficeService().system_status(db, as_of or date.today())
