# Hermes Investment Office Architecture Benchmark v1.0（Consolidated）

## 开源项目架构对标与迁移规范（合并基线）

> 状态：Architecture Research / 合并基线（结论 + 代码级验证证据自洽）
>
> 版本：v1.0 Consolidated
>
> 来源：v0.1 原始对标 + Revision 1（三处核心修正）+ 代码级验证证据链
> （2026-08-23 对 5 个仓库的树/关键文件逐行检查）
>
> 本文件为 Architecture Benchmark 的唯一权威版本。原 v0.1 与
> Revision_1 保留为历史存档，后续引用一律指向本文件。
>
> 定位：本文件是 Technical Specification v0.1 的输入，不是冻结契约。
> 冻结契约以《后端架构冻结规范 v1.0（Consolidated）》为准。

------------------------------------------------------------------------

# 1. 文档目的

本 Benchmark 不是 Technical Specification，也不是施工文档。

它的作用是：

> 在进入 Technical Specification / Data Schema v0.1 前，对成熟开源
> 项目进行架构审计，确定 Hermes Investment Office 应该复用哪些思想、
> 适配哪些组件、拒绝哪些范式。

它回答：

- 哪些项目解决了类似问题；
- 哪些架构思想值得迁移；
- 哪些设计需要 Hermes 自研；
- 哪些组件不能直接复制。

------------------------------------------------------------------------

# 2. Hermes Investment Office 定位

Hermes Investment Office 不是：

- Trading Bot；
- 高频交易系统；
- 股票预测系统；
- 自动交易机器人。

定位：

> AI Investment Research Platform + Personal Portfolio Operating System

核心：

``` text
Investment Thesis
        |
Research
        |
Evidence
        |
Valuation
        |
Portfolio Decision
        |
Review Lifecycle
```

架构原则（与冻结规范 v1.0 一致）：

``` text
Hermes Control Plane

负责：
- Reasoning
- Workflow
- Skill Orchestration
- Research Interaction

Investment Backend

负责：
- Facts
- Calculation
- Persistent State
- Audit
```

------------------------------------------------------------------------

# 3. Benchmark 项目列表与验证状态

  项目                         定位                              研究价值
  ---------------------------- --------------------------------- ----------
  LangAlpha                    AI Investment Research Platform   最高
  AI Berkshire                 Value Investing Skill Framework   最高
  Vibe-Trading                 Agent/MCP 工程框架                高
  FinRobot                     Financial Analysis Engine         中
  TradingAgents                Multi-agent Debate                低
  Anthropic Financial Skills   Skill 标准                        中

代码级验证状态（v1.0 新增——每个结论均有仓库事实背书）：

  项目                         仓库（验证对象）                   验证深度
  ---------------------------- ---------------------------------- --------
  LangAlpha                    ginlix-ai/LangAlpha               树+迁移+API
  AI Berkshire                 xbtlin/ai-berkshire               树+SKILL.md
  Vibe-Trading                 HKUDS/Vibe-Trading                树+引擎源码
  FinRobot                     AI4Finance-Foundation/FinRobot    树+引擎源码
  TradingAgents                TauricResearch/TradingAgents      未深挖
  Anthropic Financial Skills   anthropics/financial-services     树+SKILL.md 清单

**LangAlpha 同名仓库歧义（必须写明仓库地址）**：本 Benchmark 参考的是
ginlix-ai/LangAlpha（"Claude Code for Finance"，3414 文件、29 个 Alembic
migration）。另有 Chen-zexi/LangAlpha（multi-agent workflow）与商业产品
langalpha.ai，均非本 Benchmark 所指。引用时一律写仓库地址，避免歧义。

------------------------------------------------------------------------

# 4. 修订记录（v0.1 → v1.0）

Revision 1 与代码级验证共同修正原 Benchmark 的三个主要问题：

## 4.1 AI Berkshire 定位修正

原判断：AI Berkshire 提供 Thesis Database。

修正：

AI Berkshire 提供：

- 投资方法论；
- Thesis Tracking Workflow；
- 假设管理思想；
- 红线管理思想；
- 周期性 Review 方法。

**代码级验证：不存在直接可迁移数据库 Schema。** xbtlin/ai-berkshire
（2866 文件）为纯 SKILL.md + prompt + 文件工作流（论文存为
reports/{公司名}-thesis.md，假设状态用 emoji 标记），无任何
schema / models / migrations。

因此：

``` text
AI Berkshire

提供：
Investment Methodology

不提供：
Hermes Thesis Database Implementation

Hermes Thesis Domain 必须自研。
```

## 4.2 LangAlpha 定位修正

原判断：LangAlpha 最接近完整 Hermes。

修正：

LangAlpha 更准确定位：

> Research Workflow Platform

可迁移：Workspace 思想、Thread/Event 模型、Skill Contract、
Provider Layer、Research Provenance。

不可直接迁移：Portfolio / Thesis / Valuation / Risk Domain。

原因：LangAlpha 是 chat/research-centric（核心状态为
conversation_threads + workspaces），Hermes 是 thesis-centric
（核心状态为 Thesis + Transaction Ledger）。**范式不同。**

## 4.3 Valuation Engine 参考修正

原判断：FinRobot 作为 Valuation Engine 主要参考。

修正：

``` text
Vibe-Trading quantlib（工程范式）
        +
FinRobot methodology（方法清单）
```

分工：

- Vibe-Trading：Contract Design、Assumption Handling、Deterministic
  Calculation、Reproducibility、Validation；
- FinRobot：DCF / DDM / LBO / WACC / Comparable / Scenario Analysis。

最终实现：Hermes 自研。

**代码级依据**：Vibe-Trading agent/src/quantlib/valuation/ 为机构级
确定性估值工程（详见第 7.3 节）；FinRobot valuation_engine.py 为
教学/演示级实现（except: pass 吞异常、默认倍数、字符串解析指标），
只借方法清单与结果结构，不迁移代码。

------------------------------------------------------------------------

# 5. LangAlpha Architecture Review

## 5.1 代码级验证事实

ginlix-ai/LangAlpha（3414 文件、29 个 Alembic migration）：

- 17 张应用表：users, workspaces, workspace_files, watchlists,
  conversation_threads, conversation_queries, conversation_responses,
  conversation_usages, automations, market_insights 等；
- provenance 真实存在：migrations 013/015 创建 provenance_records 与
  provenance_result_bodies；
- migration 覆盖 thread/turn 生命周期、subagent ledger、user_skills、
  mcp_connectors、agent_plugins；
- checkpoint 基建（LangGraph PostgresSaver）服务于多轮 agent 状态恢复，
  **不迁移**——我们的状态是 Thesis + Transaction Ledger，持久化模型
  完全不同。

## 5.2 可迁移

### Workspace / Thread / State（Adapt）

用于：

``` text
Investment Workspace
        |
Research Thread
        |
Research Event
        |
Thesis Revision
```

需要适配：LangAlpha 是 Chat-centric，Hermes 是 Thesis-centric。
迁移的是概念与表设计思路（research_workspace / research_thread /
research_event / state_snapshot），不是代码——LangAlpha 技术栈
（LangGraph 编排）与冻结契约（FastAPI + SQLAlchemy + APScheduler）不同，
任何"复用模块"的想法不成立。

### Skill Contract（Adapt）

``` text
Skill
 ↓
Tool Requirement
 ↓
Workflow
 ↓
Artifact
 ↓
Audit
```

不直接复制工具依赖。LangAlpha 有 user_skills / agent_plugins 表，
skill 注册是平台级能力，可参考其注册模型（表设计思路）。

### Provider Layer（Adapt）

``` text
MarketDataProvider

get_price()
get_financial_data()
health_check()
quality_score()
```

Hermes 增强（冻结规范 v1.0 要求）：

- source_timestamp；
- provider；
- quality；
- fallback_used。

### Evidence Chain（Adapt）

LangAlpha 的 provenance_records 证实"证据链"概念在该生态成立。
迁移概念：evidence_items / research_claims / claim_evidence
（冻结规范第 28 章）。粒度差异：LangAlpha 是 result-body 级别的调用
记录，我们是结论级别的 claim-evidence 绑定，需自研。

------------------------------------------------------------------------

# 6. AI Berkshire Architecture Review

## 6.1 定位

AI Berkshire 是：

> Investment Intelligence Layer（方法论层）

不是 Backend。

仓库真实结构：codex-skills/（investment-checklist、investment-research、
financial-data、earnings-review、portfolio-review、thesis-tracker、
thesis-drift、news-pulse、industry-research、investment-memo-craft 等）
+ tools/financial_rigor.py 确定性校验工具。

## 6.2 吸收内容（Adopt 思想）

### Thesis Methodology

包括：

- 投资逻辑（买入前写卖出条件）；
- 核心假设（5 句话论文模板 + 3-7 个可验证假设清单 + 验证频率）；
- 风险红线（触发 = 必须重新评估，致命红线 = 立即清仓）；
- Review 周期（季度检查）；
- Thesis Drift 判断。

### Financial Rigor

> LLM 不负责精确金融计算。

tools/financial_rigor.py（估值校验工具）是该方法论的实现示例，
可借鉴其校验项清单（verify-valuation 的检查点）。

## 6.3 Hermes 自研（Build）

数据库：

``` text
theses
thesis_assumptions
thesis_reviews
thesis_events
thesis_red_flags
```

**这些不是 AI Berkshire 已存在 Schema，是 Hermes 自己的设计**
（冻结规范第 27 章）。注意范式差异：AI Berkshire 以 markdown 即存储，
冻结规范明确"Markdown 只是 Presentation Artifact，数据库是 Source of
Truth"——吸收方法论，不吸收存储形态。

------------------------------------------------------------------------

# 7. Vibe-Trading Architecture Review

## 7.1 定位

Vibe-Trading 偏 Quant / Trading / Factor / Backtest / Execution。

不作为主架构（交易/执行范式与长期投资研究不符，且冻结规范
No Broker Execution）。

## 7.2 可迁移

### MCP / Tool Registry（Adapt）

``` text
Hermes
 ↓
MCP
 ↓
Typed Tools
 ↓
Backend
```

代码级验证：agent/mcp_server.py 存在（README 称 17 个 MCP 工具，
stdio 子进程形态），封装形态与冻结规范第 32 章 Typed MCP Contract 同构。

### Engineering Contract（Adopt 思想）

吸收：输入输出明确、类型约束、生命周期管理、可测试性。

### Hypothesis Registry（Adopt 思想）

``` text
Hypothesis / Data Source / Skill / Validation / Invalidation
对应：
Thesis / Assumption / Evidence / Review
```

## 7.3 Valuation Engineering（最高迁移价值——Adopt 思想）

Vibe-Trading 的 agent/src/quantlib/valuation/（dcf.py / comps.py /
threestatement.py / contracts.py）是本 Benchmark 所有项目中与冻结
规范（4.2 LLM 不负责计算、22 章 Valuation Run 可复现）最契合的
参考实现。工程特征（技术规范阶段的直接模板）：

- **无任何默认参数**：无风险利率、beta、ERP、税率、终值增长率全部
  required，缺失抛 MissingInputError——调用方精确知道缺什么，而不是
  得到一个建立在猜测上的数字；
- **终值双方法交叉验证**：永续增长法 vs 退出倍数法互相推导对方的
  隐含值（"growth 隐含 35x EBITDA 即使 Gordon 公式算对了也是错的"）；
- **growth >= WACC 直接拒绝**（终值未定义，不是"很大"）；
- **Assumption 携带 basis**：终值增长率与退出倍数必须是 Assumption
  对象，拒绝裸 float——"估值观点穿着常量外衣"反模式被显式禁止
  （对应 valuation_runs.assumptions_json）；
- **equity bridge 符号约定强制**（非负 magnitude + 公式负责符号）；
- **WACC 权重 basis 显式参数**（current vs target market values）；
- **contracts.py 契约层**：Assumption / MissingInputError /
  ValuationError / require_inputs / require_positive。

## 7.4 不采用（Reject）

- 自动交易；
- 高频策略；
- 因子交易体系。

------------------------------------------------------------------------

# 8. FinRobot Architecture Review

## 8.1 定位与验证

FinRobot 是 multi-agent 权益研究平台。核心原则（README 原文）：

> strict separation between deterministic financial computation and
> LLM-based narration... DCF, DDM, LBO, WACC, comparable-company
> analysis, Monte Carlo simulations are calculated through deterministic
> code paths with full provenance.

**该原则与冻结规范 4.2/4.3 完全一致——构成对我们架构方向的外部验证。**

## 8.2 吸收（Reference 方法清单）

估值方法：DCF / DDM / LBO / WACC / Comparable / Monte Carlo。
结果结构：method / target_price / low / high / assumptions /
confidence——与 valuation_runs（bear/base/bull + assumptions_json）
同构，可借鉴方法清单与结果结构。

**实现质量是教学/演示级（except: pass、默认倍数、字符串解析指标、
依赖 DataFrame 列名约定）——代码不迁移。**

## 8.3 不吸收（Reject）

- Backend Architecture；
- Agent Architecture（Bull/Bear/Judge 多 agent 编排，与单一 Hermes
  控制平面冲突）；
- Portfolio Decision Framework。

## 8.4 许可证注意

FinRobot 含 NOTICE + TRADEMARK_POLICY（Apache 生态）。借鉴概念无风险，
任何代码复制前必须核对许可证与商标条款。

------------------------------------------------------------------------

# 9. Anthropic Financial Skills Review

提升优先级（v1.0 上调为 Skill 标准参照）。

仓库：anthropics/financial-services（634 文件），结构为
plugins/agent-plugins/{plugin}/skills/{skill}/SKILL.md。

价值：提供

``` text
SKILL.md
 ↓
Workflow
 ↓
Artifact
 ↓
Human Review
```

规范，适合作为 Hermes Skills Directory 设计参考。

实际 skill 清单：

- earnings-reviewer：earnings-analysis / earnings-preview /
  morning-note / model-update / audit-xls
- market-researcher：sector-overview / comps-analysis /
  competitive-analysis / idea-generation
- model-builder：3-statement-model
- 另有 gl-reconciler / kyc-screener / meeting-prep-agent

注意：target 是通用金融（投行/PE/财富管理），需按 A 股长期投资定位
裁剪。morning-note / earnings-analysis / sector-overview 与
daily-brief / stock-research / thesis-review skill 直接同构，
是 vendor/adapt 的优先候选。

------------------------------------------------------------------------

# 10. TradingAgents Review

不采用。

原因：

TradingAgents:

``` text
Prediction
 ↓
Debate
 ↓
BUY/SELL
```

Hermes:

``` text
Business Understanding
 ↓
Thesis Validation
 ↓
Long-term Decision
```

投资范式不同。README 与论文（arXiv 2412.20138）确认其范式为交易
执行（fundamental/sentiment/technical analyst + trader + risk
management），与冻结规范 Non-goals（自动实盘交易）冲突。

------------------------------------------------------------------------

# 11. 修订后迁移矩阵

策略档位：Adopt（直接采纳）/ Adapt（采纳并适配）/ Reference（参考
不迁移）/ Build（自研）/ Reject（明确拒绝）

  模块                   参考项目                       策略
  -------------------- ------------------------------ -----------
  Workspace            LangAlpha                      Adapt
  Research Timeline    LangAlpha                      Adapt
  Skill Contract       LangAlpha + Anthropic Skills   Adopt
  Thesis Methodology   AI Berkshire                   Adopt 思想
  Thesis Database      Hermes 自研                    Build
  Provider Layer       LangAlpha/Vibe-Trading         Adapt
  MCP Contract         Vibe-Trading                   Adapt
  Valuation Contract   Vibe-Trading quantlib          Adopt 思想
  Valuation Methods    FinRobot                       Reference
  ETF Engine           Hermes 自研                    Build
  Risk Engine          Hermes 自研                    Build
  Portfolio Ledger     Hermes 自研                    Build
  Execution Engine     Vibe-Trading                   Reject
  Trading Debate       TradingAgents                  Reject

------------------------------------------------------------------------

# 12. 迁移成本评估

  组件                 迁移成本
  -------------------- ----------
  Skill Prompt         低
  Skill Workflow       低-中
  Workspace 思想        中
  Provider Interface   中
  MCP Contract         中
  Valuation Engine     高
  Portfolio System     高
  Thesis Database      高

原则：

- 成本高的核心领域优先自研（Valuation / Portfolio / Thesis）；
- 概念可迁移、代码不迁移：所有 Adapt/Adopt 项均为自研实现，参考
  对象提供表设计 / 方法签名 / 契约层 / 文档字符串级别的模板，
  不引入其代码依赖；
- 各参考仓库规模（迁移成本量级参照）：LangAlpha 3414 文件 / 29
  migrations / 全栈；Vibe-Trading 2656 文件 / 含 desktop/frontend；
  ai-berkshire 2866 文件 / 纯 skill+prompt；FinRobot 176 文件 /
  教学级引擎。

------------------------------------------------------------------------

# 13. License / Dependency Audit

进入 Technical Specification 前，所有参考项目需要确认（TODO，写入
技术规范阶段任务清单）：

- License；
- Third-party dependency；
- 是否允许 vendor；
- 是否只能参考思想。

已知线索：FinRobot 含 NOTICE + TRADEMARK_POLICY；Vibe-Trading 与
LangAlpha 的 LICENSE 需逐一核对。任何代码级参考在复制前完成核查并
记录于 ADR。

原则：不 Fork。

采用：

``` text
Architecture Borrowing
+
Skill Borrowing
+
Schema Adaptation
```

------------------------------------------------------------------------

# 14. Final Architecture Direction

## Platform Layer

来源：LangAlpha（概念）+ Vibe-Trading（工程范式）

形成：

``` text
FastAPI
PostgreSQL
DuckDB
Parquet
MCP
Provider Layer
Job Layer
```

## Intelligence Layer

来源：AI Berkshire（方法论）+ LangAlpha（工作流概念）
+ Anthropic Financial Skills（skill 规范）

形成：

``` text
Skill System
Research Workflow
Thesis Methodology
Evidence Workflow
Review Lifecycle
```

## Calculation Layer

来源：Vibe-Trading Engineering + FinRobot Methods

形成：

``` text
Valuation Engine
ETF Engine
Risk Engine
Portfolio Engine
```

## Domain Layer

Hermes 自研：

``` text
Instrument
Portfolio
Transaction Ledger
Thesis
Evidence
Risk Model
```

------------------------------------------------------------------------

# 15. Technical Specification 输入

Architecture Benchmark v1.0 完成后：

``` text
Domain Model
 ↓
ERD
 ↓
Data Contract
 ↓
Provider Contract
 ↓
Engine Contract
 ↓
MCP Contract
 ↓
Test Matrix
```

本 Benchmark 对 Technical Specification 的具体输入：

1. Valuation Engine Contract：以 Vibe-Trading quantlib 为模板——
   无默认参数原则、Assumption 携带 basis、终值双方法交叉验证、
   contracts 层（MissingInputError / require_inputs / require_positive）；
2. research_workspace / research_thread / research_event /
   state_snapshot 表设计：参考 LangAlpha 概念（chat-centric 差异
   已剔除）；
3. Thesis 表设计：自研（冻结规范第 27 章），方法论参考 AI Berkshire
   thesis-tracker 的假设/红线/频率结构；
4. Skill 结构：Anthropic SKILL.md 规范 + AI Berkshire 工作流分层；
5. Evidence Chain：LangAlpha provenance 概念 + 冻结规范
   claim-evidence 粒度；
6. License / Dependency Audit 结论回流（第 13 章 TODO）。

------------------------------------------------------------------------

# Final Verdict

Hermes Investment Office 最终组合：

> LangAlpha 的 Research Operating Layer\
> + AI Berkshire 的 Investment Methodology\
> + Anthropic Financial Skills 的 Skill Standard\
> + Vibe-Trading 的 Engineering Contract 与确定性估值工程\
> + FinRobot 的 Valuation Methodology（方法清单）\
> + Hermes 自研 A 股长期投资 Backend

架构方向的外部验证：FinRobot 的"确定性计算与 LLM 叙述严格分离 +
full provenance"原则、Vibe-Trading quantlib 的"无默认参数 +
可复现"工程实践，与冻结规范 4.2/4.3 双向印证——本架构方向被两个
独立成熟项目背书。

下一阶段：

Technical Specification v0.1

TS-01 Domain Model Specification
