# =====================================================================
# tests/unit/test_portfolio_engine.py —— Portfolio Engine（GOLD-PRT-001/002/003/009）
# =====================================================================
from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.portfolio.engine import compute_snapshot, compute_twr, replay

NOW = datetime(2026, 8, 21, tzinfo=UTC)
I1, I2 = uuid4(), uuid4()


def tx(ttype: str, amount: str, qty: str | None = None, price: str | None = None,
       fees: str = "0", inst=None, td: date = date(2026, 8, 21), at=None):
    return SimpleNamespace(
        transaction_type=ttype, amount_cny=Decimal(amount),
        quantity=Decimal(qty) if qty else None,
        price_cny=Decimal(price) if price else None,
        fees_cny=Decimal(fees), instrument_id=inst, trade_date=td,
        trade_at=at or NOW, created_at=NOW,
    )


def test_gold_prt_001_ledger_replay_basic() -> None:
    """BUY/SELL/DIVIDEND/FEE/CASH_IN 混合 → 全字段 fold 正确。"""
    txs = [
        tx("CASH_IN", "10000", inst=I1),                          # 入金 10000
        tx("BUY", "-1000", qty="100", price="10", inst=I1),       # 买 100@10
        tx("DIVIDEND", "50", inst=I1),                            # 分红 50
        tx("FEE", "-5", inst=I1),                                 # 费用 5
        tx("SELL", "550", qty="50", price="11", inst=I1),         # 卖 50@11
    ]
    rr = replay(txs)
    assert rr.cash_cny == Decimal("9595.0000")      # 10000 −1000 +50 −5 +550
    pos = rr.positions[I1]
    assert pos.quantity == Decimal("50")
    assert pos.avg_cost == Decimal("10.0000")
    assert pos.cost_basis_cny == Decimal("500.0000")
    assert pos.realized_pnl_cny == Decimal("50.0000")   # 50×(11−10)


def test_gold_prt_002_weighted_avg_cost_with_fee() -> None:
    """加权平均成本 + FEE 分摊：1000@10 买 500@14+费 5 → 11.3367。

    （规范示例 11.3367 = (1000×10 + 500×14 + 5)/1500；ts06 §5.3.1 示例价 12 处
    存在算术笔误——公式语义以本黄金值锁定为准。）
    """
    txs = [
        tx("BUY", "-10000", qty="1000", price="10", inst=I1),
        tx("BUY", "-7000", qty="500", price="14", fees="5", inst=I1),
    ]
    rr = replay(txs)
    pos = rr.positions[I1]
    assert pos.avg_cost == Decimal("11.3367")       # (1000×10+500×12+5)/1500
    assert pos.quantity == Decimal("1500")
    # SELL 后 avg_cost 不变、realized 按结转成本
    txs.append(tx("SELL", "3900", qty="300", price="13", inst=I1))
    rr2 = replay(txs)
    assert rr2.positions[I1].avg_cost == Decimal("11.3367")
    assert rr2.positions[I1].realized_pnl_cny == Decimal("498.9900")  # 300×(13−11.3367)


def test_gold_prt_003_sign_convention() -> None:
    """符号约定：数据层恒现金视角，引擎按类型显式分支。"""
    txs = [
        tx("CASH_IN", "10000", inst=I1),
        tx("BUY", "-5000", qty="500", price="10", inst=I1),
        tx("CASH_OUT", "-2000", inst=I1),
    ]
    rr = replay(txs)
    assert rr.cash_cny == Decimal("3000.0000")      # 10000 −5000 −2000
    assert rr.positions[I1].quantity == Decimal("500")


def test_fold_order_trade_date_trade_at_created_at() -> None:
    """fold 顺序：trade_date, trade_at, created_at（§5.2.1）。"""
    txs = [
        tx("BUY", "-500", qty="50", price="10", inst=I1, td=date(2026, 8, 20), at=datetime(2026, 8, 20, 10, 0, tzinfo=UTC)),
        tx("BUY", "-500", qty="50", price="10", inst=I1, td=date(2026, 8, 21), at=datetime(2026, 8, 21, 10, 0, tzinfo=UTC)),
        tx("BUY", "-500", qty="50", price="10", inst=I1, td=date(2026, 8, 21), at=datetime(2026, 8, 21, 9, 0, tzinfo=UTC)),
    ]
    rr = replay(txs)
    assert rr.positions[I1].quantity == Decimal("150")


def test_gold_prt_009_snapshot() -> None:
    """快照黄金值：NAV = 现金 + 市值；position 全字段。"""
    txs = [
        tx("CASH_IN", "10000", inst=I1),
        tx("BUY", "-5000", qty="500", price="10", inst=I1),
    ]
    rr = replay(txs)
    rows, pf = compute_snapshot(rr, {I1: Decimal("12")}, snapshot_date=date(2026, 8, 21))
    assert pf["cash_cny"] == Decimal("5000.0000")
    assert pf["market_value_cny"] == Decimal("6000.0000")
    assert pf["nav_cny"] == Decimal("11000.0000")       # NAV = 现金 + 市值
    assert pf["engine_version"] == "portfolio-engine/0.1.0"
    row = rows[0]
    assert row["quantity"] == Decimal("500")
    assert row["market_value_cny"] == Decimal("6000.0000")
    assert row["unrealized_pnl_cny"] == Decimal("1000.0000")   # 500×(12−10)


def test_replay_deterministic() -> None:
    """同一序列两次 replay 逐字段相等（GOLD-PRT-010 前置）。"""
    txs = [tx("BUY", "-100", qty="10", price="10", inst=I1),
           tx("SELL", "55", qty="5", price="11", inst=I1)]
    a, b = replay(txs), replay(txs)
    assert a.cash_cny == b.cash_cny
    assert a.positions[I1].avg_cost == b.positions[I1].avg_cost
    assert a.positions[I1].realized_pnl_cny == b.positions[I1].realized_pnl_cny


def test_unsupported_type_rejected() -> None:

    txs = [SimpleNamespace(transaction_type="UNKNOWN", amount_cny=Decimal("1"),
                           quantity=None, price_cny=None, fees_cny=Decimal(0),
                           instrument_id=I1, trade_date=date(2026, 8, 21),
                           trade_at=NOW, created_at=NOW)]
    with pytest.raises(ValueError):
        replay(txs)


def test_corporate_actions_adjust_cost_and_rights_cash() -> None:
    action = lambda kind, params, td: SimpleNamespace(  # noqa: E731
        action_type=kind, parameters=params, effective_date=td, ex_date=None,
        status="IMPLEMENTED", instrument_id=I1, created_at=NOW,
    )
    rr = replay(
        [tx("BUY", "-1000", qty="100", price="10", inst=I1)],
        corporate_actions=[
            action("SPLIT", {"split_ratio": "2"}, date(2026, 8, 22)),
            action("BONUS_SHARE", {"bonus_ratio": "0.1"}, date(2026, 8, 23)),
            action("RIGHTS_ISSUE", {"rights_ratio": "0.2", "subscription_price": "8"}, date(2026, 8, 24)),
        ],
    )
    position = rr.positions[I1]
    assert position.quantity == Decimal("264.0")
    assert position.cost_basis_cny == Decimal("1352.0000")
    assert position.avg_cost == Decimal("5.1212")
    assert rr.cash_cny == Decimal("-1352.0000")


def test_twr_excludes_external_cash_flow() -> None:
    assert compute_twr([
        {"start_nav": "100", "end_nav": "110", "external_flow": "0"},
        {"start_nav": "110", "end_nav": "220", "external_flow": "100"},
    ]) == Decimal("0.1524")
