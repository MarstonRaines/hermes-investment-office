# Dashboard

Dashboard 是只读的本地 Streamlit API 客户端，不连接 PostgreSQL、不读取
`data/`、不计算估值或组合指标。

```bash
cd /Users/blyadsuka/Developer/Investment_Agent
python3.12 -m venv .dashboard-venv
./.dashboard-venv/bin/pip install -r dashboard/requirements.txt
HERMES_BACKEND_URL=http://127.0.0.1:8000 \
  ./.dashboard-venv/bin/streamlit run dashboard/app.py \
  --server.address 127.0.0.1 --server.port 8501
```

Backend 未启动或接口返回错误时，页面会显示错误码，不会自行回退到本地数据。
