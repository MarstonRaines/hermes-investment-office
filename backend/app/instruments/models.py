# backend/app/instruments/models.py —— 模块归属：instruments（Instrument Master）
from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import Date, ForeignKey, Index, Integer, Text, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.common.base import Base
from app.common.db import enum_ck
from app.common.enums import InstrumentStatus, InstrumentType, WatchlistStatus
from app.common.mixins import CreatedAtMixin, TimestampMixin, UUIDPrimaryKeyMixin
from app.common.types import LOT, TIMESTAMPTZ

pk = UUIDPrimaryKeyMixin.pk


class Instrument(Base, TimestampMixin):
    """统一资产身份（SoT）。identity 不可变；属性演进走 versioned update（乐观锁 version）。"""
    __tablename__ = "instruments"
    __table_args__ = (
        UniqueConstraint("symbol", "market", name="uq_instruments_symbol_market"),
        enum_ck("instruments", "instrument_type", InstrumentType),
        enum_ck("instruments", "status", InstrumentStatus),
    )
    instrument_id: Mapped[UUID] = pk("instrument_id")
    instrument_type: Mapped[InstrumentType] = mapped_column(Text, nullable=False)
    symbol: Mapped[str] = mapped_column(Text, nullable=False)       # 交易所代码（展示用），不是主键
    name: Mapped[str] = mapped_column(Text, nullable=False)
    market: Mapped[str] = mapped_column(Text, nullable=False)       # SSE / SZSE（边界层校验，ts02 未冻结 DB CHECK）
    exchange: Mapped[str | None] = mapped_column(Text)              # 冗余保留
    currency: Mapped[str] = mapped_column(Text, nullable=False, server_default="CNY")
    lot_size: Mapped[Decimal | None] = mapped_column(LOT)           # 一手股数
    status: Mapped[InstrumentStatus] = mapped_column(Text, nullable=False, server_default="ACTIVE")
    isin: Mapped[str | None] = mapped_column(Text)                  # 预留
    version: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("1"))  # 乐观并发

    provider_symbols: Mapped[list["ProviderSymbol"]] = relationship(back_populates="instrument")
    watchlist_members: Mapped[list["WatchlistMember"]] = relationship(
        back_populates="instrument"
    )


class ProviderSymbol(Base, CreatedAtMixin):
    """Provider 代码映射（时态：valid_from / valid_to，NULL=当前有效）。"""
    __tablename__ = "provider_symbols"
    __table_args__ = (
        UniqueConstraint("provider", "symbol", "valid_from",
                         name="uq_provider_symbols_provider_sym_from"),
        Index("ix_provider_symbols_instrument", "instrument_id"),
        Index("ix_provider_symbols_lookup", "provider", "symbol",
              postgresql_where=text("valid_to IS NULL")),           # resolve_instrument 主路径
    )
    provider_symbol_id: Mapped[UUID] = pk("provider_symbol_id")
    instrument_id: Mapped[UUID] = mapped_column(
        ForeignKey("instruments.instrument_id"), nullable=False)
    provider: Mapped[str] = mapped_column(Text, nullable=False)     # tushare / akshare / yahoo / cninfo / internal
    symbol: Mapped[str] = mapped_column(Text, nullable=False)       # 600519.SH / ^NDX ...
    valid_from: Mapped[date] = mapped_column(Date, nullable=False)
    valid_to: Mapped[date | None] = mapped_column(Date)

    instrument: Mapped[Instrument] = relationship(back_populates="provider_symbols")


class Watchlist(Base, TimestampMixin):
    """观察池实体（ADR-006；Instrument Master 负责身份与所有权登记）。"""

    __tablename__ = "watchlists"
    __table_args__ = (
        enum_ck("watchlists", "status", WatchlistStatus),
    )

    watchlist_id: Mapped[UUID] = pk("watchlist_id")
    name: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    status: Mapped[WatchlistStatus] = mapped_column(
        Text, nullable=False, server_default=WatchlistStatus.ACTIVE.value
    )

    members: Mapped[list["WatchlistMember"]] = relationship(
        back_populates="watchlist"
    )


class WatchlistMember(Base, CreatedAtMixin):
    """观察池成员关系；移除通过 removed_at 标记，不物理删除。"""

    __tablename__ = "watchlist_members"
    __table_args__ = (
        UniqueConstraint(
            "watchlist_id", "instrument_id",
            name="uq_watchlist_members_watchlist_instrument",
        ),
        Index("ix_watchlist_members_active", "watchlist_id", "removed_at"),
    )

    watchlist_member_id: Mapped[UUID] = pk("watchlist_member_id")
    watchlist_id: Mapped[UUID] = mapped_column(
        ForeignKey("watchlists.watchlist_id"), nullable=False
    )
    instrument_id: Mapped[UUID] = mapped_column(
        ForeignKey("instruments.instrument_id"), nullable=False
    )
    added_at: Mapped[datetime] = mapped_column(
        TIMESTAMPTZ, nullable=False, server_default=text("now()")
    )
    removed_at: Mapped[datetime | None] = mapped_column(TIMESTAMPTZ)
    note: Mapped[str | None] = mapped_column(Text)

    watchlist: Mapped[Watchlist] = relationship(back_populates="members")
    instrument: Mapped[Instrument] = relationship(back_populates="watchlist_members")
