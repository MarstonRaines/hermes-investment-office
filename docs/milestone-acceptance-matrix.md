# M0–M7 里程碑验收矩阵

状态基于当前工作树的实现、真实本地 PostgreSQL `hermes_test`、Docker 运行态和
`docs/ts08.md` 的 ACC 条目；历史提交说明不作为完成依据。契约冲突按
TS-04 > TS-02 > TS-01 > 冻结规范 > 旧文档处理。

| 里程碑 | 权威验收范围 | 当前判定 | 主要证据 |
|---|---|---:|---|
| M0 Foundation | ORM/迁移、Instrument Master、Provider Symbol、append-only/CHECK、启动与架构边界 | ✅ | `tests/architecture/`；`tests/unit/test_instruments.py`；Alembic upgrade/check/roundtrip；Docker `/healthz` |
| M0.5 Data Feasibility Spike | provider capability 三方一致、单位归一化、fallback 可见性、Attention 配置与 Parquet schema 版本目录 | ✅ | `test_provider_consistency.py`、`test_no_silent_fallback.py`、provider/gateway/normalizer 测试、`test_attention_engine.py`、`test_parquet_store.py` |
| M1 Data Layer | Gateway/限流/退避、raw evidence/provenance、PIT、OHLCVA + financial_history Parquet、PG pointer、日历/FX/复权、同步 job | ✅ | `test_gateway.py`、`test_sync_jobs.py`、`test_data_persistence.py`（含 financial_history/v1 Provider contract/PIT）、`test_parquet_store.py`、`test_calendar_service.py`、`test_fx_service.py`、`test_vertical_slice.py` |
| M1.5 Vertical Slice | Instrument→数据→财务→估值→Thesis→PAPER→Daily Brief；MCP StreamableHTTP 与 envelope | ✅ | `test_vertical_slice.py`、`test_valuation_service.py`、`test_thesis_service.py`、`test_briefing_service.py`、`test_mcp_server.py` |
| M2 Portfolio Core | Ledger replay、PAPER/REAL 隔离、人工 `ACCOUNT_WRITE`、proposal 状态机、REVERSAL、审计/provenance | ✅ | `test_portfolio_engine.py`、`test_portfolio_service.py`、`test_completion_contracts.py`、`test_core_user_paths_e2e.py`；M2 migration `f5a6b7c8d9e0` |
| M3 Investment Engines | DCF/DDM/Owner Earnings/Comparable/Scenario、风险、ETF/QDII 四日期与确定性计算 | ✅ | `test_valuation_engine.py`、`test_valuation_models.py`、`test_m3_etf_engine.py`、`test_m3_mcp_contract.py`、`test_attention_engine.py`、`test_scheduler.py` |
| M3-① ETF/契约收口 | freshness 由 `trading_calendar + freshness.yaml` 驱动；QDII `UNKNOWN` 保持 `WARNING`；Level 1 不泄露物理路径；holdings v1/v2 指针路由；三只 ETF DB-backed E2E | ✅ | `tests/integration/test_m3_etf_e2e.py`（510300/513650/512890）；`test_m3_storage_contract.py`；`test_briefing_service.py`；MCP/REST market tests |
| M4 Research Memory | Research Workspace/Note/Evidence、provenance、Thesis 不可变版本、PIT、状态/红旗事件 | ✅ | `test_thesis_service.py`、`test_core_user_paths_e2e.py`、`test_completion_architecture.py`、研究 REST/MCP adapters |
| M5 Hermes Integration | 28 个 TS-07 core + 3 个 ADR-006 工具、权限/错误码/freshness 门禁、REST、runtime policy skills | ✅ | `test_mcp_server.py`、`test_m3_mcp_contract.py`、`test_completion_contracts.py`、MCP 运行态 `tools/list` |
| M6 Automation | valuation→ETF→risk→anomaly→context 调度链、工作日 07:30 默认调度、非交易日跳过、job 幂等、Attention 唯一写入、红线触发 Thesis Review、Daily Context/Brief、freshness 状态转换与审计 | ✅ | `test_scheduler.py`（顺序/非交易日/valuation runner/red flag review）、`test_attention_engine.py`、`test_briefing_service.py`、`test_completion_contracts.py` |
| M7 Dashboard | “今日/标的/组合/问 Hermes”四入口；业务事实只经 Backend REST，对话只经本机 Hermes 网关；无 DB/Provider/Parquet/前端业务公式；手工 REAL 账本与全写入预览确认；移动观察池四列；日 K+MA5/20/30；来源页；Hermes 会话历史 | ✅ | `tests/architecture/test_completion_architecture.py`；`dashboard/app.py`；`dashboard/hermes_chat.py`；`dashboard/README.md`；`DESIGN.md`；REST DB-backed E2E；真实 Hermes 会话；最终桌面/移动截图；Impeccable finish reviewer disposition `ship`（无 material blocker） |

## 关键安全验收

- 主 compose 的 PostgreSQL 没有宿主机 `ports`；Backend 只绑定 `127.0.0.1:8000`。
- 开发 overlay 只为宿主机测试映射 `127.0.0.1:5432`，不改变主 compose 的隔离断言。
- `MCP_ALLOWED_TOOLS` 为 31 个，MCP `tools/list` 不包含 `ACCOUNT_WRITE`；自动路径只有
  proposal，不执行 Broker 下单、资金划转或 REAL transaction 写入。
- REAL transaction/reversal 需要本地人工 `ACCOUNT_WRITE` 入口；REST 无 header 返回
  403，MCP/Job/Engine 没有该权限。
- migration 不写业务种子；幂等 `bootstrap` 只创建产品默认 Instrument、三 ETF 观察池和
  空 REAL 手工组合，不创建或覆盖行情、持仓、现金和交易。
- 产品不连接券商；Hermes/MCP/Job/Engine 只能生成建议，不能自动登记成交或改变 REAL
  账本。
- Dashboard 的持久写入先展示预览，再由用户独立确认；组合仍是手工本地账本，不是券商账户镜像。
- 结构化数据源为 TuShare、AkShare 新浪/东方财富/同花顺、Yahoo Finance、FRED、乐咕乐股和新浪交易日历；标的“来源”页展示真实 provenance。
- REST/MCP 的 ETF Level 元数据只返回安全摘要、freshness 和 provenance，不返回
  `parquet_path` 或其他物理存储细节。

完整命令、输出和从零使用方式见 [full-acceptance-report.md](full-acceptance-report.md)。
