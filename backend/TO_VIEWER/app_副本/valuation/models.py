# backend/app/valuation/models.py —— 模块归属：valuation（Valuation Engine）
from datetime import datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import ForeignKey, Index, Text, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import JSONB, UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.common.base import Base
from app.common.db import enum_ck
from app.common.enums import ValuationInputType, ValuationModelType, ValuationRunStatus
from app.common.mixins import CreatedAtMixin, UUIDPrimaryKeyMixin
from app.common.types import TIMESTAMPTZ, FX_RATE, MONEY, RATIO

pk = UUIDPrimaryKeyMixin.pk


class ValuationRun(Base, CreatedAtMixin):
    """可复现估值（不可变，核心表）。

    冻结不变量（ts01 §ValuationRun / ts02 §6.1）：
      - COMPLETED 后 assumption / input_ref / engine_version / result 全部禁止修改；
        需要新假设 = 新 Run（绝不 UPDATE 旧 run 的 wacc）；
      - SUPERSEDED 由"更新的已批准 run"触发，旧 run 保留；
      - 可复现三要素：assumptions + engine_version + as_of。
    """
    __tablename__ = "valuation_runs"
    __table_args__ = (
        Index("ix_valuation_runs_inst_asof", "instrument_id", text("as_of DESC")),
        enum_ck("valuation_runs", "model_type", ValuationModelType),
        enum_ck("valuation_runs", "status", ValuationRunStatus),
    )
    valuation_run_id: Mapped[UUID] = pk("valuation_run_id")
    instrument_id: Mapped[UUID] = mapped_column(ForeignKey("instruments.instrument_id"), nullable=False)
    model_type: Mapped[ValuationModelType] = mapped_column(Text, nullable=False)
    status: Mapped[ValuationRunStatus] = mapped_column(Text, nullable=False)
    as_of: Mapped[datetime] = mapped_column(TIMESTAMPTZ, nullable=False)   # 估值基准时点
    engine_version: Mapped[str] = mapped_column(Text, nullable=False)      # 可复现三要素之一
    input_snapshot_hash: Mapped[str | None] = mapped_column(Text)          # 输入冻结 hash
    bear_value: Mapped[Decimal | None] = mapped_column(MONEY)
    base_value: Mapped[Decimal | None] = mapped_column(MONEY)
    bull_value: Mapped[Decimal | None] = mapped_column(MONEY)
    current_price: Mapped[Decimal | None] = mapped_column(MONEY)           # as_of 时点市价
    margin_of_safety: Mapped[Decimal | None] = mapped_column(RATIO)
    result_json: Mapped[dict | None] = mapped_column(JSONB)                # 完整结果（含明细）
    created_by: Mapped[str] = mapped_column(Text, nullable=False)          # HERMES / HUMAN / SYSTEM
    completed_at: Mapped[datetime | None] = mapped_column(TIMESTAMPTZ)

    assumptions: Mapped[list["ValuationAssumption"]] = relationship(back_populates="run")
    input_refs: Mapped[list["ValuationInputRef"]] = relationship(back_populates="run")


class ValuationAssumption(Base, CreatedAtMixin):
    """显式估值假设（携带 basis；run 内不可变）。ts01 冻结：缺失参数必须失败（BLOCKED_MISSING_INPUT），
    绝不自动补 wacc=8%。"""
    __tablename__ = "valuation_assumptions"
    __table_args__ = (
        UniqueConstraint("valuation_run_id", "name", name="uq_valuation_assumptions_run_name"),
    )
    valuation_assumption_id: Mapped[UUID] = pk("valuation_assumption_id")
    valuation_run_id: Mapped[UUID] = mapped_column(ForeignKey("valuation_runs.valuation_run_id"), nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)   # wacc / terminal_growth / revenue_cagr / ...
    value: Mapped[Decimal] = mapped_column(FX_RATE, nullable=False)   # NUMERIC(16,8)
    unit: Mapped[str] = mapped_column(Text, nullable=False)   # ratio / cny / percent / years
    basis: Mapped[str] = mapped_column(Text, nullable=False)  # 假设依据（拒绝裸 float）
    source_tags: Mapped[list[str]] = mapped_column(JSONB, nullable=False, server_default=text("'[]'"))

    run: Mapped[ValuationRun] = relationship(back_populates="assumptions")


class ValuationInputRef(Base, CreatedAtMixin):
    """冻结输入集合（run 内不可变）：输入冻结证明，防"数据后来被 supersede"的复现漂移。"""
    __tablename__ = "valuation_input_refs"
    __table_args__ = (
        UniqueConstraint("valuation_run_id", "input_type", "object_id",
                         name="uq_valuation_input_refs_run_type_obj"),
        enum_ck("valuation_input_refs", "input_type", ValuationInputType),
    )
    valuation_input_ref_id: Mapped[UUID] = pk("valuation_input_ref_id")
    valuation_run_id: Mapped[UUID] = mapped_column(ForeignKey("valuation_runs.valuation_run_id"), nullable=False)
    input_type: Mapped[ValuationInputType] = mapped_column(Text, nullable=False)
    object_id: Mapped[UUID | None] = mapped_column(PgUUID(as_uuid=True))  # 引用对象 id
    object_version: Mapped[str | None] = mapped_column(Text)              # 版本/日期标识
    object_hash: Mapped[str | None] = mapped_column(Text)                 # 内容 hash（冻结证明）

    run: Mapped[ValuationRun] = relationship(back_populates="input_refs")
