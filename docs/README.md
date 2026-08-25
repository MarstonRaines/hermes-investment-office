# 文档导航与保留策略

本目录同时保存当前使用说明、架构契约和可追溯的研发记录。它们的用途不同；不要把历史设计
文档当作运行手册，也不要因为不参与运行就直接删除。

## 日常使用与验收

- [本地使用手册](本地使用手册.md)：启动、刷新、备份、排障和日常操作。
- [完整验收报告](full-acceptance-report.md)：当前产品形态的最终测试与运行态证据。
- [里程碑验收矩阵](milestone-acceptance-matrix.md)：M0–M7 的交付追踪。

## 产品与交互

- 根目录 [PRODUCT.md](../PRODUCT.md)：产品边界、目标用户和主工作流。
- 根目录 [DESIGN.md](../DESIGN.md)：Dashboard 设计系统与交互规则。
- [Dashboard 信息架构](dashboard-design-reference.md) 与
  [开源参考调研](dashboard-open-source-reference.md)。
- [ADR](ADR/)：不可随意反转的产品与架构裁决；ADR-009 至 ADR-011 是当前最终产品形态的
  直接依据。

## 架构与数据契约

- [后端架构冻结规范](Hermes_Investment_Office_后端架构冻结规范_v1.0_Consolidated.md)：
  权威架构边界。
- [Architecture Benchmark](Hermes_Investment_Office_Architecture_Benchmark_v1.0_Consolidated.md)：
  外部工程范式的审计记录。
- [技术规格 TS-01 至 TS-09](ts01.md)：领域模型、数据库、数据、引擎、MCP、测试与 Hermes
  集成的阶段性规格。它们是设计可追溯资料，不是日常运行说明。
- [data-contracts](data-contracts/)：Parquet、Provider 能力、单位归一化等稳定数据契约。

## 开发与历史记录

- [本地 M3 运行手册](m3-local-runbook.md)、[MCP Server 设计](mcp-server-design.md)：针对开发和
  验收的操作资料。
- [Codex 交接文档](handoff-to-codex.md)、[M1](M1_acceptance_report.md) 与
  [M1.5](M1_5_acceptance_report.md) 验收报告：历史交付证据，保留以便定位设计决策来源。

## 保留原则

- `README.md`、本地使用手册、产品/设计文档和 ADR 是当前优先阅读资料。
- TS、早期验收报告和交接文档默认保留为历史档案；若未来需要精简，应先移动到明确的
  `docs/archive/` 分区并更新引用，不能静默删除。
- `data/`、本机 `.env`、备份和会话数据不属于文档，也不应为了“清理仓库”而删除。
