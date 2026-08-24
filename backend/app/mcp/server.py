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
from uuid import UUID, uuid4

from mcp.server.mcpserver import MCPServer
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.briefing.service import BriefingService
from app.common.enums import DataQualityStatus
from app.etf.service import ETFDataService
from app.instruments.models import Instrument
from app.instruments.service import (
    InstrumentNotFoundError,
    WatchlistArchivedError,
    WatchlistMemberNotFoundError,
    WatchlistNotFoundError,
    WatchlistPermissionError,
    WatchlistService,
)
from app.jobs.models import JobRun
from app.jobs.sync_jobs import SyncJobRunner
from app.market_data.parquet import ParquetStore
from app.market_data.service import MarketDataService
from app.thesis.service import ThesisService
from app.valuation.engine import ValuationAssumptionInput
from app.valuation.errors import ValuationError
from app.valuation.service import ValuationRequest, ValuationService

logger = logging.getLogger(__name__)

__all__ = [
    "FROZEN_MCP_TOOLS",
    "M1_5_MCP_TOOLS",
    "M3_MCP_TOOLS",
    "ADR006_MCP_TOOLS",
    "MCP_ALLOWED_TOOLS",
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


class MCPDomainError(Exception):
    """业务错误：进包络 error（ts07 §4.1），不抛到协议层。"""

    def __init__(self, code: str, message: str, field: str | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.field = field


def envelope(
    data=None,
    *,
    quality_status: DataQualityStatus = DataQualityStatus.VERIFIED,
    quality_score: Decimal = Decimal("1.0"),
    quality_flags: list[str] | None = None,
    provenance: list | None = None,
    as_of: datetime | None = None,
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
    if isinstance(exc, ValuationError):
        return {"code": exc.code, "message": str(exc)}
    return {"code": "ENGINE_INTERNAL_ERROR", "message": str(exc)}


def _metric_item(snapshot) -> dict:
    details = snapshot.details or {}
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
        "levels": {
            "level_0": details.get("level_0"),
            "level_1": details.get("level_1"),
            "level_2": details.get("level_2"),
        },
        "index_pe": str(snapshot.index_pe) if snapshot.index_pe is not None else None,
        "index_pb": str(snapshot.index_pb) if snapshot.index_pb is not None else None,
        "valuation_band": snapshot.valuation_band,
        "reference_nav_basis": snapshot.reference_nav_basis,
        "engine_version": snapshot.engine_version,
        "input_hash": snapshot.input_hash,
        "provenance_id": str(snapshot.provenance_id),
        "quality_flags": snapshot.quality_flags,
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


def _resolve_sync_universe(session: Session, universe: list[str], end_date: str) -> list[UUID]:
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


def build_mcp_server(
    session_factory: Callable[[], Session],
    *,
    parquet_store: ParquetStore,
    market_service: MarketDataService,
    valuation_service: ValuationService,
    thesis_service: ThesisService,
    briefing_service: BriefingService,
    sync_runner: SyncJobRunner,
    etf_service: ETFDataService | None = None,
) -> MCPServer:
    server = MCPServer(name="hermes-backend", version="0.1.0",
                       description="Hermes Investment Office Backend（MCP 契约 TS-07）")

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
                stmt = select(Instrument)
                if symbol:
                    stmt = stmt.where(Instrument.symbol == symbol)
                if name:
                    stmt = stmt.where(Instrument.name.contains(name))
                if instrument_type:
                    stmt = stmt.where(Instrument.instrument_type == instrument_type)
                if market:
                    stmt = stmt.where(Instrument.market == market)
                rows = session.execute(stmt.limit(limit)).scalars().all()
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
    def get_market_snapshot(instrument_ids: list[str], as_of: str) -> dict:
        try:
            as_of_date = date.fromisoformat(as_of)
            session = session_factory()
            try:
                rows = []
                for iid in instrument_ids:
                    bars = market_service.get_ohlcva(session, UUID(iid), as_of=as_of_date)
                    if not bars:
                        rows.append({"instrument_id": iid, "trade_date": None, "close": None})
                        continue
                    last = bars[-1]
                    rows.append({
                        "instrument_id": iid,
                        "trade_date": last["trade_date"].isoformat(),
                        "close": last["close"],
                        "pct_change": last["pct_change"],
                        "volume": last["volume"],
                        "amount": last["amount"],
                        "provider": last["provider"],
                    })
                return envelope({"snapshots": rows})
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
            as_of: str = "",
            window_days: int = 20,
            instrument_id: str | None = None,
        ) -> dict:
            try:
                instrument_ids = instrument_ids or ([instrument_id] if instrument_id else [])
                if not instrument_ids:
                    raise MCPDomainError("INVALID_ARGUMENT", "instrument_ids 不能为空", "instrument_ids")
                if window_days < 1:
                    raise MCPDomainError("INVALID_ARGUMENT", "window_days 必须 >= 1", "window_days")
                if "T" in as_of:
                    as_of_dt = datetime.fromisoformat(as_of.replace("Z", "+00:00"))
                else:
                    as_of_dt = datetime.combine(
                        date.fromisoformat(as_of), time.max, tzinfo=UTC
                    )
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
                    return envelope({
                        "as_of": as_of_dt.isoformat(),
                        "items": items,
                        "window_days": window_days,
                    }, quality_status=status,
                       quality_score=min(scores),
                       quality_flags=list(dict.fromkeys(flags)),
                       provenance=list({ref["provenance_id"]: ref for ref in provenance}.values()),
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

    # ---- Job ----

    @server.tool(name="get_job_status", description="job 进度查询（禁止在 MCP 层等待 provider 拉取）")
    def get_job_status(job_run_id: str) -> dict:
        try:
            session = session_factory()
            try:
                job = session.get(JobRun, UUID(job_run_id))
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
                return envelope({
                    "valuation_run_id": str(run.valuation_run_id),
                    "status": run.status,
                    "base_value": str(run.base_value),
                    "bear_value": str(run.bear_value),
                    "bull_value": str(run.bull_value),
                    "margin_of_safety": str(run.margin_of_safety),
                    "engine_version": run.engine_version,
                })
            finally:
                session.close()
        except Exception as exc:  # noqa: BLE001
            return envelope(error=_to_error(exc))

    # ---- Thesis ----

    @server.tool(name="get_thesis", description="Thesis 当前 head（as_of 可选 → PIT 版本）")
    def get_thesis(thesis_id: str, as_of: str | None = None) -> dict:
        try:
            session = session_factory()
            try:
                as_of_dt = (datetime.fromisoformat(as_of)
                            if as_of else None)
                rev = thesis_service.get_thesis(session, UUID(thesis_id), as_of=as_of_dt)
                if rev is None:
                    raise MCPDomainError("NOT_FOUND", f"thesis {thesis_id} 无版本")
                return envelope({
                    "thesis_id": thesis_id,
                    "version": rev.version,
                    "summary": rev.summary,
                    "thesis_body": rev.thesis_body,
                    "authored_by": rev.authored_by,
                    "created_at": rev.created_at.isoformat(),
                })
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
                })
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
