# backend/app/calendar/models.py —— 模块归属：calendar（Trading Calendar）
from datetime import date, datetime
from uuid import UUID

from sqlalchemy import Boolean, Date, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.common.base import Base
from app.common.db import enum_ck
from app.common.enums import MarketCode, SessionStatus
from app.common.mixins import CreatedAtMixin, UUIDPrimaryKeyMixin

pk = UUIDPrimaryKeyMixin.pk


class TradingCalendarEntry(Base, CreatedAtMixin):
    """交易日历（可维护：人工校准 + 来源同步）。提供 is_trading_day / next_trading_day 确定性接口
    （冻结规范 §17）。"""
    __tablename__ = "trading_calendar"
    __table_args__ = (
        UniqueConstraint("market", "trade_date", name="uq_trading_calendar_market_date"),
        enum_ck("trading_calendar", "market", MarketCode),
        enum_ck("trading_calendar", "session_status", SessionStatus),
    )
    calendar_entry_id: Mapped[UUID] = pk("calendar_entry_id")
    market: Mapped[MarketCode] = mapped_column(Text, nullable=False)      # CN / US
    trade_date: Mapped[date] = mapped_column(Date, nullable=False)
    is_trading_day: Mapped[bool] = mapped_column(Boolean, nullable=False)
    session_status: Mapped[SessionStatus] = mapped_column(Text, nullable=False)  # OPEN / CLOSED / PARTIAL
    holiday_name: Mapped[str | None] = mapped_column(Text)
    source: Mapped[str] = mapped_column(Text, nullable=False)             # 数据来源


