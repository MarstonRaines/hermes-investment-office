---
name: stock-research
description: 通过 MCP 完成个股研究并保存可追溯研究笔记。
---

# Stock Research

1. `resolve_instrument` 解析稳定身份。
2. `get_fundamentals`、`get_financial_history`、`get_latest_filings` 获取 PIT 事实。
3. `get_research_context`、`get_evidence`、`search_research` 查历史依据。
4. 只解释 Backend 返回的数值和质量；不静默补缺失事实。
5. 用户要求持久化时调用 `save_research_note`，保留来源和 as_of。

Thesis 不是研究笔记的副作用；创建 revision 前必须得到用户确认，并遵守 freshness 门禁。
