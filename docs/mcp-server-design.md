# M1.5-01 MCP Server 集成方案（mcp-server-design）

> 状态：**设计定稿（2026-08-24，供施工会话直接执行）**
>
> 输入：TS-07（核心 28 工具/权限/错误码/包络）、ADR-006 D3（观察池 3 个业务扩展工具）、ts01 §1.6（统一响应包络）、TS-09 §1（Hermes Agent 客户端）、ADR-004 D3（认证预留）

---

## 1. 方案选择（调研结论）

| 方案 | 结论 | 理由 |
|---|---|---|
| **FastMCP（官方 MCP Python SDK）** | ✅ **采用** | 已并入 `modelcontextprotocol/python-sdk`（FastMCP 2.x 官方）；工具显式注册（白名单天然可控）；`stateless_http + json_response` 匹配无状态后端；认证（Bearer/OAuth 2.1）现成；in-memory 测试友好 |
| fastapi-mcp（tadata-org）| ❌ 拒绝 | 自动把全部 REST 端点转工具——无法满足"TS-07 核心 28 + ADR-006 业务扩展白名单 + ACCOUNT_WRITE 不进 MCP"的权限纪律；控制粒度差 |
| 手写 streamable-http | ❌ 拒绝 | 协议细节工作量大（JSON-RPC/session）；官方高层封装已成熟，无必要 |

**关键事实**：
- FastMCP 已并入官方 SDK：`from mcp.server.fastmcp import FastMCP`（非第三方依赖，符合最小依赖原则）；
- Hermes Agent 客户端（`hermes mcp add --url`）走标准 MCP 协议，**任何合规 server 都能连**；
- 已知挂载坑（issue #1367：路径 307/404）：**用 `path="/"` + `app.mount("/mcp", ...)` 规避**（FastMCP 官方文档的 mount 模式）。

## 2. 集成结构

```text
backend/app/mcp/
├── __init__.py
├── server.py          # FastMCP 实例 + 挂载（app.mount("/mcp", ...)）
├── tools/
│   ├── __init__.py    # 工具注册入口（register_all(mcp)）
│   ├── market.py      # 5 工具（ts07 §2.1）
│   ├── fundamental.py # 4 工具（§2.2）
│   ├── valuation.py   # 3 工具（§2.3）
│   ├── portfolio.py   # 5 工具（§2.4）
│   ├── research.py    # 4 工具（§2.5）
│   ├── thesis.py      # 4 工具（§2.6）
│   ├── briefing.py    # 2 工具（§2.7）
│   └── jobs.py        # 1 工具（§2.8）
├── envelope.py        # 统一响应包络（ts01 §1.6）
├── errors.py          # 18 错误码 → MCP tool error 映射（ts07 §4）
└── permission.py      # 工具→权限标注（READ/RESEARCH_WRITE/PROPOSAL_WRITE，ts07 §3）
```

```python
# server.py 骨架（施工会话按此实现）
from mcp.server.fastmcp import FastMCP
from app.main import app

mcp = FastMCP(
    "investment-backend",
    stateless_http=True,        # 无状态：Hermes 每次调用独立（不依赖会话粘性）
    json_response=True,         # 工具返回结构化 JSON
    # auth=...                  # ADR-004 D3 激活时启用（FastMCP 2.6 Blast Auth/Bearer）
)

from app.mcp.tools import register_all
register_all(mcp)

# 挂载（规避 #1367：path="/" + mount("/mcp")）
mcp_app = mcp.streamable_http_app()
app.mount("/mcp", mcp_app)
```

## 3. 工具注册模式（白名单纪律）

```python
# tools/market.py —— 每个工具 = 薄适配：校验 → service → 包络
from app.mcp.envelope import envelope
from app.mcp.errors import MCPToolError

@mcp.tool()
def get_portfolio(portfolio_id: str, as_of: str | None = None) -> dict:
    """获取组合快照（READ）。portfolio_id: UUID；as_of: ISO 日期（PIT）。"""
    # 1) 参数校验（pydantic 或手写）→ 非法 → MCPToolError(INVALID_PARAMS)
    # 2) 调 service（app.portfolio.service.PortfolioService）
    # 3) 包络组装：envelope(request_id, as_of, data, quality, provenance)
    ...
```

**纪律（架构测试断言）**：
- `tools/list` 返回的工具名集合 == TS-07 28 个唯一工具（逐名相等，无清单外工具）；
- 工具函数体内**禁止** SQL/表名/Provider symbol（薄适配层纯度）；
- 写工具（save_research_note/create_thesis_revision/...）标注 RESEARCH_WRITE，proposal 标 PROPOSAL_WRITE——标注仅文档化（权限裁决在 Backend service 层，双层保险）。

## 4. 统一响应包络（ts01 §1.6 冻结）

```json
{
  "request_id": "uuid",
  "as_of": "2026-08-23T10:30:00+08:00",
  "data": {},
  "quality": {"status": "VERIFIED", "score": 0.97, "flags": []},
  "provenance": [{"provenance_id": "uuid", "source": "", "provider": "",
                   "observed_at": "", "retrieved_at": "", "quality_score": 0.97}]
}
```

- `envelope.py` 提供组装函数（request_id 由工具内生成或复用调用方）；decision-sensitive 返回值必须携带 quality+provenance（ts01 验收）。

## 5. 错误映射（ts07 §4，18 错误码）

| MCP 语义 | HTTP 类比 | 示例码 |
|---|---|---|
| 工具返回 `isError: true` + 结构化错误 JSON | 4xx | DOMAIN_CONFLICT(409)/MISSING_VALUATION_INPUT(422)/NOT_FOUND/PERMISSION_DENIED/FRESHNESS_GATE/STALE_INPUT_REJECTED |
| 工具抛异常 → 捕获转错误 JSON | 5xx | ENGINE_INTERNAL_ERROR(500) |

```json
{"error": {"code": "MISSING_VALUATION_INPUT", "field": "wacc",
           "message": "...", "request_id": "uuid"}}
```

## 6. 测试策略（TS-08 CTR-MCP 组）

- **FastMCP in-memory 客户端**：`ClientSession(streamablehttp_client(mcp))` 或 FastMCP 内建测试（无网络、确定性）——工具注册白名单、包络字段、错误码、freshness 门禁（freezegun）全覆盖；
- 真实链路验证（M1.5-08）：`hermes mcp add investment-backend --url http://127.0.0.1:8000/mcp` → `hermes mcp test` → 对话中调用。

## 7. 依赖

```text
pip install "mcp[cli]"    # 官方 MCP Python SDK（含 FastMCP 2.x）
```

仅此一个新增依赖（协议官方库，符合最小依赖原则）。

## 8. 施工注意（避坑清单）

1. **挂载**：`mcp.streamable_http_app()` 用 `path="/"`，`app.mount("/mcp", mcp_app)`——不要给 http_app 传 "/mcp"（触发 307→404 已知问题 #1367）；
2. **lifespan**：`stateless_http=True` 时 session 管理简化；若需要 lifespan 合并，用 FastAPI lifespan 包裹 `mcp.session_manager.run()`；
3. **同步/异步**：工具函数可用同步（FastMCP 自动跑线程池）——service 层是同步 SQLAlchemy，保持同步最简单；
4. **JSON 序列化**：Decimal → str（ts03 §9.4 陷阱）；UUID → str；datetime → ISO8601；
5. **as_of 语义**：get 类工具支持 `as_of`（PIT，ts01）；不传时用当前时间；
6. **工具描述**：每个工具 description 写清"何时用、参数语义、返回结构"（Hermes 模型靠 description 选工具）。

## 9. 验收（M1.5-01 完成标准）

- [ ] `/v1/mcp` 端点可 initialize + tools/list（TS-07 核心 28 逐名匹配；ADR-006 3 个工具单独校验）
- [ ] 3 个代表性工具（get_price_history / run_valuation / get_portfolio）端到端返回包络
- [ ] 错误路径（缺参/404/权限）返回结构化错误
- [ ] in-memory 测试 + 架构测试（白名单断言）全绿
