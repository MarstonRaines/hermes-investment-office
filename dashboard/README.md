# Dashboard

Dashboard 是本地 Streamlit 客户端，不连接 PostgreSQL、不读取 `data/`、
不计算估值或组合指标。行情、组合和研究事实只经 Backend REST；持仓、现金、
流水更正以及交易建议审批通过本机 Backend 的人工 `ACCOUNT_WRITE` REST 入口完成。
“问 Hermes”单独连接本机 Hermes Agent 会话网关，模型调用、工具编排和会话持久化
均由 Hermes 负责。页面可列出、恢复、导出和永久删除由 Investment Office 创建的
Hermes 会话；Dashboard 仍不直接读取 `state.db`。

当前一级导航固定为“今日、标的、组合、问 Hermes”；ADR-011 已取代旧五入口导航。
观察池在桌面显示代码、名称、最新价、日涨跌、跟踪指数和行情日，窄屏只保留前四个
核心行情列。标的详情提供不复权日 K、MA5/MA20/MA30 与“来源”页。所有持久写入先
显示预览，再由用户独立确认；系统不连接券商，组合只是本地手工 REAL 账本。

```bash
cd hermes-investment-office
python3.12 -m venv .dashboard-venv
./.dashboard-venv/bin/pip install -r dashboard/requirements.txt
HERMES_BACKEND_URL=http://127.0.0.1:8000 \
HERMES_AGENT_URL=http://127.0.0.1:9119 \
HERMES_AGENT_HOST_HEADER=127.0.0.1:9119 \
HERMES_AGENT_CWD="$(pwd)" \
  ./.dashboard-venv/bin/streamlit run dashboard/app.py \
  --server.address 127.0.0.1 --server.port 8501
```

推荐直接运行 `./scripts/hermes start`，由脚本启动本机 Hermes 对话服务，并由 Docker
启动 Backend 与 Dashboard。
Backend 未启动或接口返回错误时，页面会显示错误码，不会自行回退到本地数据或示例数据。
