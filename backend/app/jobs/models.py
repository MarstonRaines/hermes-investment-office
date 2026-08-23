# backend/app/jobs/models.py —— 模块归属：jobs（Job Worker）
from datetime import datetime
from uuid import UUID

from sqlalchemy import Index, Text, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.common.base import Base
from app.common.db import enum_ck
from app.common.enums import JobStatus, JobType
from app.common.mixins import CreatedAtMixin, UUIDPrimaryKeyMixin
from app.common.types import TIMESTAMPTZ

pk = UUIDPrimaryKeyMixin.pk


class JobRun(Base, CreatedAtMixin):
    """一次 job 执行。job_type=INGESTION 的 job_run_id 被 provenance_records.ingestion_run_id 引用
    （ts02 §2 注：不新增第 41 张表）。"""
    __tablename__ = "job_runs"
    __table_args__ = (
        Index("ix_job_runs_name_created", "job_name", text("created_at DESC")),
        enum_ck("job_runs", "job_type", JobType),
        enum_ck("job_runs", "status", JobStatus),
    )
    job_run_id: Mapped[UUID] = pk("job_run_id")
    job_name: Mapped[str] = mapped_column(Text, nullable=False)
    job_type: Mapped[JobType] = mapped_column(Text, nullable=False)       # SYNC / COMPUTE / INGESTION / BRIEF
    status: Mapped[JobStatus] = mapped_column(Text, nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(TIMESTAMPTZ)
    finished_at: Mapped[datetime | None] = mapped_column(TIMESTAMPTZ)
    error: Mapped[str | None] = mapped_column(Text)                       # 失败必须记录，不可静默吞掉（§35.4）
    input_version: Mapped[str | None] = mapped_column(Text)
    output_version: Mapped[str | None] = mapped_column(Text)
    params: Mapped[dict | None] = mapped_column(JSONB)
