"""M2 ledger corrections and proposal decision lineage."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "f5a6b7c8d9e0"
down_revision: str | None = "e4f5a6b7c8d9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_constraint(
        op.f("ck_portfolio_transactions_transaction_type_check"),
        "portfolio_transactions", type_="check",
    )
    op.create_check_constraint(
        op.f("ck_portfolio_transactions_transaction_type_check"),
        "portfolio_transactions",
        "transaction_type IN ('BUY', 'SELL', 'DIVIDEND', 'FEE', 'CASH_IN', 'CASH_OUT', 'REVERSAL')",
    )
    op.drop_constraint(
        op.f("ck_portfolio_transactions_instrument_required"),
        "portfolio_transactions", type_="check",
    )
    op.create_check_constraint(
        op.f("ck_portfolio_transactions_instrument_required"),
        "portfolio_transactions",
        "transaction_type IN ('CASH_IN','CASH_OUT','REVERSAL') OR instrument_id IS NOT NULL",
    )
    op.drop_constraint(op.f("ck_audit_events_action_check"), "audit_events", type_="check")
    op.create_check_constraint(
        op.f("ck_audit_events_action_check"), "audit_events",
        "action IN ('CREATE', 'UPDATE', 'APPROVE', 'REJECT', 'EXECUTE', 'REVERSE', 'SUPERSEDE', 'STATUS_CHANGE', 'LOGIN', 'PROVIDER_FALLBACK', 'FRESHNESS_CHANGE', 'PERMISSION_DENIED')",
    )
    op.add_column(
        "trade_proposals",
        sa.Column("linked_valuation_run_id", sa.UUID(), nullable=True),
    )
    op.add_column("trade_proposals", sa.Column("provenance_id", sa.UUID(), nullable=True))
    op.create_foreign_key(
        op.f("fk_trade_proposals_linked_valuation_run_id_valuation_runs"),
        "trade_proposals", "valuation_runs",
        ["linked_valuation_run_id"], ["valuation_run_id"],
    )
    op.create_foreign_key(
        op.f("fk_trade_proposals_provenance_id_provenance_records"),
        "trade_proposals", "provenance_records",
        ["provenance_id"], ["provenance_id"],
    )


def downgrade() -> None:
    op.drop_constraint(
        op.f("fk_trade_proposals_provenance_id_provenance_records"),
        "trade_proposals", type_="foreignkey",
    )
    op.drop_constraint(
        op.f("fk_trade_proposals_linked_valuation_run_id_valuation_runs"),
        "trade_proposals", type_="foreignkey",
    )
    op.drop_column("trade_proposals", "provenance_id")
    op.drop_column("trade_proposals", "linked_valuation_run_id")
    op.drop_constraint(op.f("ck_audit_events_action_check"), "audit_events", type_="check")
    op.create_check_constraint(
        op.f("ck_audit_events_action_check"), "audit_events",
        "action IN ('CREATE', 'UPDATE', 'APPROVE', 'REJECT', 'EXECUTE', 'REVERSE', 'SUPERSEDE', 'STATUS_CHANGE', 'LOGIN', 'PROVIDER_FALLBACK')",
    )
    op.drop_constraint(
        op.f("ck_portfolio_transactions_transaction_type_check"),
        "portfolio_transactions", type_="check",
    )
    op.create_check_constraint(
        op.f("ck_portfolio_transactions_transaction_type_check"),
        "portfolio_transactions",
        "transaction_type IN ('BUY', 'SELL', 'DIVIDEND', 'FEE', 'CASH_IN', 'CASH_OUT')",
    )
    op.drop_constraint(
        op.f("ck_portfolio_transactions_instrument_required"),
        "portfolio_transactions", type_="check",
    )
    op.create_check_constraint(
        op.f("ck_portfolio_transactions_instrument_required"),
        "portfolio_transactions",
        "transaction_type IN ('CASH_IN','CASH_OUT') OR instrument_id IS NOT NULL",
    )
