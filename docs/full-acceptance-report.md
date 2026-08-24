# Hermes Investment Office 完整验收报告

日期：2026-08-24（本地开发验收）

本报告覆盖 M0–M7 以及 M3-① ETF 契约收口。验收只使用本机 Docker PostgreSQL、独立
`hermes_test` 和确定性 fixture；没有连接生产库、真实 Broker 或真实资金账户。

## 结论

全部里程碑判定为通过。MCP/REST、DB-backed 核心路径、迁移往返、架构边界、Ruff 和
编译检查均已纳入最终复核。逐项矩阵见
[milestone-acceptance-matrix.md](milestone-acceptance-matrix.md)。

## 运行态与迁移证据

主 compose 使用本机环境变量 `HERMES_POSTGRES_PASSWORD`，不把凭证写入仓库：

```bash
export HERMES_POSTGRES_PASSWORD='<local-only-password>'
docker compose -f docker-compose.yml up -d --build
docker compose -f docker-compose.yml ps
curl --fail http://127.0.0.1:8000/healthz
```

验收结果：`hermes-db` healthy、`hermes-backend` healthy，health endpoint 返回
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

最终结果：全量 pytest `259 passed`；架构测试通过；Ruff `All checks passed!`；
compileall 通过；import-linter 通过（0 broken contracts）。

DB-backed 核心 E2E：

- `tests/integration/test_m3_etf_e2e.py`：510300、513650、512890 三只 ETF，真实
  PostgreSQL 事务、fake deterministic Gateway、NAV/holdings/quota/index/FX/metric、PIT、
  `etf_holdings/v2` 路由，以及 QDII `UNKNOWN`/`WARNING`。
- `tests/integration/test_core_user_paths_e2e.py`：Instrument、显式 Watchlist、PAPER/REAL
  Portfolio、人工 ACCOUNT_WRITE、REVERSAL、Proposal、Research Note/Evidence、Thesis
  revision/PIT、Daily Context/Brief 全部走 REST adapter 与真实测试库。
- `tests/unit/test_mcp_server.py`：StreamableHTTP initialize、tools/list、tools/call、未知
  工具拒绝和真实测试库 `resolve_instrument`。

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
| M7 | ✅ | API-only Dashboard、Skills、localhost 部署 |

## 从零启动与使用

1. 安装 Docker Desktop、Python 3.12，并在本机设置数据库口令：

   ```bash
   export HERMES_POSTGRES_PASSWORD='<local-only-password>'
   ```

2. 启动主 compose：

   ```bash
   docker compose -f docker-compose.yml up -d --build
   curl --fail http://127.0.0.1:8000/healthz
   ```

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

4. 显式创建 Instrument 和 Watchlist；不会自动 seed ETF：

   ```bash
   curl -X POST http://127.0.0.1:8000/v1/instruments \
     -H 'Content-Type: application/json' \
     -d '{"instrument_type":"CN_ETF","symbol":"510300","name":"沪深300ETF","market":"SSE","currency":"CNY"}'
   curl -X POST http://127.0.0.1:8000/v1/watchlists \
     -H 'Content-Type: application/json' -d '{"name":"本地研究池"}'
   curl http://127.0.0.1:8000/docs
   ```

   将创建返回的 UUID 通过 `POST /v1/watchlists/{watchlist_id}/members` 加入观察池。
   已有 510300/513650/512890 身份时，可按 `docs/m3-local-runbook.md` 显式调用
   `seed_existing_etf_pool`；缺失身份只报告，不创建、覆盖或删除。

5. 通过 REST 使用市场、财务、估值、组合、Research、Thesis 和 Briefing；通过 MCP 使用
   `http://127.0.0.1:8000/mcp`。同步调用只创建 Job，随后用 `get_job_status` 查询，
   再以 `as_of` 调用读工具。外网 Provider 不可用时，使用 deterministic fixture 完成
   测试，不伪造生产数据。

   Backend EOD scheduler 是显式 opt-in：启动前设置
   `HERMES_SCHEDULER_ENABLED=true`，必要时设置 `HERMES_SCHEDULER_TIMEZONE`、
   `HERMES_SCHEDULER_HOUR`、`HERMES_SCHEDULER_MINUTE`。它只读取当前显式观察池与 REAL
   持仓形成的 universe；没有观察池时跳过，不会自动 seed。

6. 安装 Hermes skills（可选）：

   ```bash
   ln -sfn /Users/blyadsuka/Developer/Investment_Agent/skills ~/.hermes/skills/investment
   hermes skills list
   hermes mcp add investment-backend --url http://127.0.0.1:8000/mcp --connect-timeout 15
   ```

7. 启动只读 Dashboard（可选）：

   ```bash
   python3.12 -m venv .dashboard-venv
   ./.dashboard-venv/bin/pip install -r dashboard/requirements.txt
   HERMES_BACKEND_URL=http://127.0.0.1:8000 \
     ./.dashboard-venv/bin/streamlit run dashboard/app.py \
     --server.address 127.0.0.1 --server.port 8501
   ```

## 安全边界与禁止项

- 可用：行情/财务/PIT、ETF/QDII、估值、风险、Research/Evidence、Thesis、Briefing、
  MCP/REST、PAPER 模拟和提案记录。
- REAL transaction/reversal 只能走本机人工 `ACCOUNT_WRITE` REST 入口；缺少 header 返回
  403。MCP、Hermes Agent、Job、Scheduler、Engine 不具备此权限。
- 不可用且不会实现为自动路径：真实 Broker 下单、真实资金划转、自动执行 proposal、生产
  数据写入/删除/重置。
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
