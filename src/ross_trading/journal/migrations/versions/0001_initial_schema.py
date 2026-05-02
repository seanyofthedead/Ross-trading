"""initial scanner-journal schema: picks, watchlist_entries, scanner_decisions.

Revision ID: 0001_initial
Revises:
Create Date: 2026-05-02 00:00:00

Creates all three journal tables with the four-value ``DecisionKind`` enum
and the seven-value ``RejectionReason`` enum already populated, even
though A3 only emits three of the kinds today and the rejection enum is
unused until #51. Landing the full vocabulary now keeps #51 a wiring-only
change.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None


_DECISION_KINDS = ("picked", "stale_feed", "feed_gap", "rejected")
_REJECTION_REASONS = (
    "no_snapshot",
    "missing_baseline",
    "missing_float",
    "rel_volume",
    "pct_change",
    "price_band",
    "float_size",
)


def upgrade() -> None:
    op.create_table(
        "picks",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("ticker", sa.String(), nullable=False),
        sa.Column("ts", sa.String(), nullable=False),
        sa.Column("rel_volume", sa.String(), nullable=False),
        sa.Column("pct_change", sa.String(), nullable=False),
        sa.Column("price", sa.String(), nullable=False),
        sa.Column("float_shares", sa.Integer(), nullable=False),
        sa.Column("news_present", sa.Boolean(), nullable=False),
        sa.Column("headline_count", sa.Integer(), nullable=False),
        sa.Column("rank", sa.Integer(), nullable=False),
    )
    op.create_index("ix_picks_ticker_ts", "picks", ["ticker", "ts"])

    op.create_table(
        "watchlist_entries",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("ticker", sa.String(), nullable=False),
        sa.Column(
            "pick_id",
            sa.Integer(),
            sa.ForeignKey("picks.id"),
            nullable=False,
        ),
        sa.Column("added_at", sa.String(), nullable=False),
        sa.Column("removed_at", sa.String(), nullable=True),
    )
    op.create_index(
        "ix_watchlist_active_by_ticker",
        "watchlist_entries",
        ["ticker"],
        sqlite_where=sa.text("removed_at IS NULL"),
        postgresql_where=sa.text("removed_at IS NULL"),
    )

    op.create_table(
        "scanner_decisions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "kind",
            sa.Enum(*_DECISION_KINDS, name="decision_kind"),
            nullable=False,
        ),
        sa.Column("decision_ts", sa.String(), nullable=False),
        sa.Column("ticker", sa.String(), nullable=True),
        sa.Column(
            "pick_id",
            sa.Integer(),
            sa.ForeignKey("picks.id"),
            nullable=True,
        ),
        sa.Column("reason", sa.String(), nullable=True),
        sa.Column("gap_start", sa.String(), nullable=True),
        sa.Column("gap_end", sa.String(), nullable=True),
        sa.Column(
            "rejection_reason",
            sa.Enum(*_REJECTION_REASONS, name="rejection_reason"),
            nullable=True,
        ),
    )
    op.create_index(
        "ix_scanner_decisions_decision_ts",
        "scanner_decisions",
        ["decision_ts"],
    )
    op.create_index(
        "ix_scanner_decisions_kind_ts",
        "scanner_decisions",
        ["kind", "decision_ts"],
    )


def downgrade() -> None:
    op.drop_index("ix_scanner_decisions_kind_ts", table_name="scanner_decisions")
    op.drop_index("ix_scanner_decisions_decision_ts", table_name="scanner_decisions")
    op.drop_table("scanner_decisions")
    op.drop_index("ix_watchlist_active_by_ticker", table_name="watchlist_entries")
    op.drop_table("watchlist_entries")
    op.drop_index("ix_picks_ticker_ts", table_name="picks")
    op.drop_table("picks")
