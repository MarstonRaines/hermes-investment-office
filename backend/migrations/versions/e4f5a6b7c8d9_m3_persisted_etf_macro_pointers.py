"""Persisted ETF/macro pointer extensions for M3 jobs.

Revision ID: e4f5a6b7c8d9
Revises: d3e4f5a6b7c8
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "e4f5a6b7c8d9"
down_revision: str | None = "d3e4f5a6b7c8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Existing tables become PG pointers to their Parquet numeric payloads.
    op.add_column("etf_nav_observations", sa.Column("parquet_path", sa.Text(), nullable=True))
    op.add_column("fx_observations", sa.Column("parquet_path", sa.Text(), nullable=True))

    # ADR-007's pointer is extended with an explicit artifact kind so index
    # valuation rows cannot collide with a price row on the same date/provider.
    op.add_column(
        "index_bar_index",
        sa.Column("data_kind", sa.Text(), server_default="PRICE", nullable=False),
    )
    op.drop_constraint(
        "uq_index_bar_index_inst_date_provider", "index_bar_index", type_="unique"
    )
    op.create_unique_constraint(
        "uq_index_bar_index_inst_date_provider",
        "index_bar_index",
        ["instrument_id", "trade_date", "provider", "data_kind"],
    )
    op.create_check_constraint(
        op.f("ck_index_bar_index_data_kind_check"),
        "index_bar_index",
        "data_kind IN ('PRICE', 'VALUATION')",
    )



def downgrade() -> None:
    op.drop_constraint(
        op.f("ck_index_bar_index_data_kind_check"),
        "index_bar_index", type_="check",
    )
    op.drop_constraint(
        "uq_index_bar_index_inst_date_provider", "index_bar_index", type_="unique"
    )
    op.create_unique_constraint(
        "uq_index_bar_index_inst_date_provider",
        "index_bar_index",
        ["instrument_id", "trade_date", "provider"],
    )
    op.drop_column("index_bar_index", "data_kind")
    op.drop_column("fx_observations", "parquet_path")
    op.drop_column("etf_nav_observations", "parquet_path")
