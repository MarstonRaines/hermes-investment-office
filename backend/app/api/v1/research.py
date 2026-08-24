"""Research/Evidence REST adapters."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.common.database import get_db
from app.common.enums import (
    ClaimType,
    Confidence,
    EvidenceDirection,
    EvidenceSourceType,
    WorkspaceSubjectType,
)
from app.research.service import ResearchDomainError, ResearchService

router = APIRouter(prefix="/research")


class WorkspaceCreate(BaseModel):
    title: str = Field(min_length=1)
    subject_type: WorkspaceSubjectType = WorkspaceSubjectType.GENERAL
    subject_id: UUID | None = None


class NoteCreate(BaseModel):
    title: str = Field(min_length=1)
    body_md: str = Field(min_length=1)
    workspace_id: UUID | None = None
    thread_id: UUID | None = None
    instrument_id: UUID | None = None


class EvidenceCreate(BaseModel):
    title: str = Field(min_length=1)
    claim: str = Field(min_length=1)
    source_type: EvidenceSourceType = EvidenceSourceType.DOCUMENT
    source_ref: str | None = None
    instrument_id: UUID | None = None
    direction: EvidenceDirection = EvidenceDirection.NEUTRAL
    claim_type: ClaimType = ClaimType.FACT
    confidence: Confidence = Confidence.MEDIUM
    observed_at: datetime | None = None
    published_at: datetime | None = None


@router.post("/workspaces", status_code=201)
def create_workspace(req: WorkspaceCreate, db: Session = Depends(get_db)) -> dict:
    row = ResearchService().create_workspace(
        db, req.title, subject_type=req.subject_type, subject_id=req.subject_id,
    )
    db.commit()
    return {"workspace_id": str(row.workspace_id), "title": row.title, "status": row.status}


@router.post("/notes", status_code=201)
def save_note(req: NoteCreate, db: Session = Depends(get_db)) -> dict:
    try:
        row = ResearchService().save_note(
            db, req.title, req.body_md, workspace_id=req.workspace_id,
            thread_id=req.thread_id, instrument_id=req.instrument_id, created_by="HUMAN",
        )
        db.commit()
        return {"research_note_id": str(row.research_note_id), "title": row.title,
                "provenance_id": str(row.provenance_id)}
    except ResearchDomainError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/evidence", status_code=201)
def add_evidence(req: EvidenceCreate, db: Session = Depends(get_db)) -> dict:
    row = ResearchService().add_evidence(
        db, title=req.title, claim=req.claim, source_type=req.source_type,
        source_ref=req.source_ref, instrument_id=req.instrument_id,
        direction=req.direction, claim_type=req.claim_type, confidence=req.confidence,
        observed_at=req.observed_at, published_at=req.published_at, created_by="HUMAN",
    )
    db.commit()
    return {"evidence_id": str(row.evidence_id), "title": row.title,
            "provenance_id": str(row.provenance_id)}


@router.get("/evidence")
def get_evidence(instrument_id: UUID | None = None, thesis_revision_id: UUID | None = None,
                 limit: int = 50, db: Session = Depends(get_db)) -> dict:
    rows = ResearchService().get_evidence(
        db, instrument_id=instrument_id, thesis_revision_id=thesis_revision_id, limit=limit,
    )
    return {"items": [{"evidence_id": str(row.evidence_id), "title": row.title,
                       "claim": row.claim, "direction": row.direction,
                       "confidence": row.confidence, "source_ref": row.source_ref,
                       "provenance_id": str(row.provenance_id)} for row in rows]}


@router.get("/search")
def search_research(query: str, limit: int = 20, db: Session = Depends(get_db)) -> dict:
    rows = ResearchService().search(db, query, limit=limit)
    return {"query": query, "items": [
        {"type": "research_note", "id": str(row.research_note_id), "title": row.title,
         "text": row.body_md} if hasattr(row, "research_note_id") else
        {"type": "evidence", "id": str(row.evidence_id), "title": row.title, "text": row.claim}
        for row in rows
    ]}


@router.get("/context")
def get_context(instrument_id: UUID | None = None, workspace_id: UUID | None = None,
                as_of: datetime | None = None, db: Session = Depends(get_db)) -> dict:
    return ResearchService().get_context(db, instrument_id=instrument_id,
                                         workspace_id=workspace_id, as_of=as_of)
