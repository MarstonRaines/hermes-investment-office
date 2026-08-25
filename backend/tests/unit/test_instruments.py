"""Instrument Master 单元测试（M0 验收：Instrument 可创建与查询、Mapping 可工作）。

覆盖：
- 创建 / 查询 / resolve（provider_symbols 时态映射）
- 幂等冲突（同 market+symbol → 409 SymbolConflictError）
- 乐观锁版本冲突（409 VersionConflictError）
- Pydantic 校验（非法枚举 / US_ETF 拒绝 / currency 非 CNY 拒绝）
"""

from __future__ import annotations

from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.common.enums import InstrumentType
from app.etf.models import ETFProfile
from app.instruments.schemas import InstrumentCreate
from app.instruments.service import (
    InstrumentNotFoundError,
    InstrumentService,
    InvalidInstrumentSymbolError,
    SymbolConflictError,
    VersionConflictError,
)


def _svc(db):
    return InstrumentService(db)


def _create(db, symbol="600519", market="SSE", name="贵州茅台", itype=InstrumentType.CN_EQUITY):
    req = InstrumentCreate(instrument_type=itype, symbol=symbol, name=name, market=market)
    return _svc(db).create(req)


class TestInstrumentCreate:
    def test_create_and_get(self, db_session):
        inst = _create(db_session)
        got = _svc(db_session).get(inst.instrument_id)
        assert got.symbol == "600519"
        assert got.name == "贵州茅台"
        assert got.market == "SSE"
        assert got.currency == "CNY"
        assert got.version == 1
        assert got.instrument_type == "CN_EQUITY"

    def test_create_conflict_same_symbol(self, db_session):
        _create(db_session)
        with pytest.raises(SymbolConflictError):
            _create(db_session, name="重名标的")

    def test_create_etf_type(self, db_session):
        inst = _create(db_session, symbol="513100", name="纳指ETF", itype=InstrumentType.CN_ETF)
        assert inst.instrument_type == "CN_ETF"

    def test_create_index_type(self, db_session):
        inst = _create(db_session, symbol="000300", name="沪深300", itype=InstrumentType.INDEX)
        assert inst.instrument_type == "INDEX"

    @pytest.mark.parametrize(
        ("symbol", "market", "instrument_type", "provider_symbol"),
        [
            ("600519", "SSE", "CN_EQUITY", "600519.SH"),
            ("003816.SZ", "SZSE", "CN_EQUITY", "003816.SZ"),
            ("510300", "SSE", "CN_ETF", "510300.SH"),
            ("159915", "SZSE", "CN_ETF", "159915.SZ"),
        ],
    )
    def test_auto_registration_infers_identity_and_provider_symbols(
        self,
        db_session,
        symbol,
        market,
        instrument_type,
        provider_symbol,
    ):
        service = _svc(db_session)
        instrument, created = service.ensure_cn_instrument(symbol, "测试标的")

        assert created is True
        assert instrument.symbol == provider_symbol[:6]
        assert instrument.market == market
        assert instrument.exchange == market
        assert instrument.instrument_type == instrument_type
        assert {
            service.resolve(provider, provider_symbol).instrument_id
            for provider in ("tushare", "akshare_sina", "akshare_eastmoney")
        } == {instrument.instrument_id}
        if instrument_type == "CN_ETF":
            profile = db_session.get(ETFProfile, instrument.instrument_id)
            assert profile is not None
            assert profile.fund_name == "测试标的"

    def test_auto_registration_reuses_existing_and_rejects_unsupported_code(self, db_session):
        service = _svc(db_session)
        existing = _create(db_session)

        instrument, created = service.ensure_cn_instrument("600519.SH", "输入名称")
        assert created is False
        assert instrument.instrument_id == existing.instrument_id

        with pytest.raises(InvalidInstrumentSymbolError):
            service.ensure_cn_instrument("920001", "北交所标的")
        with pytest.raises(InvalidInstrumentSymbolError):
            service.ensure_cn_instrument("123001", "可转债")

    def test_qdii_registration_creates_a_valid_pending_index_profile(self, db_session):
        service = _svc(db_session)

        instrument, _ = service.ensure_cn_instrument("159999", "测试跨境ETF(QDII)")

        profile = db_session.get(ETFProfile, instrument.instrument_id)
        assert profile is not None
        assert profile.is_qdii is True
        assert profile.underlying_index_id is not None
        underlying = service.get(profile.underlying_index_id)
        assert underlying.instrument_type == "INDEX"
        assert "待核实" in underlying.name

    def test_common_overseas_index_name_is_inferred_as_qdii(self, db_session):
        service = _svc(db_session)

        instrument, _ = service.ensure_cn_instrument("159941", "纳指ETF富国")

        profile = db_session.get(ETFProfile, instrument.instrument_id)
        assert profile is not None
        assert profile.is_qdii is True
        assert profile.underlying_index_id is not None

    def test_auto_registration_upgrades_a_legacy_etf_without_profile(self, db_session):
        service = _svc(db_session)
        legacy = _create(
            db_session,
            symbol="159941",
            market="SZSE",
            name="纳指ETF富国",
            itype=InstrumentType.CN_ETF,
        )
        assert db_session.get(ETFProfile, legacy.instrument_id) is None

        instrument, created = service.ensure_cn_instrument("159941", "纳指ETF富国")

        assert created is False
        assert instrument.instrument_id == legacy.instrument_id
        assert db_session.get(ETFProfile, legacy.instrument_id) is not None


class TestProviderSymbolMapping:
    def test_add_and_resolve(self, db_session):
        svc = _svc(db_session)
        inst = _create(db_session)
        svc.add_provider_symbol(inst.instrument_id, "tushare", "600519.SH")
        svc.add_provider_symbol(inst.instrument_id, "yahoo", "600519.SS")

        assert svc.resolve("tushare", "600519.SH").instrument_id == inst.instrument_id
        assert svc.resolve("yahoo", "600519.SS").instrument_id == inst.instrument_id
        assert svc.resolve("tushare", "不存在的代码") is None

    def test_resolve_ignores_expired_mapping(self, db_session):
        from datetime import date, timedelta

        svc = _svc(db_session)
        inst = _create(db_session)
        ps = svc.add_provider_symbol(inst.instrument_id, "tushare", "600519.SH")
        # 关闭旧映射（valid_to 设置 → 不再被 resolve 命中）
        ps.valid_to = date.today() - timedelta(days=1)
        db_session.commit()
        assert svc.resolve("tushare", "600519.SH") is None

    def test_mapping_belongs_to_instrument(self, db_session):
        svc = _svc(db_session)
        inst_a = _create(db_session, symbol="600519")
        inst_b = _create(db_session, symbol="000001", name="平安银行", market="SZSE")
        svc.add_provider_symbol(inst_a.instrument_id, "tushare", "600519.SH")
        svc.add_provider_symbol(inst_b.instrument_id, "tushare", "000001.SZ")
        assert svc.resolve("tushare", "600519.SH").instrument_id == inst_a.instrument_id
        assert svc.resolve("tushare", "000001.SZ").instrument_id == inst_b.instrument_id


class TestOptimisticLock:
    def test_version_conflict(self, db_session):
        svc = _svc(db_session)
        inst = _create(db_session)
        from app.instruments.schemas import InstrumentUpdate

        svc.update(inst.instrument_id, InstrumentUpdate(name="改名一次", version=1))
        with pytest.raises(VersionConflictError):
            svc.update(inst.instrument_id, InstrumentUpdate(name="并发写入", version=1))

    def test_update_increments_version(self, db_session):
        from app.instruments.schemas import InstrumentUpdate

        svc = _svc(db_session)
        inst = _create(db_session)
        updated = svc.update(inst.instrument_id, InstrumentUpdate(name="新名字", version=1))
        assert updated.version == 2
        assert updated.name == "新名字"


class TestNotFound:
    def test_get_missing(self, db_session):
        with pytest.raises(InstrumentNotFoundError):
            _svc(db_session).get(uuid4())


class TestSchemaValidation:
    """Pydantic 边界校验（ts03 §10 / TS-08 CTR-PYD 组）。"""

    def test_invalid_type_rejected(self):
        with pytest.raises(ValidationError):
            InstrumentCreate(instrument_type="US_ETF", symbol="SPY", name="x", market="SSE")

    def test_currency_must_be_cny(self):
        with pytest.raises(ValidationError):
            InstrumentCreate(
                instrument_type="CN_EQUITY", symbol="600519", name="x", market="SSE", currency="USD"
            )

    def test_empty_symbol_rejected(self):
        with pytest.raises(ValidationError):
            InstrumentCreate(instrument_type="CN_EQUITY", symbol="", name="x", market="SSE")

    def test_at_risk_not_in_enum(self):
        # AT_RISK 不是合法枚举值（冻结规范 §27.1 / ts02 §5.3）
        import app.common.enums as enums

        values = {e.value for e in enums.ThesisHealthStatus}
        assert "AT_RISK" not in values
