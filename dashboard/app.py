"""Read-only Streamlit dashboard.

The dashboard is deliberately an HTTP client.  It never imports backend
models, opens a database connection, reads the data volume, or recomputes an
investment metric.  Backend REST responses are rendered as received.
"""

from __future__ import annotations

import json
import os
from datetime import date
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import streamlit as st


DEFAULT_API = "http://127.0.0.1:8000"


def _api_base() -> str:
    return os.getenv("HERMES_BACKEND_URL", DEFAULT_API).rstrip("/")


def _get(path: str, **params) -> dict:
    query = urlencode({key: value for key, value in params.items() if value is not None})
    url = f"{_api_base()}{path}" + (f"?{query}" if query else "")
    try:
        with urlopen(Request(url, headers={"Accept": "application/json"}), timeout=10) as response:
            return json.load(response)
    except (HTTPError, URLError, TimeoutError) as exc:
        return {"error": {"code": "BACKEND_UNAVAILABLE", "message": str(exc)}}


def _show_response(label: str, payload: dict) -> None:
    st.subheader(label)
    if payload.get("error"):
        st.error(f"{payload['error'].get('code')}: {payload['error'].get('message')}")
    else:
        st.json(payload)


def _today() -> None:
    market_date = st.date_input("市场日期", value=date.today(), key="today_date")
    context = _get(f"/v1/briefing/contexts/{market_date.isoformat()}")
    _show_response("Freshness / Daily Context", context)
    briefs = _get(f"/v1/briefing/briefs/{market_date.isoformat()}")
    _show_response("Daily Brief", briefs)


def _portfolio() -> None:
    portfolios = _get("/v1/portfolios")
    _show_response("Portfolios", portfolios)
    items = portfolios.get("items", [])
    if not items:
        st.info("尚未创建组合。请通过 REST/MCP 的受控入口创建。")
        return
    labels = {item["name"]: item["portfolio_id"] for item in items}
    selected = st.selectbox("组合", list(labels))
    portfolio_id = labels[selected]
    _show_response("Portfolio", _get(f"/v1/portfolios/{portfolio_id}"))
    _show_response("Positions", _get(f"/v1/portfolios/{portfolio_id}/positions"))


def _research() -> None:
    query = st.text_input("研究搜索", key="research_query")
    if query.strip():
        _show_response("Research Search", _get("/v1/research/search", query=query))
    _show_response("Evidence", _get("/v1/research/evidence"))


def _thesis() -> None:
    thesis_id = st.text_input("Thesis ID", key="thesis_id")
    if thesis_id.strip():
        _show_response("Thesis / PIT Revision", _get(f"/v1/theses/{thesis_id}"))
        _show_response("Research Evidence", _get("/v1/research/evidence", thesis_revision_id=thesis_id))
    else:
        st.info("输入 Thesis ID 查看当前版本；历史版本由 as_of 查询参数驱动。")


def main() -> None:
    st.set_page_config(page_title="Hermes Investment Office", layout="wide")
    st.title("Hermes Investment Office")
    st.caption(f"Backend: {_api_base()} · Read-only API client")
    page = st.sidebar.radio("导航", ["今日", "持仓", "研究", "Thesis"])
    if page == "今日":
        _today()
    elif page == "持仓":
        _portfolio()
    elif page == "研究":
        _research()
    else:
        _thesis()


if __name__ == "__main__":
    main()
