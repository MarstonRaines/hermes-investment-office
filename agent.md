# Hermes Investment Office — Agent 工作指南（agent.md）

> 本文件为进入本仓库工作的 AI Agent（Codex / DSH）提供项目理解、权威文档导航与强制施工纪律。
>
> **权威架构输入**：`docs/Hermes_Investment_Office_后端架构冻结规范_v1.0_Consolidated.md`（状态：**FROZEN**，唯一施工基线）
>
> **架构推导依据**：`docs/Hermes_Investment_Office_Architecture_Benchmark_v1.0_Consolidated.md`（开源对标结论）
>
> 冲突优先级：`v1.0 (Consolidated) > v0.2.1 > v0.2 > v0.1`

---

## 1. 项目定位

**Hermes Investment Office = AI Investment Research Platform + Personal Portfolio Operating System**（个人 AI 投资办公室）。

- **投资范围**：A 股个股、A 股 ETF（宽基/行业）、A 股场内跟踪美股指数（S&P 500 / Nasdaq 100 等）的 QDII ETF（如 513500 标普500ETF、513100 纳指ETF）
- **明确排除**：美股市场直接上市的 ETF（SPY / VOO / QQQ 等）不属于 v0.1 范围
- **投资周期**：3～10 年长期持有
- **核心投资逻辑**：公司质量、安全边际、合理估值、长期持有、持续验证 Investment Thesis
- **卖出逻辑**：Thesis 被证伪、明显高估
- **AI 可做**：搜集信息、研究、给出 Buy / Hold / Reduce 建议、目标仓位、维护 Shadow Portfolio
- **AI 禁止**：真实账户自动下单

系统能力：每日自动收集持仓与观察池信息、更新行情/财务/公告/事件、自动计算估值/组合状态/风险指标、Hermes 结合 Skills 解释重要变化、维护长期 Thesis、生成每日投资日报、维护真实与 Paper / Shadow Portfolio、Web Dashboard、所有重要结论可追溯。

**本项目不是**：AI 自动炒股机器人、短期预测系统、高频交易系统、自动交易 Agent。

---

> **Hermes 载体（ADR-008，2026-08-23 澄清）**：本项目的控制平面角色 "Hermes" 与 Nous Research 开源产品 **Hermes Agent**（MIT，`~/.hermes`，DeepSeek 模型）恰好同名。经确认：**角色载体 = Hermes Agent 产品**。文档中 "Hermes" 默认指角色/载体二者统一的投资经理 Agent；"Hermes Agent" 特指产品。TS-09（Hermes Integration Design）以此为前提。

## 2. 总体架构（Hermes-first）

**Hermes = 控制平面**（思考、编排、解释）；**Investment Backend = 事实与计算平面**（数据、状态、计算、审计）；**Dashboard = 展示层**（Streamlit，不承担核心业务逻辑）；**Scheduler = 可靠运行**；**Data Contract = 长期稳定**。

### 数据访问路径（物理隔离，唯一路径）

```text
Hermes → MCP（仅 Typed Tools）→ Backend → Database
```

- 数据库（PostgreSQL / DuckDB 数据目录）只能被 Backend Container 访问，docker-compose 内部网络，**不得映射端口到宿主机**
- Hermes 禁止直连 PostgreSQL、禁止直接修改 data/ 文件、禁止绕过 MCP 调用内部逻辑
- Backend 的 MCP/API 端口绑定 127.0.0.1（单机部署）

---

## 3. 最高优先级冻结原则（Architecture Contract，修改必须走 ADR）

1. **Hermes 永远不是 Financial Source of Truth**：仓位/持仓成本/价格/PE/PB/估值/目标仓位/风险等事实必须来自 Backend。Hermes 数值类回答默认"现查 MCP"，禁止凭 Memory 或会话记忆作答（Memory 只存长期偏好与自然语言摘要）。
2. **LLM 永远不负责 Decision-sensitive Arithmetic**：PE/PB/EV/EBITDA/ROE/DCF/收益率/回撤/相关性/ETF overlap 等精确计算必须由确定性代码完成；Hermes 只负责提假设、解释结果、比较情景、综合投资政策、生成研究结论，禁止"心算"。
3. **所有重要投资结论必须可审计**：可追溯至原始数据来源、获取时间、数据版本、计算引擎版本、估值假设、Thesis 版本、结论生成时间、对应 Research Evidence。追溯链：`Data Source → Calculation Version → Thesis Revision → Final Conclusion`。禁止产生无法解释来源的 Buy/Hold/Reduce/Target Weight/Fair Value/Thesis Broken。
4. **Runtime Boundary Enforcement**：物理隔离（见第 2 节），逻辑隔离之外必须物理隔离。
5. **Evidence First**：与原则 3 同义的不同表述，用于 skill 与施工文档中的一致性引用。

---

## 4. 技术栈与架构模式

- **架构模式**：模块化单体（Modular Monolith）。第一阶段禁止为了"架构先进"拆微服务。
- **技术栈**：Python 3.12 / FastAPI / SQLAlchemy / Alembic / Pydantic / APScheduler / PostgreSQL / DuckDB / Parquet / pytest
- **明确不引入**：Kafka、Kubernetes、RabbitMQ、ElasticSearch、复杂 Service Mesh、微服务、Celery、Airflow、Kubernetes CronJob；Redis 不是 v0.1 必需依赖。如确需，通过 ADR 引入。

---

## 5. 数据存储架构

**PostgreSQL + Parquet/DuckDB + Raw Evidence Store** 三层：

| 层 | 内容 | 原则 |
|---|---|---|
| PostgreSQL | Instruments、Provider Symbol Mapping、Portfolio/Account/Transactions、Trade Proposals、Thesis 系列、Valuation Runs、Research Notes/Claims、Evidence Metadata、Daily Brief/Context、FX Rates、Audit Log、Job State | 事务性/状态性/关系型/可审计数据 |
| Parquet + DuckDB | OHLCVA、历史估值、财务时间序列、因子、指数/ETF 成分历史、Screening Dataset、横截面分析 | 不把大规模时间序列塞进业务库；**schema 版本化**（目录携带 schema_version，如 `ohlcva/v1/`，变更必须新增版本目录并保留旧版本） |
| Raw Evidence Store | 财报 PDF、公告、SEC Filing、网页快照、Provider 原始 JSON/CSV | `data/raw/`、`data/parquet/`、`data/documents/` |

### 数据生命周期与关键语义

- `RAW → NORMALIZED → DERIVED`：原始数据尽量原样保存可重复解析；归一化处理 Symbol/Currency/Unit/Statement Mapping/Date/Timezone/Corporate Action/Adjustment。
- **单位归一化（冻结）**：base_unit = CNY；原始单位必须保留；每个归一化字段保存四元组 `original_value / original_unit / normalized_value / normalized_unit`。
- **时区语义（冻结）**：所有时间戳统一 UTC + 时区标注，禁止裸本地时间；A 股 Asia/Shanghai，美股 America/New_York；QDII ETF 数据必须显式标注所对应的美股交易日。
- **PIT（Point-in-Time）从 v0.1 开始**：区分 `period_end` 与 `published_at`；历史查询支持 `as_of=<date>`，禁止 look-ahead bias。"财报该披露尚未披露"是正常状态不是错误；停牌/无成交/NaN 是合法状态，必须有显式 quality/status 标记，Anomaly Detection 不得把合法缺口当数据错误。

---

## 6. 领域模块（backend/app/）

```text
instruments / market_data / fundamentals / events / valuation / etf /
portfolio / risk / research / thesis / briefing / providers / audit /
jobs / calendar / fx / corporate_actions / api / mcp
```

**模块依赖方向（架构测试强制）**：

- 领域模块不得互相反向依赖；
- providers 只能被数据层调用，不得被业务引擎反向调用；
- api/ 与 mcp/ 是薄适配层，不得包含业务逻辑；
- 禁止跨模块直接读取其他模块的表形成隐式耦合（SQLAlchemy 模型注册白名单）。

---

## 7. 关键设计与引擎

- **Instrument Master（第一等领域）**：内部主键统一为 `instrument_id`（如 `CN-SSE-600519`）；**禁止以 TuShare / Yahoo / AkShare 的 Symbol 作为内部主键**（600519.SH / 600519.SS / 600519 只是 Provider 映射，见 provider_symbols 表）。v0.1 资产类型：`CN_EQUITY / CN_ETF / INDEX / CASH`；QDII ETF 附加 `is_qdii + underlying_index_id`。架构预留 HK_EQUITY / US_EQUITY / US_ETF / BOND / COMMODITY 扩展，v0.1 不实现。
- **Data Provider Layer**：MarketDataProvider / FundamentalProvider / FilingProvider / ETFProvider / MacroProvider / NewsProvider 接口；实现目录 `providers/{tushare,akshare,yahoo,sec,cninfo,web}`。维护 `provider-capability.md`（primary/fallback/quality/已知限制），由 M0.5 Spike 产出，变更走 ADR。**禁止静默 Fallback**：任何 Fallback 必须写入 Audit、保留真实来源、记录 `value/provider/source_timestamp/ingested_at/adjustment/quality/fallback_used`。
- **数据源策略（v0.1）**：A 股结构化数据 TuShare（优先，积分制需实测）→ AkShare；官方披露走巨潮/交易所/IR；QDII 指数行情 Yahoo（^GSPC/^NDX）、指数估值 FRED/Shiller PE/自聚合（待 Spike）；新闻第一期为 Hermes Web Research + 结构化事件记录。
- **Valuation Engine（确定性计算引擎）**：客观估值层（PE/PB/EV/EBITDA/FCF Yield/Dividend Yield/历史分位）+ 内在价值层（DCF/DDM/Owner Earnings/Comparable/Scenario，输出 Bear/Base/Bull）。`valuation_runs` 表要求 `assumptions_json + engine_version + as_of_date` 三者齐备保证历史可复现。工程范式参考 Vibe-Trading quantlib：**无默认参数**（缺失抛错而非猜测）、Assumption 携带 basis、终值双方法交叉验证、contracts 层（MissingInputError / require_inputs / require_positive）。
- **ETF Engine（独立于个股估值）**：禁止用公司 DCF 研究指数 ETF。负责 valuation / constituents / concentration / sector_exposure / overlap / dividend / tracking，输出 `VERY_CHEAP ~ VERY_EXPENSIVE`。QDII 额外分析：`premium_discount`（溢价率）、`fx_contribution`（汇率贡献拆分）、`quota_status`（额度/限购）、`net_value_t1`（T+1 净值时序）。**穿透分级 Level 0/1/2（冻结）**：禁止假设实时穿透，所有穿透结果必须携带 `as_of_date / source / confidence`。
- **Portfolio Engine**：**Transaction Ledger 为唯一事实来源**，禁止直接手工修改 Position 作为 canonical state。交易类型 BUY/SELL/DIVIDEND/FEE/CASH_IN/CASH_OUT。REAL 与 PAPER 完全隔离；Hermes 对 REAL 只能产生 `trade_proposal`（DRAFT → PROPOSED → APPROVED → REJECTED → EXECUTED）；**ACCOUNT_WRITE 人工入口（冻结）**：真实交易落账只能通过受保护的 localhost admin 入口，任何自动化路径（含 Hermes）无权直接写入真实交易；第一阶段禁止 Broker API。
- **Risk Engine（v0.1 务实范围）**：Single Position Concentration / Sector Concentration / Asset Class Exposure / ETF Overlap / Portfolio & Position Drawdown / Correlation / Cash Ratio / Valuation Concentration。ETF 必须按穿透分级参与（如"沪深300 ETF + 消费 ETF + 茅台"要算真实底层茅台暴露）。
- **Thesis Database（核心业务资产）**：`theses` + `thesis_assumptions`（UNKNOWN/HEALTHY/WARNING/BROKEN）+ `thesis_red_flags` + `thesis_reviews` + `thesis_events`。**数据库是 Source of Truth，Markdown 只是 Presentation Artifact**；每个 Thesis 有版本，研究历史不可被静默覆盖；方法论参考 AI Berkshire（5 句话论文模板、3-7 个可验证假设、风险红线、季度 Review、Thesis Drift），但 Schema 完全自研。
- **Research Evidence Model（Provenance 一等公民）**：`evidence_items`（source_type/provider/url/document_id/published_at/retrieved_at/content_hash）+ `research_claims`（claim/claim_type/confidence）+ `claim_evidence` 关联。每个重要研究结论可点击追溯至财报/公告/Filing/Provider 数据/Web 来源。
- **FX Engine（新增，冻结）**：`fx_rates(date, base, quote, rate, provider)`，v0.1 至少 USD/CNY；用途是 QDII ETF 分析（净值/溢价归因、汇率贡献拆分），组合本身以人民币计价；汇率缺失时 QDII 相关分析标记 WARNING。
- **Trading Calendar（新增，冻结）**：A 股/美股交易日、节假日、Session；提供 `is_trading_day(market, date)` / `next_trading_day(market, date)` 确定性接口；数据可维护（人工校准 + 来源同步）；用于 Scheduler 调度、同步触发、日报时机、FX 交易日。**禁止在 Hermes 侧重复实现交易日历**。
- **Corporate Actions（新增，冻结）**：Dividend / Split / Bonus Share / Rights Issue；每个行为可追溯（来源 + 生效日 + 参数）；长期收益计算不得只依赖 adjusted_close。
- **Performance Calculation Policy（冻结）**：行情分析用 adjusted price；组合收益以 Transaction Ledger + Corporate Action + Cash Flow 为唯一计算依据；禁止 raw 与 adjusted 混用；禁止 LLM 计算收益率/回撤/仓位。

---

## 8. MCP 契约与权限模型

Hermes 只接触**高层、Typed、受控的 MCP Tool**（Market / Fundamental / Valuation / Portfolio / Research / Thesis / Briefing / Job 工具组），**禁止暴露 `raw_sql()` 等无约束接口**。工具清单必须与冻结契约一致（架构测试检查）。

### 权限分级（Hermes 视角）

```text
READ（自由）→ RESEARCH_WRITE（save_research_note / record_thesis_review / update_thesis_assumption）
→ PROPOSAL_WRITE（create_trade_proposal）
→ ACCOUNT_WRITE（record_real_transaction 等，Hermes 禁止，仅用户/人工入口）
→ EXECUTION（v0.1 不存在，禁止 Broker API）
```

### 三层物理/逻辑执行保障

1. 网络隔离（Hermes 物理上无法触达数据库）；
2. Hermes 侧工具约束（cron 日报 job 的 enabled_toolsets 只给 web + mcp，不给 terminal/file）；
3. MCP 权限分级（后端 MCP 层拒绝越权调用）。

### 认证边界

v0.1 单机 localhost 部署允许无 MCP Token；未来跨机器部署必须增加 API Token + 认证层 + Request Audit（ADR）。

---

## 9. 调度与每日流水线

**调度责任冻结**：Backend Job Pipeline 由 Backend 自主管理（APScheduler + Job Worker）。Hermes Cron 不负责触发数据同步、等待计算任务、调度确定性 Engine——只负责：获取 daily_context、新闻研究、Thesis 分析、Daily Brief 生成。

每日流水线（前半段 Backend 驱动，后半段 Hermes Cron 驱动）：

```text
Trading Calendar → Backend Scheduler → Sync（价格/财务/公告/ETF/公司行为）
→ 数据质量检查 → 确定性引擎（Valuation / Portfolio / Risk / Anomaly）
→ Daily Context Builder → Hermes Cron（仅 LLM 阶段）→ Daily Brief（存 Backend，Dashboard 展示）
```

- **Job 记录**：job_id / status / started_at / finished_at / error / input_version / output_version；失败必须记录且不可静默吞掉；Job 失败不阻塞后续独立 Job。
- **Freshness Contract（冻结）**：daily_context 必须包含 `data_freshness`（OK / WARNING / STALE / FAILED）等字段。`data_freshness != OK` 时 Hermes **禁止**生成 Buy/Hold/Sell 建议、更新 Thesis、创建 Trade Proposal；只允许输出数据异常报告、请求重新同步、标记 Attention Item。
- **Attention Filtering（冻结）**：由 Backend 确定性规则（`attention_rules.yaml`，如 price_drop -8%、PE percentile < 20）触发；**禁止 LLM 自主判断阈值**；LLM 只负责解释。日报只分析 Attention Items，禁止每天全量分析所有资产。
- **Model Routing Policy**：按 `task_class → required_capability → model_profile` 路由；**禁止在业务逻辑中硬编码具体模型名**，映射只存在于 Hermes 配置层。
- **Daily Brief Delivery**：v0.1 默认 Backend Storage + Dashboard Display，不接入 Telegram/Email/Push（未来作为 Provider 扩展需 ADR）。

---

## 10. 架构对标结论（Benchmark 摘要）

开源对标（v1.0 代码级验证）与迁移矩阵：

| 模块 | 参考项目 | 策略 |
|---|---|---|
| Workspace / Research Timeline | LangAlpha（ginlix-ai/LangAlpha） | Adapt（概念与表设计思路，chat-centric → thesis-centric 适配） |
| Skill Contract | LangAlpha + Anthropic Financial Skills | Adopt |
| Thesis Methodology | AI Berkshire（xbtlin/ai-berkshire） | Adopt 思想（无 Schema 可迁移，纯 SKILL.md 工作流） |
| Thesis Database / ETF Engine / Risk / Portfolio Ledger | Hermes 自研 | Build |
| Provider Layer / MCP Contract | LangAlpha / Vibe-Trading | Adapt |
| Valuation Contract | Vibe-Trading quantlib | Adopt 思想（无默认参数、Assumption 带 basis、交叉验证） |
| Valuation Methods | FinRobot | Reference（只借方法清单与结果结构，代码教学级不迁移） |
| Execution Engine / Trading Debate | Vibe-Trading / TradingAgents | Reject |

原则：**概念可迁移、代码不迁移；不 Fork**（Architecture Borrowing + Skill Borrowing + Schema Adaptation）。任何代码级参考前必须核对 License 与商标条款（FinRobot 含 NOTICE + TRADEMARK_POLICY），结论记录于 ADR。

外部验证：FinRobot"确定性计算与 LLM 叙述严格分离 + full provenance"、Vibe-Trading quantlib"无默认参数 + 可复现"与冻结规范 4.2/4.3 双向印证。

---

## 11. 施工纪律（19 条，必须遵守）

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
12. 每个关键 Engine 必须有单元测试（含黄金值测试）；
13. 每个 Milestone 必须通过 fresh test（含架构测试）；
14. Schema 变更必须有 Alembic Migration（PG）+ schema 版本化（Parquet）；
15. Architecture Contract 变更必须新增 ADR；
16. 不得在业务逻辑中硬编码具体模型名；
17. 不得绕过物理隔离（禁止把数据库端口暴露给宿主机）；
18. Hermes 侧禁止 terminal 直连数据库（investment-runtime-policy 强制）；
19. Spike 结论必须回流（provider-capability.md / ADR），不得与实现脱节。

**架构测试（每个 Milestone 验收必须包含）**：模块依赖方向（import-linter / pytest-arch）、数据库访问边界（SQLAlchemy 模型注册白名单）、Dashboard 隔离（依赖清单无数据库驱动）、API 层纯度、MCP 暴露面与冻结契约一致、REAL 交易写入只经 ACCOUNT_WRITE 人工入口、确定性计算黄金值测试。

---

## 12. 文档导航（权威输入 + Technical Specification 系列）

**权威输入（FROZEN）**：

- `docs/Hermes_Investment_Office_后端架构冻结规范_v1.0_Consolidated.md` —— 唯一施工基线（架构、数据、MCP、权限、里程碑、纪律）
- `docs/Hermes_Investment_Office_Architecture_Benchmark_v1.0_Consolidated.md` —— 开源对标结论与迁移矩阵

**Technical Specification 系列（docs/ts01.md ~ ts08.md，依赖链自上而下）**：

```text
TS-01 Domain Model Specification（ts01.md）—— 领域冻结：thesis-centric/ledger-centric/provenance-first
  ↓
TS-02 PostgreSQL ERD（ts02.md）—— 40 张表 / 9 个表族物理设计（迁移注意：job_runs 先于 provenance_records）
  ↓
TS-03 SQLAlchemy + Pydantic Model（ts03.md）—— ORM 映射 + Pydantic v2 域模型 + 模块白名单 TABLE_OWNER
TS-04 Data Contract（ts04.md）—— Parquet schema 版本化、单位四元组、PIT、Freshness、quality、attention 规则
  ↓
TS-05 Provider Architecture（ts05.md）—— 六类 Provider 接口、Capability Matrix、fallback、Spike 回流
TS-06 Engine Contract（ts06.md）—— Valuation / ETF / Portfolio / Risk / FX / Anomaly 六大确定性引擎契约
  ↓
TS-07 MCP Contract（ts07.md）—— 八组 28 个唯一工具、五级权限、18 错误码、Freshness 门禁
  ↓
TS-08 Test Matrix（ts08.md）—— 六层测试矩阵（ARCH/GOLD/CTR/STM/DQ/ACC）+ Milestone 验收映射
  ↓
TS-09 Hermes Integration Design（ts09.md）—— 控制平面载体（Nous Hermes Agent，ADR-008）+ Investment
  Profile + 8 业务 skills + cron 三任务 + 模型路由 + 工作流编排（M5 输入）
```

引用规则：

- 施工以 ts01-ts08 为唯一技术输入；TS-07 裁决 `create_thesis_revision` 为准（`create_thesis` 为兼容别名，待 ADR 回写冻结规范 §32.6）；
- 工具白名单按 **28 个唯一工具**逐名断言（get_daily_context 单一工具定义）；
- 引擎输出必须携带 provenance（DERIVED_ENGINE）+ engine_version + input_hash；黄金值输入只能由 ts06 契约构造（防自证循环）。

---

## 13. 里程碑（当前阶段）

```text
M0 Foundation → M0.5 Data Feasibility Spike → M1 Data Layer → M1.5 Vertical Slice
→ M2 Portfolio Core → M3 Investment Engines → M4 Research Memory
→ M5 Hermes Integration → M6 Automation → M7 Dashboard
```

- **M0.5 Spike 重点**：TuShare 积分实测、AkShare 稳定性、Yahoo 指数行情、ETF 持仓披露、财务单位归一化、Index Valuation Source、Attention Filtering 规则引擎。输出 `provider-capability-report`，结果回流 ADR。
- **当前文档阶段**：冻结规范 + Technical Specification v0.1 系列（TS-01 ~ TS-08）**已完成**；下一阶段为 **M0 施工**（仓库初始化、FastAPI、PostgreSQL、SQLAlchemy/Alembic、pytest、Instrument Master、架构测试框架），M0 验收后进入 M0.5 Data Feasibility Spike（TuShare 积分实测、Index Valuation Source、FX Provider 等未冻结项回流 ADR）。

---

## 14. 变更管理

- 冲突优先级：`v1.0 (Consolidated) > v0.2.1 > v0.2 > v0.1`，本仓库以两份 Consolidated 文档为准。
- 冻结项变更必须新增 ADR，记录：修改原因、影响范围、迁移方案；ADR 存放于 `docs/ADR/`。
- 既有 ADR：ADR-001-cron-boundary（Cron 职责边界）、ADR-002-provider-strategy（Provider 策略）、ADR-003-qdii-etf-scope（QDII ETF 范围澄清）、**ADR-004-remote-access-roadmap**（远程访问与原生客户端演进路线：v0.1 本地单机不变；Tailscale 私有网络通道；激活 §33.3 认证预留；macOS .app 展示层替换，消费 TS-07 REST Contract；配置化 bind_host/base_url）、**ADR-005-provider-network-routing**（per-provider 代理三态）、**ADR-006-watchlist-domain**（观察池领域对象 + 初始池 510300/513650/512890）、**ADR-007-index-bar-index**（index_bar_index 表）、**ADR-008-hermes-identity**（Hermes 角色载体 = Nous Hermes Agent）。
- **演进预留（ADR-004，v0.1 施工时落实）**：api/ 层按"未来被原生客户端消费"标准实现完整 JSON REST 契约（无 Streamlit 会话依赖）；`bind_host / base_url / auth.enabled` 进配置层（默认 127.0.0.1、auth 关闭）；禁止把 127.0.0.1 写死进业务逻辑。
- **v0.1 Non-goals（禁止提前加入，除非新 ADR 显式修改）**：自动实盘交易、Broker API、高频交易、分钟级行情、Tick 数据、复杂技术指标平台、强化学习、多 Agent 投票交易、量化择时、完整因子平台、机器学习选股、复杂机构级 VaR、自研大模型、Kubernetes、微服务化。

---

## 15. 一句话定义

> **Hermes 负责思考、编排和解释。Backend 负责事实、状态和计算。Thesis 负责长期投资逻辑。Evidence 负责审计和复盘。Scheduler 负责可靠运行。Data Contract 负责长期稳定。**
