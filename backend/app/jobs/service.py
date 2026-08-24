"""Read facade for scheduler state."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy.orm import Session

from app.jobs.models import JobRun

__all__ = ["JobService"]


class JobService:
    def get(self, session: Session, job_run_id: UUID) -> JobRun | None:
        return session.get(JobRun, job_run_id)
