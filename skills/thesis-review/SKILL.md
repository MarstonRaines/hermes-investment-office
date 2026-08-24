---
name: thesis-review
description: 复核 Thesis 版本、假设和证据，并等待人工确认状态变化。
---

1. `get_thesis` 以 as_of 读取 PIT revision。
2. `get_evidence`、`get_latest_filings` 和 `get_fundamentals` 核对可验证条件。
3. 先向用户报告 REAFFIRM/REVISE/INVALIDATE 建议和证据，再调用 `record_thesis_review`。
4. 修改假设用 `update_thesis_assumption`，创建新版本用 `create_thesis_revision`；必须提供 freshness。
5. 不覆盖历史 revision，不把模型推断写成事实。
