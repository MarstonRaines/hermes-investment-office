---
version: 1
slug: "dashboard-app-py"
primary_target: "dashboard/app.py"
related_targets: ["dashboard/requirements.txt"]
---

范围：完整 Dashboard 与 localhost 人工记账入口；桌面大屏优先，兼顾窄屏可读。

模式：Operate。唯一用户在早晨快速判断数据是否可信、今天是否有事项需要处理，并在需要时继续查看标的、组合、研究历史或手工记账。

任务顺序：系统状态与数据新鲜度 → 关注事项 → 结构化日报 → 观察池/组合概览 → 详情与回顾。分析区只读；人工记账区必须与分析区明显隔离并要求明确确认。

内容：全部来自 Backend REST。展示真实空状态、失败状态、降级状态和 provenance；示意稿数字不得进入产品。

方向：标准金融 Dashboard；`.impeccable/mocks/approved/dashboard-first-viewport.png` 是历史方向参考，不是用户逐像素批准稿或最终验收基准。当前四入口、真实工作流与 `.impeccable/review/desktop.png`、`.impeccable/review/mobile.png` 为事实源。明暗跟随系统，中文为主，高信息密度，细边框、克制圆角、成熟图表与表格。

记忆点：任何投资数字都能继续展开它的 as_of、freshness、provenance 与引擎版本；任何真实组合变更都明确标记为人工行为。

未决：无。用户已授权本轮按已确认契约直接完成施工与运行态初始化。
