# =====================================================================
# backend/app/providers/contracts/news.py —— 冻结：NewsProvider（TS-05 §2.6）
#
# 状态：v0.1 不实现。冻结规范 §12.4：新闻/事件第一阶段走
# Hermes Web Research + 结构化事件记录；若日报 cron 的新闻研究在
# 3 分钟硬中断内无法完成，将 news collection 下沉为 Backend Job（ADR 记录）。
# 接口在此冻结，供后续实现。
# =====================================================================
from __future__ import annotations

import abc
from datetime import datetime

from pydantic import BaseModel

from app.providers.contracts.base import (
    BaseProvider,
    ProvenanceEnvelope,
    ProviderCapability,
)

__all__ = [
    "NewsItemResult",
    "NewsProvider",
]


class NewsItemResult(BaseModel):
    news_id: str
    title: str
    url: str | None = None
    published_at: datetime | None = None
    retrieved_at: datetime
    source: str                    # 站点/渠道名
    summary: str | None = None
    content_hash: str | None = None
    provenance: ProvenanceEnvelope


class NewsProvider(BaseProvider):
    capabilities = frozenset({ProviderCapability.NEWS})

    @abc.abstractmethod
    async def search(
        self,
        query: str,
        since: datetime,
        limit: int = 20,
    ) -> list[NewsItemResult]:
        """关键词检索。v0.1 不实现；接口冻结供后续。"""
