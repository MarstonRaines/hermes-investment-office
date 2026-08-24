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
from pathlib import Path
from uuid import UUID, uuid4

import yaml
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.audit.service import write_audit_event
from app.briefing.models import DailyBrief, DailyContext
from app.calendar.service import CalendarService
from app.common.enums import (
    ActorType,
    AuditAction,
    BriefStatus,
    FreshnessStatus,
    MarketCode,
    QuotaStatus,
)
from app.common.freshness import freshness_payload
from app.etf.models import ETFHoldingSnapshot, ETFMetricSnapshot, ETFNavObservation, ETFProfile
from app.fundamentals.models import FinancialFact
from app.fx.models import FXObservation
from app.market_data.models import IndexBarIndex
from app.market_data.service import MarketDataService

__all__ = ["BriefingService", "BriefingDomainError"]


class BriefingDomainError(Exception):
    code = "BRIEFING_DOMAIN_ERROR"


class BriefingService:
    def __init__(self, market_service: MarketDataService, calendar: CalendarService | None = None,
                 thresholds: dict | None = None) -> None:
        self.market_service = market_service
        self.calendar = calendar or CalendarService()
        self.thresholds = thresholds or {
            "market": {"warn_lag_sessions": 1, "stale_lag_sessions": 2},
            "fundamental": {"warn_ingestion_hours": 24, "warn_days": 365, "stale_days": 730},
            "etf_nav": {"warn_lag_sessions": 1, "stale_lag_sessions": 2},
            "etf_holdings": {"warn_disclosure_cycle_days": 60, "warn_days": 60, "stale_days": 120},
            "index": {"warn_lag_sessions": 1, "stale_lag_sessions": 2},
            "fx": {"warn_business_days": 1, "warn_lag_sessions": 1, "stale_lag_sessions": 2},
            "quota": {"warn_days": 1, "stale_days": 7},
        }

    @classmethod
    def from_settings(cls) -> BriefingService:
        """Composition seam used by REST; dependency wiring stays out of api/."""

        from app.common.config import settings
        from app.market_data.parquet import ParquetStore

        thresholds = None
        path = Path(settings.freshness_config_path)
        if path.exists():
            thresholds = yaml.safe_load(path.read_text(encoding="utf-8"))
        return cls(
            MarketDataService(ParquetStore(f"{settings.data_dir}/parquet")),
            CalendarService(),
            thresholds=thresholds,
        )

    # ---- Daily Context ----

    def build_daily_context(
        self,
        session: Session,
        market_date: date,
        *,
        instruments: list[UUID],
        engine_versions: dict | None = None,
    ) -> DailyContext:
        """Build the seven-domain freshness contract from persisted calendars/config."""
        now = datetime.combine(market_date, datetime.max.time(), tzinfo=UTC)
        expected = self.calendar.prev_trading_day(session, market_date) or market_date

        evaluated_at = now.isoformat()

        def date_domain(name: str, latest: date | None, expected_date: date | None,
                        *, applicable: bool = True, missing: FreshnessStatus = FreshnessStatus.STALE,
                        market: MarketCode = MarketCode.CN) -> dict:
            if not applicable:
                status = FreshnessStatus.OK
                lag = None
            elif latest is None or expected_date is None:
                status = missing
                lag = None
            else:
                lag = self.calendar.trading_day_distance(
                    session, latest, expected_date,
                    market=market,
                )
                if lag is None:
                    status = FreshnessStatus.WARNING
                elif lag >= self.thresholds[name].get(
                    "stale_lag_sessions", self.thresholds[name].get("stale_sessions", 10**9)
                ):
                    status = FreshnessStatus.STALE
                elif lag >= self.thresholds[name].get(
                    "warn_lag_sessions", self.thresholds[name].get("warn_sessions", 10**9)
                ):
                    status = FreshnessStatus.WARNING
                else:
                    status = FreshnessStatus.OK
            config = self.thresholds[name]
            return {
                "status": status.value, "evaluated_at": evaluated_at,
                "latest_point": latest.isoformat() if latest else None,
                "expected_point": expected_date.isoformat() if expected_date else None,
                "latest": latest.isoformat() if latest else None,
                "expected": expected_date.isoformat() if expected_date else None,
                "lag": {"sessions": lag, "days": (expected_date - latest).days
                         if latest and expected_date else None},
                "lag_sessions": lag, "applicable": applicable,
                "thresholds": config,
                "detail": f"{name} latest={latest.isoformat()}" if latest else f"{name} unavailable",
                "affected_scope": None,
                "stale_provenance_ids": [],
                "required_action": None if status is FreshnessStatus.OK else f"resync:{name}",
            }

        latest_market = [self.market_service.latest_trade_date(session, iid) for iid in instruments]
        market_latest = max((value for value in latest_market if value), default=None)
        domains: dict[str, dict] = {"market": date_domain("market", market_latest, expected)}

        facts = session.scalars(select(FinancialFact).where(
            FinancialFact.instrument_id.in_(instruments) if instruments else False,
            FinancialFact.published_at.is_not(None),
            FinancialFact.published_at <= now,
        ).order_by(FinancialFact.published_at.desc()).limit(1)).first()
        domains["fundamental"] = self._age_domain(
            "fundamental", facts.published_at.date() if facts and facts.published_at else None,
            market_date, applicable=bool(instruments), missing=FreshnessStatus.WARNING,
        )

        profiles = list(session.scalars(select(ETFProfile).where(
            ETFProfile.instrument_id.in_(instruments) if instruments else False,
        )).all())
        etf_ids = [row.instrument_id for row in profiles]
        qdii_ids = [row.instrument_id for row in profiles if row.is_qdii]
        nav = session.scalar(select(ETFNavObservation.nav_date).where(
            ETFNavObservation.instrument_id.in_(etf_ids) if etf_ids else False,
            ETFNavObservation.nav_date <= market_date,
        ).order_by(ETFNavObservation.nav_date.desc()).limit(1))
        domains["etf_nav"] = date_domain("etf_nav", nav, expected, applicable=bool(etf_ids))
        holding = session.scalar(select(ETFHoldingSnapshot.disclosure_date).where(
            ETFHoldingSnapshot.instrument_id.in_(etf_ids) if etf_ids else False,
            ETFHoldingSnapshot.disclosure_date <= market_date,
        ).order_by(ETFHoldingSnapshot.disclosure_date.desc()).limit(1))
        domains["etf_holdings"] = self._age_domain(
            "etf_holdings", holding, market_date, applicable=bool(etf_ids), missing=FreshnessStatus.WARNING,
        )
        index_ids = [row.underlying_index_id for row in profiles if row.is_qdii and row.underlying_index_id]
        index_date = session.scalar(select(IndexBarIndex.trade_date).where(
            IndexBarIndex.instrument_id.in_(index_ids) if index_ids else False,
            IndexBarIndex.data_kind == "PRICE", IndexBarIndex.trade_date <= market_date,
        ).order_by(IndexBarIndex.trade_date.desc()).limit(1))
        expected_index = self.calendar.prev_trading_day(session, market_date, MarketCode.US) or market_date
        domains["index"] = date_domain(
            "index", index_date, expected_index, applicable=bool(index_ids), market=MarketCode.US,
        )
        fx = session.scalar(select(FXObservation.as_of).where(
            FXObservation.base_currency == "USD", FXObservation.quote_currency == "CNY",
            FXObservation.as_of <= now,
        ).order_by(FXObservation.as_of.desc()).limit(1))
        domains["fx"] = date_domain(
            "fx", fx.date() if fx else None, expected_index, applicable=bool(qdii_ids),
            market=MarketCode.US,
            missing=FreshnessStatus.WARNING,
        )
        metric = session.scalar(select(ETFMetricSnapshot).where(
            ETFMetricSnapshot.instrument_id.in_(qdii_ids) if qdii_ids else False,
            ETFMetricSnapshot.as_of <= now,
        ).order_by(ETFMetricSnapshot.as_of.desc()).limit(1))
        quota_status = getattr(metric, "quota_status", None) if metric else None
        quota_unknown = bool(qdii_ids) and (metric is None or str(getattr(quota_status, "value", quota_status)) == QuotaStatus.UNKNOWN.value)
        domains["quota"] = {
            "status": FreshnessStatus.WARNING.value if quota_unknown else (
                FreshnessStatus.OK.value if qdii_ids else FreshnessStatus.OK.value
            ),
            "evaluated_at": evaluated_at,
            "latest_point": metric.as_of.isoformat() if metric else None,
            "expected_point": now.isoformat(),
            "latest": metric.as_of.isoformat() if metric else None,
            "expected": now.isoformat(), "applicable": bool(qdii_ids),
            "lag": {"sessions": None, "days": None},
            "thresholds": self.thresholds["quota"],
            "source_validity": "unknown" if quota_unknown else ("valid" if qdii_ids else "not_applicable"),
            "detail": "quota status UNKNOWN; source validity is unknown" if quota_unknown else "quota status is persisted",
            "affected_scope": [str(value) for value in qdii_ids] or None,
            "required_action": "confirm_quota_status" if quota_unknown else None,
            "stale_provenance_ids": [str(metric.provenance_id)] if quota_unknown and metric else [],
        }
        data_freshness = freshness_payload(domains)
        overall = FreshnessStatus(data_freshness["overall"])
        existing = self.get_daily_context(session, market_date)
        if existing is None:
            ctx = DailyContext(
                daily_context_id=uuid4(), market_date=market_date,
                generated_at=datetime.now(UTC), freshness_status=overall.value,
                data_freshness=data_freshness["domains"],
                markets={"CN": {"date": market_date.isoformat(), "session": "CLOSED"},
                         "US": {"date": None, "session": "UNKNOWN"}},
                engine_versions=engine_versions or {}, source_status={}, summary=None,
            )
            session.add(ctx)
        else:
            before = existing.freshness_status
            existing.generated_at = datetime.now(UTC)
            existing.freshness_status = overall.value
            existing.data_freshness = data_freshness["domains"]
            existing.engine_versions = engine_versions or existing.engine_versions or {}
            ctx = existing
            if before != overall.value:
                write_audit_event(
                    session, action=AuditAction.FRESHNESS_CHANGE,
                    entity_type="daily_context", entity_id=ctx.daily_context_id,
                    actor_type=ActorType.JOB, actor_id="freshness",
                    payload={"from": before, "to": overall.value, "domains": data_freshness["domains"]},
                )
        session.flush()
        return ctx

    def _age_domain(self, name: str, latest: date | None, expected: date | None,
                    *, applicable: bool, missing: FreshnessStatus) -> dict:
        if not applicable:
            status = FreshnessStatus.OK
            age = None
        elif latest is None or expected is None:
            status = missing
            age = None
        else:
            age = max(0, (expected - latest).days)
            cfg = self.thresholds[name]
            status = (
                FreshnessStatus.STALE if age >= cfg.get("stale_days", 10**9)
                else FreshnessStatus.WARNING if age >= cfg.get("warn_days", 10**9)
                else FreshnessStatus.OK
            )
        config = self.thresholds[name]
        return {
            "status": status.value, "evaluated_at": datetime.now(UTC).isoformat(),
            "latest_point": latest.isoformat() if latest else None,
            "expected_point": expected.isoformat() if expected else None,
            "latest": latest.isoformat() if latest else None,
            "expected": expected.isoformat() if expected else None,
            "lag": {"sessions": None, "days": age}, "age_days": age,
            "thresholds": config,
            "detail": f"{name} latest={latest.isoformat()}" if latest else f"{name} unavailable",
            "affected_scope": None, "applicable": applicable, "stale_provenance_ids": [],
            "required_action": None if status is FreshnessStatus.OK else f"resync:{name}",
        }

    def get_daily_context(self, session: Session, market_date: date) -> DailyContext | None:
        """market_date 幂等查询（MCP 消费入口）。"""
        return session.execute(
            select(DailyContext).where(DailyContext.market_date == market_date)
        ).scalars().first()

    def freshness_as_of(self, session: Session, market_date: date) -> dict:
        """Return the latest persisted freshness contract at or before a date."""
        row = session.scalar(select(DailyContext).where(
            DailyContext.market_date <= market_date,
        ).order_by(DailyContext.market_date.desc()).limit(1))
        if row is None:
            return {"overall": FreshnessStatus.OK.value, "domains": {}}
        return {"overall": row.freshness_status, "domains": row.data_freshness or {}}

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
        if session.get(DailyContext, daily_context_id) is None:
            raise BriefingDomainError("daily_context 不存在")
        existing = session.scalar(select(DailyBrief).where(DailyBrief.market_date == market_date))
        if existing is not None:
            return existing
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

    def get_daily_brief(self, session: Session, market_date: date) -> DailyBrief | None:
        return session.scalar(select(DailyBrief).where(DailyBrief.market_date == market_date))
