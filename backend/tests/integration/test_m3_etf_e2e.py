from __future__ import annotations

import asyncio
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import app.models  # noqa: F401
from app.calendar.service import CalendarService
from app.common.enums import MarketCode, QuotaStatus
from app.common.gateway import GatewayFetch
from app.etf.config import load_qdii_alignment_config, load_valuation_band_config
from app.etf.models import ETFProfile
from app.etf.service import ETFDataService
from app.instruments.models import Instrument
from app.macro.service import MacroDataService
from app.market_data.parquet import ParquetStore
from app.market_data.repository import persist_market_bars
from app.providers.contracts.base import ProvenanceEnvelope
from app.providers.contracts.etf import (
    HoldingItem,
    HoldingSnapshotResult,
    NavResult,
    QuotaStatusResult,
)
from app.providers.contracts.macro import FxRateResult, IndexBarResult, IndexValuationResult
from app.providers.contracts.market_data import MarketBarResult

NOW = datetime(2026, 8, 24, 12, tzinfo=UTC)
MARKET_DATE = date(2026, 8, 24)


def _env(source: str, provider: str = "fixture") -> ProvenanceEnvelope:
    return ProvenanceEnvelope(
        source=source, provider=provider, observed_at=NOW, retrieved_at=NOW,
        as_of_date=MARKET_DATE, quality_score=Decimal("0.99"),
        quality_status="VERIFIED", transform_version="fixture/1",
    )


class FixtureETF:
    def __init__(self, ids: list, index_id):
        self.ids = ids
        self.index_id = index_id

    async def fetch_nav_history(self, instrument_id):
        return GatewayFetch(
            rows=[NavResult(
                instrument_id=instrument_id, nav_date=MARKET_DATE - timedelta(days=1),
                nav=Decimal("4.0000") if instrument_id != self.ids[1] else Decimal("1.0000"),
                published_at=NOW, retrieved_at=NOW, provenance=_env("fund_nav"),
            )], actual_provider="fixture", requested_provider="fixture",
        )

    async def fetch_holdings(self, instrument_id):
        return GatewayFetch(rows=[HoldingSnapshotResult(
            instrument_id=instrument_id, report_period=date(2026, 6, 30),
            disclosure_date=date(2026, 7, 20), source="QUARTERLY",
            holdings=[HoldingItem(rank=1, provider_symbol="600519.SH", weight_pct=Decimal("5.0"))],
            disclosure_completeness="TOP_N", provenance=_env("holdings"),
        )], actual_provider="fixture", requested_provider="fixture")

    async def fetch_quota(self, instrument_id):
        status = QuotaStatus.UNKNOWN if instrument_id == self.ids[1] else QuotaStatus.NOT_APPLICABLE
        return GatewayFetch(rows=[QuotaStatusResult(
            instrument_id=instrument_id, quota_status=status,
            announcement_date=MARKET_DATE - timedelta(days=1), provenance=_env("quota"),
        )], actual_provider="fixture", requested_provider="fixture")


class FixtureMacro:
    def __init__(self, index_id):
        self.index_id = index_id

    async def fetch_index_history(self, index_id, _start, _end):
        rows = [IndexBarResult(
            index_id=index_id, trade_date=date(2026, 8, 21), close=Decimal("100"),
            currency="USD", provenance=_env("index"),
        ), IndexBarResult(
            index_id=index_id, trade_date=MARKET_DATE, close=Decimal("101"),
            currency="USD", provenance=_env("index"),
        )]
        return GatewayFetch(rows=rows, actual_provider="fixture", requested_provider="fixture")

    async def fetch_fx_rates(self, _start, _end):
        rows = [FxRateResult(
            rate=Decimal("7.20"), as_of=datetime(2026, 8, 21, 10, tzinfo=UTC),
            trade_date=date(2026, 8, 21), provenance=_env("fx"),
        ), FxRateResult(
            rate=Decimal("7.21"), as_of=NOW, trade_date=MARKET_DATE,
            provenance=_env("fx"),
        )]
        return GatewayFetch(rows=rows, actual_provider="fixture", requested_provider="fixture")

    async def fetch_index_valuation(self, index_id, _start, _end):
        rows = [IndexValuationResult(
            index_id=index_id, as_of_date=date(2026, 8, 1) + timedelta(days=offset),
            pe=Decimal("10") + Decimal(offset) / Decimal("10"), pb=Decimal("1.2"),
            source="fixture", provenance=_env("index_valuation"),
        ) for offset in range(20)]
        return GatewayFetch(rows=rows, actual_provider="fixture", requested_provider="fixture")


def test_three_etf_db_backed_sync_pit_and_versioned_read(db_session, tmp_path) -> None:
    instruments = [
        Instrument(instrument_type="CN_ETF", symbol="510300", name="沪深300ETF", market="SSE", currency="CNY"),
        Instrument(instrument_type="CN_ETF", symbol="513650", name="标普500ETF", market="SSE", currency="CNY"),
        Instrument(instrument_type="CN_ETF", symbol="512890", name="红利低波ETF", market="SSE", currency="CNY"),
    ]
    index = Instrument(instrument_type="INDEX", symbol="^GSPC", name="S&P 500", market="SSE", currency="USD")
    db_session.add_all([*instruments, index])
    db_session.flush()
    db_session.add_all([
        ETFProfile(instrument_id=instruments[0].instrument_id, is_qdii=False),
        ETFProfile(instrument_id=instruments[1].instrument_id, is_qdii=True, underlying_index_id=index.instrument_id),
        ETFProfile(instrument_id=instruments[2].instrument_id, is_qdii=False),
    ])
    calendar = CalendarService()
    dates = [MARKET_DATE - timedelta(days=offset) for offset in range(30)]
    calendar.sync_dates(db_session, dates, market=MarketCode.CN)
    calendar.sync_dates(db_session, dates, market=MarketCode.US)
    store = ParquetStore(tmp_path / "parquet")
    bars = [MarketBarResult(
        instrument_id=row.instrument_id, trade_date=MARKET_DATE, close=Decimal("4.05"),
        provider="fixture", pct_change=Decimal("0.5"), provenance=_env("market"),
    ) for row in instruments]
    persist_market_bars(db_session, bars, parquet_store=store)
    db_session.flush()

    etf = ETFDataService(
        FixtureETF([row.instrument_id for row in instruments], index.instrument_id), store,
        band_config=load_valuation_band_config("config/etf-valuation-band.yaml"),
        alignment_config=load_qdii_alignment_config("config/qdii-alignment.yaml"),
        calendar=calendar,
    )
    macro = MacroDataService(FixtureMacro(index.instrument_id), store)
    for row in instruments:
        asyncio.run(etf.sync_nav(db_session, row.instrument_id))
        asyncio.run(etf.sync_holdings(db_session, row.instrument_id))
        summary = asyncio.run(etf.sync_quota(db_session, row.instrument_id))
        asyncio.run(etf.refresh_metrics(
            db_session, row.instrument_id, as_of=NOW,
            quota_status=summary.quota_status,
            quota_provenance_ids=summary.provenance_ids,
            quota_observed_at=summary.quota_observed_at,
        ))
    asyncio.run(macro.sync_index_history(db_session, index.instrument_id, date(2026, 8, 1), MARKET_DATE))
    asyncio.run(macro.sync_index_valuation(db_session, index.instrument_id, date(2026, 8, 1), MARKET_DATE))
    asyncio.run(macro.sync_fx(db_session, date(2026, 8, 1), MARKET_DATE))
    # Recompute QDII after macro facts are present; the metric is a persisted PIT read.
    asyncio.run(etf.refresh_metrics(db_session, instruments[1].instrument_id, as_of=NOW))
    db_session.flush()

    for row in instruments:
        metric = etf.read_metric(db_session, row.instrument_id, as_of=NOW)
        assert metric is not None
        assert metric.provenance_id is not None
        assert etf.read_holdings(db_session, row.instrument_id, as_of=MARKET_DATE)
    qdii = etf.read_metric(db_session, instruments[1].instrument_id, as_of=NOW)
    assert qdii.quota_status == QuotaStatus.UNKNOWN.value
    assert qdii.details["level_1"]["status"] == "DISCLOSED"
    assert "parquet_path" not in qdii.details["level_1"]
    assert (tmp_path / "parquet" / "etf_holdings" / "v2").exists()
    assert not (tmp_path / "parquet" / "etf_holdings" / "v1").exists()
