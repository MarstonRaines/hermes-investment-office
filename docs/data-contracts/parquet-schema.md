# Parquet Schema 版本化契约（parquet-schema.md）

> 状态：**FROZEN（v1.0，2026-08-23；ohlcva/v1 与 M1.5 实现同步）**
>
> 依据：TS-04 §2（Parquet Schema 版本化细则）、冻结规范 §8.2
>
> 核心规则：**Parquet 数据集是分析型资产，变更 = 新版本目录，永不复用旧版本目录**；`schema.json` 为机器可验证契约（列清单与实际文件不一致 → 加载失败，禁止静默读取）。

---

## 1. 版本化机制（冻结）

```text
data/parquet/
├── ohlcva/v1/            ← 当前版本目录（v2 出现时 v1 保留）
├── financial_history/v1/
├── etf_holdings/v1/
├── index_history/v1/     ← 待 ADR-007 指针表配套
└── <dataset>/v<N>/
      ├── schema.json     ← 机器校验契约（列名/类型/必填）
      ├── _metadata
      └── <hash>/trade_date_month=YYYY-MM/part-<id>.parquet
```

| 规则 | 内容 |
|---|---|
| 版本演进 | schema 变更 → 新增 `v<N+1>/` 目录并保留旧版本；写入方只写当前版本，读取按版本解析 |
| schema.json | `{"schema_version": "v1", "columns": [{name, type, required, description}], "dataset": "ohlcva"}`；加载时逐列比对，不一致 → `SchemaMismatchError` → job FAILED |
| 物理放宽 | v0.1：required 是逻辑契约；物理列一律 nullable，缺口语义（MISSING_FIELD/VALUE_NA）由 quality_flags 标记（ts04 §6.3），不因个别字段缺失丢弃整行 |
| 变更流程 | 列名/类型/必填性变更必须：新增版本目录 + data-contracts 记录迁移说明 + ADR（涉及读取方）|

---

## 2. ohlcva/v1（冻结，与 M1.5 实现一致）

**数据集**：OHLCVA 日线行情（A 股个股 / ETF 场内）

**物理布局**：`parquet/ohlcva/v1/<instrument_id_hash>/trade_date_month=YYYY-MM/part-<instrument_id>.parquet`

**列契约（17 列）**：

| 列 | 类型 | 必填 | 说明 |
|---|---|---|---|
| instrument_id | string | ✅ | 内部稳定标识（PG UUID 字符串）；**禁止存 Provider symbol** |
| trade_date | date32 | ✅ | A 股交易日（Asia/Shanghai 日历日）|
| open / high / low / close | double | ✅ | raw price（CNY/份）|
| volume | double | ✅ | 成交量（股/份）|
| amount | double | ✅ | 成交额（CNY）|
| pre_close | double | ✅ | 昨收（raw price）|
| pct_change | double | ✅ | 百分比数值（-8.1 = -8.1%）|
| turnover_rate | double | — | 换手率（百分比）|
| adj_factor | double | ✅ | 后复权因子（corporate_actions 统一维护）|
| adjusted_close | double | ✅ | 复权价 = raw close × adj_factor（黄金值校验）|
| provider | string | ✅ | 实际取数 provider |
| source_timestamp | timestamp | — | Provider 数据时间戳（UTC）|
| ingested_at | timestamp | ✅ | 系统写入时间（UTC）|
| quality_status | string | ✅ | 行级质量状态 |

**分区策略**：instrument_id 哈希目录 + `trade_date_month=YYYY-MM` 月分区（v0.1 简化；量大后按年归档）。

**查询路径（冻结）**：PG `market_bar_index.parquet_path` 指针 → DuckDB `read_parquet(路径)` → 返回。禁止绕过 PG 指针扫描全目录（指针层承担存在性/质量状态查询）。

---

## 3. financial_history/v1（契约声明；M1.6 实现）

**数据集**：财务事实时间序列（与 PG `financial_facts` 行级同构，但存全量历史便于分析）

**列契约**：

| 列 | 类型 | 必填 | 说明 |
|---|---|---|---|
| financial_fact_id | string | ✅ | 与 PG 行对应（可回溯 provenance）|
| instrument_id | string | ✅ | |
| metric_code | string | ✅ | REVENUE/GROSS_PROFIT/...（冻结清单 §16）|
| period_start / period_end | date32 | ✅ | 报告期 |
| period_type | string | — | Q1/H1/Q3/FY |
| statement_type | string | ✅ | INCOME/BALANCE/CASH_FLOW/OTHER |
| value | double | ✅ | 归一化值（CNY，单位四元组见 unit-normalization.md）|
| currency / unit | string | ✅ | 恒 CNY |
| published_at | timestamp | ✅ | **PIT 关键：披露时点** |
| retrieved_at | timestamp | ✅ | |
| is_restated | bool | ✅ | |
| provider | string | ✅ | |
| provenance_id | string | ✅ | |
| quality_status | string | ✅ | |

**PIT 查询**：`published_at <= as_of` 过滤（与 PG financial_facts 同一语义）。

---

## 4. etf_holdings/v1（契约声明；ETF 季报持仓，Level 1 穿透）

**数据集**：基金披露持仓（季报/半年报/年报）

**列契约**：

| 列 | 类型 | 必填 | 说明 |
|---|---|---|---|
| holding_snapshot_id | string | ✅ | 对应 PG `etf_holding_snapshots` |
| instrument_id | string | ✅ | ETF 的 instrument_id |
| report_period | date32 | ✅ | 报告期 |
| disclosure_date | date32 | ✅ | 披露日（穿透 as_of 依据）|
| rank | int | — | 持仓排名 |
| stock_code | string | ✅ | 底层标的代码（**经 provider_symbols 解析为 instrument_id 前为暂存值**）|
| stock_name | string | — | |
| weight | double | ✅ | 占净值比例（0-100）|
| quantity / market_value | double | — | 持股数/持仓市值 |
| source | string | ✅ | QUARTERLY/HALF_YEAR/ANNUAL |

**穿透分级（冻结规范 §23.1）**：本数据集 = Level 1；Level 2（估算 exposure）由 ETF Engine 计算产出，不落本数据集。

---

## 5. index_history/v1（契约声明；待 ADR-007）

**数据集**：指数点位与估值历史（^GSPC/^NDX/沪深300 等）

**列契约**（草案，ADR-007 冻结后生效）：

| 列 | 类型 | 必填 | 说明 |
|---|---|---|---|
| index_instrument_id | string | ✅ | INDEX 类型 instrument_id |
| trade_date | date32 | ✅ | 对应市场交易日 |
| close | double | ✅ | 指数收盘点位 |
| open/high/low | double | — | |
| volume | double | — | |
| pe / pb | double | — | 指数估值（乐咕/FRED 源，S6 锁定）|
| provider | string | ✅ | |
| quality_status | string | ✅ | |

> PG 指针表设计见 **ADR-007**（index_bar_index，与 market_bar_index 同构）。

---

## 6. 写入与校验（施工规范）

```text
写入流程：
  normalizer → 构造 DataFrame（列名与 schema.json 完全一致）
    → PyArrow 写入 v<N> 目录（分区键 trade_date_month）
    → 写 PG 指针（parquet_path + 行数 + quality）
    → job 成功
加载流程：
  读 schema.json → 逐列比对实际文件 → 不一致抛 SchemaMismatchError
    → 一致 → DuckDB 查询
```

**架构测试挂钩（TS-08 ARCH/CTR）**：schema.json 与实际列一致（每 job）、版本目录单调、旧版本目录不可变。

---

## 7. 变更记录

| 版本 | 日期 | 变更 |
|---|---|---|
| v1.0 | 2026-08-23 | 冻结 ohlcva/v1（与 M1.5 实现同步）；声明 financial_history/v1、etf_holdings/v1、index_history/v1（后者待 ADR-007）|
