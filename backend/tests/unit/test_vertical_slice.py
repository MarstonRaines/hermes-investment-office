# =====================================================================
# tests/unit/test_vertical_slice.py —— M1.5 Vertical Slice 端到端（ACC-M1.5-001）
#
# 单资产闭环：Instrument → Data → Fundamental → Valuation → Thesis →
# Paper Portfolio → Daily Brief（真实标的数据 + mock provider，真实 DB）。
# =====================================================================
from __future__ import annotations

import asyncio
import json
import os
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

from sqlalchemy import create_engine
from sqlalchemy.orm import Session as SASession

import app.models  # noqa: F401
from app.common.config import settings
from app.providers.contracts.base import (
    BaseProvider,
    ProvenanceEnvelope,
    ProviderCapability,
    ProviderHealth,
    ProviderQualityReport,
    ProviderRole,
    QualityTier,
)
from app.providers.contracts.fundamentals import FinancialFactResult
from app.providers.contracts.market_data import MarketBarResult

NOW = datetime(2026, 8, 21, tzinfo=UTC)

TEST_DB_URL = os.environ.get(
    "HERMES_TEST_DB_URL",
    "postgresql+psycopg2://hermes:hermes@127.0.0.1:5432/hermes_test",
)
_engine = create_engine(TEST_DB_URL, pool_pre_ping=True)


def job_session() -> SASession:
    return SASession(bind=_engine)


class SliceProvider(BaseProvider):
    """垂直切片 mock provider：行情 + 财务 + 复权全喂。"""
    provider_name = "tushare"
    display_name = "TuShare Pro"
    capabilities = frozenset({ProviderCapability.CN_DAILY_QUOTE,
                              ProviderCapability.CN_ETF_QUOTE,
                              ProviderCapability.ADJ_FACTOR,
                              ProviderCapability.FINANCIAL_STATEMENTS})
    default_role = ProviderRole.PRIMARY
    quality_tier = QualityTier.TIER_2
    known_limits = []
    bars: list = []
    facts: list = []

    def __init__(self, config=None, symbol_resolver=None) -> None:
        self.config = config
        self._resolve = symbol_resolver

    async def get_price_history(self, instrument_id, start, end, adjust=None):
        return [b for b in SliceProvider.bars if start <= b.trade_date <= end]

    async def get_adj_factors(self, instrument_id, start, end):
        return []

    async def get_financial_history(self, instrument_id, metrics, start_period, end_period):
        return [f for f in SliceProvider.facts if start_period <= f.period_end <= end_period]

    async def health_check(self) -> ProviderHealth:
        return ProviderHealth(provider="tushare", status="HEALTHY", checked_at=NOW)

    async def quality_report(self) -> ProviderQualityReport:
        return ProviderQualityReport(provider="tushare", tier=self.quality_tier,
                                     quality_score=Decimal("0.96"))


def _bar(iid, td: date, close: str) -> MarketBarResult:
    return MarketBarResult(
        instrument_id=iid, trade_date=td, close=Decimal(close), provider="tushare",
        provenance=ProvenanceEnvelope(
            source="cn_daily_market", provider="tushare",
            source_record_id=f"tushare@{iid}@{td.isoformat()}",
            observed_at=NOW, retrieved_at=NOW, as_of_date=td,
            quality_score=Decimal("0.96"), quality_status="VERIFIED",
            transform_version="market-normalizer/0.1.0",
        ),
    )


def _fact(iid, metric: str, value: str, period="2025-12-31", statement="INCOME",
          published="2026-04-16") -> FinancialFactResult:
    return FinancialFactResult(
        instrument_id=iid, metric_code=metric,
        period_end=date.fromisoformat(period), period_type="FY",
        statement_type=statement,
        published_at=datetime.fromisoformat(f"{published}T00:00:00+08:00"),
        retrieved_at=NOW, original_value=Decimal(value), original_unit="元",
        value=Decimal(value), unit="CNY",
        provenance=ProvenanceEnvelope(
            source="cn_financial_statements", provider="tushare",
            source_record_id=f"tushare@{iid}@{period}@{published}",
            published_at=datetime.fromisoformat(f"{published}T00:00:00+08:00"),
            observed_at=NOW, retrieved_at=NOW, as_of_date=date.fromisoformat(period),
            quality_score=Decimal("0.96"), quality_status="VERIFIED",
            transform_version="fundamental-normalizer/0.1.0",
        ),
    )


def test_vertical_slice_full_loop(tmp_path) -> None:
    """ACC-M1.5-001：Instrument→Data→Fundamental→Valuation→Thesis→PAPER→Brief 全闭环。"""
    from app.briefing.service import BriefingService
    from app.calendar.service import CalendarService
    from app.instruments.models import Instrument, ProviderSymbol
    from app.jobs.sync_jobs import build_sync_runner
    from app.market_data.parquet import ParquetStore
    from app.market_data.service import MarketDataService
    from app.portfolio.service import PortfolioService
    from app.providers.capability_matrix import load_capability_matrix
    from app.providers.factory import ProviderFactory
    from app.providers.raw_store import RawEvidenceStore
    from app.providers.registry import ProviderRegistry
    from app.providers.runtime_config import RuntimeProviderConfigs
    from app.thesis.service import ThesisService
    from app.valuation.engine import ValuationAssumptionInput
    from app.valuation.service import ValuationRequest, ValuationService

    golden = json.loads(
        (Path(__file__).resolve().parents[1] / "golden" / "valuation_golden.json").read_text()
    )["cases"][0]

    session = job_session()
    try:
        # ---- 1. Instrument ----
        suffix = uuid4().hex[:8]
        inst = Instrument(instrument_type="CN_EQUITY", symbol=f"V{suffix}",
                          name="切片标的", market="SSE", currency="CNY")
        session.add(inst)
        session.flush()
        session.add(ProviderSymbol(
            instrument_id=inst.instrument_id, provider="tushare",
            symbol=f"V{suffix}.SH", valid_from=date(2020, 1, 1)))
        session.commit()
        iid = inst.instrument_id

        # ---- 2/3. Data + Fundamental（真实 pipeline：gateway → raw → parquet/PG）----
        matrix = load_capability_matrix(settings.provider_capability_path)
        reg = ProviderRegistry(matrix)
        reg.register(SliceProvider)
        from app.providers.bootstrap import ALL_PROVIDERS

        for cls in ALL_PROVIDERS:      # 矩阵链完整性（akshare_sina 等）
            if cls.provider_name not in reg.all():
                reg.register(cls)
        runtime = RuntimeProviderConfigs(providers={
            "tushare": {"max_retries": 1, "retry_backoff_base": 0.01,
                        "rate_limit": {"qps": 1000.0, "burst": 10, "daily_quota": 100000}},
            "akshare_sina": {"max_retries": 1, "retry_backoff_base": 0.01,
                             "rate_limit": {"qps": 1000.0, "burst": 10, "daily_quota": 100000}},
        })
        factory = ProviderFactory(reg, runtime, matrix, session_factory=job_session)
        parquet = ParquetStore(tmp_path / "parquet")
        raw = RawEvidenceStore(tmp_path / "data")
        runner = build_sync_runner(job_session, factory, reg, parquet, raw)
        market_service = MarketDataService(parquet)

        SliceProvider.bars = [_bar(iid, date(2026, 8, 20), "100"),
                              _bar(iid, date(2026, 8, 21), "101")]
        job, _ = runner.create_sync_job(session, "market_sync_job",
                                        {"universe": [str(iid)], "start": "2026-08-01",
                                         "end": "2026-08-31"})
        session.commit()
        result = asyncio.run(runner.run_market_sync(job.job_run_id, [iid],
                                                    date(2026, 8, 1), date(2026, 8, 31)))
        assert result.bars == 2
        rows = market_service.get_ohlcva(session, iid, as_of=date(2026, 8, 21))
        assert rows[-1]["close"] == 101.0      # 最新交易日收盘

        # 财务：NI=100, SHARES=10, EQUITY=200（黄金值同构）
        SliceProvider.facts = [
            _fact(iid, "NET_INCOME", "100"),
            _fact(iid, "SHARES_OUTSTANDING", "10", statement="OTHER"),
            _fact(iid, "TOTAL_EQUITY", "200", statement="BALANCE"),
        ]
        fjob, _ = runner.create_sync_job(session, "fundamental_sync_job",
                                         {"universe": [str(iid)], "metrics": ["NET_INCOME"],
                                          "start_period": "2025-01-01", "end_period": "2025-12-31"})
        session.commit()
        fres = asyncio.run(runner.run_fundamental_sync(fjob.job_run_id, [iid],
                                                       ["NET_INCOME", "SHARES_OUTSTANDING", "TOTAL_EQUITY"],
                                                       date(2025, 1, 1), date(2025, 12, 31)))
        assert fres.bars >= 3

        # ---- 4. Valuation（DCF，黄金值输入）----
        valuation = ValuationService(market_service)
        assumptions = [ValuationAssumptionInput(**a) for a in golden["input"]["assumptions"]]
        run = valuation.run_valuation(session, ValuationRequest(
            instrument_id=iid, model_type="DCF", as_of=date(2026, 8, 21),
            assumptions=assumptions,
            fcf_forecast=[Decimal(str(f)) for f in golden["input"]["fcf_forecast"]],
        ))
        assert run.status == "COMPLETED"
        assert run.base_value > 0

        # ---- 5. Thesis（DRAFT → ACTIVE，关联估值）----
        thesis_svc = ThesisService()
        thesis = thesis_svc.create_thesis(session, iid, "切片 Thesis", {"price": 101})
        session.flush()
        from app.common.enums import ThesisLifecycleStatus

        thesis_svc.transition_lifecycle(session, thesis.thesis_id, ThesisLifecycleStatus.ACTIVE,
                                        actor="HUMAN", reason="切片验收")
        session.commit()

        # ---- 6. Paper Portfolio（入金 → 模拟买入 → 快照）----
        pf_svc = PortfolioService()
        pf = pf_svc.create_portfolio(session, "切片组合")
        session.flush()
        from app.common.enums import TransactionType

        pf_svc.record_transaction(session, pf.portfolio_id, TransactionType.CASH_IN,
                                  amount_cny=Decimal("10000"),
                                  trade_date=date(2026, 8, 20))
        pf_svc.simulate_paper_trade(session, pf.portfolio_id, TransactionType.BUY,
                                    instrument_id=iid, quantity=Decimal("50"),
                                    price_cny=Decimal("100"), trade_date=date(2026, 8, 21))
        session.flush()
        snap = pf_svc.snapshot(session, pf.portfolio_id, date(2026, 8, 21),
                               {iid: Decimal("101")})
        assert snap["nav_cny"] == Decimal("10050.0000")    # 5000 现金 + 50×101
        session.commit()

        # ---- 7. Daily Brief（context + brief）----
        # market_date 全局唯一（uq_daily_contexts_market_date）：按运行取唯一日期
        market_date = date(2026, 8, 21) + __import__("datetime").timedelta(
            days=int(uuid4().hex[:2], 16))
        brief_svc = BriefingService(market_service, CalendarService())
        ctx = brief_svc.build_daily_context(session, market_date, instruments=[iid])
        session.flush()
        brief = brief_svc.save_daily_brief(
            session, ctx.daily_context_id, market_date,
            "# 切片日报\n- 估值 base=3529.16\n- 持仓 50 股",
            sections=[{"id": "valuation", "value": str(run.base_value)},
                      {"id": "position", "quantity": "50"}],
            model_profile="fast")
        session.commit()

        # ---- 闭环断言 ----
        from app.briefing.models import DailyBrief, DailyContext
        from app.market_data.models import MarketBarIndex
        from app.valuation.models import ValuationRun

        assert (session.query(MarketBarIndex)
                .filter(MarketBarIndex.instrument_id == iid).count()) == 2
        assert session.get(ValuationRun, run.valuation_run_id).status == "COMPLETED"
        assert session.get(DailyContext, ctx.daily_context_id) is not None
        saved = session.get(DailyBrief, brief.daily_brief_id)
        assert saved is not None and saved.model_profile == "fast"
        assert saved.sections[0]["value"] == str(run.base_value)
    finally:
        session.close()
