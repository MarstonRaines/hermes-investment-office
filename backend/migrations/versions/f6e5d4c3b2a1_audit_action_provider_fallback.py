"""audit_events.action 增加 PROVIDER_FALLBACK（TS-05 §5.2 冻结）

背景：
- TS-05 §5.2 冻结契约：fallback 发生时 audit_events 必有一行
  action=PROVIDER_FALLBACK（payload 完整记录 requested/actual/attempts/reason）；
- ts02 §8.3 的 AuditAction 枚举（M0 建表 CHECK）原无该值 —— 本迁移同步扩展，
  保证 ORM 枚举（唯一事实来源）与 DB CHECK 一致（alembic check 往返零差异）。

Revision ID: after append-only triggers
Revises: c9d4f2a1b3e5
"""

from alembic import op

revision = "f6e5d4c3b2a1"
down_revision = "c9d4f2a1b3e5"
branch_labels = None
depends_on = None

ACTIONS = (
    "'CREATE', 'UPDATE', 'APPROVE', 'REJECT', 'EXECUTE', 'REVERSE', "
    "'SUPERSEDE', 'STATUS_CHANGE', 'LOGIN', 'PROVIDER_FALLBACK'"
)
CONSTRAINT = "ck_audit_events_action_check"


def upgrade() -> None:
    # op.f()：该名已含约定前缀，禁止二次套用（与 M0 建表迁移一致）
    op.drop_constraint(op.f(CONSTRAINT), "audit_events", type_="check")
    op.create_check_constraint(op.f(CONSTRAINT), "audit_events", f"action IN ({ACTIONS})")


def downgrade() -> None:
    op.drop_constraint(op.f(CONSTRAINT), "audit_events", type_="check")
    op.create_check_constraint(
        op.f(CONSTRAINT), "audit_events",
        "action IN ('CREATE', 'UPDATE', 'APPROVE', 'REJECT', 'EXECUTE', "
        "'REVERSE', 'SUPERSEDE', 'STATUS_CHANGE', 'LOGIN')",
    )
