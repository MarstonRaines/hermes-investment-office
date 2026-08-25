# =====================================================================
# backend/app/mcp/server.py —— MCP Server（TS-07，冻结）
#
# - 传输：StreamableHTTP（FastAPI 内嵌 /mcp，冻结规范 §31.1）；
# - 工具白名单：M1.5 实现子集 ⊆ ts07 冻结核心 28 工具；ADR-006 业务扩展单独登记；
# - 统一响应包络（ts01 五要素：request_id / as_of / data / quality / provenance）；
# - 业务错误进包络 error（{code, message, field}），协议层错误（未知工具等）
#   由 MCP SDK 处理（-32601）；
# - v0.1 localhost 信任边界（ADR-004 D3：认证远程化时激活）。
# =====================================================================
from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import UTC, date, datetime, time
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

from mcp.server.mcpserver import MCPServer

from app.briefing.service import BriefingService
from app.common.enums import (
    DataQualityStatus,
    ProposalType,
    ReviewConclusion,
    ReviewType,
    ThesisHealthStatus,
)
from app.common.freshness import FreshnessGateError, freshness_payload
from app.etf.service import ETFDataService
from app.fundamentals.service import FROZEN_METRIC_CODES, FundamentalsService
from app.instruments.service import (
    InstrumentNotFoundError,
    InstrumentService,
    WatchlistArchivedError,
    WatchlistMemberNotFoundError,
    WatchlistNotFoundError,
    WatchlistPermissionError,
    WatchlistService,
)
from app.jobs.service import JobService
from app.market_data.service import MarketDataService
from app.portfolio.service import PortfolioDomainError, PortfolioService
from app.research.service import ResearchDomainError, ResearchService
from app.risk.service import RiskService
from app.thesis.service import (
    InvalidThesisTransitionError,
    RevisionConflictError,
    ThesisDomainError,
    ThesisService,
)
from app.valuation.errors import ValuationError
from app.valuation.schemas import ValuationAssumptionInput
from app.valuation.service import ValuationRequest, ValuationService

logger = logging.getLogger(__name__)

__all__ = [
    "FROZEN_MCP_TOOLS",
    "M1_5_MCP_TOOLS",
    "M3_MCP_TOOLS",
    "ADR006_MCP_TOOLS",
    "MCP_ALLOWED_TOOLS",
    "MCP_TOOL_PERMISSIONS",
    "envelope",
    "MCPDomainError",
    "build_mcp_server",
]

# ts07 §2 冻结 28 工具白名单（八组；M5 全量实现，M1.5 实现子集）。
# ADR-006 的观察池工具保持为单独的业务扩展集合，避免改变 TS-07 核心
# 白名单的数量/语义；它们也不暴露任何原始 Provider 便利工具。
FROZEN_MCP_TOOLS = frozenset({
    # Market（5）
    "resolve_instrument", "get_market_snapshot", "get_price_history",
    "get_market_metrics", "sync_market_data",
    # Fundamental（4）
    "get_fundamentals", "get_financial_history", "get_latest_filings",
    "sync_fundamentals",
    # Valuation（3）
    "run_valuation", "get_latest_valuation", "get_valuation_history",
    # Portfolio（5）
    "get_portfolio", "get_positions", "get_portfolio_exposure",
    "get_portfolio_risk", "create_trade_proposal",
    # Research（4）
    "get_research_context", "save_research_note", "get_evidence", "search_research",
    # Thesis（4）
    "get_thesis", "create_thesis_revision", "record_thesis_review",
    "update_thesis_assumption",
    # Briefing（2）
    "get_daily_context", "save_daily_brief",
    # Job（1）
    "get_job_status",
})

# M1.5 Vertical Slice 实现子集（ACC-M1.5-002：链路打通所需最小工具）
M1_5_MCP_TOOLS = frozenset({
    "resolve_instrument",
    "get_market_snapshot",
    "sync_market_data",
    "get_job_status",
    "run_valuation",
    "get_thesis",
    "get_daily_context",
    "save_daily_brief",
})

M3_MCP_TOOLS = frozenset({"get_market_metrics"})
ADR006_MCP_TOOLS = frozenset({
    "get_watchlist", "add_watchlist_member", "remove_watchlist_member",
})
MCP_ALLOWED_TOOLS = FROZEN_MCP_TOOLS | ADR006_MCP_TOOLS

MCP_TOOL_PERMISSIONS = {
    **{name: "READ" for name in FROZEN_MCP_TOOLS if name not in {
        "save_research_note", "create_thesis_revision", "record_thesis_review",
        "update_thesis_assumption", "save_daily_brief", "create_trade_proposal",
    }},
    "save_research_note": "RESEARCH_WRITE",
    "create_thesis_revision": "RESEARCH_WRITE",
    "record_thesis_review": "RESEARCH_WRITE",
    "update_thesis_assumption": "RESEARCH_WRITE",
    "save_daily_brief": "RESEARCH_WRITE",
    "create_trade_proposal": "PROPOSAL_WRITE",
    "get_watchlist": "READ",
    "add_watchlist_member": "RESEARCH_WRITE",
    "remove_watchlist_member": "RESEARCH_WRITE",
}


class MCPDomainError(Exception):
    """业务错误：进包络 error（ts07 §4.1），不抛到协议层。"""

    def __init__(self, code: str, message: str, field: str | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.field = field


def _parse_as_of_datetime(value: str | None, *, default: datetime | None = None) -> datetime:
    if not value:
        return default or datetime.now(UTC)
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    if "T" not in value and " " not in value:
        parsed = datetime.combine(parsed.date(), time.max, tzinfo=parsed.tzinfo)
    return parsed


def _parse_as_of_date(value: str | None, *, default: date | None = None) -> date:
    return _parse_as_of_datetime(value, default=datetime.combine(
        default or date.today(), time.max, tzinfo=UTC,
    )).date()


def _adjust_price_bars(bars: list[dict], adjust: str) -> list[dict]:
    """Select the frozen adjustment semantics without mutating market storage."""
    if adjust == "none":
        return bars
    factors = [Decimal(str(row["adj_factor"])) for row in bars if row.get("adj_factor") is not None]
    latest_factor = factors[-1] if factors else Decimal("1")
    adjusted: list[dict] = []
    for row in bars:
        item = dict(row)
        raw = item.get("close")
        if raw is None:
            adjusted.append(item)
            continue
        raw_decimal = Decimal(str(raw))
        factor = Decimal(str(item.get("adj_factor"))) if item.get("adj_factor") is not None else Decimal("1")
        item["raw_close"] = raw
        if adjust == "hfq":
            selected = item.get("adjusted_close")
            selected = selected if selected is not None else raw_decimal * factor
        else:
            selected = raw_decimal * factor / latest_factor
        item["close"] = float(selected)
        adjusted.append(item)
    return adjusted


def envelope(
    data=None,
    *,
    quality_status: DataQualityStatus = DataQualityStatus.VERIFIED,
    quality_score: Decimal = Decimal("1.0"),
    quality_flags: list[str] | None = None,
    provenance: list | None = None,
    as_of: datetime | None = None,
    freshness: dict | None = None,
    error: dict | None = None,
) -> dict:
    """统一响应包络（ts01 冻结五要素；业务错误时 data 缺省、error 携带）。"""
    env = {
        "request_id": str(uuid4()),
        "as_of": (as_of or datetime.now(UTC)).isoformat(),
    }
    if error is not None:
        env["error"] = error
        return env
    env["data"] = data
    env["quality"] = {
        "status": quality_status.value,
        "score": str(quality_score),
        "flags": quality_flags or [],
    }
    env["provenance"] = provenance or []
    env["freshness"] = freshness_payload(freshness or {})
    return env


def _to_error(exc: Exception) -> dict:
    if isinstance(exc, MCPDomainError):
        out = {"code": exc.code, "message": exc.message}
        if exc.field:
            out["field"] = exc.field
        return out
    if isinstance(exc, (WatchlistPermissionError, WatchlistArchivedError)):
        return {"code": "PERMISSION_DENIED", "message": str(exc)}
    if isinstance(exc, (WatchlistNotFoundError, WatchlistMemberNotFoundError, InstrumentNotFoundError)):
        return {"code": "NOT_FOUND", "message": str(exc)}
    if isinstance(exc, FreshnessGateError):
        return {"code": exc.code, "message": "当前数据新鲜度不是 OK，禁止该决策写入"}
    if isinstance(exc, (PortfolioDomainError, ResearchDomainError)):
        return {"code": getattr(exc, "code", "DOMAIN_CONFLICT"), "message": str(exc)}
    if isinstance(exc, (ThesisDomainError, InvalidThesisTransitionError, RevisionConflictError)):
        return {"code": getattr(exc, "code", "DOMAIN_CONFLICT"), "message": str(exc)}
    if isinstance(exc, ValuationError):
        return {"code": exc.code, "message": str(exc)}
    return {"code": "ENGINE_INTERNAL_ERROR", "message": "内部引擎错误"}


def _metric_item(snapshot) -> dict:
    details = snapshot.details or {}
    levels = {
        name: _public_level_metadata(details.get(name))
        for name in ("level_0", "level_1", "level_2")
    }
    return {
        "instrument_id": str(snapshot.instrument_id),
        "instrument_type": "CN_ETF",
        "source": details.get("source") or "etf_metrics",
        "market_date": snapshot.market_date.isoformat(),
        "market_price_cny": (
            str(snapshot.market_price_cny)
            if snapshot.market_price_cny is not None else None
        ),
        "is_qdii": snapshot.is_qdii,
        "underlying_index_id": (
            str(snapshot.underlying_index_id)
            if snapshot.underlying_index_id else None
        ),
        "premium_discount": (
            str(snapshot.premium_discount)
            if snapshot.premium_discount is not None else None
        ),
        "fx_contribution": (
            str(snapshot.fx_contribution)
            if snapshot.fx_contribution is not None else None
        ),
        "r_usd": details.get("r_usd"),
        "fx_chg": details.get("fx_chg"),
        "r_cny": details.get("r_cny"),
        "quota_status": str(getattr(snapshot.quota_status, "value", snapshot.quota_status)),
        "net_value_t1": (
            str(snapshot.net_value_t1)
            if snapshot.net_value_t1 is not None else None
        ),
        "nav_date": snapshot.nav_date.isoformat() if snapshot.nav_date else None,
        "underlying_session_date": (
            snapshot.underlying_session_date.isoformat()
            if snapshot.underlying_session_date else None
        ),
        "fx_as_of": snapshot.fx_as_of.isoformat() if snapshot.fx_as_of else None,
        "freshness": details.get("freshness"),
        "data_freshness": details.get("data_freshness"),
        "levels": levels,
        "index_pe": str(snapshot.index_pe) if snapshot.index_pe is not None else None,
        "index_pb": str(snapshot.index_pb) if snapshot.index_pb is not None else None,
        "valuation_band": snapshot.valuation_band,
        "reference_nav_basis": snapshot.reference_nav_basis,
        "engine_version": snapshot.engine_version,
        "input_hash": snapshot.input_hash,
        "provenance_id": str(snapshot.provenance_id),
        "quality_flags": snapshot.quality_flags,
    }


def _public_level_metadata(value: object) -> dict | None:
    if not isinstance(value, dict):
        return None
    allowed = {"as_of_date", "status", "is_estimate", "source", "completeness", "confidence", "description"}
    return {key: value[key] for key in allowed if key in value}


def _valuation_public(run) -> dict:
    return {
        "valuation_run_id": str(run.valuation_run_id),
        "instrument_id": str(run.instrument_id),
        "model_type": str(getattr(run.model_type, "value", run.model_type)),
        "status": str(getattr(run.status, "value", run.status)),
        "as_of": run.as_of.isoformat(), "engine_version": run.engine_version,
        "input_snapshot_hash": run.input_snapshot_hash,
        "bear_value": str(run.bear_value) if run.bear_value is not None else None,
        "base_value": str(run.base_value) if run.base_value is not None else None,
        "bull_value": str(run.bull_value) if run.bull_value is not None else None,
        "current_price": str(run.current_price) if run.current_price is not None else None,
        "margin_of_safety": str(run.margin_of_safety) if run.margin_of_safety is not None else None,
        "result": run.result_json,
        "provenance_id": str(run.provenance_id) if run.provenance_id else None,
    }


def _worst_quality(statuses: list[DataQualityStatus]) -> DataQualityStatus:
    if not statuses:
        return DataQualityStatus.REJECTED
    statuses = [
        DataQualityStatus(getattr(value, "value", value))
        for value in statuses
    ]
    rank = {
        DataQualityStatus.VERIFIED: 0,
        DataQualityStatus.ACCEPTABLE: 1,
        DataQualityStatus.STALE: 2,
        DataQualityStatus.CONFLICT: 3,
        DataQualityStatus.REJECTED: 4,
    }
    return max(statuses, key=lambda value: rank[value])


def _freshness_for(briefing_service: BriefingService, session: Any, market_date: date) -> dict:
    """Keep minimal offline service stubs compatible without weakening DB paths."""
    try:
        return briefing_service.freshness_as_of(session, market_date)
    except AttributeError:
        return {}


def _market_provenance(market_service: MarketDataService, session: Any, instrument_id: UUID, **kwargs) -> list[dict]:
    reader = getattr(market_service, "provenance_view", None)
    if reader is None:
        return []
    try:
        return reader(session, instrument_id, **kwargs)
    except AttributeError:
        return []


def _resolve_sync_universe(session: Any, universe: list[str], end_date: str) -> list[UUID]:
    if len(universe) == 1 and universe[0].upper() in {"ALL", "WATCHLIST"}:
        watchlist = WatchlistService(session).get_default()
        if watchlist is None:
            return []
        return sorted(
            WatchlistService(session).daily_universe_for_date(
                watchlist.watchlist_id, date.fromisoformat(end_date)
            ),
            key=str,
        )
    try:
        return [UUID(value) for value in universe]
    except (ValueError, TypeError) as exc:
        raise MCPDomainError("INVALID_ARGUMENT", "universe 必须是 UUID 列表或 WATCHLIST/ALL", "universe") from exc


class _UnavailableETFService:
    """Keep the frozen MCP surface stable in minimal/offline test assembly."""

    def read_metric(self, session, instrument_id, *, as_of):
        return None

    def read_metric_provenance(self, session, snapshot):
        return []


def build_mcp_server(
    session_factory: Callable[[], Any],
    *,
    parquet_store: Any,
    market_service: MarketDataService,
    valuation_service: ValuationService,
    thesis_service: ThesisService,
    briefing_service: BriefingService,
    sync_runner: Any,
    etf_service: ETFDataService | None = None,
) -> MCPServer:
    server = MCPServer(name="hermes-backend", version="0.1.0",
                       description="Hermes Investment Office Backend（MCP 契约 TS-07）")

    if etf_service is None:
        etf_service = _UnavailableETFService()
    fundamentals_service = FundamentalsService()
    job_service = JobService()
    portfolio_service = PortfolioService()
    risk_service = RiskService()

    # ---- Market ----

    @server.tool(name="resolve_instrument", description="符号/名称 → 稳定 instrument_id（唯一身份入口）")
    def resolve_instrument(
        symbol: str | None = None,
        name: str | None = None,
        provider_symbol: str | None = None,
        provider: str | None = None,
        instrument_type: str | None = None,
        market: str | None = None,
        limit: int = 10,
    ) -> dict:
        try:
            if not any([symbol, name, provider_symbol]):
                raise MCPDomainError("INVALID_ARGUMENT", "query 至少给一个（symbol/name/provider_symbol）")
            session = session_factory()
            try:
                rows = InstrumentService(session).search(
                    symbol=symbol, name=name, provider_symbol=provider_symbol,
                    provider=provider, instrument_type=instrument_type, market=market, limit=limit,
                )
                # provider_symbol 只用于消歧，输出不回显 provider 字段（ts01 §1.6）
                matches = [{
                    "instrument_id": str(r.instrument_id),
                    "instrument_type": r.instrument_type,
                    "name": r.name,
                    "symbol": r.symbol,
                    "market": r.market,
                    "status": r.status,
                } for r in rows]
                if not matches:
                    raise MCPDomainError("NOT_FOUND", "无匹配标的")
                return envelope({"matches": matches, "total": len(matches), "next_offset": None})
            finally:
                session.close()
        except Exception as exc:  # noqa: BLE001
            return envelope(error=_to_error(exc))

    @server.tool(name="get_market_snapshot", description="as_of 前最近已完成交易日行情（OHLCVA）")
    def get_market_snapshot(instrument_ids: list[str], as_of: str | None = None) -> dict:
        try:
            as_of_date = _parse_as_of_date(as_of)
            session = session_factory()
            try:
                rows = []
                provenance = []
                latest_as_of = as_of_date
                for iid in instrument_ids:
                    bars = market_service.get_ohlcva(session, UUID(iid), as_of=as_of_date)
                    if not bars:
                        rows.append({"instrument_id": iid, "trade_date": None, "close": None})
                        continue
                    last = bars[-1]
                    latest_as_of = min(latest_as_of, last["trade_date"])
                    provenance.extend(_market_provenance(
                        market_service,
                        session, UUID(iid), as_of=as_of_date, limit=1,
                    ))
                    rows.append({
                        "instrument_id": iid,
                        "trade_date": last["trade_date"].isoformat(),
                        "close": last["close"],
                        "pct_change": last["pct_change"],
                        "volume": last["volume"],
                        "amount": last["amount"],
                        "provider": last["provider"],
                    })
                return envelope({"snapshots": rows}, provenance=provenance,
                                freshness=_freshness_for(briefing_service, session, as_of_date),
                                as_of=datetime.combine(latest_as_of, time.max, tzinfo=UTC))
            finally:
                session.close()
        except Exception as exc:  # noqa: BLE001
            return envelope(error=_to_error(exc))

    @server.tool(name="get_price_history", description="PIT 行情历史序列（PG pointer → Parquet）")
    def get_price_history(
        instrument_ids: list[str], start_date: str, end_date: str,
        as_of: str | None = None, adjust: str = "none",
    ) -> dict:
        try:
            if adjust not in {"none", "qfq", "hfq"}:
                raise MCPDomainError("INVALID_ARGUMENT", "adjust 必须是 none/qfq/hfq", "adjust")
            start = date.fromisoformat(start_date)
            end = date.fromisoformat(end_date)
            if start > end:
                raise MCPDomainError("INVALID_ARGUMENT", "start_date 不能晚于 end_date", "start_date")
            pit = date.fromisoformat(as_of[:10]) if as_of else end
            session = session_factory()
            try:
                series = []
                provenance = []
                for raw_id in instrument_ids:
                    bars = market_service.get_ohlcva(session, UUID(raw_id), start=start, end=end, as_of=pit)
                    series.append({"instrument_id": raw_id, "bars": _adjust_price_bars(bars, adjust), "adjust": adjust})
                    provenance.extend(_market_provenance(
                        market_service,
                        session, UUID(raw_id), start=start, end=end, as_of=pit,
                    ))
                return envelope({"series": series, "start_date": start.isoformat(), "end_date": end.isoformat()},
                                provenance=list({ref["provenance_id"]: ref for ref in provenance}.values()),
                                freshness=_freshness_for(briefing_service, session, pit),
                                as_of=datetime.combine(pit, time.max, tzinfo=UTC))
            finally:
                session.close()
        except Exception as exc:  # noqa: BLE001
            return envelope(error=_to_error(exc))

    @server.tool(name="sync_market_data", description="幂等触发 market/ETF/macro 同步 job；只入队")
    def sync_market_data(
        universe: list[str] | str,
        start_date: str,
        end_date: str,
        data_type: str = "OHLCV",
        sync_kind: str = "market",
    ) -> dict:
        try:
            if sync_kind not in {"market", "etf", "macro"}:
                raise MCPDomainError("INVALID_ARGUMENT", "sync_kind 必须是 market/etf/macro", "sync_kind")
            if isinstance(universe, str):
                raw_universe = [universe]
            else:
                raw_universe = universe
            session = session_factory()
            try:
                resolved = _resolve_sync_universe(session, raw_universe, end_date)
                job_name = f"{sync_kind}_sync_job"
                job, created = sync_runner.create_sync_job(session, job_name, {
                    "universe": [str(value) for value in resolved],
                    "start_date": start_date,
                    "end_date": end_date,
                    "data_type": data_type,
                })
                session.commit()
                return envelope({
                    "job_run_id": str(job.job_run_id),
                    "status": "PENDING" if created else ("ALREADY_EXISTS" if job.status == "SUCCEEDED" else "ALREADY_RUNNING"),
                })
            finally:
                session.close()
        except Exception as exc:  # noqa: BLE001
            return envelope(error=_to_error(exc))

    if etf_service is not None:
        @server.tool(name="get_market_metrics", description="ETF Engine 市场指标与 QDII 对齐结果")
        def get_market_metrics(
            instrument_ids: list[str] | None = None,
            as_of: str | None = None,
            window_days: int = 20,
            instrument_id: str | None = None,
        ) -> dict:
            try:
                instrument_ids = instrument_ids or ([instrument_id] if instrument_id else [])
                if not instrument_ids:
                    raise MCPDomainError("INVALID_ARGUMENT", "instrument_ids 不能为空", "instrument_ids")
                if window_days < 1:
                    raise MCPDomainError("INVALID_ARGUMENT", "window_days 必须 >= 1", "window_days")
                as_of_dt = _parse_as_of_datetime(as_of)
                session = session_factory()
                try:
                    items = []
                    provenance = []
                    statuses = []
                    scores = []
                    flags = []
                    for raw_id in instrument_ids:
                        try:
                            snapshot = etf_service.read_metric(
                                session, UUID(raw_id), as_of=as_of_dt
                            )
                        except (ValueError, TypeError) as exc:
                            raise MCPDomainError("INVALID_ARGUMENT", str(exc), "instrument_ids") from exc
                        if snapshot is None:
                            raise MCPDomainError(
                                "NOT_FOUND", f"没有 PIT 指标结果: {raw_id}", "instrument_ids"
                            )
                        item = _metric_item(snapshot)
                        items.append(item)
                        refs = etf_service.read_metric_provenance(session, snapshot)
                        if not refs:
                            refs = [{
                                "provenance_id": str(snapshot.provenance_id),
                                "source_kind": "DERIVED_ENGINE",
                                "source": item["source"],
                                "provider": "internal",
                                "as_of_date": snapshot.market_date.isoformat(),
                                "quality_status": str(
                                    getattr(snapshot.quality_status, "value", snapshot.quality_status)
                                ),
                                "quality_flags": snapshot.quality_flags,
                            }]
                        provenance.extend(refs)
                        statuses.append(snapshot.quality_status)
                        scores.append(snapshot.quality_score)
                        flags.extend(snapshot.quality_flags)
                    status = _worst_quality(statuses)
                    freshness_domains = {}
                    for item in items:
                        metric_freshness = item.get("freshness") or {}
                        if isinstance(metric_freshness, dict) and metric_freshness.get("domains"):
                            freshness_domains.update(metric_freshness["domains"])
                        else:
                            freshness_domains[item["instrument_id"]] = {
                                "status": item.get("data_freshness") or "OK"
                            }
                    return envelope({
                        "as_of": as_of_dt.isoformat(),
                        "items": items,
                        "window_days": window_days,
                    }, quality_status=status,
                       quality_score=min(scores),
                       quality_flags=list(dict.fromkeys(flags)),
                       provenance=list({ref["provenance_id"]: ref for ref in provenance}.values()),
                       freshness=freshness_payload(freshness_domains),
                       as_of=as_of_dt)
                finally:
                    session.close()
            except Exception as exc:  # noqa: BLE001
                return envelope(error=_to_error(exc))

    @server.tool(name="get_watchlist", description="读取观察池及其当前/历史成员")
    def get_watchlist(watchlist_id: str | None = None, include_removed: bool = False) -> dict:
        try:
            session = session_factory()
            try:
                service = WatchlistService(session)
                watchlist = service.get(UUID(watchlist_id)) if watchlist_id else service.get_default()
                if watchlist is None:
                    raise MCPDomainError("NOT_FOUND", "没有 ACTIVE 默认观察池")
                members = service.list_members(
                    watchlist.watchlist_id,
                    include_removed=include_removed,
                    permission="READ",
                )
                return envelope({
                    "watchlist_id": str(watchlist.watchlist_id),
                    "name": watchlist.name,
                    "description": watchlist.description,
                    "status": str(getattr(watchlist.status, "value", watchlist.status)),
                    "members": [
                        {
                            "watchlist_member_id": str(member.watchlist_member_id),
                            "instrument_id": str(member.instrument_id),
                            "added_at": member.added_at.isoformat(),
                            "removed_at": member.removed_at.isoformat() if member.removed_at else None,
                            "note": member.note,
                        }
                        for member in members
                    ],
                })
            finally:
                session.close()
        except Exception as exc:  # noqa: BLE001
            return envelope(error=_to_error(exc))

    @server.tool(name="add_watchlist_member", description="向观察池加入标的（RESEARCH_WRITE）")
    def add_watchlist_member(watchlist_id: str, instrument_id: str, note: str | None = None) -> dict:
        try:
            session = session_factory()
            try:
                member = WatchlistService(session).add_member(
                    UUID(watchlist_id), UUID(instrument_id),
                    note=note, permission="RESEARCH_WRITE",
                )
                session.commit()
                return envelope({
                    "watchlist_member_id": str(member.watchlist_member_id),
                    "watchlist_id": str(member.watchlist_id),
                    "instrument_id": str(member.instrument_id),
                    "added_at": member.added_at.isoformat(),
                    "removed_at": None,
                    "note": member.note,
                })
            finally:
                session.close()
        except Exception as exc:  # noqa: BLE001
            return envelope(error=_to_error(exc))

    @server.tool(name="remove_watchlist_member", description="从观察池软移除标的（RESEARCH_WRITE）")
    def remove_watchlist_member(watchlist_id: str, instrument_id: str) -> dict:
        try:
            session = session_factory()
            try:
                member = WatchlistService(session).remove_member(
                    UUID(watchlist_id), UUID(instrument_id), permission="RESEARCH_WRITE"
                )
                session.commit()
                return envelope({
                    "watchlist_member_id": str(member.watchlist_member_id),
                    "watchlist_id": str(member.watchlist_id),
                    "instrument_id": str(member.instrument_id),
                    "added_at": member.added_at.isoformat(),
                    "removed_at": member.removed_at.isoformat(),
                    "note": member.note,
                })
            finally:
                session.close()
        except Exception as exc:  # noqa: BLE001
            return envelope(error=_to_error(exc))

    # ---- Fundamental ----

    @server.tool(name="get_fundamentals", description="PIT 标准化财务指标")
    def get_fundamentals(
        instrument_id: str, as_of: str | None = None, metrics: list[str] | None = None,
    ) -> dict:
        try:
            requested = metrics or ["REVENUE", "NET_INCOME", "TOTAL_EQUITY", "SHARES_OUTSTANDING"]
            unknown = [metric for metric in requested if metric not in FROZEN_METRIC_CODES]
            if unknown:
                raise MCPDomainError(
                    "INVALID_ARGUMENT", f"未知 metric_code: {', '.join(unknown)}", "metrics",
                )
            as_of_dt = _parse_as_of_datetime(as_of)
            session = session_factory()
            try:
                rows = {}
                provenance = []
                for metric in requested:
                    row = fundamentals_service.get_latest(session, UUID(instrument_id), metric, as_of_dt)
                    if row is None:
                        continue
                    rows[metric] = {
                        "value": str(row.value), "unit": row.unit,
                        "period_end": row.period_end.isoformat(),
                        "period_type": str(getattr(row.period_type, "value", row.period_type)),
                        "statement_type": str(getattr(row.statement_type, "value", row.statement_type)),
                        "published_at": row.published_at.isoformat() if row.published_at else None,
                        "is_restated": row.is_restated,
                        "quality_status": str(getattr(row.quality_status, "value", row.quality_status)),
                    }
                    provenance.append({
                        "provenance_id": str(row.provenance_id), "source": row.provider,
                        "provider": row.provider, "as_of_date": row.period_end.isoformat(),
                        "quality_status": str(getattr(row.quality_status, "value", row.quality_status)),
                    })
                return envelope({"instrument_id": instrument_id, "as_of": as_of_dt.isoformat(), "metrics": rows},
                                provenance=provenance, as_of=as_of_dt)
            finally:
                session.close()
        except Exception as exc:  # noqa: BLE001
            return envelope(error=_to_error(exc))

    @server.tool(name="get_financial_history", description="PIT 财务事实历史")
    def get_financial_history(
        instrument_id: str, as_of: str | None = None, metrics: list[str] | None = None,
        start_period: str | None = None, end_period: str | None = None,
    ) -> dict:
        try:
            if metrics:
                unknown = [metric for metric in metrics if metric not in FROZEN_METRIC_CODES]
                if unknown:
                    raise MCPDomainError(
                        "INVALID_ARGUMENT", f"未知 metric_code: {', '.join(unknown)}", "metrics",
                    )
            as_of_dt = _parse_as_of_datetime(as_of)
            session = session_factory()
            try:
                rows = fundamentals_service.history(
                    session, UUID(instrument_id), as_of_dt, metrics=metrics,
                    start_period=date.fromisoformat(start_period) if start_period else None,
                    end_period=date.fromisoformat(end_period) if end_period else None,
                )
                data = [{
                    "financial_fact_id": str(row.financial_fact_id), "metric_code": row.metric_code,
                    "period_end": row.period_end.isoformat(), "period_type": str(row.period_type),
                    "value": str(row.value), "unit": row.unit, "published_at": row.published_at.isoformat(),
                    "is_restated": row.is_restated, "provenance_id": str(row.provenance_id),
                } for row in rows]
                return envelope({"instrument_id": instrument_id, "items": data},
                                provenance=[{"provenance_id": str(row.provenance_id), "source": row.provider,
                                             "provider": row.provider, "quality_status": str(row.quality_status)}
                                            for row in rows], as_of=as_of_dt)
            finally:
                session.close()
        except Exception as exc:  # noqa: BLE001
            return envelope(error=_to_error(exc))

    @server.tool(name="get_latest_filings", description="最新已披露财报事实引用")
    def get_latest_filings(instrument_id: str, as_of: str | None = None, limit: int = 20) -> dict:
        try:
            as_of_dt = _parse_as_of_datetime(as_of)
            session = session_factory()
            try:
                rows = fundamentals_service.filings(session, UUID(instrument_id), as_of_dt, limit=limit)
                return envelope({"instrument_id": instrument_id, "filings": [{
                    "source_document_id": row.source_document_id, "published_at": row.published_at.isoformat(),
                    "period_end": row.period_end.isoformat(), "statement_type": str(row.statement_type),
                    "provenance_id": str(row.provenance_id),
                } for row in rows]},
                                provenance=[{"provenance_id": str(row.provenance_id), "source": row.provider,
                                             "provider": row.provider, "quality_status": str(row.quality_status)}
                                            for row in rows], as_of=as_of_dt)
            finally:
                session.close()
        except Exception as exc:  # noqa: BLE001
            return envelope(error=_to_error(exc))

    @server.tool(name="sync_fundamentals", description="幂等触发财务事实同步 job")
    def sync_fundamentals(
        universe: list[str], start_period: str, end_period: str,
        metrics: list[str] | None = None,
    ) -> dict:
        try:
            session = session_factory()
            try:
                resolved = _resolve_sync_universe(session, universe, end_period)
                job, created = sync_runner.create_sync_job(session, "fundamental_sync_job", {
                    "universe": [str(value) for value in resolved], "start_period": start_period,
                    "end_period": end_period, "metrics": metrics or [],
                })
                session.commit()
                return envelope({"job_run_id": str(job.job_run_id),
                                 "status": "PENDING" if created else "ALREADY_EXISTS"})
            finally:
                session.close()
        except Exception as exc:  # noqa: BLE001
            return envelope(error=_to_error(exc))

    # ---- Job ----

    @server.tool(name="get_job_status", description="job 进度查询（禁止在 MCP 层等待 provider 拉取）")
    def get_job_status(job_run_id: str) -> dict:
        try:
            session = session_factory()
            try:
                job = job_service.get(session, UUID(job_run_id))
                if job is None:
                    raise MCPDomainError("NOT_FOUND", f"job {job_run_id} 不存在")
                return envelope({
                    "job_run_id": str(job.job_run_id),
                    "job_name": job.job_name,
                    "status": job.status,
                    "started_at": job.started_at.isoformat() if job.started_at else None,
                    "finished_at": job.finished_at.isoformat() if job.finished_at else None,
                    "error": job.error,
                })
            finally:
                session.close()
        except Exception as exc:  # noqa: BLE001
            return envelope(error=_to_error(exc))

    # ---- Valuation ----

    @server.tool(name="run_valuation", description="同步估值（v0.1 DCF；缺失假设 → MISSING_VALUATION_INPUT）")
    def run_valuation(
        instrument_id: str,
        model_type: str,
        as_of: str,
        fcf_forecast: list[float],
        assumptions: list[dict],
    ) -> dict:
        try:
            session = session_factory()
            try:
                req = ValuationRequest(
                    instrument_id=UUID(instrument_id),
                    model_type=model_type,
                    as_of=date.fromisoformat(as_of),
                    assumptions=[ValuationAssumptionInput(**a) for a in assumptions],
                    fcf_forecast=[Decimal(str(f)) for f in fcf_forecast],
                    created_by="HERMES",
                )
                run = valuation_service.run_valuation(session, req)
                data = {
                    "valuation_run_id": str(run.valuation_run_id),
                    "status": run.status,
                    "base_value": str(run.base_value),
                    "bear_value": str(run.bear_value),
                    "bull_value": str(run.bull_value),
                    "margin_of_safety": str(run.margin_of_safety),
                    "engine_version": run.engine_version,
                    "input_snapshot_hash": run.input_snapshot_hash,
                    "provenance_id": str(run.provenance_id) if run.provenance_id else None,
                }
                provenance = []
                if run.provenance_id:
                    provenance.append({
                        "provenance_id": str(run.provenance_id),
                        "source": "valuation_engine",
                        "provider": "internal",
                        "source_kind": "DERIVED_ENGINE",
                        "quality_status": "VERIFIED",
                    })
                return envelope(data, provenance=provenance, as_of=run.as_of)
            finally:
                session.close()
        except Exception as exc:  # noqa: BLE001
            return envelope(error=_to_error(exc))

    @server.tool(name="get_latest_valuation", description="读取标的最新 PIT 估值结果")
    def get_latest_valuation(instrument_id: str, as_of: str | None = None) -> dict:
        try:
            session = session_factory()
            try:
                run = valuation_service.latest(
                    session, UUID(instrument_id),
                    datetime.fromisoformat(as_of.replace("Z", "+00:00")) if as_of else None,
                )
                if run is None:
                    raise MCPDomainError("NOT_FOUND", "没有可见估值结果", "instrument_id")
                data = _valuation_public(run)
                return envelope(data, provenance=[{"provenance_id": data.get("provenance_id"),
                                                   "source": "valuation_engine", "provider": "internal",
                                                   "quality_status": "VERIFIED"}], as_of=run.as_of)
            finally:
                session.close()
        except Exception as exc:  # noqa: BLE001
            return envelope(error=_to_error(exc))

    @server.tool(name="get_valuation_history", description="读取标的估值运行历史")
    def get_valuation_history(instrument_id: str, as_of: str | None = None, limit: int = 50) -> dict:
        try:
            session = session_factory()
            try:
                rows = valuation_service.history(
                    session, UUID(instrument_id),
                    as_of=datetime.fromisoformat(as_of.replace("Z", "+00:00")) if as_of else None,
                    limit=limit,
                )
                runs = [_valuation_public(row) for row in rows]
                provenance = [
                    {
                        "provenance_id": str(row.provenance_id),
                        "source": "valuation_engine",
                        "provider": "internal",
                        "source_kind": "DERIVED_ENGINE",
                        "quality_status": "VERIFIED",
                    }
                    for row in rows if row.provenance_id
                ]
                return envelope({"instrument_id": instrument_id, "runs": runs}, provenance=provenance)
            finally:
                session.close()
        except Exception as exc:  # noqa: BLE001
            return envelope(error=_to_error(exc))

    # ---- Thesis ----

    # ---- Portfolio / Risk ----

    @server.tool(name="get_portfolio", description="读取组合状态与 NAV 快照")
    def get_portfolio(portfolio_id: str, as_of: str | None = None) -> dict:
        try:
            session = session_factory()
            try:
                cutoff = date.fromisoformat(as_of[:10]) if as_of else date.max
                data = portfolio_service.latest_view(session, UUID(portfolio_id), cutoff)
                if data is None:
                    raise MCPDomainError("NOT_FOUND", "组合不存在", "portfolio_id")
                snapshot = data.get("snapshot")
                as_of_dt = datetime.fromisoformat(snapshot["as_of"]) if snapshot else datetime.now(UTC)
                return envelope(data, provenance=portfolio_service.provenance_view(session, UUID(portfolio_id)),
                                freshness=_freshness_for(briefing_service, session, cutoff), as_of=as_of_dt)
            finally:
                session.close()
        except Exception as exc:  # noqa: BLE001
            return envelope(error=_to_error(exc))

    @server.tool(name="get_positions", description="读取由交易流水派生的持仓快照")
    def get_positions(portfolio_id: str, as_of: str | None = None) -> dict:
        try:
            session = session_factory()
            try:
                cutoff = date.fromisoformat(as_of[:10]) if as_of else date.max
                data = portfolio_service.positions_view(session, UUID(portfolio_id), cutoff)
                if data is None:
                    raise MCPDomainError("NOT_FOUND", "组合不存在", "portfolio_id")
                return envelope(data, provenance=portfolio_service.provenance_view(session, UUID(portfolio_id)),
                                freshness=_freshness_for(briefing_service, session, cutoff))
            finally:
                session.close()
        except Exception as exc:  # noqa: BLE001
            return envelope(error=_to_error(exc))

    @server.tool(name="get_portfolio_exposure", description="确定性组合暴露")
    def get_portfolio_exposure(portfolio_id: str, as_of: str | None = None) -> dict:
        try:
            session = session_factory()
            try:
                cutoff = date.fromisoformat(as_of[:10]) if as_of else date.max
                data = risk_service.exposure(session, UUID(portfolio_id), cutoff)
                if data is None:
                    raise MCPDomainError("NOT_FOUND", "组合不存在", "portfolio_id")
                return envelope(data, provenance=portfolio_service.provenance_view(session, UUID(portfolio_id)),
                                freshness=_freshness_for(briefing_service, session, cutoff))
            finally:
                session.close()
        except Exception as exc:  # noqa: BLE001
            return envelope(error=_to_error(exc))

    @server.tool(name="get_portfolio_risk", description="确定性集中度/回撤风险")
    def get_portfolio_risk(portfolio_id: str, as_of: str | None = None) -> dict:
        try:
            session = session_factory()
            try:
                cutoff = date.fromisoformat(as_of[:10]) if as_of else date.max
                data = risk_service.risk(session, UUID(portfolio_id), cutoff)
                if data is None:
                    raise MCPDomainError("NOT_FOUND", "组合不存在", "portfolio_id")
                return envelope(data, provenance=portfolio_service.provenance_view(session, UUID(portfolio_id)),
                                freshness=_freshness_for(briefing_service, session, cutoff))
            finally:
                session.close()
        except Exception as exc:  # noqa: BLE001
            return envelope(error=_to_error(exc))

    @server.tool(name="create_trade_proposal", description="创建交易建议；REAL 仅 PROPOSED，不执行账户写入")
    def create_trade_proposal(
        portfolio_id: str, instrument_id: str, proposal_type: str, quantity: float,
        limit_price_cny: float | None = None, target_weight: float | None = None,
        rationale: str | None = None, thesis_revision_id: str | None = None,
        linked_valuation_run_id: str | None = None, freshness: dict | str = "OK",
    ) -> dict:
        try:
            session = session_factory()
            try:
                server_freshness = _freshness_for(
                    briefing_service, session, date.today()
                )
                proposal = PortfolioService().create_trade_proposal(
                    session, UUID(portfolio_id), UUID(instrument_id), ProposalType(proposal_type),
                    quantity=Decimal(str(quantity)),
                    limit_price_cny=Decimal(str(limit_price_cny)) if limit_price_cny is not None else None,
                    target_weight=Decimal(str(target_weight)) if target_weight is not None else None,
                    rationale=rationale,
                    thesis_revision_id=UUID(thesis_revision_id) if thesis_revision_id else None,
                    linked_valuation_run_id=UUID(linked_valuation_run_id) if linked_valuation_run_id else None,
                    freshness=server_freshness, created_by="HERMES",
                )
                session.commit()
                return envelope({"trade_proposal_id": str(proposal.trade_proposal_id),
                                 "portfolio_id": portfolio_id, "instrument_id": instrument_id,
                                 "proposal_type": proposal.proposal_type, "quantity": str(proposal.quantity),
                                 "status": proposal.status},
                                freshness=server_freshness)
            finally:
                session.close()
        except Exception as exc:  # noqa: BLE001
            return envelope(error=_to_error(exc))

    @server.tool(name="get_thesis", description="Thesis 当前 head（as_of 可选 → PIT 版本）")
    def get_thesis(thesis_id: str, as_of: str | None = None) -> dict:
        try:
            session = session_factory()
            try:
                as_of_dt = _parse_as_of_datetime(as_of) if as_of else None
                rev = thesis_service.get_thesis(session, UUID(thesis_id), as_of=as_of_dt)
                if rev is None:
                    raise MCPDomainError("NOT_FOUND", f"thesis {thesis_id} 无版本")
                provenance = []
                if rev.provenance_id:
                    provenance.append({
                        "provenance_id": str(rev.provenance_id),
                        "source": "thesis_revision", "provider": "internal",
                        "source_kind": "HERMES" if rev.authored_by.startswith("HERMES") else "HUMAN",
                        "quality_status": "VERIFIED",
                    })
                return envelope({
                    "thesis_id": thesis_id,
                    "version": rev.version,
                    "summary": rev.summary,
                    "thesis_body": rev.thesis_body,
                    "authored_by": rev.authored_by,
                    "created_at": rev.created_at.isoformat(),
                    "provenance_id": str(rev.provenance_id) if rev.provenance_id else None,
                }, provenance=provenance,
                    freshness=_freshness_for(briefing_service, session, rev.created_at.date()),
                    as_of=rev.created_at)
            finally:
                session.close()
        except Exception as exc:  # noqa: BLE001
            return envelope(error=_to_error(exc))

    @server.tool(name="create_thesis_revision", description="追加不可变 Thesis revision（带 freshness 门禁）")
    def create_thesis_revision(
        thesis_id: str, base_revision_id: str, change_reason: str,
        thesis_body: dict, freshness: dict | str = "OK",
    ) -> dict:
        try:
            session = session_factory()
            try:
                server_freshness = _freshness_for(
                    briefing_service, session, date.today()
                )
                revision = thesis_service.create_revision(
                    session, UUID(thesis_id), thesis_body,
                    base_revision_id=UUID(base_revision_id), authored_by="HERMES",
                    change_reason=change_reason, freshness=server_freshness,
                )
                session.commit()
                return envelope({"thesis_id": thesis_id, "revision_id": str(revision.thesis_revision_id),
                                 "version": revision.version, "created_at": revision.created_at.isoformat()},
                                freshness=server_freshness,
                                provenance=[{"provenance_id": str(revision.provenance_id),
                                             "source": "thesis_revision", "provider": "internal",
                                             "quality_status": "VERIFIED"}])
            finally:
                session.close()
        except Exception as exc:  # noqa: BLE001
            return envelope(error=_to_error(exc))

    @server.tool(name="record_thesis_review", description="记录 Thesis 复核结论（带 freshness 门禁）")
    def record_thesis_review(
        thesis_id: str, review_type: str, conclusion: str,
        notes: str | None = None, health_after: str | None = None,
        freshness: dict | str = "OK",
    ) -> dict:
        try:
            session = session_factory()
            try:
                server_freshness = _freshness_for(
                    briefing_service, session, date.today()
                )
                review = thesis_service.record_review(
                    session, UUID(thesis_id), ReviewType(review_type), ReviewConclusion(conclusion),
                    actor_id="HERMES", notes=notes,
                    health_after=ThesisHealthStatus(health_after) if health_after else None,
                    freshness=server_freshness,
                )
                session.commit()
                return envelope({"review_id": str(review.review_id), "thesis_id": thesis_id,
                                 "review_type": review.review_type, "conclusion": review.conclusion,
                                 "reviewed_at": review.reviewed_at.isoformat()},
                                freshness=server_freshness,
                                provenance=[{"provenance_id": str(review.provenance_id),
                                             "source": "thesis_review", "provider": "internal",
                                             "quality_status": "VERIFIED"}])
            finally:
                session.close()
        except Exception as exc:  # noqa: BLE001
            return envelope(error=_to_error(exc))

    @server.tool(name="update_thesis_assumption", description="更新 Thesis 假设状态（带 freshness 门禁）")
    def update_thesis_assumption(
        assumption_id: str, status: str, test_condition: str | None = None,
        note: str | None = None, freshness: dict | str = "OK",
    ) -> dict:
        try:
            session = session_factory()
            try:
                server_freshness = _freshness_for(
                    briefing_service, session, date.today()
                )
                assumption = thesis_service.update_assumption(
                    session, UUID(assumption_id), ThesisHealthStatus(status), actor_id="HERMES",
                    test_condition=test_condition, note=note, freshness=server_freshness,
                )
                session.commit()
                return envelope({"assumption_id": str(assumption.assumption_id),
                                 "thesis_id": str(assumption.thesis_id), "status": assumption.status,
                                 "updated_at": assumption.created_at.isoformat()},
                                freshness=server_freshness)
            finally:
                session.close()
        except Exception as exc:  # noqa: BLE001
            return envelope(error=_to_error(exc))

    # ---- Research ----

    @server.tool(name="get_research_context", description="读取研究笔记与证据上下文")
    def get_research_context(
        instrument_id: str | None = None, workspace_id: str | None = None,
        as_of: str | None = None,
    ) -> dict:
        try:
            session = session_factory()
            try:
                resolved_instrument_id = UUID(instrument_id) if instrument_id else None
                as_of_dt = (
                    datetime.fromisoformat(as_of.replace("Z", "+00:00"))
                    if as_of else None
                )
                context = ResearchService().get_context(
                    session, instrument_id=resolved_instrument_id,
                    workspace_id=UUID(workspace_id) if workspace_id else None,
                    as_of=as_of_dt,
                )
                if resolved_instrument_id is not None:
                    theses = thesis_service.list_for_instrument(
                        session, resolved_instrument_id, as_of=as_of_dt,
                    )
                    context["related"] = {
                        "thesis_ids": [row["thesis_id"] for row in theses],
                        "evidence_ids": [row["id"] for row in context["evidence"]],
                    }
                return envelope(context)
            finally:
                session.close()
        except Exception as exc:  # noqa: BLE001
            return envelope(error=_to_error(exc))

    @server.tool(name="save_research_note", description="保存研究笔记（RESEARCH_WRITE）")
    def save_research_note(
        title: str, body_md: str, instrument_id: str | None = None,
        workspace_id: str | None = None, thread_id: str | None = None,
    ) -> dict:
        try:
            session = session_factory()
            try:
                note = ResearchService().save_note(
                    session, title, body_md,
                    instrument_id=UUID(instrument_id) if instrument_id else None,
                    workspace_id=UUID(workspace_id) if workspace_id else None,
                    thread_id=UUID(thread_id) if thread_id else None,
                    created_by="HERMES",
                )
                session.commit()
                return envelope({"research_note_id": str(note.research_note_id), "title": note.title,
                                 "created_at": note.created_at.isoformat()},
                                provenance=[{"provenance_id": str(note.provenance_id),
                                             "source": "research_note", "provider": "internal",
                                             "quality_status": "VERIFIED"}])
            finally:
                session.close()
        except Exception as exc:  # noqa: BLE001
            return envelope(error=_to_error(exc))

    @server.tool(name="get_evidence", description="读取 Evidence 及其 provenance")
    def get_evidence(instrument_id: str | None = None, thesis_revision_id: str | None = None,
                     limit: int = 50) -> dict:
        try:
            session = session_factory()
            try:
                rows = ResearchService().get_evidence(
                    session, instrument_id=UUID(instrument_id) if instrument_id else None,
                    thesis_revision_id=UUID(thesis_revision_id) if thesis_revision_id else None,
                    limit=limit,
                )
                return envelope({"items": [{"evidence_id": str(row.evidence_id), "title": row.title,
                                             "claim": row.claim, "direction": row.direction,
                                             "confidence": row.confidence, "source_ref": row.source_ref,
                                             "provenance_id": str(row.provenance_id)} for row in rows]},
                                provenance=[{"provenance_id": str(row.provenance_id), "source": "evidence_item",
                                             "provider": "internal", "quality_status": "VERIFIED"} for row in rows])
            finally:
                session.close()
        except Exception as exc:  # noqa: BLE001
            return envelope(error=_to_error(exc))

    @server.tool(name="search_research", description="搜索研究笔记与证据")
    def search_research(query: str, limit: int = 20) -> dict:
        try:
            if not query.strip():
                raise MCPDomainError("INVALID_ARGUMENT", "query 不能为空", "query")
            session = session_factory()
            try:
                rows = ResearchService().search(session, query, limit=limit)
                items = []
                for row in rows:
                    if hasattr(row, "research_note_id"):
                        items.append({"type": "research_note", "id": str(row.research_note_id),
                                      "title": row.title, "text": row.body_md})
                    else:
                        items.append({"type": "evidence", "id": str(row.evidence_id),
                                      "title": row.title, "text": row.claim})
                return envelope({"query": query, "items": items})
            finally:
                session.close()
        except Exception as exc:  # noqa: BLE001
            return envelope(error=_to_error(exc))

    # ---- Briefing ----

    @server.tool(name="get_daily_context", description="daily context（freshness 载体，冻结规范 §36）")
    def get_daily_context(market_date: str) -> dict:
        try:
            session = session_factory()
            try:
                ctx = briefing_service.get_daily_context(session, date.fromisoformat(market_date))
                if ctx is None:
                    raise MCPDomainError("NOT_FOUND", f"{market_date} 无 daily context")
                return envelope({
                    "market_date": ctx.market_date.isoformat(),
                    "freshness_status": ctx.freshness_status,
                    "data_freshness": ctx.data_freshness,
                    "markets": ctx.markets,
                    "engine_versions": ctx.engine_versions,
                    "summary": ctx.summary,
                }, freshness={"overall": ctx.freshness_status, "domains": ctx.data_freshness or {}},
                           provenance=[{"provenance_id": str(ctx.daily_context_id), "source": "daily_context",
                                        "provider": "internal", "quality_status": "VERIFIED"}])
            finally:
                session.close()
        except Exception as exc:  # noqa: BLE001
            return envelope(error=_to_error(exc))

    @server.tool(name="save_daily_brief", description="brief 落库（model_profile 必填，禁止记录模型名）")
    def save_daily_brief(
        daily_context_id: str,
        market_date: str,
        content_md: str,
        sections: list[dict] | None = None,
        model_profile: str = "fast",
    ) -> dict:
        try:
            session = session_factory()
            try:
                brief = briefing_service.save_daily_brief(
                    session, UUID(daily_context_id), date.fromisoformat(market_date),
                    content_md, sections=sections, model_profile=model_profile)
                session.commit()
                return envelope({
                    "daily_brief_id": str(brief.daily_brief_id),
                    "status": brief.status,
                })
            finally:
                session.close()
        except Exception as exc:  # noqa: BLE001
            return envelope(error=_to_error(exc))

    return server
