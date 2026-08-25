# Hermes Investment Office 完整验收报告

日期：2026-08-25（本地开发与 Dashboard 最终收口验收）

本报告覆盖 M0–M7、M3-① ETF 契约收口，以及最终本地产品形态。测试使用本机 Docker
PostgreSQL、独立 `hermes_test` 和确定性 fixture；日常运行不连接券商或真实资金账户，
持仓与现金只来自用户手工账本。

## 结论

全部里程碑判定为通过。MCP/REST、DB-backed 核心路径、迁移往返、架构边界、Ruff、
编译检查、桌面/移动端 Dashboard、Hermes 定时任务和本地备份均纳入最终复核。逐项矩阵见
[milestone-acceptance-matrix.md](milestone-acceptance-matrix.md)。

最新 Impeccable finish reviewer disposition 为 `ship`，未发现 material blocker。该结论来自
最终实现与桌面/移动截图复核，不是施工前预写的 `PASS` 或 `APPROVED`。

## 运行态与迁移证据

主 compose 通过本机 `backend/.env` 读取凭证，`scripts/hermes` 会安全加载该文件：

```bash
./scripts/hermes start
./scripts/hermes status
curl --fail http://127.0.0.1:8000/healthz
```

验收结果：`hermes-db` healthy、`hermes-backend` healthy、`hermes-dashboard` running，
health endpoint 返回
`{"status":"ok","service":"hermes-backend","version":"0.1.0"}`。Backend
entrypoint 在启动前执行 `alembic upgrade head`。

宿主机测试使用仅绑定回环地址的开发 overlay：

```bash
docker compose -f docker-compose.yml -f docker-compose.dev.yml up -d db
```

当前数据库 migration head：`g6b7c8d9e0f1`。在 `backend/` 且已设置本地
`HERMES_DB_URL`/`HERMES_TEST_DB_URL` 后：

```bash
./.venv/bin/alembic current
./.venv/bin/alembic check
```

结果：`g6b7c8d9e0f1 (head)`，`No new upgrade operations detected.`。

往返验证只对显式命名的临时本地库执行：创建空库 → `alembic upgrade head` →
`alembic downgrade f5a6b7c8d9e0` → `alembic upgrade head` → `alembic check` → 删除该
临时库。结果：所有 upgrade/downgrade 步骤成功，check 无差异；未对 `hermes`、
`hermes_test` 或任何生产数据库做 downgrade/drop。

MCP 运行态验证：

```bash
curl --fail -X POST http://127.0.0.1:8000/mcp/ \
  -H 'Content-Type: application/json' \
  -H 'Accept: application/json, text/event-stream' \
  --data '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"acceptance","version":"1"}}}'
```

结果：initialize 成功；`tools/list` 返回 31 个工具，`ACCOUNT_WRITE` 不存在。

## 测试与静态质量

在 `backend/`、本地测试库已迁移且 `HERMES_TEST_DB_URL` 指向独立测试库时：

```bash
./.venv/bin/python -m pytest -q
./.venv/bin/pytest -q tests/architecture
./.venv/bin/ruff check app tests
./.venv/bin/python -m compileall -q app tests migrations
./.venv/bin/lint-imports --no-cache
```

最终结果：**287 项 pytest 全部通过**；架构测试通过；Ruff `All checks passed!`；compileall 通过；
import-linter 的 4 条架构契约全部通过（0 broken contracts）。

DB-backed 核心 E2E：

- `tests/integration/test_m3_etf_e2e.py`：510300、513650、512890 三只 ETF，真实
  PostgreSQL 事务、fake deterministic Gateway、NAV/holdings/quota/index/FX/metric、PIT、
  `etf_holdings/v2` 路由，以及 QDII `UNKNOWN`/`WARNING`。
- `tests/integration/test_core_user_paths_e2e.py`：Instrument、显式 Watchlist、PAPER/REAL
  Portfolio、人工 ACCOUNT_WRITE、REVERSAL、Proposal、Research Note/Evidence、Thesis
  revision/PIT、Daily Context/Brief 全部走 REST adapter 与真实测试库。
- `tests/unit/test_mcp_server.py`：StreamableHTTP initialize、tools/list、tools/call、未知
  工具拒绝和真实测试库 `resolve_instrument`。

## 发布前全量缺陷扫描

针对 518680.SH 首次 Hermes 研究暴露的问题，发布前对行情、ETF、研究上下文、REST/MCP、
配置模板和真实 Dashboard 路径做了交叉扫描，并完成以下修复：

- 修复 Parquet 经 pandas 读取后将空时间恢复成 `NaT`、继而导致价格历史 JSON 序列化报错；
  `get_price_history` 现可稳定返回完整日线，空来源时间以 `null` 表达。
- 修复部分 GET 列表参数被 OpenAPI 误判为 request body；市场和财务查询现全部使用 query
  parameters，默认财务指标也改为系统实际支持的指标名。
- 非 QDII ETF 不再触发额度同步；518680.SH 的 quota 状态现为 `NOT_APPLICABLE`，不会再被
  其他 QDII 标的的告警污染。
- 修复日内同步把 ETF 指标写到当日 23:59、形成“未来数据”的问题；同时统一 Dashboard 与
  ETF API 的最新修订选择规则，优先展示同一市场日最近重算的有效版本。
- Hermes 研究上下文现会返回标的关联的 Thesis/Evidence，并要求研究流程加载已有 Thesis，
  避免已有观点在对话中被误报为空。
- 修复 `.env.example`、Docker Compose 代理默认值与本地数据库模板，使首次启动不再依赖
  开发机专属代理或绝对路径。

真实运行态扫描结果：28 条 REST 读取路径中 27 条返回 200，唯一 404 是“尚无最新估值”的
合法空状态；20 个只读 MCP 工具均返回有效结果或契约内空状态；成功读取 3 个 Hermes 历史
会话；桌面端与 390px 移动端均完成四入口、K 线/均线、A 股红涨绿跌、会话列表/导出/删除、
手工账本与模块间距复核。浏览器控制台及 Backend 日志未发现未处理异常。

## Dashboard 最终实现证据

- 一级导航只有“今日、标的、组合、问 Hermes”；ADR-011 已取代旧五入口导航。
- 系统无券商连接、下单或资金划转；组合是用户手工维护、仅追加、可审计的本地 REAL 账本。
- 所有持久写入采用“填写或选择 → 预览 → 独立确认”；取消确认不调用 Backend 写接口。
- 桌面观察池展示六列；移动端固定保留“代码、名称、最新价、日涨跌”四个核心列。
- 标的详情展示不复权日 K 与 MA5/MA20/MA30，行情采用 A 股红涨绿跌，并提供独立“来源”页。
- Hermes 会话历史可列表、恢复、中文 Markdown 导出；永久删除需要第二次明确确认。
- 结构化来源覆盖 TuShare、AkShare 新浪/东方财富/同花顺、Yahoo Finance、FRED、乐咕乐股，以及由新浪同步并可人工校准的交易日历；来源记录携带时点、质量和 provenance。
- 视觉实现使用 `seed=c375a27a`，可见文字字号下限为 `12px`；方向参考图不是逐像素验收稿。
- 最终截图为 `.impeccable/review/desktop.png` 与 `.impeccable/review/mobile.png`；finish reviewer disposition 为 `ship`，无 material blocker。

## 里程碑验收摘要

| M | 结果 | 核心交付 |
|---|---:|---|
| M0 | ✅ | ORM/迁移、Instrument、mapping、约束、触发器、启动 |
| M0.5 | ✅ | Provider capability、单位/Fallback/Attention/Parquet 契约 |
| M1 | ✅ | Gateway、raw/provenance、PIT、Parquet+PG pointer、calendar/FX/CA、jobs |
| M1.5 | ✅ | 单资产垂直闭环、估值、Thesis、PAPER、Daily Brief、MCP 基础链路 |
| M2 | ✅ | Portfolio Ledger、REAL/PAPER、proposal、REVERSAL、audit |
| M3 | ✅ | valuation/risk/ETF/QDII/确定性引擎 |
| M3-① | ✅ | freshness、3 ETF E2E、v1/v2 routing、路径脱敏、TS-04 envelope |
| M4 | ✅ | Research/Evidence/Thesis/PIT/不可变历史 |
| M5 | ✅ | 28+3 MCP、REST、权限、错误码、runtime policy |
| M6 | ✅ | scheduler、attention、daily context/brief、freshness 门禁 |
| M7 | ✅ | 四入口 REST-only Dashboard、本机 Hermes 会话入口、预览确认式手工账本、来源页、移动四列、localhost 部署与备份；finish reviewer：`ship`（无 material blocker） |

## 从零启动与使用

1. 安装并启动 Docker Desktop，在本机 `backend/.env` 保存数据库口令和可选 Provider
   凭证。该文件不提交到仓库。

2. 启动、初始化产品默认项并刷新数据：

   ```bash
   ./scripts/hermes start
   ./scripts/hermes bootstrap
   ./scripts/hermes refresh
   ./scripts/hermes open
   ```

   `bootstrap` 幂等创建默认 REAL 手工组合、核心观察池，以及 510300.SH、513650.SH、
   512890.SH 三个默认标的；不会创建虚构行情、持仓、现金或交易。

3. 如需宿主机迁移/测试，使用 dev overlay；它只绑定 `127.0.0.1:5432`：

   ```bash
   docker compose -f docker-compose.yml -f docker-compose.dev.yml up -d db
   cd backend
   python3.12 -m venv .venv
   ./.venv/bin/pip install -r requirements.lock
   ./.venv/bin/pip install -e ".[dev]"
   cp .env.example .env
   # 只在本机 .env 或环境变量中填写 HERMES_DB_URL/HERMES_TEST_DB_URL
   ./.venv/bin/alembic upgrade head
   ./.venv/bin/alembic check
   ```

4. 日常使用直接打开 `http://127.0.0.1:8501`：

   - “组合 → 手工记账”录入现有持仓、现金，以及后续买卖、分红和费用；每次写入均先预览再确认。
   - “标的”增删观察对象，并在同一详情中查看行情、估值、研究、事件与来源；“问 Hermes”用于开放式研究和历史会话管理。
   - Hermes 只能提出建议；用户批准/拒绝后，实际日期、价格、数量和费用仍由用户登记。
   - 数据缺失或过期会显示 WARNING/FAILED，不会用示例值伪装为正常。

5. Backend 默认在工作日 07:30（Asia/Shanghai）刷新。Hermes 已注册 31 个 MCP 工具，
   并配置工作日 09:00 日报、周六 10:00 周报、每季度首日 10:00 复核；本机每日 03:00
   自动备份，保留 30 份日备份和 12 份周备份。运维命令见
   [本地使用手册.md](本地使用手册.md)。

## 安全边界与禁止项

- 可用：行情/财务/PIT、ETF/QDII、估值、风险、Research/Evidence、Thesis、Briefing、
  MCP/REST、手工 REAL 账本和提案记录。
- REAL transaction/reversal 只能走本机人工 `ACCOUNT_WRITE` REST 入口；缺少 header 返回
  403。MCP、Hermes Agent、Job、Scheduler、Engine 不具备此权限。
- 不可用且不会实现为产品路径：券商连接、真实 Broker 下单、真实资金划转、自动执行
  proposal、自动生成持仓或现金。
- `REVERSAL` 只追加抵消记录，原交易不可更新；审计和 provenance 与业务写入同事务，
  数据库触发器提供 append-only 兜底。
- 不提交或输出 `.env`、API key、数据库口令；物理 Parquet 路径不会进入 REST/MCP/Skill
  公共结果。

## 故障排查

- `backend` 不健康：先看 `docker compose -f docker-compose.yml ps` 和
  `docker compose -f docker-compose.yml logs backend db`，确认 `healthz` 与容器内
  migration 日志。
- 宿主机测试连接失败：确认使用 dev overlay，并检查 `HERMES_TEST_DB_URL` 指向
  `hermes_test`，不要改用生产库。
- migration 有差异：运行 `alembic current`、`alembic check`；往返验证只使用新建的
  临时本地库，禁止在含数据的库上 downgrade。
- Provider 网络失败：查看 Job/Provenance/Freshness；使用 deterministic fixture 验证
  逻辑，不能关闭 freshness 门禁或静默 fallback。
