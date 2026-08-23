# =====================================================================
# tests/unit/test_providers_contracts.py —— 六接口契约（TS-05 §2，冻结）
# =====================================================================
from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.common.enums import DataQualityStatus, QuotaStatus, SourceKind
from app.providers.contracts.base import (
    ProvenanceEnvelope,
    ProviderAuthError,
    ProviderCapability,
    ProviderConfigError,
    ProviderDataError,
    ProviderRateLimited,
    ProviderRole,
    ProviderTimeout,
    ProviderUnavailable,
    QualityTier,
)
from app.providers.contracts.etf import ETFProvider, NavResult, QuotaStatusResult
from app.providers.contracts.filings import FilingProvider
from app.providers.contracts.fundamentals import FinancialFactResult, FundamentalProvider
from app.providers.contracts.macro import FxRateResult, IndexBarResult, MacroProvider
from app.providers.contracts.market_data import (
    AdjFactorResult,
    MarketBarResult,
    MarketDataProvider,
    MarketSnapshotResult,
)
from app.providers.contracts.news import NewsItemResult, NewsProvider

NOW = datetime(2026, 8, 23, 12, 0, tzinfo=UTC)


def _env() -> ProvenanceEnvelope:
    return ProvenanceEnvelope(
        source="cn_daily_market",
        provider="tushare",
        observed_at=NOW,
        retrieved_at=NOW,
        quality_score=Decimal("0.96"),
        quality_status=DataQualityStatus.VERIFIED,
        transform_version="market-normalizer/0.1.0",
    )


def test_enums_are_shared_single_source() -> None:
    """TS-05 §2.0：DataQualityStatus/SourceKind/QuotaStatus 来自 app.common.enums（无第二份定义）。"""
    from app.providers.contracts import base as contracts_base

    assert contracts_base.DataQualityStatus is DataQualityStatus
    assert contracts_base.SourceKind is SourceKind
    assert contracts_base.QuotaStatus is QuotaStatus
    assert "class DataQualityStatus" not in open("app/providers/contracts/base.py").read()


def test_provider_capability_enum_has_14_domains() -> None:
    assert len(ProviderCapability) == 14


def test_provenance_envelope_required_fields() -> None:
    with pytest.raises(ValidationError):
        ProvenanceEnvelope()  # observed_at/retrieved_at/quality_score/quality_status/transform_version 必填


def test_provenance_envelope_quality_score_range() -> None:
    with pytest.raises(ValidationError):
        ProvenanceEnvelope(
            source="x", provider="x", observed_at=NOW, retrieved_at=NOW,
            quality_score=Decimal("1.5"), quality_status=DataQualityStatus.VERIFIED,
            transform_version="v1",
        )


def test_market_bar_result() -> None:
    r = MarketBarResult(
        instrument_id=uuid4(), trade_date=date(2026, 8, 21),
        close=Decimal("138.5"), provider="tushare", provenance=_env(),
    )
    assert r.currency == "CNY"
    assert r.provenance.source == "cn_daily_market"


def test_financial_fact_result_unit_quadruple() -> None:
    r = FinancialFactResult(
        instrument_id=uuid4(), metric_code="REVENUE",
        period_end=date(2025, 12, 31), statement_type="INCOME",
        retrieved_at=NOW, original_value=Decimal("123456789"),
        original_unit="元", value=Decimal("123456789"), provenance=_env(),
    )
    assert r.unit == "CNY"
    assert r.is_restated is False


def test_nav_result_and_quota() -> None:
    nav = NavResult(
        instrument_id=uuid4(), nav_date=date(2026, 8, 20),
        nav=Decimal("1.234"), retrieved_at=NOW, provenance=_env(),
    )
    assert nav.nav == Decimal("1.234")
    quota = QuotaStatusResult(
        instrument_id=uuid4(), quota_status=QuotaStatus.UNKNOWN, provenance=_env(),
    )
    assert quota.quota_status is QuotaStatus.UNKNOWN


def test_fx_and_index_bar() -> None:
    fx = FxRateResult(rate=Decimal("6.7118"), as_of=NOW, provenance=_env())
    assert fx.base_currency == "USD" and fx.quote_currency == "CNY"
    ib = IndexBarResult(index_id=uuid4(), trade_date=date(2026, 8, 21),
                        close=Decimal("7674.37"), provenance=_env())
    assert ib.currency == "USD"


def test_adj_factor_result() -> None:
    a = AdjFactorResult(instrument_id=uuid4(), trade_date=date(2026, 8, 21),
                        adj_factor=Decimal("1.0"), provenance=_env())
    assert a.adj_factor == Decimal("1.0")


def test_snapshot_requires_trade_date_field() -> None:
    # trade_date 无默认值（可为 None，但字段必须存在）
    s = MarketSnapshotResult(instrument_id=uuid4(), as_of=date(2026, 8, 21),
                             trade_date=None, provenance=_env())
    assert s.trade_date is None


def test_provider_error_hierarchy() -> None:
    for exc in (ProviderAuthError("a"), ProviderRateLimited("r"),
                ProviderTimeout("t"), ProviderUnavailable("u"),
                ProviderDataError("d"), ProviderConfigError("c")):
        assert isinstance(exc, Exception)
    rl = ProviderRateLimited("r", retry_after=2.5)
    assert rl.retry_after == 2.5


def test_abstract_providers_have_frozen_capabilities() -> None:
    """TS-05 §2：六接口 capabilities 冻结。"""
    assert MarketDataProvider.capabilities == frozenset({
        ProviderCapability.CN_DAILY_QUOTE, ProviderCapability.CN_ETF_QUOTE,
        ProviderCapability.ADJ_FACTOR})
    assert FundamentalProvider.capabilities == frozenset({ProviderCapability.FINANCIAL_STATEMENTS})
    assert FilingProvider.capabilities == frozenset({ProviderCapability.FILINGS})
    assert ETFProvider.capabilities == frozenset({
        ProviderCapability.FUND_NAV, ProviderCapability.FUND_HOLDINGS,
        ProviderCapability.QUOTA_STATUS})
    assert MacroProvider.capabilities == frozenset({
        ProviderCapability.INDEX_QUOTE, ProviderCapability.INDEX_VALUATION,
        ProviderCapability.FX_RATES, ProviderCapability.MACRO_SERIES})
    assert NewsProvider.capabilities == frozenset({ProviderCapability.NEWS})


def test_provider_role_and_quality_tier_values() -> None:
    assert ProviderRole.PRIMARY.value == "PRIMARY"
    assert QualityTier.TIER_1.value == "TIER_1"


def test_news_item_result_frozen_contract() -> None:
    n = NewsItemResult(news_id="n1", title="t", retrieved_at=NOW, source="web", provenance=_env())
    assert n.summary is None
