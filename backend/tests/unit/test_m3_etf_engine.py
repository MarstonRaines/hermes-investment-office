from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from types import SimpleNamespace
from uuid import uuid4

from app.audit.models import ProvenanceRecord
from app.common.enums import DataQualityStatus, MarketCode, QuotaStatus
from app.etf.config import FreshnessThresholdConfig, load_valuation_band_config
from app.etf.engine import ETFEngine, ETFMetricInput
from app.etf.models import ETFHoldingSnapshot, ETFMetricSnapshot, ETFProfile
from app.etf.service import (
    ETFDataService,
    _decimal,
    _freshness,
    _freshness_domains,
    _holding_metadata,
)
from app.instruments.models import Instrument
from app.market_data.parquet import ParquetStore


def _engine() -> ETFEngine:
    return ETFEngine(
        band_config=load_valuation_band_config("config/etf-valuation-band.yaml")
    )


def _qdii(**overrides) -> ETFMetricInput:
    values = {
        "instrument_id": uuid4(),
        "as_of": datetime(2026, 8, 24, 16, tzinfo=UTC),
        "market_date": date(2026, 8, 24),
        "market_price_cny": Decimal("1.10"),
        "is_qdii": True,
        "underlying_index_id": uuid4(),
        "nav": Decimal("1.00"),
        "nav_date": date(2026, 8, 24),
        "reference_nav_basis": "OFFICIAL_NAV_T1",
        "underlying_session_date": date(2026, 8, 24),
        "index_close": Decimal("110"),
        "index_previous_close": Decimal("100"),
        "fx_rate": Decimal("7.7"),
        "fx_previous_rate": Decimal("7.0"),
        "fx_as_of": datetime(2026, 8, 24, 21, tzinfo=UTC),
        "market_nav_distance": 0,
        "underlying_market_distance": 0,
        "fx_underlying_distance": 0,
        "nav_underlying_distance": 0,
        "pe_percentile": Decimal("0.20"),
        "quota_status": QuotaStatus.UNKNOWN,
    }
    values.update(overrides)
    return ETFMetricInput(**values)


def test_qdii_formula_uses_decimal_and_preserves_components() -> None:
    output = _engine().compute(_qdii())

    assert output.premium_discount == Decimal("0.1")
    assert output.r_usd == Decimal("0.1")
    assert output.fx_chg == Decimal("0.1")
    assert output.r_cny == Decimal("0.21")
    assert output.fx_contribution == Decimal("0.11")
    assert output.quota_status == QuotaStatus.UNKNOWN


def test_nav_alignment_failure_returns_null_with_flag() -> None:
    output = _engine().compute(_qdii(market_nav_distance=2))

    assert output.premium_discount is None
    assert "NAV_TIME_ALIGNMENT_FAILED" in output.quality_flags


def test_missing_fx_never_produces_fx_contribution() -> None:
    output = _engine().compute(_qdii(fx_rate=None))

    assert output.fx_contribution is None
    assert "FX_MISSING" in output.quality_flags


def test_valuation_band_boundaries_are_config_driven() -> None:
    engine = _engine()
    assert engine.compute(_qdii(pe_percentile=Decimal("0.19"))).valuation_band == "VERY_CHEAP"
    assert engine.compute(_qdii(pe_percentile=Decimal("0.20"))).valuation_band == "CHEAP"
    assert engine.compute(_qdii(pe_percentile=Decimal("0.40"))).valuation_band == "FAIR"
    assert engine.compute(_qdii(pe_percentile=Decimal("0.60"))).valuation_band == "EXPENSIVE"
    assert engine.compute(_qdii(pe_percentile=Decimal("0.80"))).valuation_band == "VERY_EXPENSIVE"


def test_missing_valuation_percentile_is_explicit_gap() -> None:
    output = _engine().compute(_qdii(pe_percentile=None))

    assert output.valuation_band is None
    assert "INDEX_VALUATION_UNAVAILABLE" in output.quality_flags


def test_premium_does_not_infer_quota_status() -> None:
    output = _engine().compute(_qdii(quota_status=QuotaStatus.UNKNOWN))

    assert output.premium_discount == Decimal("0.1")
    assert output.quota_status == QuotaStatus.UNKNOWN


def test_level1_confidence_uses_disclosure_completeness() -> None:
    row = ETFHoldingSnapshot(
        holding_snapshot_id=uuid4(),
        instrument_id=uuid4(),
        report_period=date(2026, 6, 30),
        disclosure_date=date(2026, 8, 20),
        source="QUARTERLY",
        holding_count=1,
        holdings_json={"disclosure_completeness": "TOP_N"},
        parquet_path="parquet/holdings.parquet",
        provenance_id=uuid4(),
    )
    assert _holding_metadata(row)["confidence"] == "0.6"

    row.holdings_json = {"disclosure_completeness": "FULL"}
    assert _holding_metadata(row)["confidence"] == "0.9"


def test_freshness_exposes_domains_and_aggregates_worst_status() -> None:
    stale = SimpleNamespace(
        provenance_id=uuid4(),
        quality_status=DataQualityStatus.STALE,
        quality_flags=["STALE_INPUT"],
    )
    domains = {
        name: {
            "latest": date(2026, 8, 24),
            "latest_key": name,
            "present": True,
            "required": True,
            "applicable": True,
            "records": [],
        }
        for name in ("market", "etf_nav", "etf_holdings", "index", "fx", "quota")
    }
    domains["index"]["records"] = [stale]
    domains["index"]["required_action"] = "resync: macro_sync_job"
    domains["fx"]["required_action"] = "resync: macro_sync_job"
    result = _freshness(
        datetime(2026, 8, 24, 16, tzinfo=UTC),
        date(2026, 8, 24),
        DataQualityStatus.VERIFIED,
        ["FX_TIME_ALIGNMENT_FAILED"],
        domains=domains,
    )
    assert set(result["domains"]) == {
        "market", "etf_nav", "etf_holdings", "index", "fx", "quota"
    }
    assert result["domains"]["index"]["status"] == "STALE"
    assert result["domains"]["fx"]["status"] == "WARNING"
    assert result["domains"]["fx"]["required_action"] == "resync: macro_sync_job"
    assert result["overall"] == "STALE"


def test_unknown_quota_is_warning_not_failed() -> None:
    domains = {
        name: {
            "latest": date(2026, 8, 24),
            "expected": date(2026, 8, 24),
            "latest_key": name,
            "expected_key": f"expected_{name}",
            "lag_sessions": 0,
            "thresholds": {"warn_lag_sessions": 1, "stale_lag_sessions": 2},
            "present": True,
            "required": True,
            "applicable": True,
            "records": [],
        }
        for name in ("market", "etf_nav", "etf_holdings", "index", "fx")
    }
    domains["quota"] = {
        "latest": None,
        "expected": None,
        "latest_key": "latest_observed_at",
        "expected_key": "expected_observed_at",
        "thresholds": {},
        "present": False,
        "required": True,
        "applicable": True,
        "unknown": True,
        "flags": ["QUOTA_STATUS_UNKNOWN"],
        "required_action": "confirm_quota_status",
        "extra": {"quota_status": "UNKNOWN", "source_validity": "unknown"},
        "records": [SimpleNamespace(
            provenance_id=uuid4(),
            quality_status=DataQualityStatus.REJECTED,
            quality_flags=["QUOTA_SOURCE_UNAVAILABLE"],
        )],
    }
    result = _freshness(
        datetime(2026, 8, 24, 16, tzinfo=UTC),
        date(2026, 8, 24),
        DataQualityStatus.VERIFIED,
        [],
        domains=domains,
    )
    assert result["overall"] == "WARNING"
    assert result["domains"]["quota"]["status"] == "WARNING"
    assert result["domains"]["quota"]["source_validity"] == "unknown"
    assert result["domains"]["quota"]["required_action"] == "confirm_quota_status"
    assert "confirm official status" in result["domains"]["quota"]["detail"]


def test_freshness_uses_separate_cn_us_calendar_sessions() -> None:
    sessions = {
        MarketCode.CN: [date(2026, 8, 20), date(2026, 8, 21), date(2026, 8, 24)],
        MarketCode.US: [date(2026, 8, 20), date(2026, 8, 21), date(2026, 8, 24)],
    }

    class Calendar:
        def is_trading_day(self, _session, value, market):
            return value in sessions[market]

        def prev_trading_day(self, _session, value, market):
            return max((item for item in sessions[market] if item < value), default=None)

        def trading_day_distance(self, _session, left, right, *, market):
            if left is None or right is None:
                return None
            values = sessions[market]
            if left not in values or right not in values:
                return None
            return abs(values.index(left) - values.index(right))

    as_of = datetime(2026, 8, 24, 16, tzinfo=UTC)
    domains = _freshness_domains(
        as_of=as_of,
        session=object(),
        calendar=Calendar(),
        instrument_id=uuid4(),
        thresholds=FreshnessThresholdConfig(),
        profile=SimpleNamespace(is_qdii=True),
        market_date=date(2026, 8, 21),
        expected_market_date=date(2026, 8, 24),
        expected_us_date=date(2026, 8, 24),
        market_present=True,
        market_records=[],
        nav_date=date(2026, 8, 20),
        nav_present=True,
        nav_records=[],
        holding_metadata={
            "as_of_date": "2026-08-21",
            "report_period": "2026-06-30",
            "completeness": "TOP_N",
        },
        holding_records=[],
        underlying_date=date(2026, 8, 20),
        index_present=True,
        index_records=[],
        fx_as_of=datetime(2026, 8, 21, 9, tzinfo=UTC),
        fx_trade_date=date(2026, 8, 20),
        fx_present=True,
        fx_records=[],
        quota_status=QuotaStatus.UNKNOWN,
        quota_provenance_ids=(),
        quota_observed_at=None,
        quota_records=[],
    )
    result = _freshness(
        as_of, date(2026, 8, 21), DataQualityStatus.ACCEPTABLE, [], domains=domains
    )
    assert result["domains"]["market"]["lag"]["sessions"] == 1
    assert result["domains"]["etf_nav"]["lag"]["sessions"] == 2
    assert result["domains"]["quota"]["status"] == "WARNING"
    assert result["overall"] == "WARNING"

    domains["quota"]["unknown"] = False
    domains["quota"]["present"] = True
    domains["quota"]["flags"] = []
    domains["quota"]["extra"]["quota_status"] = "OPEN"
    domains["quota"]["extra"]["source_validity"] = "valid"
    recovered = _freshness(
        as_of, date(2026, 8, 21), DataQualityStatus.VERIFIED, [], domains=domains
    )
    assert recovered["domains"]["quota"]["status"] == "OK"


def test_freshness_status_contract_covers_ok_warning_stale_and_failed() -> None:
    def domain(*, present: bool, lag_sessions: int | None, required: bool = True) -> dict:
        return {
            "latest": date(2026, 8, 24) if present else None,
            "expected": date(2026, 8, 24),
            "latest_key": "latest_point",
            "expected_key": "expected_point",
            "lag_sessions": lag_sessions,
            "thresholds": {"warn_lag_sessions": 0, "stale_lag_sessions": 2},
            "present": present,
            "required": required,
            "applicable": True,
            "records": [],
            "required_action": "resync: test_sync_job",
        }

    cases = (
        ("OK", domain(present=True, lag_sessions=0)),
        ("WARNING", domain(present=True, lag_sessions=1)),
        ("STALE", domain(present=True, lag_sessions=3)),
        ("FAILED", domain(present=False, lag_sessions=None)),
    )
    for expected, spec in cases:
        result = _freshness(
            datetime(2026, 8, 24, 16, tzinfo=UTC),
            date(2026, 8, 24),
            DataQualityStatus.ACCEPTABLE,
            [],
            domains={"market": spec},
        )
        assert result["overall"] == expected
        assert result["domains"]["market"]["status"] == expected


def test_non_qdii_freshness_marks_index_fx_quota_not_applicable() -> None:
    class Calendar:
        def is_trading_day(self, _session, _value, _market):
            return True

        def prev_trading_day(self, _session, value, _market):
            return value

        def trading_day_distance(self, _session, left, right, *, market):
            return 0 if left == right else None

    domains = _freshness_domains(
        as_of=datetime(2026, 8, 24, 16, tzinfo=UTC),
        session=object(),
        calendar=Calendar(),
        instrument_id=uuid4(),
        thresholds=FreshnessThresholdConfig(),
        profile=SimpleNamespace(is_qdii=False),
        market_date=date(2026, 8, 24),
        expected_market_date=date(2026, 8, 24),
        expected_us_date=date(2026, 8, 24),
        market_present=True,
        market_records=[],
        nav_date=date(2026, 8, 23),
        nav_present=True,
        nav_records=[],
        holding_metadata=None,
        holding_records=[],
        underlying_date=None,
        index_present=False,
        index_records=[],
        fx_as_of=None,
        fx_trade_date=None,
        fx_present=False,
        fx_records=[],
        quota_status=QuotaStatus.NOT_APPLICABLE,
        quota_provenance_ids=(),
        quota_observed_at=None,
        quota_records=[],
    )
    result = _freshness(
        datetime(2026, 8, 24, 16, tzinfo=UTC),
        date(2026, 8, 24),
        DataQualityStatus.ACCEPTABLE,
        [],
        domains=domains,
    )
    for name in ("index", "fx", "quota"):
        assert result["domains"][name]["applicable"] is False
        assert result["domains"][name]["status"] == "OK"


def test_parquet_float_is_coerced_at_service_engine_boundary() -> None:
    assert _decimal(1.25) == Decimal("1.25")
    assert _decimal(Decimal("1.25")) == Decimal("1.25")


def test_pit_read_prefers_newest_snapshot_when_as_of_ties(db_session, tmp_path) -> None:
    instrument = Instrument(
        instrument_type="CN_ETF",
        symbol=f"PIT{uuid4().hex[:8]}",
        name="PIT ETF",
        market="SSE",
        currency="CNY",
    )
    db_session.add(instrument)
    db_session.flush()
    db_session.add(ETFProfile(instrument_id=instrument.instrument_id, is_qdii=False))

    as_of = datetime(2026, 8, 24, 16, tzinfo=UTC)
    provenance_rows = [
        ProvenanceRecord(
            provenance_id=uuid4(),
            source_kind="DERIVED_ENGINE",
            source="etf_metrics",
            provider="internal",
            observed_at=as_of,
            retrieved_at=as_of,
            quality_score=Decimal("0.9"),
            quality_status="VERIFIED",
            transform_version="test",
        )
        for _ in range(2)
    ]
    db_session.add_all(provenance_rows)
    db_session.flush()
    snapshots = [
        ETFMetricSnapshot(
            etf_metric_snapshot_id=uuid4(),
            instrument_id=instrument.instrument_id,
            as_of=as_of,
            market_date=date(2026, 8, 24),
            is_qdii=False,
            quota_status=QuotaStatus.NOT_APPLICABLE,
            details={"marker": "old"},
            engine_version="test",
            input_hash="sha256:old",
            quality_score=Decimal("0.9"),
            quality_status="VERIFIED",
            quality_flags=[],
            provenance_id=provenance_rows[0].provenance_id,
            created_at=datetime(2026, 8, 24, 16, 0, tzinfo=UTC),
        ),
        ETFMetricSnapshot(
            etf_metric_snapshot_id=uuid4(),
            instrument_id=instrument.instrument_id,
            as_of=as_of,
            market_date=date(2026, 8, 24),
            is_qdii=False,
            quota_status=QuotaStatus.NOT_APPLICABLE,
            details={"marker": "new"},
            engine_version="test",
            input_hash="sha256:new",
            quality_score=Decimal("0.9"),
            quality_status="VERIFIED",
            quality_flags=[],
            provenance_id=provenance_rows[1].provenance_id,
            created_at=datetime(2026, 8, 24, 16, 1, tzinfo=UTC),
        ),
    ]
    db_session.add_all(snapshots)
    db_session.flush()

    service = ETFDataService(
        object(),
        ParquetStore(tmp_path / "parquet"),
        band_config=load_valuation_band_config("config/etf-valuation-band.yaml"),
    )
    result = service.read_metric(db_session, instrument.instrument_id, as_of=as_of)
    assert result is not None
    assert result.details["marker"] == "new"
