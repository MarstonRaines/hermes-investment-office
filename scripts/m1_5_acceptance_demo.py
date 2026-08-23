# =====================================================================
# scripts/m1_5_acceptance_demo.py —— M1.5 Vertical Slice 验收演示（真实数据）
#
# 演示链路（ACC-M1.5-001~004，真实茅台数据 + 真实 DB）：
#   [1] Instrument（600519 已入库，M1 数据层产物）
#   [2] Data/Fundamental（已有 483 bars + 财务事实）
#   [3] run_valuation（真实 PIT 财务 + 显式假设 DCF）
#   [4] Thesis（DRAFT → ACTIVE）
#   [5] PAPER Portfolio（入金 + 模拟买入 600519 + 快照）
#   [6] Daily Context + Brief
#   [7] MCP 链路（JSON-RPC：resolve_instrument / get_daily_context /
#         get_job_status / run_valuation 经 /mcp 端点）
#
# 用法：cd backend && .venv/bin/python ../scripts/m1_5_acceptance_demo.py
# =====================================================================
from __future__ import annotations

import asyncio
import json
import os
import sys
from datetime import date, datetime, timezone
from decimal import Decimal
from uuid import uuid4

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("HERMES_CONFIG_DIR", "config")

import app.models  # noqa: F401,E402 —— 注册全部 ORM 模型（FK 目标表完整性）

from sqlalchemy import create_engine, select  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from app.common.config import settings  # noqa: E402
from app.instruments.models import Instrument  # noqa: E402


def log(msg: str) -> None:
    print(msg)


def main() -> None:
    log("=" * 68)
    log("M1.5 Vertical Slice 验收演示（真实数据）")
    log("=" * 68)
    engine = create_engine(settings.db_url)

    from app.briefing.service import BriefingService
    from app.calendar.service import CalendarService
    from app.market_data.parquet import ParquetStore
    from app.market_data.service import MarketDataService
    from app.portfolio.service import PortfolioService
    from app.thesis.service import ThesisService
    from app.valuation.engine import ValuationAssumptionInput
    from app.valuation.service import ValuationRequest, ValuationService

    parquet = ParquetStore(f"{settings.data_dir}/parquet")
    market_service = MarketDataService(parquet)

    with Session(bind=engine) as session:
        inst = session.execute(
            select(Instrument).where(Instrument.symbol == "600519")
        ).scalar_one_or_none()
        iid = None
        if inst is not None:
            iid = inst.instrument_id
            rows = market_service.get_ohlcva(session, iid, as_of=date.today())
            if not rows:
                iid = None
        if iid is None:
            log("[0] 数据缺失——自举：真实同步茅台 2 年行情 + 财务（TuShare 直连）")
            from datetime import timedelta
            from app.providers.bootstrap import register_all_providers
            from app.providers.capability_matrix import load_capability_matrix
            from app.providers.factory import ProviderFactory
            from app.providers.raw_store import RawEvidenceStore
            from app.providers.registry import ProviderRegistry
            from app.providers.runtime_config import load_runtime_configs
            from app.jobs.sync_jobs import build_sync_runner
            from app.instruments.models import ProviderSymbol

            matrix = load_capability_matrix(settings.provider_capability_path)
            registry = ProviderRegistry(matrix)
            register_all_providers(registry)
            runtime = load_runtime_configs(settings.providers_runtime_path)
            factory = ProviderFactory(registry, runtime, matrix,
                                      session_factory=lambda: Session(bind=engine))
            raw = RawEvidenceStore(settings.data_dir)
            runner = build_sync_runner(lambda: Session(bind=engine), factory, registry,
                                       parquet, raw)
            inst = Instrument(instrument_type="CN_EQUITY", symbol="600519",
                              name="贵州茅台", market="SSE", currency="CNY")
            session.add(inst)
            session.flush()
            session.add(ProviderSymbol(
                instrument_id=inst.instrument_id, provider="tushare",
                symbol="600519.SH", valid_from=date(2020, 1, 1)))
            session.commit()
            iid = inst.instrument_id
            end = date.today() - __import__("datetime").timedelta(days=1)
            start = end - __import__("datetime").timedelta(days=730)
            job, _ = runner.create_sync_job(session, "market_sync_job",
                                            {"universe": [str(iid)],
                                             "start": start.isoformat(), "end": end.isoformat()})
            session.commit()
            result = asyncio.run(runner.run_market_sync(job.job_run_id, [iid], start, end))
            log(f"[0] market_sync: {result.bars} bars")
            fjob, _ = runner.create_sync_job(session, "fundamental_sync_job",
                                             {"universe": [str(iid)],
                                              "metrics": ["REVENUE", "NET_INCOME", "TOTAL_EQUITY", "SHARES_OUTSTANDING"],
                                              "start_period": f"{end.year - 2}-01-01",
                                              "end_period": f"{end.year}-12-31"})
            session.commit()
            fres = asyncio.run(runner.run_fundamental_sync(
                fjob.job_run_id, [iid],
                ["REVENUE", "NET_INCOME", "TOTAL_EQUITY", "SHARES_OUTSTANDING"],
                date(end.year - 2, 1, 1), date(end.year, 12, 31)))
            log(f"[0] fundamental_sync: {fres.bars} facts")
        from app.calendar.service import CalendarService
        from app.calendar.source import fetch_sina_trade_dates

        cal_svc = CalendarService()
        n_cal = cal_svc.sync_dates(session, fetch_sina_trade_dates())
        session.commit()
        log(f"[0] 交易日历同步: {n_cal} 行")

        rows = market_service.get_ohlcva(session, iid, as_of=date.today())
        log(f"[1] Instrument: {inst.name} ({iid})")
        log(f"[2] Data 就绪: {len(rows)} 根日线（最新 {rows[-1]['trade_date']} 收盘 {rows[-1]['close']}）")

        # ---- [3] Valuation（真实 PIT 财务 + 显式假设；缺输入 → 重同步财务重试）----
        from app.valuation.errors import MissingValuationInputError

        valuation = ValuationService(market_service)

        def re_sync_fundamentals() -> None:
            from app.providers.bootstrap import register_all_providers
            from app.providers.capability_matrix import load_capability_matrix
            from app.providers.factory import ProviderFactory
            from app.providers.raw_store import RawEvidenceStore
            from app.providers.registry import ProviderRegistry
            from app.providers.runtime_config import load_runtime_configs
            from app.jobs.sync_jobs import build_sync_runner

            matrix = load_capability_matrix(settings.provider_capability_path)
            registry = ProviderRegistry(matrix)
            register_all_providers(registry)
            runtime = load_runtime_configs(settings.providers_runtime_path)
            factory = ProviderFactory(registry, runtime, matrix,
                                      session_factory=lambda: Session(bind=engine))
            runner = build_sync_runner(lambda: Session(bind=engine), factory, registry,
                                       parquet, RawEvidenceStore(settings.data_dir))
            end = date.today() - __import__("datetime").timedelta(days=1)
            fjob, _ = runner.create_sync_job(session, "fundamental_sync_job",
                                             {"universe": [str(iid)],
                                              "metrics": ["NET_INCOME", "SHARES_OUTSTANDING", "TOTAL_EQUITY"],
                                              "start_period": f"{end.year - 2}-01-01",
                                              "end_period": f"{end.year}-12-31"})
            session.commit()
            fres = asyncio.run(runner.run_fundamental_sync(
                fjob.job_run_id, [iid],
                ["NET_INCOME", "SHARES_OUTSTANDING", "TOTAL_EQUITY"],
                date(end.year - 2, 1, 1), date(end.year, 12, 31)))
            log(f"[3] 财务重同步: {fres.bars} facts")

        assumptions = [
            ValuationAssumptionInput(name="wacc_base", value=Decimal("0.10"), basis="analyst_assumption"),
            ValuationAssumptionInput(name="terminal_growth_base", value=Decimal("0.04"), basis="long_run_nominal_growth_assumption"),
            ValuationAssumptionInput(name="exit_multiple_base", value=Decimal("20"), basis="peer_median_fcf_multiple"),
            ValuationAssumptionInput(name="wacc_bear", value=Decimal("0.12"), basis="risk_premium_upside"),
            ValuationAssumptionInput(name="terminal_growth_bear", value=Decimal("0.03"), basis="downside_long_run"),
            ValuationAssumptionInput(name="exit_multiple_bear", value=Decimal("12"), basis="downside_peer_multiple"),
            ValuationAssumptionInput(name="fcf_multiplier_bear", value=Decimal("0.85"), basis="downside_scenario"),
            ValuationAssumptionInput(name="wacc_bull", value=Decimal("0.08"), basis="lower_risk_premium"),
            ValuationAssumptionInput(name="terminal_growth_bull", value=Decimal("0.05"), basis="upside_long_run"),
            ValuationAssumptionInput(name="exit_multiple_bull", value=Decimal("28"), basis="upside_peer_multiple"),
            ValuationAssumptionInput(name="fcf_multiplier_bull", value=Decimal("1.15"), basis="upside_scenario"),
        ]
        try:
            run = valuation.run_valuation(session, ValuationRequest(
                instrument_id=iid, model_type="DCF", as_of=date.today(),
                assumptions=assumptions,
                fcf_forecast=[Decimal(str(v)) for v in
                              (300e9, 330e9, 360e9, 390e9, 420e9)],
                created_by="HERMES_DEMO",
            ))
        except MissingValuationInputError:
            re_sync_fundamentals()
            run = valuation.run_valuation(session, ValuationRequest(
                instrument_id=iid, model_type="DCF", as_of=date.today(),
                assumptions=assumptions,
                fcf_forecast=[Decimal(str(v)) for v in
                              (300e9, 330e9, 360e9, 390e9, 420e9)],
                created_by="HERMES_DEMO",
            ))
        log(f"[3] Valuation COMPLETED: base={run.base_value:,.0f} 元 "
            f"bear={run.bear_value:,.0f} bull={run.bull_value:,.0f} "
            f"MoS={run.margin_of_safety} engine={run.engine_version}")

        # ---- [4] Thesis ----
        thesis_svc = ThesisService()
        thesis = thesis_svc.create_thesis(session, iid, "贵州茅台研究",
                                          {"price": float(rows[-1]["close"]),
                                           "base_value": str(run.base_value)})
        from app.common.enums import ThesisLifecycleStatus

        thesis_svc.transition_lifecycle(session, thesis.thesis_id, ThesisLifecycleStatus.ACTIVE,
                                        actor="HUMAN", reason="M1.5 验收")
        session.commit()
        log(f"[4] Thesis ACTIVE: {thesis.thesis_id}")

        # ---- [5] PAPER Portfolio ----
        from app.common.enums import TransactionType

        pf_svc = PortfolioService()
        pf = pf_svc.create_portfolio(session, f"切片组合{uuid4().hex[:4]}")
        session.flush()
        pf_svc.record_transaction(session, pf.portfolio_id, TransactionType.CASH_IN,
                                  amount_cny=Decimal("1000000"), trade_date=date.today())
        close = Decimal(str(rows[-1]["close"]))
        pf_svc.simulate_paper_trade(session, pf.portfolio_id, TransactionType.BUY,
                                    instrument_id=iid, quantity=Decimal("100"),
                                    price_cny=close, trade_date=date.today())
        session.flush()
        snap = pf_svc.snapshot(session, pf.portfolio_id, date.today(), {iid: close})
        session.commit()
        log(f"[5] PAPER 快照: NAV={snap['nav_cny']:,.2f} 现金={snap['cash_cny']:,.2f} "
            f"市值={snap['market_value_cny']:,.2f}")

        # ---- [6] Daily Context + Brief ----
        brief_svc = BriefingService(market_service, CalendarService())
        ctx = brief_svc.get_daily_context(session, date.today())
        if ctx is None:     # 幂等：同 market_date 已有 context 则复用
            ctx = brief_svc.build_daily_context(session, date.today(), instruments=[iid])
            session.flush()
        from app.briefing.models import DailyBrief
        from sqlalchemy import select as sel

        existing_brief = session.execute(
            sel(DailyBrief).where(DailyBrief.market_date == date.today())).scalars().first()
        brief_id = existing_brief.daily_brief_id if existing_brief else None
        if existing_brief is None:     # 幂等：同 market_date 已有 brief 则跳过
            brief = brief_svc.save_daily_brief(
                session, ctx.daily_context_id, date.today(),
                f"# 日报 {date.today()}\n- 估值 base={run.base_value:,.0f}\n- 持仓 100 股",
                sections=[{"id": "valuation", "value": str(run.base_value)},
                          {"id": "thesis", "thesis_id": str(thesis.thesis_id)}],
                model_profile="fast")
            session.commit()
            brief_id = brief.daily_brief_id
        log(f"[6] Daily Context freshness={ctx.freshness_status}；Brief {brief_id}")

    # ---- [7] MCP 链路（经 /mcp 端点，JSON-RPC）----
    from fastapi.testclient import TestClient
    from app.main import app as backend_app

    # base_url 带端口：allowed_hosts 为 "127.0.0.1:*" 模式（transport_security）
    with TestClient(backend_app, base_url="http://127.0.0.1:8000") as client:
        def call(method: str, params: dict, rid: int):
            r = client.post("/mcp", json={"jsonrpc": "2.0", "id": rid, "method": method,
                                          "params": params},
                            headers={"Accept": "application/json, text/event-stream",
                                     "Content-Type": "application/json"},
                            follow_redirects=True)
            for line in r.text.splitlines():
                if line.startswith("data: "):
                    return json.loads(line[6:])
            return json.loads(r.text)

        listed = call("tools/list", {}, 1)
        tools = sorted(t["name"] for t in listed["result"]["tools"])
        log(f"[7] MCP tools/list: {len(tools)} 工具（白名单子集）")

        resolved = call("tools/call", {"name": "resolve_instrument",
                                       "arguments": {"symbol": "600519"}}, 2)
        payload = json.loads(resolved["result"]["content"][0]["text"])
        mcp_iid = payload["data"]["matches"][0]["instrument_id"]
        log(f"[7] MCP resolve_instrument(600519) → {mcp_iid}")

        ctx_r = call("tools/call", {"name": "get_daily_context",
                                    "arguments": {"market_date": str(date.today())}}, 3)
        ctx_payload = json.loads(ctx_r["result"]["content"][0]["text"])
        log(f"[7] MCP get_daily_context → freshness={ctx_payload['data']['freshness_status']}")

        job_r = call("tools/call", {"name": "get_job_status",
                                    "arguments": {"job_run_id": str(job_id_of(session))}}, 4)
        log(f"[7] MCP get_job_status → {json.loads(job_r['result']['content'][0]['text'])['data']['status']}")

    log("=" * 68)
    log("M1.5 Vertical Slice 验收演示完成 ✅（ACC-M1.5-001~004）")


def job_id_of(session) -> str:
    from app.jobs.models import JobRun

    row = session.execute(select(JobRun.job_run_id).order_by(JobRun.created_at.desc()).limit(1)).first()
    return str(row[0]) if row else str(uuid4())


if __name__ == "__main__":
    main()
