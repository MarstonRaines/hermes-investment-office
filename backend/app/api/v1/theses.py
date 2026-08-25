"""Thesis REST adapters with PIT and freshness semantics."""

from __future__ import annotations

from datetime import date, datetime
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.briefing.service import BriefingService
from app.common.database import get_db
from app.common.enums import ReviewConclusion, ReviewType, ThesisHealthStatus
from app.thesis.service import ThesisDomainError, ThesisService

router = APIRouter(prefix="/theses")


class ThesisCreate(BaseModel):
    instrument_id: UUID
    title: str = Field(min_length=1)
    body: dict


class RevisionCreate(BaseModel):
    base_revision_id: UUID
    change_reason: str = Field(min_length=1)
    thesis_body: dict
    freshness: dict | str = "OK"


class ReviewCreate(BaseModel):
    review_type: ReviewType
    conclusion: ReviewConclusion
    notes: str | None = None
    health_after: ThesisHealthStatus | None = None
    freshness: dict | str = "OK"


class AssumptionUpdate(BaseModel):
    status: ThesisHealthStatus
    test_condition: str | None = None
    note: str | None = None
    freshness: dict | str = "OK"


def _thesis_payload(row, revision) -> dict:
    return {
        "thesis_id": str(row.thesis_id), "instrument_id": str(row.instrument_id),
        "lifecycle_status": row.lifecycle_status, "health_status": row.health_status,
        "conviction": row.conviction,
        "current_revision": {
            "revision_id": str(revision.thesis_revision_id), "version": revision.version,
            "thesis_body": revision.thesis_body, "summary": revision.summary,
            "created_at": revision.created_at.isoformat(),
        } if revision else None,
    }


@router.post("", status_code=201)
def create_thesis(req: ThesisCreate, db: Session = Depends(get_db)) -> dict:
    row = ThesisService().create_thesis(db, req.instrument_id, req.title, req.body, authored_by="HUMAN")
    db.commit()
    return ThesisService().public_view(db, row.thesis_id)


@router.get("/{thesis_id}")
def get_thesis(thesis_id: UUID, as_of: datetime | None = None, db: Session = Depends(get_db)) -> dict:
    result = ThesisService().public_view(db, thesis_id, as_of=as_of)
    if result is None:
        raise HTTPException(status_code=404, detail="thesis revision 不存在")
    return result


@router.post("/{thesis_id}/revisions", status_code=201)
def create_revision(thesis_id: UUID, req: RevisionCreate, db: Session = Depends(get_db)) -> dict:
    try:
        freshness = BriefingService.from_settings().freshness_as_of(db, date.today())
        row = ThesisService().create_revision(
            db, thesis_id, req.thesis_body, base_revision_id=req.base_revision_id,
            authored_by="HUMAN", change_reason=req.change_reason, freshness=freshness,
        )
        db.commit()
        return {"thesis_id": str(thesis_id), "revision_id": str(row.thesis_revision_id),
                "version": row.version, "provenance_id": str(row.provenance_id)}
    except ThesisDomainError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/{thesis_id}/reviews", status_code=201)
def record_review(thesis_id: UUID, req: ReviewCreate, db: Session = Depends(get_db)) -> dict:
    try:
        freshness = BriefingService.from_settings().freshness_as_of(db, date.today())
        row = ThesisService().record_review(
            db, thesis_id, req.review_type, req.conclusion, actor_id="HUMAN",
            notes=req.notes, health_after=req.health_after, freshness=freshness,
        )
        db.commit()
        return {"review_id": str(row.review_id), "thesis_id": str(row.thesis_id),
                "conclusion": row.conclusion, "reviewed_at": row.reviewed_at.isoformat()}
    except ThesisDomainError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.patch("/assumptions/{assumption_id}")
def update_assumption(assumption_id: UUID, req: AssumptionUpdate, db: Session = Depends(get_db)) -> dict:
    try:
        freshness = BriefingService.from_settings().freshness_as_of(db, date.today())
        row = ThesisService().update_assumption(
            db, assumption_id, req.status, actor_id="HUMAN",
            test_condition=req.test_condition, note=req.note, freshness=freshness,
        )
        db.commit()
        return {"assumption_id": str(row.assumption_id), "thesis_id": str(row.thesis_id),
                "status": row.status}
    except ThesisDomainError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from exc
