# =====================================================================
# backend/app/market_data/service.py —— Market Data 查询服务（ts04 §2.6.3 标准路径）
#
# 标准路径（冻结）：PG market_bar_index 指针 → DuckDB 读取 → 返回。
# - PG 是权威（质量/指针/血缘），Parquet 是分析层；
# - as_of 过滤在数据访问层强制执行（trade_date <= as_of）；
# - 无数据 = 合法缺口（停牌/未同步），返回空列表不抛错（ts04 §5.3）。
# =====================================================================
from __future__ import annotations

from datetime import date
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.market_data.models import IndexBarIndex, MarketBarIndex
from app.market_data.parquet import ParquetStore

__all__ = ["MarketDataService"]


class MarketDataService:
    def __init__(self, parquet_store: ParquetStore) -> None:
        self.parquet_store = parquet_store

    def get_ohlcva(
        self,
        session: Session,
        instrument_id: UUID,
        *,
        start: date | None = None,
        end: date | None = None,
        as_of: date | None = None,
    ) -> list[dict]:
        """PG 指针定位 + DuckDB 读取（ts04 §2.6.3）。返回 OHLCVA 行列表。"""
        # 1) PG 指针检查：该标的有已同步区间吗（质量与血缘权威）
        stmt = select(MarketBarIndex).where(MarketBarIndex.instrument_id == instrument_id)
        if start is not None:
            stmt = stmt.where(MarketBarIndex.trade_date >= start)
        if end is not None:
            stmt = stmt.where(MarketBarIndex.trade_date <= end)
        if as_of is not None:
            stmt = stmt.where(MarketBarIndex.trade_date <= as_of)
        idx_rows = session.execute(stmt.limit(1)).scalars().all()
        if not idx_rows:
            return []   # 合法缺口：未同步/停牌
        # 2) DuckDB 读取（数值在 Parquet）
        return self.parquet_store.read_ohlcva(
            str(instrument_id), start=start, end=end, as_of=as_of,
        )

    def latest_trade_date(self, session: Session, instrument_id: UUID) -> date | None:
        """该标的最新已入库交易日（增量同步 checkpoint 用）。"""
        row = session.execute(
            select(MarketBarIndex.trade_date)
            .where(MarketBarIndex.instrument_id == instrument_id)
            .order_by(MarketBarIndex.trade_date.desc())
            .limit(1)
        ).first()
        return row[0] if row else None

    def get_index_history(
        self,
        session: Session,
        index_id: UUID,
        *,
        start: date | None = None,
        end: date | None = None,
        as_of: date | None = None,
    ) -> list[dict]:
        """ADR-007 标准路径：index_bar_index → index_history/v1 Parquet。"""
        stmt = select(IndexBarIndex).where(
            IndexBarIndex.instrument_id == index_id,
            IndexBarIndex.data_kind == "PRICE",
        )
        if start is not None:
            stmt = stmt.where(IndexBarIndex.trade_date >= start)
        if end is not None:
            stmt = stmt.where(IndexBarIndex.trade_date <= end)
        if as_of is not None:
            stmt = stmt.where(IndexBarIndex.trade_date <= as_of)
        pointers = list(session.scalars(stmt).all())
        if not pointers:
            return []
        return self.parquet_store.read_index_history(
            str(index_id), start=start, end=end, as_of=as_of,
            parquet_paths=[row.parquet_path for row in pointers if row.parquet_path],
        )

    def get_index_valuations(
        self,
        session: Session,
        index_id: UUID,
        *,
        as_of: date | None = None,
    ) -> list[dict]:
        """PG valuation pointers -> index_valuation/v1 Parquet."""
        stmt = select(IndexBarIndex).where(
            IndexBarIndex.instrument_id == index_id,
            IndexBarIndex.data_kind == "VALUATION",
        )
        if as_of is not None:
            stmt = stmt.where(IndexBarIndex.trade_date <= as_of)
        pointers = list(session.scalars(stmt).all())
        if not pointers:
            return []
        return self.parquet_store.read_index_valuations(
            str(index_id), as_of=as_of,
            parquet_paths=[row.parquet_path for row in pointers if row.parquet_path],
        )
