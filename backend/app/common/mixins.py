# backend/app/common/mixins.py
from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import func
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.common.types import TIMESTAMPTZ


class UUIDPrimaryKeyMixin:
    """UUID 主键约定（ts02 §1.2：全表 UUID PRIMARY KEY）。

    ts02 要求主键列名 = <单数实体>_id（instrument_id / provenance_id / …），
    普通 mixin 属性无法为每张表生成不同列名，故本 mixin 只固化两个不变量：
      1) 类型 = UUID（as_uuid=True，Python 侧 UUID 对象）；
      2) 默认值 = uuid4()（与 gen_random_uuid() 等价的客户端生成，利于批量插入）。
    列名由每个模型按 ts02 显式声明。架构测试校验：每表恰好一个 UUID 主键，
    禁止业务键（symbol / 日期复合键）作主键。
    """

    @staticmethod
    def pk(name: str) -> Mapped[UUID]:
        """生成 ts02 命名风格的 UUID 主键列（运行时即列对象，注解仅供静态类型提示）。"""
        return mapped_column(name, PgUUID(as_uuid=True), primary_key=True, default=uuid4)


class CreatedAtMixin:
    """append-only 表统一 created_at（DB 默认 now()）。"""
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMPTZ, nullable=False, server_default=func.now())


class TimestampMixin(CreatedAtMixin):
    """可变表统一 created_at + updated_at（onupdate 由 ORM/服务层维护）。"""
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMPTZ, nullable=False, server_default=func.now(), onupdate=func.now())
