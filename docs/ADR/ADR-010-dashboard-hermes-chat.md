# ADR-010：Dashboard 内置 Hermes 研究会话

> 导航状态说明：本 ADR 的会话能力决策继续有效；第 1 条“增加第五项导航”及其旧入口数量已由 ADR-011 取代。当前一级导航固定为“今日、标的、组合、问 Hermes”。

- 状态：已接受
- 日期：2026-08-25
- 决策者：系统所有者

## 背景

原 Dashboard 只有“今日、观察池、持仓、回顾”四个入口，用户可以查看研究结果，却不能在同一工作台直接向 Hermes 提问。产品契约已经把 Hermes 定义为对话式控制平面，缺少对话入口会让“查看状态 → 深入研究 → 回到组合”的每日闭环中断。

用户明确要求在左侧增加“问 Hermes”，并允许研究持仓、观察池以及尚未录入系统的其他股票。系统仍不得连接券商或自动修改 REAL 账本。

## 决策

1. （历史决策，已由 ADR-011 取代）Dashboard 当时在旧四入口上增加第五项“问 Hermes”；当前实现将入口重组为“今日、标的、组合、问 Hermes”四项。
2. 页面使用 Streamlit 原生聊天界面，连接本机 Hermes Agent 的 WebSocket JSON-RPC 网关；不复制模型调用、工具编排、会话存储或推理逻辑。
3. Hermes Agent 继续绑定 `127.0.0.1:9119`。Docker 内的 Dashboard 只通过 `host.docker.internal` 建立传输连接，并把协议 Host 固定为 `127.0.0.1:9119`；客户端拒绝非回环协议 Host。
4. `./scripts/hermes start|restart|stop|status` 同步管理这项本地对话服务。服务不可用时页面明确显示恢复命令，不回退到其他模型或伪造回答。
5. 会话由 Hermes 自己持久化。Dashboard 只在浏览器会话中保存显示状态，并把 Hermes 持久会话编号写入本地 URL 查询参数以支持刷新恢复。
6. 系统内标的仍通过 Backend MCP 获取事实和确定性计算。系统外标的可以使用公开网络来源研究，但必须标注来源和时点，不得冒充 Backend 事实，也不得自动新增观察池、持仓、Thesis 或交易建议。
7. Dashboard 提供历史会话列表、恢复、中文 Markdown 导出和永久删除。列表、消息读取与删除只调用 Hermes 自带、带临时会话令牌的本机 Session API；Dashboard 不直接读取或改写 `~/.hermes/state.db`。删除需要二次明确操作，删除当前会话前先关闭活动连接。

## 不变边界

- Dashboard 的金融事实、组合状态、研究状态和人工账本写入仍只经过 Backend REST。
- Dashboard、Hermes 和 MCP 都不能访问 PostgreSQL 或数据卷。
- Hermes 无 `ACCOUNT_WRITE`，不能批准/完成建议、写 REAL 流水、连接券商、下单或划转资金。
- 关键数值仍由 Backend 确定性引擎计算；系统外缺少确定性结果时必须直说。

## 影响

- 原“Dashboard 只经 Backend REST”的表述收窄为“Dashboard 的业务事实与账本动作只经 Backend REST”；对话传输是唯一允许的本机 WebSocket 例外。
- Dashboard 显式依赖 Hermes Agent `0.20.x` 的稳定 `session.create`、`session.resume`、`session.close`、`prompt.submit`、流式事件协议，以及同版本本机会话列表、消息与删除 API。
- Hermes 更新后需要通过真实会话验收确认协议兼容；禁止静默改用一次性 CLI 子进程。
