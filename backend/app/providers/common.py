# =====================================================================
# backend/app/providers/common.py —— Provider 共享助手（时区/单位/provenance）
#
# 冻结约束（TS-05 §2.7 / TS-04 §4）：
# - 时间戳一律 UTC 存储；业务日期 DATE 不带时区；
# - observed_at：行情 = trade_date 15:00 Asia/Shanghai（A 股收盘）；
#   美股指数 = 16:00 America/New_York（美股收盘）。
# =====================================================================
from __future__ import annotations

from datetime import UTC, date, datetime, time
from zoneinfo import ZoneInfo

__all__ = [
    "shanghai_15",
    "ny_close",
    "as_utc",
    "pct_or_none",
]

_SH = ZoneInfo("Asia/Shanghai")
_NY = ZoneInfo("America/New_York")


def shanghai_15(d: date) -> datetime:
    """A 股交易日 15:00 Asia/Shanghai → UTC（TS-05 §2.1 observed_at 规则）。"""
    return datetime.combine(d, time(15, 0), tzinfo=_SH).astimezone(UTC)


def ny_close(d: date) -> datetime:
    """美股交易日 16:00 America/New_York → UTC（TS-05 §2.5 observed_at 规则）。"""
    return datetime.combine(d, time(16, 0), tzinfo=_NY).astimezone(UTC)


def as_utc(dt: datetime) -> datetime:
    """归一化到 UTC；naive 视为 UTC。"""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


def pct_or_none(v) -> float | None:
    """数值安全转换（None/NaN/空串 → None）。"""
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    if f != f:  # NaN
        return None
    return f
