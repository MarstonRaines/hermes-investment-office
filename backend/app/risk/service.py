"""Read facade that supplies persisted portfolio facts to the pure risk engine."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.portfolio.models import Portfolio, PortfolioSnapshot, PositionSnapshot
from app.risk.engine import compute_risk

__all__ = ["RiskService"]


class RiskService:
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
        return {"portfolio_id": str(portfolio_id), "snapshot_date": latest.isoformat() if latest else None,
                "exposure": compute_risk(positions=values, nav=nav)["exposure"]}

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
        history_stmt = select(PortfolioSnapshot.nav_cny).where(
            PortfolioSnapshot.portfolio_id == portfolio_id,
        )
        if cutoff is not None:
            history_stmt = history_stmt.where(PortfolioSnapshot.snapshot_date <= cutoff)
        history = list(session.scalars(history_stmt.order_by(PortfolioSnapshot.snapshot_date.asc())).all())
        nav = snapshot.nav_cny if snapshot else (history[-1] if history else Decimal("0"))
        return {"portfolio_id": str(portfolio_id), "risk": compute_risk(
            positions={row.instrument_id: row.market_value_cny or Decimal("0") for row in current},
            nav=nav, nav_history=history,
        )}
