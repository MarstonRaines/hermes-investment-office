---
name: daily-brief
description: 生成可追溯的每日投资简报。
---

# 每日简报

## 输入（from MCP）

- `get_daily_context(market_date)`：先读 freshness、Attention 和引擎版本。
- `get_job_status(job_run_id)`：只用于解释同步是否完成。
- 需要标的事实时使用 `get_market_snapshot`、`get_market_metrics`、`get_portfolio_risk`。

## 工作流

1. 读取 daily context；FAILED 或缺失时停止分析阶段。
2. 只解释 Backend 已标记的 Attention Items，不自行判断阈值。
3. 每个结论携带 as_of、quality 和 provenance。
4. 用户要求保存时调用 `save_daily_brief`，填写 `model_profile`，不保存具体模型名。

## 人工复核

任何 Buy/Hold/Sell、Thesis 更新或交易提案都必须转为人工确认事项。
