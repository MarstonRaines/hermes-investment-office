---
name: stock-research
description: 研究持仓、观察池或任意其他股票；系统内走 MCP，系统外走有来源的公开研究。
---

# 个股研究

1. 先用 `resolve_instrument` 判断标的是否已进入系统。成功时，使用稳定身份继续研究。
2. 系统内标的先用 `get_market_snapshot` 与 `get_price_history` 读取行情；ETF 再读 `get_market_metrics`，股票再读 `get_fundamentals`、`get_financial_history`、`get_latest_filings`。随后用 `get_research_context`、`get_evidence`、`search_research` 查历史依据；若上下文的 `related.thesis_ids` 非空，必须逐一调用 `get_thesis` 读取现有投资观点，不能把“未找到笔记”等同于“没有 Thesis”。
3. 解析失败时继续研究公开来源，优先公司投资者关系页面、交易所/监管披露和可靠数据源；逐项标注来源、发布日期或查询时点。
4. 只解释 Backend 返回的系统内数值和质量；系统外不自行计算精确估值、收益或风险，也不静默补缺失事实。
5. 用户要求持久化时才调用 `save_research_note`，保留来源和 `as_of`；系统外标的可保存为不绑定 `instrument_id` 的研究笔记。

Thesis 不是研究笔记的副作用；创建修订前必须得到用户确认，并遵守 freshness 门禁。系统外标的不得自动创建 Thesis、观察池条目、持仓或交易建议。
