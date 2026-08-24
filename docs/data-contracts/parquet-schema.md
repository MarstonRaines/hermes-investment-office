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
├── etf_holdings/v1/      ← Level 1；holding_snapshot_id 隔离快照身份
├── etf_nav/v1/           ← PG etf_nav_observations.parquet_path 指针
├── fx/v1/                ← PG fx_observations.parquet_path 指针
├── index_history/v1/     ← ADR-007：PG index_bar_index 指针已实现
├── index_valuation/v1/   ← PG index_bar_index(data_kind=VALUATION)
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
| holding_snapshot_id | string | ✅ | 快照内部身份；目录路径按此 UUID 隔离，禁止不同 source 覆盖 |
| instrument_id | string | ✅ | ETF 的 instrument_id |
| report_period | date32 | ✅ | 报告期 |
| disclosure_date | date32 | ✅ | 披露日（穿透 as_of 依据）|
| source | string | ✅ | QUARTERLY/HALF_YEAR/ANNUAL/OTHER |
| rank | int | — | 持仓排名 |
| provider_symbol | string | — | 底层标的原始展示代码 |
| security_name | string | — | 底层标的名称 |
| holding_instrument_id | string | — | 解析成功的内部身份；失败时保留原始代码并标记 `UNRESOLVED_SYMBOL` |
| weight_pct | double | — | Provider 原始占净值百分比（可审计）|
| weight_ratio | double | — | `weight_pct / 100` 的单行 ratio（0-1）；禁止按当前披露行总和二次归一化 |
| shares / market_value | double | — | 持股数/持仓市值 |
| provider | string | ✅ | 实际取数 provider |
| ingested_at | timestamp | ✅ | 系统写入时间（UTC）|
| holding_level | string | ✅ | 固定为 `LEVEL_1_DISCLOSED`，禁止写入估算值 |
| quality_flags | string | — | 行级标记；当前至少支持 `UNRESOLVED_SYMBOL` |

**穿透分级（冻结规范 §23.1）**：本数据集 = Level 1；Level 2（估算 exposure）预留给 ETF Engine，不落本数据集。
当前 M3 切片尚未实现 Level 2，指标输出必须使用 `status=NOT_IMPLEMENTED`、`is_estimate=false`，不得输出 `ESTIMATE`。
Level 1 confidence 读取 PG header 的 `holdings_json.disclosure_completeness`：`TOP_N=0.6`、`FULL=0.9`；不得用 `holding_count` 猜测完整性。
读取必须先选定 PIT header，再将其唯一 `parquet_path` 传给读取器；禁止对
`etf_holdings/v1` 全量 glob 扫描。

---

## 5. index_history/v1（ADR-007 已冻结并实现）

**数据集**：指数点位历史（^GSPC/^NDX/沪深300 等）；估值序列见 §6.1。

**列契约**：

| 列 | 类型 | 必填 | 说明 |
|---|---|---|---|
| instrument_id | string | ✅ | 冻结的 INDEX 类型 instrument_id；禁止 Provider symbol |
| trade_date | date32 | ✅ | 对应市场交易日 |
| open / high / low / close | double | — | 指数点位 |
| volume | double | — | 若 Provider 提供 |
| currency | string | ✅ | 指数点位币种 |
| provider | string | ✅ | 实际取数 provider |
| source_timestamp | timestamp | — | Provider 时间戳（UTC）|
| ingested_at | timestamp | ✅ | 系统写入时间（UTC）|
| quality_status | string | ✅ | 行级质量状态 |

> PG 指针表为 **ADR-007** 的 `index_bar_index`，与 `market_bar_index` 同构；指数 PE/PB 由指数估值 Provider 单独提供，不伪造为点位列。

## 6. etf_nav/v1、fx/v1、index_valuation/v1

这些数据集的数值载荷写入 Parquet，PostgreSQL 只保存事实元数据、质量、provenance
和 `parquet_path` 指针。所有读取先按 PG 指针做 PIT 筛选，再由 DuckDB 读取对应文件。

| 数据集 | 关键列 | PIT 规则 |
|---|---|---|
| `etf_nav/v1` | `instrument_id`, `nav_date`, `nav`, `published_at`, `provider`, `provenance_id` | `published_at <= as_of`；`nav_date` 与披露时点同时保留 |
| `fx/v1` | `base_currency`, `quote_currency`, `rate`, `as_of`, `trade_date`, `provider`, `provenance_id` | `as_of <= as_of`，并按底层指数交易日裁剪 |
| `index_valuation/v1` | `instrument_id`, `as_of_date`, `pe`, `pb`, `source`, `provider`, `provenance_id` | `as_of_date <= underlying_session_date` |

`index_bar_index.data_kind` 区分 `PRICE` 与 `VALUATION`，两者不得在同一指针唯一键
上互相覆盖；Provider fallback 的实际来源、原因和质量标记仍由 provenance 保存。

---

## 7. 写入与校验（施工规范）

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

## 8. 变更记录

| 版本 | 日期 | 变更 |
|---|---|---|
| v1.0 | 2026-08-23 | 冻结 ohlcva/v1（与 M1.5 实现同步）；声明 financial_history/v1、etf_holdings/v1、index_history/v1 |
| v1.1 | 2026-08-24 | ADR-007 `index_bar_index` 与 `index_history/v1` 落地；ETF Level 1 持仓 Parquet 列契约落地 |
| v1.2 | 2026-08-24 | 快照身份隔离、冻结 `instrument_id`、权重 ratio/未解析符号标记；NAV/FX/指数估值数据集与 PG 指针落地 |
