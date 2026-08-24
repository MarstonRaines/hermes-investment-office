from __future__ import annotations

import asyncio
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest

import app.models  # noqa: F401
from app.common.base import Base
from app.common.gateway import GatewayFetch
from app.common.provenance import ProvenanceEnvelope
from app.etf.config import load_qdii_alignment_config, load_valuation_band_config
from app.etf.service import ETFDataService, _holding_rows
from app.market_data.normalizer import (
    fx_parquet_path_for,
    index_valuation_path_for,
    nav_parquet_path_for,
)
from app.market_data.parquet import ParquetStore, SchemaMismatchError
from app.providers.contracts.etf import (
    HoldingItem,
    HoldingSnapshotResult,
    NavResult,
    QuotaStatusResult,
)
from app.providers.contracts.macro import IndexBarResult
from app.providers.etf_gateway import ETFDataGateway


def _provenance(provider: str, source: str) -> ProvenanceEnvelope:
    now = datetime(2026, 8, 24, tzinfo=UTC)
    return ProvenanceEnvelope(
        source=source,
        provider=provider,
        observed_at=now,
        retrieved_at=now,
        as_of_date=date(2026, 8, 24),
        quality_score=Decimal("0.90"),
        quality_status="ACCEPTABLE",
        transform_version="test/0.1",
        source_record_id=f"{provider}:2026-08-24",
    )


def test_metadata_contains_adr_tables_and_m3_columns() -> None:
    assert len(Base.metadata.tables) == 43
    assert {"watchlists", "watchlist_members", "index_bar_index"} <= set(Base.metadata.tables)
    assert {
        "reference_nav_basis", "valuation_band", "band_basis", "band_inputs",
        "band_thresholds_hash", "details",
    } <= set(Base.metadata.tables["etf_metric_snapshots"].c.keys())


def test_migration_does_not_seed_or_overwrite_etf_pool() -> None:
    migration = Path(__file__).resolve().parents[2] / "migrations" / "versions" / (
        "c2d3e4f5a6b7_adr_006_007_watchlists_index_bar.py"
    )
    source = migration.read_text(encoding="utf-8")
    assert "op.create_table(\n        \"watchlists\"" in source
    assert "op.execute" not in source
    assert "510300" not in source
    assert "513650" not in source
    assert "512890" not in source


def test_etf_holdings_and_index_history_have_versioned_parquet_paths(tmp_path) -> None:
    store = ParquetStore(tmp_path / "parquet")
    etf_id = uuid4()
    index_id = uuid4()
    snapshot = HoldingSnapshotResult(
        instrument_id=etf_id,
        report_period=date(2026, 6, 30),
        disclosure_date=date(2026, 8, 20),
        source="HALF_YEAR",
        holdings=[
            HoldingItem(rank=1, provider_symbol="600519.SH", weight_pct=Decimal("5.1")),
            HoldingItem(rank=2, provider_symbol="000001.SZ", weight_pct=Decimal("4.9")),
        ],
        provenance=_provenance("akshare_eastmoney", "cn_fund_holdings"),
    )
    assert store.write_etf_holdings([snapshot]) == 2
    parquet_paths = sorted(
        (tmp_path / "parquet" / "etf_holdings" / "v2").rglob("*.parquet")
    )
    assert len(parquet_paths) == 1
    holdings = store.read_etf_holdings(str(etf_id), parquet_path=str(parquet_paths[0]))
    assert holdings[0]["holding_level"] == "LEVEL_1_DISCLOSED"
    assert holdings[0]["provider_symbol"] == "600519.SH"
    assert {row["holding_snapshot_id"] for row in holdings} == {
        holdings[0]["holding_snapshot_id"]
    }
    assert [row["weight_ratio"] for row in holdings] == pytest.approx([0.051, 0.049])
    assert all(row["holding_instrument_id"] is None for row in holdings)
    assert all(row["quality_flags"] == "UNRESOLVED_SYMBOL" for row in holdings)
    assert store.read_etf_holdings(str(etf_id)) == []

    second = snapshot.model_copy(
        update={
            "source": "QUARTERLY",
            "holdings": [HoldingItem(rank=1, provider_symbol="600519.SH", weight_pct=Decimal("2"))],
        }
    )
    assert store.write_etf_holdings([second]) == 1
    parquet_paths = sorted(
        (tmp_path / "parquet" / "etf_holdings" / "v2").rglob("*.parquet")
    )
    assert len(parquet_paths) == 2
    assert sum(
        len(store.read_etf_holdings(str(etf_id), parquet_path=str(path)))
        for path in parquet_paths
    ) == 3

    bars = [
        IndexBarResult(
            index_id=index_id,
            trade_date=date(2026, 8, 21),
            close=Decimal("100"),
            currency="USD",
            provenance=_provenance("yahoo", "us_index_quote"),
        )
    ]
    assert store.write_index_history(bars) == 1
    history = store.read_index_history(str(index_id))
    assert history[0]["close"] == 100.0
    assert history[0]["instrument_id"] == str(index_id)
    assert "index_id" not in history[0]
    assert (tmp_path / "parquet" / "index_history" / "v1").exists()


def test_etf_holdings_v1_is_preserved_and_v2_uses_disclosed_ratio(tmp_path) -> None:
    store = ParquetStore(tmp_path / "parquet")
    snapshot = HoldingSnapshotResult(
        instrument_id=uuid4(),
        report_period=date(2026, 6, 30),
        disclosure_date=date(2026, 8, 20),
        source="HALF_YEAR",
        holdings=[
            HoldingItem(rank=1, provider_symbol="A", weight_pct=Decimal("5.1")),
            HoldingItem(rank=2, provider_symbol="B", weight_pct=Decimal("4.9")),
        ],
        provenance=_provenance("primary", "cn_fund_holdings"),
    )
    assert store.write_etf_holdings([snapshot], version=1) == 2
    v1_path = next((tmp_path / "parquet" / "etf_holdings" / "v1").rglob("*.parquet"))
    v1_rows = store.read_etf_holdings(
        str(snapshot.instrument_id), parquet_path=str(v1_path)
    )
    assert [row["weight_ratio"] for row in v1_rows] == pytest.approx([0.51, 0.49])
    v1_bytes = v1_path.read_bytes()

    assert store.write_etf_holdings([snapshot], version=2) == 2
    v2_path = next((tmp_path / "parquet" / "etf_holdings" / "v2").rglob("*.parquet"))
    v2_rows = store.read_etf_holdings(
        str(snapshot.instrument_id), parquet_path=str(v2_path)
    )
    assert [row["weight_ratio"] for row in v2_rows] == pytest.approx([0.051, 0.049])
    assert v1_path.read_bytes() == v1_bytes
    assert str(v1_path) != str(v2_path)
    assert store.schema_json_path("etf_holdings", 1).exists()
    assert store.schema_json_path("etf_holdings", 2).exists()


def test_etf_holdings_read_uses_only_the_pg_pointer_path(tmp_path, monkeypatch) -> None:
    store = ParquetStore(tmp_path / "parquet")
    snapshot = HoldingSnapshotResult(
        instrument_id=uuid4(),
        report_period=date(2026, 6, 30),
        disclosure_date=date(2026, 8, 20),
        source="HALF_YEAR",
        holdings=[HoldingItem(rank=1, provider_symbol="A", weight_pct=Decimal("5"))],
        provenance=_provenance("primary", "cn_fund_holdings"),
    )
    assert store.write_etf_holdings([snapshot]) == 1
    parquet_path = next((tmp_path / "parquet" / "etf_holdings" / "v2").rglob("*.parquet"))

    def forbidden_glob(*_args, **_kwargs):
        raise AssertionError("ETF holdings reads must not scan a dataset glob")

    monkeypatch.setattr(Path, "glob", forbidden_glob)
    monkeypatch.setattr(Path, "rglob", forbidden_glob)
    rows = store.read_etf_holdings(
        str(snapshot.instrument_id), parquet_path=str(parquet_path)
    )
    assert len(rows) == 1
    assert "holding_snapshot_id=" in str(parquet_path)

    with pytest.raises(SchemaMismatchError):
        store.read_etf_holdings(
            str(snapshot.instrument_id),
            parquet_path="parquet/etf_holdings/missing-version.parquet",
        )


def test_service_holding_rows_keep_disclosed_percentages_and_unresolved_null() -> None:
    snapshot = HoldingSnapshotResult(
        instrument_id=uuid4(),
        report_period=date(2026, 6, 30),
        disclosure_date=date(2026, 8, 20),
        source="HALF_YEAR",
        holdings=[
            HoldingItem(rank=1, provider_symbol="UNKNOWN", weight_pct=Decimal("5.1")),
            HoldingItem(rank=2, provider_symbol="KNOWN", weight_pct=Decimal("4.9")),
        ],
        provenance=_provenance("primary", "cn_fund_holdings"),
    )

    class Session:
        def scalar(self, _statement):
            return None

    rows = _holding_rows(Session(), snapshot, "primary", uuid4(), "parquet/path")
    assert [row["weight_ratio"] for row in rows] == pytest.approx([0.051, 0.049])
    assert rows[0]["holding_instrument_id"] is None
    assert rows[0]["provider_symbol"] == "UNKNOWN"
    assert rows[0]["quality_flags"] == "UNRESOLVED_SYMBOL"


def test_parquet_read_preserves_null_for_mixed_resolved_holdings(tmp_path) -> None:
    store = ParquetStore(tmp_path / "parquet")
    known_id = uuid4()
    snapshot = HoldingSnapshotResult(
        instrument_id=uuid4(),
        report_period=date(2026, 6, 30),
        disclosure_date=date(2026, 8, 20),
        source="HALF_YEAR",
        holdings=[
            HoldingItem(rank=1, provider_symbol="KNOWN", instrument_id=known_id,
                        weight_pct=Decimal("5.1")),
            HoldingItem(rank=2, provider_symbol="UNKNOWN", weight_pct=Decimal("4.9")),
        ],
        provenance=_provenance("primary", "cn_fund_holdings"),
    )

    snapshot_id = uuid4()
    rows = _holding_rows(
        SimpleNamespace(scalar=lambda _statement: None), snapshot, "primary",
        snapshot_id, "parquet/etf_holdings/v2/test.parquet",
    )
    store.write_etf_holdings_rows(rows)
    got = store.read_etf_holdings(
        str(snapshot.instrument_id), parquet_path=rows[0]["_parquet_path"],
    )
    assert got[0]["holding_instrument_id"] == str(known_id)
    assert got[1]["holding_instrument_id"] is None
    assert got[1]["quality_flags"] == "UNRESOLVED_SYMBOL"


def test_nav_fx_and_index_valuation_pointer_datasets_are_readable(tmp_path) -> None:
    store = ParquetStore(tmp_path / "parquet")
    instrument_id = uuid4()
    index_id = uuid4()
    observed = datetime(2026, 8, 24, 9, tzinfo=UTC)
    provenance_id = str(uuid4())

    nav_path = nav_parquet_path_for(instrument_id, date(2026, 8, 23), "primary")
    assert store.write_etf_nav([{
        "instrument_id": str(instrument_id), "nav_date": date(2026, 8, 23),
        "nav": Decimal("1.2345"), "currency": "CNY", "published_at": observed,
        "retrieved_at": observed, "provider": "primary", "quality_status": "VERIFIED",
        "provenance_id": provenance_id, "_parquet_path": nav_path,
    }]) == 1
    assert store.read_etf_nav(str(instrument_id), parquet_path=nav_path)[0]["nav"] == 1.2345

    fx_path = fx_parquet_path_for("USD", "CNY", observed, "primary")
    assert store.write_fx_rates([{
        "base_currency": "USD", "quote_currency": "CNY", "rate": Decimal("7.2"),
        "as_of": observed, "trade_date": date(2026, 8, 24), "provider": "primary",
        "quality_status": "VERIFIED", "provenance_id": provenance_id,
        "_parquet_path": fx_path,
    }]) == 1
    assert store.read_fx_rates(parquet_paths=[fx_path])[0]["rate"] == 7.2

    valuation_path = index_valuation_path_for(index_id, date(2026, 8, 23), "primary")
    assert store.write_index_valuations([{
        "instrument_id": str(index_id), "as_of_date": date(2026, 8, 23),
        "pe": Decimal("18.5"), "pb": Decimal("2.1"), "source": "test",
        "provider": "primary", "source_timestamp": observed, "ingested_at": observed,
        "quality_status": "VERIFIED", "provenance_id": provenance_id,
        "_parquet_path": valuation_path,
    }]) == 1
    assert store.read_index_valuations(str(index_id), parquet_paths=[valuation_path])[0]["pe"] == 18.5


def test_qdii_alignment_threshold_is_configuration_driven() -> None:
    config = load_qdii_alignment_config("config/qdii-alignment.yaml")
    assert config.max_market_nav_days == 1
    assert config.max_underlying_market_days == 1
    assert config.freshness.market.stale_lag_sessions == 2
    assert config.freshness.etf_holdings.warn_lag_sessions == 60
    assert config.config_hash.startswith("sha256:")


def test_gateway_normalizes_single_quota_event_to_job_rows() -> None:
    instrument_id = uuid4()
    event = QuotaStatusResult(
        instrument_id=instrument_id,
        quota_status="RESTRICTED",
        effective_from=date(2026, 8, 24),
        announcement_date=date(2026, 8, 23),
        provenance=_provenance("primary", "quota"),
    )

    class Gateway:
        async def fetch_with_fallback(self, _capability, _fetcher, *, instrument_id=None):
            return event, SimpleNamespace(
                actual_provider="primary",
                requested_provider="primary",
                fallback_used=False,
                fallback_reason=None,
            )

    result = asyncio.run(ETFDataGateway(Gateway()).fetch_quota(instrument_id))
    assert result.rows == [event]
    assert result.actual_provider == "primary"


def test_quota_sync_keeps_status_in_metric_job_summary_and_provenance(tmp_path) -> None:
    instrument_id = uuid4()
    observed = datetime(2026, 8, 24, 9, tzinfo=UTC)
    event = QuotaStatusResult(
        instrument_id=instrument_id,
        quota_status="RESTRICTED",
        effective_from=date(2026, 8, 24),
        announcement_date=date(2026, 8, 23),
        provenance=_provenance("primary", "quota").model_copy(
            update={"observed_at": observed}
        ),
    )

    class Gateway:
        async def fetch_quota(self, _instrument_id):
            return GatewayFetch(
                rows=[event], actual_provider="primary", requested_provider="primary"
            )

    class Raw:
        async def save(self, _provider, job_name, _label, content):
            assert job_name == "etf_quota"
            assert content
            return SimpleNamespace(raw_hash="sha256:quota", raw_object_key="raw/quota.json")

    class Session:
        def __init__(self):
            self.added = []

        def add(self, row):
            row.provenance_id = row.provenance_id or uuid4()
            self.added.append(row)

    service = ETFDataService(
        Gateway(), ParquetStore(tmp_path / "parquet"), raw_store=Raw(),
        band_config=load_valuation_band_config("config/etf-valuation-band.yaml"),
    )
    summary = asyncio.run(service.sync_quota(Session(), instrument_id))
    assert summary.quota_status.value == "RESTRICTED"
    assert summary.quota_observed_at == observed
    assert len(summary.provenance_ids) == 1


def test_nav_sync_preserves_actual_provider_fallback_and_raw_provenance(tmp_path) -> None:
    instrument_id = uuid4()
    nav = NavResult(
        instrument_id=instrument_id,
        nav_date=date(2026, 8, 23),
        nav=Decimal("1.234567"),
        published_at=datetime(2026, 8, 24, 8, tzinfo=UTC),
        retrieved_at=datetime(2026, 8, 24, 9, tzinfo=UTC),
        provenance=_provenance("akshare_eastmoney", "cn_fund_nav").model_copy(
            update={
                "fallback_used": True,
                "requested_provider": "tushare",
                "fallback_reason": "PRIMARY_TIMEOUT",
                "quality_flags": ["FALLBACK_USED"],
            }
        ),
    )

    class Gateway:
        async def fetch_nav_history(self, _instrument_id):
            return GatewayFetch(
                rows=[nav], actual_provider="akshare_eastmoney",
                requested_provider="tushare", fallback_used=True,
                fallback_reason="PRIMARY_TIMEOUT",
            )

    class Raw:
        async def save(self, provider, job_name, label, content):
            assert provider == "akshare_eastmoney"
            assert job_name == "etf_nav"
            assert content
            return SimpleNamespace(raw_hash="sha256:raw", raw_object_key="raw/nav.json")

    class Session:
        def __init__(self):
            self.added = []
            self.executed = 0

        def add(self, row):
            if getattr(row, "provenance_id", None) is None:
                row.provenance_id = uuid4()
            self.added.append(row)

        def execute(self, _statement):
            self.executed += 1

    session = Session()
    service = ETFDataService(
        Gateway(), ParquetStore(tmp_path / "parquet"), raw_store=Raw(),
        band_config=load_valuation_band_config("config/etf-valuation-band.yaml"),
    )
    summary = asyncio.run(service.sync_nav(session, instrument_id))

    assert summary.actual_provider == "akshare_eastmoney"
    assert summary.fallback_used is True
    assert session.executed == 1
    provenance = session.added[0]
    assert provenance.raw_hash == "sha256:raw"
    assert provenance.raw_object_key == "raw/nav.json"
    assert provenance.fallback_used is True
