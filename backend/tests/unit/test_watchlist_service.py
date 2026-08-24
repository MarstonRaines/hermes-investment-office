from __future__ import annotations

from datetime import date
from decimal import Decimal
from uuid import uuid4

import pytest

from app.instruments.models import Instrument, Watchlist
from app.instruments.service import (
    WatchlistArchivedError,
    WatchlistPermissionError,
    WatchlistService,
)
from app.portfolio.models import Portfolio, PositionSnapshot


def _instrument(session, suffix: str) -> Instrument:
    row = Instrument(
        instrument_type="CN_ETF", symbol=f"W{suffix}{uuid4().hex[:4]}",
        name=f"观察池测试 {suffix}", market="SSE", currency="CNY",
    )
    session.add(row)
    session.flush()
    return row


def test_member_temporal_state_and_permissions(db_session) -> None:
    watchlist = Watchlist(watchlist_id=uuid4(), name="测试池", status="ACTIVE")
    instrument = _instrument(db_session, "A")
    db_session.add(watchlist)
    db_session.flush()
    service = WatchlistService(db_session)

    member = service.add_member(
        watchlist.watchlist_id, instrument.instrument_id, permission="RESEARCH_WRITE"
    )
    assert service.current_instrument_ids(watchlist.watchlist_id) == {instrument.instrument_id}
    with pytest.raises(WatchlistPermissionError):
        service.add_member(
            watchlist.watchlist_id, instrument.instrument_id, permission="READ"
        )

    removed = service.remove_member(
        watchlist.watchlist_id, instrument.instrument_id, permission="RESEARCH_WRITE"
    )
    assert removed.removed_at is not None
    assert service.current_instrument_ids(watchlist.watchlist_id) == set()
    assert service.list_members(watchlist.watchlist_id, include_removed=True)[0].removed_at is not None
    reactivated = service.add_member(
        watchlist.watchlist_id, instrument.instrument_id, permission="RESEARCH_WRITE"
    )
    assert reactivated.watchlist_member_id == member.watchlist_member_id
    assert reactivated.removed_at is None

    watchlist.status = "ARCHIVED"
    with pytest.raises(WatchlistArchivedError):
        service.remove_member(
            watchlist.watchlist_id, instrument.instrument_id, permission="RESEARCH_WRITE"
        )


def test_daily_universe_is_watchlist_union_latest_real_holdings(db_session) -> None:
    watchlist = Watchlist(watchlist_id=uuid4(), name="空池", status="ACTIVE")
    watched = _instrument(db_session, "WATCH")
    stale = _instrument(db_session, "STALE")
    held = _instrument(db_session, "HELD")
    portfolio = Portfolio(
        portfolio_id=uuid4(), name="真实组合", mode="REAL", status="ACTIVE",
        base_currency="CNY",
    )
    db_session.add_all([watchlist, portfolio])
    db_session.flush()
    db_session.add_all([
        PositionSnapshot(
            position_snapshot_id=uuid4(), portfolio_id=portfolio.portfolio_id,
            instrument_id=stale.instrument_id, snapshot_date=date(2026, 8, 20),
            quantity=Decimal("10"), cost_basis_cny=Decimal("100"),
            market_price_cny=Decimal("10"), market_value_cny=Decimal("100"),
            engine_version="test",
        ),
        PositionSnapshot(
            position_snapshot_id=uuid4(), portfolio_id=portfolio.portfolio_id,
            instrument_id=stale.instrument_id, snapshot_date=date(2026, 8, 21),
            quantity=Decimal("0"), cost_basis_cny=Decimal("0"),
            market_price_cny=Decimal("10"), market_value_cny=Decimal("0"),
            engine_version="test",
        ),
        PositionSnapshot(
            position_snapshot_id=uuid4(), portfolio_id=portfolio.portfolio_id,
            instrument_id=held.instrument_id, snapshot_date=date(2026, 8, 21),
            quantity=Decimal("2"), cost_basis_cny=Decimal("20"),
            market_price_cny=Decimal("10"), market_value_cny=Decimal("20"),
            engine_version="test",
        ),
    ])
    db_session.flush()
    service = WatchlistService(db_session)
    service.add_member(
        watchlist.watchlist_id, watched.instrument_id, permission="RESEARCH_WRITE"
    )

    universe = service.daily_universe_for_date(
        watchlist.watchlist_id, date(2026, 8, 21)
    )
    assert universe == {watched.instrument_id, held.instrument_id}

    empty_watchlist = Watchlist(watchlist_id=uuid4(), name="真正空池", status="ACTIVE")
    db_session.add(empty_watchlist)
    db_session.flush()
    assert service.daily_universe_for_date(
        empty_watchlist.watchlist_id, date(2026, 8, 21)
    ) == {held.instrument_id}
