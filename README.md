# Hermes Investment Office

本项目是本地单机投资研究与组合操作系统：Backend 负责事实、确定性计算、持久化和审计；Hermes 通过受控 MCP 进行编排与解释；Dashboard 只消费 REST API。当前 M0–M7 的实现、迁移、契约和验收证据见 [完整验收报告](docs/full-acceptance-report.md) 与 [里程碑矩阵](docs/milestone-acceptance-matrix.md)。

## 从零启动

前置条件：Docker Desktop、Python 3.12。所有命令都在本地开发环境执行。

1. 设置本机数据库凭证，不要把值写入 Git 或聊天记录：

   ```bash
   export HERMES_POSTGRES_PASSWORD='<仅本机保存的数据库口令>'
   ```

2. 启动 Docker 中的 PostgreSQL 与 Backend。主 compose 不向宿主机暴露 PostgreSQL，只绑定 Backend 到回环地址；容器启动时会自动执行 `alembic upgrade head`：

   ```bash
   docker compose -f docker-compose.yml up -d --build
   docker compose -f docker-compose.yml ps
   curl http://127.0.0.1:8000/healthz
   ```

   Backend EOD scheduler 默认关闭。确认已显式创建并加入观察池后，如需启用，可在启动前设置
   `HERMES_SCHEDULER_ENABLED=true`（可选 `HERMES_SCHEDULER_TIMEZONE/HOUR/MINUTE`）；空观察池
   会安全跳过，不会自动 seed。

3. 若需要在宿主机运行 pytest/uvicorn，使用仅绑定 `127.0.0.1` 的开发覆盖文件，并在本机环境中设置完整的 `HERMES_DB_URL`（凭证部分不展示）：

   ```bash
   docker compose -f docker-compose.yml -f docker-compose.dev.yml up -d db
   cd backend
   python3.12 -m venv .venv
   ./.venv/bin/pip install -r requirements.lock
   ./.venv/bin/pip install -e ".[dev]"
   cp .env.example .env       # 只在本机填写；不要提交 .env
   export HERMES_DB_URL='postgresql+psycopg2://<user>:<password>@127.0.0.1:5432/<database>'
   ./.venv/bin/alembic upgrade head
   ./.venv/bin/alembic check
   ./.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000
   ```

## 显式创建实体与同步

迁移、Docker entrypoint 和启动装配不会自动 seed ETF 池，也不会覆盖已有 Instrument。先通过 REST `POST /v1/instruments` 创建必要身份，再通过 `POST /v1/watchlists` 创建观察池并用 `POST /v1/watchlists/{watchlist_id}/members` 加入成员；已有 ETF 可按 [M3 本地运行手册](docs/m3-local-runbook.md) 显式调用 `seed_existing_etf_pool`。同步只创建异步 Job：

   ```bash
   curl -s -X POST http://127.0.0.1:8000/v1/instruments \
     -H 'Content-Type: application/json' \
     -d '{"instrument_type":"CN_ETF","symbol":"<symbol>","name":"<name>","market":"SSE","currency":"CNY"}'
   curl -s -X POST http://127.0.0.1:8000/v1/watchlists \
     -H 'Content-Type: application/json' -d '{"name":"本地研究池"}'
   curl -s "http://127.0.0.1:8000/v1/instruments/resolve?provider=<provider>\&symbol=<symbol>"
   curl -s -X POST http://127.0.0.1:8000/v1/briefing/contexts \
     -H 'Content-Type: application/json' -d '{"market_date":"<YYYY-MM-DD>","instruments":[]}'
   ```

主要 REST 面：`/v1/instruments`、`/v1/watchlists`、`/v1/market`、`/v1/fundamentals`、`/v1/valuations`、`/v1/portfolios`、`/v1/research`、`/v1/theses`、`/v1/briefing`。完整接口以 `/docs` 和 OpenAPI 为准。

MCP 地址为 `http://127.0.0.1:8000/mcp`。注册 Hermes Agent：

   ```bash
   hermes mcp add investment-backend --url http://127.0.0.1:8000/mcp --connect-timeout 15
   ln -sfn "$PWD/skills" ~/.hermes/skills/investment
   hermes skills list
   ```

## Dashboard

Dashboard 不连接数据库、不读取数据卷、不计算指标：

   ```bash
   python3.12 -m venv .dashboard-venv
   ./.dashboard-venv/bin/pip install -r dashboard/requirements.txt
   HERMES_BACKEND_URL=http://127.0.0.1:8000 \
     ./.dashboard-venv/bin/streamlit run dashboard/app.py \
     --server.address 127.0.0.1 --server.port 8501
   ```

打开 `http://127.0.0.1:8501`，可查看 Daily Context/Brief、组合/持仓、研究证据和 Thesis PIT 版本。

## 测试与验收

   ```bash
   cd backend
   ./.venv/bin/python -m pytest -q
   ./.venv/bin/ruff check app tests
   ./.venv/bin/python -m compileall -q app tests migrations
   ./.venv/bin/lint-imports --no-cache
   ```

迁移往返只在独立本地临时库执行：`upgrade head → downgrade <上一 head> → upgrade head → alembic check`。不要对含用户数据的库做 downgrade。验收命令和结果集中记录在 [docs/full-acceptance-report.md](docs/full-acceptance-report.md)。

## 安全边界

- 支持研究、行情/财务/PIT、ETF/QDII、估值、风险、组合、Research/Evidence、Thesis、Briefing、MCP/REST 和 PAPER 模拟。
- REAL 组合只能通过本地人工 `ACCOUNT_WRITE` 入口写入交易；MCP、Hermes、Job、Scheduler、Engine 都不能写入 REAL transaction。
- 系统绝不调用真实 Broker 下单、划转资金或自动执行交易提案；`create_trade_proposal` 最高只产生提案。
- `REVERSAL` 是追加更正记录，不修改原交易；审计与 provenance 同事务写入，append-only 由数据库触发器兜底。
- 物理 Parquet 路径、存储细节、环境变量值和 Provider 凭证不出现在 REST/MCP 结果、Skill 或提交中。
- 只对本地开发库/独立测试库操作；禁止生产数据库连接、删除或重置。

故障排查：先看 `docker compose ps` 和 `docker compose logs db backend`，再确认 `healthz`、数据库 URL、`alembic current` 和 `alembic check`；Provider 外网不可用时使用 deterministic fixture，不降低数据库、契约或安全验收标准。
