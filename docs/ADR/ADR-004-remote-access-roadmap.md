# ADR-004：远程访问与原生客户端演进路线（Remote Access & Native Client Roadmap）

> 状态：**Accepted（方向已定；v0.1 冻结实现不变）**
>
> 日期：2026-08-23
>
> 关联文档：《后端架构冻结规范 v1.0 Consolidated》（§4.4、§31.1、§33.3、§39、§40）、TS-07 MCP Contract（§1 传输与端点契约）、TS-01/TS-02/TS-03
>
> 类型：演进路线记录（不修改 v0.1 冻结项，仅激活预留与标记演进点）

---

## 1. 背景（Context）

系统 v0.1 按冻结规范部署为**本地单机**形态：所有服务绑定 `127.0.0.1`，MCP 无 Token，Dashboard 用 Streamlit，仅供本机使用。

用户的长期使用意图：

> 将运行 Hermes Investment Office 的 Mac 作为**私有投资服务器**：
>
> 1. 在异地（其他设备、手机）可以与 Agent 对话（研究、日报、Thesis、组合问答）；
> 2. 随时查看组合、日报、估值数据；
> 3. 展示层未来升级为 **macOS 原生 .app**（SwiftUI），替代/伴随 Streamlit。

## 2. 决策（Decision）

### D1：v0.1 保持本地单机形态，不做任何实现变更

- 冻结规范 §40（Streamlit Dashboard 绑定 127.0.0.1）、§33.3（localhost 无 Token）在 v0.1 施工与验收中**原样执行**；
- 本 ADR 不修改任何 TS-01~TS-08 已冻结的技术规范。

### D2：远程访问通道 = Tailscale 私有网络（冻结方向）

- Mac 与用户设备（手机/笔记本）加入同一 Tailscale overlay 网络；
- 远程访问的是 **Backend 的 REST/MCP HTTP 端口与 Hermes 运行时 Web 界面**，经 Tailscale 加密隧道直达；
- **禁止**公网端口暴露、禁止反向代理直通公网（v0.1 演进期）；
- 数据库仍保持 §4.4 物理隔离（PostgreSQL 仅 Backend 容器可达），Tailscale 只打通应用层 HTTP，不改变数据层边界；
- 依赖说明：Tailscale 为网络层工具（非代码依赖），符合项目最小依赖原则；如未来 Tailscale 不可用，可替换为 WireGuard 自建（等效拓扑）。

### D3：远程化 = 激活 §33.3 已冻结的认证预留，而不是发明新机制

当第一次从 Tailscale 网络外的设备访问（或 Tailscale 网络内出现多用户风险）时，按顺序激活：

```text
激活条件触发
    ↓
① Backend 认证层：API Token（Bearer）校验中间件，v0.1 预留关闭
    ↓
② Request Audit：audit_events.request_id 关联（TS-02 §8.3 已具备）
    ↓
③ 可选：按访问来源分级（LAN/Tailscale/公网）限制工具权限
```

- 认证层实现必须复用 TS-03 的配置体系（`settings`），不在业务代码中硬编码；
- MCP 工具权限矩阵（TS-07 §3）在认证激活后不变，仅增加"请求来源"维度。

### D4：原生客户端 .app = 展示层替换，后端零改动

- 目标范围：**macOS 优先**（SwiftUI，用户生态）；iOS/iPadOS 作为后续扩展（同一 SwiftUI 代码库）；
- .app 的角色与 Streamlit 相同：**只消费 Backend REST API，不触碰数据库、不重复实现业务逻辑**（冻结规范 §40 边界原样适用）；
- API 契约来源：**TS-07 每个 MCP 工具的 REST equivalent**（如 `GET /v1/portfolios/{id}/snapshot`、`GET /v1/theses/{id}`、`GET /v1/etfs/{instrument_id}/metrics`）——这是 .app 与后端的现成契约，无需重新设计 API；
- v0.1 的 `api/` 模块（TS-03 薄适配层）即为 .app 的 API 面，施工时按"未来会被原生客户端消费"的标准实现 REST 端点（完整 JSON 契约、分页、错误码，不依赖 Streamlit 特有的会话机制）。

### D5：配置化预留（文档级约定，施工时执行）

以下 v0.1 即按"可配置"实现，但默认值保持本地单机：

```yaml
# 配置层约定（TS-03 settings 体系）
backend:
  bind_host: 127.0.0.1        # 远程化时改为 0.0.0.0 或 tailscale IP（需 D3 认证激活后）
  bind_port: 8000
  base_url: http://127.0.0.1:8000   # Hermes 侧 MCP URL、.app 侧 API URL 统一引用此配置
  auth:
    enabled: false             # v0.1 关闭；远程化激活（D3）
    token_env: HERMES_BACKEND_TOKEN
dashboard:
  bind_host: 127.0.0.1
  api_base_url: ${backend.base_url}
```

- 禁止在代码/文档中把 `127.0.0.1` 作为唯一合法形态写入业务逻辑（允许作为默认值）；
- `base_url` 是 Hermes Investment Profile（§31.1）与未来 .app 的唯一接入点配置。

### D6：远程对话入口

- "异地与 Agent 对话" = 用户设备经 Tailscale 访问 Mac 上 **Hermes 运行时的 Web 界面**（对话入口）与 Backend REST/MCP（数据入口）；
- 对话通道的安全模型与 D2/D3 一致（Tailscale 加密 + 认证层）；
- 主动通知（日报推送）仍按冻结规范 §39：v0.1 不做，未来作为 Provider 扩展（ADR 后实施）。

## 3. 影响与不变量（Consequences）

### 不修改的冻结项

| 冻结项 | 状态 | 说明 |
|---|---|---|
| §4.4 物理隔离 | 不变 | 远程只到应用层；数据库永不暴露 |
| §40 Streamlit v0.1 展示层 | 不变 | .app 是未来替换/并存形态 |
| §33.1 五级权限矩阵 | 不变 | 认证激活只增加"来源"维度 |
| §39 推送通道 | 不变 | 仍为未来 Provider 扩展 |
| TS-01~TS-08 全部技术规范 | 不变 | 本 ADR 不触发任何 TS 修订 |

### 新引入的演进点（标记，不实现）

1. **认证中间件插槽**（D3）：api/ 层预留依赖注入点，v0.1 为 no-op；
2. **配置化绑定**（D5）：`bind_host / base_url / auth.enabled` 进入 settings；
3. **REST API 客户端友好性**（D4）：api/ 施工标准 = 完整 JSON 契约（无 Streamlit 会话依赖）；
4. **Tailscale 网络规划**：设备组、Mac 的 Tailscale IP 记录（运维文档，不属代码）。

### 风险

| 风险 | 缓解 |
|---|---|
| Tailscale 服务可用性 | 拓扑等价替换 WireGuard；不把 Tailscale 写进代码 |
| 认证层实现质量（激活时） | 激活前单独立项评审；Token 只经环境变量；Request Audit 强制 |
| 手机端 UX 不确定 | .app macOS 优先，iOS 延后；Web 界面（Tailscale 内）先行验证 |
| Streamlit 与 .app 并存期维护成本 | 明确 .app 只是另一个 Backend API 客户端，无共享代码负担 |

## 4. 迁移路径（v0.1 → 远程化）

```text
阶段 0（当前）：v0.1 本地单机施工（Streamlit + 127.0.0.1）
    │
阶段 1：Tailscale 接入（Mac + 个人设备）
    │       ├── 配置化 bind_host/base_url（D5 默认值已就绪）
    │       └── 远程访问 Hermes Web 界面（对话）—— 仅 Tailscale 内，认证层仍关闭
    │
阶段 2：认证层激活（D3）
    │       └── API Token + Request Audit + 来源分级（需单独立项评审）
    │
阶段 3：macOS .app（D4）
    │       └── 消费 TS-07 REST Contract；与 Streamlit 并存或替换（另行 ADR 决策）
    │
阶段 4（可选）：iOS/iPadOS 扩展；推送 Provider（§39）
```

每阶段独立验收；阶段 2 前不暴露任何公网端口。

## 5. 相关 ADR

- ADR-001-cron-boundary、ADR-002-provider-strategy、ADR-003-qdii-etf-scope（既有）
- 本 ADR 激活条件触发时（阶段 2 立项）应新增实施级 ADR，引用本文件
