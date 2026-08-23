"""valuation_runs 触发器改为状态机守卫（E5 语义落地，ts01 §4.2 / ts06 §3.6.4）

背景（2026-08-24 施工发现）：
- M0 的 fn_reject_update 对 valuation_runs 一刀切拒绝 UPDATE，与冻结状态机
  （CREATED→VALIDATING→RUNNING→COMPLETED，ts01 §4.2）冲突——状态迁移必须写 status；
- ts02 §10.1 的不可变语义是"COMPLETED 后不可变"（E5 / ts06 §3.6.4）：
  假设/输入引用/engine_version/结果列冻结，但状态机迁移是生命周期内合法写入；
- 本迁移保留触发器名与事件（trg_valuation_runs_no_update，UPDATE+DELETE，
  架构测试 ARCH-DB-004 不变），替换函数为状态机守卫：
    · DELETE 永远拒绝；
    · COMPLETED 之前：允许任意列变更（状态机迁移 + 完成时回填结果）；
    · COMPLETED 之后：仅允许 COMPLETED→SUPERSEDED（且冻结列不变），
      其余任何变更 → 拒绝（含对结果列的修改）。
其他 13 张不可变表保持 fn_reject_update 严格拒绝（其生命周期无状态机）。

Revision ID: after audit action extension
Revises: f6e5d4c3b2a1
"""

from alembic import op

revision = "a7b8c9d0e1f2"
down_revision = "f6e5d4c3b2a1"
branch_labels = None
depends_on = None

GUARD_SQL = """
CREATE OR REPLACE FUNCTION fn_valuation_runs_guard() RETURNS trigger AS $$
DECLARE
  frozen_changed boolean;
BEGIN
  IF TG_OP = 'DELETE' THEN
    RAISE EXCEPTION 'table valuation_runs is append-only';
  END IF;
  frozen_changed := NEW.instrument_id IS DISTINCT FROM OLD.instrument_id
       OR NEW.model_type IS DISTINCT FROM OLD.model_type
       OR NEW.as_of IS DISTINCT FROM OLD.as_of
       OR NEW.engine_version IS DISTINCT FROM OLD.engine_version
       OR NEW.input_snapshot_hash IS DISTINCT FROM OLD.input_snapshot_hash
       OR NEW.bear_value IS DISTINCT FROM OLD.bear_value
       OR NEW.base_value IS DISTINCT FROM OLD.base_value
       OR NEW.bull_value IS DISTINCT FROM OLD.bull_value
       OR NEW.current_price IS DISTINCT FROM OLD.current_price
       OR NEW.margin_of_safety IS DISTINCT FROM OLD.margin_of_safety
       OR NEW.result_json IS DISTINCT FROM OLD.result_json;
  IF OLD.status = 'COMPLETED' THEN
    -- E5：COMPLETED 后仅允许 COMPLETED→SUPERSEDED，且冻结列不得变更
    IF NEW.status <> 'SUPERSEDED' THEN
      RAISE EXCEPTION 'valuation_runs: COMPLETED 后仅允许 SUPERSEDED 状态迁移';
    END IF;
    IF frozen_changed THEN
      RAISE EXCEPTION 'valuation_runs: 冻结列禁止修改（E5，COMPLETED 后不可变）';
    END IF;
    RETURN NEW;
  END IF;
  -- 生命周期内（CREATED/VALIDATING/RUNNING/BLOCKED/FAILED）：允许状态机迁移与完成回填
  RETURN NEW;
END $$ LANGUAGE plpgsql;
"""


def upgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS trg_valuation_runs_no_update ON valuation_runs;")
    op.execute(GUARD_SQL)
    op.execute(
        "CREATE TRIGGER trg_valuation_runs_no_update "
        "BEFORE UPDATE OR DELETE ON valuation_runs "
        "FOR EACH ROW EXECUTE FUNCTION fn_valuation_runs_guard();"
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS trg_valuation_runs_no_update ON valuation_runs;")
    op.execute(
        "CREATE TRIGGER trg_valuation_runs_no_update "
        "BEFORE UPDATE OR DELETE ON valuation_runs "
        "FOR EACH ROW EXECUTE FUNCTION fn_reject_update();"
    )
