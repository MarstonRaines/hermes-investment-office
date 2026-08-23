# =====================================================================
# backend/app/fx/service.py —— FX Engine（M1：同步 + 交叉验证）
#
# S7 实测：Yahoo USDCNY=X primary（日频），FRED DEXCHUS 交叉验证（权威口径）；
# 双源偏差超阈值 → CROSS_SOURCE_DEVIATION flag + audit（TS-05 §5.5 ADVISORY）。
# 冻结约束：fx_observations 只被 ETF 引擎消费（GOLD-FX-004），
# 绝不进入组合 NAV 折算路径（ts01/ts02 冻结）。
# =====================================================================
from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.audit.service import write_provenance
from app.fx.models import FXObservation
from app.providers.contracts.base import ProviderCapability
from app.providers.contracts.macro import FxRateResult
from app.providers.gateway import DataGateway

__all__ = ["FXService", "FX_CROSS_CHECK_THRESHOLD"]

FX_CROSS_CHECK_THRESHOLD = Decimal("0.01")   # 双源偏差 >1% → CROSS_SOURCE_DEVIATION


class FXService:
    def __init__(self, gateway: DataGateway) -> None:
        self.gateway = gateway

    async def sync_fx(
        self,
        session: Session,
        start: date,
        end: date,
        *,
        cross_check: bool = True,
    ) -> dict:
        """同步 USD/CNY：yahoo primary（经 gateway fallback 链）+ fred 交叉验证。

        返回 {"written": n, "yahoo": n1, "fred": n2, "deviations": [dates]}。
        """
        # 1) primary（矩阵链：yahoo → DATA_UNAVAILABLE）
        results, _ = await self.gateway.fetch_with_fallback(
            ProviderCapability.FX_RATES,
            lambda provider: provider.get_fx_rates("USD", "CNY", start, end),
        )
        yahoo_rows = [r for r in results if r.provenance.provider == "yahoo"]

        # 2) fred 交叉验证（auxiliary，非 fallback）
        fred_map: dict[date, FxRateResult] = {}
        if cross_check:
            try:
                fred = await self.gateway.fetch_extension(
                    "fred",
                    lambda provider: provider.get_fx_rates("USD", "CNY", start, end),
                )
                fred_map = {r.trade_date: r for r in fred if r.trade_date}
            except Exception:  # noqa: BLE001 —— 交叉验证失败不阻塞主链路
                fred_map = {}

        # 3) 偏差判定 + 落库
        written = 0
        deviations: list[str] = []
        for r in yahoo_rows:
            flags = list(r.provenance.quality_flags)
            fred_rate = fred_map.get(r.trade_date)
            if fred_rate is not None and r.rate:
                diff = abs(r.rate - fred_rate.rate) / r.rate
                if diff > FX_CROSS_CHECK_THRESHOLD:
                    flags.append("CROSS_SOURCE_DEVIATION")
                    deviations.append(f"{r.trade_date}:yahoo={r.rate}/fred={fred_rate.rate}")
            env = r.provenance.model_copy(update={"quality_flags": flags})
            written += self._upsert_observation(session, r, env)
        return {"written": written, "yahoo": len(yahoo_rows),
                "fred": len(fred_map), "deviations": deviations}

    def _upsert_observation(self, session: Session, r: FxRateResult, env) -> int:
        prov = write_provenance(session, env)
        prov.provenance_id = prov.provenance_id or uuid4()
        stmt = insert(FXObservation).values(
            fx_observation_id=uuid4(),
            base_currency="USD", quote_currency="CNY",
            rate=r.rate, as_of=r.as_of, trade_date=r.trade_date,
            provider=r.provenance.provider,
            provenance_id=prov.provenance_id,
        ).on_conflict_do_nothing(constraint="uq_fx_inst_pair_asof_provider")
        return session.execute(stmt).rowcount

    def get_fx_rate(self, session: Session, as_of: datetime | date) -> Decimal | None:
        """as_of 时点最近一笔 USD/CNY（QDII 分析用）。"""
        if isinstance(as_of, date) and not isinstance(as_of, datetime):
            as_of = datetime.combine(as_of, datetime.min.time(), tzinfo=UTC)
        row = session.execute(
            select(FXObservation.rate)
            .where(
                FXObservation.base_currency == "USD",
                FXObservation.quote_currency == "CNY",
                FXObservation.as_of <= as_of,
            )
            .order_by(FXObservation.as_of.desc())
            .limit(1)
        ).first()
        return row[0] if row else None
