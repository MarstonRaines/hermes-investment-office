---
name: portfolio-review
description: 查看组合、持仓、风险和暴露，形成可审计回顾。
---

1. `get_portfolio` 获取组合模式和最新快照。
2. `get_positions` 获取交易流水派生的持仓。
3. `get_portfolio_exposure` 与 `get_portfolio_risk` 获取确定性暴露、集中度和回撤。
4. 只引用 Backend 返回的数值；REAL/PAPER 分开叙述。
5. 如需行动，只能调用 `create_trade_proposal`；不得执行真实交易。
