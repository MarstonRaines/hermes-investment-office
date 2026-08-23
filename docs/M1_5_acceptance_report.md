# M1.5 Vertical Slice 验收报告

> 状态：**COMPLETED（2026-08-24）**
> 对应：冻结规范 §47 M1.5 / ts06 §3/§5 / ts07 / ts08 §8.4 / TS-09
> 验收 = fresh test 全绿 + 真实数据演示（scripts/m1_5_acceptance_demo.py）

---

## 1. ACC-M1.5-001~004 逐项验收

| ACC ID | 验收条目 | 判定 | 结果 |
|---|---|---|---|
| ACC-M1.5-001 | 一个真实标的走完全流程 | INT `vertical_slice` 端到端（真实 pipeline + mock provider）；GOLD-VAL-001；GOLD-PRT-009 | ✅ `test_vertical_slice.py`：Instrument→Data(483)→Fundamental→Valuation→Thesis→PAPER→Brief 全闭环；演示真实茅台全链路 |
| ACC-M1.5-002 | MCP 链路打通 | INT：MCP 端点 tools/list + resolve_instrument/get_daily_context/get_job_status；ARCH-MCP-001/004 | ✅ `test_mcp_server.py` JSON-RPC 全链路；演示 4 次 MCP 调用全部 200；白名单 8 子集 ⊆ 冻结 28、无清单外工具 |
| ACC-M1.5-003 | 估值可复现 | GOLD-VAL-001/010；ARCH-GLD-003 | ✅ 黄金值文件 `tests/golden/valuation_golden.json`（输入表+as_of+期望输出）；BLOCKED_MISSING_INPUT 绝不补默认 |
| ACC-M1.5-004 | Paper Portfolio 闭环 | GOLD-PRT-001/003/009；STM-TRD-005 | ✅ 引擎黄金值 + PAPER 自动路径（Hermes 模拟交易）+ 快照 NAV=现金+市值 |

## 2. 交付物清单

| 层 | 模块 | 说明 |
|---|---|---|
| Valuation | `valuation/engine|service|errors` | DCF 三情景（FCFE 口径 v0.1 声明）+ 客观层 PE/PB + 终值双方法交叉验证 + 状态机 + 原子落库 |
| Thesis | `thesis/service` | lifecycle/health 正交状态机 + red flag + revision 单调 + get_thesis(as_of) PIT |
| Portfolio | `portfolio/engine|service` | fold replay + 加权平均成本 + 快照 upsert + PAPER 模拟路径 |
| Briefing | `briefing/service` | daily_context（freshness 判定）+ daily_brief（model_profile 必填） |
| MCP | `mcp/server` + `bootstrap`(app 级) | StreamableHTTP /mcp + 8 工具 + 包络 + 白名单 |
| 迁移 | `a7b8c9d0e1f2` / `b1c2d3e4f5a6` | valuation_runs 状态机守卫 / 快照 upsert-by-supersede 守卫 |
| 测试 | 173 全绿（+黄金值文件） | GOLD-VAL/PRT、STM-THS/VAL/TRD、ARCH-MCP/GLD、vertical_slice 端到端 |

## 3. 施工期发现与修复（契约冲突，已按冻结流程处理）

1. **valuation_runs append-only 触发器 vs 状态机**（ts01 §4.2）：M0 严格触发器阻止
   status 迁移 → 迁移 a7b8c9d0e1f2 改为状态机守卫（E5 语义：COMPLETED 后仅
   SUPERSEDED，冻结列不变；生命周期内允许迁移与回填）；
2. **快照表 upsert-by-supersede**（ts06 §5.7.1）：严格触发器阻止同日期重跑 →
   迁移 b1c2d3e4f5a6 键不变守卫（同键可替换、跨日期/删除拒绝）；
3. **SHARES_OUTSTANDING 实测缺口**：TuShare fina_indicator 无股本列（实测确认）→
   改用 daily_basic.total_share（万股 ×10000），2000 积分档实测可用；
4. **ts06 §5.3.1 示例算术笔误**：11.3367 对应买价 14（(10000+7000+5)/1500），
   公式语义以黄金值锁定为准（测试注释记录）；
5. **MCP SDK 装配**：mcp 2.0 无 fastmcp；Starlette 挂载不传播子 app lifespan
   （任务组初始化）→ 根 lifespan 手动进入；挂载路径 /mcp + 子路由 "/" 避免双路径；
   transport_security Host 校验要求带端口（127.0.0.1:* 模式）；
6. **ARCH-DEP 分层**：装配层（registry/factory/gateway 接线）移出 mcp/ → app/bootstrap.py
   （mcp 禁止 import providers.* 静态扫描全绿）。

## 4. 测试基线（173 全绿）

- Valuation：GOLD-VAL-001（黄金值文件驱动）/009/010/011/013、交叉验证 FAIL、
  STM-VAL、服务集成（含 CN_ETF 拒绝、触发器不可变）；
- Thesis：STM-THS-001~009、GOLD-PIT-002（PIT 版本）；
- Portfolio：GOLD-PRT-001/002/003/009、fold 顺序、确定性、PAPER/REAL 边界、as_of replay；
- Briefing：freshness 三态、幂等、model_profile 必填；
- MCP：白名单、包络五要素、JSON-RPC 全链路、未知工具拒绝；
- 架构：A4 三方一致、无静默 fallback 静态扫描（mcp 无 providers import）。

## 5. 演示结果（scripts/m1_5_acceptance_demo.py，真实茅台数据）

```
[3] Valuation COMPLETED: base=8.62e12 bear=5.17e12 bull=1.85e13（显式演示假设）
[4] Thesis ACTIVE
[5] PAPER 快照: NAV=1,000,000 现金=872,717 市值=127,283（100 股 @1272.83）
[6] Daily Context + Brief（model_profile=fast）
[7] MCP: tools/list(8) / resolve_instrument(600519) / get_daily_context /
    get_job_status(SUCCEEDED) —— 全部 200 OK
```

## 6. 遗留与下一步

- M1.5 剩余：MCP 全量 28 工具（M5）；ETF Engine（M3）；Valuation 其余模型
  （DDM/OWNER_EARNINGS/COMPARABLE/SCENARIO，M3）；cninfo FilingProvider（M1.5+）；
- 演示注：Valuation 假设为演示档（FCF 3000 亿/年 × 20x 终值 → 高估），
  真实估值需 Thesis 驱动假设（M4 复核流程）；
- freshness=STALE 的演示现象来自幂等复用旧 context（日历同步前构建），
  逻辑本身已由单测覆盖。
