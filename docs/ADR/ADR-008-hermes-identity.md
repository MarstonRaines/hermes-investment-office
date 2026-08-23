# ADR-008：Hermes 身份澄清（角色名 vs 载体产品 Nous Hermes Agent）

> 状态：**Accepted（2026-08-23）**
>
> 类型：认知澄清（不修改任何冻结契约；确立后续文档读法约定）
>
> 关联：冻结规范 §5（Hermes 职责）、§31（Hermes Investment Profile）、TS-01~TS-08 全部 "Hermes" 表述

---

## 1. 背景：同名巧合

项目全部文档（冻结规范/TS 系列/ADR）中 "Hermes" 指**控制平面角色**（抽象概念：负责思考、编排、解释的 Investment Manager Agent）。用户 Mac 上实际安装了一个同名产品：

> **Hermes Agent**（Nous Research 开源，MIT License，`~/.hermes`，模型已配置 DeepSeek deepseek-v4-flash）
> 官网：https://hermes-agent.nousresearch.com/ · 仓库：https://github.com/NousResearch/hermes-agent

两者**恰好同名**，且产品能力与角色要求高度吻合（MCP 客户端 / 内置 cron / SKILL.md skills / 多平台 gateway）。经用户确认（2026-08-23）：

> **Hermes 控制平面角色的实现载体 = Nous Research 的 Hermes Agent 产品。**

## 2. 读法约定（后续所有文档/对话遵守）

| 表述 | 含义 |
|---|---|
| "Hermes"（无前缀）| 控制平面角色；在上下文明确实现时亦可指载体产品（Nous Hermes Agent）|
| "Hermes Agent" / "hermes-agent" / "~/.hermes" | 具体的 Nous Research 产品（载体）|
| "Hermes Profile" / "Investment Profile"（§31）| 载体上的投资配置（skills + MCP + cron + 模型）——落地为 Hermes Agent 的配置/skills/cron |
| "Hermes 侧" / "Hermes 的职责"（冻结规范 §5）| 载体（Hermes Agent）应实现的行为契约 |

## 3. 载体能力对照（事实，2026-08-23 本机核实）

| 角色要求（冻结规范/TS） | Hermes Agent 能力 | 状态 |
|---|---|---|
| MCP 客户端（§31.1：streamable-http → 127.0.0.1:8000/mcp）| ✅ 支持 Streamable HTTP / stdio / OAuth；`optional-mcps/` 为客户端目录 | 待注册 investment-backend |
| Cron（§31.1：标准 5-field；§5.2 边界）| ✅ 内置 cron scheduler（自然语言定时 + 无人值守）| 语法细节待确认（见 TS-09 调研）|
| Skills（SKILL.md 规范，§43 skills/ 目录）| ✅ agentskills.io 标准（SKILL.md）+ 自动学习循环 | 兼容 |
| 多平台对话（用户远程需求，ADR-004）| ✅ Telegram/Discord/Slack/WhatsApp/Signal gateway | 天然满足 |
| 跨会话记忆 | ✅ 内置学习循环 + 会话检索 + Honcho | 与 §4.1 边界兼容（记忆只存偏好摘要）|
| 模型路由（§34）| ✅ 任意模型可切换（`hermes model`）；当前 deepseek-v4-flash | 分档路由待 TS-09 设计 |

## 4. 受影响的既有表述（处置）

| 位置 | 原文 | 处置 |
|---|---|---|
| ts01 §验收基线末句 | "Hermes 的真正竞争资产不会是聊天框，也不会是一个**现成开源 Agent**" | **不修改**（研究报告）；正确读法：竞争资产是领域模型（Thesis/Ledger/Provenance 因果链），载体可以是开源 Agent——二者不矛盾；本 ADR 确立读法 |
| 冻结规范 §31.1 "Hermes Profile" | Investment Profile 概念 | 不修改；落地映射见 §3（载体配置 + skills + cron）|
| 冻结规范 §31.1 "cron 使用标准 5-field 表达式" | cron 语法要求 | 待 TS-09 调研确认 Hermes Agent cron 是否支持 5-field；若不支持 → ADR 修订此条 |
| 冻结规范 §33.2 "enabled_toolsets 限制"（日报 job 只给 web + mcp）| 工具集约束 | 待 TS-09 设计 Hermes Agent 侧等效机制（tool 白名单）|
| 本文档/agent.md | — | agent.md 已加入载体说明（见 §6）|

## 5. 架构图（最终认知）

```text
手机/其他设备（Telegram/Discord/... 或 Dashboard）
        │
        ▼
Hermes Agent（Nous Research，DeepSeek 模型）—— 控制平面载体
   ├── MCP 客户端 → http://127.0.0.1:8000/mcp → Investment Backend
   ├── 内置 cron → 日报/周报/季报任务
   ├── skills/（investment-runtime-policy 等，SKILL.md）
   └── 记忆/学习循环（只存偏好与摘要，§4.1）
```

## 6. 后续动作

1. agent.md 加入载体说明（已完成，见 agent.md §2/§12）；
2. TS-09 Hermes Integration Design 以本 ADR 为前提编写（载体 = Hermes Agent）；
3. TS-09 调研项：cron 语法（5-field 兼容性）、`hermes mcp add` 注册命令、模型路由分档、tool 白名单机制；
4. 若调研发现与冻结规范冲突（如 cron 非 5-field）→ 新增 ADR 修订，**不静默偏离**。

---

## 7. 载体能力调研结论（2026-08-23 本机 CLI 实测）

| 冻结规范要求 | Hermes Agent 实际能力 | 结论 |
|---|---|---|
| §31.1 MCP：HTTP transport → `http://127.0.0.1:8000/mcp` | `hermes mcp add <name> --url <url>`（Streamable HTTP/SSE）；`--auth {oauth,header}`；`hermes mcp list/test/configure` | ✅ **无冲突，直接匹配** |
| §31.1 Cron：**标准 5-field 表达式** | `hermes cron create '0 9 * * *' <prompt>`；支持自然语言（'30m'/'every 2h'）；`--deliver telegram/discord/signal`；`--skill`；`--script`/`--no-agent`（纯脚本）；`--monitor-script/--monitor-url`（**变化才触发 agent**）；`--model/--provider`（**任务级模型覆盖**）；`hermes cron list/edit/pause/resume/runs` | ✅ **完全满足 5-field** + 增强能力 |
| §34 Model Routing（task_class→model_profile）| `hermes model`（默认模型）；cron 任务级 `--model/--provider` 覆盖；`hermes fallback`（备用 provider）| ✅ 映射落于 cron 任务配置层 |
| §31 Skills（SKILL.md 规范）| SKILL.md（agentskills.io 标准）；`hermes skills install/search/inspect/audit`；自动学习循环 | ✅ 兼容 |
| §33.2 enabled_toolsets 限制（日报 job 只给 web+mcp）| cron 任务可附加 `--skill` 约束；tool 白名单机制见 `hermes tools`（TS-09 细化）| ⚠️ 待 TS-09 设计等效机制 |
| ADR-004 远程对话（手机）| `hermes gateway`（Telegram/Discord/Slack/WhatsApp/Signal）+ `hermes send`；`hermes gateway install` 装为用户服务 | ✅ 天然满足 |
| 交付渠道（§39 后端存储为主）| cron `--deliver` 可投递多平台（v0.1 仍以后端存储 + Dashboard 为主，deliver 作为可选增强）| 按冻结优先 |

**调研结论：Hermes Agent 与冻结规范 §31/§33/§34 无冲突，全部能力匹配或超出。TS-09 以本表为技术基线。**
