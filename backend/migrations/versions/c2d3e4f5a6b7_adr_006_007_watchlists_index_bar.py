"""ADR-006 watchlists and ADR-007 index bar pointer.

Revision ID: c2d3e4f5a6b7
Revises: b1c2d3e4f5a6
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c2d3e4f5a6b7"
down_revision: str | None = "b1c2d3e4f5a6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add first-class watchlists and the index-history PG pointer."""
    # Deliberately no seed INSERT here: existing instruments may not contain the
    # proposed ETF pool, and a migration must never overwrite user data.
    op.create_table(
        "watchlists",
        sa.Column("watchlist_id", sa.UUID(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("status", sa.Text(), server_default="ACTIVE", nullable=False),
        sa.Column("updated_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint(
            "status IN ('ACTIVE', 'ARCHIVED')",
            name=op.f("ck_watchlists_status_check"),
        ),
        sa.PrimaryKeyConstraint("watchlist_id", name=op.f("pk_watchlists")),
    )
    op.create_table(
        "watchlist_members",
        sa.Column("watchlist_member_id", sa.UUID(), nullable=False),
        sa.Column("watchlist_id", sa.UUID(), nullable=False),
        sa.Column("instrument_id", sa.UUID(), nullable=False),
        sa.Column("added_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("removed_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("watchlist_member_id", name=op.f("pk_watchlist_members")),
        sa.UniqueConstraint(
            "watchlist_id", "instrument_id",
            name="uq_watchlist_members_watchlist_instrument",
        ),
    )
    op.create_index(
        "ix_watchlist_members_active",
        "watchlist_members",
        ["watchlist_id", "removed_at"],
        unique=False,
    )
    op.create_table(
        "index_bar_index",
        sa.Column("index_bar_id", sa.UUID(), nullable=False),
        sa.Column("instrument_id", sa.UUID(), nullable=False),
        sa.Column("trade_date", sa.Date(), nullable=False),
        sa.Column("provider", sa.Text(), nullable=False),
        sa.Column("source_timestamp", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("ingested_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("quality_status", sa.Text(), nullable=False),
        sa.Column("provenance_id", sa.UUID(), nullable=False),
        sa.Column("parquet_path", sa.Text(), nullable=False),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint(
            "quality_status IN ('VERIFIED', 'ACCEPTABLE', 'STALE', 'CONFLICT', 'REJECTED')",
            name=op.f("ck_index_bar_index_quality_status_check"),
        ),
        sa.PrimaryKeyConstraint("index_bar_id", name=op.f("pk_index_bar_index")),
        sa.UniqueConstraint(
            "instrument_id", "trade_date", "provider",
            name="uq_index_bar_index_inst_date_provider",
        ),
    )
    op.create_foreign_key(
        op.f("fk_watchlist_members_watchlist_id_watchlists"),
        "watchlist_members", "watchlists",
        ["watchlist_id"], ["watchlist_id"],
    )
    op.create_foreign_key(
        op.f("fk_watchlist_members_instrument_id_instruments"),
        "watchlist_members", "instruments",
        ["instrument_id"], ["instrument_id"],
    )
    op.create_foreign_key(
        op.f("fk_index_bar_index_instrument_id_instruments"),
        "index_bar_index", "instruments",
        ["instrument_id"], ["instrument_id"],
    )
    op.create_foreign_key(
        op.f("fk_index_bar_index_provenance_id_provenance_records"),
        "index_bar_index", "provenance_records",
        ["provenance_id"], ["provenance_id"],
    )


def downgrade() -> None:
    """Remove only the tables introduced by this revision."""
    op.drop_table("index_bar_index")
    op.drop_index("ix_watchlist_members_active", table_name="watchlist_members")
    op.drop_table("watchlist_members")
    op.drop_table("watchlists")
