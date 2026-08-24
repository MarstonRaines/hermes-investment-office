# =====================================================================
# backend/app/market_data/normalizer.py —— Market Bar Normalizer（TS-05 §7.3）
#
# 职责：MarketBarResult（Provider 输出）→ 落库记录（market_bar_index +
# provenance_records）。纯函数，唯一输入是 result + raw artifact 定位，
# 不依赖网络/Provider 当前 schema —— 可重复解析（transform_version 记录版本）。
# =====================================================================
from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from app.market_data.models import IndexBarIndex, MarketBarIndex
from app.providers.contracts.macro import IndexBarResult
from app.providers.contracts.market_data import MarketBarResult

__all__ = [
    "market_bar_index_row", "parquet_path_for",
    "index_bar_index_row", "index_parquet_path_for",
    "holdings_path_for", "nav_parquet_path_for", "fx_parquet_path_for",
    "index_valuation_path_for",
]


def parquet_path_for(instrument_id: UUID, trade_date) -> str:
    """ohlcva/v1 目标路径（ts04 §2.6.3：PG 指针 → DuckDB 读取）。

    物理布局：data/parquet/ohlcva/v1/<instrument_id_hash>/trade_date_month=YYYY-MM/
    简化分区（v0.1 允许仅 trade_date_month 分区，ts04 §2.2 注）。
    """
    hash_dir = f"{int(instrument_id.hex[:2], 16):02x}"
    return f"parquet/ohlcva/v1/{hash_dir}/trade_date_month={trade_date.strftime('%Y-%m')}/part-{instrument_id}.parquet"


def market_bar_index_row(
    bar: MarketBarResult,
    provenance_id: UUID,
    *,
    raw_hash: str | None = None,
    raw_object_key: str | None = None,
    ingestion_run_id: UUID | None = None,
) -> MarketBarIndex:
    """MarketBarResult → market_bar_index 行（数值在 Parquet，PG 只存指针与质量）。"""
    return MarketBarIndex(
        instrument_id=bar.instrument_id,
        trade_date=bar.trade_date,
        provider=bar.provider,                       # 实际取数 provider（fallback 时=actual）
        source_timestamp=bar.source_timestamp,
        ingested_at=datetime.now(),
        quality_status=bar.provenance.quality_status,
        provenance_id=provenance_id,
        parquet_path=parquet_path_for(bar.instrument_id, bar.trade_date),
    )


def index_parquet_path_for(index_id: UUID, trade_date, provider: str | None = None) -> str:
    """index_history/v1 指针路径（ADR-007）。"""
    hash_dir = f"{int(index_id.hex[:2], 16):02x}"
    suffix = f"-{provider.replace('/', '_')}" if provider else ""
    return (
        f"parquet/index_history/v1/{hash_dir}/"
        f"trade_date_month={trade_date.strftime('%Y-%m')}/part-{index_id}{suffix}.parquet"
    )


def holdings_path_for(
    instrument_id: UUID,
    report_period,
    *,
    holding_snapshot_id: UUID | None = None,
) -> str:
    """ETF Level 1 path isolated by holding snapshot identity.

    ``instrument_id`` remains in the partition path for discovery, while the
    snapshot UUID is the immutable identity that prevents two sources for the
    same report period from overwriting each other.
    """
    hash_dir = f"{int(instrument_id.hex[:2], 16):02x}"
    identity = str(holding_snapshot_id or instrument_id)
    return (
        f"parquet/etf_holdings/v1/{hash_dir}/"
        f"report_period={report_period.isoformat()}/"
        f"holding_snapshot_id={identity}/part-{identity}.parquet"
    )


def nav_parquet_path_for(instrument_id: UUID, nav_date, provider: str) -> str:
    hash_dir = f"{int(instrument_id.hex[:2], 16):02x}"
    safe_provider = provider.replace("/", "_")
    return (
        f"parquet/etf_nav/v1/{hash_dir}/nav_date={nav_date.isoformat()}/"
        f"part-{instrument_id}-{safe_provider}.parquet"
    )


def fx_parquet_path_for(base_currency: str, quote_currency: str, as_of, provider: str) -> str:
    safe_provider = provider.replace("/", "_")
    return (
        f"parquet/fx/v1/{base_currency}{quote_currency}/"
        f"as_of_date={as_of.date().isoformat()}/part-{safe_provider}.parquet"
    )


def index_valuation_path_for(index_id: UUID, as_of_date, provider: str) -> str:
    hash_dir = f"{int(index_id.hex[:2], 16):02x}"
    safe_provider = provider.replace("/", "_")
    return (
        f"parquet/index_valuation/v1/{hash_dir}/"
        f"as_of_date={as_of_date.isoformat()}/part-{index_id}-{safe_provider}.parquet"
    )


def index_bar_index_row(
    bar: IndexBarResult,
    provenance_id: UUID,
) -> IndexBarIndex:
    """IndexBarResult → index_bar_index PG pointer."""
    return IndexBarIndex(
        instrument_id=bar.index_id,
        trade_date=bar.trade_date,
        provider=bar.provenance.provider,
        source_timestamp=getattr(bar, "source_timestamp", None),
        ingested_at=datetime.now(UTC),
        quality_status=bar.provenance.quality_status,
        provenance_id=provenance_id,
        parquet_path=index_parquet_path_for(
            bar.index_id, bar.trade_date, bar.provenance.provider
        ),
        data_kind="PRICE",
    )
