"""Hermes Investment Office 本地操作台。

Dashboard 的业务事实只通过 Backend REST API 读取与写入；不连接数据库、不读取
数据卷，也不在前端重算投资指标。“问 Hermes”单独连接本机 Hermes Agent 会话网关。
所有手工账本写入仍经过 Backend 的 ACCOUNT_WRITE 边界。
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from html import escape
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import altair as alt
import streamlit as st
from hermes_chat import (
    HermesChatClient,
    HermesChatError,
    check_agent_health,
    render_transcript_markdown,
)

DEFAULT_API = "http://127.0.0.1:8000"
DEFAULT_HERMES_AGENT = "http://127.0.0.1:9119"
WRITE_HEADERS = {"X-Account-Write": "ACCOUNT_WRITE"}


def _local_today() -> date:
    return datetime.now().astimezone().date()


st.set_page_config(
    page_title="Hermes Investment Office",
    page_icon="H",
    layout="wide",
    initial_sidebar_state="expanded",
)

# 第一段可见正文必须是设计契约；它不会绘制任何界面元素。
st.markdown(
    """<!--
THESIS: 一眼看清今天是否可行动，并从同一界面完成手工投资账本和 Hermes 研究对话；拒绝原始 JSON 与研究工具堆叠。
OWN-WORLD: 冷白纸面、克制蓝绿信号、细描边金融卡片、统一线性图标和高密度中文表格。
STORY: 用户先处理今日行动，再从标的中心完成数据补齐、研究与估值，组合只承载手工账本；任何标的都可带上下文交给 Hermes。
FIRST VIEWPORT: 左侧今日、标的、组合、问 Hermes 四项导航；首页先呈现行动与数据状态，历史事实只在局部历史模式中查看。
FORM: 信息密度型运营驾驶舱；mode=operate；direction=canon；seed key c375a27a。
FINISH: unreviewed and undocumented is unfinished; this build ends with the finish review, the verdict, and DESIGN.md
-->""",
    unsafe_allow_html=True,
)

_styles = Path(__file__).with_name("styles.css")
if _styles.exists():
    st.markdown(f"<style>{_styles.read_text(encoding='utf-8')}</style>", unsafe_allow_html=True)


@dataclass(frozen=True)
class ApiResult:
    ok: bool
    data: Any = None
    status: int = 0
    code: str = ""
    message: str = ""


def _api_base() -> str:
    return os.getenv("HERMES_BACKEND_URL", DEFAULT_API).rstrip("/")


def _agent_base() -> str:
    return os.getenv("HERMES_AGENT_URL", DEFAULT_HERMES_AGENT).rstrip("/")


def _agent_host_header() -> str:
    return os.getenv("HERMES_AGENT_HOST_HEADER", "127.0.0.1:9119")


def _agent_cwd() -> str:
    return os.getenv("HERMES_AGENT_CWD", "")


def _request(
    method: str,
    path: str,
    *,
    params: dict[str, Any] | None = None,
    payload: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
    timeout: float = 20,
) -> ApiResult:
    query = urlencode({key: value for key, value in (params or {}).items() if value is not None})
    url = f"{_api_base()}{path}" + (f"?{query}" if query else "")
    request_headers = {"Accept": "application/json", **(headers or {})}
    body = None
    if payload is not None:
        body = json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8")
        request_headers["Content-Type"] = "application/json"
    try:
        with urlopen(
            Request(url, data=body, headers=request_headers, method=method),
            timeout=timeout,
        ) as response:
            raw = response.read()
            return ApiResult(
                True,
                json.loads(raw.decode("utf-8")) if raw else {},
                response.status,
            )
    except HTTPError as exc:
        try:
            error_payload = json.loads(exc.read().decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            error_payload = {}
        detail = error_payload.get("detail") or error_payload.get("message") or str(exc)
        if isinstance(detail, list):
            detail = "；".join(str(item.get("msg", item)) for item in detail)
        return ApiResult(
            False,
            error_payload,
            exc.code,
            "NOT_FOUND" if exc.code == 404 else "API_ERROR",
            str(detail),
        )
    except (URLError, TimeoutError, OSError) as exc:
        return ApiResult(False, status=0, code="BACKEND_UNAVAILABLE", message=str(exc))


def _get(path: str, **params: Any) -> ApiResult:
    return _request("GET", path, params=params)


def _write(
    method: str,
    path: str,
    *,
    payload: dict[str, Any] | None = None,
    params: dict[str, Any] | None = None,
    account_write: bool = False,
    timeout: float = 30,
) -> ApiResult:
    return _request(
        method,
        path,
        payload=payload,
        params=params,
        headers=WRITE_HEADERS if account_write else None,
        timeout=timeout,
    )


def _number(value: Any) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _money(value: Any, *, signed: bool = False) -> str:
    number = _number(value)
    if number is None:
        return "—"
    sign = "+" if signed and number > 0 else ""
    return f"{sign}¥{number:,.2f}"


def _percent(value: Any, *, ratio: bool = True, signed: bool = False) -> str:
    number = _number(value)
    if number is None:
        return "—"
    if ratio:
        number *= 100
    sign = "+" if signed and number > 0 else ""
    return f"{sign}{number:.2f}%"


def _plain_number(value: Any, decimals: int = 3) -> str:
    number = _number(value)
    return "—" if number is None else f"{number:,.{decimals}f}"


def _tone(value: Any) -> str:
    number = _number(value)
    if number is None or number == 0:
        return "neutral"
    return "positive" if number > 0 else "negative"


def _icon(name: str) -> str:
    icons = {
        "nav": '<svg viewBox="0 0 24 24"><path d="M3 17l5-5 4 3 7-9 2 2"/></svg>',
        "cash": '<svg viewBox="0 0 24 24"><path d="M3 9h18M5 9v9m4-9v9m6-9v9m4-9v9M3 20h18M4 6l8-3 8 3z"/></svg>',
        "pnl": '<svg viewBox="0 0 24 24"><path d="M4 20V10m5 10V5m5 15v-8m5 8V3"/></svg>',
        "target": '<svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="7"/><circle cx="12" cy="12" r="2"/><path d="M12 2v3m0 14v3M2 12h3m14 0h3"/></svg>',
        "risk": '<svg viewBox="0 0 24 24"><path d="M12 3l7 3v5c0 5-3 8-7 10-4-2-7-5-7-10V6z"/><path d="M9 12l2 2 4-5"/></svg>',
    }
    return icons.get(name, "")


def _metric_card(
    title: str,
    value: str,
    *,
    change: str = "",
    detail: str = "",
    tone: str = "neutral",
    icon: str = "nav",
) -> None:
    st.markdown(
        f"""
        <div class="metric-card">
          <div class="metric-title">{escape(title)}</div>
          <div class="metric-main {tone}">{escape(value)}</div>
          <div class="metric-foot"><span class="{tone}">{escape(change)}</span><span>{escape(detail)}</span></div>
          <div class="metric-icon">{_icon(icon)}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def _page_header(title: str, subtitle: str, *, updated_at: str | None = None) -> None:
    left, right = st.columns([5, 1.2], vertical_alignment="center")
    with left:
        st.markdown(
            f'<div class="page-heading"><h1>{escape(title)}</h1><p>{escape(subtitle)}</p></div>',
            unsafe_allow_html=True,
        )
    with right:
        if updated_at:
            st.caption(f"数据更新 · {updated_at.replace('T', ' ')[:16]}")
        if st.button("刷新", icon=":material/refresh:", width="stretch"):
            st.rerun()


def _section_heading(title: str, subtitle: str = "") -> None:
    st.markdown(
        f'<div class="section-heading"><h3>{escape(title)}</h3><span>{escape(subtitle)}</span></div>',
        unsafe_allow_html=True,
    )


def _error_state(result: ApiResult, *, title: str = "暂时无法读取数据") -> None:
    if result.code == "BACKEND_UNAVAILABLE":
        hint = "Backend 尚未就绪。请运行 ./scripts/hermes status，或稍后刷新。"
    elif result.status == 404:
        hint = "对应记录尚未创建，或所选日期之前没有可见版本。"
    else:
        hint = result.message or "请求未完成，请在系统状态中查看最近任务。"
    st.markdown(
        f"""
        <div class="state-card state-error">
          <strong>{escape(title)}</strong>
          <span>{escape(hint)}</span>
          <code>{escape(result.code or str(result.status))}</code>
        </div>
        """,
        unsafe_allow_html=True,
    )


def _empty_state(title: str, detail: str, *, compact: bool = False) -> None:
    css = " compact" if compact else ""
    st.markdown(
        f'<div class="state-card state-empty{css}"><strong>{escape(title)}</strong><span>{escape(detail)}</span></div>',
        unsafe_allow_html=True,
    )


def _freshness_banner(freshness: dict[str, Any] | None) -> None:
    freshness = freshness or {"overall": "FAILED", "domains": {}}
    overall = str(freshness.get("overall", "FAILED"))
    domains = freshness.get("domains") or {}
    affected = [name for name, value in domains.items() if (value or {}).get("status") != "OK"]
    if overall == "OK":
        text = "行情与研究上下文通过新鲜度检查，决策敏感功能可用。"
    else:
        labels = {
            "market": "行情",
            "fundamental": "财务",
            "etf_nav": "基金净值",
            "etf_holdings": "ETF持仓",
            "index": "指数",
            "fx": "汇率",
            "quota": "QDII额度",
            "daily_context": "每日上下文",
        }
        names = "、".join(labels.get(item, item) for item in affected) or "每日上下文"
        text = f"{names}存在缺口或过期；系统不会用示例值补齐，交易建议等敏感写入保持关闭。"
    st.markdown(
        f'<div class="freshness freshness-{overall.lower()}" role="status"><span class="status-dot"></span><strong>{escape(overall)}</strong><span>{escape(text)}</span></div>',
        unsafe_allow_html=True,
    )


def _line_chart(
    history: list[dict[str, Any]], *, key: str, height: int = 240,
) -> None:
    rows = [
        {"日期": item.get("date") or item.get("trade_date"), "数值": _number(item.get("nav_cny") or item.get("close"))}
        for item in history
    ]
    rows = [row for row in rows if row["日期"] and row["数值"] is not None]
    if len(rows) < 2:
        _empty_state("历史证据不足", "录入账本并完成至少两次快照后，这里会显示真实净值走势。")
        return
    base = alt.Chart(alt.Data(values=rows)).encode(
        x=alt.X("日期:T", title=None, axis=alt.Axis(format="%m-%d", grid=False)),
        y=alt.Y("数值:Q", title=None, scale=alt.Scale(zero=False)),
        tooltip=[alt.Tooltip("日期:T", format="%Y-%m-%d"), alt.Tooltip("数值:Q", format=",.2f")],
    )
    area = base.mark_area(
        line=False,
        color="#0b6ffb",
        opacity=0.08,
    )
    line = base.mark_line(color="#0b6ffb", strokeWidth=2.2, interpolate="monotone")
    # Streamlit 会复用同一位置的非交互 Vega View。把业务身份写进 spec，
    # 切换标的或查询日期时让前端重建图层，而不是沿用上一组数据。
    chart = (area + line).properties(
        height=height,
        name=key.replace("-", "_"),
    )
    st.altair_chart(
        chart,
        width="stretch",
    )


def _candlestick_chart(
    history: list[dict[str, Any]], *, key: str, height: int,
) -> None:
    rows = []
    for item in history:
        values = {
            "开盘": _number(item.get("open")),
            "最高": _number(item.get("high")),
            "最低": _number(item.get("low")),
            "收盘": _number(item.get("close")),
        }
        if not item.get("trade_date") or any(value is None for value in values.values()):
            continue
        rows.append({
            "日期": item["trade_date"],
            **values,
            "MA5": _number(item.get("ma5")),
            "MA20": _number(item.get("ma20")),
            "MA30": _number(item.get("ma30")),
        })
    if len(rows) < 2:
        _empty_state("K 线证据不足", "至少需要两个交易日的完整开高低收行情。")
        return

    data = alt.Data(values=rows)
    x = alt.X("日期:T", title=None, axis=alt.Axis(format="%m-%d", grid=False))
    tooltip = [
        alt.Tooltip("日期:T", title="日期", format="%Y-%m-%d"),
        alt.Tooltip("开盘:Q", format=".3f"),
        alt.Tooltip("最高:Q", format=".3f"),
        alt.Tooltip("最低:Q", format=".3f"),
        alt.Tooltip("收盘:Q", format=".3f"),
        alt.Tooltip("MA5:Q", format=".3f"),
        alt.Tooltip("MA20:Q", format=".3f"),
        alt.Tooltip("MA30:Q", format=".3f"),
    ]
    direction = alt.condition(
        "datum.收盘 >= datum.开盘",
        alt.value("#e5484d"),
        alt.value("#0aa66d"),
    )
    base = alt.Chart(data).encode(x=x, tooltip=tooltip)
    wick = base.mark_rule(strokeWidth=1).encode(
        y=alt.Y("最低:Q", title=None, scale=alt.Scale(zero=False)),
        y2=alt.Y2("最高:Q"),
        color=direction,
    )
    body = base.mark_bar(size=4).encode(
        y=alt.Y("开盘:Q", title=None, scale=alt.Scale(zero=False)),
        y2=alt.Y2("收盘:Q"),
        color=direction,
    )
    averages = base.transform_fold(
        ["MA5", "MA20", "MA30"], as_=["均线", "均线值"],
    ).mark_line(strokeWidth=1.6).encode(
        y=alt.Y("均线值:Q", title=None, scale=alt.Scale(zero=False)),
        color=alt.Color(
            "均线:N",
            title=None,
            scale=alt.Scale(
                domain=["MA5", "MA20", "MA30"],
                range=["#0b6ffb", "#dc8b13", "#7c68ee"],
            ),
            legend=alt.Legend(orient="top", direction="horizontal"),
        ),
    )
    chart = alt.layer(wick, body, averages).resolve_scale(
        color="independent",
    ).properties(
        height=height,
        name=key.replace("-", "_"),
    )
    st.caption("日 K · 不复权 · 红涨绿跌")
    st.altair_chart(chart, width="stretch")


def _donut(
    values: dict[str, Any],
    *,
    center_label: str,
    center_value: str,
    height: int = 240,
) -> None:
    labels = {
        "CN_ETF": "ETF",
        "CN_EQUITY": "股票",
        "CASH": "现金",
        "LOW": "低风险",
        "MEDIUM": "中风险",
        "HIGH": "高风险",
        "UNAVAILABLE": "待评估",
    }
    rows = []
    for key, value in values.items():
        number = _number(value)
        if number is not None and number > 0:
            rows.append({"类别": labels.get(str(key), str(key)), "占比": number})
    if not rows:
        _empty_state("尚无可绘制数据", "完成持仓录入与风险快照后自动生成。")
        return
    chart = alt.Chart(alt.Data(values=rows)).mark_arc(innerRadius=54, outerRadius=82).encode(
        theta=alt.Theta("占比:Q"),
        color=alt.Color(
            "类别:N",
            scale=alt.Scale(range=["#0b6ffb", "#28b779", "#ff9f1c", "#7c68ee", "#ef476f"]),
            legend=alt.Legend(title=None, orient="bottom", columns=2),
        ),
        tooltip=[alt.Tooltip("类别:N"), alt.Tooltip("占比:Q", format=".2%")],
    ).properties(height=height)
    text_data = alt.Data(values=[{"label": center_label, "value": center_value}])
    center = alt.Chart(text_data).mark_text(
        align="center",
        baseline="middle",
        dy=-7,
        fontSize=12,
        color="#64748b",
    ).encode(text="label:N")
    center_value_chart = alt.Chart(text_data).mark_text(
        align="center",
        baseline="middle",
        dy=11,
        fontSize=16,
        fontWeight=600,
        color="#172033",
    ).encode(text="value:N")
    st.altair_chart(chart + center + center_value_chart, use_container_width=True)


def _watchlist_table(items: list[dict[str, Any]]) -> None:
    if not items:
        _empty_state("观察池为空", "前往“标的”页添加证券。", compact=True)
        return
    rows = []
    for item in items:
        latest = item.get("latest") or {}
        change = _number(latest.get("pct_change"))
        tone = _tone(change)
        rows.append(
            "<tr>"
            f"<td><strong>{escape(str(item.get('symbol', '—')))}</strong></td>"
            f"<td>{escape(str(item.get('name', '—')))}</td>"
            f"<td>{escape(_plain_number(latest.get('close')))}</td>"
            f"<td class='{tone}'>{escape(_percent(change, ratio=False, signed=True))}</td>"
            f"<td>{escape(str(item.get('tracking_index') or '—'))}</td>"
            f"<td>{escape(str(latest.get('trade_date') or '—'))}</td>"
            "</tr>"
        )
    st.markdown(
        """
        <div class="table-wrap"><table class="office-table">
        <thead><tr><th>代码</th><th>名称</th><th>最新价</th><th>日涨跌</th><th>跟踪指数</th><th>行情日</th></tr></thead>
        <tbody>""" + "".join(rows) + """</tbody></table></div>
        <p class="mobile-table-note">窄屏优先显示核心行情；跟踪指数与行情日可在下方标的详情中查看。</p>""",
        unsafe_allow_html=True,
    )


def _attention_list(items: list[dict[str, Any]]) -> None:
    if not items:
        _empty_state("当前无待处理事项", "系统会把数据缺口、异常与待审批建议集中到这里。", compact=True)
        return
    rendered = []
    for item in items[:6]:
        severity = str(item.get("severity") or "INFO")
        rendered.append(
            f'<div class="attention-row"><span class="severity severity-{escape(severity.lower())}">{escape(severity)}</span>'
            f'<div><strong>{escape(str(item.get("title") or "关注事项"))}</strong>'
            f'<small>{escape(str(item.get("date") or ""))}</small></div></div>'
        )
    st.markdown('<div class="attention-list">' + "".join(rendered) + "</div>", unsafe_allow_html=True)


def _write_feedback(result: ApiResult, success: str) -> None:
    if result.ok:
        st.toast(success, icon="✅")
        st.rerun()
    else:
        st.error(result.message or f"写入失败（{result.status}）")


def _queue_confirmation(
    key: str,
    *,
    title: str,
    summary: list[tuple[str, Any]],
    **request: Any,
) -> None:
    """把真实状态写入停在可检查的预览层，确认后才调用 Backend。"""

    st.session_state[key] = {
        "title": title,
        "summary": [(label, "—" if value in (None, "") else str(value)) for label, value in summary],
        **request,
    }


def _consume_confirmation(key: str, *, confirm_label: str = "确认写入") -> dict[str, Any] | None:
    pending = st.session_state.get(key)
    if not isinstance(pending, dict):
        return None
    with st.container(border=True):
        st.markdown(f"**{pending.get('title') or '请确认本次操作'}**")
        for label, value in pending.get("summary") or []:
            st.caption(f"{label}：{value}")
        confirm, cancel = st.columns(2)
        if cancel.button("取消", key=f"{key}_cancel", width="stretch"):
            st.session_state.pop(key, None)
            st.rerun()
        if confirm.button(
            confirm_label,
            key=f"{key}_confirm",
            type="primary",
            width="stretch",
        ):
            return st.session_state.pop(key)
    return None


def _all_instruments() -> ApiResult:
    return _get("/v1/instruments", limit=100)


def _instrument_options(items: list[dict[str, Any]]) -> dict[str, str]:
    return {
        f"{item.get('symbol')} · {item.get('name')}": str(item.get("instrument_id"))
        for item in items
        if item.get("instrument_type") != "INDEX"
    }


def _navigate(
    page: str,
    instrument: dict[str, Any] | None = None,
    prompt: str | None = None,
) -> None:
    st.session_state["main_navigation"] = page
    if instrument:
        instrument_id = str(instrument.get("instrument_id") or "")
        st.session_state["selected_instrument_id"] = instrument_id
        st.session_state["hermes_context_instrument"] = {
            "instrument_id": instrument_id,
            "symbol": instrument.get("symbol"),
            "name": instrument.get("name"),
        }
    if prompt:
        st.session_state["hermes_pending_prompt"] = prompt


def _local_history_as_of(scope: str, *, label: str = "查看历史版本") -> date:
    enabled_key = f"{scope}_history_enabled"
    date_key = f"{scope}_history_date"
    if date_key not in st.session_state:
        st.session_state[date_key] = _local_today()
    with st.expander(label):
        enabled = st.checkbox("进入只读历史模式", key=enabled_key)
        selected = st.date_input(
            "历史日期",
            max_value=_local_today(),
            disabled=not enabled,
            key=date_key,
        )
        st.caption("历史模式严格按当时可见事实查询，不能执行估值、写入或修改研究状态。")
    if enabled:
        st.markdown(
            f'<div class="history-banner" role="status"><strong>历史模式</strong><span>当前查看 {escape(selected.isoformat())} 的只读快照</span></div>',
            unsafe_allow_html=True,
        )
        return selected
    return _local_today()


def _bootstrap_status_panel(bootstrap: dict[str, Any] | None) -> None:
    if not bootstrap:
        return
    stages = bootstrap.get("stages") or []
    with st.container(border=True):
        _section_heading(
            "资料初始化",
            "已就绪" if bootstrap.get("status") == "READY" else "部分阶段需要重试",
        )
        if not stages:
            st.caption("尚无初始化阶段记录。")
            return
        columns = st.columns(min(len(stages), 5))
        labels = {
            "DONE": "完成",
            "FAILED": "失败",
            "EMPTY": "暂无数据",
            "NOT_APPLICABLE": "不适用",
            "SKIPPED_NOT_CONFIGURED": "未配置",
        }
        for column, stage in zip(columns, stages[:5], strict=False):
            status = str(stage.get("status") or "UNKNOWN")
            tone = "ok" if status == "DONE" else "warning" if status in {"EMPTY", "NOT_APPLICABLE"} else "failed"
            with column:
                st.markdown(
                    f'<div class="bootstrap-stage stage-{tone}"><strong>{escape(str(stage.get("label") or stage.get("code") or "阶段"))}</strong>'
                    f'<span>{escape(labels.get(status, status))}</span>'
                    f'<small>{escape(str(stage.get("message") or ((str(stage.get("items")) + " 条") if stage.get("items") is not None else "")))}</small></div>',
                    unsafe_allow_html=True,
                )


def _fact_display(code: str, row: dict[str, Any]) -> str:
    value = _number(row.get("value"))
    if value is None:
        return "—"
    if code == "SHARES_OUTSTANDING":
        return f"{value / 100_000_000:,.2f} 亿股"
    if str(row.get("unit") or "").upper() == "CNY":
        return f"¥{value / 100_000_000:,.2f} 亿"
    return f"{value:,.2f}"


def _render_thesis_body(body: Any) -> None:
    if not isinstance(body, dict):
        st.markdown(str(body or "尚无正文"))
        return
    notice = body.get("notice")
    if notice:
        st.info(str(notice))
    objective = body.get("objective_snapshot") or {}
    if objective:
        st.caption(
            f"客观数据截至 {objective.get('as_of') or '—'} · 最近财报期 {objective.get('latest_financial_period') or '—'}"
        )
    labels = {
        "investment_case": "核心逻辑",
        "assumptions": "关键假设",
        "risks": "主要风险",
        "questions": "待回答问题",
        "hermes_updates": "Hermes 研究记录",
    }
    for key, label in labels.items():
        values = body.get(key) or []
        if not values:
            continue
        st.markdown(f"**{label}**")
        st.markdown("\n".join(f"- {item}" for item in values))


def _valuation_workspace(
    payload: dict[str, Any], instrument: dict[str, Any], as_of: date, *, read_only: bool,
) -> None:
    objective = payload.get("objective_valuation") or {}
    if objective.get("ready"):
        _section_heading("客观估值快照", f"财务期 {objective.get('financial_period') or '—'}")
        cols = st.columns(4)
        values = [
            ("市盈率 PE", _plain_number(objective.get("pe"), 2)),
            ("市净率 PB", _plain_number(objective.get("pb"), 2)),
            ("每股收益 EPS", _plain_number(objective.get("eps"), 4)),
            ("自由现金流收益率", _percent(objective.get("fcf_yield"))),
        ]
        for column, (label, value) in zip(cols, values, strict=True):
            with column:
                st.metric(label, value)
        flags = objective.get("quality_flags") or []
        st.caption(
            f"口径 {objective.get('pe_basis') or '—'} · 数据截至 {objective.get('as_of') or '—'}"
            + (f" · 质量标记 {'、'.join(map(str, flags))}" if flags else "")
        )
    else:
        missing = "、".join(objective.get("missing") or []) or "财务事实"
        _empty_state("客观估值尚未就绪", f"仍缺少：{missing}。可点击“补齐资料”重试同步。", compact=True)

    valuation = payload.get("valuation")
    _section_heading("主观价值区间", "只保存你明确确认的假设")
    if valuation:
        cols = st.columns(4)
        for col, (label, key) in zip(
            cols,
            [
                ("悲观价值", "bear_value"),
                ("基准价值", "base_value"),
                ("乐观价值", "bull_value"),
                ("安全边际", "margin_of_safety"),
            ],
            strict=True,
        ):
            with col:
                st.metric(
                    label,
                    _money(valuation.get(key)) if "value" in key else _percent(valuation.get(key)),
                )
        st.caption(f"运行于 {str(valuation.get('as_of') or '')[:10]} · 引擎 {valuation.get('engine_version') or '—'}")
    else:
        st.caption("尚未确认主观估值。系统不会用默认增长率或默认倍数替你作判断。")

    prompt = (
        f"请基于 Backend 中 {instrument.get('symbol')} {instrument.get('name')} 的最新行情、财务事实和投资观点，"
        "先解释客观估值指标，再提出悲观/基准/乐观的每股价值区间及概率。不要替我保存，等我确认。"
    )
    st.button(
        "先问 Hermes 形成假设",
        icon=":material/auto_awesome:",
        on_click=_navigate,
        args=("问 Hermes", instrument, prompt),
        disabled=read_only,
        key=f"ask_valuation_{instrument.get('instrument_id')}",
    )
    with st.expander("我已形成假设，结构化录入"):
        if read_only:
            st.info("历史模式只读。退出历史模式后才能运行新估值。")
            return
        with st.form(f"valuation_assumptions_{instrument.get('instrument_id')}"):
            bear = st.text_input("悲观价值（元/股）", placeholder="例如 8.50")
            base = st.text_input("基准价值（元/股）", placeholder="例如 11.20")
            bull = st.text_input("乐观价值（元/股）", placeholder="例如 14.00")
            probabilities = st.text_input("情景概率（悲观/基准/乐观，%）", placeholder="例如 20/60/20")
            basis = st.text_area("假设依据", placeholder="说明盈利、行业、风险与估值区间依据；不能为空。")
            preview = st.form_submit_button("预览估值假设", type="primary")
        if preview:
            try:
                scenario_values = [Decimal(value.strip()) for value in (bear, base, bull)]
                probability_values = [
                    Decimal(value.strip()) / Decimal(100)
                    for value in probabilities.replace("，", "/").split("/")
                ]
                if len(probability_values) != 3 or sum(probability_values) != Decimal(1):
                    raise ValueError("三个概率之和必须等于 100%")
                if any(value <= 0 for value in scenario_values) or not basis.strip():
                    raise ValueError("三个价值和假设依据都必须填写")
                st.session_state[f"valuation_draft_{instrument.get('instrument_id')}"] = {
                    "values": [str(value) for value in scenario_values],
                    "probabilities": [str(value) for value in probability_values],
                    "basis": basis.strip(),
                }
            except (ArithmeticError, ValueError) as exc:
                st.error(f"无法生成预览：{exc}")
        draft_key = f"valuation_draft_{instrument.get('instrument_id')}"
        draft = st.session_state.get(draft_key)
        if draft:
            st.markdown(
                f"**待确认** · 悲观 ¥{draft['values'][0]} / 基准 ¥{draft['values'][1]} / 乐观 ¥{draft['values'][2]}  "
                f"  \n概率 {Decimal(draft['probabilities'][0]):.0%} / {Decimal(draft['probabilities'][1]):.0%} / {Decimal(draft['probabilities'][2]):.0%}"
            )
            st.caption(draft["basis"])
            if st.button(
                "确认并运行估值",
                type="primary",
                icon=":material/check_circle:",
                key=f"confirm_valuation_{instrument.get('instrument_id')}",
            ):
                assumptions = [
                    {
                        "name": name,
                        "value": value,
                        "unit": "ratio",
                        "basis": draft["basis"],
                        "source_tags": ["USER_CONFIRMED"],
                    }
                    for name, value in zip(
                        ("p_bear", "p_base", "p_bull"), draft["probabilities"], strict=True,
                    )
                ]
                result = _write(
                    "POST",
                    "/v1/valuations",
                    payload={
                        "instrument_id": instrument.get("instrument_id"),
                        "model_type": "SCENARIO",
                        "as_of": f"{as_of.isoformat()}T00:00:00+08:00",
                        "fcf_forecast": draft["values"],
                        "assumptions": assumptions,
                    },
                    timeout=60,
                )
                if result.ok:
                    st.session_state.pop(draft_key, None)
                    st.toast("估值已完成并保留假设与来源。")
                    st.rerun()
                else:
                    st.error(result.message or "估值运行失败，请检查客观数据是否齐全。")


def _today_page(as_of: date) -> None:
    result = _get("/v1/office/today", as_of=as_of.isoformat())
    if not result.ok:
        _page_header("今日", "投资办公室的每日控制面")
        _error_state(result)
        return
    payload = result.data
    portfolio = payload.get("portfolio") or {}
    _page_header(
        "今日",
        "先处理数据缺口、待复核事项与研究动作，再查看组合指标",
        updated_at=payload.get("updated_at"),
    )
    _freshness_banner(payload.get("freshness"))

    attention_column, brief_column = st.columns([1, 2.25])
    with attention_column, st.container(border=True):
        _section_heading("关注事项", f"{len(payload.get('attention') or [])} 条")
        attention = payload.get("attention") or []
        _attention_list(attention)
        if attention:
            action_type = str(attention[0].get("type") or "")
            target_page = "组合" if action_type == "proposal" else "标的"
            st.button(
                "立即处理",
                icon=":material/arrow_forward:",
                width="stretch",
                on_click=_navigate,
                args=(target_page,),
                key="today_primary_action",
            )
    with brief_column, st.container(border=True):
        _section_heading("今日日报", "事实优先")
        points = payload.get("today_points") or []
        if points:
            st.markdown("\n".join(f"- {point}" for point in points))
        else:
            _empty_state("今日要点尚未生成", "09:00 日报任务会在每日上下文之后生成。", compact=True)
        brief = payload.get("brief")
        if brief:
            with st.expander("查看日报原文"):
                st.markdown(brief.get("content_md") or "暂无正文")

    risk = portfolio.get("risk") or {}
    risk_flags = set(risk.get("quality_flags") or [])
    risk_ready = bool(risk) and "INSUFFICIENT_NAV_HISTORY" not in risk_flags and portfolio.get("annualized_volatility") is not None

    metric_columns = st.columns(5)
    cards = [
        (
            "组合净值（NAV）",
            _money(portfolio.get("nav_cny")),
            _money(portfolio.get("daily_pnl_cny"), signed=True),
            _percent(portfolio.get("daily_return"), signed=True),
            _tone(portfolio.get("daily_pnl_cny")),
            "nav",
        ),
        (
            "现金与等价物",
            _money(portfolio.get("cash_cny")),
            _percent(portfolio.get("cash_ratio")),
            "当前现金占比",
            "neutral",
            "cash",
        ),
        (
            "当日盈亏（PIT）",
            _money(portfolio.get("daily_pnl_cny"), signed=True),
            _percent(portfolio.get("daily_return"), signed=True),
            "已扣除外部资金流",
            _tone(portfolio.get("daily_pnl_cny")),
            "pnl",
        ),
        (
            "年初至今（TWR）",
            _percent(portfolio.get("ytd_return"), signed=True),
            "",
            "时间加权收益",
            _tone(portfolio.get("ytd_return")),
            "target",
        ),
        (
            "组合风险（估计）",
            _percent(portfolio.get("annualized_volatility")) if risk_ready else "—",
            _percent(portfolio.get("max_drawdown"), signed=True) if risk_ready else "—",
            "年化波动 / 最大回撤" if risk_ready else "证据不足 / 待评估",
            "neutral",
            "risk",
        ),
    ]
    for column, card in zip(metric_columns, cards, strict=True):
        with column:
            _metric_card(
                card[0], card[1], change=card[2], detail=card[3], tone=card[4], icon=card[5]
            )

    main_left, main_middle, main_right = st.columns([2.15, 1, 1])
    with main_left, st.container(border=True):
        _section_heading("组合净值走势", "真实账本快照")
        _line_chart(
            portfolio.get("history") or [],
            key=f"today-portfolio-history-{portfolio.get('portfolio_id') or 'none'}",
        )
    with main_middle, st.container(border=True):
        _section_heading("资产配置", "当前快照")
        _donut(
            portfolio.get("allocation") or {},
            center_label="总资产",
            center_value=_money(portfolio.get("nav_cny")),
        )
    with main_right, st.container(border=True):
        _section_heading("风险分布", "确定性引擎")
        if risk_ready:
            _donut(
                portfolio.get("risk_distribution") or {},
                center_label="最大回撤",
                center_value=_percent(portfolio.get("max_drawdown"), signed=True),
            )
        else:
            _empty_state("风险证据不足", "至少需要两次有效净值快照；当前状态不得解释为零风险。")

    with st.container(border=True):
        watchlist = payload.get("watchlist") or {}
        _section_heading("观察池", watchlist.get("name") or "默认观察池")
        _watchlist_table(watchlist.get("items") or [])


def _render_instrument_detail(instrument_id: str, as_of: date, *, read_only: bool = False) -> None:
    result = _get(f"/v1/office/instruments/{instrument_id}", as_of=as_of.isoformat())
    if not result.ok:
        _error_state(result, title="无法读取标的详情")
        return
    payload = result.data
    instrument = payload.get("instrument") or {}
    market = payload.get("market") or {}
    latest = market.get("latest") or {}
    instrument_type = str(instrument.get("instrument_type") or "")
    is_etf = instrument_type == "CN_ETF"
    recent_bootstrap = st.session_state.get("last_instrument_bootstrap")
    if isinstance(recent_bootstrap, dict) and recent_bootstrap.get("instrument_id") == instrument_id:
        _bootstrap_status_panel(recent_bootstrap)
    st.markdown(
        f'<div class="instrument-title"><div><strong>{escape(str(instrument.get("name") or "—"))}</strong>'
        f'<span>{escape(str(instrument.get("symbol") or "—"))}</span></div>'
        f'<div class="price"><strong>{escape(_plain_number(latest.get("close")))}</strong>'
        f'<span class="{_tone(latest.get("pct_change"))}">{escape(_percent(latest.get("pct_change"), ratio=False, signed=True))}</span></div></div>',
        unsafe_allow_html=True,
    )
    action_columns = st.columns([1.15, 1.15, 4])
    prompt = (
        f"请研究 {instrument.get('symbol')} {instrument.get('name')}。优先使用 Backend 已有行情、财务事实、"
        "投资观点与事件，并把事实、解释、缺口和下一步验证分开说明。"
    )
    with action_columns[0]:
        st.button(
            "问 Hermes",
            icon=":material/auto_awesome:",
            type="primary",
            width="stretch",
            on_click=_navigate,
            args=("问 Hermes", instrument, prompt),
            key=f"ask_hermes_{instrument_id}",
        )
    with action_columns[1]:
        if st.button(
            "补齐资料",
            icon=":material/sync:",
            width="stretch",
            disabled=read_only,
            key=f"bootstrap_{instrument_id}",
        ):
            with st.status("正在补齐标的资料…", expanded=True) as status:
                bootstrap_result = _write(
                    "POST",
                    f"/v1/instruments/{instrument_id}/bootstrap",
                    payload={"force": True},
                    timeout=240,
                )
                if bootstrap_result.ok:
                    st.session_state["last_instrument_bootstrap"] = bootstrap_result.data
                    status.update(label="资料初始化完成", state="complete")
                    st.rerun()
                status.update(label="部分资料未能同步", state="error")
                st.error(bootstrap_result.message or "请检查数据源配置后重试。")

    readiness = payload.get("bootstrap") or {}
    if not readiness.get("ready"):
        _bootstrap_status_panel({
            "instrument_id": instrument_id,
            "status": "PARTIAL",
            "stages": [
                {"code": "market", "label": "行情与 K 线", "status": "DONE" if readiness.get("market") else "EMPTY"},
                {
                    "code": "etf_metrics" if is_etf else "fundamentals",
                    "label": "ETF 净值与指标" if is_etf else "财务事实",
                    "status": (
                        "DONE"
                        if readiness.get("etf_metrics" if is_etf else "fundamentals")
                        else "EMPTY"
                    ),
                },
                {
                    "code": "filings",
                    "label": "公司财报" if is_etf else "财报公告索引",
                    "status": "NOT_APPLICABLE" if is_etf else ("DONE" if readiness.get("filings") else "EMPTY"),
                },
                {
                    "code": "corporate_actions", "label": "分红与公司行动", "status": "DONE",
                    "items": readiness.get("corporate_actions") or 0,
                },
                {"code": "thesis", "label": "基础投资观点", "status": "DONE" if readiness.get("thesis") else "EMPTY"},
            ],
        })

    valuation_label = "ETF 指标" if is_etf else "估值"
    overview, market_tab, valuation_tab, research_tab, events_tab, source_tab = st.tabs(
        ["总览", "行情", valuation_label, "研究", "事件", "来源"]
    )
    metrics = payload.get("etf_metrics") or {}
    objective = payload.get("objective_valuation") or {}
    fundamentals = payload.get("fundamentals") or {}
    facts = fundamentals.get("metrics") or {}
    with overview:
        cols = st.columns(4)
        values = (
            [
                ("最新价格", _plain_number(latest.get("close"))),
                ("日涨跌", _percent(latest.get("pct_change"), ratio=False, signed=True)),
                ("溢折价率", _percent(metrics.get("premium_discount"), signed=True)),
                ("估值带", str(metrics.get("valuation_band") or "—")),
            ]
            if is_etf
            else [
                ("最新价格", _plain_number(latest.get("close"))),
                ("日涨跌", _percent(latest.get("pct_change"), ratio=False, signed=True)),
                ("市盈率 PE", _plain_number(objective.get("pe"), 2)),
                ("市净率 PB", _plain_number(objective.get("pb"), 2)),
            ]
        )
        for col, (label, value) in zip(cols, values, strict=True):
            with col:
                st.metric(label, value)
        _candlestick_chart(
            market.get("history") or [],
            key=f"instrument-overview-{instrument_id}-{as_of.isoformat()}",
            height=300,
        )
        if facts:
            _section_heading("最新财务事实", "按披露时点可见")
            fact_labels = {
                "REVENUE": "营业收入",
                "NET_INCOME": "归母净利润",
                "TOTAL_EQUITY": "归母权益",
                "CASH": "现金",
                "DEBT": "有息负债",
                "FREE_CASH_FLOW": "自由现金流",
                "SHARES_OUTSTANDING": "总股本",
            }
            fact_rows = [
                {
                    "指标": fact_labels.get(code, code),
                    "数值": _fact_display(code, row),
                    "报告期": row.get("period_end"),
                    "披露时间": str(row.get("published_at") or "")[:10],
                    "来源": row.get("provider"),
                }
                for code, row in facts.items()
                if code in fact_labels
            ]
            st.dataframe(fact_rows, hide_index=True, width="stretch")
    with market_tab:
        _candlestick_chart(
            market.get("history") or [],
            key=f"instrument-market-{instrument_id}-{as_of.isoformat()}",
            height=440,
        )
        history = market.get("history") or []
        if history:
            st.dataframe(history, hide_index=True, width="stretch")
    with valuation_tab:
        if is_etf and metrics:
            metric_columns = st.columns(4)
            metric_values = [
                ("指标日期", str(metrics.get("market_date") or metrics.get("as_of") or "—")),
                ("指数 PE", _plain_number(metrics.get("index_pe"))),
                ("指数 PB", _plain_number(metrics.get("index_pb"))),
                ("额度状态", str(metrics.get("quota_status") or "UNKNOWN")),
            ]
            for column, (label, value) in zip(metric_columns, metric_values, strict=True):
                with column:
                    st.metric(label, value)
            flags = metrics.get("quality_flags") or []
            if flags:
                st.warning("质量标记：" + "、".join(str(flag) for flag in flags))
            st.caption(
                f"质量 {metrics.get('quality_status') or 'UNKNOWN'} · 引擎 {metrics.get('engine_version') or '—'}"
            )
        elif is_etf:
            _empty_state("尚无 ETF 指标快照", "等待 NAV、持仓与指数数据同步。")
        else:
            _valuation_workspace(payload, instrument, as_of, read_only=read_only)
    with research_tab:
        thesis = payload.get("thesis")
        if thesis:
            st.caption(
                f"状态 {thesis.get('lifecycle_status')} · 健康度 {thesis.get('health_status')} · 版本 {thesis.get('version') or '—'}"
            )
            st.markdown(f"**{thesis.get('summary') or '研究观点'}**")
            _render_thesis_body(thesis.get("body"))
            st.button(
                "继续与 Hermes 研究",
                icon=":material/auto_awesome:",
                on_click=_navigate,
                args=("问 Hermes", instrument, prompt),
                key=f"continue_research_{instrument_id}",
            )
        else:
            _empty_state("尚未建立投资观点", "点击“补齐资料”建立明确标注为待研究的基础档案。")
    with events_tab:
        events = payload.get("events") or []
        if not events:
            _empty_state("暂无事件", "财报披露、公司行动、笔记、证据与投资观点复核会统一按时间显示。")
        else:
            event_labels = {
                "filing": "披露", "corporate_action": "公司行动", "note": "笔记",
                "evidence": "证据", "thesis": "投资观点", "review": "复核",
            }
            rows = []
            for event in events:
                event_type = str(event.get("type") or "event")
                rows.append(
                    f'<div class="event-row"><div><span>{escape(event_labels.get(event_type, event_type))}</span>'
                    f'<time>{escape(str(event.get("at") or "")[:16])}</time></div>'
                    f'<strong>{escape(str(event.get("title") or "未命名事件"))}</strong>'
                    f'<p>{escape(str(event.get("text") or ""))}</p></div>'
                )
            st.markdown('<div class="event-list">' + "".join(rows) + "</div>", unsafe_allow_html=True)
    with source_tab:
        provenance = market.get("provenance") or []
        source_rows = [
            {"数据域": "行情", **row}
            for row in provenance
        ] + [
            {
                "数据域": "财务事实",
                "metric_code": code,
                "provider": row.get("provider"),
                "provenance_id": row.get("provenance_id"),
                "as_of_date": row.get("period_end"),
                "quality_status": row.get("quality_status"),
            }
            for code, row in facts.items()
        ]
        if source_rows:
            st.dataframe(source_rows, hide_index=True, width="stretch")
        else:
            _empty_state("暂无来源记录", "真实同步完成后可追溯 Provider 与摄取批次。")


def _watchlist_management(payload: dict[str, Any]) -> None:
    watchlist = payload.get("watchlist") or {}
    watchlist_id = watchlist.get("watchlist_id")
    active_items = watchlist.get("items") or []
    if not watchlist_id:
        _empty_state("没有可用观察池", "请先运行本地产品初始化。")
        return
    add_tab, remove_tab = st.tabs(["添加新标的", "移出观察池"])
    with add_tab:
        with st.form("add_watchlist_instrument_form", clear_on_submit=True):
            symbol = st.text_input("证券代码", placeholder="例如 003816")
            name = st.text_input("证券名称", placeholder="例如 中国广核")
            st.caption("只需代码和名称。系统自动识别交易所与资产类型，并初始化行情、财务事实、披露、公司行动和基础投资观点。")
            submitted = st.form_submit_button("加入并初始化", type="primary")
        if submitted:
            if not symbol.strip() or not name.strip():
                st.error("证券代码和名称必填")
            else:
                _queue_confirmation(
                    "confirm_watchlist_add",
                    title="预览：加入标的并初始化研究档案",
                    summary=[("证券代码", symbol.strip()), ("证券名称", name.strip())],
                    payload={"symbol": symbol.strip(), "name": name.strip()},
                )
        pending_add = _consume_confirmation(
            "confirm_watchlist_add",
            confirm_label="确认加入并初始化",
        )
        if pending_add:
            with st.status("正在建立标的研究档案…", expanded=True) as status:
                result = _write(
                    "POST",
                    f"/v1/watchlists/{watchlist_id}/instruments",
                    payload=pending_add["payload"],
                    timeout=240,
                )
                if not result.ok:
                    status.update(label="标的添加失败", state="error")
                    st.error(result.message or "添加失败，请检查代码后重试。")
                else:
                    instrument = result.data.get("instrument") or {}
                    instrument_id = str(instrument.get("instrument_id") or "")
                    st.session_state["selected_instrument_id"] = instrument_id
                    st.session_state["instrument_detail_selector"] = instrument_id
                    st.session_state["last_instrument_bootstrap"] = result.data.get("bootstrap") or {}
                    status.update(label="标的已加入，正在打开详情", state="complete")
                    st.toast("标的已加入；详情页会显示每个初始化阶段。")
                    st.rerun()
    with remove_tab:
        active_options = {
            f"{item.get('symbol')} · {item.get('name')}": str(item.get("instrument_id"))
            for item in active_items
        }
        if not active_options:
            st.caption("观察池为空。")
        else:
            selected = st.selectbox("选择要移出的标的", list(active_options), key="watchlist_remove")
            if st.button("预览移出", key="watchlist_remove_button"):
                _queue_confirmation(
                    "confirm_watchlist_remove",
                    title="预览：移出观察池",
                    summary=[("标的", selected), ("影响", "只移出观察池，不删除标的研究档案")],
                    instrument_id=active_options[selected],
                )
            pending_remove = _consume_confirmation(
                "confirm_watchlist_remove",
                confirm_label="确认移出观察池",
            )
            if pending_remove:
                result = _write(
                    "DELETE",
                    f"/v1/watchlists/{watchlist_id}/members/{pending_remove['instrument_id']}",
                )
                _write_feedback(result, "已移出观察池")


def _watchlist_page(as_of: date) -> None:
    latest_date = _local_today()
    result = _get("/v1/office/watchlist", as_of=latest_date.isoformat())
    _page_header("标的", "从一个入口完成观察、资料补齐、研究与估值")
    if not result.ok:
        _error_state(result)
        return
    payload = result.data
    _freshness_banner(payload.get("freshness"))
    watchlist = payload.get("watchlist") or {}
    items = watchlist.get("items") or []
    with st.expander("添加或管理标的", expanded=not items):
        _watchlist_management(payload)

    all_result = _all_instruments()
    all_items = all_result.data if all_result.ok and isinstance(all_result.data, list) else []
    all_items = [item for item in all_items if item.get("instrument_type") != "INDEX"]
    scope = st.radio(
        "标的范围",
        ["观察池", "全部标的"],
        horizontal=True,
        label_visibility="collapsed",
        key="instrument_scope",
    )
    visible_items = items if scope == "观察池" else all_items
    with st.container(border=True):
        _section_heading(
            (watchlist.get("name") or "观察池")
            if scope == "观察池"
            else "全部已登记标的",
            f"{len(visible_items)} 个标的",
        )
        if scope == "观察池":
            _watchlist_table(visible_items)
        elif visible_items:
            st.dataframe(
                [
                    {
                        "代码": item.get("symbol"), "名称": item.get("name"),
                        "类型": "股票" if item.get("instrument_type") == "CN_EQUITY" else "ETF",
                        "市场": item.get("market"), "状态": item.get("status"),
                    }
                    for item in visible_items
                ],
                hide_index=True,
                width="stretch",
            )
        else:
            _empty_state("当前范围没有标的", "点击上方“添加或管理标的”开始。", compact=True)
    if visible_items:
        by_id = {str(item.get("instrument_id")): item for item in visible_items}
        option_ids = list(by_id)
        preferred = str(st.session_state.get("selected_instrument_id") or "")
        current = st.session_state.get("instrument_detail_selector")
        if current not in by_id:
            st.session_state["instrument_detail_selector"] = preferred if preferred in by_id else option_ids[0]
        selected_id = st.selectbox(
            "查看标的详情",
            option_ids,
            format_func=lambda value: f"{by_id[value].get('symbol')} · {by_id[value].get('name')}",
            key="instrument_detail_selector",
        )
        st.session_state["selected_instrument_id"] = selected_id
        detail_as_of = _local_history_as_of("instrument_detail")
        _render_instrument_detail(
            selected_id,
            detail_as_of,
            read_only=bool(st.session_state.get("instrument_detail_history_enabled")),
        )


def _positions_table(positions: list[dict[str, Any]]) -> None:
    if not positions:
        _empty_state("尚未录入持仓", "打开“手工记账”录入现有持仓；期初迁入不会改变你另行录入的现金。")
        return
    rows = [
        {
            "代码": (row.get("instrument") or {}).get("symbol"),
            "名称": (row.get("instrument") or {}).get("name"),
            "数量": _number(row.get("quantity")),
            "平均成本": _number(row.get("average_cost_cny")),
            "最新价": _number(row.get("market_price_cny")),
            "市值": _number(row.get("market_value_cny")),
            "未实现盈亏": _number(row.get("unrealized_pnl_cny")),
        }
        for row in positions
    ]
    st.dataframe(
        rows,
        hide_index=True,
        width="stretch",
        column_config={
            "数量": st.column_config.NumberColumn(format="%,.4f"),
            "平均成本": st.column_config.NumberColumn(format="¥%,.4f"),
            "最新价": st.column_config.NumberColumn(format="¥%,.4f"),
            "市值": st.column_config.NumberColumn(format="¥%,.2f"),
            "未实现盈亏": st.column_config.NumberColumn(format="¥%,.2f"),
        },
    )


def _manual_ledger(portfolio_id: str) -> None:
    instruments_result = _all_instruments()
    if not instruments_result.ok:
        _error_state(instruments_result, title="无法读取手工账本标的")
        return
    instruments = instruments_result.data if isinstance(instruments_result.data, list) else []
    options = _instrument_options(instruments)
    if not options:
        _empty_state("没有可选标的", "先在观察池中创建或登记标的。")
        return
    opening_tab, cash_tab, activity_tab, correction_tab = st.tabs(
        ["迁入现有持仓", "现金变动", "买卖/分红/费用", "流水更正"]
    )
    with opening_tab:
        st.caption("录入证券、数量、平均成本与起始持有日；系统用配对流水建立成本，不改变当前现金。")
        with st.form("opening_position_form", clear_on_submit=True):
            selected = st.selectbox("标的", list(options), key="opening_instrument")
            quantity = st.number_input("当前数量", min_value=0.0001, value=100.0, step=100.0)
            average_cost = st.number_input("平均成本（元）", min_value=0.0001, value=1.0, step=0.01, format="%.4f")
            holding_date = st.date_input("起始持有日", value=_local_today())
            note = st.text_input("备注（可选）")
            submitted = st.form_submit_button("预览迁入", type="primary")
        if submitted:
            _queue_confirmation(
                "confirm_opening_position",
                title="预览：迁入现有持仓",
                summary=[
                    ("标的", selected),
                    ("数量", quantity),
                    ("平均成本", f"¥{average_cost:,.4f}"),
                    ("起始持有日", holding_date.isoformat()),
                    ("说明", "仅建立手工成本流水，不修改现金"),
                ],
                path=f"/v1/portfolios/{portfolio_id}/opening-positions",
                payload={
                    "instrument_id": options[selected],
                    "quantity": str(Decimal(str(quantity))),
                    "average_cost_cny": str(Decimal(str(average_cost))),
                    "holding_date": holding_date.isoformat(),
                    "note": note or None,
                },
            )
        pending_opening = _consume_confirmation(
            "confirm_opening_position",
            confirm_label="确认迁入持仓",
        )
        if pending_opening:
            response = _write(
                "POST",
                pending_opening["path"],
                payload=pending_opening["payload"],
                account_write=True,
            )
            _write_feedback(response, "期初持仓已迁入")
    with cash_tab:
        with st.form("cash_transaction_form", clear_on_submit=True):
            cash_type = st.selectbox("类型", ["CASH_IN", "CASH_OUT"], format_func=lambda value: "现金转入" if value == "CASH_IN" else "现金转出")
            amount = st.number_input("金额（元）", min_value=0.01, value=10000.0, step=1000.0)
            trade_date = st.date_input("日期", value=_local_today(), key="cash_date")
            note = st.text_input("备注（可选）", key="cash_note")
            submitted = st.form_submit_button("预览现金流水", type="primary")
        if submitted:
            signed_amount = Decimal(str(amount)) * (1 if cash_type == "CASH_IN" else -1)
            _queue_confirmation(
                "confirm_cash_transaction",
                title="预览：记入现金流水",
                summary=[
                    ("类型", "现金转入" if cash_type == "CASH_IN" else "现金转出"),
                    ("金额", f"¥{signed_amount:,.2f}"),
                    ("日期", trade_date.isoformat()),
                    ("备注", note),
                ],
                path=f"/v1/portfolios/{portfolio_id}/transactions",
                payload={
                    "transaction_type": cash_type,
                    "amount_cny": str(signed_amount),
                    "trade_date": trade_date.isoformat(),
                    "note": note or None,
                },
            )
        pending_cash = _consume_confirmation(
            "confirm_cash_transaction",
            confirm_label="确认记入现金流水",
        )
        if pending_cash:
            response = _write(
                "POST",
                pending_cash["path"],
                payload=pending_cash["payload"],
                account_write=True,
            )
            _write_feedback(response, "现金流水已记录")
    with activity_tab:
        activity = st.selectbox(
            "业务类型",
            ["BUY", "SELL", "DIVIDEND", "FEE"],
            format_func={"BUY": "买入", "SELL": "卖出", "DIVIDEND": "分红", "FEE": "费用"}.get,
            key="ledger_activity",
        )
        with st.form("activity_transaction_form", clear_on_submit=True):
            selected = st.selectbox("标的", list(options), key="activity_instrument")
            if activity in {"BUY", "SELL"}:
                quantity = st.number_input("成交数量", min_value=0.0001, value=100.0, step=100.0)
                price = st.number_input("成交价（元）", min_value=0.0001, value=1.0, step=0.01, format="%.4f")
                direct_amount = None
                fees = st.number_input("费用（元）", min_value=0.0, value=0.0, step=0.01)
            else:
                quantity = None
                price = None
                direct_amount = st.number_input("金额（元）", min_value=0.01, value=100.0, step=10.0)
                fees = 0.0
            trade_date = st.date_input("业务日期", value=_local_today(), key="activity_date")
            note = st.text_input("备注（可选）", key="activity_note")
            submitted = st.form_submit_button("预览账本流水", type="primary")
        if submitted:
            if activity in {"BUY", "SELL"}:
                amount = Decimal(str(quantity)) * Decimal(str(price))
                amount = -amount if activity == "BUY" else amount
            else:
                amount = Decimal(str(direct_amount))
                amount = -amount if activity == "FEE" else amount
            activity_label = {
                "BUY": "买入", "SELL": "卖出", "DIVIDEND": "分红", "FEE": "费用",
            }[activity]
            _queue_confirmation(
                "confirm_activity_transaction",
                title="预览：写入不可变账本流水",
                summary=[
                    ("业务类型", activity_label),
                    ("标的", selected),
                    ("数量", quantity),
                    ("成交价", f"¥{price:,.4f}" if price is not None else None),
                    ("现金影响", f"¥{amount:,.2f}"),
                    ("费用", f"¥{fees:,.2f}"),
                    ("业务日期", trade_date.isoformat()),
                ],
                path=f"/v1/portfolios/{portfolio_id}/transactions",
                payload={
                    "transaction_type": activity,
                    "instrument_id": options[selected],
                    "quantity": str(quantity) if quantity is not None else None,
                    "price_cny": str(price) if price is not None else None,
                    "amount_cny": str(amount),
                    "fees_cny": str(fees),
                    "trade_date": trade_date.isoformat(),
                    "note": note or None,
                },
            )
        pending_activity = _consume_confirmation(
            "confirm_activity_transaction",
            confirm_label="确认写入账本",
        )
        if pending_activity:
            response = _write(
                "POST",
                pending_activity["path"],
                payload=pending_activity["payload"],
                account_write=True,
            )
            _write_feedback(response, "账本流水已写入")
    with correction_tab:
        transactions = _get(f"/v1/portfolios/{portfolio_id}/transactions", limit=300)
        if not transactions.ok:
            _error_state(transactions, title="无法读取可更正流水")
            return
        rows = transactions.data.get("items", [])
        reversed_ids = {row.get("reverses_transaction_id") for row in rows if row.get("reverses_transaction_id")}
        eligible = {
            f"{row.get('trade_date')} · {row.get('transaction_type')} · {row.get('amount_cny')}": row.get("transaction_id")
            for row in rows
            if row.get("transaction_type") != "REVERSAL" and row.get("transaction_id") not in reversed_ids
        }
        if not eligible:
            st.caption("没有可更正的流水。")
        else:
            selected = st.selectbox("选择原流水", list(eligible), key="reversal_transaction")
            correction_date = st.date_input("更正日期", value=_local_today(), key="reversal_date")
            st.warning("更正会追加一条 REVERSAL，不会删除或修改原流水。")
            if st.button("预览更正", key="reversal_button"):
                _queue_confirmation(
                    "confirm_reversal",
                    title="预览：追加流水更正",
                    summary=[
                        ("原流水", selected),
                        ("更正日期", correction_date.isoformat()),
                        ("影响", "追加 REVERSAL，不删除或覆盖原流水"),
                    ],
                    transaction_id=eligible[selected],
                    trade_date=correction_date.isoformat(),
                )
            pending_reversal = _consume_confirmation(
                "confirm_reversal",
                confirm_label="确认追加更正",
            )
            if pending_reversal:
                response = _write(
                    "POST",
                    f"/v1/portfolios/{portfolio_id}/transactions/{pending_reversal['transaction_id']}/reversal",
                    params={"trade_date": pending_reversal["trade_date"]},
                    account_write=True,
                )
                _write_feedback(response, "更正流水已追加")


def _proposal_actions(
    portfolio_id: str, proposals: list[dict[str, Any]], *, read_only: bool = False,
) -> None:
    if not proposals:
        _empty_state("暂无交易建议", "Hermes 只能创建建议，不能连接券商或自动执行。")
        return
    for proposal in proposals:
        instrument = proposal.get("instrument") or {}
        with st.container(border=True):
            left, middle, right = st.columns([2.6, 1, 1.6], vertical_alignment="center")
            with left:
                st.markdown(f"**{instrument.get('symbol', '—')} · {instrument.get('name', '—')}**")
                st.caption(proposal.get("rationale") or "未填写理由")
            with middle:
                st.write(f"{proposal.get('proposal_type')} · {proposal.get('quantity')}")
                st.caption(f"状态 {proposal.get('status')}")
            with right:
                proposal_id = proposal.get("trade_proposal_id")
                if read_only:
                    st.caption("历史模式只读")
                    continue
                if proposal.get("status") == "PROPOSED":
                    approve, reject = st.columns(2)
                    if approve.button("批准", key=f"approve_{proposal_id}", type="primary"):
                        _queue_confirmation(
                            f"confirm_proposal_{proposal_id}",
                            title="预览：批准交易建议",
                            summary=[
                                ("标的", f"{instrument.get('symbol', '—')} · {instrument.get('name', '—')}"),
                                ("建议", f"{proposal.get('proposal_type')} · {proposal.get('quantity')}"),
                                ("说明", "只批准建议，不连接券商或自动成交"),
                            ],
                            status="APPROVED",
                        )
                    if reject.button("拒绝", key=f"reject_{proposal_id}"):
                        _queue_confirmation(
                            f"confirm_proposal_{proposal_id}",
                            title="预览：拒绝交易建议",
                            summary=[
                                ("标的", f"{instrument.get('symbol', '—')} · {instrument.get('name', '—')}"),
                                ("建议", f"{proposal.get('proposal_type')} · {proposal.get('quantity')}"),
                            ],
                            status="REJECTED",
                        )
                    pending_proposal = _consume_confirmation(
                        f"confirm_proposal_{proposal_id}",
                        confirm_label="确认变更建议状态",
                    )
                    if pending_proposal:
                        response = _write(
                            "POST",
                            f"/v1/portfolios/{portfolio_id}/proposals/{proposal_id}/transition",
                            payload={"status": pending_proposal["status"]},
                            account_write=True,
                        )
                        _write_feedback(
                            response,
                            "建议已批准" if pending_proposal["status"] == "APPROVED" else "建议已拒绝",
                        )
                elif proposal.get("status") == "APPROVED":
                    with st.popover("登记实际成交", width="stretch"):
                        with st.form(f"execute_{proposal_id}"):
                            executed_qty = st.number_input("实际数量", min_value=0.0001, value=float(proposal.get("quantity") or 1), key=f"qty_{proposal_id}")
                            executed_price = st.number_input("实际价格", min_value=0.0001, value=float(proposal.get("limit_price_cny") or 1), key=f"price_{proposal_id}")
                            executed_fee = st.number_input("实际费用", min_value=0.0, value=0.0, key=f"fee_{proposal_id}")
                            executed_date = st.date_input("成交日期", value=_local_today(), key=f"date_{proposal_id}")
                            submitted = st.form_submit_button("预览成交登记")
                        if submitted:
                            _queue_confirmation(
                                f"confirm_execution_{proposal_id}",
                                title="预览：登记外部实际成交",
                                summary=[
                                    ("标的", f"{instrument.get('symbol', '—')} · {instrument.get('name', '—')}"),
                                    ("实际数量", executed_qty),
                                    ("实际价格", f"¥{executed_price:,.4f}"),
                                    ("实际费用", f"¥{executed_fee:,.2f}"),
                                    ("成交日期", executed_date.isoformat()),
                                    ("说明", "仅记录你已在外部完成的成交"),
                                ],
                                payload={
                                    "status": "EXECUTED",
                                    "quantity": str(executed_qty),
                                    "price_cny": str(executed_price),
                                    "fees_cny": str(executed_fee),
                                    "trade_date": executed_date.isoformat(),
                                },
                            )
                    pending_execution = _consume_confirmation(
                        f"confirm_execution_{proposal_id}",
                        confirm_label="确认登记实际成交",
                    )
                    if pending_execution:
                        response = _write(
                            "POST",
                            f"/v1/portfolios/{portfolio_id}/proposals/{proposal_id}/transition",
                            payload=pending_execution["payload"],
                            account_write=True,
                        )
                        _write_feedback(response, "实际成交已登记")


def _portfolio_page(as_of: date) -> None:
    _page_header("组合", "手工 REAL 账本、不可变流水与人工确认建议")
    as_of = _local_history_as_of("portfolio", label="查看组合历史快照")
    read_only = bool(st.session_state.get("portfolio_history_enabled"))
    initial = _get("/v1/office/portfolios", as_of=as_of.isoformat())
    if not initial.ok:
        _error_state(initial)
        return
    items = initial.data.get("items") or []
    if not items:
        _empty_state("尚无投资组合", "运行 ./scripts/hermes bootstrap 创建默认手工组合。")
        return
    labels = {f"{item.get('name')} · {item.get('mode')}": item.get("portfolio_id") for item in items}
    selected_label = st.selectbox("组合", list(labels), label_visibility="collapsed")
    result = _get(
        "/v1/office/portfolios",
        as_of=as_of.isoformat(),
        portfolio_id=labels[selected_label],
    )
    if not result.ok:
        _error_state(result)
        return
    payload = result.data
    portfolio = payload.get("selected") or {}
    _freshness_banner(payload.get("freshness"))
    cols = st.columns(4)
    for col, card in zip(
        cols,
        [
            ("组合净值", _money(portfolio.get("nav_cny")), "nav"),
            ("现金", _money(portfolio.get("cash_cny")), "cash"),
            ("证券市值", _money(portfolio.get("market_value_cny")), "pnl"),
            (
                "未实现盈亏",
                _money(portfolio.get("unrealized_pnl_cny"), signed=True),
                "target",
            ),
        ],
        strict=True,
    ):
        with col:
            _metric_card(card[0], card[1], icon=card[2])
    positions_tab, ledger_tab, transactions_tab, proposal_tab = st.tabs(
        ["当前持仓", "手工记账", "流水", "交易建议"]
    )
    with positions_tab:
        _positions_table(portfolio.get("positions") or [])
        with st.container(border=True):
            _section_heading("组合净值历史", portfolio.get("snapshot_date") or "尚无快照")
            _line_chart(
                portfolio.get("history") or [],
                key=(
                    f"portfolio-history-{portfolio.get('portfolio_id') or 'none'}-"
                    f"{as_of.isoformat()}"
                ),
                height=280,
            )
    with ledger_tab:
        if read_only:
            _empty_state("历史模式只读", "退出历史模式后才能录入持仓、现金或交易流水。", compact=True)
        else:
            _manual_ledger(str(portfolio.get("portfolio_id")))
    with transactions_tab:
        rows = portfolio.get("transactions") or []
        if rows:
            type_labels = {
                "OPENING": "期初迁入", "BUY": "买入", "SELL": "卖出",
                "DIVIDEND": "分红", "FEE": "费用", "CASH_IN": "现金转入",
                "CASH_OUT": "现金转出", "REVERSAL": "流水更正",
            }
            transaction_rows = []
            for row in rows:
                instrument = row.get("instrument") or {}
                transaction_rows.append({
                    "日期": row.get("trade_date"),
                    "类型": type_labels.get(row.get("transaction_type"), row.get("transaction_type")),
                    "代码": instrument.get("symbol") or "—",
                    "名称": instrument.get("name") or "—",
                    "数量": _number(row.get("quantity")),
                    "成交价": _number(row.get("price_cny")),
                    "现金影响": _number(row.get("amount_cny")),
                    "费用": _number(row.get("fees_cny")),
                    "备注": row.get("note") or "—",
                    "更正对象": str(row.get("reverses_transaction_id") or "—"),
                })
            st.dataframe(
                transaction_rows,
                hide_index=True,
                width="stretch",
                column_config={
                    "数量": st.column_config.NumberColumn(format="%,.4f"),
                    "成交价": st.column_config.NumberColumn(format="¥%,.4f"),
                    "现金影响": st.column_config.NumberColumn(format="¥%,.2f"),
                    "费用": st.column_config.NumberColumn(format="¥%,.2f"),
                },
            )
        else:
            _empty_state("账本尚无流水", "期初持仓、现金与后续交易都会按时间保留。")
    with proposal_tab:
        _proposal_actions(
            str(portfolio.get("portfolio_id")),
            portfolio.get("proposals") or [],
            read_only=read_only,
        )


def _query_session_id() -> str:
    value = st.query_params.get("hermes_session", "")
    if isinstance(value, list):
        value = value[0] if value else ""
    return str(value or "").strip()


def _remember_query_session(session_id: str) -> None:
    if session_id and _query_session_id() != session_id:
        st.query_params["hermes_session"] = session_id


def _new_hermes_conversation() -> None:
    client = st.session_state.pop("hermes_chat_client", None)
    if isinstance(client, HermesChatClient):
        client.new_session()
    st.session_state.pop("hermes_chat_messages", None)
    st.session_state.pop("hermes_loaded_session", None)
    if "hermes_session" in st.query_params:
        del st.query_params["hermes_session"]


def _open_hermes_conversation(session_id: str) -> None:
    client = st.session_state.pop("hermes_chat_client", None)
    if isinstance(client, HermesChatClient):
        client.close()
    st.session_state.pop("hermes_chat_messages", None)
    st.session_state.pop("hermes_loaded_session", None)
    st.query_params["hermes_session"] = session_id


def _session_time(value: Any) -> str:
    try:
        return datetime.fromtimestamp(float(value)).astimezone().strftime("%Y-%m-%d %H:%M")
    except (OSError, OverflowError, TypeError, ValueError):
        return "时间未知"


def _session_title(session: dict[str, Any]) -> str:
    title = str(session.get("title") or session.get("preview") or "未命名会话").strip()
    return title if len(title) <= 52 else f"{title[:51]}…"


def _hermes_history_panel(client: HermesChatClient) -> None:
    with st.expander("历史会话"):
        try:
            sessions = client.list_sessions(limit=50)
        except HermesChatError as exc:
            st.error(f"无法读取历史会话：{exc}")
            return
        if not sessions:
            _empty_state("暂无历史会话", "完成第一次对话后，会话会自动出现在这里。", compact=True)
            return

        current_id = _query_session_id()
        session_by_id = {str(item["id"]): item for item in sessions}
        session_ids = list(session_by_id)
        target_key = "hermes_history_target"
        if st.session_state.get(target_key) not in session_ids:
            st.session_state[target_key] = current_id if current_id in session_by_id else session_ids[0]

        rows = [
            {
                "会话": ("当前 · " if str(item["id"]) == current_id else "") + _session_title(item),
                "最后活动": _session_time(item.get("last_active") or item.get("started_at")),
                "消息": int(item.get("message_count") or 0),
                "会话 ID": str(item["id"]),
            }
            for item in sessions
        ]
        st.dataframe(
            rows,
            hide_index=True,
            width="stretch",
            height=min(286, 39 + 35 * len(rows)),
            column_config={"消息": st.column_config.NumberColumn(format="%d 条")},
        )
        selected_id = st.selectbox(
            "选择要操作的会话",
            session_ids,
            format_func=lambda value: (
                f"{_session_title(session_by_id[value])} · "
                f"{_session_time(session_by_id[value].get('last_active') or session_by_id[value].get('started_at'))}"
            ),
            key=target_key,
        )
        selected = session_by_id[selected_id]

        open_column, export_column, delete_column = st.columns(3)
        with open_column:
            if st.button(
                "打开此会话",
                icon=":material/chat:",
                disabled=selected_id == current_id,
                width="stretch",
                key="open_hermes_history",
            ):
                _open_hermes_conversation(selected_id)
                st.rerun()
        with export_column:
            if st.button(
                "生成导出文件",
                icon=":material/download:",
                width="stretch",
                key="prepare_hermes_export",
            ):
                try:
                    with st.spinner("正在生成 Markdown…"):
                        transcript = client.transcript(selected_id)
                    data, filename = render_transcript_markdown(selected, transcript)
                    st.session_state["hermes_export"] = {
                        "session_id": selected_id,
                        "data": data,
                        "filename": filename,
                    }
                except HermesChatError as exc:
                    st.error(f"无法导出这段会话：{exc}")
        with delete_column:
            if st.button(
                "删除会话",
                icon=":material/delete:",
                width="stretch",
                key="request_hermes_delete",
            ):
                st.session_state["hermes_delete_target"] = selected_id

        export_data = st.session_state.get("hermes_export")
        if isinstance(export_data, dict) and export_data.get("session_id") == selected_id:
            st.download_button(
                "下载 Markdown",
                data=str(export_data.get("data") or ""),
                file_name=str(export_data.get("filename") or "Hermes-研究会话.md"),
                mime="text/markdown; charset=utf-8",
                icon=":material/file_save:",
                width="stretch",
                key="download_hermes_export",
            )

        if st.session_state.get("hermes_delete_target") == selected_id:
            st.warning(f"将永久删除“{_session_title(selected)}”及其全部消息，此操作无法撤销。")
            confirm_column, cancel_column = st.columns(2)
            with confirm_column:
                if st.button(
                    "永久删除",
                    type="primary",
                    width="stretch",
                    key="confirm_hermes_delete_button",
                ):
                    try:
                        client.delete_session(selected_id)
                    except HermesChatError as exc:
                        st.error(f"删除失败：{exc}")
                        return
                    st.session_state.pop("hermes_delete_target", None)
                    st.session_state.pop("hermes_export", None)
                    if selected_id == current_id:
                        st.session_state.pop("hermes_chat_client", None)
                        st.session_state.pop("hermes_chat_messages", None)
                        st.session_state.pop("hermes_loaded_session", None)
                        if "hermes_session" in st.query_params:
                            del st.query_params["hermes_session"]
                    st.toast("会话已永久删除。", icon="✅")
                    st.rerun()
            with cancel_column:
                if st.button("取消", width="stretch", key="cancel_hermes_delete"):
                    st.session_state.pop("hermes_delete_target", None)
                    st.rerun()


def _hermes_client() -> HermesChatClient:
    stored_session_id = _query_session_id()
    client = st.session_state.get("hermes_chat_client")
    if (
        isinstance(client, HermesChatClient)
        and stored_session_id
        and client.stored_session_id != stored_session_id
    ):
        client.close()
        client = None
    if not isinstance(client, HermesChatClient):
        client = HermesChatClient(
            base_url=_agent_base(),
            host_header=_agent_host_header(),
            cwd=_agent_cwd(),
            stored_session_id=stored_session_id,
        )
        st.session_state["hermes_chat_client"] = client
    return client


def _hermes_page_header() -> None:
    left, right = st.columns([5, 1.2], vertical_alignment="center")
    with left:
        st.markdown(
            '<div class="page-heading"><h1>问 Hermes</h1><p>研究持仓、观察池，或任何其他股票</p></div>',
            unsafe_allow_html=True,
        )
    with right:
        if st.button(
            "新建会话",
            icon=":material/add_comment:",
            width="stretch",
            key="new_hermes_conversation",
        ):
            _new_hermes_conversation()
            st.rerun()


def _hermes_welcome() -> None:
    st.markdown(
        """
        <div class="hermes-welcome">
          <div class="hermes-welcome-mark">H</div>
          <div>
            <strong>把标的和问题直接交给 Hermes</strong>
            <p>系统内标的优先查询可审计的 Backend 事实；尚未录入的股票会检索公开来源，并明确说明来源、时点和缺口。</p>
            <small>Hermes 可以研究、比较和解释，但不会连接券商、自动下单或修改 REAL 账本。</small>
          </div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def _hermes_context_bar() -> None:
    context = st.session_state.get("hermes_context_instrument")
    if not isinstance(context, dict) or not context.get("instrument_id"):
        return
    st.markdown(
        f'<div class="hermes-context"><span>当前研究标的</span><strong>{escape(str(context.get("symbol") or "—"))} · {escape(str(context.get("name") or "—"))}</strong>'
        '<small>对话会自动保存；写入研究档案仍需你逐次确认。</small></div>',
        unsafe_allow_html=True,
    )


def _set_hermes_artifact_action(action: str) -> None:
    st.session_state["hermes_artifact_action"] = action


def _hermes_artifact_actions(messages: list[dict[str, Any]]) -> None:
    latest = next(
        (str(row.get("content") or "") for row in reversed(messages) if row.get("role") == "assistant"),
        "",
    )
    if not latest:
        return
    instruments_result = _all_instruments()
    instruments = (
        instruments_result.data
        if instruments_result.ok and isinstance(instruments_result.data, list)
        else []
    )
    instruments = [item for item in instruments if item.get("instrument_type") != "INDEX"]
    if not instruments:
        st.caption("需要先在“标的”页登记证券，才能把对话内容写入长期研究档案。")
        return
    by_id = {str(item.get("instrument_id")): item for item in instruments}
    context = st.session_state.get("hermes_context_instrument") or {}
    preferred = str(context.get("instrument_id") or "")
    if st.session_state.get("hermes_artifact_target") not in by_id:
        st.session_state["hermes_artifact_target"] = preferred if preferred in by_id else next(iter(by_id))

    with st.expander("将本轮结论用于长期研究"):
        target_id = st.selectbox(
            "关联标的",
            list(by_id),
            format_func=lambda value: f"{by_id[value].get('symbol')} · {by_id[value].get('name')}",
            key="hermes_artifact_target",
        )
        target = by_id[target_id]
        action_columns = st.columns(4)
        actions = [
            ("研究笔记", "note", ":material/note_add:"),
            ("纳入投资观点", "thesis", ":material/fact_check:"),
            ("建立估值", "valuation", ":material/calculate:"),
            ("跟踪事项", "tracking", ":material/flag:"),
        ]
        for column, (label, action, icon) in zip(action_columns, actions, strict=True):
            with column:
                if action == "valuation":
                    st.button(
                        label,
                        icon=icon,
                        width="stretch",
                        on_click=_navigate,
                        args=("标的", target, None),
                        key="hermes_action_valuation",
                    )
                else:
                    st.button(
                        label,
                        icon=icon,
                        width="stretch",
                        on_click=_set_hermes_artifact_action,
                        args=(action,),
                        key=f"hermes_action_{action}",
                    )

        action = st.session_state.get("hermes_artifact_action")
        if action not in {"note", "thesis", "tracking"}:
            st.caption("以上动作都不会自动执行；选择后先显示可编辑预览。")
            return
        action_labels = {
            "note": "研究笔记",
            "thesis": "投资观点新版本",
            "tracking": "跟踪事项",
        }
        default_title = {
            "note": f"Hermes 研究记录 · {target.get('name')}",
            "thesis": f"Hermes 研究补充 · {target.get('name')}",
            "tracking": f"待跟踪 · {target.get('name')}",
        }[action]
        st.markdown(f"**预览并确认：{action_labels[action]}**")
        title = st.text_input(
            "标题",
            value=default_title,
            key=f"hermes_artifact_title_{action}",
        )
        body = st.text_area(
            "正文",
            value=latest,
            height=220,
            key=f"hermes_artifact_body_{action}",
        )
        if action == "tracking":
            st.info("此动作保存一条待跟踪研究事项，不会创建自动提醒、连接券商或触发交易。")
        confirm, cancel = st.columns([1, 1])
        with confirm:
            if st.button(
                "确认写入",
                type="primary",
                icon=":material/check_circle:",
                width="stretch",
                key=f"confirm_hermes_artifact_{action}",
            ):
                if not title.strip() or not body.strip():
                    st.error("标题和正文不能为空。")
                elif action in {"note", "tracking"}:
                    note_body = body.strip()
                    if action == "tracking":
                        note_body = "**跟踪状态：待人工复核**\n\n" + note_body
                    result = _write(
                        "POST",
                        "/v1/research/notes",
                        payload={
                            "title": title.strip(),
                            "body_md": note_body,
                            "instrument_id": target_id,
                        },
                    )
                    if result.ok:
                        st.session_state.pop("hermes_artifact_action", None)
                        st.toast("已写入研究档案。")
                        st.rerun()
                    else:
                        st.error(result.message or "研究档案写入失败。")
                else:
                    detail = _get(f"/v1/office/instruments/{target_id}")
                    if not detail.ok:
                        st.error(detail.message or "无法读取当前投资观点。")
                    else:
                        thesis = detail.data.get("thesis")
                        if thesis:
                            existing_body = thesis.get("body") if isinstance(thesis.get("body"), dict) else {}
                            updates = list(existing_body.get("hermes_updates") or [])
                            updates.append(f"{_local_today().isoformat()} · {title.strip()}\n\n{body.strip()}")
                            result = _write(
                                "POST",
                                f"/v1/theses/{thesis.get('thesis_id')}/revisions",
                                payload={
                                    "base_revision_id": thesis.get("revision_id"),
                                    "change_reason": title.strip(),
                                    "thesis_body": {**existing_body, "hermes_updates": updates},
                                    "freshness": "OK",
                                },
                            )
                        else:
                            result = _write(
                                "POST",
                                "/v1/theses",
                                payload={
                                    "instrument_id": target_id,
                                    "title": title.strip(),
                                    "body": {
                                        "status": "待研究",
                                        "notice": "该版本由用户从 Hermes 对话中明确确认写入。",
                                        "hermes_updates": [body.strip()],
                                        "investment_case": [],
                                        "assumptions": [],
                                        "risks": [],
                                    },
                                },
                            )
                        if result.ok:
                            st.session_state.pop("hermes_artifact_action", None)
                            st.toast("投资观点新版本已写入。")
                            st.rerun()
                        else:
                            st.error(result.message or "投资观点写入失败。")
        with cancel:
            if st.button(
                "取消",
                width="stretch",
                key=f"cancel_hermes_artifact_{action}",
            ):
                st.session_state.pop("hermes_artifact_action", None)
                st.rerun()


def _hermes_chat_page() -> None:
    _hermes_page_header()
    health = check_agent_health(_agent_base(), _agent_host_header())
    if not health.ok:
        st.markdown(
            f"""
            <div class="state-card state-error compact">
              <strong>Hermes 对话服务未连接</strong>
              <span>运行 <code>./scripts/hermes start</code> 后刷新本页。{escape(health.message)}</span>
              <code>HERMES_AGENT_UNAVAILABLE</code>
            </div>
            """,
            unsafe_allow_html=True,
        )
        return

    st.markdown(
        f"""
        <div class="hermes-connection" role="status">
          <span class="status-dot status-ok"></span>
          <strong>Hermes Agent 已连接</strong>
          <span>v{escape(health.version or '—')} · 会话自动保存 · 仅本机</span>
        </div>
        """,
        unsafe_allow_html=True,
    )

    client = _hermes_client()
    _hermes_history_panel(client)
    _hermes_context_bar()
    messages = st.session_state.setdefault("hermes_chat_messages", [])
    stored_session_id = _query_session_id()
    if stored_session_id and st.session_state.get("hermes_loaded_session") != stored_session_id:
        try:
            with st.spinner("正在恢复 Hermes 会话…"):
                restored = client.connect()
            messages[:] = restored
            st.session_state["hermes_loaded_session"] = client.stored_session_id
            _remember_query_session(client.stored_session_id)
        except HermesChatError as exc:
            client.close()
            st.session_state.pop("hermes_chat_client", None)
            st.error(f"无法恢复这段会话：{exc}")
            if st.button("开始新会话", type="primary", key="replace_missing_hermes_session"):
                _new_hermes_conversation()
                st.rerun()
            return

    prompt_from_suggestion = str(st.session_state.pop("hermes_pending_prompt", "") or "")
    if not messages:
        _hermes_welcome()
        suggestion_columns = st.columns(3)
        suggestions = (
            "研究 600519.SH 的长期投资逻辑",
            "复核 513650.SH 最近有什么变化",
            "研究 NVDA，并注明外部数据来源",
        )
        for column, suggestion in zip(suggestion_columns, suggestions, strict=True):
            with column:
                if st.button(suggestion, width="stretch", key=f"hermes_suggestion_{suggestion}"):
                    prompt_from_suggestion = suggestion

    for message in messages:
        avatar = ":material/person:" if message["role"] == "user" else ":material/auto_awesome:"
        with st.chat_message(message["role"], avatar=avatar):
            st.markdown(message["content"])

    _hermes_artifact_actions(messages)

    typed_prompt = st.chat_input(
        "输入股票代码、名称或研究问题…",
        max_chars=12000,
        key="hermes_chat_input",
    )
    prompt = prompt_from_suggestion or typed_prompt
    if not prompt:
        return

    messages.append({"role": "user", "content": prompt})
    with st.chat_message("user", avatar=":material/person:"):
        st.markdown(prompt)

    status_line = None
    try:
        if not client.session_id:
            client.connect()
        _remember_query_session(client.stored_session_id)
        st.session_state["hermes_loaded_session"] = client.stored_session_id

        with st.chat_message("assistant", avatar=":material/auto_awesome:"):
            status_line = st.empty()
            status_line.caption("Hermes 正在理解问题…")

            def on_event(event_type: str, payload: dict[str, Any]) -> None:
                if event_type == "tool.start":
                    name = payload.get("name") or payload.get("tool_name") or "研究工具"
                    status_line.caption(f"Hermes 正在调用 {name}…")
                elif event_type == "tool.complete":
                    status_line.caption("Hermes 正在整理证据…")

            answer = st.write_stream(client.stream_reply(prompt, on_event=on_event))
            status_line.empty()
        if not isinstance(answer, str) or not answer.strip():
            answer = "Hermes 已完成处理，但没有返回可显示的文字。"
        messages.append({"role": "assistant", "content": answer})
        st.rerun()
    except HermesChatError as exc:
        if status_line is not None:
            status_line.empty()
        st.error(f"这次对话没有完成：{exc}")


def _sidebar() -> tuple[str, date]:
    with st.sidebar:
        st.markdown(
            '<div class="brand"><div class="brand-mark">H</div><div><strong>Hermes</strong><span>Investment Office</span></div></div>',
            unsafe_allow_html=True,
        )
        labels = ["今日", "标的", "组合", "问 Hermes"]
        if "main_navigation" not in st.session_state and _query_session_id():
            st.session_state["main_navigation"] = labels[-1]
        selected = st.radio("主导航", labels, label_visibility="collapsed", key="main_navigation")
        page = selected
        st.markdown('<div class="sidebar-divider"></div>', unsafe_allow_html=True)
        as_of = _local_today()
        st.caption("默认始终显示最新数据；历史快照只在标的或组合内开启。")
        status = _get("/v1/office/system", as_of=as_of.isoformat())
        if status.ok:
            data = status.data
            freshness = data.get("freshness") or "FAILED"
            css = freshness.lower()
            st.markdown(
                f'<div class="sidebar-status"><span class="status-dot status-{escape(css)}"></span><div><strong>系统 {escape(str(data.get("backend") or "—"))}</strong>'
                f'<small>数据新鲜度 {escape(str(freshness))} · 失败任务 {data.get("failed_jobs", 0)}</small></div></div>',
                unsafe_allow_html=True,
            )
            with st.expander("运行状态"):
                st.caption(f"Backend 调度：{'已启用' if data.get('scheduler_enabled') else '未启用'}")
                st.caption(f"每日 {data.get('scheduler_time')} · {data.get('scheduler_timezone')}")
                st.caption(f"每日上下文：{data.get('daily_context_date') or '尚未生成'}")
        else:
            st.markdown(
                '<div class="sidebar-status"><span class="status-dot status-failed"></span><div><strong>Backend 未连接</strong><small>手工写入暂不可用</small></div></div>',
                unsafe_allow_html=True,
            )
        st.markdown('<div class="sidebar-foot">本地优先 · 无券商连接</div>', unsafe_allow_html=True)
    return page, as_of


def main() -> None:
    page, as_of = _sidebar()
    if page == "今日":
        _today_page(as_of)
    elif page == "标的":
        _watchlist_page(as_of)
    elif page == "组合":
        _portfolio_page(as_of)
    else:
        _hermes_chat_page()


if __name__ == "__main__":
    main()
