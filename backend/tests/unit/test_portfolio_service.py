# =====================================================================
# tests/unit/test_portfolio_service.py —— Portfolio 服务集成（PAPER 闭环）
#
# 覆盖：Ledger 写入校验、PAPER 自动路径（STM-TRD-005）、REAL 拒绝、
# 快照落库（GOLD-PRT-009 DB 层）、NAV 断言。
# =====================================================================
from __future__ import annotations

from datetime import date
from decimal import Decimal
from uuid import uuid4

import pytest

import app.models  # noqa: F401
from app.common.enums import PortfolioMode, TransactionType
from app.portfolio.models import (
    PortfolioSnapshot,
    PositionSnapshot,
)
from app.portfolio.service import (
    PortfolioDomainError,
    PortfolioService,
)


def test_paper_portfolio_full_loop(db_session, instrument) -> None:
    """PAPER 组合：入金 → 模拟买入 → 快照（NAV=现金+市值）。"""
    svc = PortfolioService()
    pf = svc.create_portfolio(db_session, "模拟组合", mode=PortfolioMode.PAPER)
    db_session.flush()

    svc.record_transaction(db_session, pf.portfolio_id, TransactionType.CASH_IN,
                           amount_cny=Decimal("10000"), trade_date=date(2026, 8, 20),
                           created_by="HUMAN")
    svc.simulate_paper_trade(
        db_session, pf.portfolio_id, TransactionType.BUY,
        instrument_id=instrument.instrument_id, quantity=Decimal("500"),
        price_cny=Decimal("10"), trade_date=date(2026, 8, 21),
    )
    db_session.flush()

    result = svc.snapshot(db_session, pf.portfolio_id, date(2026, 8, 21),
                          {instrument.instrument_id: Decimal("12")})
    assert result["cash_cny"] == Decimal("5000.0000")
    assert result["market_value_cny"] == Decimal("6000.0000")
    assert result["nav_cny"] == Decimal("11000.0000")
    db_session.flush()

    snap = db_session.query(PortfolioSnapshot).one()
    assert snap.nav_cny == Decimal("11000.0000")
    assert snap.engine_version == "portfolio-engine/0.1.0"
    pos = db_session.query(PositionSnapshot).one()
    assert pos.quantity == Decimal("500")
    assert pos.unrealized_pnl_cny == Decimal("1000.0000")


def test_paper_snapshot_upsert_by_supersede(db_session, instrument) -> None:
    """同 snapshot_date 重跑 → upsert（不产生重复快照）。"""
    svc = PortfolioService()
    pf = svc.create_portfolio(db_session, "P", mode=PortfolioMode.PAPER)
    db_session.flush()
    svc.simulate_paper_trade(db_session, pf.portfolio_id, TransactionType.BUY,
                             instrument_id=instrument.instrument_id,
                             quantity=Decimal("10"), price_cny=Decimal("10"),
                             trade_date=date(2026, 8, 21))
    db_session.flush()
    svc.snapshot(db_session, pf.portfolio_id, date(2026, 8, 21),
                 {instrument.instrument_id: Decimal("11")})
    db_session.flush()
    svc.snapshot(db_session, pf.portfolio_id, date(2026, 8, 21),
                 {instrument.instrument_id: Decimal("12")})
    db_session.flush()
    assert db_session.query(PortfolioSnapshot).count() == 1
    snap = db_session.query(PortfolioSnapshot).one()
    assert snap.nav_cny == Decimal("20.0000")      # −100 现金 + 10×12（无入金）


def test_real_portfolio_rejects_paper_trade(db_session) -> None:
    """REAL 组合禁止模拟交易（Hermes 最高只能写 trade_proposals，§5.8.2 #4）。"""
    svc = PortfolioService()
    pf = svc.create_portfolio(db_session, "真实", mode=PortfolioMode.REAL)
    db_session.flush()
    with pytest.raises(PortfolioDomainError):
        svc.simulate_paper_trade(db_session, pf.portfolio_id, TransactionType.BUY,
                                 instrument_id=uuid4(), quantity=Decimal("1"),
                                 price_cny=Decimal("1"), trade_date=date(2026, 8, 21))


def test_sign_validation(db_session) -> None:
    """amount_cny 符号校验（§5.4）：BUY 必须为负。"""
    svc = PortfolioService()
    pf = svc.create_portfolio(db_session, "P", mode=PortfolioMode.PAPER)
    db_session.flush()
    with pytest.raises(PortfolioDomainError):
        svc.record_transaction(db_session, pf.portfolio_id, TransactionType.BUY,
                               amount_cny=Decimal("100"), trade_date=date(2026, 8, 21))
    with pytest.raises(PortfolioDomainError):
        svc.record_transaction(db_session, pf.portfolio_id, TransactionType.BUY,
                               quantity=Decimal("-1"), amount_cny=Decimal("-100"),
                               trade_date=date(2026, 8, 21))
    with pytest.raises(PortfolioDomainError):
        svc.record_transaction(db_session, pf.portfolio_id, TransactionType.CASH_IN,
                               amount_cny=Decimal("-100"), trade_date=date(2026, 8, 21))


def test_replay_asof(db_session, instrument) -> None:
    """as_of replay：只 fold trade_date <= as_of 的交易（GOLD-PIT-003 前置）。"""
    svc = PortfolioService()
    pf = svc.create_portfolio(db_session, "P", mode=PortfolioMode.PAPER)
    db_session.flush()
    svc.record_transaction(db_session, pf.portfolio_id, TransactionType.CASH_IN,
                           amount_cny=Decimal("1000"), trade_date=date(2026, 8, 20))
    svc.simulate_paper_trade(db_session, pf.portfolio_id, TransactionType.BUY,
                             instrument_id=instrument.instrument_id,
                             quantity=Decimal("10"), price_cny=Decimal("50"),
                             trade_date=date(2026, 8, 21))
    db_session.flush()
    rr_before = svc.replay_portfolio(db_session, pf.portfolio_id, as_of=date(2026, 8, 20))
    assert rr_before.cash_cny == Decimal("1000.0000")
    assert rr_before.positions == {}
    rr_full = svc.replay_portfolio(db_session, pf.portfolio_id)
    assert rr_full.cash_cny == Decimal("500.0000")
