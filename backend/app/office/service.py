"""投资办公室只读聚合服务。

Dashboard 只消费这里产出的稳定 JSON 视图，不直连数据库、不读取 Parquet，也不在
浏览器端重算投资指标。写操作仍由各领域服务拥有。
"""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from math import sqrt
from statistics import stdev
from typing import Any
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.briefing.models import AttentionItem, DailyBrief, DailyContext
from app.briefing.service import BriefingService
from app.common.config import settings
from app.common.enums import PortfolioMode
from app.corporate_actions.models import CorporateAction
from app.etf.models import ETFProfile
from app.etf.service import read_metric_snapshot
from app.fundamentals.models import FinancialFact
from app.instruments.models import Instrument, Watchlist, WatchlistMember
from app.jobs.models import JobRun
from app.market_data.models import MarketBarIndex
from app.market_data.service import MarketDataService
from app.portfolio.engine import compute_twr
from app.portfolio.models import (
    Portfolio,
    PortfolioSnapshot,
    PortfolioTransaction,
    PositionSnapshot,
    TradeProposal,
)
from app.research.models import ResearchNote
from app.research.service import ResearchService
from app.thesis.models import Thesis, ThesisEvent, ThesisReview
from app.thesis.service import ThesisService
from app.valuation.engine import compute_objective
from app.valuation.models import ValuationRun

__all__ = ["OfficeService"]


def _value(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if hasattr(value, "value"):
        return value.value
    return value


def _json_value(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    return _value(value)


def _symbol(instrument: Instrument) -> str:
    if "." in instrument.symbol:
        return instrument.symbol
    suffix = ".SH" if instrument.market == "SSE" else ".SZ"
    return f"{instrument.symbol}{suffix}"


def _as_float(value: Any) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _visible_at(as_of: date) -> datetime:
    """Return the PIT cutoff without making future intraday rows visible."""

    return min(datetime.combine(as_of, time.max, tzinfo=UTC), datetime.now(UTC))


def _event_text(payload: Any) -> str:
    """把审计 payload 收敛为用户可读摘要，不把 Python/JSON 字典直出到界面。"""

    if not isinstance(payload, dict):
        return str(_value(payload) or "已记录")
    labels = {
        "revision": "版本",
        "authored_by": "作者",
        "change_reason": "变更原因",
        "health_after": "变更后健康度",
        "conclusion": "结论",
        "note": "说明",
        "source": "来源",
        "adj_factor": "复权因子",
        "prev_factor": "前一复权因子",
        "cash_dividend_per_share": "每股现金分红",
        "bonus_share_ratio": "送股比例",
        "split_ratio": "拆分比例",
        "rights_issue_ratio": "配股比例",
    }
    parts: list[str] = []
    for key, label in labels.items():
        value = payload.get(key)
        if value in (None, "", [], {}):
            continue
        if isinstance(value, (list, tuple)):
            rendered = "、".join(str(_value(item)) for item in value)
        elif isinstance(value, dict):
            continue
        else:
            rendered = str(_value(value))
        if rendered == "SYSTEM":
            rendered = "系统"
        parts.append(f"{label} {rendered}")
    return " · ".join(parts) or "已记录结构化详情"


class OfficeService:
    def __init__(self, market: MarketDataService | None = None) -> None:
        self.market = market or MarketDataService.from_settings()

    def today(self, session: Session, as_of: date) -> dict:
        context = session.scalar(select(DailyContext).where(
            DailyContext.market_date <= as_of,
        ).order_by(DailyContext.market_date.desc()).limit(1))
        brief = session.scalar(select(DailyBrief).where(
            DailyBrief.market_date <= as_of,
        ).order_by(DailyBrief.market_date.desc()).limit(1))
        portfolio = self._default_portfolio(session)
        portfolio_view = self._portfolio_summary(session, portfolio, as_of) if portfolio else None
        watchlist = self._watchlist(session, as_of)
        attention = self._attention(session, context, portfolio, as_of)
        freshness = (
            {"overall": context.freshness_status, "domains": context.data_freshness or {}}
            if context else BriefingService.from_settings().freshness_as_of(session, as_of)
        )
        latest_ingestion = session.scalar(select(func.max(MarketBarIndex.ingested_at)))
        return {
            "as_of": as_of.isoformat(),
            "updated_at": _value(latest_ingestion),
            "freshness": freshness,
            "system": self.system_status(session, as_of),
            "portfolio": portfolio_view,
            "watchlist": watchlist,
            "attention": attention,
            "brief": {
                "market_date": brief.market_date.isoformat(),
                "content_md": brief.content_md,
                "sections": brief.sections or [],
                "status": brief.status,
            } if brief else None,
            "today_points": self._today_points(
                freshness=freshness,
                watchlist=watchlist,
                portfolio=portfolio_view,
                attention=attention,
            ),
        }

    def watchlist(self, session: Session, as_of: date) -> dict:
        return {
            "as_of": as_of.isoformat(),
            "watchlist": self._watchlist(session, as_of, history_days=370),
            "freshness": BriefingService.from_settings().freshness_as_of(session, as_of),
        }

    def instrument(self, session: Session, instrument_id: UUID, as_of: date) -> dict | None:
        instrument = session.get(Instrument, instrument_id)
        if instrument is None:
            return None
        history_start = as_of - timedelta(days=370)
        bars = self.market.get_ohlcva(
            session,
            instrument_id,
            start=history_start - timedelta(days=60),
            end=as_of,
            as_of=as_of,
        )
        history = [
            row for row in self._market_history(bars)
            if row["trade_date"] >= history_start.isoformat()
        ]
        visible_at = _visible_at(as_of)
        metric = read_metric_snapshot(session, instrument_id, as_of=visible_at)
        valuation = session.scalar(select(ValuationRun).where(
            ValuationRun.instrument_id == instrument_id,
            ValuationRun.as_of <= visible_at,
            ValuationRun.status.in_(["COMPLETED", "SUPERSEDED"]),
        ).order_by(ValuationRun.as_of.desc()).limit(1))
        thesis = session.scalar(select(Thesis).where(
            Thesis.instrument_id == instrument_id,
            Thesis.created_at <= visible_at,
        ).order_by(Thesis.created_at.desc()).limit(1))
        revision = ThesisService().get_thesis(
            session, thesis.thesis_id, as_of=visible_at,
        ) if thesis else None
        notes = list(session.scalars(select(ResearchNote).where(
            ResearchNote.instrument_id == instrument_id,
            ResearchNote.created_at <= visible_at,
        ).order_by(ResearchNote.created_at.desc()).limit(20)).all())
        evidence = ResearchService().get_evidence(
            session,
            instrument_id=instrument_id,
            thesis_revision_id=revision.thesis_revision_id if revision else None,
            limit=20,
        )
        evidence = [row for row in evidence if row.created_at <= visible_at]
        facts = list(session.scalars(select(FinancialFact).where(
            FinancialFact.instrument_id == instrument_id,
            FinancialFact.published_at.is_not(None),
            FinancialFact.published_at <= visible_at,
        ).order_by(
            FinancialFact.period_end.desc(), FinancialFact.published_at.desc(),
        ).limit(500)).all())
        latest_facts: dict[str, FinancialFact] = {}
        for row in facts:
            latest_facts.setdefault(row.metric_code, row)
        filings = self._filing_rows(facts)
        corporate_actions = list(session.scalars(select(CorporateAction).where(
            CorporateAction.instrument_id == instrument_id,
        ).order_by(CorporateAction.ex_date.desc()).limit(100)).all())
        corporate_actions = [
            row for row in corporate_actions
            if (row.announce_date or row.ex_date) is None or (row.announce_date or row.ex_date) <= as_of
        ]
        thesis_events = list(session.scalars(select(ThesisEvent).where(
            ThesisEvent.thesis_id == thesis.thesis_id,
            ThesisEvent.created_at <= visible_at,
        ).order_by(ThesisEvent.created_at.desc()).limit(50)).all()) if thesis else []
        thesis_reviews = list(session.scalars(select(ThesisReview).where(
            ThesisReview.thesis_id == thesis.thesis_id,
            ThesisReview.reviewed_at <= visible_at,
        ).order_by(ThesisReview.reviewed_at.desc()).limit(50)).all()) if thesis else []
        objective = self._objective_snapshot(latest_facts, self._latest_bar(bars))
        events = self._instrument_events(
            notes=notes,
            evidence=evidence,
            filings=filings,
            corporate_actions=corporate_actions,
            thesis_events=thesis_events,
            thesis_reviews=thesis_reviews,
        )
        return {
            "instrument": self._instrument_row(instrument),
            "market": {
                "latest": self._latest_bar(bars),
                "history": history,
                "provenance": self.market.provenance_view(
                    session, instrument_id, as_of=as_of, limit=5,
                ),
            },
            "valuation": {
                "valuation_run_id": str(valuation.valuation_run_id),
                "status": valuation.status,
                "as_of": valuation.as_of.isoformat(),
                "bear_value": _value(valuation.bear_value),
                "base_value": _value(valuation.base_value),
                "bull_value": _value(valuation.bull_value),
                "current_price": _value(valuation.current_price),
                "margin_of_safety": _value(valuation.margin_of_safety),
                "engine_version": valuation.engine_version,
            } if valuation else None,
            "objective_valuation": objective,
            "fundamentals": {
                "metrics": {
                    code: {
                        "value": _value(row.value),
                        "unit": row.unit,
                        "period_end": row.period_end.isoformat(),
                        "period_type": _value(row.period_type),
                        "published_at": _value(row.published_at),
                        "quality_status": _value(row.quality_status),
                        "provider": row.provider,
                        "provenance_id": str(row.provenance_id),
                    }
                    for code, row in latest_facts.items()
                },
                "filings": filings,
            },
            "etf_metrics": {
                "as_of": metric.as_of.isoformat(),
                "market_date": metric.market_date.isoformat(),
                "premium_discount": _value(metric.premium_discount),
                "valuation_band": metric.valuation_band,
                "index_pe": _value(metric.index_pe),
                "index_pb": _value(metric.index_pb),
                "quota_status": _value(metric.quota_status),
                "quality_status": _value(metric.quality_status),
                "quality_flags": metric.quality_flags or [],
                "engine_version": metric.engine_version,
            } if metric else None,
            "thesis": {
                "thesis_id": str(thesis.thesis_id),
                "lifecycle_status": thesis.lifecycle_status,
                "health_status": thesis.health_status,
                "conviction": thesis.conviction,
                "revision_id": str(revision.thesis_revision_id) if revision else None,
                "version": revision.version if revision else None,
                "summary": revision.summary if revision else None,
                "body": revision.thesis_body if revision else None,
            } if thesis else None,
            "events": events,
            "bootstrap": {
                "market": bool(bars),
                "fundamentals": bool(latest_facts) if str(instrument.instrument_type) == "CN_EQUITY" else None,
                "etf_metrics": bool(metric) if str(instrument.instrument_type) == "CN_ETF" else None,
                "filings": bool(filings) if str(instrument.instrument_type) == "CN_EQUITY" else None,
                "corporate_actions": len(corporate_actions),
                "thesis": bool(thesis),
                "ready": (
                    bool(bars)
                    and bool(thesis)
                    and (
                        bool(latest_facts)
                        if str(instrument.instrument_type) == "CN_EQUITY"
                        else bool(metric)
                    )
                ),
            },
            "freshness": BriefingService.from_settings().freshness_as_of(session, as_of),
        }

    @staticmethod
    def _filing_rows(facts: list[FinancialFact]) -> list[dict]:
        rows: list[dict] = []
        seen: set[str] = set()
        for fact in facts:
            key = fact.source_document_id or (
                f"{fact.period_end.isoformat()}@"
                f"{fact.published_at.isoformat() if fact.published_at else 'unknown'}"
            )
            if key in seen:
                continue
            seen.add(key)
            rows.append({
                "id": key,
                "title": f"{fact.period_end.year} 年 {str(_value(fact.period_type) or '报告期')} 财务披露",
                "period_end": fact.period_end.isoformat(),
                "period_type": _value(fact.period_type),
                "published_at": _value(fact.published_at),
                "provider": fact.provider,
                "source_document_id": fact.source_document_id,
                "provenance_id": str(fact.provenance_id),
            })
        return rows[:30]

    @staticmethod
    def _objective_snapshot(
        facts: dict[str, FinancialFact], latest: dict | None,
    ) -> dict | None:
        required = ("NET_INCOME", "SHARES_OUTSTANDING")
        missing = [code for code in required if code not in facts]
        close = (latest or {}).get("close")
        if close is None:
            missing.append("OHLCVA_CLOSE")
        if missing:
            return {"ready": False, "missing": missing}
        result = compute_objective(
            Decimal(str(close)),
            facts["NET_INCOME"].value,
            facts["SHARES_OUTSTANDING"].value,
            facts.get("TOTAL_EQUITY").value if facts.get("TOTAL_EQUITY") else None,
            period_type=_value(facts["NET_INCOME"].period_type),
            debt=facts.get("DEBT").value if facts.get("DEBT") else None,
            cash=facts.get("CASH").value if facts.get("CASH") else None,
            operating_income=(
                facts.get("OPERATING_INCOME").value if facts.get("OPERATING_INCOME") else None
            ),
            free_cash_flow=(
                facts.get("FREE_CASH_FLOW").value if facts.get("FREE_CASH_FLOW") else None
            ),
        )
        return {
            "ready": True,
            "as_of": (latest or {}).get("trade_date"),
            "financial_period": facts["NET_INCOME"].period_end.isoformat(),
            **_json_value(result),
        }

    @staticmethod
    def _instrument_events(
        *,
        notes: list,
        evidence: list,
        filings: list[dict],
        corporate_actions: list[CorporateAction],
        thesis_events: list[ThesisEvent],
        thesis_reviews: list[ThesisReview],
    ) -> list[dict]:
        events = [
            {
                "type": "note", "id": str(row.research_note_id), "title": row.title,
                "text": row.body_md, "at": row.created_at.isoformat(),
            }
            for row in notes
        ] + [
            {
                "type": "evidence", "id": str(row.evidence_id), "title": row.title,
                "text": row.claim, "direction": row.direction, "source_ref": row.source_ref,
                "at": row.created_at.isoformat(),
            }
            for row in evidence
        ] + [
            {
                "type": "filing", "id": row["id"], "title": row["title"],
                "text": f"报告期 {row['period_end']} · 来源 {row['provider']}",
                "source_ref": row.get("source_document_id"), "at": row["published_at"],
            }
            for row in filings
        ] + [
            {
                "type": "corporate_action", "id": str(row.corporate_action_id),
                "title": {
                    "DIVIDEND": "现金分红", "BONUS_SHARE": "送转股",
                    "SPLIT": "拆股", "RIGHTS_ISSUE": "配股",
                }.get(str(_value(row.action_type)), str(_value(row.action_type))),
                "text": _event_text(row.parameters),
                "at": _value(row.announce_date or row.ex_date),
            }
            for row in corporate_actions
        ] + [
            {
                "type": "thesis", "id": str(row.thesis_event_id),
                "title": f"投资观点 · {_value(row.event_type)}",
                "text": _event_text(row.event_data), "at": row.created_at.isoformat(),
            }
            for row in thesis_events
        ] + [
            {
                "type": "review", "id": str(row.review_id), "title": "投资观点复核",
                "text": row.notes or str(_value(row.conclusion)),
                "at": row.reviewed_at.isoformat(),
            }
            for row in thesis_reviews
        ]
        return sorted(events, key=lambda row: str(row.get("at") or ""), reverse=True)[:100]

    def portfolios(self, session: Session, as_of: date, portfolio_id: UUID | None = None) -> dict:
        rows = list(session.scalars(select(Portfolio).order_by(Portfolio.created_at.asc())).all())
        selected = next((row for row in rows if row.portfolio_id == portfolio_id), None)
        selected = selected or (rows[0] if rows else None)
        return {
            "as_of": as_of.isoformat(),
            "items": [{
                "portfolio_id": str(row.portfolio_id),
                "name": row.name,
                "mode": row.mode,
                "status": row.status,
            } for row in rows],
            "selected": self._portfolio_detail(session, selected, as_of) if selected else None,
            "freshness": BriefingService.from_settings().freshness_as_of(session, as_of),
        }

    def review(self, session: Session, as_of: date) -> dict:
        visible_at = _visible_at(as_of)
        theses = list(session.scalars(select(Thesis).where(
            Thesis.created_at <= visible_at,
        ).order_by(Thesis.updated_at.desc())).all())
        reviews = list(session.scalars(select(ThesisReview).where(
            ThesisReview.reviewed_at <= visible_at,
        ).order_by(ThesisReview.reviewed_at.desc()).limit(100)).all())
        notes = list(session.scalars(select(ResearchNote).where(
            ResearchNote.created_at <= visible_at,
        ).order_by(
            ResearchNote.created_at.desc()
        ).limit(100)).all())
        proposals = list(session.scalars(select(TradeProposal).where(
            TradeProposal.created_at <= visible_at,
        ).order_by(
            TradeProposal.created_at.desc()
        ).limit(100)).all())
        instruments = self._instruments(
            session,
            list({
                *[row.instrument_id for row in theses],
                *[row.instrument_id for row in proposals],
                *[row.instrument_id for row in notes if row.instrument_id],
            }),
        )
        return {
            "as_of": as_of.isoformat(),
            "theses": [{
                "thesis_id": str(row.thesis_id),
                "instrument": self._instrument_row(instruments[row.instrument_id])
                if row.instrument_id in instruments else None,
                "lifecycle_status": row.lifecycle_status,
                "health_status": row.health_status,
                "conviction": row.conviction,
                "updated_at": row.updated_at.isoformat(),
            } for row in theses],
            "reviews": [{
                "review_id": str(row.review_id),
                "thesis_id": str(row.thesis_id),
                "review_type": row.review_type,
                "conclusion": row.conclusion,
                "health_before": row.health_before,
                "health_after": row.health_after,
                "notes": row.notes,
                "reviewed_at": row.reviewed_at.isoformat(),
            } for row in reviews],
            "notes": [{
                "research_note_id": str(row.research_note_id),
                "instrument_id": str(row.instrument_id) if row.instrument_id else None,
                "title": row.title,
                "body_md": row.body_md,
                "created_by": row.created_by,
                "created_at": row.created_at.isoformat(),
            } for row in notes],
            "proposals": [self._proposal_row(row, instruments) for row in proposals],
        }

    def system_status(self, session: Session, as_of: date) -> dict:
        latest_context = session.scalar(select(DailyContext).where(
            DailyContext.market_date <= as_of,
        ).order_by(DailyContext.market_date.desc()).limit(1))
        latest_jobs = list(session.scalars(select(JobRun).order_by(
            JobRun.created_at.desc()
        ).limit(50)).all())
        # 系统健康度按每类任务的最近一次结果判断。旧失败仍保留在 jobs 中供审计，
        # 但后续成功后不能让首页永久处于故障状态。
        latest_by_name: dict[str, JobRun] = {}
        for row in latest_jobs:
            latest_by_name.setdefault(row.job_name, row)
        current_jobs = list(latest_by_name.values())
        failed = [row for row in current_jobs if row.status == "FAILED"]
        pending = [row for row in current_jobs if row.status in {"PENDING", "RUNNING"}]
        return {
            "backend": "OK",
            "scheduler_enabled": settings.scheduler_enabled,
            "scheduler_time": f"{settings.scheduler_hour:02d}:{settings.scheduler_minute:02d}",
            "scheduler_timezone": settings.scheduler_timezone,
            "daily_context_date": latest_context.market_date.isoformat() if latest_context else None,
            "freshness": latest_context.freshness_status if latest_context else "FAILED",
            "failed_jobs": len(failed),
            "pending_jobs": len(pending),
            "jobs": [{
                "job_run_id": str(row.job_run_id),
                "name": row.job_name,
                "status": row.status,
                "started_at": _value(row.started_at),
                "finished_at": _value(row.finished_at),
                "error": row.error,
            } for row in latest_jobs[:12]],
        }

    def _default_portfolio(self, session: Session) -> Portfolio | None:
        return session.scalar(select(Portfolio).where(
            Portfolio.mode == PortfolioMode.REAL.value,
            Portfolio.status == "ACTIVE",
        ).order_by(Portfolio.created_at.asc()).limit(1)) or session.scalar(
            select(Portfolio).where(Portfolio.status == "ACTIVE")
            .order_by(Portfolio.created_at.asc()).limit(1)
        )

    def _portfolio_summary(
        self, session: Session, portfolio: Portfolio, as_of: date,
    ) -> dict:
        snapshots = list(session.scalars(select(PortfolioSnapshot).where(
            PortfolioSnapshot.portfolio_id == portfolio.portfolio_id,
            PortfolioSnapshot.snapshot_date <= as_of,
        ).order_by(PortfolioSnapshot.snapshot_date.asc())).all())
        latest = snapshots[-1] if snapshots else None
        previous = snapshots[-2] if len(snapshots) > 1 else None
        daily_flow = Decimal("0")
        if latest and previous:
            flows = session.scalars(select(PortfolioTransaction).where(
                PortfolioTransaction.portfolio_id == portfolio.portfolio_id,
                PortfolioTransaction.trade_date > previous.snapshot_date,
                PortfolioTransaction.trade_date <= latest.snapshot_date,
                PortfolioTransaction.transaction_type.in_(["CASH_IN", "CASH_OUT"]),
            )).all()
            daily_flow = sum((row.amount_cny for row in flows), Decimal("0"))
        daily_pnl = (
            latest.nav_cny - previous.nav_cny - daily_flow if latest and previous else None
        )
        daily_return = (
            daily_pnl / (previous.nav_cny + daily_flow)
            if daily_pnl is not None and previous and previous.nav_cny + daily_flow > 0 else None
        )
        ytd = [row for row in snapshots if row.snapshot_date.year == as_of.year]
        periods = []
        for left, right in zip(ytd, ytd[1:], strict=False):
            flow = session.scalar(select(func.coalesce(func.sum(PortfolioTransaction.amount_cny), 0)).where(
                PortfolioTransaction.portfolio_id == portfolio.portfolio_id,
                PortfolioTransaction.trade_date > left.snapshot_date,
                PortfolioTransaction.trade_date <= right.snapshot_date,
                PortfolioTransaction.transaction_type.in_(["CASH_IN", "CASH_OUT"]),
            )) or Decimal("0")
            periods.append({"start_nav": left.nav_cny, "end_nav": right.nav_cny, "external_flow": flow})
        try:
            ytd_return = compute_twr(periods) if periods else None
        except ValueError:
            ytd_return = None
        nav_returns = []
        for left, right in zip(snapshots, snapshots[1:], strict=False):
            flow = session.scalar(select(func.coalesce(func.sum(PortfolioTransaction.amount_cny), 0)).where(
                PortfolioTransaction.portfolio_id == portfolio.portfolio_id,
                PortfolioTransaction.trade_date > left.snapshot_date,
                PortfolioTransaction.trade_date <= right.snapshot_date,
                PortfolioTransaction.transaction_type.in_(["CASH_IN", "CASH_OUT"]),
            )) or Decimal("0")
            denominator = left.nav_cny + flow
            if denominator > 0:
                nav_returns.append(float(right.nav_cny / denominator - 1))
        volatility = stdev(nav_returns) * sqrt(252) if len(nav_returns) >= 20 else None
        risk = latest.risk_summary if latest else None
        metrics = (risk or {}).get("metrics", {})
        drawdown = metrics.get("portfolio_drawdown", {}).get("max_drawdown")
        allocation = metrics.get("asset_class_exposure") or (
            (latest.exposures or {}).get("weights", {}) if latest else {}
        )
        risk_levels: dict[str, int] = {}
        for metric in metrics.values():
            if isinstance(metric, dict) and metric.get("level"):
                level = str(metric["level"])
                risk_levels[level] = risk_levels.get(level, 0) + 1
        risk_level_total = sum(risk_levels.values())
        risk_distribution = {
            key: Decimal(value) / Decimal(risk_level_total)
            for key, value in risk_levels.items()
        } if risk_level_total else {}
        unrealized_pnl = None
        if latest:
            unrealized_pnl = session.scalar(select(func.coalesce(
                func.sum(PositionSnapshot.unrealized_pnl_cny), 0,
            )).where(
                PositionSnapshot.portfolio_id == portfolio.portfolio_id,
                PositionSnapshot.snapshot_date == latest.snapshot_date,
            ))
        return {
            "portfolio_id": str(portfolio.portfolio_id),
            "name": portfolio.name,
            "mode": portfolio.mode,
            "snapshot_date": latest.snapshot_date.isoformat() if latest else None,
            "nav_cny": _value(latest.nav_cny) if latest else None,
            "cash_cny": _value(latest.cash_cny) if latest else None,
            "market_value_cny": _value(latest.market_value_cny) if latest else None,
            "cash_ratio": _value(
                latest.cash_cny / latest.nav_cny if latest and latest.nav_cny > 0 else None
            ),
            "daily_pnl_cny": _value(daily_pnl),
            "daily_return": _value(daily_return),
            "ytd_return": _value(ytd_return),
            "annualized_volatility": _value(Decimal(str(volatility)) if volatility is not None else None),
            "max_drawdown": _value(drawdown),
            "unrealized_pnl_cny": _value(unrealized_pnl),
            "allocation": {str(key): _value(value) for key, value in allocation.items()},
            "risk_distribution": {
                str(key): _value(value) for key, value in risk_distribution.items()
            },
            "risk": risk,
            "history": [{
                "date": row.snapshot_date.isoformat(),
                "nav_cny": _value(row.nav_cny),
                "cash_cny": _value(row.cash_cny),
                "market_value_cny": _value(row.market_value_cny),
            } for row in snapshots],
        }

    def _portfolio_detail(self, session: Session, portfolio: Portfolio, as_of: date) -> dict:
        summary = self._portfolio_summary(session, portfolio, as_of)
        latest_date = date.fromisoformat(summary["snapshot_date"]) if summary["snapshot_date"] else None
        positions = list(session.scalars(select(PositionSnapshot).where(
            PositionSnapshot.portfolio_id == portfolio.portfolio_id,
            PositionSnapshot.snapshot_date == latest_date,
        ).order_by(PositionSnapshot.market_value_cny.desc())).all()) if latest_date else []
        transactions = list(session.scalars(select(PortfolioTransaction).where(
            PortfolioTransaction.portfolio_id == portfolio.portfolio_id,
            PortfolioTransaction.trade_date <= as_of,
        ).order_by(
            PortfolioTransaction.trade_date.desc(), PortfolioTransaction.created_at.desc(),
        ).limit(300)).all())
        proposals = list(session.scalars(select(TradeProposal).where(
            TradeProposal.portfolio_id == portfolio.portfolio_id,
            TradeProposal.created_at <= _visible_at(as_of),
        ).order_by(TradeProposal.created_at.desc()).limit(100)).all())
        instruments = self._instruments(
            session,
            list({
                *[row.instrument_id for row in positions],
                *[row.instrument_id for row in transactions if row.instrument_id],
                *[row.instrument_id for row in proposals],
            }),
        )
        summary["positions"] = [{
            "instrument": self._instrument_row(instruments[row.instrument_id])
            if row.instrument_id in instruments else {"instrument_id": str(row.instrument_id)},
            "quantity": _value(row.quantity),
            "cost_basis_cny": _value(row.cost_basis_cny),
            "average_cost_cny": _value(
                row.cost_basis_cny / row.quantity if row.quantity else None
            ),
            "market_price_cny": _value(row.market_price_cny),
            "market_value_cny": _value(row.market_value_cny),
            "realized_pnl_cny": _value(row.realized_pnl_cny),
            "unrealized_pnl_cny": _value(row.unrealized_pnl_cny),
        } for row in positions]
        summary["transactions"] = [{
            "transaction_id": str(row.transaction_id),
            "instrument": self._instrument_row(instruments[row.instrument_id])
            if row.instrument_id in instruments else None,
            "transaction_type": row.transaction_type,
            "quantity": _value(row.quantity),
            "price_cny": _value(row.price_cny),
            "amount_cny": _value(row.amount_cny),
            "fees_cny": _value(row.fees_cny),
            "trade_date": row.trade_date.isoformat(),
            "note": row.note,
            "reverses_transaction_id": _value(row.reverses_transaction_id),
        } for row in transactions]
        summary["proposals"] = [self._proposal_row(row, instruments) for row in proposals]
        return summary

    def _watchlist(
        self, session: Session, as_of: date, *, history_days: int = 45,
    ) -> dict | None:
        watchlist = session.scalar(select(Watchlist).where(
            Watchlist.status == "ACTIVE",
        ).order_by(Watchlist.created_at.asc()).limit(1))
        if watchlist is None:
            return None
        members = list(session.scalars(select(WatchlistMember).where(
            WatchlistMember.watchlist_id == watchlist.watchlist_id,
            WatchlistMember.removed_at.is_(None),
        ).order_by(WatchlistMember.added_at.asc())).all())
        instruments = self._instruments(session, [row.instrument_id for row in members])
        profiles = {
            row.instrument_id: row
            for row in session.scalars(select(ETFProfile).where(
                ETFProfile.instrument_id.in_([item.instrument_id for item in members])
            )).all()
        } if members else {}
        rows = []
        for member in members:
            instrument = instruments.get(member.instrument_id)
            if instrument is None:
                continue
            bars = self.market.get_ohlcva(
                session,
                instrument.instrument_id,
                start=as_of - timedelta(days=history_days),
                end=as_of,
                as_of=as_of,
            )
            latest = self._latest_bar(bars)
            profile = profiles.get(instrument.instrument_id)
            rows.append({
                **self._instrument_row(instrument),
                "tracking_index": profile.tracking_index_name if profile else None,
                "is_qdii": bool(profile.is_qdii) if profile else False,
                "latest": latest,
                "history": self._market_history(bars) if history_days > 60 else [],
                "note": member.note,
                "added_at": member.added_at.isoformat(),
            })
        return {
            "watchlist_id": str(watchlist.watchlist_id),
            "name": watchlist.name,
            "description": watchlist.description,
            "items": rows,
        }

    def _attention(
        self,
        session: Session,
        context: DailyContext | None,
        portfolio: Portfolio | None,
        as_of: date,
    ) -> list[dict]:
        items: list[dict] = []
        if context:
            rows = list(session.scalars(select(AttentionItem).where(
                AttentionItem.daily_context_id == context.daily_context_id,
                AttentionItem.is_processed.is_(False),
            ).order_by(AttentionItem.created_at.desc())).all())
            items.extend({
                "type": "market",
                "severity": row.severity or "INFO",
                "title": row.rule_name,
                "detail": row.detail or {},
                "date": context.market_date.isoformat(),
            } for row in rows)
        freshness = BriefingService.from_settings().freshness_as_of(session, as_of)
        if freshness["overall"] != "OK":
            items.insert(0, {
                "type": "system",
                "severity": freshness["overall"],
                "title": "数据新鲜度需要处理",
                "detail": {
                    "message": "决策敏感写入已关闭，请先检查失败或过期的数据域。",
                    "domains": freshness["domains"],
                },
                "date": as_of.isoformat(),
            })
        if portfolio:
            proposals = list(session.scalars(select(TradeProposal).where(
                TradeProposal.portfolio_id == portfolio.portfolio_id,
                TradeProposal.status.in_(["PROPOSED", "APPROVED"]),
            ).order_by(TradeProposal.created_at.desc())).all())
            items.extend({
                "type": "proposal",
                "severity": "INFO",
                "title": "有一条交易建议待人工处理",
                "detail": {
                    "trade_proposal_id": str(row.trade_proposal_id),
                    "proposal_type": row.proposal_type,
                    "quantity": _value(row.quantity),
                    "status": row.status,
                },
                "date": row.created_at.date().isoformat(),
            } for row in proposals)
        return items

    @staticmethod
    def _today_points(*, freshness: dict, watchlist: dict | None,
                      portfolio: dict | None, attention: list[dict]) -> list[str]:
        points = [f"数据新鲜度：{freshness['overall']}。"]
        market_items = (watchlist or {}).get("items", [])
        changes = [
            _as_float((item.get("latest") or {}).get("pct_change")) for item in market_items
        ]
        changes = [value for value in changes if value is not None]
        if changes:
            points.append(
                f"观察池 {len(changes)} 个标的有最新行情，"
                f"上涨 {sum(value > 0 for value in changes)} 个、下跌 {sum(value < 0 for value in changes)} 个。"
            )
        else:
            points.append("观察池尚无可用行情；界面不会用示例数据代替。")
        if portfolio and portfolio.get("nav_cny") is not None:
            points.append(
                f"组合当前净值 ¥{float(portfolio['nav_cny']):,.2f}，"
                f"现金占比 {float(portfolio.get('cash_ratio') or 0):.2%}。"
            )
        else:
            points.append("尚未录入期初持仓和现金，可在“持仓”页开始建账。")
        points.append(f"当前有 {len(attention)} 条关注事项。")
        return points

    @staticmethod
    def _instruments(session: Session, ids: list[UUID]) -> dict[UUID, Instrument]:
        if not ids:
            return {}
        return {
            row.instrument_id: row
            for row in session.scalars(select(Instrument).where(
                Instrument.instrument_id.in_(ids)
            )).all()
        }

    @staticmethod
    def _instrument_row(row: Instrument) -> dict:
        return {
            "instrument_id": str(row.instrument_id),
            "symbol": _symbol(row),
            "name": row.name,
            "instrument_type": row.instrument_type,
            "market": row.market,
            "currency": row.currency,
            "status": row.status,
        }

    @staticmethod
    def _bar(row: dict) -> dict:
        return {
            "trade_date": _value(row.get("trade_date")),
            "open": _value(row.get("open")),
            "high": _value(row.get("high")),
            "low": _value(row.get("low")),
            "close": _value(row.get("close")),
            "pct_change": _value(row.get("pct_change")),
            "volume": _value(row.get("volume")),
            "amount": _value(row.get("amount")),
            "provider": row.get("provider"),
        }

    @classmethod
    def _market_history(cls, bars: list[dict]) -> list[dict]:
        closes: list[float | None] = []
        history = []
        for bar in bars:
            closes.append(_as_float(bar.get("close")))
            row = cls._bar(bar)
            for window in (5, 20, 30):
                values = [value for value in closes[-window:] if value is not None]
                row[f"ma{window}"] = sum(values) / window if len(values) == window else None
            history.append(row)
        return history

    def _latest_bar(self, bars: list[dict]) -> dict | None:
        if not bars:
            return None
        latest = self._bar(bars[-1])
        if latest.get("pct_change") is None and len(bars) >= 2:
            previous = _as_float(bars[-2].get("close"))
            current = _as_float(bars[-1].get("close"))
            if previous and current is not None:
                latest["pct_change"] = str((current / previous - 1) * 100)
        return latest

    def _proposal_row(
        self, row: TradeProposal, instruments: dict[UUID, Instrument],
    ) -> dict:
        return {
            "trade_proposal_id": str(row.trade_proposal_id),
            "portfolio_id": str(row.portfolio_id),
            "instrument": self._instrument_row(instruments[row.instrument_id])
            if row.instrument_id in instruments else {"instrument_id": str(row.instrument_id)},
            "proposal_type": row.proposal_type,
            "quantity": _value(row.quantity),
            "limit_price_cny": _value(row.limit_price_cny),
            "target_weight": _value(row.target_weight),
            "status": row.status,
            "rationale": row.rationale,
            "created_by": row.created_by,
            "approved_at": _value(row.approved_at),
            "created_at": row.created_at.isoformat(),
        }
