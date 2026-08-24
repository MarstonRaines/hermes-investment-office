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

__all__ = [
    "ENGINE_VERSION", "Position", "ReplayResult", "compute_twr", "replay", "compute_snapshot",
]

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


def replay(transactions: list, corporate_actions: list | None = None) -> ReplayResult:
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

    ordered = [
        (getattr(tx, "trade_date"), 1, getattr(tx, "trade_at", None), getattr(tx, "created_at", None), tx)
        for tx in transactions if getattr(tx, "transaction_id", None) not in canceled
    ]
    for action in corporate_actions or []:
        action_date = _action_date(action)
        status = str(getattr(getattr(action, "status", None), "value", getattr(action, "status", "IMPLEMENTED")))
        if action_date is not None and status in {"IMPLEMENTED", "ADJUSTED"}:
            ordered.append((action_date, 0, None, getattr(action, "created_at", None), action))
    ordered.sort(key=lambda item: (item[0], item[1], str(item[2] or item[3] or "")))
    result = ReplayResult()
    for _, event_kind, _, _, tx in ordered:
        if event_kind == 0:
            _apply_corporate_action(result, tx)
            continue
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


def compute_twr(periods: list[dict]) -> Decimal:
    """Compute the frozen TWR chain from NAV points and external cash flows.

    Each item contains ``start_nav``, ``end_nav`` and optional ``external_flow``;
    the flow is positive for CASH_IN and negative for CASH_OUT.
    """
    result = Decimal("1")
    for period in periods:
        start = Decimal(str(period["start_nav"]))
        end = Decimal(str(period["end_nav"]))
        flow = Decimal(str(period.get("external_flow", "0")))
        denominator = start + flow
        if denominator <= 0:
            raise ValueError("TWR 的 NAV + external_flow 必须为正")
        result *= end / denominator
    return _money(result - Decimal("1"))


def _action_date(action) -> date | None:
    return (
        getattr(action, "effective_date", None)
        or getattr(action, "ex_date", None)
        or getattr(action, "record_date", None)
    )


def _parameter(parameters: dict, *names: str) -> Decimal | None:
    for name in names:
        value = parameters.get(name)
        if value is not None:
            try:
                parsed = Decimal(str(value))
            except Exception as exc:  # noqa: BLE001
                raise ValueError(f"corporate action 参数 {name} 非数值") from exc
            if parsed <= 0:
                raise ValueError(f"corporate action 参数 {name} 必须为正")
            return parsed
    return None


def _apply_corporate_action(result: ReplayResult, action) -> None:
    instrument_id = getattr(action, "instrument_id", None)
    if instrument_id is None:
        return
    position = result.positions.get(instrument_id)
    if position is None or position.quantity <= 0:
        return
    action_type = str(getattr(getattr(action, "action_type", None), "value", action.action_type))
    parameters = getattr(action, "parameters", None) or {}
    if action_type == "SPLIT":
        ratio = _parameter(parameters, "split_ratio", "ratio")
        if ratio is None:
            raise ValueError("SPLIT 缺少 split_ratio")
        position.quantity *= ratio
        position.avg_cost = _money(position.avg_cost / ratio)
    elif action_type == "BONUS_SHARE":
        ratio = _parameter(parameters, "bonus_ratio", "stk_ratio", "ratio")
        if ratio is None:
            ratio_per_10 = _parameter(parameters, "stk_ratio_per_10")
            ratio = ratio_per_10 / Decimal("10") if ratio_per_10 is not None else None
        if ratio is None:
            raise ValueError("BONUS_SHARE 缺少 bonus_ratio")
        factor = Decimal("1") + ratio
        position.quantity *= factor
        position.avg_cost = _money(position.avg_cost / factor)
    elif action_type == "RIGHTS_ISSUE":
        ratio = _parameter(parameters, "rights_ratio", "ratio")
        if ratio is None:
            ratio_per_10 = _parameter(parameters, "rights_ratio_per_10")
            ratio = ratio_per_10 / Decimal("10") if ratio_per_10 is not None else None
        price = _parameter(parameters, "subscription_price", "price_cny")
        if ratio is None or price is None:
            raise ValueError("RIGHTS_ISSUE 缺少认购比例或认购价")
        rights_quantity = position.quantity * ratio
        amount = rights_quantity * price
        result.cash_cny -= amount
        old_cost_basis = position.cost_basis_cny
        position.quantity += rights_quantity
        position.cost_basis_cny = _money(old_cost_basis + amount)
        position.avg_cost = _money(position.cost_basis_cny / position.quantity)
    elif action_type == "DIVIDEND":
        cash = _parameter(parameters, "cash_per_share", "dividend_per_share")
        if cash is None:
            cash_per_10 = _parameter(parameters, "cash_div_per_10")
            cash = cash_per_10 / Decimal("10") if cash_per_10 is not None else None
        if cash is not None:
            result.cash_cny += position.quantity * cash


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
