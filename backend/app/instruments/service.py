"""Instrument Master 服务（第一等领域，冻结规范 §10 / TS-01 所有权）。

Write Authority（TS-01 §3）：
- instruments / provider_symbols / etf_profiles 的唯一写入者；
- Instrument 身份（instrument_id）不可变；属性 versioned update（乐观锁）；
- 禁止以 Provider Symbol 作为内部主键（施工纪律第 9 条）。

跨表约束（ts02 §3.3）：is_qdii=true 时 underlying_index_id 必填且指向 INDEX；
服务层验证 + DB 触发器兜底（trg_etf_profile_index_type 见迁移）。
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from uuid import UUID, uuid4

from sqlalchemy import and_, func, select
from sqlalchemy.orm import Session

from app.common.enums import InstrumentType, WatchlistStatus
from app.instruments.models import Instrument, ProviderSymbol, Watchlist, WatchlistMember
from app.instruments.schemas import InstrumentCreate, InstrumentUpdate
from app.portfolio.models import Portfolio, PositionSnapshot

DEFAULT_ETF_POOL = ("510300", "513650", "512890")


class InstrumentDomainError(Exception):
    """Instrument 域错误基类。"""


class InstrumentNotFoundError(InstrumentDomainError):
    pass


class SymbolConflictError(InstrumentDomainError):
    pass


class VersionConflictError(InstrumentDomainError):
    pass


class InvalidUnderlyingIndexError(InstrumentDomainError):
    pass


class WatchlistNotFoundError(InstrumentDomainError):
    pass


class WatchlistMemberNotFoundError(InstrumentDomainError):
    pass


class WatchlistPermissionError(InstrumentDomainError):
    pass


class WatchlistArchivedError(InstrumentDomainError):
    pass


class InstrumentService:
    """Instrument Master 唯一写入口。"""

    def __init__(self, session: Session) -> None:
        self._session = session

    # ---- 查询 ----

    def get(self, instrument_id: UUID) -> Instrument:
        inst = self._session.get(Instrument, instrument_id)
        if inst is None:
            raise InstrumentNotFoundError(instrument_id)
        return inst

    def get_by_symbol(self, market: str, symbol: str) -> Instrument | None:
        stmt = select(Instrument).where(
            Instrument.market == market, Instrument.symbol == symbol
        )
        return self._session.scalar(stmt)

    def resolve(self, provider: str, symbol: str) -> Instrument | None:
        """resolve_instrument：按 (provider, symbol) 找当前有效映射（provider_symbols 时态）。

        查询模式：ix_provider_symbols_lookup 部分索引（valid_to IS NULL）。
        """
        stmt = (
            select(Instrument)
            .join(ProviderSymbol, ProviderSymbol.instrument_id == Instrument.instrument_id)
            .where(
                ProviderSymbol.provider == provider,
                ProviderSymbol.symbol == symbol,
                ProviderSymbol.valid_to.is_(None),
            )
        )
        return self._session.scalar(stmt)

    # ---- 写入 ----

    def create(self, req: InstrumentCreate, provider_symbols: list[tuple[str, str]] | None = None) -> Instrument:
        """创建 Instrument（含可选初始 provider_symbols 时态映射）。

        幂等性：同 (market, symbol) 已存在 → SymbolConflictError（uq_instruments_symbol_market）。
        """
        existing = self.get_by_symbol(req.market.value, req.symbol)
        if existing is not None:
            raise SymbolConflictError(f"{req.market.value}:{req.symbol} 已存在")

        inst = Instrument(
            instrument_id=uuid4(),
            instrument_type=req.instrument_type,
            symbol=req.symbol,
            name=req.name,
            market=req.market.value,
            currency=req.currency,
            status=req.status.value,
            lot_size=req.lot_size,
            isin=req.isin,
            version=1,
        )
        self._session.add(inst)

        for provider, symbol in provider_symbols or []:
            self._session.add(
                ProviderSymbol(
                    provider_symbol_id=uuid4(),
                    instrument_id=inst.instrument_id,
                    provider=provider,
                    symbol=symbol,
                    valid_from=date.today(),
                )
            )
        self._session.commit()
        self._session.refresh(inst)
        return inst

    def update(self, instrument_id: UUID, req: InstrumentUpdate) -> Instrument:
        """属性演进（versioned update，乐观锁）：version 不匹配 → 409。

        身份（instrument_id / symbol / market）不可变（ts02 §3.1）。
        """
        inst = self.get(instrument_id)
        if inst.version != req.version:
            raise VersionConflictError(
                f"version 冲突：期望 {req.version}，当前 {inst.version}"
            )
        if req.name is not None:
            inst.name = req.name
        if req.status is not None:
            inst.status = req.status.value
        if req.isin is not None:
            inst.isin = req.isin
        inst.version += 1
        self._session.commit()
        self._session.refresh(inst)
        return inst

    def add_provider_symbol(self, instrument_id: UUID, provider: str, symbol: str, valid_from=None) -> ProviderSymbol:
        """追加时态映射（valid_from 起生效；同 provider 旧映射保持 valid_to IS NULL 可共存？——不：
        同 provider 同 symbol 的新映射应关闭旧映射（时间语义），由调用方/同步 job 管理 valid_to。
        v0.1 服务层规则：新增映射不自动关闭旧映射，避免隐式状态变更（显式 supersede）。
        """
        inst = self.get(instrument_id)
        ps = ProviderSymbol(
            provider_symbol_id=uuid4(),
            instrument_id=inst.instrument_id,
            provider=provider,
            symbol=symbol,
            valid_from=valid_from or date.today(),
        )
        self._session.add(ps)
        self._session.commit()
        self._session.refresh(ps)
        return ps


class WatchlistService:
    """Watchlist 关系的唯一写入口（ADR-006）。

    观察池成员只引用 Instrument 身份，不创建或修改 Instrument。默认 ETF 池
    也只提供显式、幂等的已有标的导入方法；迁移和启动装配不会自动 seed。
    """

    def __init__(self, session: Session) -> None:
        self._session = session

    def commit(self) -> None:
        self._session.commit()

    def get(self, watchlist_id: UUID) -> Watchlist:
        row = self._session.get(Watchlist, watchlist_id)
        if row is None:
            raise WatchlistNotFoundError(watchlist_id)
        return row

    def get_default(self) -> Watchlist | None:
        return self._session.scalar(
            select(Watchlist)
            .where(Watchlist.status == WatchlistStatus.ACTIVE)
            .order_by(Watchlist.created_at.asc())
            .limit(1)
        )

    def ensure_default_watchlist(
        self,
        *,
        name: str = "默认观察池",
        description: str | None = "由 Instrument Master 管理的默认研究观察池",
    ) -> Watchlist:
        """确保存在一个 ACTIVE 观察池；不会覆盖任何已有观察池。"""
        row = self._session.scalar(
            select(Watchlist)
            .where(Watchlist.status == WatchlistStatus.ACTIVE)
            .order_by(Watchlist.created_at.asc())
            .limit(1)
        )
        if row is not None:
            return row
        row = Watchlist(
            watchlist_id=uuid4(), name=name, description=description,
            status=WatchlistStatus.ACTIVE,
        )
        self._session.add(row)
        self._session.flush()
        return row

    def list_members(
        self,
        watchlist_id: UUID,
        *,
        include_removed: bool = False,
        permission: str = "READ",
    ) -> list[WatchlistMember]:
        self._require_permission(permission, "READ")
        self.get(watchlist_id)
        stmt = select(WatchlistMember).where(
            WatchlistMember.watchlist_id == watchlist_id
        )
        if not include_removed:
            stmt = stmt.where(WatchlistMember.removed_at.is_(None))
        return list(
            self._session.scalars(
                stmt.order_by(WatchlistMember.added_at.asc())
            ).all()
        )

    def add_member(
        self,
        watchlist_id: UUID,
        instrument_id: UUID,
        *,
        note: str | None = None,
        permission: str = "RESEARCH_WRITE",
    ) -> WatchlistMember:
        """加入或重新激活成员；不会复制同一观察池/标的关系。"""
        watchlist = self.get(watchlist_id)
        self._require_permission(permission, "RESEARCH_WRITE")
        if watchlist.status == WatchlistStatus.ARCHIVED:
            raise WatchlistArchivedError(watchlist_id)
        if self._session.get(Instrument, instrument_id) is None:
            raise InstrumentNotFoundError(instrument_id)
        row = self._session.scalar(
            select(WatchlistMember).where(
                WatchlistMember.watchlist_id == watchlist_id,
                WatchlistMember.instrument_id == instrument_id,
            )
        )
        if row is None:
            row = WatchlistMember(
                watchlist_member_id=uuid4(),
                watchlist_id=watchlist_id,
                instrument_id=instrument_id,
                note=note,
            )
            self._session.add(row)
        else:
            row.removed_at = None
            row.added_at = datetime.now(UTC)
            if note is not None:
                row.note = note
        self._session.flush()
        return row

    def remove_member(
        self,
        watchlist_id: UUID,
        instrument_id: UUID,
        *,
        permission: str = "RESEARCH_WRITE",
    ) -> WatchlistMember:
        """软移除成员，保留关系行以供审计。"""
        watchlist = self.get(watchlist_id)
        self._require_permission(permission, "RESEARCH_WRITE")
        if watchlist.status == WatchlistStatus.ARCHIVED:
            raise WatchlistArchivedError(watchlist_id)
        row = self._session.scalar(
            select(WatchlistMember).where(
                WatchlistMember.watchlist_id == watchlist_id,
                WatchlistMember.instrument_id == instrument_id,
                WatchlistMember.removed_at.is_(None),
            )
        )
        if row is None:
            raise WatchlistMemberNotFoundError(
                f"watchlist={watchlist_id}, instrument={instrument_id}"
            )
        row.removed_at = datetime.now(UTC)
        self._session.flush()
        return row

    def seed_existing_etf_pool(
        self,
        watchlist_id: UUID,
        *,
        symbols: tuple[str, ...] = DEFAULT_ETF_POOL,
    ) -> dict[str, list[str]]:
        """显式导入已存在的 ETF；缺失身份只报告，不创建或覆盖数据。"""
        self.get(watchlist_id)
        added: list[str] = []
        missing: list[str] = []
        for symbol in symbols:
            inst = self._session.scalar(
                select(Instrument).where(
                    Instrument.symbol == symbol,
                    Instrument.instrument_type == InstrumentType.CN_ETF,
                )
            )
            if inst is None:
                missing.append(symbol)
                continue
            self.add_member(watchlist_id, inst.instrument_id)
            added.append(symbol)
        return {"added": added, "missing": missing}

    def current_instrument_ids(self, watchlist_id: UUID) -> set[UUID]:
        watchlist = self.get(watchlist_id)
        if str(getattr(watchlist.status, "value", watchlist.status)) != WatchlistStatus.ACTIVE.value:
            return set()
        return {
            row.instrument_id for row in self.list_members(watchlist_id, permission="READ")
        }

    def daily_universe(
        self,
        watchlist_id: UUID,
        *,
        real_instrument_ids: set[UUID] | frozenset[UUID] = frozenset(),
    ) -> set[UUID]:
        """研究日 universe = 当前观察池成员 ∪ 真实持仓身份。"""
        return self.current_instrument_ids(watchlist_id) | set(real_instrument_ids)

    def daily_universe_for_date(self, watchlist_id: UUID, as_of: date) -> set[UUID]:
        """active watchlist members ∪ positive holdings in active REAL portfolios."""
        latest_position = (
            select(
                PositionSnapshot.portfolio_id,
                PositionSnapshot.instrument_id,
                func.max(PositionSnapshot.snapshot_date).label("latest_date"),
            )
            .where(PositionSnapshot.snapshot_date <= as_of)
            .group_by(PositionSnapshot.portfolio_id, PositionSnapshot.instrument_id)
            .subquery()
        )
        real_ids = set(self._session.scalars(
            select(PositionSnapshot.instrument_id)
            .join(Portfolio, Portfolio.portfolio_id == PositionSnapshot.portfolio_id)
            .join(
                latest_position,
                and_(
                    latest_position.c.portfolio_id == PositionSnapshot.portfolio_id,
                    latest_position.c.instrument_id == PositionSnapshot.instrument_id,
                    latest_position.c.latest_date == PositionSnapshot.snapshot_date,
                ),
            )
            .where(
                Portfolio.mode == "REAL",
                Portfolio.status == "ACTIVE",
                PositionSnapshot.quantity > 0,
            )
        ).all())
        return self.daily_universe(watchlist_id, real_instrument_ids=real_ids)

    @staticmethod
    def _require_permission(actual: str, required: str) -> None:
        levels = {
            "READ": 0,
            "RESEARCH_WRITE": 1,
            "PROPOSAL_WRITE": 2,
            "ACCOUNT_WRITE": 3,
        }
        if levels.get(actual, -1) < levels[required]:
            raise WatchlistPermissionError(
                f"需要 {required} 权限，当前 {actual}"
            )
