"""Persisted macro facts used by the ETF Engine.

Only this data-service layer receives the Gateway port.  MCP and the Engine
read the resulting PG pointers/Parquet datasets and never call Providers.
"""

from __future__ import annotations

import json
from datetime import date
from typing import Any, Protocol
from uuid import UUID, uuid4

from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.audit.service import write_provenance
from app.common.gateway import GatewayFetch
from app.common.provenance import ProvenanceEnvelope
from app.fx.models import FXObservation
from app.market_data.models import IndexBarIndex
from app.market_data.normalizer import (
    fx_parquet_path_for,
    index_valuation_path_for,
)
from app.market_data.parquet import ParquetStore
from app.market_data.repository import persist_index_bars


class MacroGatewayPort(Protocol):
    async def fetch_index_history(
        self, index_id: UUID, start: date, end: date
    ) -> GatewayFetch[Any]: ...

    async def fetch_fx_rates(self, start: date, end: date) -> GatewayFetch[Any]: ...

    async def fetch_index_valuation(
        self, index_id: UUID, start: date, end: date
    ) -> GatewayFetch[Any]: ...


class MacroDataService:
    def __init__(
        self,
        gateway: MacroGatewayPort,
        parquet_store: ParquetStore,
        *,
        raw_store=None,
    ) -> None:
        self.gateway = gateway
        self.parquet_store = parquet_store
        self.raw_store = raw_store

    async def sync_index_history(
        self,
        session: Session,
        index_id: UUID,
        start: date,
        end: date,
        *,
        ingestion_run_id: UUID | None = None,
    ):
        fetched = await self.gateway.fetch_index_history(index_id, start, end)
        raw = await self._save_raw(
            fetched, "index_history", f"index_history_{index_id}_{start}_{end}.json"
        )
        bars = [
            row.model_copy(
                update={
                    "provenance": _with_gateway(
                        row.provenance, raw=raw, fetched=fetched,
                        ingestion_run_id=ingestion_run_id,
                    )
                }
            )
            for row in fetched.rows
        ]
        result = persist_index_bars(
            session, bars, parquet_store=self.parquet_store,
            ingestion_run_id=ingestion_run_id,
        )
        return _summary(fetched, result.inserted + result.updated)

    async def sync_fx(
        self,
        session: Session,
        start: date,
        end: date,
        *,
        ingestion_run_id: UUID | None = None,
    ):
        fetched = await self.gateway.fetch_fx_rates(start, end)
        raw = await self._save_raw(
            fetched, "fx", f"fx_USD_CNY_{start}_{end}.json"
        )
        rows: list[dict] = []
        for item in fetched.rows:
            env = _with_gateway(
                item.provenance, raw=raw, fetched=fetched,
                ingestion_run_id=ingestion_run_id,
            )
            provenance = write_provenance(session, env)
            provenance.provenance_id = provenance.provenance_id or uuid4()
            path = fx_parquet_path_for(
                item.base_currency, item.quote_currency, item.as_of, env.provider
            )
            rows.append({
                "base_currency": item.base_currency,
                "quote_currency": item.quote_currency,
                "rate": item.rate,
                "as_of": item.as_of,
                "trade_date": item.trade_date,
                "provider": env.provider,
                "quality_status": env.quality_status,
                "provenance_id": str(provenance.provenance_id),
                "_parquet_path": path,
                "_model": item,
                "_env": env,
                "_provenance_id": provenance.provenance_id,
            })
        self.parquet_store.write_fx_rates(rows)
        written = 0
        for row in rows:
            item = row["_model"]
            stmt = insert(FXObservation).values(
                fx_observation_id=uuid4(),
                base_currency=item.base_currency,
                quote_currency=item.quote_currency,
                rate=item.rate,
                as_of=item.as_of,
                trade_date=item.trade_date,
                provider=row["provider"],
                provenance_id=row["_provenance_id"],
                parquet_path=row["_parquet_path"],
            ).on_conflict_do_update(
                constraint="uq_fx_inst_pair_asof_provider",
                set_={
                    "rate": item.rate,
                    "trade_date": item.trade_date,
                    "provenance_id": row["_provenance_id"],
                    "parquet_path": row["_parquet_path"],
                },
            )
            written += session.execute(stmt).rowcount
        return _summary(fetched, written)

    async def sync_index_valuation(
        self,
        session: Session,
        index_id: UUID,
        start: date,
        end: date,
        *,
        ingestion_run_id: UUID | None = None,
    ):
        fetched = await self.gateway.fetch_index_valuation(index_id, start, end)
        raw = await self._save_raw(
            fetched,
            "index_valuation",
            f"index_valuation_{index_id}_{start}_{end}.json",
        )
        rows: list[dict] = []
        for item in fetched.rows:
            env = _with_gateway(
                item.provenance, raw=raw, fetched=fetched,
                ingestion_run_id=ingestion_run_id,
            )
            provenance = write_provenance(session, env)
            provenance.provenance_id = provenance.provenance_id or uuid4()
            path = index_valuation_path_for(
                item.index_id, item.as_of_date, env.provider
            )
            rows.append({
                "instrument_id": str(item.index_id),
                "as_of_date": item.as_of_date,
                "pe": item.pe,
                "pb": item.pb,
                "source": item.source,
                "provider": env.provider,
                "source_timestamp": env.observed_at,
                "ingested_at": env.retrieved_at,
                "quality_status": env.quality_status,
                "provenance_id": str(provenance.provenance_id),
                "_parquet_path": path,
                "_provenance_id": provenance.provenance_id,
            })
        self.parquet_store.write_index_valuations(rows)
        inserted = 0
        updated = 0
        for row in rows:
            stmt = insert(IndexBarIndex).values(
                index_bar_id=uuid4(),
                instrument_id=UUID(row["instrument_id"]),
                trade_date=row["as_of_date"],
                provider=row["provider"],
                source_timestamp=row["source_timestamp"],
                ingested_at=row["ingested_at"],
                quality_status=row["quality_status"],
                data_kind="VALUATION",
                provenance_id=row["_provenance_id"],
                parquet_path=row["_parquet_path"],
            ).on_conflict_do_update(
                constraint="uq_index_bar_index_inst_date_provider",
                set_={
                    "source_timestamp": row["source_timestamp"],
                    "ingested_at": row["ingested_at"],
                    "quality_status": row["quality_status"],
                    "data_kind": "VALUATION",
                    "provenance_id": row["_provenance_id"],
                    "parquet_path": row["_parquet_path"],
                },
            )
            # PostgreSQL rowcount is one for both paths; the exact insert/update
            # split is not part of the public job contract.
            if session.execute(stmt).rowcount:
                inserted += 1
        return _summary(fetched, inserted + updated)

    async def _save_raw(self, fetched: GatewayFetch[Any], job_name: str, label: str):
        if self.raw_store is None or not fetched.rows:
            return None
        payload = json.dumps(
            [_dump(row) for row in fetched.rows],
            ensure_ascii=False,
            default=str,
        ).encode("utf-8")
        return await self.raw_store.save(fetched.actual_provider, job_name, label, payload)


def _with_gateway(
    env: ProvenanceEnvelope,
    *,
    raw,
    fetched: GatewayFetch[Any],
    ingestion_run_id: UUID | None,
) -> ProvenanceEnvelope:
    flags = list(env.quality_flags)
    update = {
        "provider": fetched.actual_provider,
        "fallback_used": fetched.fallback_used,
        "requested_provider": fetched.requested_provider,
        "fallback_reason": fetched.fallback_reason,
        "quality_flags": flags,
    }
    if raw is not None:
        update.update(raw_hash=raw.raw_hash, raw_object_key=raw.raw_object_key)
    if ingestion_run_id is not None:
        update["ingestion_run_id"] = ingestion_run_id
    return env.model_copy(update=update)


def _dump(row: Any) -> Any:
    if hasattr(row, "model_dump"):
        return row.model_dump(mode="json")
    if hasattr(row, "__dict__"):
        return {key: value for key, value in vars(row).items() if not key.startswith("_")}
    return row


class MacroSyncSummary:
    def __init__(self, written: int, actual_provider: str, fallback_used: bool, fallback_reason: str | None):
        self.written = written
        self.actual_provider = actual_provider
        self.fallback_used = fallback_used
        self.fallback_reason = fallback_reason


def _summary(fetched: GatewayFetch[Any], written: int) -> MacroSyncSummary:
    return MacroSyncSummary(
        written=written,
        actual_provider=fetched.actual_provider,
        fallback_used=fetched.fallback_used,
        fallback_reason=fetched.fallback_reason,
    )


__all__ = ["MacroDataService", "MacroGatewayPort", "MacroSyncSummary"]
