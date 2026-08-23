# backend/app/corporate_actions/models.py —— 模块归属：corporate_actions
from datetime import date
from decimal import Decimal
from uuid import UUID

from sqlalchemy import Date, ForeignKey, Index, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.common.base import Base
from app.common.db import enum_ck
from app.common.enums import CorporateActionStatus, CorporateActionType
from app.common.mixins import CreatedAtMixin, UUIDPrimaryKeyMixin
from app.common.types import FX_RATE

pk = UUIDPrimaryKeyMixin.pk


class CorporateAction(Base, CreatedAtMixin):
    """公司行动。adj_factor 与 OHLCVA 的 adj_factor 对应，由本模块统一维护（来源+生效日+参数可追溯）。
    长期收益计算不得只依赖 adjusted_close（冻结规范 §20/§21）。"""
    __tablename__ = "corporate_actions"
    __table_args__ = (
        Index("ix_corporate_actions_inst_exdate", "instrument_id", "ex_date"),
        enum_ck("corporate_actions", "action_type", CorporateActionType),
        enum_ck("corporate_actions", "status", CorporateActionStatus),
    )
    corporate_action_id: Mapped[UUID] = pk("corporate_action_id")
    instrument_id: Mapped[UUID] = mapped_column(ForeignKey("instruments.instrument_id"), nullable=False)
    action_type: Mapped[CorporateActionType] = mapped_column(Text, nullable=False)  # DIVIDEND/SPLIT/BONUS_SHARE/RIGHTS_ISSUE
    announce_date: Mapped[date | None] = mapped_column(Date)
    ex_date: Mapped[date | None] = mapped_column(Date)                    # 除权除息日
    record_date: Mapped[date | None] = mapped_column(Date)
    effective_date: Mapped[date | None] = mapped_column(Date)
    parameters: Mapped[dict] = mapped_column(JSONB, nullable=False)       # 每股股利、比例等
    adj_factor: Mapped[Decimal | None] = mapped_column(FX_RATE)           # 复权因子
    status: Mapped[CorporateActionStatus] = mapped_column(Text, nullable=False)
    provenance_id: Mapped[UUID] = mapped_column(ForeignKey("provenance_records.provenance_id"), nullable=False)
