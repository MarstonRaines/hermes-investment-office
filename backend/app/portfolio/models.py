# backend/app/portfolio/models.py —— 模块归属：portfolio（Portfolio Ledger Service）
from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    ForeignKey,
    Index,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.common.base import Base
from app.common.db import enum_ck, range_ck
from app.common.enums import (
    AccountType,
    PortfolioMode,
    PortfolioStatus,
    ProposalType,
    TradeProposalStatus,
    TransactionSource,
    TransactionType,
)
from app.common.mixins import CreatedAtMixin, TimestampMixin, UUIDPrimaryKeyMixin
from app.common.types import MONEY, QTY, RATIO, TIMESTAMPTZ

pk = UUIDPrimaryKeyMixin.pk


class Portfolio(Base, TimestampMixin):
    """组合身份。base_currency 恒 CNY（DB CHECK 冻结）；REAL/PAPER 完全隔离。"""
    __tablename__ = "portfolios"
    __table_args__ = (
        CheckConstraint("base_currency = 'CNY'", name="base_currency_cny"),
        enum_ck("portfolios", "mode", PortfolioMode),
        enum_ck("portfolios", "status", PortfolioStatus),
    )
    portfolio_id: Mapped[UUID] = pk("portfolio_id")
    name: Mapped[str] = mapped_column(Text, nullable=False)
    mode: Mapped[PortfolioMode] = mapped_column(Text, nullable=False)
    base_currency: Mapped[str] = mapped_column(Text, nullable=False, server_default="CNY")
    status: Mapped[PortfolioStatus] = mapped_column(Text, nullable=False, server_default="ACTIVE")


class Account(Base, CreatedAtMixin):
    __tablename__ = "accounts"
    __table_args__ = (enum_ck("accounts", "account_type", AccountType),)
    account_id: Mapped[UUID] = pk("account_id")
    portfolio_id: Mapped[UUID] = mapped_column(ForeignKey("portfolios.portfolio_id"), nullable=False)
    account_type: Mapped[AccountType] = mapped_column(Text, nullable=False)   # CASH / BROKERAGE
    name: Mapped[str] = mapped_column(Text, nullable=False)
    currency: Mapped[str] = mapped_column(Text, nullable=False, server_default="CNY")
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default="ACTIVE")


class PortfolioTransaction(Base, CreatedAtMixin):
    """Transaction Ledger —— 组合唯一事实来源（append-only，核心表）。

    冻结约定（ts02 §7.3，与 TS-06 Portfolio Engine 对齐）：
      - amount_cny 现金视角：BUY 负 / SELL 正 / DIVIDEND 正 / FEE 负；
        数据层存"绝对值语义 + 公式负责符号"，禁止混用正负约定；
      - 纠错 = REVERSAL 新记录（reverses_transaction_id 指向原单），禁止 UPDATE；
      - ledger replay = fold(transactions ORDER BY trade_at, created_at)。
    """
    __tablename__ = "portfolio_transactions"
    __table_args__ = (
        CheckConstraint(
            "transaction_type IN ('CASH_IN','CASH_OUT','REVERSAL') OR instrument_id IS NOT NULL",
            name="instrument_required"),
        CheckConstraint(
            "reverses_transaction_id IS NULL OR reverses_transaction_id <> transaction_id",
            name="reversal"),
        Index("ix_transactions_portfolio_date", "portfolio_id", "trade_date"),
        Index("ix_transactions_portfolio_instrument", "portfolio_id", "instrument_id"),
        enum_ck("portfolio_transactions", "transaction_type", TransactionType),
        enum_ck("portfolio_transactions", "source", TransactionSource),
    )
    transaction_id: Mapped[UUID] = pk("transaction_id")
    portfolio_id: Mapped[UUID] = mapped_column(ForeignKey("portfolios.portfolio_id"), nullable=False)
    account_id: Mapped[UUID | None] = mapped_column(ForeignKey("accounts.account_id"))
    instrument_id: Mapped[UUID | None] = mapped_column(ForeignKey("instruments.instrument_id"))  # CASH 类可空
    transaction_type: Mapped[TransactionType] = mapped_column(Text, nullable=False)
    quantity: Mapped[Decimal | None] = mapped_column(QTY)              # BUY/SELL/DIVIDEND
    price_cny: Mapped[Decimal | None] = mapped_column(MONEY)           # 成交单价
    amount_cny: Mapped[Decimal] = mapped_column(MONEY, nullable=False) # 成交金额（符号约定见类注释）
    fees_cny: Mapped[Decimal] = mapped_column(MONEY, nullable=False, server_default=text("0"))
    tax_cny: Mapped[Decimal] = mapped_column(MONEY, nullable=False, server_default=text("0"))
    trade_at: Mapped[datetime] = mapped_column(TIMESTAMPTZ, nullable=False)
    trade_date: Mapped[date] = mapped_column(Date, nullable=False)     # A 股交易日
    source: Mapped[TransactionSource] = mapped_column(Text, nullable=False)
    reverses_transaction_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("portfolio_transactions.transaction_id"))           # 纠错引用
    note: Mapped[str | None] = mapped_column(Text)
    provenance_id: Mapped[UUID | None] = mapped_column(ForeignKey("provenance_records.provenance_id"))
    created_by: Mapped[str] = mapped_column(Text, nullable=False)


class PositionSnapshot(Base, CreatedAtMixin):
    """派生持仓（只读 SoT）——没有 set_position()，只能由 Ledger replay 产出（ts01 冻结）。

    Position(t) = fold(Transaction[<=t])；CLOSED 持仓不删除。"""
    __tablename__ = "position_snapshots"
    __table_args__ = (
        UniqueConstraint("portfolio_id", "instrument_id", "snapshot_date",
                         name="uq_position_snapshots_pf_inst_date"),
        Index("ix_position_snapshots_pf_date", "portfolio_id", text("snapshot_date DESC")),
    )
    position_snapshot_id: Mapped[UUID] = pk("position_snapshot_id")
    portfolio_id: Mapped[UUID] = mapped_column(ForeignKey("portfolios.portfolio_id"), nullable=False)
    instrument_id: Mapped[UUID] = mapped_column(ForeignKey("instruments.instrument_id"), nullable=False)
    snapshot_date: Mapped[date] = mapped_column(Date, nullable=False)
    quantity: Mapped[Decimal] = mapped_column(QTY, nullable=False)
    cost_basis_cny: Mapped[Decimal] = mapped_column(MONEY, nullable=False)  # 加权成本
    market_price_cny: Mapped[Decimal | None] = mapped_column(MONEY)
    market_value_cny: Mapped[Decimal | None] = mapped_column(MONEY)
    realized_pnl_cny: Mapped[Decimal | None] = mapped_column(MONEY)
    unrealized_pnl_cny: Mapped[Decimal | None] = mapped_column(MONEY)
    is_qdii: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))  # 冗余
    engine_version: Mapped[str] = mapped_column(Text, nullable=False)  # replay 引擎版本


class PortfolioSnapshot(Base, CreatedAtMixin):
    """时点组合状态。NAV = 现金 + 市值（QDII 场内市值按 CNY 直接计入，不穿透美元）。"""
    __tablename__ = "portfolio_snapshots"
    __table_args__ = (
        UniqueConstraint("portfolio_id", "snapshot_date", name="uq_portfolio_snapshots_pf_date"),
    )
    portfolio_snapshot_id: Mapped[UUID] = pk("portfolio_snapshot_id")
    portfolio_id: Mapped[UUID] = mapped_column(ForeignKey("portfolios.portfolio_id"), nullable=False)
    snapshot_date: Mapped[date] = mapped_column(Date, nullable=False)
    as_of: Mapped[datetime] = mapped_column(TIMESTAMPTZ, nullable=False)
    cash_cny: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    market_value_cny: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    nav_cny: Mapped[Decimal] = mapped_column(MONEY, nullable=False)   # NAV = 现金 + 市值
    exposures: Mapped[dict | None] = mapped_column(JSONB)             # 行业/资产类暴露
    risk_summary: Mapped[dict | None] = mapped_column(JSONB)          # 集中度/回撤摘要（Risk 产出落此）
    engine_version: Mapped[str] = mapped_column(Text, nullable=False)


class TargetAllocation(Base, CreatedAtMixin):
    """目标配置（时态：effective_from / effective_to）。"""
    __tablename__ = "target_allocations"
    __table_args__ = (
        range_ck("target_allocations", "target_weight", "0", "1"),
    )
    target_allocation_id: Mapped[UUID] = pk("target_allocation_id")
    portfolio_id: Mapped[UUID] = mapped_column(ForeignKey("portfolios.portfolio_id"), nullable=False)
    instrument_id: Mapped[UUID | None] = mapped_column(ForeignKey("instruments.instrument_id"))
    asset_class: Mapped[str | None] = mapped_column(Text)             # CN_EQUITY / CN_ETF / CASH 等
    target_weight: Mapped[Decimal] = mapped_column(RATIO, nullable=False)
    rebalance_frequency: Mapped[str | None] = mapped_column(Text)
    effective_from: Mapped[date] = mapped_column(Date, nullable=False)
    effective_to: Mapped[date | None] = mapped_column(Date)


class TradeProposal(Base, TimestampMixin):
    """交易建议：Hermes 对 REAL 组合能写的最高权限对象（冻结规范 §25.2 / §33.1 PROPOSAL_WRITE）。

    EXECUTED 只能由 ACCOUNT_WRITE 人工入口确认后落账（写 portfolio_transactions 并回填
    executed_transaction_id）；任何自动化路径（含 Hermes Cron/Job）写 REAL 交易 = 架构违规。"""
    __tablename__ = "trade_proposals"
    __table_args__ = (
        enum_ck("trade_proposals", "proposal_type", ProposalType),
        enum_ck("trade_proposals", "status", TradeProposalStatus),
    )
    trade_proposal_id: Mapped[UUID] = pk("trade_proposal_id")
    portfolio_id: Mapped[UUID] = mapped_column(ForeignKey("portfolios.portfolio_id"), nullable=False)
    instrument_id: Mapped[UUID] = mapped_column(ForeignKey("instruments.instrument_id"), nullable=False)
    proposal_type: Mapped[ProposalType] = mapped_column(Text, nullable=False)
    quantity: Mapped[Decimal] = mapped_column(QTY, nullable=False)
    limit_price_cny: Mapped[Decimal | None] = mapped_column(MONEY)
    target_weight: Mapped[Decimal | None] = mapped_column(RATIO)
    status: Mapped[TradeProposalStatus] = mapped_column(Text, nullable=False)
    rationale: Mapped[str | None] = mapped_column(Text)
    thesis_revision_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("thesis_revisions.thesis_revision_id"))            # 决策依据（portfolio → thesis 单向依赖）
    linked_valuation_run_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("valuation_runs.valuation_run_id"))               # 决策依据（PIT 估值）
    provenance_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("provenance_records.provenance_id"))               # 决策输入血缘
    created_by: Mapped[str] = mapped_column(Text, nullable=False)     # HERMES / HUMAN
    approved_by: Mapped[str | None] = mapped_column(Text)             # 仅 HUMAN
    approved_at: Mapped[datetime | None] = mapped_column(TIMESTAMPTZ)
    executed_transaction_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("portfolio_transactions.transaction_id"))          # 落账后回填
