# =====================================================================
# backend/app/providers/resolve.py —— provider symbol 解析（TS-05 §2 一致性规则）
#
# 冻结规则：接口内不出现 provider symbol 主键——调用方先经 provider_symbols
# 解析为内部 instrument_id；Provider 取数时再按 provider 反查具体 symbol。
# 本模块提供该反查的同步助手（供 factory 注入 provider）。
# =====================================================================
from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.instruments.models import ProviderSymbol

__all__ = ["resolve_provider_symbol"]


def resolve_provider_symbol(
    db: Session,
    instrument_id: UUID,
    provider: str,
) -> str | None:
    """按 provider 反查当前有效（valid_to IS NULL）的 symbol；无映射返回 None。"""
    row = db.execute(
        select(ProviderSymbol.symbol).where(
            ProviderSymbol.instrument_id == instrument_id,
            ProviderSymbol.provider == provider,
            ProviderSymbol.valid_to.is_(None),
        )
    ).first()
    return row[0] if row else None
