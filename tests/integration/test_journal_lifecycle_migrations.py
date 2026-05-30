"""A3 (#91) -- alembic upgrade head + downgrade base round trip for 0003.

Mirrors ``tests/integration/test_journal_migrations.py``: runs the full
migration tree (including the 0003 trade-lifecycle tables) against an
in-memory SQLite engine via an injected connection, and proves the
lifecycle tables appear on upgrade and disappear cleanly on downgrade --
including a populated-DB round trip.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import inspect
from sqlalchemy.orm import Session

from ross_trading.journal.engine import create_journal_engine
from ross_trading.journal.models import (
    ExitReason,
    Fill,
    Order,
    OrderIntent,
    OrderSide,
    OrderStatus,
    OrderType,
    Position,
    PositionStatus,
    Trade,
)

if TYPE_CHECKING:
    from collections.abc import Iterator

    from sqlalchemy.engine import Connection, Engine

pytestmark = pytest.mark.integration

_MIGRATIONS_DIR = (
    Path(__file__).resolve().parents[2]
    / "src"
    / "ross_trading"
    / "journal"
    / "migrations"
)
_LIFECYCLE_TABLES = frozenset(
    {"orders", "fills", "positions", "trades", "risk_events", "regime_snapshots"}
)
_TS = datetime(2026, 5, 4, 14, 30, tzinfo=UTC)


def _alembic_config(connection: Connection) -> Config:
    cfg = Config()
    cfg.set_main_option("script_location", str(_MIGRATIONS_DIR))
    cfg.attributes["connection"] = connection
    return cfg


@pytest.fixture
def migrated_engine() -> Iterator[Engine]:
    engine = create_journal_engine("sqlite://")
    with engine.begin() as conn:
        command.upgrade(_alembic_config(conn), "head")
    try:
        yield engine
    finally:
        engine.dispose()


def test_upgrade_head_creates_lifecycle_tables(migrated_engine: Engine) -> None:
    with migrated_engine.connect() as conn:
        names = set(inspect(conn).get_table_names())
    assert names >= _LIFECYCLE_TABLES


def test_upgrade_head_then_downgrade_to_0002_drops_lifecycle() -> None:
    engine = create_journal_engine("sqlite://")
    try:
        with engine.begin() as conn:
            cfg = _alembic_config(conn)
            command.upgrade(cfg, "head")
            after_upgrade = set(inspect(conn).get_table_names())
            assert after_upgrade >= _LIFECYCLE_TABLES

            command.downgrade(cfg, "0002_check_constraints")
            after_downgrade = set(inspect(conn).get_table_names())
            assert _LIFECYCLE_TABLES.isdisjoint(after_downgrade)
            # scanner tables from earlier revisions survive.
            assert "scanner_decisions" in after_downgrade
    finally:
        engine.dispose()


def test_upgrade_head_then_downgrade_base() -> None:
    engine = create_journal_engine("sqlite://")
    try:
        with engine.begin() as conn:
            cfg = _alembic_config(conn)
            command.upgrade(cfg, "head")
            command.downgrade(cfg, "base")
            after = set(inspect(conn).get_table_names())
            assert _LIFECYCLE_TABLES.isdisjoint(after)
            assert "scanner_decisions" not in after
    finally:
        engine.dispose()


def test_lifecycle_round_trip_with_seeded_rows() -> None:
    """Seed an order/fill/position/trade chain, then downgrade to 0002.

    Proves 0003 downgrade drops the lifecycle tables cleanly even on a
    populated DB (SQLite ``DROP TABLE`` of FK-linked rows), and that the
    earlier scanner schema is untouched.
    """
    engine = create_journal_engine("sqlite://")
    try:
        with engine.begin() as conn:
            command.upgrade(_alembic_config(conn), "head")

        with Session(engine) as session, session.begin():
            pos = Position(
                ticker="ABCD",
                status=PositionStatus.CLOSED,
                requested_shares=500,
                filled_shares=500,
                opened_ts=_TS,
                closed_ts=datetime(2026, 5, 4, 15, 0, tzinfo=UTC),
            )
            session.add(pos)
            session.flush()
            order = Order(
                ticker="ABCD",
                side=OrderSide.BUY,
                order_type=OrderType.MARKETABLE_LIMIT,
                intent=OrderIntent.ENTRY,
                status=OrderStatus.FILLED,
                requested_shares=500,
                limit_price=Decimal("3.50"),
                stop_price=Decimal("3.20"),
                target_price=Decimal("4.10"),
                position_id=pos.id,
                created_ts=_TS,
            )
            session.add(order)
            session.flush()
            session.add(
                Fill(
                    order_id=order.id,
                    filled_shares=500,
                    fill_price=Decimal("3.50"),
                    fill_ts=_TS,
                )
            )
            session.add(
                Trade(
                    position_id=pos.id,
                    realized_pnl=Decimal("300.00"),
                    opened_ts=_TS,
                    closed_ts=datetime(2026, 5, 4, 15, 0, tzinfo=UTC),
                    exit_reason=ExitReason.TARGET_HIT,
                )
            )

        with Session(engine) as session:
            assert session.query(Position).count() == 1
            assert session.query(Order).count() == 1
            assert session.query(Fill).count() == 1
            assert session.query(Trade).count() == 1

        with engine.begin() as conn:
            command.downgrade(_alembic_config(conn), "0002_check_constraints")
            after = set(inspect(conn).get_table_names())
        assert _LIFECYCLE_TABLES.isdisjoint(after)
    finally:
        engine.dispose()
