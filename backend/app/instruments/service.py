"""Instrument Master 服务（第一等领域，冻结规范 §10 / TS-01 所有权）。

Write Authority（TS-01 §3）：
- instruments / provider_symbols / etf_profiles 的唯一写入者；
- Instrument 身份（instrument_id）不可变；属性 versioned update（乐观锁）；
- 禁止以 Provider Symbol 作为内部主键（施工纪律第 9 条）。

跨表约束（ts02 §3.3）：is_qdii=true 时 underlying_index_id 必填且指向 INDEX；
服务层验证 + DB 触发器兜底（trg_etf_profile_index_type 见迁移）。
"""

from __future__ import annotations

from datetime import date
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.common.enums import InstrumentStatus, InstrumentType
from app.instruments.models import Instrument, ProviderSymbol
from app.instruments.schemas import InstrumentCreate, InstrumentUpdate


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
