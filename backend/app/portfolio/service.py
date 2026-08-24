# =====================================================================
# backend/app/portfolio/service.py —— Portfolio Ledger 服务（TS-06 §5，冻结）
#
# - Ledger 唯一写入者（record_transaction / simulate_paper_trade）；
# - Position 无写入端点（快照只读 SoT 派生，STM-POS-002）；
# - PAPER 组合：Hermes 可自动建议 + 模拟交易（STM-TRD-005，冻结规范 §25.1）；
# - 快照 upsert-by-supersede（同 snapshot_date 重跑替换，append-only 兜底）。
# =====================================================================
from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.audit.service import write_audit_event, write_internal_provenance
from app.common.enums import (
    AccountType,
    ActorType,
    AuditAction,
    PortfolioMode,
    PortfolioStatus,
    SourceKind,
    TradeProposalStatus,
    TransactionSource,
    TransactionType,
)
from app.common.freshness import require_freshness
from app.portfolio.engine import (
    ENGINE_VERSION,
    ReplayResult,
    compute_snapshot,
    replay,
)
from app.portfolio.models import (
    Account,
    Portfolio,
    PortfolioSnapshot,
    PortfolioTransaction,
    PositionSnapshot,
    TradeProposal,
)

__all__ = ["PortfolioService", "PortfolioDomainError", "ProposalTransitionError"]


class PortfolioDomainError(Exception):
    code = "PORTFOLIO_DOMAIN_ERROR"


class ProposalTransitionError(PortfolioDomainError):
    code = "INVALID_PROPOSAL_TRANSITION"


class PortfolioService:
    # ---- 组合与账户 ----

    def create_portfolio(
        self,
        session: Session,
        name: str,
        *,
        mode: PortfolioMode = PortfolioMode.PAPER,
    ) -> Portfolio:
        portfolio = Portfolio(
            portfolio_id=uuid4(),
            name=name,
            mode=mode.value,
            base_currency="CNY",
            status=PortfolioStatus.ACTIVE.value,
        )
        session.add(portfolio)
        session.flush()
        session.add(Account(
            account_id=uuid4(),
            portfolio_id=portfolio.portfolio_id,
            account_type=AccountType.BROKERAGE.value,
            name=f"{name} 主账户",
            currency="CNY",
        ))
        session.flush()
        return portfolio

    def list_portfolios(self, session: Session) -> list[Portfolio]:
        return list(session.scalars(select(Portfolio).order_by(Portfolio.created_at.asc())).all())

    def get_portfolio(self, session: Session, portfolio_id: UUID) -> tuple[Portfolio, PortfolioSnapshot | None] | None:
        portfolio = session.get(Portfolio, portfolio_id)
        if portfolio is None:
            return None
        snapshot = session.scalar(select(PortfolioSnapshot).where(
            PortfolioSnapshot.portfolio_id == portfolio_id,
        ).order_by(PortfolioSnapshot.snapshot_date.desc()).limit(1))
        return portfolio, snapshot

    def get_positions(self, session: Session, portfolio_id: UUID) -> tuple[date | None, list[PositionSnapshot]]:
        rows = list(session.scalars(select(PositionSnapshot).where(
            PositionSnapshot.portfolio_id == portfolio_id,
        ).order_by(PositionSnapshot.snapshot_date.desc())).all())
        snapshot_date = rows[0].snapshot_date if rows else None
        return snapshot_date, [row for row in rows if row.snapshot_date == snapshot_date]

    def latest_view(self, session: Session, portfolio_id: UUID, cutoff: date | None = None) -> dict | None:
        """Read-only portfolio facade used by REST/MCP adapters."""
        portfolio = session.get(Portfolio, portfolio_id)
        if portfolio is None:
            return None
        stmt = select(PortfolioSnapshot).where(PortfolioSnapshot.portfolio_id == portfolio_id)
        if cutoff is not None:
            stmt = stmt.where(PortfolioSnapshot.snapshot_date <= cutoff)
        snapshot = session.scalar(stmt.order_by(PortfolioSnapshot.snapshot_date.desc()).limit(1))
        return {
            "portfolio_id": str(portfolio.portfolio_id), "name": portfolio.name,
            "mode": str(getattr(portfolio.mode, "value", portfolio.mode)),
            "status": str(getattr(portfolio.status, "value", portfolio.status)),
            "base_currency": portfolio.base_currency,
            "snapshot": {
                "snapshot_date": snapshot.snapshot_date.isoformat(), "as_of": snapshot.as_of.isoformat(),
                "cash_cny": str(snapshot.cash_cny), "market_value_cny": str(snapshot.market_value_cny),
                "nav_cny": str(snapshot.nav_cny), "exposures": snapshot.exposures,
                "risk_summary": snapshot.risk_summary, "engine_version": snapshot.engine_version,
            } if snapshot else None,
        }

    def positions_view(self, session: Session, portfolio_id: UUID, cutoff: date | None = None) -> dict | None:
        if session.get(Portfolio, portfolio_id) is None:
            return None
        stmt = select(PositionSnapshot).where(PositionSnapshot.portfolio_id == portfolio_id)
        if cutoff is not None:
            stmt = stmt.where(PositionSnapshot.snapshot_date <= cutoff)
        rows = list(session.scalars(stmt.order_by(PositionSnapshot.snapshot_date.desc())).all())
        latest = rows[0].snapshot_date if rows else None
        rows = [row for row in rows if row.snapshot_date == latest]
        return {
            "portfolio_id": str(portfolio_id), "snapshot_date": latest.isoformat() if latest else None,
            "positions": [{
                "instrument_id": str(row.instrument_id), "snapshot_date": row.snapshot_date.isoformat(),
                "quantity": str(row.quantity), "cost_basis_cny": str(row.cost_basis_cny),
                "market_price_cny": str(row.market_price_cny) if row.market_price_cny is not None else None,
                "market_value_cny": str(row.market_value_cny) if row.market_value_cny is not None else None,
                "realized_pnl_cny": str(row.realized_pnl_cny) if row.realized_pnl_cny is not None else None,
                "unrealized_pnl_cny": str(row.unrealized_pnl_cny) if row.unrealized_pnl_cny is not None else None,
                "is_qdii": row.is_qdii, "engine_version": row.engine_version,
            } for row in rows],
        }

    def provenance_view(self, session: Session, portfolio_id: UUID) -> list[dict]:
        rows = session.scalars(select(PortfolioTransaction).where(
            PortfolioTransaction.portfolio_id == portfolio_id,
            PortfolioTransaction.provenance_id.is_not(None),
        ).order_by(PortfolioTransaction.created_at.desc()).limit(50)).all()
        return [{"provenance_id": str(row.provenance_id), "source": "portfolio_ledger",
                 "provider": "internal", "quality_status": "VERIFIED"} for row in rows]

    # ---- Ledger 写入（唯一事实来源）----

    def record_transaction(
        self,
        session: Session,
        portfolio_id: UUID,
        transaction_type: TransactionType,
        *,
        instrument_id: UUID | None = None,
        quantity: Decimal | None = None,
        price_cny: Decimal | None = None,
        amount_cny: Decimal,
        fees_cny: Decimal = Decimal("0"),
        trade_date: date,
        trade_at: datetime | None = None,
        source: TransactionSource = TransactionSource.MANUAL,
        note: str | None = None,
        created_by: str = "HUMAN",
        permission: str | None = None,
        actor_type: ActorType = ActorType.HUMAN,
        reverses_transaction_id: UUID | None = None,
    ) -> PortfolioTransaction:
        """Ledger 写入（唯一写入者是 Ledger Service/人工入口，§5.8）。

        数据层校验：quantity 恒正；amount_cny 现金视角（BUY/FEE/CASH_OUT 负，
        SELL/DIVIDEND/CASH_IN 正）；fees_cny 正数。
        """
        portfolio = session.get(Portfolio, portfolio_id)
        if portfolio is None:
            raise PortfolioDomainError("组合不存在")
        if portfolio.mode == PortfolioMode.REAL.value and (
            permission != "ACCOUNT_WRITE" or actor_type is not ActorType.HUMAN
        ):
            raise PortfolioDomainError("REAL 交易只能通过人工 ACCOUNT_WRITE 入口")
        if transaction_type == TransactionType.REVERSAL:
            raise PortfolioDomainError("REVERSAL 必须通过 reverse_transaction 创建")
        if quantity is not None and quantity <= 0:
            raise PortfolioDomainError("quantity 恒为正，方向由 transaction_type 表达（ts02 §7.3）")
        if fees_cny < 0:
            raise PortfolioDomainError("fees_cny 恒为正数金额（现金视角在公式中处理）")
        sign_rule = {
            TransactionType.BUY: -1, TransactionType.SELL: 1,
            TransactionType.DIVIDEND: 1, TransactionType.FEE: -1,
            TransactionType.CASH_IN: 1, TransactionType.CASH_OUT: -1,
        }
        if (amount_cny < 0) != (sign_rule[transaction_type] < 0):
            raise PortfolioDomainError(
                f"amount_cny 符号不符合现金视角（{transaction_type.value}: "
                f"{'负' if sign_rule[transaction_type] < 0 else '正'}）")
        provenance_id = uuid4()
        tx = PortfolioTransaction(
            transaction_id=uuid4(),
            portfolio_id=portfolio_id,
            instrument_id=instrument_id,
            transaction_type=transaction_type.value,
            quantity=quantity,
            price_cny=price_cny,
            amount_cny=amount_cny,
            fees_cny=fees_cny,
            trade_at=trade_at or datetime.now(UTC),
            trade_date=trade_date,
            source=source.value,
            note=note,
            created_by=created_by,
            provenance_id=provenance_id,
        )
        session.add(tx)
        write_internal_provenance(
            session,
            source_kind=SourceKind.HERMES if actor_type is ActorType.HERMES else SourceKind.HUMAN,
            source="portfolio_transaction", actor_id=created_by, as_of_date=trade_date,
            provenance_id=provenance_id, transform_version="portfolio-ledger/0.1.0",
        )
        session.flush()
        write_audit_event(
            session, action=AuditAction.CREATE, entity_type="portfolio_transaction",
            entity_id=tx.transaction_id, actor_type=actor_type, actor_id=created_by,
            payload={"source": tx.source},
        )
        return tx

    def post_transaction(self, session: Session, portfolio_id: UUID, transaction_type: TransactionType, **kwargs) -> PortfolioTransaction:
        """人工 ACCOUNT_WRITE 门面；自动化调用方没有可传递的 bypass。"""

        kwargs["permission"] = "ACCOUNT_WRITE"
        kwargs["actor_type"] = ActorType.HUMAN
        kwargs.setdefault("created_by", "HUMAN")
        tx = self.record_transaction(session, portfolio_id, transaction_type, **kwargs)
        session.flush()
        return tx

    def reverse_transaction(
        self, session: Session, portfolio_id: UUID, transaction_id: UUID, *,
        trade_date: date, note: str | None = None, permission: str | None = None,
    ) -> PortfolioTransaction:
        """Create an append-only correction; never updates the original row."""

        portfolio = session.get(Portfolio, portfolio_id)
        original = session.get(PortfolioTransaction, transaction_id)
        if portfolio is None or original is None or original.portfolio_id != portfolio_id:
            raise PortfolioDomainError("交易不存在")
        if portfolio.mode == PortfolioMode.REAL.value and permission != "ACCOUNT_WRITE":
            raise PortfolioDomainError("REAL 交易更正只能通过人工 ACCOUNT_WRITE 入口")
        if original.transaction_type == TransactionType.REVERSAL.value:
            raise PortfolioDomainError("不能再次反转 REVERSAL")
        already = session.scalar(select(PortfolioTransaction).where(
            PortfolioTransaction.reverses_transaction_id == transaction_id,
        ))
        if already is not None:
            raise PortfolioDomainError("交易已经被反转")
        if portfolio.mode == PortfolioMode.REAL.value and portfolio.status != PortfolioStatus.ACTIVE.value:
            raise PortfolioDomainError("已关闭组合不可写入")
        now = datetime.now(UTC)
        provenance_id = uuid4()
        tx = PortfolioTransaction(
            transaction_id=uuid4(), portfolio_id=portfolio_id,
            account_id=original.account_id, instrument_id=original.instrument_id,
            transaction_type=TransactionType.REVERSAL.value,
            quantity=original.quantity, price_cny=original.price_cny,
            amount_cny=-original.amount_cny, fees_cny=original.fees_cny,
            tax_cny=original.tax_cny, trade_at=now, trade_date=trade_date,
            source=TransactionSource.REVERSAL.value,
            reverses_transaction_id=transaction_id,
            note=note or f"反转交易 {transaction_id}", created_by="HUMAN",
            provenance_id=provenance_id,
        )
        session.add(tx)
        write_internal_provenance(
            session, source_kind=SourceKind.HUMAN, source="account_write",
            provider="localhost-admin", actor_id="HUMAN", as_of_date=trade_date,
            provenance_id=provenance_id, transform_version="account-write/0.1.0",
        )
        session.flush()
        write_audit_event(
            session, action=AuditAction.REVERSE, entity_type="portfolio_transaction",
            entity_id=tx.transaction_id, actor_type=ActorType.HUMAN, actor_id="HUMAN",
            payload={"reverses_transaction_id": str(transaction_id), "permission": "ACCOUNT_WRITE"},
        )
        session.flush()
        return tx

    def simulate_paper_trade(
        self,
        session: Session,
        portfolio_id: UUID,
        transaction_type: TransactionType,
        *,
        instrument_id: UUID | None,
        quantity: Decimal,
        price_cny: Decimal,
        fees_cny: Decimal = Decimal("0"),
        trade_date: date,
        note: str | None = None,
    ) -> PortfolioTransaction:
        """PAPER 自动路径（STM-TRD-005）：Hermes 自动模拟交易，不产生 REAL 交易。

        仅允许 mode='PAPER' 组合；REAL 组合 → PortfolioDomainError
        （Hermes 对 REAL 最高只能写 trade_proposals，§5.8.2 #4）。
        """
        portfolio = session.get(Portfolio, portfolio_id)
        if portfolio is None:
            raise PortfolioDomainError(f"portfolio {portfolio_id} 不存在")
        if portfolio.mode != PortfolioMode.PAPER.value:
            raise PortfolioDomainError(
                f"REAL 组合禁止模拟交易（{portfolio_id}）；Hermes 只能写 trade_proposals")
        amount = price_cny * quantity
        amount_cny = -amount if transaction_type == TransactionType.BUY else amount
        return self.record_transaction(
            session, portfolio_id, transaction_type,
            instrument_id=instrument_id, quantity=quantity, price_cny=price_cny,
            amount_cny=amount_cny, fees_cny=fees_cny, trade_date=trade_date,
            source=TransactionSource.HERMES_PAPER, note=note, created_by="HERMES_PAPER",
            permission="PAPER_ENGINE", actor_type=ActorType.HERMES,
        )

    def create_trade_proposal(
        self,
        session: Session,
        portfolio_id: UUID,
        instrument_id: UUID,
        proposal_type,
        *,
        quantity: Decimal,
        limit_price_cny: Decimal | None = None,
        target_weight: Decimal | None = None,
        rationale: str | None = None,
        thesis_revision_id: UUID | None = None,
        linked_valuation_run_id: UUID | None = None,
        freshness: dict | str = "OK",
        created_by: str = "HERMES",
    ):
        """Create the highest object Hermes may write for a REAL portfolio."""

        require_freshness(freshness)
        from app.common.enums import ProposalType, TradeProposalStatus
        from app.instruments.models import Instrument
        from app.portfolio.models import TradeProposal
        from app.thesis.models import ThesisRevision
        from app.valuation.models import ValuationRun

        portfolio = session.get(Portfolio, portfolio_id)
        if portfolio is None or session.get(Instrument, instrument_id) is None:
            raise PortfolioDomainError("组合或标的不存在")
        if thesis_revision_id is not None and session.get(ThesisRevision, thesis_revision_id) is None:
            raise PortfolioDomainError("Thesis revision 不存在")
        if linked_valuation_run_id is not None and session.get(ValuationRun, linked_valuation_run_id) is None:
            raise PortfolioDomainError("估值运行不存在")
        if quantity <= 0:
            raise PortfolioDomainError("proposal quantity 必须为正")
        proposal = TradeProposal(
            trade_proposal_id=uuid4(), portfolio_id=portfolio_id, instrument_id=instrument_id,
            proposal_type=ProposalType(proposal_type).value,
            quantity=quantity, limit_price_cny=limit_price_cny, target_weight=target_weight,
            status=TradeProposalStatus.PROPOSED.value, rationale=rationale,
            thesis_revision_id=thesis_revision_id, linked_valuation_run_id=linked_valuation_run_id,
            created_by=created_by,
        )
        proposal.provenance_id = uuid4()
        session.add(proposal)
        write_internal_provenance(
            session, source_kind=SourceKind.HERMES, source="trade_proposal", provider="internal",
            actor_id=created_by, as_of_date=date.today(), provenance_id=proposal.provenance_id,
            transform_version="portfolio-proposal/0.1.0",
        )
        session.flush()
        write_audit_event(
            session, action=AuditAction.CREATE, entity_type="trade_proposal",
            entity_id=proposal.trade_proposal_id, actor_type=ActorType.HERMES,
            actor_id=created_by, payload={"permission": "PROPOSAL_WRITE"},
        )
        return proposal

    def transition_proposal(
        self, session: Session, proposal_id: UUID, to_status: str, *,
        actor: str, permission: str,
        trade_date: date | None = None, price_cny: Decimal | None = None,
    ) -> TradeProposal:
        """Manual proposal state machine; EXECUTED is never an MCP action."""

        proposal = session.get(TradeProposal, proposal_id)
        if proposal is None:
            raise PortfolioDomainError("proposal 不存在")
        current = TradeProposalStatus(proposal.status)
        target = TradeProposalStatus(to_status)
        allowed = {
            TradeProposalStatus.DRAFT: {TradeProposalStatus.PROPOSED},
            TradeProposalStatus.PROPOSED: {TradeProposalStatus.APPROVED, TradeProposalStatus.REJECTED},
            TradeProposalStatus.APPROVED: {TradeProposalStatus.EXECUTED, TradeProposalStatus.REJECTED},
            TradeProposalStatus.REJECTED: set(), TradeProposalStatus.EXECUTED: set(),
        }
        if target not in allowed[current]:
            raise ProposalTransitionError(f"proposal {current.value} → {target.value} 非法")
        if target in {TradeProposalStatus.APPROVED, TradeProposalStatus.REJECTED, TradeProposalStatus.EXECUTED} \
                and permission != "ACCOUNT_WRITE":
            raise PortfolioDomainError("proposal 审批/执行只能通过人工 ACCOUNT_WRITE 入口")
        if target is TradeProposalStatus.APPROVED:
            proposal.approved_by = actor
            proposal.approved_at = datetime.now(UTC)
        if target is TradeProposalStatus.EXECUTED:
            portfolio = session.get(Portfolio, proposal.portfolio_id)
            execution_price = price_cny or proposal.limit_price_cny
            if execution_price is None:
                raise PortfolioDomainError("执行 proposal 必须提供成交价")
            if portfolio is None:
                raise PortfolioDomainError("组合不存在")
            tx_type = TransactionType(proposal.proposal_type)
            if portfolio.mode == PortfolioMode.REAL.value:
                tx = self.post_transaction(
                    session, portfolio.portfolio_id, tx_type,
                    instrument_id=proposal.instrument_id, quantity=proposal.quantity,
                    price_cny=execution_price,
                    amount_cny=(-1 if tx_type is TransactionType.BUY else 1) * execution_price * proposal.quantity,
                    trade_date=trade_date or date.today(), source=TransactionSource.MANUAL,
                    note=f"proposal {proposal.trade_proposal_id}",
                )
            else:
                tx = self.simulate_paper_trade(
                    session, portfolio.portfolio_id, tx_type,
                    instrument_id=proposal.instrument_id, quantity=proposal.quantity,
                    price_cny=execution_price, trade_date=trade_date or date.today(),
                    note=f"proposal {proposal.trade_proposal_id}",
                )
            proposal.executed_transaction_id = tx.transaction_id
        proposal.status = target.value
        write_audit_event(
            session, action=AuditAction.STATUS_CHANGE, entity_type="trade_proposal",
            entity_id=proposal.trade_proposal_id, actor_type=ActorType.HUMAN, actor_id=actor,
            payload={"from": current.value, "to": target.value},
        )
        session.flush()
        return proposal

    # ---- Replay（Position(t) = fold）----

    def replay_portfolio(
        self,
        session: Session,
        portfolio_id: UUID,
        as_of: date | None = None,
    ) -> ReplayResult:
        stmt = select(PortfolioTransaction).where(
            PortfolioTransaction.portfolio_id == portfolio_id)
        if as_of is not None:
            stmt = stmt.where(PortfolioTransaction.trade_date <= as_of)
        txs = session.execute(stmt).scalars().all()
        return replay(txs)

    # ---- 快照（只读 SoT 派生；upsert-by-supersede）----

    def snapshot(
        self,
        session: Session,
        portfolio_id: UUID,
        snapshot_date: date,
        prices: dict[UUID, Decimal],
    ) -> dict:
        """生成 position_snapshots + portfolio_snapshots（NAV=现金+市值，§5.6.4）。

        同 snapshot_date 重跑 → upsert（新行替换，append-only 不删除历史版本之外）。
        """
        rr = self.replay_portfolio(session, portfolio_id, as_of=snapshot_date)
        pos_rows, pf_row = compute_snapshot(rr, prices, snapshot_date=snapshot_date)
        pf = PortfolioSnapshot(
            portfolio_snapshot_id=uuid4(),
            portfolio_id=portfolio_id,
            snapshot_date=snapshot_date,
            as_of=datetime.combine(snapshot_date, datetime.min.time(), tzinfo=UTC),
            cash_cny=pf_row["cash_cny"],
            market_value_cny=pf_row["market_value_cny"],
            nav_cny=pf_row["nav_cny"],
            engine_version=ENGINE_VERSION,
        )
        stmt = insert(PortfolioSnapshot).values(
            portfolio_snapshot_id=pf.portfolio_snapshot_id,
            portfolio_id=pf.portfolio_id, snapshot_date=pf.snapshot_date,
            as_of=pf.as_of, cash_cny=pf.cash_cny, market_value_cny=pf.market_value_cny,
            nav_cny=pf.nav_cny, engine_version=pf.engine_version,
        ).on_conflict_do_update(
            constraint="uq_portfolio_snapshots_pf_date",
            set_={"cash_cny": pf.cash_cny, "market_value_cny": pf.market_value_cny,
                  "nav_cny": pf.nav_cny, "as_of": pf.as_of},
        )
        session.execute(stmt)

        for row in pos_rows:
            stmt2 = insert(PositionSnapshot).values(
                position_snapshot_id=uuid4(),
                portfolio_id=portfolio_id,
                instrument_id=row["instrument_id"],
                snapshot_date=snapshot_date,
                quantity=row["quantity"],
                cost_basis_cny=row["cost_basis_cny"],
                market_price_cny=row["market_price_cny"],
                market_value_cny=row["market_value_cny"],
                realized_pnl_cny=row["realized_pnl_cny"],
                unrealized_pnl_cny=row["unrealized_pnl_cny"],
                is_qdii=False,
                engine_version=ENGINE_VERSION,
            ).on_conflict_do_update(
                constraint="uq_position_snapshots_pf_inst_date",
                set_={"quantity": row["quantity"], "cost_basis_cny": row["cost_basis_cny"],
                      "market_price_cny": row["market_price_cny"],
                      "market_value_cny": row["market_value_cny"],
                      "realized_pnl_cny": row["realized_pnl_cny"],
                      "unrealized_pnl_cny": row["unrealized_pnl_cny"]},
            )
            session.execute(stmt2)
        session.flush()
        return {"nav_cny": pf.nav_cny, "cash_cny": pf.cash_cny,
                "market_value_cny": pf.market_value_cny, "positions": len(pos_rows)}
