"""Add M3 ETF metric lineage and valuation-band fields.

Revision ID: d3e4f5a6b7c8
Revises: c2d3e4f5a6b7
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "d3e4f5a6b7c8"
down_revision: str | None = "c2d3e4f5a6b7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("etf_metric_snapshots", sa.Column("reference_nav_basis", sa.Text(), nullable=True))
    op.add_column("etf_metric_snapshots", sa.Column("valuation_band", sa.Text(), nullable=True))
    op.add_column("etf_metric_snapshots", sa.Column("band_basis", sa.Text(), nullable=True))
    op.add_column(
        "etf_metric_snapshots",
        sa.Column("band_inputs", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )
    op.add_column("etf_metric_snapshots", sa.Column("band_thresholds_hash", sa.Text(), nullable=True))
    op.add_column(
        "etf_metric_snapshots",
        sa.Column("details", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("etf_metric_snapshots", "details")
    op.drop_column("etf_metric_snapshots", "band_thresholds_hash")
    op.drop_column("etf_metric_snapshots", "band_inputs")
    op.drop_column("etf_metric_snapshots", "band_basis")
    op.drop_column("etf_metric_snapshots", "valuation_band")
    op.drop_column("etf_metric_snapshots", "reference_nav_basis")
