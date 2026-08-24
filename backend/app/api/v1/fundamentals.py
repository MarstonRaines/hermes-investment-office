"""PIT fundamental REST adapters."""

from __future__ import annotations

from datetime import date, datetime
from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.common.database import get_db
from app.fundamentals.service import FundamentalsService

router = APIRouter(prefix="/fundamentals")


@router.get("")
def get_fundamentals(
    instrument_id: UUID, as_of: datetime, metrics: list[str] | None = None,
    db: Session = Depends(get_db),
) -> dict:
    service = FundamentalsService()
    rows = [service.get_latest(db, instrument_id, metric, as_of) for metric in (metrics or [])]
    rows = [row for row in rows if row is not None]
    return {
        "data": {"instrument_id": str(instrument_id), "metrics": {
            row.metric_code: {"value": str(row.value), "unit": row.unit,
                              "period_end": row.period_end.isoformat(),
                              "published_at": row.published_at.isoformat() if row.published_at else None}
            for row in rows
        }},
        "as_of": as_of.isoformat(),
        "provenance": [{"provenance_id": str(row.provenance_id), "source": row.provider,
                        "provider": row.provider, "quality_status": str(row.quality_status)} for row in rows],
    }


@router.get("/history")
def get_history(
    instrument_id: UUID, as_of: datetime, start_period: date | None = None,
    end_period: date | None = None, metrics: list[str] | None = None,
    db: Session = Depends(get_db),
) -> dict:
    rows = FundamentalsService().history(
        db, instrument_id, as_of, metrics=metrics,
        start_period=start_period, end_period=end_period,
    )
    return {"data": {"instrument_id": str(instrument_id), "items": [
        {"financial_fact_id": str(row.financial_fact_id), "metric_code": row.metric_code,
         "period_end": row.period_end.isoformat(), "value": str(row.value), "unit": row.unit,
         "published_at": row.published_at.isoformat() if row.published_at else None}
        for row in rows
    ]}, "as_of": as_of.isoformat(), "provenance": [
        {"provenance_id": str(row.provenance_id), "source": row.provider,
         "provider": row.provider, "quality_status": str(row.quality_status)} for row in rows
    ]}
