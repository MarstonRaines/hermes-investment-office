# =====================================================================
# tests/unit/test_fx_service.py —— FX Engine（S7 双源 + 交叉验证）
# =====================================================================
from __future__ import annotations

import asyncio
from datetime import date, datetime, timezone
from decimal import Decimal
from uuid import uuid4

import app.models  # noqa: F401

from app.audit.models import ProvenanceRecord
from app.common.config import settings
from app.fx.models import FXObservation
from app.fx.service import FXService
from app.providers.capability_matrix import load_capability_matrix
from app.providers.contracts.base import (
    BaseProvider,
    ProvenanceEnvelope,
    ProviderCapability,
    ProviderHealth,
    ProviderQualityReport,
    ProviderRole,
    QualityTier,
)
from app.providers.contracts.macro import FxRateResult
from app.providers.gateway import DataGateway
from app.providers.registry import ProviderRegistry

NOW = datetime(2026, 8, 21, 12, 0, tzinfo=timezone.utc)


def _fx(provider: str, td: date, rate: str) -> FxRateResult:
    return FxRateResult(
        base_currency="USD", quote_currency="CNY", rate=Decimal(rate),
        as_of=datetime.combine(td, datetime.min.time(), tzinfo=timezone.utc),
        trade_date=td,
        provenance=ProvenanceEnvelope(
            source="fx_rates", provider=provider,
            source_record_id=f"{provider}@{td.isoformat()}",
            observed_at=NOW, retrieved_at=NOW, as_of_date=td,
            quality_score=Decimal("0.96"), quality_status="VERIFIED",
            transform_version="macro-normalizer/0.1.0",
        ),
    )


class FakeYahoo(BaseProvider):
    provider_name = "yahoo"
    display_name = "Yahoo"
    capabilities = frozenset({ProviderCapability.FX_RATES})
    default_role = ProviderRole.PRIMARY
    quality_tier = QualityTier.TIER_3
    known_limits = []
    rows: list = []

    def __init__(self, config=None) -> None:
        self.config = config

    async def get_fx_rates(self, base, quote, start, end):
        return [r for r in FakeYahoo.rows if start <= r.trade_date <= end]

    async def health_check(self) -> ProviderHealth:
        return ProviderHealth(provider="yahoo", status="HEALTHY", checked_at=NOW)

    async def quality_report(self) -> ProviderQualityReport:
        return ProviderQualityReport(provider="yahoo", tier=self.quality_tier,
                                     quality_score=Decimal("0.8"))


class FakeFred(BaseProvider):
    provider_name = "fred"
    display_name = "FRED"
    capabilities = frozenset({ProviderCapability.FX_RATES})
    default_role = ProviderRole.AUXILIARY
    quality_tier = QualityTier.TIER_1
    known_limits = []
    rows: list = []

    def __init__(self, config=None) -> None:
        self.config = config

    async def get_fx_rates(self, base, quote, start, end):
        return [r for r in FakeFred.rows if start <= r.trade_date <= end]

    async def health_check(self) -> ProviderHealth:
        return ProviderHealth(provider="fred", status="HEALTHY", checked_at=NOW)

    async def quality_report(self) -> ProviderQualityReport:
        return ProviderQualityReport(provider="fred", tier=self.quality_tier,
                                     quality_score=Decimal("0.98"))


def make_gateway() -> DataGateway:
    matrix = load_capability_matrix(settings.provider_capability_path)
    reg = ProviderRegistry(matrix)
    reg.register(FakeYahoo)
    reg.register(FakeFred)
    return DataGateway(reg)


def test_sync_fx_primary_and_crosscheck(db_session) -> None:
    """S7：yahoo primary 落库 + fred 交叉验证（0.4% 偏差 < 阈值不标记）。"""
    FakeYahoo.rows = [_fx("yahoo", date(2026, 8, 21), "6.7118")]
    FakeFred.rows = [_fx("fred", date(2026, 8, 21), "6.7412")]

    async def run() -> dict:
        svc = FXService(make_gateway())
        return await svc.sync_fx(db_session, date(2026, 8, 1), date(2026, 8, 31))

    result = asyncio.run(run())
    db_session.flush()
    assert result["written"] == 1
    assert result["deviations"] == []
    obs = db_session.query(FXObservation).one()
    assert obs.rate == Decimal("6.7118")
    assert obs.provider == "yahoo"
    prov = db_session.get(ProvenanceRecord, obs.provenance_id)
    assert "CROSS_SOURCE_DEVIATION" not in prov.quality_flags


def test_sync_fx_deviation_flagged(db_session) -> None:
    """双源偏差 >1% → CROSS_SOURCE_DEVIATION flag（TS-05 §5.5 ADVISORY）。"""
    FakeYahoo.rows = [_fx("yahoo", date(2026, 8, 21), "6.7118")]
    FakeFred.rows = [_fx("fred", date(2026, 8, 21), "7.5000")]

    async def run() -> dict:
        svc = FXService(make_gateway())
        return await svc.sync_fx(db_session, date(2026, 8, 1), date(2026, 8, 31))

    result = asyncio.run(run())
    db_session.flush()
    assert len(result["deviations"]) == 1
    prov = db_session.query(ProvenanceRecord).one()
    assert "CROSS_SOURCE_DEVIATION" in prov.quality_flags


def test_get_fx_rate_asof(db_session) -> None:
    FakeYahoo.rows = [_fx("yahoo", date(2026, 8, 20), "6.70"), _fx("yahoo", date(2026, 8, 21), "6.71")]

    async def run() -> dict:
        svc = FXService(make_gateway())
        return await svc.sync_fx(db_session, date(2026, 8, 1), date(2026, 8, 31))

    asyncio.run(run())
    db_session.flush()
    svc = FXService(make_gateway())
    assert svc.get_fx_rate(db_session, date(2026, 8, 20)) == Decimal("6.70")
    assert svc.get_fx_rate(db_session, date(2026, 8, 21)) == Decimal("6.71")
    assert svc.get_fx_rate(db_session, date(2026, 8, 19)) is None   # 未同步（缺口语义）
