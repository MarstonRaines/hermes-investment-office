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

from app.common.enums import (
    AccountType,
    PortfolioMode,
    PortfolioStatus,
    TransactionSource,
    TransactionType,
)
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
)

__all__ = ["PortfolioService", "PortfolioDomainError"]


class PortfolioDomainError(Exception):
    code = "PORTFOLIO_DOMAIN_ERROR"


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
    ) -> PortfolioTransaction:
        """Ledger 写入（唯一写入者是 Ledger Service/人工入口，§5.8）。

        数据层校验：quantity 恒正；amount_cny 现金视角（BUY/FEE/CASH_OUT 负，
        SELL/DIVIDEND/CASH_IN 正）；fees_cny 正数。
        """
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
        )
        session.add(tx)
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
        )

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
