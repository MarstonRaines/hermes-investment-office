# backend/app/common/base.py
from sqlalchemy import MetaData
from sqlalchemy.orm import DeclarativeBase

# 显式命名约定：让 Alembic autogenerate 生成的约束/索引名可预测、可幂等。
# 本报告所有约束均显式命名（见各表 __table_args__），约定只作为"漏网之鱼"的兜底，
# 保证任何未命名约束也能生成稳定名称。多列唯一约束必须显式命名
# （约定模板只用 column_0，会丢列）。
NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)
