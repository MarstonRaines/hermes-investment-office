# =====================================================================
# backend/app/briefing/service.py —— Daily Brief 最小版（M1.5 Vertical Slice）
#
# - build_daily_context：确定性构建 daily_contexts（freshness 状态 + 数据明细）
#   —— 冻结规范 §36 Freshness Contract 的 v0.1 落地；
# - save_daily_brief：brief 落库（model_profile 必填 = profile 标识，
#   禁止记录具体模型名，ARCH-API-005）；
# - get_daily_context：MCP 消费入口（market_date 幂等查询）。
#
# freshness 判定（v0.1 确定性规则，文档化）：
#   market 域：最新行情 trade_date >= 最近交易日（CalendarService.prev_trading_day）
#              → OK；缺失 → STALE；
#   fundamental 域：存在已披露财务事实 → OK；无 → WARNING（披露未到属合法缺口）；
#   fx 域：最近 3 个自然日内有观测 → OK；否则 WARNING（QDII 分析降级）。
#   任一域 STALE → 总状态 STALE；任一域 FAILED → FAILED；否则取最差。
# =====================================================================
from __future__ import annotations

from datetime import UTC, date, datetime
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.briefing.models import DailyBrief, DailyContext
from app.calendar.service import CalendarService
from app.common.enums import BriefStatus, FreshnessStatus
from app.fundamentals.models import FinancialFact
from app.fx.models import FXObservation
from app.market_data.service import MarketDataService

__all__ = ["BriefingService", "BriefingDomainError"]


class BriefingDomainError(Exception):
    code = "BRIEFING_DOMAIN_ERROR"


class BriefingService:
    def __init__(self, market_service: MarketDataService, calendar: CalendarService | None = None) -> None:
        self.market_service = market_service
        self.calendar = calendar or CalendarService()

    # ---- Daily Context ----

    def build_daily_context(
        self,
        session: Session,
        market_date: date,
        *,
        instruments: list[UUID],
        engine_versions: dict | None = None,
    ) -> DailyContext:
        """确定性构建 daily_contexts（freshness 判定 + 数据明细）。"""
        today = date.today()
        prev_td = self.calendar.prev_trading_day(session, market_date)
        expected = prev_td if prev_td else market_date

        # market 域：任一标的的最新行情 >= 最近交易日
        latest = {
            iid: self.market_service.latest_trade_date(session, iid)
            for iid in instruments
        }
        market_ok = any(ld is not None and ld >= expected for ld in latest.values()) if instruments else False
        market_status = FreshnessStatus.OK if market_ok else FreshnessStatus.STALE

        # fundamental 域：存在已披露事实
        fact_count = session.execute(
            select(FinancialFact).where(FinancialFact.published_at.is_not(None)).limit(1)
        ).scalars().first()
        fundamental_status = FreshnessStatus.OK if fact_count else FreshnessStatus.WARNING

        # fx 域：最近 3 个自然日有观测
        fx_cutoff = datetime.combine(today - __import__("datetime").timedelta(days=3),
                                     datetime.min.time(), tzinfo=UTC)
        fx_row = session.execute(
            select(FXObservation).where(FXObservation.as_of >= fx_cutoff).limit(1)
        ).scalars().first()
        fx_status = FreshnessStatus.OK if fx_row else FreshnessStatus.WARNING

        data_freshness = {
            "market": market_status.value,
            "fundamental": fundamental_status.value,
            "fx": fx_status.value,
        }
        statuses = [market_status, fundamental_status, fx_status]
        if FreshnessStatus.FAILED in statuses:
            overall = FreshnessStatus.FAILED
        elif FreshnessStatus.STALE in statuses:
            overall = FreshnessStatus.STALE
        elif FreshnessStatus.WARNING in statuses:
            overall = FreshnessStatus.WARNING
        else:
            overall = FreshnessStatus.OK

        ctx = DailyContext(
            daily_context_id=uuid4(),
            market_date=market_date,
            generated_at=datetime.now(UTC),
            freshness_status=overall.value,
            data_freshness=data_freshness,
            markets={"CN": {"date": market_date.isoformat(), "session": "CLOSED"},
                     "US": {"date": None, "session": "UNKNOWN"}},
            engine_versions=engine_versions or {},
            source_status={},
            summary=None,
        )
        session.add(ctx)
        session.flush()
        return ctx

    def get_daily_context(self, session: Session, market_date: date) -> DailyContext | None:
        """market_date 幂等查询（MCP 消费入口）。"""
        return session.execute(
            select(DailyContext).where(DailyContext.market_date == market_date)
        ).scalars().first()

    # ---- Daily Brief ----

    def save_daily_brief(
        self,
        session: Session,
        daily_context_id: UUID,
        market_date: date,
        content_md: str,
        *,
        sections: list[dict] | None = None,
        model_profile: str = "fast",
    ) -> DailyBrief:
        """brief 落库（model_profile 必填 = profile 标识；禁止记录具体模型名）。"""
        if not model_profile:
            raise BriefingDomainError("model_profile 必填（ARCH-API-005：不记录具体模型名）")
        brief = DailyBrief(
            daily_brief_id=uuid4(),
            daily_context_id=daily_context_id,
            market_date=market_date,
            content_md=content_md,
            sections=sections,
            model_profile=model_profile,
            status=BriefStatus.DRAFT.value,
            generated_by="hermes-backend",
        )
        session.add(brief)
        session.flush()
        return brief
