# backend/app/common/db.py —— 约束助手
from enum import StrEnum
from sqlalchemy import CheckConstraint


def enum_ck(table: str, column: str, enum_cls: type[StrEnum]) -> CheckConstraint:
    """生成 ts02 §1.3 命名规范的枚举 CHECK：ck_<表>_<列>_check。

    枚举定义是唯一事实来源：加值/删值后由 Alembic 生成新 migration 同步 CHECK。
    """
    values = ", ".join(f"'{m.value}'" for m in enum_cls)
    return CheckConstraint(f"{column} IN ({values})", name=f"ck_{table}_{column}_check")


def range_ck(table: str, column: str, low: str, high: str) -> CheckConstraint:
    """数值区间 CHECK（如 quality_score 0-1）：ck_<表>_<列>_range。"""
    return CheckConstraint(f"{column} >= {low} AND {column} <= {high}",
                           name=f"ck_{table}_{column}_range")
