# ADR-006：观察池（Watchlist）领域对象与每日研究范围

> 状态：**Accepted（2026-08-23）**
>
> 关联：冻结规范 §1（"每日自动收集持仓与观察池信息"）、§45（生命周期闭环第一步"添加观察池"）、§40.1（Dashboard RESEARCH 板块 Watchlist 视图）、TS-02/TS-07
>
> 类型：架构缺口补全（冻结规范已有概念，TS-01~08 未建模为领域对象）
>
> 背景：M1 Data Layer 正在施工，数据同步范围依赖本决策，立即固化。

---

## 1. 背景

用户使用场景（2026-08-23 明确）：

> 我有一个**标的池**（Watchlist），放着关注的标的（宽基指数 ETF / 其他 ETF / 个股）。
> 系统对池中标的进行**每日自动研究**；同时可以**单独对话研究池外股票**（临时研究，不进每日管道）。

架构现状核查：

| 出处 | 现状 |
|---|---|
| 冻结规范 §1 | "每日自动收集**持仓与观察池**信息" ✓ 概念存在 |
| 冻结规范 §45 | 生命周期第一步 = "**添加观察池**" ✓ 概念存在 |
| 冻结规范 §40.1 | Dashboard RESEARCH 板块规划 "**Watchlist** / Latest Research" ✓ 展示规划存在 |
| TS-01~TS-08 / ts02 40 张表 | **无 watchlist 领域对象** ❌ 缺口 |
| LangAlpha | 有 watchlists 表（chat-centric 参考，仅借鉴概念，不迁移代码）|

## 2. 决策

### D1：观察池 = 一级领域对象（新增 2 张表）

```sql
-- watchlists：观察池身份
CREATE TABLE watchlists (
    watchlist_id  UUID PRIMARY KEY,
    name          TEXT NOT NULL,               -- 如 "核心关注" / "消费" / "宽基"
    description   TEXT,
    status        TEXT NOT NULL DEFAULT 'ACTIVE' CHECK (status IN ('ACTIVE','ARCHIVED')),
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- watchlist_members：池内标的（时态成员关系）
CREATE TABLE watchlist_members (
    watchlist_member_id UUID PRIMARY KEY,
    watchlist_id   UUID NOT NULL REFERENCES watchlists,
    instrument_id  UUID NOT NULL REFERENCES instruments,
    added_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    removed_at     TIMESTAMPTZ NULL,           -- NULL = 在池；非空 = 已移出
    note           TEXT,
    UNIQUE (watchlist_id, instrument_id)       -- 同一池内不重复（历史移出记录另行审计）
);
```

- 归属模块：**instruments**（Instrument Master 域；观察池是标的的集合语义，不新建模块）
- 主键 UUID；成员关系可追踪（added_at/removed_at + audit_events）
- **单默认池约定（v0.1）**：系统默认一个 `ACTIVE` 池（如名 "默认观察池"）；支持多池（按主题分池），v0.1 不强约束

### D2：每日研究范围（Daily Pipeline 语义）

```text
每日同步/研究范围 = watchlist_members（未移除）∪ 真实持仓 instruments
```

- Backend Scheduler 的数据同步、Daily Context Builder、Attention 范围、Daily Brief 分析范围**全部以此集合为驱动**；
- **池外临时研究**：单独对话中 Hermes 请求 `sync_instrument`（按需同步该标的），不进每日管道；研究产出（Thesis/Notes）正常落库，标的可随时加入观察池；
- 空池行为：仅同步持仓标的，日报提示"观察池为空"（WARNING 级 Attention Item）。

### D3：MCP / REST 契约增补（研究组）

```text
MCP: get_watchlist            REST: GET    /v1/watchlists                 （默认池 + 成员）
MCP: add_watchlist_member     REST: POST   /v1/watchlists/{id}/members    （RESEARCH_WRITE）
MCP: remove_watchlist_member  REST: DELETE /v1/watchlists/{id}/members/{instrument_id}
```

- 权限：READ（get）/ RESEARCH_WRITE（add/remove，观察池是研究配置而非投资状态）；
- `get_market_snapshot` 的 `universe` 参数支持 `"WATCHLIST"` 语义（ts07 §2.1 已有 universe 字段，扩展取值）。

### D4：与现有对象的关系

- Watchlist **不拥有** Thesis/Research Workspace（引用不级联，同 ADR-004 精神）；
- 加入观察池 ≠ 创建 Thesis（两者独立；Thesis 可在池外标的建立）；
- 移除出池不删除任何历史（Thesis/数据/审计全保留）。

## 3. 影响

| 影响面 | 内容 |
|---|---|
| ts02/ts03 | 新增 2 张表（watchlists、watchlist_members）+ ORM + migration（40 → 42 表）|
| ts07 | Research 组工具增补 3 个；universe 枚举扩展 |
| TS-08 | 新增测试：成员时态、每日范围集合计算、空池行为、权限矩阵 |
| M1（施工中）| 同步范围逻辑按 D2 实现；instrument 同步 job 消费 watchlist 集合 |
| M6（日报）| Daily Context 的 attention 范围 = 每日范围 |
| M7（Dashboard）| Watchlist 视图（§40.1 已有规划）消费 get_watchlist API |
| 架构测试 | TABLE_OWNER 42 表、模块归属 instruments |

## 4. 待办（由施工会话执行）

1. ts02 增补表设计 + ts03 ORM（migration 002_xxx）
2. Instrument Master 服务扩展（watchlist 服务方法）
3. M1 同步 job 范围接入
4. ts07 工具契约增补（后续 TS 修订或施工记录回流）

## 5. 关联 ADR

- ADR-001（Cron 边界：每日管道由 Backend 驱动，观察池是其范围输入）
- ADR-004（远程化：观察池无远程特殊处理）
