# Codex 交接文档（Hermes Investment Office）

> 写给：Codex（接手施工的 Agent）
> 来自：初始开发者视角（2026-08-24）
> 状态：M0/M0.5/M1/M1.5 已完成（173 测试全绿），下一步 M3 ETF Engine
>
> 阅读顺序建议：本文档 → agent.md → 冻结规范 §47（里程碑）→ ts06 §4（ETF Engine 契约）→ ADR-006/007

---

## 1. 项目是什么

**个人 AI 投资办公室**：一台 Mac 上的长期投资研究系统（A 股个股 / A 股 ETF / A 股场内 QDII ETF，3-10 年持有）。

```text
Hermes Agent（大脑，Nous Research 产品，DeepSeek 模型，~/.hermes）
   ├── MCP 客户端 → Investment Backend
   └── cron + skills（TS-09）
        │
        ▼
Investment Backend（事实与计算平面，FastAPI 模块化单体）
   ├── /v1/mcp（MCP Server，FastMCP）  ├── /v1/*（REST）
   ├── 40+ 表 PostgreSQL（容器，物理隔离）
   └── Parquet/DuckDB（ohlcva/v1 等）
```

**关键身份澄清（ADR-008）**：文档中的 "Hermes" = 角色名 = **Nous Research 的 Hermes Agent 产品**（已装在这台 Mac，`~/.hermes`）。不是 DSH、不是抽象概念。

**不是**：自动交易、短期预测、高频。Hermes 只提建议，交易由用户人工确认落账。

## 2. 文档体系（按优先级读）

| 文档 | 内容 | 状态 |
|---|---|---|
| `agent.md` | 工作指南总入口（架构/纪律/导航）| ✅ |
| `docs/Hermes_Investment_Office_后端架构冻结规范_v1.0_Consolidated.md` | **唯一施工基线（FROZEN）**，§47 里程碑验收 | ✅ |
| `docs/ts01~ts09.md` | 技术规范（领域/ERD/ORM/数据契约/Provider/Engine/MCP/测试矩阵/Hermes 集成）| ✅ 全 FROZEN |
| `docs/ADR/ADR-001~008` | 决策记录（**先读 004/005/006/007/008**）| ✅ |
| `docs/data-contracts/` | provider-capability（矩阵）/parquet-schema/unit-normalization/provider-prep | ✅ |
| `docs/M1_acceptance_report.md`、`docs/M1_5_acceptance_report.md` | 施工历史与踩坑记录（**必读**）| ✅ |
| `docs/mcp-server-design.md` | MCP 集成方案（FastMCP）| ✅ |
| `docs/dashboard-design-reference.md` | Dashboard 信息架构（M7 输入）| ✅ |

**冲突优先级**：`TS > 冻结规范 v1.0 > 旧版本`；**任何冻结项变更必须新增 ADR**，禁止静默偏离。

## 3. 环境与命令

```bash
# Python 3.12 venv
cd backend && ./.venv/bin/python
# 数据库（开发映射 127.0.0.1:5432；生产 compose 无端口暴露）
docker compose -f docker-compose.yml -f docker-compose.dev.yml up -d db
# 迁移（环境变量 HERMES_DB_URL）
export HERMES_DB_URL="postgresql+psycopg2://hermes:hermes@127.0.0.1:5432/hermes"
./.venv/bin/alembic upgrade head && ./.venv/bin/alembic check   # check 必须零差异！
# 测试（独立测试库；fresh test 全绿是验收门槛）
export HERMES_TEST_DB_URL="postgresql+psycopg2://hermes:hermes@127.0.0.1:5432/hermes_test"
./.venv/bin/python -m pytest tests/
# 凭证：backend/.env（HERMES_TUSHARE_TOKEN / HERMES_FRED_API_KEY）——只走环境变量，禁止进 git/代码
# 备份：scripts/backup.sh（pg_dump + rsync hardlink 快照）
```

**网络环境**（ADR-005）：系统透明代理 + 显式代理 `127.0.0.1:7892`。TuShare/新浪/乐咕直连；**eastmoney 直连被阻需代理**；Yahoo/FRED 走 env 代理。Provider 网络三态（direct/proxy/env）在 provider-capability.yaml。

## 4. 已完成（代码事实，验收通过）

| 里程碑 | 关键产出 | 验收 |
|---|---|---|
| M0 | 40 表 ORM + Alembic（**迁移链：b00c819f819c → c9d4f2a1b3e5 → f6e5d4c3b2a1 → a7b8c9d0e1f2**）+ Instrument Master + 架构测试 | 26 测试 |
| M0.5 | 数据源全实测（provider-capability.md 冻结；TuShare 2000 积分档全通）| S1-S9 |
| M1 | 7 个 Provider 实现 + Gateway/限流/退避 + Raw Evidence + Provenance 无损落库 + PIT + Parquet ohlcva/v1（schema.json 机器校验）+ 日历/FX/CA + 同步 job | 152 测试 + 茅台真实数据 |
| M1.5 | Valuation DCF 最小版（黄金值）+ Thesis 最小版 + Paper Portfolio（Ledger replay）+ Daily Context/Brief + **MCP Server（8/28 工具）** | 173 测试 + 茅台全流程 + MCP 链路 |

真实数据已就绪：茅台 483 根日线 + 20 条财务事实 + 完整 provenance（`scripts/m1_acceptance_demo.py` / `scripts/m1_5_acceptance_demo.py` 可复跑）。

## 5. 开发者视角避坑指南（踩过的坑，最重要）

1. **CHECK 约束命名（convention 二次套用坑）**：`enum_ck/range_ck` 生成**短名**（如 `quality_status_check`），由 Base 的 naming convention（`ck_%(table_name)s_%(constraint_name)s`）加前缀。**禁止**传完整名（`ck_xxx_yyy_check`）——convention 会再套一次表名，产生 66+ 字符双前缀名，超 PG 63 字符被截断，`alembic check` 永远往返失败。改枚举/约束后必须 `alembic check` 零差异。
2. **循环 FK（theses ↔ thesis_revisions）**：autogenerate 的表序是**字母序**不是拓扑序！FK 一律在迁移 upgrade 末尾 `op.create_foreign_key`、downgrade 开头 `drop_constraint`（参考 b00c819f819c 的重写方式）。新增表带 FK 时照此办理。
3. **append-only 触发器语义**（迁移 c9d4f2a1b3e5 + a7b8c9d0e1f2）：
   - 13 张表：无条件拒绝 UPDATE/DELETE（fn_reject_update）；
   - **valuation_runs：状态机守卫**（fn_valuation_runs_guard）——DELETE 永远拒绝；COMPLETED 后仅允许 →SUPERSEDED 且冻结列（结果/假设/engine_version）不可变；**状态迁移与完成回填必须合法**。⚠️ SQLAlchemy autoflush 会把"状态+结果回填"拆成两次 UPDATE 导致第二次被拒——写入路径保证中间无查询，或显式控制 flush（参考 app/valuation/service.py `_persist_result`）；
   - 快照表（position_snapshots 等）：upsert-by-supersede（键不变守卫，M1.5 新增迁移）。
4. **模块依赖（ARCH-DEP 静态扫描）**：引擎域（valuation/portfolio/risk/etf）**禁止 import `app.providers.*`**；`ProvenanceEnvelope` 全局类型在 `app/common/provenance.py`（providers/contracts/base.py 只是 re-export）；**装配层在 `app/bootstrap.py`**（mcp/ 禁止 import providers.*）。
5. **MCP 挂载（FastMCP/mcp 2.0 SDK）**：`path="/"` + `app.mount("/mcp", ...)`（传 "/mcp" 会 307→404，已知 issue #1367）；**Starlette 挂载不传播子 app lifespan → 根 lifespan 手动进入 session_manager**；Host 校验需带端口（`127.0.0.1:*` 模式）。工具白名单 = TS-07 28 工具（架构测试断言 tools/list 逐名相等）。
6. **数据源事实**（M0.5 实测）：ETF 场内行情用 **TuShare `fund_daily`**（`daily` 对 ETF 返回空）；SHARES_OUTSTANDING 用 **`daily_basic.total_share`**（万股 ×10000；fina_indicator 无股本列）；指数估值用**乐咕乐股**（不是自聚合）；折溢价用 AkShare `fund_etf_spot_em`（列名"基金折价率"，负=折价）；基金季报持仓 `fund_portfolio_hold_em`。
7. **测试库污染**：调试脚本若 commit 会污染 hermes_test（SQLAlchemy 测试报 MultipleResultsFound）——重建：`DROP DATABASE hermes_test; CREATE DATABASE ... OWNER hermes;` + `alembic upgrade head`。
8. **单位与序列化**：TuShare 报表恒为元（四元组 1:1）；JSONB 内嵌 Decimal 写库前转 str（ts03 §9.4）；金额全库 `NUMERIC`（禁 float 列）。
9. **ruff 纪律**：改完跑 `ruff` + `py_compile`；架构测试（tests/architecture/）必须保持全绿——它们是冻结纪律的机器化。

## 6. 下一步工作（优先级已定）

### 🔴 M3-① ETF Engine（立即开始，用户观察池 3 只全是 ETF）
- 契约：**ts06 §4**（ETF Engine Contract）+ 冻结规范 §23；表：`etf_metric_snapshots`（ts02 §4.6，已建）
- 子集范围（先做）：① 指数估值带（乐咕数据 → INDEX 估值，VERY_CHEAP~VERY_EXPENSIVE 五档）→ ② QDII 折溢价（`premium_discount` + `fx_contribution`，**四日期时序对齐**：market_date/nav_date/underlying_session_date/fx_as_of，对齐失败置 NULL+flag 而非假精确）→ ③ 穿透 Level 1（`fund_portfolio_hold_em` 季报持仓 → etf_holding_snapshots）→ ④ `get_etf_metrics` MCP 工具（挂上 MCP 白名单）
- 观察池标的：**510300（沪深300ETF）/ 513650（南方标普500ETF QDII）/ 512890（红利低波ETF）**（ADR-006）
- 数据源：TuShare fund_daily/fund_nav、AkShare fund_etf_spot_em/fund_portfolio_hold_em、乐咕 stock_index_pe_lg、Yahoo ^GSPC/^NDX、FRED DEXCHUS（全部 M0.5 已实测可用）

### 🟡 M2 Portfolio Core（等前置 ADR）
- CASH 资产类型 + REVERSAL 策略两个 ADR（设计侧起草中）；契约 ts06 §5、ts02 §7；REAL 组合 + Trade Proposal 全状态机 + **ACCOUNT_WRITE 人工入口（localhost admin，Hermes 无权）**

### 🟢 后续
- M3-② 个股模型 DDM/COMPARABLE/SCENARIO + Risk Engine → M4 Research Memory → **M5 Hermes 集成（MCP 28 工具全量对齐，当前 8/28；hermes mcp add + skills + cron，见 TS-09）** → M6 自动化 → M7 Dashboard（信息架构已冻结）

## 7. 施工纪律（机器化 + 文字）

1. **文字纪律**：agent.md §11 的 19 条 + 补充：不 Fork/代码不迁移；个人自用场景下 AGPL/EPL 代码可复用但**必须入 `vendor/<项目>/` 目录并保留许可证头**（许可证决策见 dashboard-open-source-reference.md §4）；观察池 ∪ 持仓 = 每日同步范围（ADR-006 D2）；Hermes 载体 = Nous Hermes Agent（ADR-008）。
2. **机器纪律（每个子阶段验收 = fresh test 全绿）**：
   - `pytest tests/` 全绿（含架构测试）
   - `alembic check` 零差异（ORM ↔ DB）
   - `ruff check` 干净
   - 真实数据演示（参考 M1/M1.5 的 acceptance demo 模式）
3. **提交规范**：主分支 main；提交信息带里程碑前缀（如 `M3-01 ETF 指数估值带：...`）；每个子阶段一个 commit 簇 + 验收报告（`docs/M3_acceptance_report.md` 照 M1/M1.5 格式）。

## 8. 协作与验证清单（开工前）

```text
[ ] 读 agent.md + 本文档 + M1_5_acceptance_report.md
[ ] 环境验证：docker compose ps（hermes-db healthy）、pytest 173 全绿、alembic check 零差异
[ ] 复跑 scripts/m1_5_acceptance_demo.py（真实茅台链路）
[ ] 确认 backend/.env 凭证在位（tushare/fred）
[ ] 开始 M3-① ETF Engine（契约 ts06 §4）
```

---

*本文档由初始开发者编写；Codex 审计后的任何补充/修正请以 ADR 或本文档修订记录方式回流。*
