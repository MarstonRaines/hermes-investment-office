# =====================================================================
# tests/unit/test_briefing_service.py —— Daily Brief 最小版
# =====================================================================
from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

import app.models  # noqa: F401
from app.briefing.service import BriefingDomainError, BriefingService
from app.calendar.service import CalendarService
from app.market_data.repository import persist_market_bars
from app.market_data.service import MarketDataService
from app.providers.contracts.base import ProvenanceEnvelope
from app.providers.contracts.market_data import MarketBarResult

NOW = datetime(2026, 8, 21, tzinfo=UTC)


def _md(offset: int) -> date:
    """唯一 market_date（daily_contexts 按 market_date 全局唯一）。"""
    return date(2026, 8, 21) + __import__("datetime").timedelta(days=offset)


def _service(session) -> BriefingService:
    return BriefingService(MarketDataService(None))


def test_build_daily_context_ok(db_session, instrument) -> None:
    """market 有数据 → OK 状态。"""
    svc = _service(db_session)
    # 给日历 + 行情
    cal = CalendarService()
    cal.sync_dates(db_session, [date(2026, 8, 20), date(2026, 8, 21)])
    db_session.flush()
    ctx = svc.build_daily_context(db_session, _md(10),
                                  instruments=[instrument.instrument_id])
    assert ctx.freshness_status == "STALE"      # 无行情 → market STALE 主导
    assert ctx.data_freshness["market"]["status"] == "STALE"


def test_build_daily_context_with_data(db_session, instrument, tmp_path) -> None:
    """行情 + 财务 + fx 齐备 → OK（freshness 门禁的输入）。"""
    from app.audit.models import ProvenanceRecord
    from app.fundamentals.models import FinancialFact
    from app.fx.models import FXObservation
    from app.market_data.parquet import ParquetStore

    store = ParquetStore(tmp_path / "parquet")
    svc = BriefingService(MarketDataService(store), CalendarService())
    svc.calendar.sync_dates(db_session, [date(2026, 8, 20), date(2026, 8, 21)])
    bar = MarketBarResult(
        instrument_id=instrument.instrument_id, trade_date=date(2026, 8, 21),
        close=Decimal("100"), provider="tushare",
        provenance=ProvenanceEnvelope(
            source="cn_daily_market", provider="tushare",
            source_record_id=f"tushare@{instrument.instrument_id}@2026-08-21",
            observed_at=NOW, retrieved_at=NOW, as_of_date=date(2026, 8, 21),
            quality_score=Decimal("0.96"), quality_status="VERIFIED",
            transform_version="market-normalizer/0.1.0",
        ),
    )
    persist_market_bars(db_session, [bar], parquet_store=store)
    db_session.flush()
    prov = ProvenanceRecord(source_kind="PROVIDER", source="cn_financial_statements",
                            provider="tushare", observed_at=NOW, retrieved_at=NOW,
                            quality_score=Decimal("0.96"), quality_status="VERIFIED",
                            transform_version="v1")
    db_session.add(prov)
    db_session.flush()
    db_session.add(FinancialFact(
        instrument_id=instrument.instrument_id, metric_code="REVENUE",
        period_end=date(2025, 12, 31), statement_type="INCOME",
        published_at=datetime(2026, 4, 16, tzinfo=UTC), retrieved_at=NOW,
        value=Decimal("100"), unit="CNY", provider="tushare",
        provenance_id=prov.provenance_id, quality_status="VERIFIED",
    ))
    db_session.add(FXObservation(
        base_currency="USD", quote_currency="CNY", rate=Decimal("6.71"),
        as_of=datetime.now(UTC), provider="yahoo", provenance_id=prov.provenance_id,
    ))
    db_session.flush()

    ctx = svc.build_daily_context(db_session, _md(11),
                                  instruments=[instrument.instrument_id])
    assert ctx.freshness_status == "OK"
    assert ctx.data_freshness["market"]["status"] == "OK"
    assert ctx.data_freshness["fundamental"]["status"] == "OK"
    assert ctx.data_freshness["fx"]["status"] == "OK"
    assert set(ctx.data_freshness) == {
        "market", "fundamental", "etf_nav", "etf_holdings", "index", "fx", "quota",
    }


def test_get_daily_context_idempotent(db_session, instrument) -> None:
    svc = _service(db_session)
    ctx = svc.build_daily_context(db_session, _md(12), instruments=[instrument.instrument_id])
    db_session.flush()
    got = svc.get_daily_context(db_session, _md(12))
    assert got.daily_context_id == ctx.daily_context_id
    assert svc.get_daily_context(db_session, _md(12).__class__(2026, 8, 20)) is None


def test_save_daily_brief_requires_profile(db_session, instrument) -> None:
    svc = _service(db_session)
    ctx = svc.build_daily_context(db_session, _md(13), instruments=[instrument.instrument_id])
    db_session.flush()
    brief = svc.save_daily_brief(db_session, ctx.daily_context_id, _md(13),
                                 "# 日报", sections=[{"id": "market", "title": "行情"}],
                                 model_profile="fast")
    db_session.flush()
    assert brief.status == "DRAFT"
    assert brief.model_profile == "fast"
    try:
        svc.save_daily_brief(db_session, ctx.daily_context_id, _md(13), "x",
                             model_profile="")
        raise AssertionError("should raise")
    except BriefingDomainError:
        pass
