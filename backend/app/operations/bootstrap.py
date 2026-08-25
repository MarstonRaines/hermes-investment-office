"""Hermes Investment Office 的幂等产品默认值。

这里只创建本地身份、观察池和手工账本，不访问外部网络，也不写任何示例行情。
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.common.enums import PortfolioMode
from app.etf.models import ETFProfile
from app.instruments.models import Instrument, ProviderSymbol
from app.instruments.service import WatchlistService
from app.portfolio.models import Portfolio, PortfolioSnapshot
from app.portfolio.service import PortfolioService

_ETF_DEFAULTS = (
    {
        "symbol": "510300",
        "name": "华泰柏瑞沪深300ETF",
        "fund_manager": "华泰柏瑞基金",
        "fund_name": "华泰柏瑞沪深300交易型开放式指数证券投资基金",
        "tracking_index": "沪深300",
        "is_qdii": False,
    },
    {
        "symbol": "513650",
        "name": "南方标普500ETF(QDII)",
        "fund_manager": "南方基金",
        "fund_name": "南方标普500交易型开放式指数证券投资基金（QDII）",
        "tracking_index": "标普500",
        "is_qdii": True,
    },
    {
        "symbol": "512890",
        "name": "华泰柏瑞中证红利低波动ETF",
        "fund_manager": "华泰柏瑞基金",
        "fund_name": "华泰柏瑞中证红利低波动交易型开放式指数证券投资基金",
        "tracking_index": "中证红利低波动",
        "is_qdii": False,
    },
)


def _ensure_instrument(
    session: Session,
    *,
    symbol: str,
    name: str,
    instrument_type: str,
    market: str = "SSE",
    exchange: str = "SSE",
    lot_size: Decimal | None = None,
) -> tuple[Instrument, bool]:
    row = session.scalar(select(Instrument).where(
        Instrument.symbol == symbol,
        Instrument.market == market,
    ))
    if row is not None:
        if str(row.instrument_type) != instrument_type:
            raise ValueError(f"{market}:{symbol} 的资产类型不是 {instrument_type}")
        return row, False
    row = Instrument(
        instrument_id=uuid4(),
        instrument_type=instrument_type,
        symbol=symbol,
        name=name,
        market=market,
        exchange=exchange,
        currency="CNY",
        lot_size=lot_size,
        status="ACTIVE",
        version=1,
    )
    session.add(row)
    session.flush()
    return row, True


def _ensure_provider_symbol(
    session: Session, instrument: Instrument, provider: str, symbol: str,
) -> bool:
    existing = session.scalar(select(ProviderSymbol).where(
        ProviderSymbol.instrument_id == instrument.instrument_id,
        ProviderSymbol.provider == provider,
        ProviderSymbol.valid_to.is_(None),
    ).limit(1))
    if existing is not None:
        return False
    session.add(ProviderSymbol(
        provider_symbol_id=uuid4(),
        instrument_id=instrument.instrument_id,
        provider=provider,
        symbol=symbol,
        valid_from=date(2020, 1, 1),
    ))
    return True


def ensure_product_defaults(session: Session) -> dict:
    """创建用户已确认的默认产品形态；重复执行不会复制数据。"""

    created_instruments = 0
    created_mappings = 0
    index, created = _ensure_instrument(
        session,
        symbol="SPX",
        name="标普500指数",
        instrument_type="INDEX",
        exchange="INDEX",
    )
    created_instruments += int(created)
    created_mappings += int(_ensure_provider_symbol(session, index, "yahoo", "^GSPC"))

    etfs: list[Instrument] = []
    for item in _ETF_DEFAULTS:
        instrument, created = _ensure_instrument(
            session,
            symbol=item["symbol"],
            name=item["name"],
            instrument_type="CN_ETF",
            lot_size=Decimal("100"),
        )
        created_instruments += int(created)
        etfs.append(instrument)
        provider_symbol = f"{item['symbol']}.SH"
        for provider in ("tushare", "akshare_sina", "akshare_eastmoney"):
            created_mappings += int(
                _ensure_provider_symbol(session, instrument, provider, provider_symbol)
            )
        profile = session.get(ETFProfile, instrument.instrument_id)
        if profile is None:
            profile = ETFProfile(
                instrument_id=instrument.instrument_id,
                is_qdii=item["is_qdii"],
                underlying_index_id=index.instrument_id if item["is_qdii"] else None,
                fund_manager=item["fund_manager"],
                fund_name=item["fund_name"],
                tracking_index_name=item["tracking_index"],
            )
            session.add(profile)
        elif bool(profile.is_qdii) != item["is_qdii"]:
            raise ValueError(f"{item['symbol']} 的 QDII 属性与产品默认值冲突")

    watchlist_service = WatchlistService(session)
    watchlist = watchlist_service.ensure_default_watchlist(
        name="核心观察池",
        description="用户确认的初始 ETF 观察池；可随时手工增删。",
    )
    for instrument in etfs:
        watchlist_service.add_member(
            watchlist.watchlist_id,
            instrument.instrument_id,
            note="初始观察标的",
        )

    portfolio = session.scalar(select(Portfolio).where(
        Portfolio.mode == PortfolioMode.REAL.value,
        Portfolio.status == "ACTIVE",
    ).order_by(Portfolio.created_at.asc()).limit(1))
    portfolio_created = portfolio is None
    if portfolio is None:
        portfolio = PortfolioService().create_portfolio(
            session,
            "我的投资组合",
            mode=PortfolioMode.REAL,
        )
    latest_snapshot = session.scalar(select(PortfolioSnapshot).where(
        PortfolioSnapshot.portfolio_id == portfolio.portfolio_id,
    ).order_by(PortfolioSnapshot.snapshot_date.desc()).limit(1))
    if latest_snapshot is None:
        PortfolioService().snapshot(session, portfolio.portfolio_id, date.today(), {})

    session.commit()
    return {
        "created_instruments": created_instruments,
        "created_provider_symbols": created_mappings,
        "watchlist_id": str(watchlist.watchlist_id),
        "portfolio_id": str(portfolio.portfolio_id),
        "portfolio_created": portfolio_created,
    }
