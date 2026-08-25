"""Read facade that supplies persisted portfolio facts to the pure risk engine."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.instruments.models import Instrument
from app.portfolio.models import Portfolio, PortfolioSnapshot, PositionSnapshot
from app.risk.engine import compute_risk

__all__ = ["RiskService"]


class RiskService:
    def __init__(self, thresholds: dict | None = None) -> None:
        self.thresholds = thresholds or {}

    def _rows(self, session: Session, portfolio_id: UUID, cutoff: date | None):
        if session.get(Portfolio, portfolio_id) is None:
            return None
        stmt = select(PositionSnapshot).where(PositionSnapshot.portfolio_id == portfolio_id)
        if cutoff is not None:
            stmt = stmt.where(PositionSnapshot.snapshot_date <= cutoff)
        rows = list(session.scalars(stmt.order_by(PositionSnapshot.snapshot_date.desc())).all())
        latest = rows[0].snapshot_date if rows else None
        return latest, [row for row in rows if row.snapshot_date == latest]

    def exposure(self, session: Session, portfolio_id: UUID, cutoff: date | None = None) -> dict | None:
        found = self._rows(session, portfolio_id, cutoff)
        if found is None:
            return None
        latest, rows = found
        snapshot = session.scalar(select(PortfolioSnapshot).where(
            PortfolioSnapshot.portfolio_id == portfolio_id,
            PortfolioSnapshot.snapshot_date == latest,
        )) if latest else None
        values = {row.instrument_id: row.market_value_cny or Decimal("0") for row in rows}
        nav = snapshot.nav_cny if snapshot else sum(values.values(), Decimal("0"))
        instruments = {
            row.instrument_id: row.instrument_type
            for row in session.scalars(
                select(Instrument).where(Instrument.instrument_id.in_(values))
            ).all()
        } if values else {}
        return {"portfolio_id": str(portfolio_id), "snapshot_date": latest.isoformat() if latest else None,
                "exposure": compute_risk(
                    positions=values,
                    nav=nav,
                    cash_cny=snapshot.cash_cny if snapshot else Decimal("0"),
                    asset_classes=instruments,
                    thresholds=self.thresholds,
                    as_of=latest,
                )["exposure"]}

    def risk(self, session: Session, portfolio_id: UUID, cutoff: date | None = None) -> dict | None:
        found = self._rows(session, portfolio_id, cutoff)
        if found is None:
            return None
        _, current = found
        snapshot_stmt = select(PortfolioSnapshot).where(
            PortfolioSnapshot.portfolio_id == portfolio_id,
        )
        if cutoff is not None:
            snapshot_stmt = snapshot_stmt.where(PortfolioSnapshot.snapshot_date <= cutoff)
        snapshot = session.scalar(snapshot_stmt.order_by(PortfolioSnapshot.snapshot_date.desc()).limit(1))
        history_stmt = select(PortfolioSnapshot).where(
            PortfolioSnapshot.portfolio_id == portfolio_id,
        )
        if cutoff is not None:
            history_stmt = history_stmt.where(PortfolioSnapshot.snapshot_date <= cutoff)
        history = list(session.scalars(
            history_stmt.order_by(PortfolioSnapshot.snapshot_date.asc())
        ).all())
        nav = snapshot.nav_cny if snapshot else (
            history[-1].nav_cny if history else Decimal("0")
        )
        values = {
            row.instrument_id: row.market_value_cny or Decimal("0") for row in current
        }
        instruments = {
            row.instrument_id: row.instrument_type
            for row in session.scalars(
                select(Instrument).where(Instrument.instrument_id.in_(values))
            ).all()
        } if values else {}
        return {"portfolio_id": str(portfolio_id), "risk": compute_risk(
            positions=values,
            nav=nav,
            nav_history=[row.nav_cny for row in history],
            snapshot_dates=[row.snapshot_date for row in history],
            cash_cny=snapshot.cash_cny if snapshot else Decimal("0"),
            asset_classes=instruments,
            thresholds=self.thresholds,
            as_of=snapshot.as_of if snapshot else None,
        )}
