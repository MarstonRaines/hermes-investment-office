"""快照表触发器改为 upsert-by-supersede 守卫（ts06 §5.7.1，冻结）

背景（2026-08-24 施工发现）：
- M0 的 fn_reject_update 对 position_snapshots / portfolio_snapshots 一刀切拒绝
  UPDATE，与 ts06 §5.7.1 冻结契约冲突："同一 snapshot_date 重跑 = supersede
  新行（或按唯一约束 upsert-by-supersede，禁止原地 UPDATE 历史）"；
- 唯一约束（portfolio_id, snapshot_date）使"新行 supersede"不可行（同日期重复行
  违反唯一键）→ 冻结契约显式允许的"upsert-by-supersede"是唯一自洽实现；
- 本迁移保留触发器名与事件（架构测试 ARCH-DB-004 不变），替换为键不变守卫：
    · DELETE 永远拒绝；
    · UPDATE 仅允许唯一键列不变（portfolio_id + snapshot_date [+
      instrument_id]）——即同一日期快照的重跑替换；
    · 跨日期改写/换键 → 拒绝（"禁止原地 UPDATE 历史"的物理兜底）。
其他 12 张不可变表保持 fn_reject_update 严格拒绝。

Revision ID: after valuation_runs guard
Revises: a7b8c9d0e1f2
"""

from alembic import op

revision = "b1c2d3e4f5a6"
down_revision = "a7b8c9d0e1f2"
branch_labels = None
depends_on = None

GUARD_SQL = """
CREATE OR REPLACE FUNCTION fn_snapshot_upsert_guard() RETURNS trigger AS $$
BEGIN
  IF TG_OP = 'DELETE' THEN
    RAISE EXCEPTION 'table % is append-only（快照只允许同键 upsert）', TG_TABLE_NAME;
  END IF;
  IF TG_TABLE_NAME = 'portfolio_snapshots' THEN
    IF NEW.portfolio_id IS DISTINCT FROM OLD.portfolio_id
       OR NEW.snapshot_date IS DISTINCT FROM OLD.snapshot_date THEN
      RAISE EXCEPTION 'portfolio_snapshots: 唯一键禁止改写（upsert-by-supersede 仅限同日期）';
    END IF;
  ELSIF TG_TABLE_NAME = 'position_snapshots' THEN
    IF NEW.portfolio_id IS DISTINCT FROM OLD.portfolio_id
       OR NEW.instrument_id IS DISTINCT FROM OLD.instrument_id
       OR NEW.snapshot_date IS DISTINCT FROM OLD.snapshot_date THEN
      RAISE EXCEPTION 'position_snapshots: 唯一键禁止改写（upsert-by-supersede 仅限同日期）';
    END IF;
  ELSE
    RAISE EXCEPTION 'fn_snapshot_upsert_guard 仅用于快照表';
  END IF;
  RETURN NEW;
END $$ LANGUAGE plpgsql;
"""

SNAPSHOT_TABLES = ("position_snapshots", "portfolio_snapshots")


def upgrade() -> None:
    op.execute(GUARD_SQL)
    for table in SNAPSHOT_TABLES:
        op.execute(f"DROP TRIGGER IF EXISTS trg_{table}_no_update ON {table};")
        op.execute(
            f"CREATE TRIGGER trg_{table}_no_update "
            f"BEFORE UPDATE OR DELETE ON {table} "
            f"FOR EACH ROW EXECUTE FUNCTION fn_snapshot_upsert_guard();"
        )


def downgrade() -> None:
    for table in SNAPSHOT_TABLES:
        op.execute(f"DROP TRIGGER IF EXISTS trg_{table}_no_update ON {table};")
        op.execute(
            f"CREATE TRIGGER trg_{table}_no_update "
            f"BEFORE UPDATE OR DELETE ON {table} "
            f"FOR EACH ROW EXECUTE FUNCTION fn_reject_update();"
        )
