# =====================================================================
# tests/unit/test_calendar_service.py —— Trading Calendar（ACC-M1-007）
# =====================================================================
from __future__ import annotations

from datetime import date

import app.models  # noqa: F401

from app.calendar.service import CalendarService
from app.common.enums import MarketCode


def test_sync_and_deterministic_interfaces(db_session) -> None:
    """ACC-M1-007：is_trading_day / next_trading_day 确定性接口。"""
    svc = CalendarService()
    dates = [date(2026, 8, 20), date(2026, 8, 21), date(2026, 8, 24)]   # 周四/周五/周一
    n = svc.sync_dates(db_session, dates)
    db_session.flush()
    assert n == 3
    assert svc.is_trading_day(db_session, date(2026, 8, 21)) is True
    assert svc.is_trading_day(db_session, date(2026, 8, 22)) is False   # 周六
    assert svc.next_trading_day(db_session, date(2026, 8, 21)) == date(2026, 8, 24)
    assert svc.prev_trading_day(db_session, date(2026, 8, 24)) == date(2026, 8, 21)


def test_sync_idempotent(db_session) -> None:
    svc = CalendarService()
    dates = [date(2026, 8, 20)]
    svc.sync_dates(db_session, dates)
    db_session.flush()
    n2 = svc.sync_dates(db_session, dates)
    db_session.flush()
    assert n2 == 1   # upsert 更新
    from app.calendar.models import TradingCalendarEntry

    assert db_session.query(TradingCalendarEntry).count() == 1


def test_market_isolation(db_session) -> None:
    svc = CalendarService()
    svc.sync_dates(db_session, [date(2026, 8, 21)], market=MarketCode.CN)
    db_session.flush()
    assert svc.is_trading_day(db_session, date(2026, 8, 21), market=MarketCode.US) is False
    svc.sync_dates(db_session, [date(2026, 8, 21)], market=MarketCode.US)
    db_session.flush()
    assert svc.is_trading_day(db_session, date(2026, 8, 21), market=MarketCode.US) is True
