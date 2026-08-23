# M1 Data Layer 验收报告

> 状态：**COMPLETED（2026-08-24）**
> 对应：冻结规范 §47 M1 / TS-04/05/08 §8.3 / ADR-005/006
> 验收 = fresh test 全绿 + 真实数据演示（scripts/m1_acceptance_demo.py）

---

## 1. ACC-M1-001~008 逐项验收

| ACC ID | 验收条目 | 判定 | 结果 |
|---|---|---|---|
| ACC-M1-001 | 可同步指定 A 股 | INT `market_sync_job` 端到端（provider→normalizer→market_bar_index+ohlcva/provenance 同事务） | ✅ `test_sync_jobs.py::test_market_sync_job_end_to_end`；演示 483 根真实茅台日线 |
| ACC-M1-002 | 可查询 OHLCVA | PG 指针→DuckDB→返回（ts04 §2.6.3）；GOLD-PIT-004 | ✅ `test_parquet_store` / `test_data_persistence::test_market_data_service_pg_pointer_duckdb`；演示 as_of 裁剪 |
| ACC-M1-003 | 可查询历史财务数据 | GOLD-PIT-001；CTR-QUD 系列 | ✅ `test_data_persistence::test_pit_query_visibility`；演示 PIT 查询 2025 年报 REVENUE=1720 亿（披露 2026-04-16） |
| ACC-M1-004 | 可追溯 Provider | CTR-PRV-006/007/008；INT provenance 反查 | ✅ `test_sync_jobs::test_fallback_writes_audit_and_flags`（fallback 记录+Envelope 落库）；演示 483 条血缘 raw 校验 483/483 |
| ACC-M1-005 | 支持 as_of | GOLD-PIT-001~004 | ✅ PIT 查询（published_at<=as_of，重述不回写历史）+ ohlcva as_of 裁剪 |
| ACC-M1-006 | 无 silent fallback | DQ-FBK-001/002/003；ARCH-DSC-002 | ✅ gateway 决策单点（换源只发生在 gateway）+ `test_no_silent_fallback.py` 静态扫描 + fallback 测试断言 FALLBACK_USED/质量衰减/audit 行 |
| ACC-M1-007 | 交易日历/汇率/复权因子可用 | INT `is_trading_day/next_trading_day`；GOLD-FX-004；CTR-PAR-005 | ✅ `test_calendar_service`；FX 双源交叉；演示 8797 行日历 + USDCNY 6.7118 + 复权一致性 True |
| ACC-M1-008 | Parquet schema 版本化 | CTR-PAR-001~004 | ✅ `test_parquet_store`（schema.json 三处一致、版本目录、列漂移检测） |

## 2. 交付物清单

| 层 | 模块 | 说明 |
|---|---|---|
| Provider | `providers/` | 六接口契约（TS-05 §2）+ 7 个实现类（tushare/akshare 分源/yahoo/fred/legulegu）+ ADR-005 网络三态 |
| 编排 | `providers/gateway.py` | fallback 决策 + 质量衰减 + PROVIDER_FALLBACK 审计双写 + 限流/退避 |
| 原始层 | `providers/raw_store.py` | 原样字节 + sha256 + 重解析校验 |
| 血缘 | `audit/service.py` | ProvenanceEnvelope 无损落库（§2.0.4）+ audit sink；AuditAction 扩展 PROVIDER_FALLBACK（迁移 f6e5d4c3b2a1） |
| 分析层 | `market_data/parquet.py` | ohlcva/v1 + schema.json 机器校验 + DuckDB 读取 |
| 服务 | `market_data` / `fundamentals` / `fx` / `calendar` / `corporate_actions` | PIT 查询、FX 双源、日历确定性接口、复权一致性 |
| Job | `jobs/sync_jobs.py` | market/fundamental sync（幂等指纹、增量 checkpoint、同事务写入、失败记录） |
| 配置 | `config/provider-capability.yaml` | Spike 回流权威源（S1-S9 实测，ADR-005/006） |
| 演示 | `scripts/m1_acceptance_demo.py` | 真实数据全链路（--fresh 重同步） |

## 3. 施工期发现与修复（ADR 级记录）

1. **TS-05 §5.2 vs ts02 §8.3 冲突**：`PROVIDER_FALLBACK` 不在 AuditAction 枚举 → 扩展枚举 + 迁移同步 CHECK（alembic check 零差异）；
2. **注册表唯一性约束**（§3.1"每个实现类 provider_name 唯一"）→ tushare 合并为单类多接口实现；
3. **gateway 实例装配缺口**：初版 `provider_cls()` 绕过 factory 配置注入 → 增加 `provider_factory` 注入（token/代理/symbol resolver 全链路生效）；
4. **ETF 行情口径**（S1）：tushare daily 对 ETF 返回空 → fund_daily（job 内先 daily 后 fund_daily）；
5. **TuShare dividend 实测可用**（2000 积分档）→ corporate_actions 真实行动类型（DIVIDEND/BONUS_SHARE），无需启发式。

## 4. 测试基线

- 单元 + 集成：**152 测试全绿**（含 45 个 M1.1 契约/网关 + 38 个 M1.2 provider + M1.4~M1.7 数据层/Job）；
- 架构：A4 三方一致（YAML↔注册表↔实现）、无静默 fallback 静态扫描、API/MCP 无 provider 接触面；
- 真实演示：483 bars / 20 财务事实 / 8797 日历行 / FX 双源 / 复权一致性 True。

## 5. 遗留与下一步

- M1.5+：cninfo FilingProvider 实现（FILINGS 域，PLANNED_NOT_IN_M1）；
- M1.5+：ETF 持仓/NAV 同步 job（etf_sync_job）与 QUOTA 人工入口（PARTIAL）；
- M1.5：MCP 链路（get_daily_context 等，M1.5 Vertical Slice）；
- 美股指数估值源（Shiller PE）spike 后 PENDING。
