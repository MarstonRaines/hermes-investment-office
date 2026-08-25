"""本地维护命令：初始化与一次性刷新。"""

from __future__ import annotations

import argparse
import asyncio
import logging
from datetime import date, timedelta

from sqlalchemy import select

from app.etf.models import ETFProfile
from app.instruments.models import Instrument
from app.jobs.sync_jobs import ETF_JOB, MACRO_JOB
from app.operations.bootstrap import ensure_product_defaults

logger = logging.getLogger(__name__)


def _runtime():
    from app.main import MCP_APP

    return MCP_APP.state


def _run_sync_job(runtime, name: str, params: dict) -> str:
    with runtime.session_factory() as session:
        job, created = runtime.sync_runner.create_sync_job(session, name, params)
        session.commit()
        job_id = job.job_run_id
        status = str(job.status)
    if not created:
        return f"{name}: {status}（已存在）"
    try:
        result = asyncio.run(runtime.sync_runner.run_job(job_id))
        return f"{name}: SUCCEEDED（写入 {result.bars} 项）"
    except Exception as exc:  # noqa: BLE001
        logger.exception("同步失败: %s", name)
        return f"{name}: FAILED（{type(exc).__name__}: {exc}）"


def bootstrap() -> int:
    runtime = _runtime()
    with runtime.session_factory() as session:
        result = ensure_product_defaults(session)
    print(f"本地产品初始化完成：{result}")
    return 0


def refresh() -> int:
    runtime = _runtime()
    with runtime.session_factory() as session:
        ensure_product_defaults(session)
    today = date.today()
    try:
        calendar = runtime.backend_scheduler.run_calendar_sync_job(today)
        print(f"calendar_sync_job: {calendar.status}")
    except Exception as exc:  # noqa: BLE001
        print(f"calendar_sync_job: FAILED（{type(exc).__name__}: {exc}）")

    universe = list(runtime.scheduler_universe_provider())
    start = today - timedelta(days=1095)
    try:
        market = runtime.backend_scheduler.run_market_sync_job(
            today,
            universe,
            lookback_days=1095,
        )
        print(f"market_sync_job: {market.status}")
    except Exception as exc:  # noqa: BLE001
        print(f"market_sync_job: FAILED（{type(exc).__name__}: {exc}）")

    with runtime.session_factory() as session:
        equity_ids = list(session.scalars(select(Instrument.instrument_id).where(
            Instrument.instrument_id.in_(universe) if universe else False,
            Instrument.instrument_type == "CN_EQUITY",
        )).all())
    if equity_ids:
        try:
            fundamentals = runtime.backend_scheduler.run_fundamental_sync_job(
                today,
                equity_ids,
                lookback_years=2,
            )
            print(f"fundamental_sync_job: {fundamentals.status}")
        except Exception as exc:  # noqa: BLE001
            print(f"fundamental_sync_job: FAILED（{type(exc).__name__}: {exc}）")
    if universe:
        try:
            corporate_actions = runtime.backend_scheduler.run_corporate_action_sync_job(
                today,
                universe,
                lookback_years=3,
            )
            print(f"corporate_action_sync_job: {corporate_actions.status}")
        except Exception as exc:  # noqa: BLE001
            print(f"corporate_action_sync_job: FAILED（{type(exc).__name__}: {exc}）")

    with runtime.session_factory() as session:
        profiles = list(session.scalars(select(ETFProfile).where(
            ETFProfile.instrument_id.in_(universe) if universe else False,
        )).all())
    etf_ids = [row.instrument_id for row in profiles]
    qdii_index_ids = sorted(
        {row.underlying_index_id for row in profiles if row.underlying_index_id},
        key=str,
    )
    if etf_ids:
        print(_run_sync_job(runtime, ETF_JOB, {
            "universe": [str(value) for value in etf_ids],
            "start_date": start.isoformat(),
            "end_date": today.isoformat(),
            "data_type": "ETF",
        }))
    if qdii_index_ids:
        print(_run_sync_job(runtime, MACRO_JOB, {
            "universe": [str(value) for value in qdii_index_ids],
            "index_ids": [str(value) for value in qdii_index_ids],
            "instruments": [str(value) for value in etf_ids],
            "start_date": start.isoformat(),
            "end_date": today.isoformat(),
            "data_type": "MACRO",
        }))

    results = runtime.backend_scheduler.run_daily_pipeline(today, universe)
    if all(result.skipped for result in results.values()):
        with runtime.session_factory() as session:
            runtime.briefing_service.build_daily_context(
                session,
                today,
                instruments=universe,
                engine_versions={"manual_refresh": "app-cli/0.1.0"},
            )
            session.commit()
    print("daily_pipeline:", {name: result.status for name, result in results.items()})
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Hermes 本地维护命令")
    parser.add_argument("command", choices=("bootstrap", "refresh"))
    args = parser.parse_args()
    return bootstrap() if args.command == "bootstrap" else refresh()


if __name__ == "__main__":
    raise SystemExit(main())
