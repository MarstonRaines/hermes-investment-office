# Hermes Investment Office

> 个人 AI 投资办公室 —— AI Investment Research Platform + Personal Portfolio Operating System
>
> 架构输入（FROZEN）：[后端架构冻结规范 v1.0](docs/Hermes_Investment_Office_后端架构冻结规范_v1.0_Consolidated.md) · [Technical Specification 系列](docs/ts01.md~ts08.md) · [Agent 工作指南](agent.md)

## 目录结构

```text
backend/          Investment Backend（事实与计算平面，FastAPI 模块化单体）
  app/            领域模块（instruments/market_data/valuation/portfolio/...）
  migrations/     Alembic 迁移（40 表 + append-only 触发器）
  tests/          unit/（单元）· architecture/（架构测试，冻结规范 §46）
dashboard/        Streamlit 展示层（M7）
data/             Raw Evidence Store（raw/parquet/documents，禁止进 git）
docs/             架构文档（冻结规范 / Benchmark / TS-01~08 / ADR）
docker-compose.yml    生产形态：PostgreSQL 容器内部网络（§4.4 物理隔离）
docker-compose.dev.yml 开发形态：仅本机回环映射 5432
```

## 快速开始（M0）

```bash
# 1. 数据库（生产 compose：无端口暴露；开发加 dev override）
docker compose -f docker-compose.yml -f docker-compose.dev.yml up -d db

# 2. 后端环境
cd backend
python3.12 -m venv .venv && ./.venv/bin/pip install -e ".[dev]"
cp .env.example .env                      # 按需填写（token 不进 git）

# 3. 迁移
HERMES_DB_URL=... ./.venv/bin/alembic upgrade head

# 4. 启动
./.venv/bin/uvicorn app.main:app --port 8000   # 绑定 127.0.0.1（默认）

# 5. 测试（fresh test：单元 + 架构全绿）
HERMES_TEST_DB_URL=postgresql+psycopg2://hermes:hermes@127.0.0.1:5432/hermes_test \
  ./.venv/bin/python -m pytest tests/
```

## 当前状态

- **文档体系：FROZEN**（冻结规范 + [Technical Specification TS-01~09](docs/ts09.md) + ADR×8 + data-contracts + Dashboard 设计，见 [agent.md](agent.md) 导航）
- **M0 Foundation：✅ 完成**（40 表迁移循环可重复、物理隔离验证）
- **M0.5 Data Feasibility Spike：✅ 完成**（数据源全实测，见 [provider-capability-report](docs/data-contracts/provider-capability-report.md)）
- **M1 Data Layer：✅ 完成**（152 测试全绿 + 真实数据演示，见 [M1 验收报告](docs/M1_acceptance_report.md)）
- **M1.5 Vertical Slice：🔄 施工中**（单资产闭环 + MCP 链路；设计输入见 [mcp-server-design](docs/mcp-server-design.md)）
- 下一步：M1.5 验收 → M2 Portfolio Core（前置：CASH/REVERSAL ADR）

> 快速上手：数据源凭证（TuShare/FRED）见 [provider-prep](docs/data-contracts/provider-prep.md)；备份脚本 `scripts/backup.sh`。
