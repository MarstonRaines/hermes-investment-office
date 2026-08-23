# =====================================================================
# tests/unit/test_parquet_store.py —— Parquet 层（TS-04 §2 冻结）
#
# 覆盖：ACC-M1-002（PG 指针 → DuckDB 读取）、ACC-M1-008（schema 版本化）、
#       GOLD-PIT-004（as_of 裁剪）、schema.json 机器校验。
# =====================================================================
from __future__ import annotations

import json
from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import uuid4

import pytest

from app.market_data.parquet import OHLCVA_SCHEMA, ParquetStore, SchemaMismatchError
from app.providers.contracts.base import ProvenanceEnvelope
from app.providers.contracts.market_data import MarketBarResult

INST = uuid4()
NOW = datetime(2026, 8, 21, 15, 0, tzinfo=UTC)


def _bar(td: date, close: str, provider="tushare") -> MarketBarResult:
    return MarketBarResult(
        instrument_id=INST, trade_date=td, open=Decimal(close), high=Decimal(close),
        low=Decimal(close), close=Decimal(close), volume=Decimal("1000"),
        amount=Decimal("138500"), pre_close=Decimal(close), pct_change=Decimal("0.5"),
        adj_factor=Decimal("1.0"), adjusted_close=Decimal(close),
        provider=provider, source_timestamp=NOW,
        provenance=ProvenanceEnvelope(
            source="cn_daily_market", provider=provider,
            source_record_id=f"{provider}@{td.isoformat()}",
            observed_at=NOW, retrieved_at=NOW, as_of_date=td,
            quality_score=Decimal("0.96"), quality_status="VERIFIED",
            transform_version="market-normalizer/0.1.0",
        ),
    )


def test_write_and_read_roundtrip(tmp_path) -> None:
    """ACC-M1-002 单元层：写入 → DuckDB 读取往返。"""
    store = ParquetStore(tmp_path / "parquet")
    store.write_ohlcva([_bar(date(2026, 8, 20), "100"), _bar(date(2026, 8, 21), "101")])
    rows = store.read_ohlcva(str(INST))
    assert len(rows) == 2
    assert rows[0]["trade_date"] == date(2026, 8, 20)
    assert rows[0]["close"] == 100.0
    assert rows[1]["close"] == 101.0
    assert rows[0]["provider"] == "tushare"
    assert rows[0]["quality_status"] == "VERIFIED"


def test_read_as_of_filter(tmp_path) -> None:
    """GOLD-PIT-004：as_of 只返回 <= as_of 的行。"""
    store = ParquetStore(tmp_path / "parquet")
    store.write_ohlcva([_bar(date(2026, 8, 20), "100"), _bar(date(2026, 8, 21), "101")])
    rows = store.read_ohlcva(str(INST), as_of=date(2026, 8, 20))
    assert [r["trade_date"] for r in rows] == [date(2026, 8, 20)]


def test_read_date_range(tmp_path) -> None:
    store = ParquetStore(tmp_path / "parquet")
    store.write_ohlcva([_bar(date(2026, 8, 19), "99"), _bar(date(2026, 8, 20), "100")])
    rows = store.read_ohlcva(str(INST), start=date(2026, 8, 20))
    assert [r["trade_date"] for r in rows] == [date(2026, 8, 20)]


def test_read_empty_is_legal_gap(tmp_path) -> None:
    """缺口语义：无数据 → 空列表，不抛错（ts04 §5.3）。"""
    store = ParquetStore(tmp_path / "parquet")
    assert store.read_ohlcva(str(uuid4())) == []


def test_schema_json_created_and_verifiable(tmp_path) -> None:
    """ACC-M1-008：schema.json 三处一致（目录/文件元数据/列契约）。"""
    store = ParquetStore(tmp_path / "parquet")
    store.write_ohlcva([_bar(date(2026, 8, 21), "101")])
    schema_path = store.schema_json_path("ohlcva", 1)
    assert schema_path.exists()
    payload = json.loads(schema_path.read_text(encoding="utf-8"))
    assert payload["schema_version"] == 1
    assert {c["name"] for c in payload["columns"]} == set(OHLCVA_SCHEMA)
    assert store.verify_schema("ohlcva", 1) is True
    # 版本目录：data/parquet/ohlcva/v1/<hash>/trade_date_month=YYYY-MM/part-*.parquet
    files = list((tmp_path / "parquet" / "ohlcva" / "v1").rglob("*.parquet"))
    assert len(files) == 1
    assert "trade_date_month=2026-08" in str(files[0])


def test_verify_schema_detects_mismatch(tmp_path) -> None:
    """schema.json 与实际列不一致 → 校验失败（禁止静默读取）。"""
    import pyarrow as pa
    import pyarrow.parquet as pq

    store = ParquetStore(tmp_path / "parquet")
    store.write_ohlcva([_bar(date(2026, 8, 21), "101")])
    # 篡改：写入一个列缺失的文件
    target = list((tmp_path / "parquet" / "ohlcva" / "v1").rglob("*.parquet"))[0]
    table = pa.Table.from_pylist([{"instrument_id": "x", "trade_date": date(2026, 8, 21)}])
    pq.write_table(table, target)
    assert store.verify_schema("ohlcva", 1) is False


def test_schema_version_mismatch_raises(tmp_path) -> None:
    store = ParquetStore(tmp_path / "parquet")
    path = store.schema_json_path("ohlcva", 1)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"schema_version": 99, "columns": []}), encoding="utf-8")
    with pytest.raises(SchemaMismatchError):
        store.ensure_schema("ohlcva", 1, store._schema_columns())


