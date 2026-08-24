---
name: etf-analysis
description: 分析 ETF、QDII 四日期、折溢价和持仓穿透结果。
---

1. 用 `resolve_instrument` 确认 ETF 身份。
2. 用 `get_market_metrics` 读取 Level 0/1/2、premium、FX、quota、四日期、freshness 和 provenance。
3. 用 `get_market_snapshot` / `get_price_history` 补充市场观察。
4. QDII quota 为 UNKNOWN 时保留 UNKNOWN 并说明影响，不改写成失败。
5. 不暴露物理存储路径、不扫描数据目录、不把估算 Level 2 伪装为事实。
