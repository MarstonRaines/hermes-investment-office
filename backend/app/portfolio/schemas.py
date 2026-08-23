# backend/app/portfolio/schemas.py —— portfolio 域（节选核心）
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field, model_validator

from app.common.enums import (
    PortfolioMode,
    PortfolioStatus,
    ProposalType,
    TradeProposalStatus,
    TransactionSource,
    TransactionType,
)
from app.common.schemas import ORMModel


class PortfolioCreate(BaseModel):
    name: str
    mode: PortfolioMode
    base_currency: Literal["CNY"] = "CNY"      # 冻结：组合单币种 CNY（DB CHECK 同步兜底）


class PortfolioRead(ORMModel):
    portfolio_id: UUID
    name: str
    mode: PortfolioMode
    base_currency: str
    status: PortfolioStatus
    created_at: datetime
    updated_at: datetime


class TransactionCreate(BaseModel):
    """落账入参（PAPER 由 Hermes 调；REAL 只能经 ACCOUNT_WRITE 人工入口）。"""
    portfolio_id: UUID
    account_id: UUID | None = None
    instrument_id: UUID | None = None
    transaction_type: TransactionType
    quantity: Decimal | None = None
    price_cny: Decimal | None = None
    amount_cny: Decimal
    fees_cny: Decimal = Decimal("0")
    tax_cny: Decimal = Decimal("0")
    trade_at: datetime
    trade_date: date
    source: TransactionSource
    reverses_transaction_id: UUID | None = None
    note: str | None = None
    provenance_id: UUID | None = None
    created_by: str

    @model_validator(mode="after")
    def _instrument_required_for_securities(self) -> "TransactionCreate":
        if self.transaction_type not in (TransactionType.CASH_IN, TransactionType.CASH_OUT) \
                and self.instrument_id is None:
            raise ValueError("非现金交易必须提供 instrument_id（DB CHECK 同步兜底）")
        if self.quantity is not None and self.quantity <= 0:
            raise ValueError("quantity 必须为正——符号由 Ledger 公式负责（ts02 §7.3 冻结）")
        return self


class TransactionRead(ORMModel):
    transaction_id: UUID
    portfolio_id: UUID
    account_id: UUID | None
    instrument_id: UUID | None
    transaction_type: TransactionType
    quantity: Decimal | None
    price_cny: Decimal | None
    amount_cny: Decimal
    fees_cny: Decimal
    tax_cny: Decimal
    trade_at: datetime
    trade_date: date
    source: TransactionSource
    reverses_transaction_id: UUID | None
    note: str | None
    provenance_id: UUID | None
    created_by: str
    created_at: datetime


class PositionRead(ORMModel):
    """派生持仓（只读）。"""
    instrument_id: UUID
    quantity: Decimal
    cost_basis_cny: Decimal
    market_price_cny: Decimal | None
    market_value_cny: Decimal | None
    realized_pnl_cny: Decimal | None
    unrealized_pnl_cny: Decimal | None
    is_qdii: bool


class PortfolioSnapshotRead(ORMModel):
    """get_portfolio_snapshot 出参（QDII 持仓仍按 A 股场内 CNY 市值计入 NAV，ts01 冻结）。"""
    portfolio_snapshot_id: UUID
    portfolio_id: UUID
    snapshot_date: date
    as_of: datetime
    cash_cny: Decimal
    market_value_cny: Decimal
    nav_cny: Decimal
    exposures: dict[str, Any] | None
    risk_summary: dict[str, Any] | None
    engine_version: str
    positions: list[PositionRead] = Field(default_factory=list)      # 服务层 join 组装


class TradeProposalCreate(BaseModel):
    """Hermes 对 REAL 组合能创建的最高权限对象（冻结规范 §33.1 PROPOSAL_WRITE）。"""
    portfolio_id: UUID
    instrument_id: UUID
    proposal_type: ProposalType
    quantity: Decimal = Field(gt=0)
    limit_price_cny: Decimal | None = None
    target_weight: Decimal | None = Field(default=None, ge=0, le=1)
    rationale: str | None = None
    thesis_revision_id: UUID | None = None
    created_by: str


class TradeProposalRead(ORMModel):
    trade_proposal_id: UUID
    portfolio_id: UUID
    instrument_id: UUID
    proposal_type: ProposalType
    quantity: Decimal
    limit_price_cny: Decimal | None
    target_weight: Decimal | None
    status: TradeProposalStatus
    rationale: str | None
    thesis_revision_id: UUID | None
    created_by: str
    approved_by: str | None
    approved_at: datetime | None
    executed_transaction_id: UUID | None
    created_at: datetime
    updated_at: datetime
