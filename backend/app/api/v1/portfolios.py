"""Portfolio REST adapters, including the explicit localhost ACCOUNT_WRITE gate."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.common.database import get_db
from app.common.enums import PortfolioMode, ProposalType, TransactionSource, TransactionType
from app.portfolio.service import PortfolioDomainError, PortfolioService

router = APIRouter(prefix="/portfolios")


class PortfolioCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    mode: PortfolioMode = PortfolioMode.PAPER


class TransactionCreate(BaseModel):
    transaction_type: TransactionType
    amount_cny: Decimal
    trade_date: date
    instrument_id: UUID | None = None
    quantity: Decimal | None = Field(default=None, gt=0)
    price_cny: Decimal | None = Field(default=None, gt=0)
    fees_cny: Decimal = Field(default=Decimal("0"), ge=0)
    note: str | None = None


class ProposalCreate(BaseModel):
    instrument_id: UUID
    proposal_type: ProposalType
    quantity: Decimal = Field(gt=0)
    limit_price_cny: Decimal | None = Field(default=None, gt=0)
    target_weight: Decimal | None = Field(default=None, ge=0, le=1)
    rationale: str | None = None
    thesis_revision_id: UUID | None = None
    linked_valuation_run_id: UUID | None = None
    freshness: dict | str = "OK"


def _service() -> PortfolioService:
    return PortfolioService()


@router.post("", status_code=201)
def create_portfolio(req: PortfolioCreate, db: Session = Depends(get_db)) -> dict:
    row = PortfolioService().create_portfolio(db, req.name, mode=req.mode)
    db.commit()
    return {"portfolio_id": str(row.portfolio_id), "name": row.name, "mode": row.mode,
            "status": row.status, "base_currency": row.base_currency}


@router.get("")
def list_portfolios(db: Session = Depends(get_db)) -> dict:
    rows = PortfolioService().list_portfolios(db)
    return {"items": [{"portfolio_id": str(row.portfolio_id), "name": row.name,
                        "mode": row.mode, "status": row.status, "base_currency": row.base_currency}
                       for row in rows]}


@router.get("/{portfolio_id}")
def get_portfolio(portfolio_id: UUID, db: Session = Depends(get_db)) -> dict:
    result = PortfolioService().get_portfolio(db, portfolio_id)
    if result is None:
        raise HTTPException(status_code=404, detail="组合不存在")
    row, snapshot = result
    return {"portfolio_id": str(row.portfolio_id), "name": row.name, "mode": row.mode,
            "status": row.status, "base_currency": row.base_currency,
            "snapshot": {
                "snapshot_date": snapshot.snapshot_date.isoformat(), "nav_cny": str(snapshot.nav_cny),
                "cash_cny": str(snapshot.cash_cny), "market_value_cny": str(snapshot.market_value_cny),
            } if snapshot else None}


@router.get("/{portfolio_id}/positions")
def get_positions(portfolio_id: UUID, db: Session = Depends(get_db)) -> dict:
    if PortfolioService().get_portfolio(db, portfolio_id) is None:
        raise HTTPException(status_code=404, detail="组合不存在")
    snapshot_date, rows = PortfolioService().get_positions(db, portfolio_id)
    return {"portfolio_id": str(portfolio_id), "snapshot_date": snapshot_date.isoformat() if snapshot_date else None,
            "positions": [{"instrument_id": str(row.instrument_id), "quantity": str(row.quantity),
                           "market_value_cny": str(row.market_value_cny) if row.market_value_cny is not None else None,
                           "unrealized_pnl_cny": str(row.unrealized_pnl_cny) if row.unrealized_pnl_cny is not None else None}
                          for row in rows]}


@router.post("/{portfolio_id}/transactions")
def post_transaction(
    portfolio_id: UUID, req: TransactionCreate,
    x_account_write: str | None = Header(default=None),
    db: Session = Depends(get_db),
) -> dict:
    if x_account_write != "ACCOUNT_WRITE":
        raise HTTPException(status_code=403, detail="需要人工 ACCOUNT_WRITE 入口")
    try:
        row = PortfolioService().post_transaction(
            db, portfolio_id, req.transaction_type,
            instrument_id=req.instrument_id, quantity=req.quantity, price_cny=req.price_cny,
            amount_cny=req.amount_cny, fees_cny=req.fees_cny, trade_date=req.trade_date,
            source=TransactionSource.MANUAL, note=req.note,
        )
        db.commit()
        return {"transaction_id": str(row.transaction_id), "transaction_type": row.transaction_type,
                "portfolio_id": str(row.portfolio_id), "provenance_id": str(row.provenance_id)}
    except PortfolioDomainError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/{portfolio_id}/transactions/{transaction_id}/reversal")
def reverse_transaction(
    portfolio_id: UUID, transaction_id: UUID, trade_date: date,
    x_account_write: str | None = Header(default=None), db: Session = Depends(get_db),
) -> dict:
    if x_account_write != "ACCOUNT_WRITE":
        raise HTTPException(status_code=403, detail="需要人工 ACCOUNT_WRITE 入口")
    try:
        row = PortfolioService().reverse_transaction(
            db, portfolio_id, transaction_id, trade_date=trade_date, permission="ACCOUNT_WRITE",
        )
        db.commit()
        return {"transaction_id": str(row.transaction_id), "transaction_type": row.transaction_type,
                "reverses_transaction_id": str(row.reverses_transaction_id)}
    except PortfolioDomainError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/{portfolio_id}/proposals", status_code=201)
def create_proposal(portfolio_id: UUID, req: ProposalCreate, db: Session = Depends(get_db)) -> dict:
    try:
        row = PortfolioService().create_trade_proposal(
            db, portfolio_id, req.instrument_id, req.proposal_type,
            quantity=req.quantity, limit_price_cny=req.limit_price_cny,
            target_weight=req.target_weight, rationale=req.rationale,
            thesis_revision_id=req.thesis_revision_id,
            linked_valuation_run_id=req.linked_valuation_run_id,
            freshness=req.freshness,
        )
        db.commit()
        return {"trade_proposal_id": str(row.trade_proposal_id), "status": row.status,
                "provenance_id": str(row.provenance_id)}
    except PortfolioDomainError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from exc
