# =====================================================================
# backend/app/portfolio/engine.py —— Portfolio Engine 纯计算（TS-06 §5，冻结）
#
# Position(t) = fold(Transaction[<=t])：Ledger 唯一事实来源，Position 是派生投影。
# v0.1 冻结：加权平均成本唯一成本基准（§5.3）；现金视角符号约定（§5.4）；
# fold 顺序 ORDER BY trade_date, trade_at, created_at（§5.2.1）。
#
# fees 口径（2026-08-24 施工声明）：portfolio_transactions.fees_cny 存正数费用；
# 行内 fees 是真实现金流出：cash += amount_cny − fees_cny；
# BUY fees 摊入成本基数；SELL fees 从 realized 扣除（§5.3.2）。
# =====================================================================
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import ROUND_HALF_UP, Decimal

from app.common.enums import TransactionType

__all__ = ["ENGINE_VERSION", "Position", "ReplayResult", "replay", "compute_snapshot"]

ENGINE_VERSION = "portfolio-engine/0.1.0"


@dataclass
class Position:
    instrument_id: object
    quantity: Decimal = Decimal("0")
    avg_cost: Decimal = Decimal("0")          # 加权平均成本（cny/份）
    cost_basis_cny: Decimal = Decimal("0")
    realized_pnl_cny: Decimal = Decimal("0")
    market_price_cny: Decimal | None = None
    market_value_cny: Decimal | None = None
    unrealized_pnl_cny: Decimal | None = None

    @property
    def status(self) -> str:
        return "FLAT" if self.quantity == 0 and self.realized_pnl_cny == 0 else (
            "CLOSED" if self.quantity == 0 else "OPEN")


@dataclass
class ReplayResult:
    cash_cny: Decimal = Decimal("0")
    positions: dict[object, Position] = field(default_factory=dict)
    engine_version: str = ENGINE_VERSION

    def position_list(self) -> list[Position]:
        return [p for p in self.positions.values() if p.quantity != 0]


def _money(v: Decimal) -> Decimal:
    return v.quantize(Decimal("0.0001"), ROUND_HALF_UP)


def replay(transactions: list) -> ReplayResult:
    """fold(Transaction[<=t])：确定性顺序 → 现金 + 持仓投影。

    transactions: ORM PortfolioTransaction 行（调用方已按 as_of 过滤）。
    """
    # A REVERSAL is an append-only correction pair.  Replaying the pair as an
    # inverse trade is subtly wrong for weighted cost (especially when the
    # original was a SELL after earlier fills).  Once the correction exists,
    # the ledger's effective history is the original history with both rows
    # excluded.  The audit rows remain available for inspection.
    by_id = {
        getattr(tx, "transaction_id", None): tx
        for tx in transactions
        if getattr(tx, "transaction_id", None) is not None
    }
    canceled: set[object] = set()
    for tx in transactions:
        if getattr(tx, "transaction_type", None) != TransactionType.REVERSAL.value:
            continue
        original_id = getattr(tx, "reverses_transaction_id", None)
        original = by_id.get(original_id)
        if original is None or getattr(original, "transaction_type", None) == TransactionType.REVERSAL.value:
            raise ValueError("REVERSAL 必须引用一条存在且未被反转的原始交易")
        canceled.update({original_id, getattr(tx, "transaction_id", None)})

    ordered = sorted(
        [tx for tx in transactions if getattr(tx, "transaction_id", None) not in canceled],
        key=lambda t: (t.trade_date, t.trade_at, t.created_at),
    )
    result = ReplayResult()
    for tx in ordered:
        ttype = TransactionType(tx.transaction_type)
        fees = Decimal(tx.fees_cny or 0)

        if ttype == TransactionType.BUY:
            pos = result.positions.setdefault(
                tx.instrument_id, Position(instrument_id=tx.instrument_id))
            qty = Decimal(tx.quantity)
            buy_amount = abs(Decimal(tx.amount_cny))          # 现金视角负 → 成本正
            result.cash_cny += Decimal(tx.amount_cny) - fees
            new_qty = pos.quantity + qty
            if new_qty > 0:
                pos.avg_cost = _money(
                    (pos.quantity * pos.avg_cost + buy_amount + fees) / new_qty)
            pos.quantity = new_qty
            pos.cost_basis_cny = _money(pos.quantity * pos.avg_cost)
        elif ttype == TransactionType.SELL:
            pos = result.positions.setdefault(
                tx.instrument_id, Position(instrument_id=tx.instrument_id))
            qty = Decimal(tx.quantity)
            proceeds = Decimal(tx.amount_cny)                  # 正
            result.cash_cny += proceeds - fees
            sold = min(qty, pos.quantity)
            pos.realized_pnl_cny += _money(sold * (proceeds / qty - pos.avg_cost) - fees)
            pos.quantity -= sold
            pos.cost_basis_cny = _money(pos.quantity * pos.avg_cost)
        elif ttype == TransactionType.DIVIDEND:
            if tx.instrument_id is not None:
                result.positions.setdefault(
                    tx.instrument_id, Position(instrument_id=tx.instrument_id))
            result.cash_cny += Decimal(tx.amount_cny)          # 数量/成本不变
        elif ttype == TransactionType.FEE:
            result.cash_cny += Decimal(tx.amount_cny)          # 负
        elif ttype == TransactionType.CASH_IN:
            result.cash_cny += Decimal(tx.amount_cny)
        elif ttype == TransactionType.CASH_OUT:
            result.cash_cny += Decimal(tx.amount_cny)          # 负
        elif ttype == TransactionType.REVERSAL:
            # Validated and removed above.  Keeping this branch makes a
            # malformed object fail loudly if a custom iterable changes while
            # the fold is running.
            raise ValueError("REVERSAL 必须通过反转配对后重放")
        else:
            raise ValueError(f"不支持的交易类型: {ttype}")
    result.cash_cny = _money(result.cash_cny)
    return result


def compute_snapshot(
    replay_result: ReplayResult,
    prices: dict[object, Decimal],
    *,
    snapshot_date: date,
) -> tuple[list[dict], dict]:
    """快照黄金值：position_snapshots 全字段 + portfolio_snapshots（NAV=现金+市值）。"""
    position_rows: list[dict] = []
    total_market = Decimal("0")
    for inst, pos in replay_result.positions.items():
        if pos.quantity == 0 and pos.realized_pnl_cny == 0:
            continue
        price = prices.get(inst)
        market_value = _money(pos.quantity * price) if price is not None else None
        unrealized = _money(pos.quantity * price - pos.cost_basis_cny) if price is not None else None
        pos.market_price_cny = price
        pos.market_value_cny = market_value
        pos.unrealized_pnl_cny = unrealized
        if market_value is not None:
            total_market += market_value
        position_rows.append({
            "instrument_id": inst,
            "snapshot_date": snapshot_date,
            "quantity": pos.quantity,
            "cost_basis_cny": pos.cost_basis_cny,
            "market_price_cny": price,
            "market_value_cny": market_value,
            "realized_pnl_cny": pos.realized_pnl_cny,
            "unrealized_pnl_cny": unrealized,
            "is_qdii": False,
            "engine_version": ENGINE_VERSION,
        })
    nav = _money(replay_result.cash_cny + total_market)
    portfolio_row = {
        "snapshot_date": snapshot_date,
        "cash_cny": replay_result.cash_cny,
        "market_value_cny": _money(total_market),
        "nav_cny": nav,
        "engine_version": ENGINE_VERSION,
    }
    return position_rows, portfolio_row
