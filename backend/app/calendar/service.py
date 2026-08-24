# =====================================================================
# backend/app/calendar/service.py —— Trading Calendar 服务（冻结规范 §17）
#
# ACC-M1-007：交易日历确定性接口（is_trading_day / next_trading_day）。
# 日历可维护：来源同步（upsert）+ 人工校准（source 字段区分）。
# =====================================================================
from __future__ import annotations

from datetime import date
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.calendar.models import TradingCalendarEntry
from app.common.enums import MarketCode, SessionStatus

__all__ = ["CalendarService"]


class CalendarService:
    def __init__(self) -> None:
        pass

    def sync_dates(
        self,
        session: Session,
        trade_dates: list[date],
        *,
        market: MarketCode = MarketCode.CN,
        source: str = "sina_calendar",
    ) -> int:
        """来源同步：upsert 到 trading_calendar（幂等）。返回写入行数。"""
        written = 0
        for d in trade_dates:
            stmt = insert(TradingCalendarEntry).values(
                calendar_entry_id=uuid4(),
                market=market.value,
                trade_date=d,
                is_trading_day=True,
                session_status=SessionStatus.OPEN.value,
                source=source,
            ).on_conflict_do_update(
                constraint="uq_trading_calendar_market_date",
                set_={"is_trading_day": True, "source": source},
            )
            written += session.execute(stmt).rowcount
        return written

    def is_trading_day(self, session: Session, d: date, market: MarketCode = MarketCode.CN) -> bool:
        """确定性接口：该日是否交易日。日历无该日记录 → False（保守）。"""
        row = session.execute(
            select(TradingCalendarEntry.is_trading_day).where(
                TradingCalendarEntry.market == market.value,
                TradingCalendarEntry.trade_date == d,
            )
        ).first()
        return bool(row and row[0])

    def next_trading_day(self, session: Session, d: date, market: MarketCode = MarketCode.CN) -> date | None:
        """d 之后（不含 d）第一个交易日；日历尽头返回 None。"""
        row = session.execute(
            select(TradingCalendarEntry.trade_date)
            .where(
                TradingCalendarEntry.market == market.value,
                TradingCalendarEntry.trade_date > d,
                TradingCalendarEntry.is_trading_day.is_(True),
            )
            .order_by(TradingCalendarEntry.trade_date.asc())
            .limit(1)
        ).first()
        return row[0] if row else None

    def prev_trading_day(self, session: Session, d: date, market: MarketCode = MarketCode.CN) -> date | None:
        """d 之前（不含 d）最近交易日。"""
        row = session.execute(
            select(TradingCalendarEntry.trade_date)
            .where(
                TradingCalendarEntry.market == market.value,
                TradingCalendarEntry.trade_date < d,
                TradingCalendarEntry.is_trading_day.is_(True),
            )
            .order_by(TradingCalendarEntry.trade_date.desc())
            .limit(1)
        ).first()
        return row[0] if row else None

    def trading_day_distance(
        self,
        session: Session,
        left: date | None,
        right: date | None,
        *,
        market: MarketCode = MarketCode.CN,
    ) -> int | None:
        """Return the distance between two dates using persisted calendar rows.

        Missing calendar coverage is deliberately ``None``.  Callers must turn
        that into an explicit alignment flag instead of silently guessing with
        weekdays.
        """
        if left is None or right is None:
            return None
        if left == right:
            return 0 if self.is_trading_day(session, left, market) else None
        start, end = sorted((left, right))
        rows = session.scalars(
            select(TradingCalendarEntry.trade_date)
            .where(
                TradingCalendarEntry.market == market.value,
                TradingCalendarEntry.trade_date >= start,
                TradingCalendarEntry.trade_date <= end,
                TradingCalendarEntry.is_trading_day.is_(True),
            )
            .order_by(TradingCalendarEntry.trade_date.asc())
        ).all()
        if start not in rows or end not in rows:
            return None
        return abs(rows.index(end) - rows.index(start))
