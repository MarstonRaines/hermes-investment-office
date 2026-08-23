# =====================================================================
# backend/app/providers/contracts/filings.py —— 冻结：FilingProvider（TS-05 §2.3）
#
# 数据范围：A 股上市公司公告与定期报告（巨潮/交易所官方披露，cninfo）、
# SEC EDGAR 文档（sec，v0.1 仅当 QDII 底层穿透需要时启用）、基金管理人披露文档。
# 返回元数据 + PDF 下载；PDF 进 Raw Evidence Store（data/documents/）。
# =====================================================================
from __future__ import annotations

import abc
from datetime import date, datetime
from uuid import UUID

from pydantic import BaseModel

from app.providers.contracts.base import BaseProvider, ProviderCapability

__all__ = [
    "FilingMeta",
    "RawDocument",
    "FilingProvider",
]


class FilingMeta(BaseModel):
    document_id: str                 # provider 原生文档 id（source_record_id 同值）
    instrument_id: UUID
    title: str
    document_type: str               # ANNUAL / HALF_YEAR / QUARTERLY / TEMP_ANNOUNCEMENT / ...
    publish_date: date
    source_uri: str | None = None
    source: str                      # cninfo_filings / sec_filings / fund_disclosure
    provider: str
    retrieved_at: datetime
    content_hash: str | None = None


class RawDocument(BaseModel):
    document_id: str
    raw_object_key: str              # data/documents/... 相对路径（§7）
    raw_hash: str                    # sha256
    content_type: str                # application/pdf 等
    size_bytes: int
    fetched_at: datetime


class FilingProvider(BaseProvider):
    capabilities = frozenset({ProviderCapability.FILINGS})

    @abc.abstractmethod
    async def get_filings(
        self,
        instrument_id: UUID,
        since: date,
    ) -> list[FilingMeta]:
        """since 之后发布的公告/定期报告元数据。"""

    @abc.abstractmethod
    async def download_document(
        self,
        document_id: str,
    ) -> RawDocument:
        """下载文档到 Raw Evidence Store，返回 artifact 定位。
        调用方（events 服务）负责写入 filing 元数据表与 provenance。"""
