# =====================================================================
# tests/unit/test_corporate_actions_service.py —— Corporate Actions（§20 + CTR-PAR-005）
# =====================================================================
from __future__ import annotations

import asyncio
from datetime import date, datetime, timezone
from decimal import Decimal
from uuid import uuid4

import app.models  # noqa: F401

from app.corporate_actions.models import CorporateAction
from app.corporate_actions.service import CorporateActionsService
from app.providers.contracts.base import ProvenanceEnvelope
from app.providers.contracts.market_data import AdjFactorResult

NOW = datetime(2026, 8, 21, 12, 0, tzinfo=timezone.utc)


def _factor(iid, td: date, factor: str) -> AdjFactorResult:
    return AdjFactorResult(
        instrument_id=iid, trade_date=td, adj_factor=Decimal(factor),
        provenance=ProvenanceEnvelope(
            source="cn_adj_factor", provider="tushare",
            source_record_id=f"tushare@{td.isoformat()}",
            observed_at=NOW, retrieved_at=NOW, as_of_date=td,
            quality_score=Decimal("0.96"), quality_status="VERIFIED",
            transform_version="market-normalizer/0.1.0",
        ),
    )


def _dividends() -> list[dict]:
    return [
        {"announce_date": "20260328", "ex_date": "20260612", "record_date": "20260611",
         "cash_div": 276.0, "stk_div": 0.0, "stk_bo_rate": 0.0, "div_proc": "实施"},
        {"announce_date": "20250816", "ex_date": "20250626", "record_date": "20250625",
         "cash_div": 0.0, "stk_div": 0.0, "stk_bo_rate": 3.0, "div_proc": "实施"},
    ]


def test_sync_adj_factors_detects_change_days(db_session, instrument) -> None:
    """因子变更日 → corporate_actions 行（因子生效日 = ex_date）。"""
    svc = CorporateActionsService(gateway=None)
    factors = [
        _factor(instrument.instrument_id, date(2026, 6, 11), "1.0"),
        _factor(instrument.instrument_id, date(2026, 6, 12), "1.05"),   # 变更日
        _factor(instrument.instrument_id, date(2026, 6, 15), "1.05"),
    ]
    result = asyncio.run(svc.sync_adj_factors(db_session, instrument.instrument_id, factors))
    db_session.flush()
    assert result["events"] == 1
    assert result["written"] == 1
    row = db_session.query(CorporateAction).one()
    assert row.ex_date == date(2026, 6, 12)
    assert row.adj_factor == Decimal("1.05")
    assert row.parameters["prev_factor"] == "1.0"


def test_sync_dividends_with_real_types(db_session, instrument) -> None:
    """dividend 接口 → 真实行动类型（DIVIDEND / BONUS_SHARE）。"""
    svc = CorporateActionsService(gateway=None)
    n = asyncio.run(svc.sync_dividends(db_session, instrument.instrument_id, _dividends()))
    db_session.flush()
    assert n == 2
    types = sorted(r.action_type for r in db_session.query(CorporateAction).all())
    assert types == ["BONUS_SHARE", "DIVIDEND"]
    div = (db_session.query(CorporateAction)
           .filter(CorporateAction.action_type == "DIVIDEND").one())
    assert div.parameters["cash_div_per_10"] == "276.0"


def test_sync_dividends_idempotent(db_session, instrument) -> None:
    svc = CorporateActionsService(gateway=None)
    asyncio.run(svc.sync_dividends(db_session, instrument.instrument_id, _dividends()))
    db_session.flush()
    n2 = asyncio.run(svc.sync_dividends(db_session, instrument.instrument_id, _dividends()))
    db_session.flush()
    assert n2 == 0
    assert db_session.query(CorporateAction).count() == 2


def test_verify_adj_factor_consistency(tmp_path, db_session, instrument) -> None:
    """CTR-PAR-005：corporate_actions 因子与 ohlcva adj_factor 一致。"""
    from app.market_data.parquet import ParquetStore
    from app.market_data.repository import persist_market_bars
    from app.providers.contracts.market_data import MarketBarResult

    store = ParquetStore(tmp_path / "parquet")
    bar = MarketBarResult(
        instrument_id=instrument.instrument_id, trade_date=date(2026, 6, 12),
        close=Decimal("100"), adj_factor=Decimal("1.05"), provider="tushare",
        provenance=ProvenanceEnvelope(
            source="cn_daily_market", provider="tushare",
            source_record_id="tushare@2026-06-12",
            observed_at=NOW, retrieved_at=NOW, as_of_date=date(2026, 6, 12),
            quality_score=Decimal("0.96"), quality_status="VERIFIED",
            transform_version="market-normalizer/0.1.0",
        ),
    )
    persist_market_bars(db_session, [bar], parquet_store=store)
    db_session.flush()

    svc = CorporateActionsService(gateway=None)
    factors = [
        _factor(instrument.instrument_id, date(2026, 6, 11), "1.0"),
        _factor(instrument.instrument_id, date(2026, 6, 12), "1.05"),
    ]
    asyncio.run(svc.sync_adj_factors(db_session, instrument.instrument_id, factors))
    db_session.flush()
    assert svc.verify_adj_factor_consistency(db_session, instrument.instrument_id, store) is True

    # 篡改 bar 因子 → 不一致
    bar2 = MarketBarResult(
        instrument_id=instrument.instrument_id, trade_date=date(2026, 6, 12),
        close=Decimal("100"), adj_factor=Decimal("9.99"), provider="tushare",
        provenance=ProvenanceEnvelope(
            source="cn_daily_market", provider="tushare",
            source_record_id="tushare@2026-06-12",
            observed_at=NOW, retrieved_at=NOW, as_of_date=date(2026, 6, 12),
            quality_score=Decimal("0.96"), quality_status="VERIFIED",
            transform_version="market-normalizer/0.1.0",
        ),
    )
    persist_market_bars(db_session, [bar2], parquet_store=store)
    db_session.flush()
    assert svc.verify_adj_factor_consistency(db_session, instrument.instrument_id, store) is False
