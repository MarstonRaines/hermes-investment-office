"""Valuation REST adapters; all calculations stay in ValuationService/Engine."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.common.database import get_db
from app.valuation.schemas import ValuationAssumptionInput
from app.valuation.service import ValuationRequest, ValuationService

router = APIRouter(prefix="/valuations")


class ValuationCreate(BaseModel):
    instrument_id: UUID
    model_type: str
    as_of: datetime
    fcf_forecast: list[Decimal] = Field(default_factory=list)
    assumptions: list[ValuationAssumptionInput] = Field(min_length=1)


def _service() -> ValuationService:
    from app.market_data.service import MarketDataService

    return ValuationService(MarketDataService.from_settings())


def _public(run) -> dict:
    return {
        "valuation_run_id": str(run.valuation_run_id), "instrument_id": str(run.instrument_id),
        "model_type": str(getattr(run.model_type, "value", run.model_type)),
        "status": str(getattr(run.status, "value", run.status)), "as_of": run.as_of.isoformat(),
        "engine_version": run.engine_version, "base_value": str(run.base_value) if run.base_value is not None else None,
        "bear_value": str(run.bear_value) if run.bear_value is not None else None,
        "bull_value": str(run.bull_value) if run.bull_value is not None else None,
        "current_price": str(run.current_price) if run.current_price is not None else None,
        "margin_of_safety": str(run.margin_of_safety) if run.margin_of_safety is not None else None,
        "result": run.result_json,
    }


@router.post("", status_code=201)
def run_valuation(req: ValuationCreate, db: Session = Depends(get_db)) -> dict:
    try:
        run = _service().run_valuation(db, ValuationRequest(
            instrument_id=req.instrument_id, model_type=req.model_type,
            as_of=req.as_of.date(), assumptions=req.assumptions,
            fcf_forecast=req.fcf_forecast, created_by="HUMAN",
        ))
        return {"data": _public(run), "as_of": run.as_of.isoformat(),
                "provenance": [{"provenance_id": str(run.valuation_run_id),
                                "source": "valuation_engine", "provider": "internal",
                                "quality_status": "VERIFIED"}]}
    except Exception as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("/{instrument_id}/latest")
def get_latest(instrument_id: UUID, as_of: datetime | None = None, db: Session = Depends(get_db)) -> dict:
    run = _service().latest(db, instrument_id, as_of)
    if run is None:
        raise HTTPException(status_code=404, detail="没有可见估值结果")
    return {"data": _public(run), "as_of": run.as_of.isoformat(), "provenance": []}


@router.get("/{instrument_id}/history")
def get_history(instrument_id: UUID, as_of: datetime | None = None, limit: int = 50,
                db: Session = Depends(get_db)) -> dict:
    rows = _service().history(db, instrument_id, as_of=as_of, limit=limit)
    return {"data": {"instrument_id": str(instrument_id), "runs": [_public(row) for row in rows]},
            "as_of": (as_of or datetime.now(UTC)).isoformat(), "provenance": []}
