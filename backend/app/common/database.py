"""数据库引擎与会话（Hermes Investment Office）。

规则：
- 引擎创建自 settings.db_url（HERMES_DB_URL 环境变量，ADR-004 D5 配置化）；
- v0.1 单库事务（冻结规范 §7 模块化单体，无 MQ）；
- 业务代码通过 get_db 依赖获取 Session；禁止在业务代码中直接创建 Engine。
"""

from __future__ import annotations

from collections.abc import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.common.config import settings

engine = create_engine(settings.db_url, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)


def get_db() -> Generator[Session, None, None]:
    """FastAPI 依赖：请求级 Session（事务提交/回滚由服务层控制）。"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
