# =====================================================================
# backend/app/fundamentals/normalizer.py —— Financial Fact Normalizer（TS-05 §7.3）
#
# FinancialFactResult → financial_facts 行。四元组（original_value/original_unit/
# value/unit）随 Provider 结果原样保留；PIT 三时点（published_at/observed_at/
# retrieved_at）不在此转换，由 ProvenanceEnvelope 承载。
# =====================================================================
from __future__ import annotations

from uuid import UUID

from app.fundamentals.models import FinancialFact
from app.providers.contracts.fundamentals import FinancialFactResult

__all__ = ["financial_fact_row"]


def financial_fact_row(
    fact: FinancialFactResult,
    provenance_id: UUID,
) -> FinancialFact:
    """FinancialFactResult → financial_facts 行（ts02 §4.3 冻结列）。"""
    return FinancialFact(
        instrument_id=fact.instrument_id,
        metric_code=fact.metric_code,
        period_start=fact.period_start,
        period_end=fact.period_end,
        period_type=fact.period_type,
        statement_type=fact.statement_type,
        report_date=fact.report_date,
        published_at=fact.published_at,
        retrieved_at=fact.retrieved_at,
        original_value=fact.original_value,
        original_unit=fact.original_unit,
        value=fact.value,
        currency=fact.currency,
        unit=fact.unit,
        is_restated=fact.is_restated,
        provider=fact.provenance.provider,
        source_document_id=fact.source_document_id,
        provenance_id=provenance_id,
        quality_status=fact.provenance.quality_status,
    )
