"""Daily context/brief REST adapters."""

from __future__ import annotations

from datetime import date
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.briefing.service import BriefingDomainError, BriefingService
from app.common.database import get_db

router = APIRouter(prefix="/briefing")


class ContextCreate(BaseModel):
    market_date: date
    instruments: list[UUID] = Field(default_factory=list)


class BriefCreate(BaseModel):
    daily_context_id: UUID
    market_date: date
    content_md: str = Field(min_length=1)
    sections: list[dict] | None = None
    model_profile: str = Field(min_length=1)


def _service() -> BriefingService:
    return BriefingService.from_settings()


@router.post("/contexts", status_code=201)
def build_context(req: ContextCreate, db: Session = Depends(get_db)) -> dict:
    row = _service().build_daily_context(db, req.market_date, instruments=req.instruments)
    db.commit()
    return {"daily_context_id": str(row.daily_context_id), "market_date": row.market_date.isoformat(),
            "freshness_status": row.freshness_status, "data_freshness": row.data_freshness}


@router.get("/contexts/{market_date}")
def get_context(market_date: date, db: Session = Depends(get_db)) -> dict:
    row = _service().get_daily_context(db, market_date)
    if row is None:
        raise HTTPException(status_code=404, detail="daily context 不存在")
    return {"daily_context_id": str(row.daily_context_id), "market_date": row.market_date.isoformat(),
            "freshness_status": row.freshness_status, "data_freshness": row.data_freshness,
            "markets": row.markets, "engine_versions": row.engine_versions}


@router.post("/briefs", status_code=201)
def save_brief(req: BriefCreate, db: Session = Depends(get_db)) -> dict:
    try:
        row = _service().save_daily_brief(
            db, req.daily_context_id, req.market_date, req.content_md,
            sections=req.sections, model_profile=req.model_profile,
        )
        db.commit()
        return {"daily_brief_id": str(row.daily_brief_id), "market_date": row.market_date.isoformat(),
                "status": row.status, "model_profile": row.model_profile}
    except BriefingDomainError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("/briefs/{market_date}")
def get_brief(market_date: date, db: Session = Depends(get_db)) -> dict:
    row = _service().get_daily_brief(db, market_date)
    if row is None:
        raise HTTPException(status_code=404, detail="daily brief 不存在")
    return {
        "daily_brief_id": str(row.daily_brief_id), "daily_context_id": str(row.daily_context_id),
        "market_date": row.market_date.isoformat(), "content_md": row.content_md,
        "sections": row.sections, "model_profile": row.model_profile, "status": row.status,
    }
