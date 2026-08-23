# =====================================================================
# scripts/m1_acceptance_demo.py —— M1 验收演示（真实网络 + 真实标的）
#
# 演示链路（冻结规范 §47 M1 验收）：
#   1. 贵州茅台入库（Instrument + provider_symbols）
#   2. market_sync_job 真实同步（TuShare，直连）
#   3. OHLCVA 查询（PG 指针 → DuckDB）
#   4. fundamental_sync_job 财务同步（近 2 年 REVENUE/NET_INCOME）
#   5. PIT 查询（as_of 可见性）
#   6. provenance 反查 + raw 落盘校验
#   7. 交易日历 + FX + 复权因子
#
# 用法：cd backend && ../scripts/m1_acceptance_demo.py   （或 .venv/bin/python ../scripts/...）
# 前置：PostgreSQL 已迁移、.env 含 HERMES_TUSHARE_TOKEN
# =====================================================================
from __future__ import annotations

import asyncio
import os
import sys
from datetime import date, timedelta
from uuid import uuid4

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("HERMES_CONFIG_DIR", "config")

from sqlalchemy import create_engine, select  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from app.common.config import settings  # noqa: E402
from app.common.enums import MarketCode  # noqa: E402
from app.instruments.models import Instrument, ProviderSymbol  # noqa: E402
from app.jobs.sync_jobs import build_sync_runner  # noqa: E402
from app.market_data.parquet import ParquetStore  # noqa: E402
from app.market_data.service import MarketDataService  # noqa: E402
from app.providers.bootstrap import register_all_providers  # noqa: E402
from app.providers.capability_matrix import load_capability_matrix  # noqa: E402
from app.providers.factory import ProviderFactory  # noqa: E402
from app.providers.raw_store import RawEvidenceStore  # noqa: E402
from app.providers.registry import ProviderRegistry  # noqa: E402
from app.providers.runtime_config import load_runtime_configs  # noqa: E402

OUT = []


def log(msg: str) -> None:
    print(msg)
    OUT.append(msg)


async def main() -> None:
    log("=" * 64)
    log("M1 Data Layer 验收演示（真实数据，TuShare 直连）")
    log("=" * 64)

    engine = create_engine(settings.db_url)
    matrix = load_capability_matrix(settings.provider_capability_path)
    registry = ProviderRegistry(matrix)
    register_all_providers(registry)
    runtime = load_runtime_configs(settings.providers_runtime_path)
    factory = ProviderFactory(registry, runtime, matrix)
    parquet = ParquetStore(f"{settings.data_dir}/parquet")
    raw = RawEvidenceStore(settings.data_dir)
    runner = build_sync_runner(lambda: Session(bind=engine), factory, registry, parquet, raw)

    with Session(bind=engine) as session:
        # ---- 1. 贵州茅台入库 ----
        inst = session.execute(
            select(Instrument).where(Instrument.symbol == "600519")
        ).scalar_one_or_none()
        if inst is None:
            inst = Instrument(
                instrument_type="CN_EQUITY", symbol="600519", name="贵州茅台",
                market="SSE", currency="CNY",
            )
            session.add(inst)
            session.flush()
            session.add(ProviderSymbol(
                instrument_id=inst.instrument_id, provider="tushare",
                symbol="600519.SH", valid_from=date(2020, 1, 1),
            ))
            session.commit()
        log(f"[1] 标的就绪: {inst.name} ({inst.instrument_id})")

        # ---- 2. market_sync_job（近 10 个自然日）----
        end = date.today() - timedelta(days=1)
        start = end - timedelta(days=15)
        job, created = runner.create_sync_job(session, "market_sync_job", {
            "universe": [str(inst.instrument_id)], "start": start.isoformat(), "end": end.isoformat(),
        })
        session.commit()
        log(f"[2] market_sync_job 创建: {job.job_run_id} (created={created})")
        result = await runner.run_market_sync(job.job_run_id, [inst.instrument_id], start, end)
        log(f"[2] 同步完成: bars={result.bars}, instruments={result.instruments}")

        # ---- 3. OHLCVA 查询（PG 指针 → DuckDB）----
        service = MarketDataService(parquet)
        rows = service.get_ohlcva(session, inst.instrument_id, start=start, end=end)
        if rows:
            latest = rows[-1]
            log(f"[3] OHLCVA 可查询: {len(rows)} 行, 最新 {latest['trade_date']} 收盘 {latest['close']} "
                f"provider={latest['provider']}")
            log(f"[3] as_of 裁剪验证: as_of={latest['trade_date']} → "
                f"{len(service.get_ohlcva(session, inst.instrument_id, as_of=latest['trade_date']))} 行")
        else:
            log("[3] 无行情（网络/数据缺口——按缺口语义处理）")

        # ---- 4. fundamental_sync_job（近 2 年营收/净利）----
        fjob, _ = runner.create_sync_job(session, "fundamental_sync_job", {
            "universe": [str(inst.instrument_id)],
            "metrics": ["REVENUE", "NET_INCOME"],
            "start_period": f"{end.year - 2}-01-01", "end_period": f"{end.year}-12-31",
        })
        session.commit()
        fres = await runner.run_fundamental_sync(
            fjob.job_run_id, [inst.instrument_id],
            ["REVENUE", "NET_INCOME"], date(end.year - 2, 1, 1), date(end.year, 12, 31))
        log(f"[4] fundamental_sync_job: facts={fres.bars}")

        # ---- 5. PIT 查询（as_of 可见性）----
        from app.fundamentals.repository import get_financial_fact_pit

        pit = get_financial_fact_pit(session, inst.instrument_id, "REVENUE",
                                     date(end.year - 1, 12, 31), end)
        if pit is not None:
            log(f"[5] PIT 查询: {pit.period_end} REVENUE = {pit.value:,.0f} 元 "
                f"(披露 {pit.published_at.date() if pit.published_at else 'N/A'})")
        else:
            log("[5] PIT 查询: 该期间未披露（合法缺口语义）")

        # ---- 6. provenance 反查 + raw 校验 ----
        from app.audit.models import ProvenanceRecord

        provs = session.execute(
            select(ProvenanceRecord).where(ProvenanceRecord.ingestion_run_id == job.job_run_id)
        ).scalars().all()
        ok_raw = sum(1 for p in provs if raw.verify(p.raw_hash, p.raw_object_key))
        log(f"[6] provenance 反查: {len(provs)} 条血缘（job={job.job_run_id}），"
            f"raw 校验通过 {ok_raw}/{len(provs)}")

        # ---- 7. 交易日历 / FX / 复权 ----
        from app.calendar.service import CalendarService
        from app.calendar.source import fetch_sina_trade_dates
        from app.corporate_actions.service import CorporateActionsService
        from app.fx.service import FXService

        cal = CalendarService()
        dates = fetch_sina_trade_dates()
        n = cal.sync_dates(session, dates)
        session.commit()
        today = date.today()
        log(f"[7a] 交易日历同步: {n} 行（新浪源），今天是否交易日="
            f"{cal.is_trading_day(session, today)}，下一交易日={cal.next_trading_day(session, today)}")

        fx = FXService(runner.gateway)
        fxr = await fx.sync_fx(session, end - timedelta(days=10), end)
        session.commit()
        log(f"[7b] FX 同步: written={fxr['written']} yahoo={fxr['yahoo']} fred={fxr['fred']} "
            f"deviations={fxr['deviations']}")
        rate = fx.get_fx_rate(session, end)
        if rate is not None:
            log(f"[7b] USD/CNY({end}) = {rate}")

        ca = CorporateActionsService(runner.gateway)
        factors = await runner.gateway.fetch_extension(
            "tushare", lambda p: p.get_adj_factors(inst.instrument_id,
                                                   date(end.year - 1, 1, 1), end))
        cfr = await ca.sync_adj_factors(session, inst.instrument_id, factors)
        dividends = await runner.gateway.fetch_extension(
            "tushare", lambda p: p.get_dividends(inst.instrument_id))
        nd = await ca.sync_dividends(session, inst.instrument_id, dividends)
        session.commit()
        consistent = ca.verify_adj_factor_consistency(session, inst.instrument_id, parquet)
        log(f"[7c] 复权/公司行动: 因子事件={cfr['events']}（写入 {cfr['written']}），"
            f"分红/送转写入 {nd}，ohlcva 一致性={consistent}")

    log("=" * 64)
    log("M1 验收演示完成 ✅")


if __name__ == "__main__":
    asyncio.run(main())
