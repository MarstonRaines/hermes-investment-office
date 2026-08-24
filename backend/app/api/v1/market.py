"""Market REST adapters: PIT reads only; synchronization is an async job."""

from __future__ import annotations

from datetime import UTC, date, datetime, time
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.briefing.service import BriefingService
from app.common.database import get_db
from app.market_data.service import MarketDataService

router = APIRouter(prefix="/market")


def _service() -> MarketDataService:
    return MarketDataService.from_settings()


@router.get("/snapshot")
def get_snapshot(instrument_ids: list[UUID], as_of: date, db: Session = Depends(get_db)) -> dict:
    rows = []
    provenance = []
    latest = as_of
    service = _service()
    for instrument_id in instrument_ids:
        bars = service.get_ohlcva(db, instrument_id, as_of=as_of)
        if bars:
            row = bars[-1]
            latest = min(latest, row["trade_date"])
            rows.append({"instrument_id": str(instrument_id), **row})
            provenance.extend(service.provenance_view(db, instrument_id, as_of=as_of, limit=1))
        else:
            rows.append({"instrument_id": str(instrument_id), "trade_date": None, "close": None})
    return {
        "data": {"snapshots": rows}, "as_of": datetime.combine(latest, time.max, tzinfo=UTC).isoformat(),
        "provenance": provenance,
        "freshness": BriefingService.from_settings().freshness_as_of(db, as_of),
    }


@router.get("/history")
def get_history(
    instrument_id: UUID, start_date: date, end_date: date, as_of: date | None = None,
    db: Session = Depends(get_db),
) -> dict:
    if start_date > end_date:
        raise HTTPException(status_code=422, detail="start_date 不能晚于 end_date")
    pit = as_of or end_date
    service = _service()
    bars = service.get_ohlcva(db, instrument_id, start=start_date, end=end_date, as_of=pit)
    return {
        "data": {"instrument_id": str(instrument_id), "bars": bars},
        "as_of": datetime.combine(pit, time.max, tzinfo=UTC).isoformat(),
        "provenance": service.provenance_view(db, instrument_id, start=start_date, end=end_date, as_of=pit),
        "freshness": BriefingService.from_settings().freshness_as_of(db, pit),
    }
