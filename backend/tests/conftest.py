"""pytest 共享 fixtures（Hermes Investment Office）。

数据库策略：
- 使用独立测试库 hermes_test（已在迁移时创建）；
- 每个测试函数：嵌套事务 + rollback，保证隔离与幂等；
- 架构测试（触发器存在性等）读取当前已迁移 schema。
"""

from __future__ import annotations

import os
from collections.abc import Generator

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

TEST_DB_URL = os.environ.get(
    "HERMES_TEST_DB_URL",
    "postgresql+psycopg2://hermes:hermes@127.0.0.1:5432/hermes_test",
)

engine = create_engine(TEST_DB_URL, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)


@pytest.fixture()
def db_session() -> Generator[Session, None, None]:
    """事务回滚隔离：测试内可见的写入在测试结束后全部回滚。"""
    conn = engine.connect()
    trans = conn.begin()
    session = Session(bind=conn, join_transaction_mode="create_savepoint")
    try:
        yield session
    finally:
        session.close()
        trans.rollback()
        conn.close()


@pytest.fixture()
def raw_engine():
    """架构测试用：直接访问已迁移 schema（只读断言）。"""
    return engine


@pytest.fixture()
def instrument(db_session):
    """一个可用的 CN_EQUITY Instrument（跨测试共享定义，避免各测试文件重复）。"""
    from app.instruments.models import Instrument

    inst = Instrument(
        instrument_type="CN_EQUITY", symbol=f"T{__import__('uuid').uuid4().hex[:6]}",
        name="测试标的", market="SSE", currency="CNY",
    )
    db_session.add(inst)
    db_session.flush()
    return inst
