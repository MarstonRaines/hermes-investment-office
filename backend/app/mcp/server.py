# =====================================================================
# backend/app/mcp/server.py —— MCP Server（TS-07，冻结）
#
# - 传输：StreamableHTTP（FastAPI 内嵌 /mcp，冻结规范 §31.1）；
# - 工具白名单：M1.5 实现子集 ⊆ ts07 冻结 28 工具（ARCH-MCP-001 测试断言）；
# - 统一响应包络（ts01 五要素：request_id / as_of / data / quality / provenance）；
# - 业务错误进包络 error（{code, message, field}），协议层错误（未知工具等）
#   由 MCP SDK 处理（-32601）；
# - v0.1 localhost 信任边界（ADR-004 D3：认证远程化时激活）。
# =====================================================================
from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import UUID, uuid4

from mcp.server.mcpserver import MCPServer
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.briefing.service import BriefingService
from app.common.enums import DataQualityStatus
from app.instruments.models import Instrument
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
    "envelope",
    "MCPDomainError",
    "build_mcp_server",
]

# ts07 §2 冻结 28 工具白名单（八组；M5 全量实现，M1.5 实现子集）
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
    if isinstance(exc, ValuationError):
        return {"code": exc.code, "message": str(exc)}
    return {"code": "ENGINE_INTERNAL_ERROR", "message": str(exc)}


def build_mcp_server(
    session_factory: Callable[[], Session],
    *,
    parquet_store: ParquetStore,
    market_service: MarketDataService,
    valuation_service: ValuationService,
    thesis_service: ThesisService,
    briefing_service: BriefingService,
    sync_runner: SyncJobRunner,
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

    @server.tool(name="sync_market_data", description="幂等触发行情同步 job（§8.2：不重复创建/拉取）")
    def sync_market_data(universe: list[str], start: str, end: str) -> dict:
        try:
            session = session_factory()
            try:
                job, created = sync_runner.create_sync_job(session, "market_sync_job", {
                    "universe": universe, "start": start, "end": end,
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
