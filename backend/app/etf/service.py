"""ETF data synchronization and metric orchestration (M3).

The service depends on a Gateway-shaped port only. Provider implementations are
assembled outside this module, so ETF Engine/API code cannot bypass the gateway.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any, Protocol
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.audit.models import ProvenanceRecord
from app.audit.service import write_provenance
from app.calendar.service import CalendarService
from app.common.enums import DataQualityStatus, MarketCode, QuotaStatus, SourceKind
from app.common.gateway import GatewayFetch
from app.common.provenance import ProvenanceEnvelope
from app.etf.config import QDIIAlignmentConfig, ValuationBandConfig
from app.etf.engine import ENGINE_VERSION, ETFEngine, ETFMetricInput
from app.etf.models import (
    ETFHoldingSnapshot,
    ETFMetricSnapshot,
    ETFNavObservation,
    ETFProfile,
)
from app.fx.models import FXObservation
from app.instruments.models import ProviderSymbol
from app.market_data.models import IndexBarIndex, MarketBarIndex
from app.market_data.normalizer import holdings_path_for, nav_parquet_path_for
from app.market_data.parquet import ParquetStore
from app.market_data.repository import persist_index_bars
from app.market_data.service import MarketDataService


class ETFGatewayPort(Protocol):
    async def fetch_market_price(self, instrument_id: UUID, start: date, end: date) -> GatewayFetch[Any]: ...
    async def fetch_nav_history(self, instrument_id: UUID) -> GatewayFetch[Any]: ...
    async def fetch_holdings(self, instrument_id: UUID) -> GatewayFetch[Any]: ...
    async def fetch_quota(self, instrument_id: UUID) -> GatewayFetch[Any]: ...
    async def fetch_index_history(self, index_id: UUID, start: date, end: date) -> GatewayFetch[Any]: ...
    async def fetch_fx_rates(self, start: date, end: date) -> GatewayFetch[Any]: ...
    async def fetch_index_valuation(self, index_id: UUID, start: date, end: date) -> GatewayFetch[Any]: ...


class RawEvidencePort(Protocol):
    async def save(self, provider: str, job_name: str, label: str, content: bytes) -> Any: ...


@dataclass(frozen=True)
class SyncSummary:
    written: int
    actual_provider: str
    fallback_used: bool
    fallback_reason: str | None = None
    quota_status: QuotaStatus | None = None
    quota_observed_at: datetime | None = None
    provenance_ids: tuple[UUID, ...] = ()


class ETFDataService:
    """Persist ETF facts and invoke the deterministic ETF Engine."""

    def __init__(
        self,
        gateway: ETFGatewayPort,
        parquet_store: ParquetStore,
        *,
        raw_store: RawEvidencePort | None = None,
        band_config: ValuationBandConfig,
        alignment_config: QDIIAlignmentConfig | None = None,
        calendar: CalendarService | None = None,
    ) -> None:
        self.gateway = gateway
        self.parquet_store = parquet_store
        self.raw_store = raw_store
        self.engine = ETFEngine(
            band_config=band_config,
            alignment_config=alignment_config,
        )
        self.calendar = calendar or CalendarService()
        self.market_service = MarketDataService(parquet_store)

    async def sync_nav(
        self, session: Session, instrument_id: UUID, *, ingestion_run_id: UUID | None = None
    ) -> SyncSummary:
        fetched = await self.gateway.fetch_nav_history(instrument_id)
        raw = await self._save_raw(
            fetched, "etf_nav", f"etf_nav_{instrument_id}.json"
        )
        rows: list[dict] = []
        for row in fetched.rows:
            env = _with_gateway(
                row.provenance, fetched=fetched, raw=raw,
                ingestion_run_id=ingestion_run_id,
            )
            prov = write_provenance(session, env)
            prov.provenance_id = prov.provenance_id or uuid4()
            path = nav_parquet_path_for(row.instrument_id, row.nav_date, env.provider)
            rows.append({
                "instrument_id": str(row.instrument_id),
                "nav_date": row.nav_date,
                "nav": row.nav,
                "currency": row.currency,
                "published_at": row.published_at,
                "retrieved_at": row.retrieved_at,
                "provider": env.provider,
                "quality_status": env.quality_status,
                "provenance_id": str(prov.provenance_id),
                "_parquet_path": path,
                "_provenance_id": prov.provenance_id,
                "_row": row,
            })
        self.parquet_store.write_etf_nav(rows)
        written = 0
        for payload in rows:
            row = payload["_row"]
            stmt = insert(ETFNavObservation).values(
                nav_observation_id=uuid4(),
                instrument_id=row.instrument_id,
                nav_date=row.nav_date,
                nav=row.nav,
                currency=row.currency,
                published_at=row.published_at,
                retrieved_at=row.retrieved_at,
                provider=payload["provider"],
                provenance_id=payload["_provenance_id"],
                parquet_path=payload["_parquet_path"],
            ).on_conflict_do_update(
                constraint="uq_etf_nav_inst_date_provider",
                set_={
                    "nav": row.nav,
                    "currency": row.currency,
                    "published_at": row.published_at,
                    "retrieved_at": row.retrieved_at,
                    "provenance_id": payload["_provenance_id"],
                    "parquet_path": payload["_parquet_path"],
                },
            )
            result = session.execute(stmt)
            written += getattr(result, "rowcount", 1) or 1
        return _summary(fetched, written)

    async def sync_holdings(
        self, session: Session, instrument_id: UUID, *, ingestion_run_id: UUID | None = None
    ) -> SyncSummary:
        fetched = await self.gateway.fetch_holdings(instrument_id)
        raw = await self._save_raw(
            fetched, "etf_holdings", f"etf_holdings_{instrument_id}.json"
        )
        snapshots = list(fetched.rows)
        parquet_rows: list[dict] = []
        prepared: list[tuple[Any, Any, UUID, str]] = []
        for snapshot in snapshots:
            env = _with_gateway(
                snapshot.provenance, fetched=fetched, raw=raw,
                ingestion_run_id=ingestion_run_id,
            )
            prov = write_provenance(session, env)
            prov.provenance_id = prov.provenance_id or uuid4()
            source = str(getattr(snapshot.source, "value", snapshot.source))
            existing = session.scalar(
                select(ETFHoldingSnapshot).where(
                    ETFHoldingSnapshot.instrument_id == snapshot.instrument_id,
                    ETFHoldingSnapshot.report_period == snapshot.report_period,
                    ETFHoldingSnapshot.source == source,
                )
            )
            holding_snapshot_id = (
                existing.holding_snapshot_id if existing is not None else uuid4()
            )
            path = holdings_path_for(
                snapshot.instrument_id,
                snapshot.report_period,
                holding_snapshot_id=holding_snapshot_id,
            )
            if existing is None:
                existing = ETFHoldingSnapshot(
                    holding_snapshot_id=holding_snapshot_id,
                    instrument_id=snapshot.instrument_id,
                    report_period=snapshot.report_period,
                    disclosure_date=snapshot.disclosure_date,
                    source=source,
                    holding_count=snapshot.holding_count or len(snapshot.holdings),
                    holdings_json=None,
                    parquet_path=path,
                    provenance_id=prov.provenance_id,
                )
                session.add(existing)
            else:
                existing.disclosure_date = snapshot.disclosure_date
                existing.holding_count = snapshot.holding_count or len(snapshot.holdings)
                existing.holdings_json = None
                existing.parquet_path = path
                existing.provenance_id = prov.provenance_id
            prepared.append((snapshot, env, holding_snapshot_id, path))
        # The UUID/header exists in the current transaction before the physical
        # rows are written.  A Parquet failure rolls the transaction back.
        session.flush()
        for snapshot, env, holding_snapshot_id, path in prepared:
            parquet_rows.extend(
                _holding_rows(
                    session, snapshot, env.provider, holding_snapshot_id, path
                )
            )
        self.parquet_store.write_etf_holdings_rows(parquet_rows)
        return _summary(fetched, len(prepared))

    async def sync_quota(
        self, session: Session, instrument_id: UUID, *, ingestion_run_id: UUID | None = None
    ) -> SyncSummary:
        fetched = await self.gateway.fetch_quota(instrument_id)
        raw = await self._save_raw(
            fetched, "etf_quota", f"etf_quota_{instrument_id}.json"
        )
        written = 0
        provenance_ids: list[UUID] = []
        latest_status: QuotaStatus | None = None
        latest_observed_at: datetime | None = None
        for row in fetched.rows:
            env = _with_gateway(
                row.provenance, fetched=fetched, raw=raw,
                ingestion_run_id=ingestion_run_id,
            )
            prov = write_provenance(session, env)
            prov.provenance_id = prov.provenance_id or uuid4()
            provenance_ids.append(prov.provenance_id)
            written += 1
            observed_at = env.observed_at
            if latest_observed_at is None or observed_at >= latest_observed_at:
                latest_observed_at = observed_at
                latest_status = QuotaStatus(
                    getattr(row.quota_status, "value", row.quota_status)
                )
        return SyncSummary(
            written=written,
            actual_provider=fetched.actual_provider,
            fallback_used=fetched.fallback_used,
            fallback_reason=fetched.fallback_reason,
            quota_status=latest_status,
            quota_observed_at=latest_observed_at,
            provenance_ids=tuple(provenance_ids),
        )

    async def sync_index_history(
        self,
        session: Session,
        index_id: UUID,
        start: date,
        end: date,
        *,
        ingestion_run_id: UUID | None = None,
    ) -> SyncSummary:
        fetched = await self.gateway.fetch_index_history(index_id, start, end)
        raw = await self._save_raw(
            fetched, "index_history", f"index_history_{index_id}_{start}_{end}.json"
        )
        bars = [
            _copy_provenance(
                row,
                _with_gateway(
                    row.provenance, fetched=fetched, raw=raw,
                    ingestion_run_id=ingestion_run_id,
                ),
            )
            for row in fetched.rows
        ]
        result = persist_index_bars(
            session, bars, parquet_store=self.parquet_store,
            ingestion_run_id=ingestion_run_id,
        )
        return _summary(fetched, result.inserted + result.updated)

    async def refresh_metrics(
        self,
        session: Session,
        instrument_id: UUID,
        *,
        as_of: datetime,
        quota_status: QuotaStatus | None = None,
        quota_provenance_ids: tuple[UUID, ...] = (),
        quota_observed_at: datetime | None = None,
    ) -> ETFMetricSnapshot:
        """Compute and persist from already-ingested facts only.

        This method is called by a backend job after the sync jobs have landed
        their facts.  It intentionally has no awaitable Provider call.
        """
        return self._compute_metrics(
            session, instrument_id, as_of=as_of,
            quota_status=quota_status,
            quota_provenance_ids=quota_provenance_ids,
            quota_observed_at=quota_observed_at,
        )

    def read_metric(
        self,
        session: Session,
        instrument_id: UUID,
        *,
        as_of: datetime,
    ) -> ETFMetricSnapshot | None:
        """Pure PIT read of a previously persisted derived result."""
        return session.scalar(
            select(ETFMetricSnapshot)
            .where(
                ETFMetricSnapshot.instrument_id == instrument_id,
                ETFMetricSnapshot.as_of <= as_of,
            )
            .order_by(ETFMetricSnapshot.as_of.desc())
            .limit(1)
        )

    def read_metric_provenance(
        self, session: Session, snapshot: ETFMetricSnapshot
    ) -> list[dict[str, Any]]:
        ids = [str(snapshot.provenance_id)]
        ids.extend(str(value) for value in (snapshot.details or {}).get("input_provenance", []))
        refs: list[dict[str, Any]] = []
        for value in dict.fromkeys(ids):
            try:
                row = session.get(ProvenanceRecord, UUID(value))
            except (ValueError, TypeError):
                row = None
            if row is not None:
                refs.append({
                    "provenance_id": str(row.provenance_id),
                    "source_kind": str(getattr(row.source_kind, "value", row.source_kind)),
                    "source": row.source,
                    "provider": row.provider,
                    "as_of_date": row.as_of_date.isoformat() if row.as_of_date else None,
                    "quality_status": str(getattr(row.quality_status, "value", row.quality_status)),
                    "quality_flags": row.quality_flags or [],
                })
        return refs

    def _compute_metrics(
        self,
        session: Session,
        instrument_id: UUID,
        *,
        as_of: datetime,
        quota_status: QuotaStatus | None = None,
        quota_provenance_ids: tuple[UUID, ...] = (),
        quota_observed_at: datetime | None = None,
    ) -> ETFMetricSnapshot:
        profile = session.get(ETFProfile, instrument_id)
        if profile is None:
            raise ValueError(f"ETF profile 不存在: {instrument_id}")

        market_rows = self.market_service.get_ohlcva(
            session, instrument_id, as_of=as_of.date()
        )
        market_row = _latest_before(market_rows, as_of.date(), "trade_date")
        market_date = _value(market_row, "trade_date") or as_of.date()
        market_price = _value(market_row, "close")
        market_pointer = session.scalar(
            select(MarketBarIndex)
            .where(
                MarketBarIndex.instrument_id == instrument_id,
                MarketBarIndex.trade_date <= as_of.date(),
            )
            .order_by(MarketBarIndex.trade_date.desc())
            .limit(1)
        )

        nav_pointer = session.scalar(
            select(ETFNavObservation)
            .where(
                ETFNavObservation.instrument_id == instrument_id,
                ETFNavObservation.published_at.is_not(None),
                ETFNavObservation.published_at <= as_of,
            )
            .order_by(ETFNavObservation.nav_date.desc())
            .limit(1)
        )
        nav_rows = self.parquet_store.read_etf_nav(
            str(instrument_id), as_of=as_of,
            parquet_path=nav_pointer.parquet_path if nav_pointer is not None else None,
        )
        nav_row = _latest_before(nav_rows, as_of.date(), "nav_date")
        nav = _value(nav_row, "nav")
        nav_date = _value(nav_row, "nav_date")

        underlying_row = None
        underlying_previous = None
        index_pointers: list[Any] = []
        fx_row = None
        fx_previous = None
        fx_pointers: list[Any] = []
        valuation_rows: list[dict] = []
        valuation_pointers: list[Any] = []
        if quota_status is None and profile.is_qdii:
            previous = session.scalar(
                select(ETFMetricSnapshot)
                .where(
                    ETFMetricSnapshot.instrument_id == instrument_id,
                    ETFMetricSnapshot.as_of <= as_of,
                )
                .order_by(ETFMetricSnapshot.as_of.desc())
                .limit(1)
            )
            if previous is not None:
                quota_status = QuotaStatus(
                    getattr(previous.quota_status, "value", previous.quota_status)
                )
        quota_status = quota_status or (
            QuotaStatus.UNKNOWN if profile.is_qdii else QuotaStatus.NOT_APPLICABLE
        )

        if profile.is_qdii and profile.underlying_index_id is not None:
            index_rows = self.market_service.get_index_history(
                session, profile.underlying_index_id, as_of=market_date
            )
            index_rows = sorted(index_rows, key=lambda row: _value(row, "trade_date") or date.min)
            if index_rows:
                underlying_row = index_rows[-1]
                underlying_previous = index_rows[-2] if len(index_rows) > 1 else None
            index_pointers = list(session.scalars(
                select(IndexBarIndex)
                .where(
                    IndexBarIndex.instrument_id == profile.underlying_index_id,
                    IndexBarIndex.data_kind == "PRICE",
                    IndexBarIndex.trade_date <= market_date,
                )
                .order_by(IndexBarIndex.trade_date.desc())
            ).all())

            underlying_date = _value(underlying_row, "trade_date")
            fx_pointers = list(session.scalars(
                select(FXObservation)
                .where(
                    FXObservation.base_currency == "USD",
                    FXObservation.quote_currency == "CNY",
                    FXObservation.as_of <= as_of,
                    FXObservation.trade_date <= underlying_date if underlying_date else True,
                )
                .order_by(FXObservation.as_of.desc())
            ).all())
            fx_rows = self.parquet_store.read_fx_rates(
                end=underlying_date, as_of=as_of,
                parquet_paths=[row.parquet_path for row in fx_pointers if row.parquet_path]
                if fx_pointers else None,
            )
            fx_rows = sorted(
                fx_rows,
                key=lambda row: (_value(row, "trade_date") or date.min, _value(row, "as_of") or datetime.min),
            )
            if fx_rows:
                fx_row = fx_rows[-1]
                fx_previous = next(
                    (
                        row for row in reversed(fx_rows[:-1])
                        if _value(row, "trade_date") != _value(fx_row, "trade_date")
                    ),
                    None,
                )
            valuation_rows = self.market_service.get_index_valuations(
                session, profile.underlying_index_id, as_of=underlying_date
            )
            valuation_pointers = list(session.scalars(
                select(IndexBarIndex)
                .where(
                    IndexBarIndex.instrument_id == profile.underlying_index_id,
                    IndexBarIndex.data_kind == "VALUATION",
                    IndexBarIndex.trade_date <= underlying_date if underlying_date else True,
                )
                .order_by(IndexBarIndex.trade_date.desc())
            ).all())
        underlying_date = _value(underlying_row, "trade_date")
        fx_as_of = _value(fx_row, "as_of")
        input_data = ETFMetricInput(
            instrument_id=instrument_id,
            as_of=as_of,
            market_date=market_date,
            market_price_cny=market_price,
            is_qdii=profile.is_qdii,
            underlying_index_id=profile.underlying_index_id,
            nav=nav,
            nav_date=nav_date,
            reference_nav_basis="OFFICIAL_NAV_T1" if nav_row is not None else None,
            underlying_session_date=underlying_date,
            index_close=_value(underlying_row, "close"),
            index_previous_close=_value(underlying_previous, "close"),
            fx_rate=_value(fx_row, "rate"),
            fx_previous_rate=_value(fx_previous, "rate"),
            fx_as_of=fx_as_of,
            market_nav_distance=self.calendar.trading_day_distance(
                session, market_date, nav_date, market=MarketCode.CN
            ),
            underlying_market_distance=_cross_market_distance(
                self.calendar, session, underlying_date, market_date
            ),
            fx_underlying_distance=self.calendar.trading_day_distance(
                session, _value(fx_row, "trade_date"), underlying_date, market=MarketCode.US
            ),
            nav_underlying_distance=_cross_market_distance(
                self.calendar, session, nav_date, underlying_date
            ),
            index_pe=_latest_value(valuation_rows, "pe", underlying_date),
            index_pb=_latest_value(valuation_rows, "pb", underlying_date),
            pe_percentile=_percentile(
                _latest_value(valuation_rows, "pe", underlying_date),
                [_value(row, "pe") for row in valuation_rows],
                self.engine.band_config.min_history,
            ),
            pb_percentile=_percentile(
                _latest_value(valuation_rows, "pb", underlying_date),
                [_value(row, "pb") for row in valuation_rows],
                self.engine.band_config.min_history,
            ),
            quota_status=quota_status,
            net_value_t1=nav,
        )
        output = self.engine.compute(input_data)

        pointer_rows = [
            market_pointer, nav_pointer, *index_pointers, *fx_pointers,
            *valuation_pointers,
        ]
        pointer_rows = [row for row in pointer_rows if row is not None]
        provenance_ids = [
            str(row.provenance_id) for row in pointer_rows if row.provenance_id
        ]
        provenance_ids.extend(str(value) for value in quota_provenance_ids)
        source_records = [
            session.get(ProvenanceRecord, UUID(value)) for value in provenance_ids
        ]
        source_records = [row for row in source_records if row is not None]
        source_flags = [
            flag for row in source_records for flag in (row.quality_flags or [])
        ]
        flags = _dedupe(output.quality_flags + source_flags)
        quality_score = min(
            [row.quality_score for row in source_records] or [Decimal("0")]
        )
        quality_status = output.quality_status
        statuses = {
            DataQualityStatus(getattr(row.quality_status, "value", row.quality_status))
            for row in source_records
        }
        if DataQualityStatus.REJECTED in statuses or DataQualityStatus.CONFLICT in statuses:
            quality_status = DataQualityStatus.REJECTED
            flags.append("INPUT_QUALITY_REJECTED")
        elif DataQualityStatus.STALE in statuses:
            quality_status = DataQualityStatus.STALE
        flags = _dedupe(flags)
        input_payload = _input_payload(
            input_data,
            source_records,
            self.engine.band_config.config_hash,
            self.engine.alignment_config.config_hash,
        )
        input_hash = "sha256:" + hashlib.sha256(
            json.dumps(input_payload, sort_keys=True, default=str).encode()
        ).hexdigest()
        derived_env = ProvenanceEnvelope(
            source_kind=SourceKind.DERIVED_ENGINE,
            source="etf_metrics",
            provider="internal",
            observed_at=as_of,
            retrieved_at=datetime.now(UTC),
            as_of_date=market_date,
            quality_score=quality_score,
            quality_status=quality_status,
            quality_flags=flags,
            transform_version=ENGINE_VERSION,
            evidence_ids=provenance_ids,
        )
        prov = write_provenance(session, derived_env)
        prov.provenance_id = prov.provenance_id or uuid4()
        freshness = _freshness(as_of, market_date, quality_status, flags)
        details = output.details | {
            "source": "etf_metrics",
            "freshness": freshness,
            "data_freshness": freshness["status"],
            "level_0": {
                "status": "OBSERVED",
                "is_estimate": False,
                "as_of_date": market_date.isoformat(),
                "confidence": "1.0",
            },
            "level_1": _latest_holding_metadata(session, instrument_id, as_of.date()),
            "level_2": {
                "status": "ESTIMATE",
                "is_estimate": True,
                "confidence": "LOW",
                "description": "未将估算 exposure 混入真实披露持仓",
            },
            "input_provenance": provenance_ids,
            "alignment_config_hash": self.engine.alignment_config.config_hash,
            "quota_observed_at": quota_observed_at.isoformat()
            if quota_observed_at is not None else None,
        }
        snapshot = ETFMetricSnapshot(
            etf_metric_snapshot_id=uuid4(),
            instrument_id=instrument_id,
            as_of=as_of,
            market_date=market_date,
            is_qdii=profile.is_qdii,
            underlying_index_id=profile.underlying_index_id,
            market_price_cny=market_price,
            nav=nav,
            nav_date=nav_date,
            underlying_session_date=underlying_date,
            premium_discount=output.premium_discount,
            fx_contribution=output.fx_contribution,
            fx_as_of=fx_as_of,
            quota_status=output.quota_status,
            net_value_t1=input_data.net_value_t1,
            index_pe=input_data.index_pe,
            index_pb=input_data.index_pb,
            reference_nav_basis=output.reference_nav_basis,
            valuation_band=output.valuation_band,
            band_basis=output.band_basis,
            band_inputs=output.band_inputs or None,
            band_thresholds_hash=output.band_thresholds_hash,
            details=details,
            engine_version=ENGINE_VERSION,
            input_hash=input_hash,
            quality_score=quality_score,
            quality_status=quality_status,
            quality_flags=flags,
            provenance_id=prov.provenance_id,
        )
        session.add(snapshot)
        session.flush()
        return snapshot

    async def _save_raw(
        self, fetched: GatewayFetch[Any], job_name: str, label: str
    ) -> Any:
        if self.raw_store is None or not fetched.rows:
            return None
        payload = json.dumps(
            [_dump(row) for row in fetched.rows], ensure_ascii=False, default=str
        ).encode("utf-8")
        return await self.raw_store.save(fetched.actual_provider, job_name, label, payload)


def _summary(fetched: GatewayFetch[Any], written: int) -> SyncSummary:
    return SyncSummary(
        written=written,
        actual_provider=fetched.actual_provider,
        fallback_used=fetched.fallback_used,
        fallback_reason=fetched.fallback_reason,
    )


def _with_raw(env: ProvenanceEnvelope, raw: Any) -> ProvenanceEnvelope:
    if raw is None:
        return env
    return env.model_copy(update={"raw_hash": raw.raw_hash, "raw_object_key": raw.raw_object_key})


def _with_gateway(
    env: ProvenanceEnvelope,
    *,
    fetched: GatewayFetch[Any],
    raw: Any,
    ingestion_run_id: UUID | None = None,
) -> ProvenanceEnvelope:
    update = {
        "provider": fetched.actual_provider,
        "fallback_used": fetched.fallback_used,
        "requested_provider": fetched.requested_provider,
        "fallback_reason": fetched.fallback_reason,
    }
    if raw is not None:
        update.update(raw_hash=raw.raw_hash, raw_object_key=raw.raw_object_key)
    if ingestion_run_id is not None:
        update["ingestion_run_id"] = ingestion_run_id
    return env.model_copy(update=update)


def _copy_provenance(row: Any, env: ProvenanceEnvelope) -> Any:
    return row.model_copy(update={"provenance": env})


def _dump(row: Any) -> Any:
    if hasattr(row, "model_dump"):
        return row.model_dump(mode="json")
    if hasattr(row, "__dict__"):
        return {key: value for key, value in vars(row).items() if not key.startswith("_")}
    return row


def _value(row: Any, *names: str) -> Any:
    value = row
    for name in names:
        if value is None:
            return None
        value = value.get(name) if isinstance(value, dict) else getattr(value, name, None)
    return value


def _latest_before(rows: Iterable[Any], cutoff: date, date_field: str) -> Any:
    valid = [row for row in rows if (_value(row, date_field) or date.min) <= cutoff]
    return max(valid, key=lambda row: _value(row, date_field) or date.min) if valid else None


def _latest_published_nav(rows: Iterable[Any], as_of: datetime) -> Any:
    valid = [
        row for row in rows
        if _value(row, "published_at") is not None
        and _value(row, "published_at") <= as_of
    ]
    return max(valid, key=lambda row: (_value(row, "nav_date") or date.min)) if valid else None


def _latest_value(rows: Iterable[Any], field: str, cutoff: date | None) -> Decimal | None:
    if cutoff is None:
        return None
    valid = [
        row for row in rows
        if (_value(row, "as_of_date") or date.min) <= cutoff
        and _value(row, field) is not None
    ]
    if not valid:
        return None
    row = max(valid, key=lambda item: _value(item, "as_of_date") or date.min)
    return _value(row, field)


def _percentile(current: Decimal | None, values: list[Decimal | None], min_history: int) -> Decimal | None:
    clean = sorted(value for value in values if value is not None)
    if current is None or len(clean) < min_history:
        return None
    return Decimal(sum(value <= current for value in clean)) / Decimal(len(clean))


def _cross_market_distance(
    calendar: CalendarService,
    session: Session,
    left: date | None,
    right: date | None,
) -> int | None:
    """Conservative distance across CN/US calendars; no weekday fallback."""
    distances = [
        calendar.trading_day_distance(session, left, right, market=MarketCode.CN),
        calendar.trading_day_distance(session, left, right, market=MarketCode.US),
    ]
    if any(value is None for value in distances):
        return None
    return max(value for value in distances if value is not None)


def _dedupe(values: Iterable[str]) -> list[str]:
    return list(dict.fromkeys(str(value) for value in values))


def _latest_holding_metadata(
    session: Session,
    instrument_id: UUID,
    as_of: date,
) -> dict[str, Any] | None:
    row = session.scalar(
        select(ETFHoldingSnapshot)
        .where(
            ETFHoldingSnapshot.instrument_id == instrument_id,
            ETFHoldingSnapshot.disclosure_date <= as_of,
        )
        .order_by(ETFHoldingSnapshot.disclosure_date.desc())
        .limit(1)
    )
    if row is None:
        return None
    return {
        "as_of_date": row.disclosure_date.isoformat(),
        "status": "DISCLOSED",
        "is_estimate": False,
        "source": str(getattr(row.source, "value", row.source)),
        "confidence": "1.0" if row.holding_count and row.holding_count > 10 else "0.6",
        "parquet_path": row.parquet_path,
    }


def _input_payload(
    inputs: ETFMetricInput,
    source_records: list[ProvenanceRecord],
    band_config_hash: str,
    alignment_config_hash: str,
) -> dict[str, Any]:
    return {
        "inputs": {
            key: str(value) if isinstance(value, (Decimal, UUID, date, datetime)) else value
            for key, value in inputs.__dict__.items()
        },
        "source_records": [
            {
                "id": str(row.provenance_id),
                "source": row.source,
                "provider": row.provider,
                "quality_status": str(row.quality_status),
            }
            for row in source_records
        ],
        "band_config_hash": band_config_hash,
        "alignment_config_hash": alignment_config_hash,
        "engine_version": ENGINE_VERSION,
    }


def _holding_rows(
    session: Session,
    snapshot: Any,
    provider: str,
    holding_snapshot_id: UUID,
    path: str,
) -> list[dict]:
    weights = [item.weight_pct for item in snapshot.holdings]
    ratios = _normalize_weights(weights)
    rows: list[dict] = []
    for index, item in enumerate(snapshot.holdings):
        ratio = ratios[index]
        resolved_id = item.instrument_id
        quality_flags: list[str] = []
        if resolved_id is None and item.provider_symbol:
            resolved_id = session.scalar(
                select(ProviderSymbol.instrument_id).where(
                    ProviderSymbol.provider == provider,
                    ProviderSymbol.symbol == item.provider_symbol,
                    ProviderSymbol.valid_to.is_(None),
                ).limit(1)
            )
        if resolved_id is None:
            quality_flags.append("UNRESOLVED_SYMBOL")
        if item.weight_pct is not None and item.weight_pct < 0:
            quality_flags.append("INVALID_WEIGHT")
        rows.append({
            "holding_snapshot_id": str(holding_snapshot_id),
            "instrument_id": str(snapshot.instrument_id),
            "report_period": snapshot.report_period,
            "disclosure_date": snapshot.disclosure_date,
            "source": str(getattr(snapshot.source, "value", snapshot.source)),
            "rank": item.rank,
            "provider_symbol": item.provider_symbol,
            "security_name": item.security_name,
            "holding_instrument_id": (
                str(resolved_id) if resolved_id is not None else item.provider_symbol
            ),
            "weight_pct": item.weight_pct,
            "weight_ratio": ratio,
            "market_value": item.market_value,
            "shares": item.shares,
            "provider": provider,
            "ingested_at": snapshot.provenance.retrieved_at,
            "holding_level": "LEVEL_1_DISCLOSED",
            "quality_flags": ",".join(quality_flags),
            "_parquet_path": path,
        })
    return rows


def _normalize_weights(values: list[Any]) -> list[float | None]:
    clean = [
        Decimal(str(value)) if value is not None and Decimal(str(value)) >= 0 else None
        for value in values
    ]
    valid = [value for value in clean if value is not None]
    if not valid:
        return [None for _ in values]
    scale = Decimal("100") if sum(valid) > Decimal("1.5") else Decimal("1")
    ratios = [value / scale if value is not None else None for value in clean]
    total = sum(value for value in ratios if value is not None)
    if total <= 0:
        return [None for _ in values]
    return [float(value / total) if value is not None else None for value in ratios]


def _freshness(
    as_of: datetime,
    market_date: date,
    quality_status: DataQualityStatus,
    flags: list[str],
) -> dict[str, Any]:
    age_days = max((as_of.date() - market_date).days, 0)
    if quality_status == DataQualityStatus.REJECTED:
        status = "FAILED"
    elif quality_status == DataQualityStatus.STALE or age_days > 5:
        status = "STALE"
    elif flags or age_days > 1:
        status = "WARNING"
    else:
        status = "OK"
    return {
        "status": status,
        "market_date": market_date.isoformat(),
        "age_days": age_days,
    }


__all__ = ["ETFGatewayPort", "RawEvidencePort", "SyncSummary", "ETFDataService"]
