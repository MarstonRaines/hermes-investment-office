# Hermes Investment Office 后端架构冻结规范 v1.0（Consolidated）

> 状态：**FROZEN / 冻结（合并基线）**
>
> 版本：v1.0 Consolidated
>
> 类型：单文件合并基线（施工与验收的唯一权威输入）
>
> 来源：v0.1 原始冻结规范 + Revision R1（v0.2）+ Revision R2（v0.2.1）+ 三轮 Hermes Runtime Audit 的工程增量
>
> v1.0 澄清修订（2026-08-23）：明确"美股指数 ETF"指 A 股场内跟踪美股指数（S&P 500 / Nasdaq 100 等）的 QDII ETF（如 513500 标普500ETF、513100 纳指ETF），不包含在美股市场直接上市的 ETF（SPY / VOO / QQQ 等）。修订涉及第 1、10、12、14、18、23、26、47、49 章。
>
> 版本关系：
>
> ```text
> v1.0 (Consolidated) = v0.1 + R1 (v0.2) + R2 (v0.2.1)
> ```
>
> 冲突优先级：
>
> ```text
> v1.0 (Consolidated) > v0.2.1 > v0.2 > v0.1
> ```
>
> 本文件为施工（Codex / DSH）、测试与验收的上位架构约束。除非显式进入 Architecture Change 流程（ADR），否则后续实现不得违反本文件中的冻结项。原三份版本文件保留为历史存档，后续所有工作以本文件为准。

---

# 1. 项目目标

构建一个面向长期投资的个人 AI 投资研究系统：

- 投资范围：
  - A 股个股
  - A 股 ETF（宽基 / 行业，如沪深300、消费等）
  - A 股场内跟踪美股指数（S&P 500 / Nasdaq 100 等）的 QDII ETF
- 明确排除：在美股市场直接上市交易的 ETF（SPY / VOO / QQQ 等）不属于 v0.1 范围
- 投资周期：
  - 以 3～10 年长期持有为主要尺度
- 核心投资逻辑：
  - 公司质量
  - 安全边际
  - 合理估值
  - 长期持有
  - 持续验证 Investment Thesis
- 卖出逻辑：
  - Thesis 被证伪
  - 明显高估
- 不以短期股价预测、技术择时、高频交易作为核心能力
- AI 可：
  - 搜集信息
  - 进行研究
  - 给出 Buy / Hold / Reduce 建议
  - 给出目标仓位
  - 维护 Shadow Portfolio
- AI 不直接进行真实账户自动下单

系统应支持：

1. 每日自动收集持仓与观察池信息；
2. 自动更新行情、OHLCVA、财务数据、公告与重大事件；
3. 自动计算估值、组合状态与风险指标；
4. Hermes 结合投资 Skills 对重要变化进行解释；
5. 维护长期 Investment Thesis；
6. 生成每日投资日报；
7. 维护真实组合与 Paper / Shadow Portfolio；
8. 提供 Web Dashboard；
9. 所有重要结论均可追溯到数据来源、计算版本与 Thesis Revision。

本系统的产品定义是：

> **以 Hermes 为核心中枢、以确定性投资后端为事实与计算基础、以长期 Investment Thesis 为核心状态、能够持续进行基本面研究、估值、组合跟踪、风险分析和每日投资决策辅助的个人 AI Private Investment Office。**

本项目不是：AI 自动炒股机器人；短期预测系统；高频交易系统；自动交易 Agent。

---

# 2. 总体架构判断

项目采用：

> **Hermes-first 架构**

而不是：LangAlpha-first / Vibe-Trading-first。

核心原则：

- **Hermes 是控制平面（思考、编排、解释）**
- **Investment Backend 是事实与计算平面（数据、状态、计算、审计）**
- **AI Berkshire / LangAlpha 提供方法论与 Skills 参考**
- **Dashboard 是展示层，不承担核心业务逻辑**
- **Scheduler 负责可靠运行**
- **Data Contract 负责长期稳定**

---

# 3. 总体架构

```text
┌──────────────────────────────────────────────────────────┐
│                     用户 / 投资者                        │
│                                                          │
│      Hermes CLI / Hermes Desktop / Hermes Web            │
│                   Streamlit Dashboard                    │
└────────────────────────────┬─────────────────────────────┘
                             │
                             ▼
┌──────────────────────────────────────────────────────────┐
│                    HERMES CONTROL PLANE                  │
│                                                          │
│ Investment Profile                                       │
│ Memory · Skills · Cron · MCP · Model Routing             │
│                                                          │
│  ┌────────────────────────────────────────────────────┐  │
│  │ Investment Skills                                  │  │
│  │                                                    │  │
│  │ investment-runtime-policy                         │  │
│  │ AI Berkshire (adapted)                             │  │
│  │ LangAlpha-derived Skills                           │  │
│  │ Custom Investment Policy                           │  │
│  │ Daily Brief / ETF / Thesis / Valuation Skills      │  │
│  └────────────────────────────────────────────────────┘  │
└────────────────────────────┬─────────────────────────────┘
                             │ MCP（仅 Typed Tools）
                             ▼
┌──────────────────────────────────────────────────────────┐
│                 INVESTMENT BACKEND                       │
│                  FastAPI + MCP Endpoint                  │
│                                                          │
│ Instrument Master                                        │
│ Data Ingestion & Reconciliation                          │
│ Fundamental Engine                                       │
│ Market Data Engine                                       │
│ Valuation Engine                                         │
│ ETF Engine                                               │
│ Portfolio Accounting                                     │
│ Risk Engine                                              │
│ Thesis / Research Store                                  │
│ Daily Context Builder                                    │
│ Provenance / Audit                                       │
│ Backend Scheduler + Job Worker                           │
│ Trading Calendar · FX · Corporate Actions                │
└─────────────┬──────────────────┬─────────────────────────┘
              │                  │
              ▼                  ▼
     ┌────────────────┐   ┌────────────────────┐
     │ PostgreSQL     │   │ Parquet + DuckDB   │
     │ (Container     │   │                    │
     │  内部网络)      │   │ OHLCVA             │
     │ Portfolio      │   │ Historical Factors │
     │ Thesis         │   │ Financial History  │
     │ Research       │   │ Screening Data     │
     │ Valuation      │   │ ETF Data           │
     │ Audit          │   │                    │
     └────────────────┘   └────────────────────┘

              │
              ▼

     ┌───────────────────────────┐
     │ Raw Evidence / Documents  │
     │                           │
     │ 财报 PDF                  │
     │ 公告                      │
     │ SEC Filings               │
     │ 网页快照                  │
     │ Provider 原始响应         │
     └───────────────────────────┘
```

## 3.1 数据访问路径（物理隔离）

```text
Hermes → MCP → Backend → Database
```

数据库只能被 Backend Container 访问。Hermes 不允许直接连接 PostgreSQL、直接修改数据文件、绕过 MCP 调用内部逻辑（详见第 5 章 Runtime Boundary）。

---

# 4. 最高优先级冻结原则（Architecture Contract）

以下内容属于最高级 Architecture Contract，任何修改必须通过 ADR。

## 4.1 Hermes 永远不是 Financial Source of Truth

Hermes Memory 可以保存：

- 用户长期投资偏好
- 某家公司长期投资逻辑的自然语言摘要
- 用户对某个行业的研究偏好
- 历史讨论上下文

但以下信息不得把 Hermes Memory 作为唯一事实来源：

- 当前仓位 / 持仓成本 / 当前市值 / 现金余额 / 收益率
- PE / PB / 财务指标 / 当前价格 / 估值结果
- 目标仓位 / Portfolio Risk / 最新公告状态 / 真实交易流水

这些数据必须来自 Investment Backend。Hermes 侧的任何投资数值回答，必须以"现查 MCP"为默认行为，禁止凭 Memory 或会话记忆作答（见第 5 章 Hermes Runtime Policy）。

## 4.2 LLM 永远不负责 Decision-sensitive Arithmetic

凡是会影响投资判断的精确计算，必须由确定性代码完成，包括但不限于：

- PE / PB / EV / EBITDA / ROE / FCF Yield / CAGR
- DCF / DDM / Owner Earnings
- 历史估值分位
- 组合收益率 / 仓位 / 最大回撤 / Correlation
- ETF overlap / 行业暴露 / Target Weight 的规则计算

Hermes 的职责是：提出合理假设、解释计算结果、比较情景、综合 Investment Policy、生成研究结论。Hermes 不负责"心算"。

## 4.3 所有重要投资结论必须可审计

任何重要投资结论必须能够追溯至：

1. 原始数据来源；
2. 数据获取时间；
3. 数据版本；
4. 计算引擎版本；
5. 估值假设；
6. 使用的 Investment Thesis 版本；
7. 生成结论的时间；
8. 对应 Research Evidence。

禁止产生无法解释来源的：Buy / Hold / Reduce / Target Weight / Fair Value / Thesis Broken。

追溯链：

```text
Data Source → Calculation Version → Thesis Revision → Final Conclusion
```

## 4.4 Runtime Boundary Enforcement（物理隔离）

逻辑隔离之外，必须物理隔离。

Hermes 不允许：

- 直接连接 PostgreSQL；
- 直接修改数据文件（data/ 目录）；
- 绕过 MCP 调用内部逻辑。

数据库访问路径唯一：

```text
Hermes → MCP → Backend → Database
```

实现要求：

- PostgreSQL / DuckDB 数据目录只暴露给 Backend Container（docker-compose 内部网络），**不得映射端口到宿主机**；
- Backend 的 MCP/API 端口绑定 127.0.0.1（单机部署）；
- Hermes 的 terminal 工具在物理上无法触达数据库。

## 4.5 Evidence First

所有投资结论必须可追溯（与 4.3 相同约束的另一种表述，用于 skill 与施工文档中的一致性引用）。

---

# 5. Hermes 的职责（Control Plane）

Hermes 是整个系统的：

> **Control Plane / Investment Manager Agent**

主要负责：

- 用户交互
- 任务编排
- Skills 调用
- MCP Tool 调用（仅 Typed Tools）
- Web Research
- 新闻与事件解释
- 基本面研究
- Investment Thesis 更新建议
- Daily Brief 生成（仅 LLM 部分）
- Weekly Portfolio Review
- Quarterly Thesis Review
- 组合建议
- Shadow Portfolio 管理
- 模型路由（按 task_class 选择 model_profile，禁止在业务逻辑中硬编码具体模型名）

Hermes 不负责：

- 触发数据同步 / 等待计算任务 / 调度确定性 Engine（由 Backend Scheduler 负责）
- 直接维护业务数据库
- 精确财务计算
- Portfolio Accounting
- 行情数据存储
- 数据清洗
- 真实账户自动交易

## 5.1 Hermes Runtime Policy（investment-runtime-policy skill）

Investment Profile 必须加载 `investment-runtime-policy` skill，固化以下规则：

1. 所有事实数据必须通过 MCP 获取；
2. 禁止 terminal 直连数据库（含 psql / duckdb CLI / 直接读写 data/ 文件）；
3. 禁止依赖 Memory 保存事实（Memory 只存偏好与摘要）；
4. 禁止 LLM 心算关键指标；
5. 投资结论必须引用 provenance；
6. 数值类回答默认"现查 MCP"，禁止凭记忆作答；
7. Daily Brief / 深度研究等任务按 Model Routing Policy 选择模型（见第 34 章）。

## 5.2 Hermes Cron 的职责边界

Hermes Cron 只负责：

- 获取 daily_context；
- 新闻研究；
- Thesis 分析；
- Daily Brief 生成。

禁止：

- 全量同步；
- 大规模计算。

---

# 6. Investment Backend 的职责（Facts + Calculation + Persistent State）

Investment Backend 是整个系统的：

> **Facts + Calculation + Persistent Business State**

必须承担：

- Instrument Master
- 行情数据获取
- 财务数据获取
- 数据清洗与统一
- Provider 对账
- PIT 数据管理
- 原始数据保存
- Fundamental Calculation
- Valuation Calculation
- ETF Analysis
- Portfolio Accounting
- Risk Calculation
- Research Store
- Thesis Store
- Evidence Store
- Audit Log
- Daily Context Builder
- Backend Scheduler + Job Worker（数据管道自调度）
- MCP Tool API
- Trading Calendar / FX / Corporate Actions

---

# 7. 后端架构模式

冻结采用：

> **模块化单体 Modular Monolith**

第一阶段禁止为了"架构先进"拆成微服务。

推荐技术栈：

```text
Python 3.12
FastAPI
SQLAlchemy
Alembic
Pydantic
APScheduler
PostgreSQL
DuckDB
Parquet
pytest
```

第一阶段明确不引入：

```text
Kafka
Kubernetes
RabbitMQ
ElasticSearch
复杂 Service Mesh
微服务拆分
Celery
Airflow
Kubernetes CronJob
```

Redis 不是 v0.1 必需依赖。

如后续确有大规模异步任务、SSE Replay、多实例状态同步、高频缓存等需求，再通过 ADR 引入。

---

# 8. 数据存储架构

系统采用：

> **PostgreSQL + Parquet + DuckDB + Raw Evidence Store**

## 8.1 PostgreSQL

PostgreSQL 保存业务状态：

- Instruments
- Provider Symbol Mapping
- Portfolio / Account / Transactions
- Trade Proposals
- Thesis / Thesis Assumptions / Thesis Reviews
- Valuation Runs
- Research Notes / Research Claims
- Evidence Metadata
- Daily Brief / Daily Context
- FX Rates
- Audit Log
- Job State

适用于：事务性数据、状态数据、关系型数据、可审计数据、高频率不高但一致性要求高的数据。

## 8.2 Parquet + DuckDB

用于：

- OHLCVA / 历史行情
- 历史估值
- 财务时间序列
- 因子
- 指数成分 / ETF 成分历史
- Screening Dataset
- 全市场横截面分析

原则：

> 不把大规模时间序列全部塞入业务数据库。

**Parquet Schema 版本化（冻结）**：

- Parquet 数据集是分析型资产，Alembic 只管 PostgreSQL，管不了 Parquet；
- 写入时版本化：parquet 目录携带 schema_version（如 `ohlcva/v1/`），列名/类型变更必须新增版本目录并保留旧版本；
- 读取时按版本解析；任何 schema 变更需在 data-contracts 中记录迁移说明；
- v0.1 冻结 OHLCVA 与 financial_facts 的 parquet schema（见第 16、19 章）。

## 8.3 Raw Evidence Store

用于保存：

- 财报 PDF
- 公告
- SEC Filing
- 网页快照
- Provider 原始 JSON
- 原始 CSV
- 数据下载文件
- Research Source Snapshot

推荐目录：

```text
data/
├── raw/
├── parquet/
└── documents/
```

---

# 9. 后端领域模块

冻结后端模块：

```text
backend/app/

├── instruments/
├── market_data/
├── fundamentals/
├── events/
├── valuation/
├── etf/
├── portfolio/
├── risk/
├── research/
├── thesis/
├── briefing/
├── providers/
├── audit/
├── jobs/
├── calendar/            # Trading Calendar（新增）
├── fx/                  # FX Engine（新增）
├── corporate_actions/   # Corporate Actions（新增）
├── api/
└── mcp/
```

每个领域模块必须拥有明确边界。

禁止跨模块直接读取其他模块数据库表形成隐式耦合。

**模块依赖方向（架构测试强制）**：

- 领域模块（instruments/market_data/...）不得互相反向依赖；
- providers 只能被数据层调用，不得被业务引擎反向调用；
- api/ 与 mcp/ 是薄适配层，不得包含业务逻辑；
- 依赖方向违规由架构测试拦截（见第 46 章）。

---

# 10. Instrument Master

Instrument Master 必须作为第一等领域存在。

禁止以 TuShare / Yahoo / AkShare 的 Symbol 作为系统内部 Primary Key。

例如：

```text
贵州茅台

Internal:   CN-SSE-600519
TuShare:    600519.SH
Yahoo:      600519.SS
AkShare:    600519
```

内部统一使用：`instrument_id`

核心表：

```text
instruments

id
symbol
name
market
exchange
asset_type
currency
lot_size
status
isin
created_at
```

Provider 映射表：

```text
provider_symbols

instrument_id
provider
provider_symbol
```

v0.1 资产类型至少支持：

```text
CN_EQUITY
CN_ETF        # 含场内 QDII ETF（跟踪美股指数，如 513500 / 513100）
INDEX
CASH
```

明确：v0.1 不包含 US_ETF（美股市场上市 ETF）。

QDII ETF 的 Instrument 附加字段：

```text
is_qdii
underlying_index_id    # 跟踪的美股指数 instrument_id（如 S&P 500 / Nasdaq 100）
```

架构必须允许未来扩展：

```text
HK_EQUITY
US_EQUITY
US_ETF
BOND
COMMODITY
```

但 v0.1 不要求实现。

---

# 11. Data Provider Layer

统一定义 Provider Interface：

```text
MarketDataProvider
FundamentalProvider
FilingProvider
ETFProvider
MacroProvider
NewsProvider
```

Provider 实现目录：

```text
providers/

├── tushare/
├── akshare/
├── yahoo/
├── sec/
├── cninfo/
└── web/
```

## 11.1 Provider Capability Matrix（冻结）

必须维护 provider-capability.md（data-contracts 下），明确：

- 哪些数据由哪个 Provider 提供；
- 哪些 Provider 是 primary、哪些是 fallback；
- 数据质量等级（quality 分级）；
- 每个 Provider 接口的已知限制（积分要求、频率限制、停更风险）。

Capability Matrix 的初始内容由 M0.5 Data Feasibility Spike 产出（见第 47 章），后续随 Provider 变更通过 ADR 更新。

---

# 12. 数据源策略（v0.1 初始策略）

## 12.1 A 股结构化行情 / 财务数据

优先：

```text
TuShare
   ↓
AkShare
```

注意：TuShare 为积分制，部分关键接口（财务三大报表、复权因子、指数成分/权重）有积分门槛。实际可用接口范围必须在 M0.5 Spike 中实测确认，结果写入 Provider Capability Matrix。

## 12.2 A 股官方披露

优先：

```text
巨潮资讯
交易所
上市公司 IR
```

## 12.3 A 股场内美股指数 QDII ETF（澄清定义）

说明："美股指数 ETF"指在 A 股交易所上市、跟踪美股指数（S&P 500 / Nasdaq 100 等）的 QDII ETF（如 513500 标普500ETF、513100 纳指ETF），不是美股市场直接上市的 ETF。

场内行情（交易价格、量额、溢价率）：

```text
TuShare / AkShare（与 A 股同渠道）
```

标的指数历史点位（S&P 500 / Nasdaq 100）：

```text
Yahoo Finance（^GSPC / ^NDX 等指数代码）
或后续替代 Provider
```

指数估值（Index PE/PB 历史分位）：

```text
FRED / Shiller PE / 自聚合方案（见第 12.5 节与 M0.5 Spike）
```

基金披露数据（持仓穿透、净值、份额、外汇额度）：

```text
基金公司季报 / AkShare 基金接口
```

宏观：

```text
FRED
```

注意：QDII ETF 净值以人民币计价、T+1 披露，底层资产为美元。溢价/折价分析与底层收益拆分依赖汇率（见第 18 章 FX Engine）。

## 12.4 新闻 / 事件

第一阶段：

```text
Hermes Web Research
+
结构化事件记录
```

后续可增加专用 News Provider。若日报 cron 的新闻研究在 3 分钟硬中断内无法完成，将 news collection 下沉为 Backend Job 的一部分，Hermes 只读结构化结果（ADR 记录）。

## 12.5 指数估值数据源（待 Spike 确认）

Index PE/PB 历史分位的数据来源必须在 M0.5 Spike 中验证：

- A 股：中证指数官网 / 沪深交易所 / AkShare 指数估值接口 / 自聚合方案（成分股 + 权重）；
- 美股：FRED / Shiller PE（multpl.com）/ 自聚合方案。

目标：确定 Index PE/PB history source，写入 Provider Capability Matrix。自聚合方案最可控但需要成分股权重数据，成本最高，优先评估现成来源。

---

# 13. 禁止静默 Fallback

任何 Provider Fallback 必须可见、可记录。

例如获取价格不能只返回：

```json
{
  "price": 1413.2
}
```

系统内部至少必须记录：

```text
value
provider
source_timestamp
ingested_at
adjustment
quality
fallback_used
```

如果发生 Provider Fallback：

- 必须写入 Audit；
- 必须保留真实来源；
- 高风险数据可要求多源交叉验证；
- Hermes 应可查询数据 provenance。

---

# 14. 数据生命周期

所有结构化投资数据经过：

```text
RAW
 ↓
NORMALIZED
 ↓
DERIVED
```

## 14.1 RAW

原始 Provider 数据。

原则：

- 尽量原样保存；
- 可重复解析；
- 可追溯。

## 14.2 NORMALIZED

转换为内部统一字段。

处理：

- Symbol
- Currency
- Unit
- Statement Mapping
- Date
- Timezone
- Corporate Action
- Adjustment

**单位归一化（冻结）**：

- 统一规范：base_unit = CNY（金额类）；
- 原始单位（元 / 万元 / 亿元 等）必须保留；
- 每个归一化字段保存四元组：

```text
original_value
original_unit
normalized_value
normalized_unit
```

- 具体映射规则由 Financial Unit Normalization Spike 确定（见第 47.2 节），写入 data-contracts。

**时区语义（冻结）**：

- A 股数据使用 Asia/Shanghai；
- 美股数据使用 America/New_York；
- daily_context 的 market_date 按"用户主时区 + 各市场 session 标注"表达（如 `market_date: 2026-08-21`, `markets: {CN: {date: ..., session: CLOSED}, US: {date: ..., session: ...}}`）；
- 所有时间戳统一存储为 UTC + 时区标注，禁止裸本地时间。
- QDII ETF 数据时序：当日 A 股收盘后，美股指数尚未收盘，QDII 净值/溢价分析基于最新可得数据（前一美股交易日收盘 + 当日盘中预估），必须显式标注数据所对应的美股交易日；

## 14.3 DERIVED

通过确定性代码生成：

- PE / PB / ROE / FCF Yield / CAGR
- Margin / Drawdown / Valuation Percentile
- Portfolio Exposure / ETF Overlap

---

# 15. PIT：Point-in-Time 数据

PIT 必须从 v0.1 开始设计。

必须区分：

```text
period_end
```

与：

```text
published_at
```

例如：

```text
Annual Report

period_end:    2025-12-31
published_at:  2026-03-28
```

任何历史查询应支持：

```text
as_of=<date>
```

原则：

> 在某个历史日期执行分析时，不允许读取当时尚未公开的信息。

目标：

- 防止 look-ahead bias；
- 为后续回测、历史 thesis review、估值复盘提供基础。

**数据缺口语义（冻结）**：

- 在 PIT 语义下，"财报该披露尚未披露"是正常状态，不是错误；
- 停牌、无成交、NaN 是合法状态，必须有显式标记（quality/status 字段），不得以异常中断流水线；
- Anomaly Detection 不得把合法缺口当作数据错误上报。

---

# 16. Fundamental Schema

采用相对通用的 Financial Facts Model。

核心表：

```text
financial_facts

instrument_id

metric_code

period_start
period_end
report_date
published_at

statement_type

value
currency
unit

is_restated

provider
source_document_id
```

基础 Metric Code 至少覆盖：

```text
REVENUE
GROSS_PROFIT
OPERATING_INCOME
NET_INCOME

OPERATING_CASH_FLOW
CAPEX
FREE_CASH_FLOW

TOTAL_ASSETS
TOTAL_LIABILITIES
TOTAL_EQUITY

CASH
DEBT

SHARES_OUTSTANDING
```

目标：

- 兼容中国 GAAP；
- 兼容 US GAAP；
- 尽可能兼容 IFRS；
- 避免把某一 Provider Schema 固化进业务层。

---

# 17. Trading Calendar（新增，冻结）

必须支持：

- A 股交易日；
- 美股交易日；
- 节假日；
- Session（开/闭市状态）。

用于：

- Backend Scheduler 调度；
- 数据同步触发；
- 日报生成时机；
- FX 汇率交易日。

实现要求：

- 交易日历数据可维护（人工校准 + 来源同步）；
- 提供 `is_trading_day(market, date)` 与 `next_trading_day(market, date)` 确定性接口；
- 节假日数据纳入备份范围。

---

# 18. FX Engine（新增，冻结）

由于组合包含：

- RMB（A 股）
- USD ETF（美股）

必须维护：

```text
fx_rates

date
base_currency
quote_currency
rate
provider
```

要求：

- v0.1 至少支持 USD/CNY；
- 组合总资产以人民币计价（A 股 + 场内 ETF），不需要跨币种折算；
- FX 的用途是 QDII ETF 分析：净值/溢价归因、底层美元资产收益的汇率贡献拆分（如标普500 美元涨幅 vs 人民币计价涨幅的差异）；
- 汇率数据源：Yahoo（USDCNY=X）/ FRED（DEXCHUS）等，来源写入 Provider Capability Matrix；
- 汇率缺失时 QDII 相关分析标记 WARNING（见 Freshness Contract）。

---

# 19. OHLCVA Schema

统一字段：

```text
instrument_id
trade_date

open
high
low
close

volume
amount

pre_close
pct_change

turnover_rate

adj_factor
adjusted_close

provider
source_timestamp
ingested_at
```

必须明确：

- raw price（原始价）
- adjusted price（复权价）

禁止混用。两套价格的用途在第 21 章 Performance Calculation Policy 定义。

---

# 20. Corporate Actions（新增，冻结）

必须支持：

- Dividend
- Split
- Bonus Share
- Rights Issue

要求：

- 复权因子来源与更新（除权除息日）在 Provider Capability Matrix 中明确；
- 长期收益计算不得只依赖 adjusted_close（见第 21 章）；
- 每个 Corporate Action 必须可追溯（来源 + 生效日 + 参数）。

---

# 21. Performance Calculation Policy（冻结）

行情分析：

```text
使用 adjusted price
```

组合收益：

```text
Transaction Ledger + Corporate Action + Cash Flow
```

作为唯一计算依据。禁止直接用 adjusted_close 推算组合收益。

禁止：

- 把 raw 与 adjusted 混用在同一计算中；
- 用 LLM 计算收益率 / 回撤 / 仓位。

---

# 22. Valuation Engine

Valuation Engine 必须作为确定性计算引擎存在。

## 22.1 Objective Valuation Layer

自动计算：

```text
PE
PB
EV/EBITDA
FCF Yield
Dividend Yield
Historical Valuation Percentile
```

## 22.2 Intrinsic Value Layer

支持：

```text
DCF
DDM
Owner Earnings
Comparable
Scenario Valuation
```

Hermes 可以提供假设，例如：

```text
Revenue CAGR
Terminal Growth
Discount Rate
Operating Margin
```

Backend 负责实际计算。

输出至少：

```text
Bear
Base
Bull
```

## 22.3 Valuation Run

核心表：

```text
valuation_runs

id
instrument_id

method
as_of_date

bear_value
base_value
bull_value

current_price
margin_of_safety

assumptions_json

engine_version

created_by
created_at
```

任何历史估值都必须可复现（assumptions_json + engine_version + as_of_date 三者齐备）。

---

# 23. ETF Engine

ETF 必须独立于个股估值体系。

禁止直接用公司 DCF 模型研究指数 ETF。

ETF Engine 负责：

```text
valuation
constituents
concentration
sector_exposure
overlap
dividend
tracking
```

QDII ETF（跟踪美股指数）额外分析：

```text
premium_discount        # 溢价率（市价 vs 净值），QDII 特有风险维度
fx_contribution         # 汇率对净值收益的贡献拆分
quota_status            # 外汇额度 / 限购状态（影响溢价持续性）
net_value_t1            # T+1 净值披露时序处理
```

指数 ETF 主要分析：

```text
Index PE
Index PB
ROE
Dividend Yield
Equity Risk Premium
Profit Growth
Historical Percentile
Concentration
```

输出：

```text
VERY_CHEAP
CHEAP
FAIR
EXPENSIVE
VERY_EXPENSIVE
```

## 23.1 ETF 穿透分级（冻结）

ETF 穿透必须标注：

- as_of_date
- source
- confidence

支持三个层级：

```text
Level 0：ETF 本身（净值、价格、规模）
Level 1：最新披露持仓（季报）
Level 2：估算 Exposure（基于 L1 + 指数近似）
```

**禁止假设实时穿透。** ETF 持仓明细只有季报披露，穿透分析是"基于最新披露持仓"的近似，不是实时。所有穿透结果必须携带披露日期与 confidence。

---

# 24. Portfolio Engine

Portfolio Accounting 采用：

> **Transaction Ledger**

作为唯一事实来源。

禁止直接手工修改 Position 作为 canonical state。

交易类型至少支持：

```text
BUY
SELL
DIVIDEND
FEE
CASH_IN
CASH_OUT
```

核心模块：

```text
portfolios
accounts
transactions
position_snapshots
target_allocations
trade_proposals
```

---

# 25. Real Portfolio 与 Paper Portfolio

Portfolio 必须支持：

```text
REAL
PAPER
```

## 25.1 PAPER / Shadow Portfolio

Hermes 可以：

- 自动创建建议；
- 更新 Target Weight；
- 自动模拟交易；
- 记录 AI 决策历史；
- 与真实组合进行对比。

## 25.2 REAL Portfolio

Hermes 只能产生：

```text
trade_proposal
```

状态机：

```text
DRAFT
PROPOSED
APPROVED
REJECTED
EXECUTED
```

真实账户交易：

- 必须由用户确认；
- 第一阶段禁止 Broker API 自动执行。

**ACCOUNT_WRITE 人工入口（冻结）**：真实交易的落账只能通过受保护的人工入口（后端 admin API 仅监听 localhost，由用户在 Dashboard / CLI 手动确认后由后端自身落账）。任何自动化路径（含 Hermes）都无权直接写入真实交易。

---

# 26. Risk Engine

v0.1 不追求复杂机构级风险模型。

第一阶段重点：

```text
Single Position Concentration
Sector Concentration
Asset Class Exposure
ETF Overlap
Portfolio Drawdown
Position Drawdown
Correlation
Cash Ratio
Valuation Concentration
```

特别要求：

> ETF 必须支持穿透分析（按第 23.1 节分级，不假设实时）。

例如：

```text
沪深300 ETF
+
消费 ETF
+
贵州茅台
```

Risk Engine 应计算真实底层茅台 Exposure（基于最新披露持仓 + 披露日期标注），而不是简单把三者视为独立资产。

QDII ETF 参与穿透时同样适用第 23.1 节分级：基于季报披露持仓穿透到底层美股（如苹果、微软），标注披露日期与 confidence，禁止假设实时穿透。

---

# 27. Thesis Database

Investment Thesis 是核心业务资产。

禁止只把 Thesis 保存为一份 Markdown。

Markdown 可作为 Presentation Artifact，但数据库是 Source of Truth。

核心表：

```text
theses

id
instrument_id

status

summary

created_at
last_reviewed_at

conviction

fair_value_low
fair_value_base
fair_value_high
```

## 27.1 Thesis Assumptions

子表：

```text
thesis_assumptions
```

状态：

```text
UNKNOWN
HEALTHY
WARNING
BROKEN
```

示例：

| Assumption | Status |
|---|---|
| 高端需求长期稳定 | HEALTHY |
| 毛利率维持高位 | HEALTHY |
| 管理层资本配置理性 | WARNING |

## 27.2 Thesis 相关对象

还应包括：

```text
thesis_red_flags
thesis_reviews
thesis_events
```

目标：

- 保存初始 thesis；
- 保存关键假设；
- 保存风险；
- 保存卖出条件；
- 保存每次 review；
- 保存 thesis drift；
- 每个 Thesis 有版本，研究历史不可被静默覆盖。

---

# 28. Research Evidence Model

Provenance 必须是一等公民。

核心表：

```text
evidence_items

id
source_type
provider

url
document_id

published_at
retrieved_at

content_hash

title

instrument_id
```

研究结论：

```text
research_claims

claim
claim_type
confidence
```

关联：

```text
claim_evidence
```

目标：

每个重要研究结论均可点击追溯至：

- 财报
- 公告
- SEC Filing
- Provider 数据
- Web Source
- 研究文档

---

# 29. AI Berkshire 的定位

AI Berkshire 不作为核心运行时依赖。

定位：

> **Investment Methodology / Skills Source**

采用：

```text
审核
 ↓
挑选
 ↓
vendor / adapt
```

优先考虑：

```text
investment-checklist
investment-research
financial-data
earnings-review
portfolio-review
thesis-tracker
thesis-drift
news-pulse
```

原则：

- 吸收投资方法论；
- 吸收 Workflow；
- 吸收 Prompt；
- 不强耦合其内部 Tool Runtime；
- 直接复制 SKILL.md 通常无法直接工作，必须审核适配后 vendor 进本项目 skills 目录。

---

# 30. LangAlpha 的定位

LangAlpha 不作为系统核心依赖。

定位：

> **Financial Research Skill / Workflow / Architecture Reference**

重点借鉴：

```text
DCF
Comparable Companies
Three Statement Model
Model Audit

Morning Note
Catalyst Calendar
Sector Overview
Competitive Analysis

Research Workspace
Provenance
Provider Fallback
Persistent State
```

原则：

- 可以借 Skill 思路；
- 可以借 Tool Contract；
- 可以借 Prompt；
- 不能假设复制 SKILL.md 就能直接工作；
- 依赖其 Sandbox / API / Database 的部分必须重新实现为本项目自己的 Tool Contract。

---

# 31. Hermes Investment Profile

创建独立：

```text
investment
```

Profile。

不得与其他开发项目的 Hermes Profile 混用。

Investment Profile 包含：

```text
Hermes Investment Manager

Skills:
- investment-runtime-policy
- investment-policy
- AI Berkshire adapted skills
- LangAlpha-derived skills
- custom research skills

MCP:
- investment-backend

Cron:
- daily-investment-brief
- weekly-portfolio-review
- quarterly-thesis-review
```

## 31.1 Hermes 侧落地细节（冻结）

- MCP Server 配置：HTTP transport，`mcp_servers.investment_backend.url = http://127.0.0.1:8000/mcp`（FastAPI 内嵌 MCP 端点，StreamableHTTP；单机部署无 token，见第 33 章认证边界）；
- 仓库 skills/ 目录通过 symlink 安装到 investment profile 的 skills 目录；
- cron 使用标准 5-field 表达式；非交易日 daily_context 不存在或为 FAILED，Hermes 依据 Freshness Contract 跳过 LLM 阶段——**禁止在 Hermes 侧重复实现交易日历**；
- 日报 cron 的 enabled_toolsets 限制为 web + mcp（不给 terminal/file），作为权限模型的运行时补充。

---

# 32. Hermes MCP Contract

Hermes 只接触：

> 高层、Typed、受控的 MCP Tool。

禁止暴露：

```text
raw_sql()
```

等无约束接口。

## 32.1 Market Tools

```text
resolve_instrument()
get_market_snapshot()
get_price_history()
get_market_metrics()
sync_market_data()
```

## 32.2 Fundamental Tools

```text
get_fundamentals()
get_financial_history()
get_latest_filings()
sync_fundamentals()
```

## 32.3 Valuation Tools

```text
run_valuation()
get_latest_valuation()
get_valuation_history()
```

## 32.4 Portfolio Tools

```text
get_portfolio()
get_positions()
get_portfolio_exposure()
get_portfolio_risk()
create_trade_proposal()
```

## 32.5 Research Tools

```text
get_research_context()
save_research_note()
get_evidence()
search_research()
```

## 32.6 Thesis Tools

```text
get_thesis()
create_thesis()
record_thesis_review()
update_thesis_assumption()
```

## 32.7 Briefing Tools

```text
get_daily_context()
save_daily_brief()
```

## 32.8 Job / Context Tools（新增）

```text
get_job_status()
get_daily_context()   # 返回含 freshness 契约字段（见第 36 章）
```

---

# 33. 权限模型

## 33.1 MCP 权限分级

### READ

Hermes 可自由调用：

```text
market
fundamental
portfolio
research
thesis
valuation
risk
```

### RESEARCH_WRITE

Hermes 可以：

```text
save_research_note
record_thesis_review
update_thesis_assumption
```

### PROPOSAL_WRITE

Hermes 可以：

```text
create_trade_proposal
```

### ACCOUNT_WRITE

Hermes 禁止：

```text
record_real_transaction
modify_real_cash
modify_real_account
```

只能用户或用户授权的人工入口操作（见第 25.2 节）。

### EXECUTION

v0.1 不存在。

禁止 Broker API。

## 33.2 物理执行（与逻辑分级叠加）

- 网络隔离是权限执行的第一层：Hermes 在物理上无法触达数据库（见第 4.4 节）；
- Hermes 侧工具约束是第二层：cron job 的 enabled_toolsets 限制（日报 job 只给 web + mcp）；
- MCP 权限分级是第三层：后端 MCP 层拒绝越权调用；
- 日常会话纪律由 investment-runtime-policy skill 承载。

## 33.3 MCP Authentication Boundary

当前部署（v0.1）：

```text
single machine
localhost
```

允许：无 MCP Token。

未来跨机器部署，必须增加：

```text
API Token
Authentication Layer
Request Audit
```

---

# 34. Model Routing Policy

模型选择属于 Runtime Policy。

禁止将具体模型名称硬编码进业务逻辑。

## 34.1 Routing Contract

```text
task_class
required_capability
model_profile
```

## 34.2 示例

Daily Brief：

```text
task_class:   daily_summary
capability:   fast_reasoning
model_profile: fast
```

Quarterly Thesis Review：

```text
task_class:   thesis_review
capability:   deep_reasoning
model_profile: deep
```

model_profile → 具体模型的映射只存在于 Hermes 配置层（cron job 的 model override / 会话配置），不在业务逻辑中。

---

# 35. Backend Scheduler & Job System

## 35.1 调度责任冻结

Backend Job Pipeline 由 Backend 自主管理。

Hermes Cron 不负责：

- 触发数据同步；
- 等待计算任务；
- 调度确定性 Engine。

## 35.2 调度架构

```text
Trading Calendar
        ↓
Backend Scheduler (APScheduler)
        ↓
Job Worker
        ↓
Data Sync
        ↓
Deterministic Engines
        ↓
Daily Context
        ↓
Hermes Cron (仅 LLM 阶段)
        ↓
Daily Brief
```

## 35.3 Job System

Job 定义：

```text
market_sync_job
fundamental_sync_job
valuation_job
risk_job
brief_generation_job
```

每个 Job 必须记录：

```text
job_id
status
started_at
finished_at
error
input_version
output_version
```

## 35.4 失败处理（冻结）

- Job 失败必须记录 error 且不可静默吞掉；
- 同步失败必须有告警通道（后端日志 + 可选 watchdog）；
- Job 失败不阻塞后续独立 Job；
- 数据同步失败导致 daily_context 无法生成时，按 Freshness Contract 处理（见第 36 章）。

---

# 36. Daily Context Freshness Contract

Hermes 不允许在过期数据上生成投资判断。

## 36.1 Daily Context 必须包含

```text
daily_context_id
generated_at
market_date
markets (CN/US session 标注，见第 14.2 节时区语义)
data_freshness
source_status
attention_items
engine_versions
```

## 36.2 Freshness 状态

允许：

```text
OK
WARNING
STALE
FAILED
```

## 36.3 Hermes 行为规则

如果：

```text
data_freshness != OK
```

则：

禁止：

- 生成 Buy/Hold/Sell 建议；
- 更新 Thesis；
- 创建 Trade Proposal。

允许：

- 输出数据异常报告；
- 请求重新同步（get_job_status / 触发重跑）；
- 标记 Attention Item。

---

# 37. Daily Investment Pipeline（修订版）

每日流水线（由 Backend Scheduler 驱动前半段，Hermes Cron 驱动后半段）：

```text
Trading Calendar
     │
     ▼
Backend Scheduler
     │
     ▼
Backend Sync (Job Worker)
     ├── Prices
     ├── Financial Updates
     ├── Filings
     ├── ETF
     └── Corporate Actions
     │
     ▼
Data Quality Check
     │
     ▼
Deterministic Engines
     ├── Valuation
     ├── Portfolio
     ├── Risk
     └── Anomaly Detection (确定性规则)
     │
     ▼
Daily Context Builder
     │
     ▼
Hermes Cron (仅 LLM 阶段)
     ├── 获取 daily_context（校验 freshness）
     ├── News Research
     ├── Investment Skills
     ├── Thesis Analysis
     └── Event Interpretation
     │
     ▼
Daily Brief (保存至 backend，Dashboard 展示)
```

---

# 38. Daily Brief 原则

禁止每天让 LLM 全量分析所有资产。

先由后端执行：

```text
Anomaly Detection
Attention Filtering
```

例如：

```text
002594  -8.1%
600519  新财报
510300  PE Percentile 进入历史低位
```

最终只把真正需要关注的 Attention Items 发给 Hermes。

目标：

- 降低 Token 消耗；
- 降低 API 成本；
- 降低噪音；
- 保持日报可读；
- 适配每天几十分钟研究时间。

## 38.1 Attention Filtering 规则（冻结）

Attention Detection 必须由 Backend 确定性规则完成。

**禁止 LLM 自主判断阈值。**

规则配置：

```text
attention_rules.yaml
```

示例：

```yaml
price_drop:
  threshold: -8%

valuation:
  pe_percentile:
    below: 20

financial:
  revenue_change:
    below: -20
```

LLM 负责解释，不负责触发。

---

# 39. Daily Brief Delivery Policy

v0.1 默认：

```text
Backend Storage
+
Dashboard Display
```

Daily Brief 保存：

```text
daily_briefs
```

由 Dashboard 查询展示。

v0.1 不默认接入：

```text
Telegram
Email
Push Notification
```

未来作为 Provider 扩展（需 ADR）。

---

# 40. Dashboard

v0.1 推荐：

> **Streamlit**

Dashboard 只读取 Backend API。

不得直接：

- 操作数据库；
- 自己算 Portfolio；
- 自己算估值；
- 绕过 Backend 业务规则。

部署要求：单机部署绑定 127.0.0.1（组合数据隐私）；未来如需远程访问，需认证层（ADR）。

## 40.1 Dashboard 首页

### TODAY

```text
Needs Attention
Daily Brief
```

### PORTFOLIO

```text
Value
Cash
Allocation
Risk
Target Weight
```

### RESEARCH

```text
Watchlist
Latest Research
```

### THESIS

```text
Healthy
Warning
Broken
Due for Review
```

---

# 41. Backup Policy

投资系统的数据具有不可再生性。

重点保护：

```text
Transaction Ledger
Thesis
Research
Evidence
Audit
Raw Provider Data
```

## 41.1 数据库备份

PostgreSQL：

```text
daily pg_dump
```

## 41.2 文件备份

data 目录：

```text
incremental backup
Time Machine / rsync
```

覆盖：

```text
data/
raw/
documents/
parquet/
```

---

# 42. OpenViking 定位

OpenViking 作为：

> Optional Semantic Memory Layer

用途：

- 历史研究语义检索；
- Thesis 回忆；
- Research Claims 的语义索引。

禁止：

- 替代 PostgreSQL 事实存储；
- 作为投资结论的唯一依据。

边界与 4.1 一致：语义索引存摘要，事实唯一来源仍在 Backend。

---

# 43. Repository Structure

冻结仓库结构：

```text
hermes-investment-office/

├── backend/
│   ├── app/
│   │   ├── api/
│   │   ├── mcp/
│   │   ├── instruments/
│   │   ├── market_data/
│   │   ├── fundamentals/
│   │   ├── events/
│   │   ├── valuation/
│   │   ├── etf/
│   │   ├── portfolio/
│   │   ├── risk/
│   │   ├── research/
│   │   ├── thesis/
│   │   ├── briefing/
│   │   ├── providers/
│   │   ├── audit/
│   │   ├── jobs/
│   │   ├── calendar/
│   │   ├── fx/
│   │   ├── corporate_actions/
│   │   └── scheduler/
│   │
│   ├── migrations/
│   └── tests/
│       ├── unit/
│       └── architecture/      # 架构测试（见第 46 章）

├── skills/
│   ├── investment-runtime-policy/
│   ├── investment-policy/
│   ├── daily-brief/
│   ├── stock-research/
│   ├── etf-analysis/
│   ├── valuation-analysis/
│   ├── portfolio-review/
│   ├── thesis-review/
│   └── vendor/
│       ├── ai-berkshire/
│       └── langalpha/

├── dashboard/

├── data/
│   ├── parquet/
│   ├── raw/
│   └── documents/

├── scripts/

├── docs/
│   ├── architecture/
│   │   └── runtime-boundary.md
│   ├── data-contracts/
│   │   ├── provider-capability.md
│   │   ├── performance-policy.md
│   │   ├── corporate-action-policy.md
│   │   └── parquet-schema.md
│   └── ADR/
│       ├── ADR-001-cron-boundary.md
│       └── ADR-002-provider-strategy.md

└── docker-compose.yml
```

---

# 44. v0.1 明确不做（Non-goals）

以下功能属于 Non-goals：

```text
自动实盘交易
Broker API
高频交易
分钟级行情
Tick 数据
复杂技术指标平台
Kronos
强化学习
多 Agent 投票交易
量化择时
完整因子平台
机器学习选股
复杂机构级 VaR
自研大模型
Kubernetes
微服务化
```

除非后续通过新的 ADR 显式修改，否则禁止提前加入。

---

# 45. 投资生命周期闭环（v0.1 必须完成）

```text
               股票 / ETF
                    │
                    ▼
               添加观察池
                    │
                    ▼
             自动获取数据
                    │
                    ▼
              基本面分析
                    │
                    ▼
                 估值
                    │
                    ▼
            Investment Thesis
                    │
                    ▼
                 建仓
                    │
                    ▼
           Portfolio Tracking
                    │
                    ▼
              Daily Monitor
                    │
                    ▼
           财报 / 事件出现
                    │
                    ▼
              Thesis Review
           ┌────────┴─────────┐
           ▼                  ▼
       Thesis OK          Thesis Broken
           │                  │
           ▼                  ▼
         Hold             Sell Proposal
```

只要这一闭环可靠完成，v0.1 即具备真实使用价值。

---

# 46. 架构测试（新增，冻结）

施工纪律是文字约束，必须由机器保障。每个 Milestone 验收必须包含架构测试。

架构测试至少覆盖：

1. **模块依赖方向**：禁止领域模块反向依赖（如 portfolio → providers、valuation → market_data 的内部实现）；使用 import-linter / pytest-arch 或等效机制；
2. **数据库访问边界**：SQLAlchemy 模型注册白名单——所有表只能由所属领域模块定义，禁止跨模块建表/写表；
3. **Dashboard 隔离**：dashboard 依赖清单中禁止出现数据库驱动 / SQLAlchemy 直接依赖；
4. **API 层纯度**：api/ 与 mcp/ 模块不得包含业务计算逻辑（审计性检查）；
5. **MCP 暴露面**：MCP 工具清单必须与冻结契约一致，禁止新增无约束工具（raw_sql 类）；
6. **交易写入**：REAL portfolio 的写入路径只能经过 ACCOUNT_WRITE 人工入口；
7. **确定性计算**：valuation/portfolio/risk 的关键计算函数必须有单元测试 + 黄金值测试（golden test）。

---

# 47. Milestone 冻结

## M0 — Foundation

目标：

- 仓库初始化
- FastAPI
- PostgreSQL（Container 内部网络）
- SQLAlchemy / Alembic
- pytest
- Instrument Master
- 基础日志与配置
- 架构测试框架（第 46 章）

验收：

- 后端可启动；
- Migration 可重复执行；
- Instrument 可创建与查询；
- Provider Symbol Mapping 可工作；
- 架构测试全绿；
- 单元测试全绿。

## M0.5 — Data Feasibility Spike

验证：

- TuShare（积分实测：行情/财务/复权/成分接口实际可用范围）
- AkShare（关键接口稳定性）
- Yahoo（美股指数行情 ^GSPC / ^NDX；验证 QDII ETF 相关指数数据可得性）
- ETF 数据（持仓披露、指数估值）
- 财务数据（单位、重述、报表映射）

新增 Spike 项：

### 47.1 Index Valuation Source Spike

- A 股：中证指数 / 沪深交易所 / AkShare / 自聚合方案；
- 美股：FRED / Shiller PE / 自聚合方案；
- 目标：确定 Index PE/PB history source。

### 47.2 Financial Unit Normalization Spike

- 确定原始单位（元/万元/亿元）到 base_unit=CNY 的映射规则；
- 验证 original_value/original_unit/normalized_value/normalized_unit 四元组模型。

### 47.3 Attention Filtering Config Spike

- 验证 attention_rules.yaml 规则引擎（确定性触发，LLM 只解释不触发）。

输出：`provider-capability-report`。

**Spike 结果回流（冻结）**：spike 报告是 ADR 的输入。若实测结果与冻结策略冲突（如 TuShare 积分不足、某接口不可用），必须通过 ADR 更新第 12 章数据源策略与 provider-capability.md，禁止"报告写一份、实现按另一套"。

## M1 — Data Layer

目标：

- Provider 正式实现
- Market Data Normalization
- Fundamental Normalization
- Provenance
- PIT
- Raw Data Store
- DuckDB / Parquet（含 schema 版本化）
- Trading Calendar
- FX Engine
- Corporate Actions

验收：

- 可同步指定 A 股；
- 可查询 OHLCVA；
- 可查询历史财务数据；
- 可追溯 Provider；
- 支持 `as_of`；
- 无 silent fallback；
- 交易日历 / 汇率 / 复权因子可用。

## M1.5 — Vertical Slice

完成单资产闭环：

```text
Instrument
 ↓
Data
 ↓
Fundamental
 ↓
Valuation
 ↓
Thesis
 ↓
Paper Portfolio
 ↓
Daily Brief
```

验收：一个真实标的走完全流程；MCP 链路（Hermes 查询 → Backend 响应）打通。

## M2 — Portfolio Core

目标：

- Portfolio
- Account
- Transaction Ledger
- Position Calculation
- REAL Portfolio
- PAPER Portfolio
- Trade Proposal

验收：

- 持仓由交易流水可复现；
- REAL / PAPER 完全隔离；
- Hermes 不可直接写真实交易；
- Portfolio State 具有审计链。

## M3 — Investment Engines

目标：

- Valuation Engine
- ETF Engine（含 Level 0/1/2 穿透）
- Risk Engine

验收：

- 所有关键计算由代码完成；
- Valuation Run 可复现；
- ETF 与 Stock 模型分离；
- Risk 可计算 Concentration / Drawdown / Exposure；
- ETF 穿透携带 as_of_date/source/confidence。

## M4 — Research Memory

目标：

- Thesis
- Assumptions
- Red Flags
- Thesis Review
- Research Notes
- Claims
- Evidence
- Audit

验收：

- 每个 Thesis 有版本；
- 每个重要 Claim 可绑定 Evidence；
- Thesis 状态可追踪；
- 研究历史不可被静默覆盖。

## M5 — Hermes Integration

目标：

- Hermes Investment Profile
- Investment Backend MCP（HTTP transport）
- Investment Runtime Policy Skill
- Investment Policy Skill
- AI Berkshire Skills 适配
- LangAlpha Skills 适配

验收：

- Hermes 可通过 MCP 获取投资事实；
- Hermes 不直接访问数据库（架构测试 + 物理隔离验证）；
- Hermes 可创建 Research Note；
- Hermes 可创建 Trade Proposal；
- Hermes 无真实交易执行权限；
- investment-runtime-policy skill 加载生效。

## M6 — Automation

目标：

- Backend Scheduler + Job System
- Daily Investment Brief
- Weekly Portfolio Review
- Quarterly Thesis Review
- Attention Filtering（确定性规则）
- Freshness Contract 生效

验收：

- Backend 自调度可自动执行；
- 日报只分析 Attention Items；
- 重要事件可触发 Thesis Review；
- Brief 保存并可追溯；
- data_freshness != OK 时 Hermes 行为符合第 36.3 节。

## M7 — Dashboard

目标：

- Streamlit Dashboard

至少包含：

- Today
- Portfolio
- Research
- Thesis

验收：

- Dashboard 只调用 Backend API；
- 不重复实现业务逻辑；
- 可查看 Evidence 与 Thesis History；
- 可查看 REAL / PAPER Portfolio；
- 绑定 127.0.0.1。

---

# 48. 施工纪律

后续 Codex / DSH 施工必须遵守：

1. 不得绕过领域模块直接写数据库；
2. 不得把业务逻辑写进 Dashboard；
3. 不得把业务事实写进 Hermes Memory 作为唯一来源；
4. 不得让 LLM 执行关键数学计算；
5. 不得加入未经批准的自动实盘交易；
6. 不得引入无必要微服务；
7. 不得删除 Provenance；
8. 不得省略 PIT；
9. 不得以 Provider Symbol 作为内部主键；
10. 不得静默 Fallback；
11. 不得把 REAL Portfolio 与 PAPER Portfolio 混用；
12. 每个关键 Engine 必须有单元测试；
13. 每个 Milestone 必须通过 fresh test（含架构测试）；
14. Schema 变更必须有 Alembic Migration（PG）+ schema 版本化（Parquet）；
15. Architecture Contract 变更必须新增 ADR；
16. 不得在业务逻辑中硬编码具体模型名；
17. 不得绕过 4.4 物理隔离（禁止把数据库端口暴露给宿主机）；
18. Hermes 侧禁止 terminal 直连数据库（investment-runtime-policy 强制）；
19. Spike 结论必须回流（provider-capability.md / ADR），不得与实现脱节。

---

# 49. 版本与变更管理

## 49.1 版本关系

```text
v1.0 (Consolidated) = v0.1 + R1 (v0.2) + R2 (v0.2.1) + Runtime Audit 工程增量
```

## 49.2 继承规则

v0.1 中未被明确修改的内容在本文件中全部保留（Architecture Contract、MCP Contract、MCP Permission Model、Repository Structure、Non-goals、Milestone、Engineering Discipline）。

如本文件与旧版本存在冲突：

```text
v1.0 (Consolidated) > v0.2.1 > v0.2 > v0.1
```

## 49.3 变更流程

- 冻结项变更必须新增 ADR，记录：修改原因、影响范围、迁移方案；
- ADR 存放于 docs/ADR/；
- 既有 ADR：ADR-001-cron-boundary（Cron 职责边界）、ADR-002-provider-strategy（Provider 策略）。
- ADR-003-qdii-etf-scope（v1.0 澄清修订：美股指数 ETF 定义为 A 股场内 QDII ETF，不包含美股市场 ETF）——修订影响见文档头部版本说明。

## 49.4 进入下一阶段条件

本文件冻结后，进入：

> **Technical Specification / Data Schema v0.1**

下一阶段输出：

1. PostgreSQL 完整 ERD；
2. SQLAlchemy Models；
3. Pydantic Domain Models；
4. Provider Interface 与 Capability Matrix 细化；
5. Provider Fallback Policy；
6. Data Quality Policy；
7. REST API Contract；
8. MCP Tool Schema；
9. Valuation Engine Contract；
10. ETF Engine Contract；
11. Portfolio Calculation Contract；
12. Risk Engine Contract；
13. Thesis State Machine；
14. Daily Context Schema（含 Freshness 字段）；
15. Audit Schema；
16. 架构测试矩阵（第 46 章落地）；
17. Parquet Schema 版本化细则；
18. Milestone 详细验收标准；
19. Codex / DSH 施工 Prompt（引用本文件为唯一架构输入）。

---

# 50. 最终定义

本系统：

> **Hermes 负责思考、编排和解释。**
>
> **Backend 负责事实、状态和计算。**
>
> **Thesis 负责长期投资逻辑。**
>
> **Evidence 负责审计和复盘。**
>
> **Scheduler 负责可靠运行。**
>
> **Data Contract 负责长期稳定。**

**文档状态：FROZEN**

本文件为唯一施工基线。后续任何涉及总体架构边界的修改，必须通过 ADR 明确记录修改原因、影响范围与迁移方案。
