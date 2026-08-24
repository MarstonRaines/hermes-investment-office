---
name: valuation-analysis
description: 通过确定性估值引擎运行带显式假设的估值。
---

1. 先用 `get_fundamentals` 和 `get_market_snapshot` 获取输入时点。
2. 向用户确认所有必要假设，再调用 `run_valuation`；缺失假设不得自动补默认值。
3. 用 `get_latest_valuation` 或 `get_valuation_history` 读取结果、状态、引擎版本和输入哈希。
4. 解释 bear/base/bull 和 margin of safety 时引用 provenance；ETF 不走股票估值模型。
