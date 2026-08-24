# M3 ETF 本地运行手册

本手册只描述本地/测试环境的 M3 ETF-first 切片。它不代表 M3 或 M3-① 已完成，
也不包含自动交易、`ACCOUNT_WRITE` 或 M2 REVERSAL。

## 启动与迁移

生产形态的 compose 不把 PostgreSQL 端口暴露到宿主机；本地开发显式使用 dev
override，只绑定回环地址：

```bash
docker compose -f docker-compose.yml -f docker-compose.dev.yml up -d db
cd backend
cp .env.example .env        # token 只填入本地 .env，不提交
./.venv/bin/alembic upgrade head
./.venv/bin/alembic check
./.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000
```

`HERMES_DATA_DIR`（默认 `../data`）必须指向可持久化目录；其中 `raw/`、
`parquet/` 和 `documents/` 不进 Git。容器部署时分别挂载数据卷，并把
`provider-capability.yaml`、`providers.yaml` 和两个 ETF 配置文件带入镜像/卷。

安全测试库应使用独立数据库：

```bash
docker exec <postgres-container> createdb -U hermes hermes_m3_local_test
HERMES_DB_URL=postgresql+psycopg2://hermes:hermes@127.0.0.1:5432/hermes_m3_local_test \
  ./backend/.venv/bin/alembic upgrade head
```

需要验证回滚时，只在该测试库执行 `alembic downgrade b1c2d3e4f5a6`，再执行
`alembic upgrade head`；不要在含用户数据的库上做迁移循环。

## 数据同步与读取边界

`sync_market_data` 只创建 job。ETF job 通过 Provider Gateway 写入 NAV、L1
持仓、额度状态、raw 和 provenance；macro job 写入指数历史、FX、指数估值及
其 PG pointer/Parquet。示例 MCP 参数：

```json
{
  "universe": ["<instrument_id>"],
  "start_date": "2026-08-01",
  "end_date": "2026-08-24",
  "data_type": "ETF",
  "sync_kind": "etf"
}
```

宏观数据把 `sync_kind` 改为 `macro`。先读取 `get_job_status`，再调用
`get_market_metrics`；后者只做 PIT 读取和有限确定性计算，不在请求内调用
Provider。`as_of` 会限制指标快照和 PG pointer 的可见范围。

ETF 持仓读取必须使用 `etf_holding_snapshots.parquet_path` 指向的单个文件，
不能扫描整个目录。当前新写入版本为 `etf_holdings/v2`，其
`weight_ratio = weight_pct / 100`；`v1` 只保留兼容读取。业务/MCP 出参只返回
Level 元数据、freshness 和 provenance，不返回物理 `parquet_path`。

## 显式观察池导入

迁移、启动装配和 Docker entrypoint 都不会 seed 510300/513650/512890。若本地
已经有这些 `Instrument`，需要人工显式调用 `WatchlistService.seed_existing_etf_pool()`；
该方法只加入已有身份，不创建、覆盖或删除 Instrument：

```python
from app.instruments.service import WatchlistService

watchlist = WatchlistService(session).ensure_default_watchlist()
WatchlistService(session).seed_existing_etf_pool(
    watchlist.watchlist_id,
    symbols=("510300", "513650", "512890"),
)
session.commit()
```

测试 fixture 可以在独立测试库中创建上述三只 ETF、对应 INDEX、calendar rows
和 fake Gateway 数据，再调用真实 `etf_sync_job`/`macro_sync_job`；fixture 不得
写入生产池。REST 观察池入口为 `GET /v1/watchlists`、
`POST /v1/watchlists/{watchlist_id}/members`、
`DELETE /v1/watchlists/{watchlist_id}/members/{instrument_id}`，成员采用软删除
并保留 `added_at`/`removed_at` 时态。

