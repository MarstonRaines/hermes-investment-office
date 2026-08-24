# =====================================================================
# backend/app/fundamentals/repository.py —— Financial Facts 持久化 + PIT 查询
#
# - persist_financial_facts：facts + provenance 同事务写入（ON CONFLICT DO NOTHING
#   —— 唯一键含 published_at，重述=新行；同键重跑幂等不重复）；
# - get_financial_fact_pit：PIT 查询（ts02 §4.3 冻结 SQL）：
#   published_at <= as_of 取最新一条，as_of 过滤在数据访问层强制执行。
# =====================================================================
from __future__ import annotations

from datetime import date, datetime
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.audit.service import write_provenance
from app.fundamentals.models import FinancialFact
from app.fundamentals.normalizer import financial_fact_row
from app.market_data.parquet import ParquetStore
from app.providers.contracts.fundamentals import FinancialFactResult
from app.providers.raw_store import RawArtifact

__all__ = ["persist_financial_facts", "get_financial_fact_pit"]


def persist_financial_facts(
    session: Session,
    facts: list[FinancialFactResult],
    raw: RawArtifact | None = None,
    ingestion_run_id: UUID | None = None,
    parquet_store: ParquetStore | None = None,
) -> int:
    """facts + provenance 同事务写入 financial_facts（幂等：同键冲突 DO NOTHING）。

    返回写入行数（冲突跳过不计）。调用方负责 commit。
    """
    written = 0
    parquet_rows: list[FinancialFact] = []
    for fact in facts:
        env = fact.provenance
        if raw is not None:
            env = env.model_copy(update={"raw_hash": raw.raw_hash, "raw_object_key": raw.raw_object_key})
        if ingestion_run_id is not None:
            env = env.model_copy(update={"ingestion_run_id": ingestion_run_id})
        prov = write_provenance(session, env)
        prov.provenance_id = prov.provenance_id or uuid4()
        row = financial_fact_row(fact, prov.provenance_id)
        row.financial_fact_id = row.financial_fact_id or uuid4()
        stmt = insert(FinancialFact).values(
            financial_fact_id=row.financial_fact_id,
            instrument_id=row.instrument_id,
            metric_code=row.metric_code,
            period_start=row.period_start,
            period_end=row.period_end,
            period_type=row.period_type,
            statement_type=row.statement_type,
            report_date=row.report_date,
            published_at=row.published_at,
            retrieved_at=row.retrieved_at,
            original_value=row.original_value,
            original_unit=row.original_unit,
            value=row.value,
            currency=row.currency,
            unit=row.unit,
            is_restated=row.is_restated,
            provider=row.provider,
            source_document_id=row.source_document_id,
            provenance_id=row.provenance_id,
            quality_status=row.quality_status,
        ).on_conflict_do_nothing(
            constraint="uq_financial_facts_inst_metric_period",
        )
        result = session.execute(stmt)
        if result.rowcount:
            written += result.rowcount
            parquet_rows.append(row)
    if parquet_store is not None and parquet_rows:
        parquet_store.write_financial_history(parquet_rows)
    return written


def get_latest_financial_fact_pit(
    session: Session,
    instrument_id: UUID,
    metric_code: str,
    as_of: datetime | date,
) -> FinancialFact | None:
    """最新可得报告期的 PIT 事实（客观层指标用：最新 period_end + 最新披露）。

    语义：先取 published_at <= as_of 的所有披露，取 period_end 最新一条；
    同一 period_end 多披露取 published_at 最新（ts02 §4.3 PIT 规则）。
    """
    if isinstance(as_of, date) and not isinstance(as_of, datetime):
        as_of = datetime.combine(as_of, datetime.min.time())
    row = session.execute(
        select(FinancialFact)
        .where(
            FinancialFact.instrument_id == instrument_id,
            FinancialFact.metric_code == metric_code,
            FinancialFact.published_at.is_not(None),
            FinancialFact.published_at <= as_of,
        )
        .order_by(FinancialFact.period_end.desc(), FinancialFact.published_at.desc())
        .limit(1)
    ).scalars().first()
    return row


def get_financial_fact_pit(
    session: Session,
    instrument_id: UUID,
    metric_code: str,
    period_end: date,
    as_of: datetime | date,
) -> FinancialFact | None:
    """PIT 查询（ts02 §4.3 冻结 SQL）：as_of 时点可见的最新一条财务事实。

    可见性判定只用 published_at（NULL 视为未披露，TS-04 §5.1）；
    缺口语义：无行 = 合法状态（未披露），不抛异常。
    """
    if isinstance(as_of, date) and not isinstance(as_of, datetime):
        as_of = datetime.combine(as_of, datetime.min.time())
    stmt = (
        select(FinancialFact)
        .where(
            FinancialFact.instrument_id == instrument_id,
            FinancialFact.metric_code == metric_code,
            FinancialFact.period_end == period_end,
            FinancialFact.published_at.is_not(None),
            FinancialFact.published_at <= as_of,
        )
        .order_by(FinancialFact.published_at.desc(), FinancialFact.created_at.desc())
        .limit(1)
    )
    return session.execute(stmt).scalar_one_or_none()
