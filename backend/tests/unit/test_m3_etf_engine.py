from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import uuid4

from app.common.enums import QuotaStatus
from app.etf.config import load_valuation_band_config
from app.etf.engine import ETFEngine, ETFMetricInput


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
