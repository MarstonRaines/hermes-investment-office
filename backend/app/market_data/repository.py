# =====================================================================
# backend/app/market_data/repository.py —— Market Bar 持久化（同事务写入）
#
# TS-05 §8.3 第 7 步：facts + provenance_records + market_bar_index 同事务写入。
# 幂等语义：market_bar_index 唯一键 (instrument_id, trade_date, provider)，
# 重跑同区间 → upsert（新 provenance 行保留为 supersede 历史，指针替换为新行）。
# =====================================================================
from __future__ import annotations

from uuid import UUID, uuid4

from sqlalchemy import literal_column
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.audit.service import write_provenance
from app.market_data.models import MarketBarIndex
from app.market_data.normalizer import market_bar_index_row
from app.providers.contracts.market_data import MarketBarResult
from app.providers.raw_store import RawArtifact

__all__ = ["persist_market_bars", "UpsertSummary"]


class UpsertSummary:
    def __init__(self, inserted: int, updated: int, provenance_ids: list[UUID]) -> None:
        self.inserted = inserted
        self.updated = updated
        self.provenance_ids = provenance_ids

    def __repr__(self) -> str:  # pragma: no cover
        return f"UpsertSummary(inserted={self.inserted}, updated={self.updated})"


def persist_market_bars(
    session: Session,
    bars: list[MarketBarResult],
    raw: RawArtifact | None = None,
    ingestion_run_id: UUID | None = None,
    parquet_store=None,
) -> UpsertSummary:
    """bars + provenance 同事务写入 market_bar_index（upsert-by-supersede）。

    若传入 parquet_store：先写 ohlcva/v1 Parquet（派生分析层），再写 PG
    （指针权威）；Parquet 失败 → 不写 PG（job FAILED，下轮重跑重写）。
    调用方负责 session.commit()（事务边界在 job 层）。
    """
    if parquet_store is not None:
        parquet_store.write_ohlcva(bars)
    inserted = 0
    updated = 0
    provenance_ids: list[UUID] = []
    for bar in bars:
        env = bar.provenance
        if raw is not None:
            env = env.model_copy(update={"raw_hash": raw.raw_hash, "raw_object_key": raw.raw_object_key})
        if ingestion_run_id is not None:
            env = env.model_copy(update={"ingestion_run_id": ingestion_run_id})
        prov = write_provenance(session, env)
        prov.provenance_id = prov.provenance_id or uuid4()   # pk 默认值在 flush 时生成，此处显式赋值
        provenance_ids.append(prov.provenance_id)
        row = market_bar_index_row(bar, prov.provenance_id,
                                   raw_hash=env.raw_hash, raw_object_key=env.raw_object_key,
                                   ingestion_run_id=ingestion_run_id)
        row.bar_id = row.bar_id or uuid4()
        stmt = insert(MarketBarIndex).values(
            bar_id=row.bar_id, instrument_id=row.instrument_id, trade_date=row.trade_date,
            provider=row.provider, source_timestamp=row.source_timestamp,
            ingested_at=row.ingested_at, quality_status=row.quality_status,
            provenance_id=row.provenance_id, parquet_path=row.parquet_path,
        ).on_conflict_do_update(
            constraint="uq_market_bar_index_inst_date",
            set_={
                "source_timestamp": row.source_timestamp,
                "ingested_at": row.ingested_at,
                "quality_status": row.quality_status,
                "provenance_id": row.provenance_id,
                "parquet_path": row.parquet_path,
            },
        ).returning(literal_column("(xmax = 0) AS is_insert"))
        is_insert = session.execute(stmt).scalar_one()
        if is_insert:
            inserted += 1
        else:
            updated += 1
    return UpsertSummary(inserted=inserted, updated=updated, provenance_ids=provenance_ids)
