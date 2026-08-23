# =====================================================================
# backend/app/market_data/normalizer.py —— Market Bar Normalizer（TS-05 §7.3）
#
# 职责：MarketBarResult（Provider 输出）→ 落库记录（market_bar_index +
# provenance_records）。纯函数，唯一输入是 result + raw artifact 定位，
# 不依赖网络/Provider 当前 schema —— 可重复解析（transform_version 记录版本）。
# =====================================================================
from __future__ import annotations

from datetime import datetime
from uuid import UUID

from app.market_data.models import MarketBarIndex
from app.providers.contracts.market_data import MarketBarResult

__all__ = ["market_bar_index_row", "parquet_path_for"]


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
