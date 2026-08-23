# 财务单位归一化契约（unit-normalization.md）

> 状态：**FROZEN（v1.0，2026-08-23）**
>
> 依据：TS-04 §3（单位归一化契约）、冻结规范 §14.2（单位归一化冻结）、M0.5 Spike S5 实测

---

## 1. 核心规则（冻结）

```text
base_unit = CNY（金额类）

每个归一化字段保存四元组：
  original_value / original_unit / normalized_value / normalized_unit
```

- 原始单位（元/万元/亿元/美元…）必须保留（`original_unit`），禁止只存归一化值；
- 归一化单位恒为 CNY（金额类）；`normalized_value = original_value × 系数`；
- 确定性：`normalize()` 为纯函数（同一输入 → 同一输出），transform_version 记录规则版本。

## 2. 来源实测结论（M0.5 Spike S5，2026-08-23）

| 数据源 | 报表数值单位 | 系数 | 说明 |
|---|---|---|---|
| **TuShare 三大报表（income/balancesheet/cashflow）** | **元** | **1:1** | 实测：茅台 2025 年报 revenue=168838102514.79（≈1688 亿 ✓）；TuShare 已统一为元 |
| TuShare fina_indicator | 元（每股指标除外：per-share 字段单位为元/股）| 1:1 | |
| AkShare（备用源）| 视接口：元/万元/亿元 混杂 | 待 M1 实测逐接口确认 | 四元组强制保留原始单位 |
| 每股类指标 | 元/股（EPS、per-share）| 1:1 | 不乘股数 |
| 比例/百分比 | 原样（0-1 或百分比，字段注释为准）| 1:1 | pct_change 等按百分比数值存储 |

**结论：TuShare 主源下，金额四元组为恒等映射（系数 1:1，单位标注元→CNY）**；四元组机制主要为 AkShare 等备用源与未来数据源保留。

## 3. 四元组落地位置（三处同构，ts04 §3 冻结）

| 位置 | 表示 |
|---|---|
| PG `financial_facts` | `original_value` / `original_unit` / `value` / `unit` 四列 |
| Parquet `financial_history/v1` | 同名列（value/unit 为归一化；original 列可省——PG 行可回溯）|
| MCP JSON | 数值字段携带 `unit: "CNY"`；原始单位在 provenance quality_flags（如 `UNIT_ORIGINAL=万元`）|

> 简化决策（冻结）：v0.1 主链路（TuShare）四元组 = 恒等映射，PG 四列仍全部写入（保证契约完整）；Parquet 只存归一化列。

## 4. normalize() 函数契约

```python
def normalize(value: Decimal, original_unit: str) -> tuple[Decimal, str]:
    """返回 (normalized_value, normalized_unit)。v1.0 规则：
    - 金额类（unit in {元, 万元, 亿元, CNY, 美元, ...}）→ 折算 CNY（汇率仅 QDII 分析路径使用，
      组合/财务路径不折算——冻结规范 §18）
    - 系数表：元=1, 万元=1e4, 亿元=1e8；CNY=1
    - 未知单位 → 抛 UnitNormalizationError（禁止猜测）
    """
```

- 未知单位 → typed error（**禁止静默猜测**，对齐"无默认参数"工程范式）；
- 汇率折算**不进入本函数**（仅 QDII FX 分析使用，见冻结规范 §18/TS-06 §7）。

## 5. 验收（TS-08 CTR-QUD 组）

| 测试 | 断言 |
|---|---|
| 四元组可重建 | original_value × 系数 == normalized_value；original_unit 保留 |
| 万元/亿元折算 | 10000 万元 → 100000000 CNY；1 亿元 → 100000000 CNY |
| 未知单位拒绝 | "股" 用于金额 → UnitNormalizationError |
| 每股指标不折算 | EPS 4.93 元/股 → 4.93（不乘股本）|
| 确定性 | 同输入两次调用输出一致（黄金值）|

## 6. 变更记录

| 版本 | 日期 | 变更 |
|---|---|---|
| v1.0 | 2026-08-23 | 冻结；S5 实测结论（TuShare 恒元 1:1）写入 |
