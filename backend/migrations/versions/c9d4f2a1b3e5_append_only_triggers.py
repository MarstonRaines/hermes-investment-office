"""append-only 触发器（ts02 §10.3 物理兜底）

对不可变表创建 BEFORE UPDATE OR DELETE 触发器，作为服务层纪律的数据库级兜底。
不可变表清单（ts02 §10.1）：
provenance_records, thesis_revisions, portfolio_transactions, valuation_runs,
valuation_assumptions, valuation_input_refs, evidence_items, evidence_links,
audit_events, outbox_events, position_snapshots, portfolio_snapshots,
thesis_events, research_events

Revision ID: 2nd (after 40-tables)
Revises: b00c819f819c
"""

from alembic import op

revision = "c9d4f2a1b3e5"
down_revision = "b00c819f819c"
branch_labels = None
depends_on = None

# 不可变表清单（ts02 §10.1）
APPEND_ONLY_TABLES = [
    "provenance_records",
    "thesis_revisions",
    "portfolio_transactions",
    "valuation_runs",
    "valuation_assumptions",
    "valuation_input_refs",
    "evidence_items",
    "evidence_links",
    "audit_events",
    "outbox_events",
    "position_snapshots",
    "portfolio_snapshots",
    "thesis_events",
    "research_events",
]

FUNCTION_SQL = """
CREATE OR REPLACE FUNCTION fn_reject_update()
RETURNS trigger AS $$
BEGIN
  RAISE EXCEPTION 'table % is append-only', TG_TABLE_NAME;
END $$ LANGUAGE plpgsql;
"""


def upgrade() -> None:
    op.execute(FUNCTION_SQL)
    for table in APPEND_ONLY_TABLES:
        op.execute(
            f"CREATE TRIGGER trg_{table}_no_update "
            f"BEFORE UPDATE OR DELETE ON {table} "
            f"FOR EACH ROW EXECUTE FUNCTION fn_reject_update();"
        )


def downgrade() -> None:
    for table in APPEND_ONLY_TABLES:
        op.execute(f"DROP TRIGGER IF EXISTS trg_{table}_no_update ON {table};")
    op.execute("DROP FUNCTION IF EXISTS fn_reject_update();")
