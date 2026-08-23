# =====================================================================
# backend/app/corporate_actions/service.py —— Corporate Actions（冻结规范 §20）
#
# - adj_factor 与 OHLCVA 的 adj_factor 对应，由本模块统一维护
#   （来源 + 生效日 + 参数可追溯）；
# - v0.1 行动来源：TuShare dividend（2000 积分档实测，2026-08-24），
#   ex_date = 除权除息日（因子生效日）；
# - CTR-PAR-005：corporate_actions.adj_factor 与 ohlcva adj_factor 一致
#   （verify_adj_factor_consistency）。
# =====================================================================
from __future__ import annotations

from datetime import UTC, date
from decimal import Decimal
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.audit.service import write_provenance
from app.common.enums import CorporateActionStatus, CorporateActionType
from app.corporate_actions.models import CorporateAction
from app.providers.contracts.base import ProvenanceEnvelope
from app.providers.gateway import DataGateway

__all__ = ["CorporateActionsService"]

TRANSFORM_VERSION = "corporate-actions-normalizer/0.1.0"


class CorporateActionsService:
    def __init__(self, gateway: DataGateway) -> None:
        self.gateway = gateway

    async def sync_adj_factors(
        self,
        session: Session,
        instrument_id,
        factors: list,
        source: str = "tushare",
    ) -> dict:
        """adj_factor 序列 → corporate_actions 因子事件（因子变更日 = 生效日）。

        factors: list[AdjFactorResult]（已含 provenance）。
        幂等：同 (instrument, ex_date) 已存在则不重复写。
        返回 {"written": n, "events": n}。
        """
        written = 0
        events = 0
        # 检测因子变更日（与前一交易日不同）
        prev: Decimal | None = None
        change_days: list[tuple[date, Decimal, Decimal | None]] = []
        for f in sorted(factors, key=lambda x: x.trade_date):
            if prev is not None and f.adj_factor != prev:
                change_days.append((f.trade_date, f.adj_factor, prev))
                events += 1
            prev = f.adj_factor

        existing = {
            (row.ex_date, row.action_type)
            for row in session.execute(
                select(CorporateAction).where(
                    CorporateAction.instrument_id == instrument_id,
                    CorporateAction.action_type == CorporateActionType.DIVIDEND.value,
                )
            ).scalars()
        }
        for ex_date, factor, prev_factor in change_days:
            if (ex_date, CorporateActionType.DIVIDEND.value) in existing:
                continue
            env = ProvenanceEnvelope(
                source="cn_adj_factor", provider=source,
                source_record_id=f"{source}@{instrument_id}@{ex_date.isoformat()}",
                observed_at=_as_dt(ex_date), retrieved_at=_as_dt(ex_date),
                as_of_date=ex_date, quality_score=Decimal("0.96"),
                quality_status="VERIFIED", transform_version=TRANSFORM_VERSION,
            )
            prov = write_provenance(session, env)
            prov.provenance_id = prov.provenance_id or uuid4()
            row = CorporateAction(
                corporate_action_id=uuid4(),
                instrument_id=instrument_id,
                action_type=CorporateActionType.DIVIDEND.value,
                ex_date=ex_date,
                parameters={
                    "source": "adj_factor_series",
                    "adj_factor": str(factor),
                    "prev_factor": str(prev_factor) if prev_factor is not None else None,
                    "note": "因子变更事件（类型以 dividend 接口同步为准）",
                },
                adj_factor=factor,
                status=CorporateActionStatus.IMPLEMENTED.value,
                provenance_id=prov.provenance_id,
            )
            session.add(row)
            written += 1
        return {"written": written, "events": events}

    async def sync_dividends(
        self,
        session: Session,
        instrument_id,
        dividends: list[dict],
        *,
        provider: str = "tushare",
    ) -> int:
        """TuShare dividend（真实行动类型）→ corporate_actions。

        幂等：同 (instrument, ex_date, action_type) 已存在则跳过。
        """
        written = 0
        existing = {
            (row.ex_date, row.action_type)
            for row in session.execute(
                select(CorporateAction).where(CorporateAction.instrument_id == instrument_id)
            ).scalars()
        }
        for d in dividends:
            ex_date = date.fromisoformat(str(d["ex_date"])[:10])
            cash = _dec(d.get("cash_div"))
            stk = _dec(d.get("stk_bo_rate")) or _dec(d.get("stk_div"))
            if cash is not None and cash > 0:
                action_type = CorporateActionType.DIVIDEND.value
            elif stk is not None and stk > 0:
                action_type = CorporateActionType.BONUS_SHARE.value
            else:
                continue
            if (ex_date, action_type) in existing:
                continue
            announce = d.get("announce_date")
            env = ProvenanceEnvelope(
                source="cn_dividend", provider=provider,
                source_record_id=f"{provider}@{instrument_id}@{ex_date.isoformat()}",
                published_at=_as_dt(announce) if announce else None,
                observed_at=_as_dt(ex_date), retrieved_at=_as_dt(ex_date),
                as_of_date=ex_date, quality_score=Decimal("0.96"),
                quality_status="VERIFIED", transform_version=TRANSFORM_VERSION,
            )
            prov = write_provenance(session, env)
            prov.provenance_id = prov.provenance_id or uuid4()
            session.add(CorporateAction(
                corporate_action_id=uuid4(),
                instrument_id=instrument_id,
                action_type=action_type,
                announce_date=_as_date(announce) if announce else None,
                ex_date=ex_date,
                record_date=_as_date(d.get("record_date")) if d.get("record_date") else None,
                parameters={
                    "cash_div_per_10": str(cash) if cash is not None else None,
                    "stk_ratio_per_10": str(stk) if stk is not None else None,
                    "div_proc": d.get("div_proc"),
                },
                status=CorporateActionStatus.IMPLEMENTED.value,
                provenance_id=prov.provenance_id,
            ))
            existing.add((ex_date, action_type))
            written += 1
        return written

    def verify_adj_factor_consistency(
        self,
        session: Session,
        instrument_id,
        parquet_store,
    ) -> bool:
        """CTR-PAR-005：corporate_actions.adj_factor 与 ohlcva adj_factor 一致。

        对每个有 ex_date 的 corporate_actions 行：ohlcva 该日 adj_factor
        （经 Parquet）必须等于行内因子（容差 1e-6）。
        """
        rows = session.execute(
            select(CorporateAction).where(
                CorporateAction.instrument_id == instrument_id,
                CorporateAction.ex_date.is_not(None),
                CorporateAction.adj_factor.is_not(None),
            )
        ).scalars().all()
        if not rows:
            return True   # 无行动记录视为一致（v0.1 空语义）
        bars = {r["trade_date"]: r["adj_factor"] for r in
                parquet_store.read_ohlcva(str(instrument_id))}
        for row in rows:
            bar_factor = bars.get(row.ex_date)
            if bar_factor is None:
                return False
            if abs(Decimal(str(bar_factor)) - row.adj_factor) > Decimal("0.000001"):
                return False
        return True


def _as_dt(v) -> object:
    from datetime import datetime

    d = _as_date(v)
    return datetime.combine(d, datetime.min.time(), tzinfo=UTC) if d else None


def _as_date(v):
    if v is None:
        return None
    s = str(v)[:10]
    try:
        return date.fromisoformat(s)
    except ValueError:
        return None


def _dec(v) -> Decimal | None:
    if v is None or str(v) in ("nan", ""):
        return None
    try:
        return Decimal(str(v))
    except Exception:  # noqa: BLE001
        return None
