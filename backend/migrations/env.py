"""Alembic 迁移环境（Hermes Investment Office）。

规则：
- 元数据来源：app.models（40 表聚合），不在此处 import 单个模型；
- DB URL：优先 HERMES_DB_URL 环境变量，否则回退 settings（.env）；
- 显式命名约定由 app.common.base.Base.metadata 承载（NamingConvention）；
- 离线（offline）与在线（online）模式均支持。
"""

from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

import app.models  # noqa: F401 —— 注册全部 ORM 模型到 Base.metadata
from app.common.base import Base
from app.common.config import settings

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# DB URL 解析顺序：命令行/ini > 环境变量 > settings（.env）
db_url = config.get_main_option("sqlalchemy.url")
if not db_url or db_url.startswith("postgresql://"):
    db_url = settings.db_url
config.set_main_option("sqlalchemy.url", db_url)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """离线模式：不连接数据库，直接生成 SQL。"""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """在线模式：连接数据库执行迁移。"""
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
