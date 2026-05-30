"""trade-lifecycle tables: orders, fills, positions, trades, risk_events, regime_snapshots.

Revision ID: 0003_trade_lifecycle
Revises: 0002_check_constraints
Create Date: 2026-05-30 00:00:00

A3 (#91). Adds the post-scanner half of the journal: the order/fill/
position/trade chain (§3.6-3.7), risk-supervisor events (§3.8, table
only -- #90 owns emission), and regime snapshots (§3.10). Enum
vocabularies are pinned as lowercase literals matching the model's
``Enum(values_callable=...)`` so the ORM persists ``.value`` strings that
satisfy these tables' CHECK constraints.

``positions`` is created before ``orders`` and ``trades`` (both FK it),
and ``orders`` before ``fills`` (FK), so create + downgrade ordering is
self-consistent. ``upgrade`` + ``downgrade`` round-trip is verified by
``tests/integration/test_journal_lifecycle_migrations.py``.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0003_trade_lifecycle"
down_revision = "0002_check_constraints"
branch_labels = None
depends_on = None


_ORDER_SIDES = ("buy", "sell")
_ORDER_TYPES = ("market", "marketable_limit", "limit", "stop")
_ORDER_INTENTS = ("entry", "exit")
_ORDER_STATUSES = (
    "pending",
    "submitted",
    "partially_filled",
    "filled",
    "cancelled",
    "rejected",
)
_POSITION_STATUSES = ("open", "closed")
_EXIT_REASONS = (
    "target_hit",
    "hard_stop",
    "jackknife",
    "macd_cross",
    "volume_dryup",
    "first_red_candle",
    "l2_weakness",
    "dilutive_news",
    "force_flatten",
)
_RISK_EVENT_KINDS = (
    "entry_blocked",
    "lockout_tripped",
    "force_flatten",
    "daily_max_loss",
    "consecutive_losers",
)
_REGIMES = ("cold", "neutral", "warm", "hot")


def upgrade() -> None:
    op.create_table(
        "positions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("ticker", sa.String(), nullable=False),
        sa.Column(
            "status",
            sa.Enum(*_POSITION_STATUSES, name="position_status"),
            nullable=False,
        ),
        sa.Column("requested_shares", sa.Integer(), nullable=False),
        sa.Column("filled_shares", sa.Integer(), nullable=False),
        sa.Column("opened_ts", sa.String(), nullable=False),
        sa.Column("closed_ts", sa.String(), nullable=True),
        sa.CheckConstraint(
            "(status = 'closed') = (closed_ts IS NOT NULL)",
            name="ck_positions_closed_ts",
        ),
        sa.CheckConstraint(
            "requested_shares > 0",
            name="ck_positions_requested_shares_positive",
        ),
        sa.CheckConstraint(
            "filled_shares >= 0",
            name="ck_positions_filled_shares_nonneg",
        ),
    )
    op.create_index(
        "ix_positions_ticker_opened_ts",
        "positions",
        ["ticker", "opened_ts"],
    )
    op.create_index(
        "ix_positions_open_by_ticker",
        "positions",
        ["ticker"],
        sqlite_where=sa.text("closed_ts IS NULL"),
        postgresql_where=sa.text("closed_ts IS NULL"),
    )

    op.create_table(
        "orders",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("ticker", sa.String(), nullable=False),
        sa.Column(
            "side",
            sa.Enum(*_ORDER_SIDES, name="order_side"),
            nullable=False,
        ),
        sa.Column(
            "order_type",
            sa.Enum(*_ORDER_TYPES, name="order_type"),
            nullable=False,
        ),
        sa.Column(
            "intent",
            sa.Enum(*_ORDER_INTENTS, name="order_intent"),
            nullable=False,
        ),
        sa.Column(
            "status",
            sa.Enum(*_ORDER_STATUSES, name="order_status"),
            nullable=False,
        ),
        sa.Column("requested_shares", sa.Integer(), nullable=False),
        sa.Column("limit_price", sa.String(), nullable=True),
        sa.Column("stop_price", sa.String(), nullable=True),
        sa.Column("target_price", sa.String(), nullable=True),
        sa.Column(
            "position_id",
            sa.Integer(),
            sa.ForeignKey("positions.id"),
            nullable=True,
        ),
        sa.Column("created_ts", sa.String(), nullable=False),
        sa.CheckConstraint(
            "intent != 'entry' OR stop_price IS NOT NULL",
            name="ck_orders_entry_requires_stop",
        ),
        sa.CheckConstraint(
            "requested_shares > 0",
            name="ck_orders_requested_shares_positive",
        ),
    )
    op.create_index(
        "ix_orders_ticker_created_ts",
        "orders",
        ["ticker", "created_ts"],
    )
    op.create_index("ix_orders_position_id", "orders", ["position_id"])

    op.create_table(
        "fills",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "order_id",
            sa.Integer(),
            sa.ForeignKey("orders.id"),
            nullable=False,
        ),
        sa.Column("filled_shares", sa.Integer(), nullable=False),
        sa.Column("fill_price", sa.String(), nullable=False),
        sa.Column("fill_ts", sa.String(), nullable=False),
        sa.CheckConstraint(
            "filled_shares > 0",
            name="ck_fills_filled_shares_positive",
        ),
    )
    op.create_index("ix_fills_order_id", "fills", ["order_id"])

    op.create_table(
        "trades",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "position_id",
            sa.Integer(),
            sa.ForeignKey("positions.id"),
            nullable=False,
        ),
        sa.Column("realized_pnl", sa.String(), nullable=False),
        sa.Column("opened_ts", sa.String(), nullable=False),
        sa.Column("closed_ts", sa.String(), nullable=False),
        sa.Column(
            "exit_reason",
            sa.Enum(*_EXIT_REASONS, name="exit_reason"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "closed_ts >= opened_ts",
            name="ck_trades_closed_after_opened",
        ),
    )
    op.create_index("ix_trades_position_id", "trades", ["position_id"])
    op.create_index("ix_trades_closed_ts", "trades", ["closed_ts"])

    op.create_table(
        "risk_events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("event_ts", sa.String(), nullable=False),
        sa.Column(
            "kind",
            sa.Enum(*_RISK_EVENT_KINDS, name="risk_event_kind"),
            nullable=False,
        ),
        sa.Column("reason", sa.String(), nullable=False),
        sa.Column("related_ticker", sa.String(), nullable=True),
    )
    op.create_index("ix_risk_events_event_ts", "risk_events", ["event_ts"])
    op.create_index(
        "ix_risk_events_kind_ts",
        "risk_events",
        ["kind", "event_ts"],
    )

    op.create_table(
        "regime_snapshots",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("snapshot_ts", sa.String(), nullable=False),
        sa.Column(
            "regime",
            sa.Enum(*_REGIMES, name="regime"),
            nullable=False,
        ),
        sa.Column("score", sa.String(), nullable=False),
        sa.Column("components", sa.String(), nullable=True),
    )
    op.create_index(
        "ix_regime_snapshots_snapshot_ts",
        "regime_snapshots",
        ["snapshot_ts"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_regime_snapshots_snapshot_ts",
        table_name="regime_snapshots",
    )
    op.drop_table("regime_snapshots")

    op.drop_index("ix_risk_events_kind_ts", table_name="risk_events")
    op.drop_index("ix_risk_events_event_ts", table_name="risk_events")
    op.drop_table("risk_events")

    op.drop_index("ix_trades_closed_ts", table_name="trades")
    op.drop_index("ix_trades_position_id", table_name="trades")
    op.drop_table("trades")

    op.drop_index("ix_fills_order_id", table_name="fills")
    op.drop_table("fills")

    op.drop_index("ix_orders_position_id", table_name="orders")
    op.drop_index("ix_orders_ticker_created_ts", table_name="orders")
    op.drop_table("orders")

    op.drop_index("ix_positions_open_by_ticker", table_name="positions")
    op.drop_index("ix_positions_ticker_opened_ts", table_name="positions")
    op.drop_table("positions")
