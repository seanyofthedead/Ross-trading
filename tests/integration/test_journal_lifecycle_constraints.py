"""A3 (#91) -- CHECK/FK enforcement on the trade-lifecycle tables.

Each test inserts a violating row via ``exec_driver_sql`` (or the writer)
so the assertion is "the database rejects this", not "the ORM happens to
refuse". The migrated-engine fixture runs ``alembic upgrade head`` so the
constraints under test are exactly the ones the migration produces.

Constraints exercised:

* ``ck_orders_entry_requires_stop`` -- an ENTRY order must carry a stop.
* ``ck_orders_requested_shares_positive`` -- shares > 0.
* ``ck_fills_filled_shares_positive`` -- filled shares > 0.
* fills.order_id FK -- a fill cannot reference a missing order.
* ``ck_positions_closed_ts`` -- closed iff closed_ts present.
* ``ck_trades_closed_after_opened`` -- closed_ts >= opened_ts.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy.exc import IntegrityError

from ross_trading.journal.engine import create_journal_engine, create_session_factory
from ross_trading.journal.models import OrderIntent, OrderSide, OrderStatus, OrderType
from ross_trading.journal.writer import JournalWriter

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


# --------------------------------------------- orders: entry-requires-stop


def test_entry_order_without_stop_rejected(migrated_engine: Engine) -> None:
    with migrated_engine.begin() as conn, pytest.raises(IntegrityError):
        conn.exec_driver_sql(
            "INSERT INTO orders "
            "(ticker, side, order_type, intent, status, requested_shares, "
            "limit_price, stop_price, target_price, position_id, created_ts) "
            "VALUES ('ABCD', 'buy', 'marketable_limit', 'entry', 'submitted', "
            "500, '3.50', NULL, '4.10', NULL, '2026-05-04T14:30:00+00:00')",
        )


def test_exit_order_without_stop_accepted(migrated_engine: Engine) -> None:
    """The stop requirement is ENTRY-only; exit legs may omit it."""
    with migrated_engine.begin() as conn:
        conn.exec_driver_sql(
            "INSERT INTO orders "
            "(ticker, side, order_type, intent, status, requested_shares, "
            "limit_price, stop_price, target_price, position_id, created_ts) "
            "VALUES ('ABCD', 'sell', 'market', 'exit', 'submitted', "
            "500, NULL, NULL, NULL, NULL, '2026-05-04T14:30:00+00:00')",
        )


def test_order_with_zero_shares_rejected(migrated_engine: Engine) -> None:
    with migrated_engine.begin() as conn, pytest.raises(IntegrityError):
        conn.exec_driver_sql(
            "INSERT INTO orders "
            "(ticker, side, order_type, intent, status, requested_shares, "
            "limit_price, stop_price, target_price, position_id, created_ts) "
            "VALUES ('ABCD', 'sell', 'market', 'exit', 'submitted', "
            "0, NULL, NULL, NULL, NULL, '2026-05-04T14:30:00+00:00')",
        )


# ----------------------------------------------------------- fills: FK + >0


def test_fill_referencing_missing_order_rejected(migrated_engine: Engine) -> None:
    with migrated_engine.begin() as conn, pytest.raises(IntegrityError):
        conn.exec_driver_sql(
            "INSERT INTO fills (order_id, filled_shares, fill_price, fill_ts) "
            "VALUES (9999, 100, '3.50', '2026-05-04T14:30:00+00:00')",
        )


def test_fill_with_zero_shares_rejected(migrated_engine: Engine) -> None:
    with migrated_engine.begin() as conn:
        conn.exec_driver_sql(
            "INSERT INTO orders "
            "(ticker, side, order_type, intent, status, requested_shares, "
            "limit_price, stop_price, target_price, position_id, created_ts) "
            "VALUES ('ABCD', 'sell', 'market', 'exit', 'submitted', "
            "500, NULL, NULL, NULL, NULL, '2026-05-04T14:30:00+00:00')",
        )
        order_id = conn.exec_driver_sql("SELECT id FROM orders").scalar_one()

    with migrated_engine.begin() as conn, pytest.raises(IntegrityError):
        conn.exec_driver_sql(
            "INSERT INTO fills (order_id, filled_shares, fill_price, fill_ts) "
            "VALUES (:oid, 0, '3.50', '2026-05-04T14:30:00+00:00')",
            {"oid": order_id},
        )


# ----------------------------------------------------- positions: closed_ts


def test_closed_position_without_closed_ts_rejected(migrated_engine: Engine) -> None:
    with migrated_engine.begin() as conn, pytest.raises(IntegrityError):
        conn.exec_driver_sql(
            "INSERT INTO positions "
            "(ticker, status, requested_shares, filled_shares, opened_ts, closed_ts) "
            "VALUES ('ABCD', 'closed', 500, 500, '2026-05-04T14:30:00+00:00', NULL)",
        )


def test_open_position_with_closed_ts_rejected(migrated_engine: Engine) -> None:
    with migrated_engine.begin() as conn, pytest.raises(IntegrityError):
        conn.exec_driver_sql(
            "INSERT INTO positions "
            "(ticker, status, requested_shares, filled_shares, opened_ts, closed_ts) "
            "VALUES ('ABCD', 'open', 500, 0, "
            "'2026-05-04T14:30:00+00:00', '2026-05-04T14:30:00+00:00')",
        )


# ----------------------------------------------------------- trades: timing


def test_trade_closed_before_opened_rejected(migrated_engine: Engine) -> None:
    with migrated_engine.begin() as conn:
        conn.exec_driver_sql(
            "INSERT INTO positions "
            "(ticker, status, requested_shares, filled_shares, opened_ts, closed_ts) "
            "VALUES ('ABCD', 'closed', 500, 500, "
            "'2026-05-04T14:30:00+00:00', '2026-05-04T14:30:00+00:00')",
        )
        pos_id = conn.exec_driver_sql("SELECT id FROM positions").scalar_one()

    with migrated_engine.begin() as conn, pytest.raises(IntegrityError):
        conn.exec_driver_sql(
            "INSERT INTO trades "
            "(position_id, realized_pnl, opened_ts, closed_ts, exit_reason) "
            "VALUES (:pid, '100.0', '2026-05-04T15:00:00+00:00', "
            "'2026-05-04T14:30:00+00:00', 'target_hit')",
            {"pid": pos_id},
        )


# ------------------------------- writer-level atomic rollback (#91 surface)


def test_writer_entry_without_stop_rolls_back_atomically(
    migrated_engine: Engine,
) -> None:
    """``record_order`` for an ENTRY without a stop rolls back -- no row leaks."""
    factory = create_session_factory(migrated_engine)
    writer = JournalWriter(factory)

    with pytest.raises(IntegrityError):
        writer.record_order(
            ticker="ABCD",
            side=OrderSide.BUY,
            order_type=OrderType.MARKETABLE_LIMIT,
            intent=OrderIntent.ENTRY,
            status=OrderStatus.SUBMITTED,
            requested_shares=500,
            created_ts=datetime(2026, 5, 4, 14, 30, tzinfo=UTC),
            limit_price=Decimal("3.50"),
            stop_price=None,  # violates ck_orders_entry_requires_stop
        )

    with migrated_engine.connect() as conn:
        count = conn.exec_driver_sql("SELECT COUNT(*) FROM orders").scalar_one()
    assert count == 0


def test_writer_fill_referencing_missing_order_rolls_back(
    migrated_engine: Engine,
) -> None:
    factory = create_session_factory(migrated_engine)
    writer = JournalWriter(factory)

    with pytest.raises(IntegrityError):
        writer.record_fill(
            order_id=9999,
            filled_shares=100,
            fill_price=Decimal("3.50"),
            fill_ts=datetime(2026, 5, 4, 14, 30, tzinfo=UTC),
        )

    with migrated_engine.connect() as conn:
        count = conn.exec_driver_sql("SELECT COUNT(*) FROM fills").scalar_one()
    assert count == 0
