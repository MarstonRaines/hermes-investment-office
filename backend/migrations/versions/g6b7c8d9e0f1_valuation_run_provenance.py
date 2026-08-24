"""Attach the derived-engine provenance record to completed valuation runs."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "g6b7c8d9e0f1"
down_revision: str | None = "f5a6b7c8d9e0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "valuation_runs",
        sa.Column("provenance_id", sa.UUID(), nullable=True),
    )
    op.create_foreign_key(
        op.f("fk_valuation_runs_provenance_id_provenance_records"),
        "valuation_runs",
        "provenance_records",
        ["provenance_id"],
        ["provenance_id"],
    )


def downgrade() -> None:
    op.drop_constraint(
        op.f("fk_valuation_runs_provenance_id_provenance_records"),
        "valuation_runs",
        type_="foreignkey",
    )
    op.drop_column("valuation_runs", "provenance_id")
