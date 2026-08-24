"""Read facade for PIT fundamental facts and filing references."""

from __future__ import annotations

from datetime import date, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.fundamentals.models import FinancialFact
from app.fundamentals.repository import get_latest_financial_fact_pit

__all__ = ["FundamentalsService"]


class FundamentalsService:
    def get_latest(self, session: Session, instrument_id: UUID, metric_code: str, as_of: datetime):
        return get_latest_financial_fact_pit(session, instrument_id, metric_code, as_of)

    def history(
        self,
        session: Session,
        instrument_id: UUID,
        as_of: datetime,
        *,
        metrics: list[str] | None = None,
        start_period: date | None = None,
        end_period: date | None = None,
    ) -> list[FinancialFact]:
        stmt = select(FinancialFact).where(
            FinancialFact.instrument_id == instrument_id,
            FinancialFact.published_at.is_not(None),
            FinancialFact.published_at <= as_of,
        )
        if metrics:
            stmt = stmt.where(FinancialFact.metric_code.in_(metrics))
        if start_period:
            stmt = stmt.where(FinancialFact.period_end >= start_period)
        if end_period:
            stmt = stmt.where(FinancialFact.period_end <= end_period)
        return list(session.scalars(
            stmt.order_by(FinancialFact.period_end.asc(), FinancialFact.metric_code.asc())
        ).all())

    def filings(self, session: Session, instrument_id: UUID, as_of: datetime, *, limit: int = 20):
        return list(session.scalars(
            select(FinancialFact).where(
                FinancialFact.instrument_id == instrument_id,
                FinancialFact.published_at.is_not(None),
                FinancialFact.published_at <= as_of,
            ).order_by(FinancialFact.published_at.desc()).limit(limit)
        ).all())
