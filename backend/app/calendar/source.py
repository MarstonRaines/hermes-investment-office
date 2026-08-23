# =====================================================================
# backend/app/calendar/source.py —— 交易日历数据源（S9 实测：新浪）
#
# S9：新浪 tool_trade_date_hist_sina 8797 行（2000-01-04 ~ 2026 全量 A 股
# 交易日）；直连正常（network=direct）。年度更新机制：同步 job 按年增量。
# 美股日历：v0.1 从 Yahoo 交易日派生或 PENDING 补充源（S9）。
# =====================================================================
from __future__ import annotations

from datetime import date

__all__ = ["fetch_sina_trade_dates"]

try:
    import akshare as ak
except ImportError:  # pragma: no cover
    ak = None  # type: ignore[assignment]


def fetch_sina_trade_dates() -> list[date]:
    """新浪 A 股交易日历全量（2000-01-04 起）。失败抛异常由 job 层处理。"""
    if ak is None:
        raise RuntimeError("akshare 未安装")
    df = ak.tool_trade_date_hist_sina()
    if df is None or df.empty:
        return []
    col = "trade_date" if "trade_date" in df.columns else df.columns[0]
    out: list[date] = []
    for v in df[col]:
        if hasattr(v, "date"):
            out.append(v.date())
        else:
            out.append(date.fromisoformat(str(v)[:10]))
    return sorted(out)
