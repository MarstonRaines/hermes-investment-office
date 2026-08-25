"""Portfolio REST adapters, including the explicit localhost ACCOUNT_WRITE gate."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field, model_validator
from sqlalchemy.orm import Session

from app.briefing.service import BriefingService
from app.common.database import get_db
from app.common.enums import (
    PortfolioMode,
    ProposalType,
    TradeProposalStatus,
    TransactionSource,
    TransactionType,
)
from app.common.freshness import FreshnessGateError
from app.market_data.service import MarketDataService
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

    @model_validator(mode="after")
    def validate_transaction(self) -> TransactionCreate:
        if self.transaction_type in {TransactionType.BUY, TransactionType.SELL} and (
            self.instrument_id is None or self.quantity is None or self.price_cny is None
        ):
            raise ValueError("买入或卖出必须选择标的并填写数量、成交价")
        if self.transaction_type in {TransactionType.DIVIDEND, TransactionType.FEE} \
                and self.instrument_id is None:
            raise ValueError("分红或费用必须选择标的")
        return self


class OpeningPositionCreate(BaseModel):
    instrument_id: UUID
    quantity: Decimal = Field(gt=0)
    average_cost_cny: Decimal = Field(gt=0)
    holding_date: date
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


class ProposalTransition(BaseModel):
    status: TradeProposalStatus
    trade_date: date | None = None
    price_cny: Decimal | None = Field(default=None, gt=0)
    quantity: Decimal | None = Field(default=None, gt=0)
    fees_cny: Decimal = Field(default=Decimal("0"), ge=0)

    @model_validator(mode="after")
    def validate_execution(self) -> ProposalTransition:
        if self.status is TradeProposalStatus.EXECUTED and (
            self.trade_date is None or self.price_cny is None or self.quantity is None
        ):
            raise ValueError("登记实际成交必须填写日期、价格和数量")
        return self


def _service() -> PortfolioService:
    return PortfolioService()


def _refresh_snapshot(
    db: Session,
    portfolio_id: UUID,
    *,
    fallback_prices: dict[UUID, Decimal] | None = None,
    snapshot_date: date | None = None,
) -> dict:
    """在账户写入事务内重建当前快照；无行情时显式退回成本价。"""

    service = PortfolioService()
    effective_date = snapshot_date or date.today()
    replayed = service.replay_portfolio(db, portfolio_id, as_of=effective_date)
    market = MarketDataService.from_settings()
    prices: dict[UUID, Decimal] = {}
    for instrument_id, position in replayed.positions.items():
        bars = market.get_ohlcva(db, instrument_id, as_of=effective_date)
        latest = bars[-1].get("close") if bars else None
        if latest is not None:
            prices[instrument_id] = Decimal(str(latest))
        elif fallback_prices and instrument_id in fallback_prices:
            prices[instrument_id] = fallback_prices[instrument_id]
        elif position.avg_cost > 0:
            prices[instrument_id] = position.avg_cost
    return service.snapshot(db, portfolio_id, effective_date, prices)


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


@router.get("/{portfolio_id}/transactions")
def get_transactions(portfolio_id: UUID, limit: int = 200, db: Session = Depends(get_db)) -> dict:
    try:
        rows = PortfolioService().list_transactions(db, portfolio_id, limit=limit)
    except PortfolioDomainError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"portfolio_id": str(portfolio_id), "items": [{
        "transaction_id": str(row.transaction_id),
        "instrument_id": str(row.instrument_id) if row.instrument_id else None,
        "transaction_type": row.transaction_type,
        "quantity": str(row.quantity) if row.quantity is not None else None,
        "price_cny": str(row.price_cny) if row.price_cny is not None else None,
        "amount_cny": str(row.amount_cny),
        "fees_cny": str(row.fees_cny),
        "trade_date": row.trade_date.isoformat(),
        "source": row.source,
        "note": row.note,
        "reverses_transaction_id": (
            str(row.reverses_transaction_id) if row.reverses_transaction_id else None
        ),
        "created_at": row.created_at.isoformat(),
    } for row in rows]}


@router.get("/{portfolio_id}/proposals")
def get_proposals(portfolio_id: UUID, limit: int = 100, db: Session = Depends(get_db)) -> dict:
    try:
        rows = PortfolioService().list_proposals(db, portfolio_id, limit=limit)
    except PortfolioDomainError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"portfolio_id": str(portfolio_id), "items": [{
        "trade_proposal_id": str(row.trade_proposal_id),
        "instrument_id": str(row.instrument_id),
        "proposal_type": row.proposal_type,
        "quantity": str(row.quantity),
        "limit_price_cny": str(row.limit_price_cny) if row.limit_price_cny is not None else None,
        "target_weight": str(row.target_weight) if row.target_weight is not None else None,
        "status": row.status,
        "rationale": row.rationale,
        "created_by": row.created_by,
        "approved_at": row.approved_at.isoformat() if row.approved_at else None,
        "executed_transaction_id": (
            str(row.executed_transaction_id) if row.executed_transaction_id else None
        ),
        "created_at": row.created_at.isoformat(),
    } for row in rows]}


@router.post("/{portfolio_id}/opening-positions", status_code=201)
def post_opening_position(
    portfolio_id: UUID,
    req: OpeningPositionCreate,
    x_account_write: str | None = Header(default=None),
    db: Session = Depends(get_db),
) -> dict:
    if x_account_write != "ACCOUNT_WRITE":
        raise HTTPException(status_code=403, detail="需要人工 ACCOUNT_WRITE 入口")
    try:
        capital, position = PortfolioService().record_opening_position(
            db,
            portfolio_id,
            req.instrument_id,
            quantity=req.quantity,
            average_cost_cny=req.average_cost_cny,
            holding_date=req.holding_date,
            note=req.note,
        )
        snapshot = _refresh_snapshot(
            db,
            portfolio_id,
            fallback_prices={req.instrument_id: req.average_cost_cny},
        )
        db.commit()
        return {
            "transaction_id": str(position.transaction_id),
            "paired_cash_transaction_id": str(capital.transaction_id),
            "portfolio_id": str(portfolio_id),
            "snapshot": {key: str(value) for key, value in snapshot.items() if key != "positions"}
            | {"positions": snapshot["positions"]},
        }
    except PortfolioDomainError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from exc


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
        fallback = (
            {req.instrument_id: req.price_cny}
            if req.instrument_id is not None and req.price_cny is not None else None
        )
        snapshot = _refresh_snapshot(db, portfolio_id, fallback_prices=fallback)
        db.commit()
        return {"transaction_id": str(row.transaction_id), "transaction_type": row.transaction_type,
                "portfolio_id": str(row.portfolio_id), "provenance_id": str(row.provenance_id),
                "snapshot": {key: str(value) for key, value in snapshot.items()}}
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
        _refresh_snapshot(db, portfolio_id)
        db.commit()
        return {"transaction_id": str(row.transaction_id), "transaction_type": row.transaction_type,
                "reverses_transaction_id": str(row.reverses_transaction_id)}
    except PortfolioDomainError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/{portfolio_id}/proposals", status_code=201)
def create_proposal(portfolio_id: UUID, req: ProposalCreate, db: Session = Depends(get_db)) -> dict:
    try:
        freshness = BriefingService.from_settings().freshness_as_of(db, date.today())
        row = PortfolioService().create_trade_proposal(
            db, portfolio_id, req.instrument_id, req.proposal_type,
            quantity=req.quantity, limit_price_cny=req.limit_price_cny,
            target_weight=req.target_weight, rationale=req.rationale,
            thesis_revision_id=req.thesis_revision_id,
            linked_valuation_run_id=req.linked_valuation_run_id,
            freshness=freshness,
        )
        db.commit()
        return {"trade_proposal_id": str(row.trade_proposal_id), "status": row.status,
                "provenance_id": str(row.provenance_id)}
    except (PortfolioDomainError, FreshnessGateError) as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/{portfolio_id}/proposals/{proposal_id}/transition")
def transition_proposal(
    portfolio_id: UUID, proposal_id: UUID, req: ProposalTransition,
    x_account_write: str | None = Header(default=None), db: Session = Depends(get_db),
) -> dict:
    if x_account_write != "ACCOUNT_WRITE":
        raise HTTPException(status_code=403, detail="需要人工 ACCOUNT_WRITE 入口")
    try:
        existing = PortfolioService().get_proposal(db, portfolio_id, proposal_id)
        if existing is None:
            raise PortfolioDomainError("proposal 不属于该组合")
        proposal = PortfolioService().transition_proposal(
            db, proposal_id, req.status.value, actor="HUMAN", permission="ACCOUNT_WRITE",
            trade_date=req.trade_date, price_cny=req.price_cny,
            quantity=req.quantity, fees_cny=req.fees_cny,
        )
        if req.status is TradeProposalStatus.EXECUTED:
            fallback = (
                {proposal.instrument_id: req.price_cny}
                if req.price_cny is not None else None
            )
            _refresh_snapshot(db, portfolio_id, fallback_prices=fallback)
        db.commit()
        return {
            "trade_proposal_id": str(proposal.trade_proposal_id),
            "portfolio_id": str(proposal.portfolio_id), "status": proposal.status,
            "executed_transaction_id": (
                str(proposal.executed_transaction_id) if proposal.executed_transaction_id else None
            ),
        }
    except PortfolioDomainError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from exc
