"""Atom A5 (#44) -- CHECK-constraint enforcement on ``scanner_decisions``.

Carries the deferred P2 review item from #43 / PR #54: the kind ->
field-population invariants now live at the schema level, so a buggy
writer cannot silently insert a malformed row.

The five CHECKs (introduced by migration ``0002_*``):

* ``(kind = 'picked')   = (pick_id IS NOT NULL)``
* ``(kind = 'rejected') = (rejection_reason IS NOT NULL)``
* ``(kind = 'feed_gap') = (gap_start IS NOT NULL)``
* ``(kind = 'feed_gap') = (gap_end IS NOT NULL)``
* ``kind IN ('stale_feed', 'feed_gap') OR ticker IS NOT NULL``

Each test inserts a violating row via ``exec_driver_sql`` so the assertion
is "the database rejects this," not "the ORM/writer happens to refuse."
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
from sqlalchemy.orm import Session

from ross_trading.journal.engine import create_journal_engine
from ross_trading.journal.models import (
    DecisionKind,
    Pick,
    RejectionReason,
)
from ross_trading.journal.models import (
    ScannerDecision as ScannerDecisionRow,
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


def _alembic_config(connection: Connection) -> Config:
    cfg = Config()
    cfg.set_main_option("script_location", str(_MIGRATIONS_DIR))
    cfg.attributes["connection"] = connection
    return cfg


@pytest.fixture
def migrated_engine() -> Iterator[Engine]:
    """Fresh engine migrated to head (function-scoped: each test isolated)."""
    engine = create_journal_engine("sqlite://")
    with engine.begin() as conn:
        command.upgrade(_alembic_config(conn), "head")
    try:
        yield engine
    finally:
        engine.dispose()


# ---------------------------------------------------- per-CHECK violations


def test_picked_without_pick_id_rejected(migrated_engine: Engine) -> None:
    """``kind='picked'`` requires ``pick_id IS NOT NULL``."""
    with migrated_engine.begin() as conn, pytest.raises(IntegrityError):
        conn.exec_driver_sql(
            "INSERT INTO scanner_decisions "
            "(kind, decision_ts, ticker, pick_id, reason, gap_start, gap_end, rejection_reason) "
            "VALUES ('picked', '2026-05-02T14:30:00+00:00', 'ABCD', NULL, NULL, NULL, NULL, NULL)",
        )


def test_non_picked_with_pick_id_rejected(migrated_engine: Engine) -> None:
    """The biconditional cuts both ways: non-picked rows must NOT carry a pick_id."""
    # First seed a real Pick row to satisfy the FK (otherwise we'd hit the
    # foreign-key check before the CHECK).
    with migrated_engine.begin() as conn:
        conn.exec_driver_sql(
            "INSERT INTO picks "
            "(ticker, ts, rel_volume, pct_change, price, "
            "float_shares, news_present, headline_count, rank) "
            "VALUES ('ABCD', '2026-05-02T14:30:00+00:00', '12.5', '18.75', '3.42', "
            "8500000, 1, 4, 1)",
        )
        pick_id = conn.exec_driver_sql("SELECT id FROM picks").scalar_one()

    with migrated_engine.begin() as conn, pytest.raises(IntegrityError):
        conn.exec_driver_sql(
            "INSERT INTO scanner_decisions "
            "(kind, decision_ts, ticker, pick_id, reason, gap_start, gap_end, rejection_reason) "
            "VALUES ('stale_feed', '2026-05-02T14:30:00+00:00', NULL, :pick_id, "
            "'stale', NULL, NULL, NULL)",
            {"pick_id": pick_id},
        )


def test_rejected_without_rejection_reason_rejected(migrated_engine: Engine) -> None:
    """``kind='rejected'`` requires ``rejection_reason IS NOT NULL``."""
    with migrated_engine.begin() as conn, pytest.raises(IntegrityError):
        conn.exec_driver_sql(
            "INSERT INTO scanner_decisions "
            "(kind, decision_ts, ticker, pick_id, reason, "
            "gap_start, gap_end, rejection_reason) "
            "VALUES ('rejected', '2026-05-02T14:30:00+00:00', 'ABCD', "
            "NULL, NULL, NULL, NULL, NULL)",
        )


def test_non_rejected_with_rejection_reason_rejected(migrated_engine: Engine) -> None:
    """Biconditional: only ``rejected`` rows may carry a rejection_reason."""
    with migrated_engine.begin() as conn, pytest.raises(IntegrityError):
        conn.exec_driver_sql(
            "INSERT INTO scanner_decisions "
            "(kind, decision_ts, ticker, pick_id, reason, gap_start, gap_end, rejection_reason) "
            "VALUES ('stale_feed', '2026-05-02T14:30:00+00:00', NULL, NULL, "
            "'stale', NULL, NULL, 'rel_volume')",
        )


def test_feed_gap_without_gap_start_rejected(migrated_engine: Engine) -> None:
    """``kind='feed_gap'`` requires ``gap_start IS NOT NULL``."""
    with migrated_engine.begin() as conn, pytest.raises(IntegrityError):
        conn.exec_driver_sql(
            "INSERT INTO scanner_decisions "
            "(kind, decision_ts, ticker, pick_id, reason, gap_start, gap_end, rejection_reason) "
            "VALUES ('feed_gap', '2026-05-02T14:30:00+00:00', NULL, NULL, NULL, "
            "NULL, '2026-05-02T14:30:00+00:00', NULL)",
        )


def test_feed_gap_without_gap_end_rejected(migrated_engine: Engine) -> None:
    """``kind='feed_gap'`` requires ``gap_end IS NOT NULL``."""
    with migrated_engine.begin() as conn, pytest.raises(IntegrityError):
        conn.exec_driver_sql(
            "INSERT INTO scanner_decisions "
            "(kind, decision_ts, ticker, pick_id, reason, gap_start, gap_end, rejection_reason) "
            "VALUES ('feed_gap', '2026-05-02T14:30:00+00:00', NULL, NULL, NULL, "
            "'2026-05-02T14:29:00+00:00', NULL, NULL)",
        )


def test_non_feed_gap_with_gap_start_rejected(migrated_engine: Engine) -> None:
    """Biconditional: gap_start belongs only on ``feed_gap`` rows."""
    with migrated_engine.begin() as conn, pytest.raises(IntegrityError):
        conn.exec_driver_sql(
            "INSERT INTO scanner_decisions "
            "(kind, decision_ts, ticker, pick_id, reason, gap_start, gap_end, rejection_reason) "
            "VALUES ('stale_feed', '2026-05-02T14:30:00+00:00', NULL, NULL, NULL, "
            "'2026-05-02T14:29:00+00:00', NULL, NULL)",
        )


def test_picked_without_ticker_rejected(migrated_engine: Engine) -> None:
    """``kind='picked'`` requires ``ticker IS NOT NULL`` (CHECK 5).

    First seed a real Pick row to satisfy the FK so this test isolates the
    ticker CHECK rather than tripping the FK first.
    """
    with migrated_engine.begin() as conn:
        conn.exec_driver_sql(
            "INSERT INTO picks "
            "(ticker, ts, rel_volume, pct_change, price, "
            "float_shares, news_present, headline_count, rank) "
            "VALUES ('ABCD', '2026-05-02T14:30:00+00:00', '12.5', '18.75', '3.42', "
            "8500000, 1, 4, 1)",
        )
        pick_id = conn.exec_driver_sql("SELECT id FROM picks").scalar_one()

    with migrated_engine.begin() as conn, pytest.raises(IntegrityError):
        conn.exec_driver_sql(
            "INSERT INTO scanner_decisions "
            "(kind, decision_ts, ticker, pick_id, reason, gap_start, gap_end, rejection_reason) "
            "VALUES ('picked', '2026-05-02T14:30:00+00:00', NULL, :pick_id, "
            "NULL, NULL, NULL, NULL)",
            {"pick_id": pick_id},
        )


def test_rejected_without_ticker_rejected(migrated_engine: Engine) -> None:
    """``kind='rejected'`` requires ``ticker IS NOT NULL`` (CHECK 5)."""
    with migrated_engine.begin() as conn, pytest.raises(IntegrityError):
        conn.exec_driver_sql(
            "INSERT INTO scanner_decisions "
            "(kind, decision_ts, ticker, pick_id, reason, gap_start, gap_end, rejection_reason) "
            "VALUES ('rejected', '2026-05-02T14:30:00+00:00', NULL, NULL, NULL, "
            "NULL, NULL, 'rel_volume')",
        )


# ----------------------- happy-path inserts must continue to succeed


def test_valid_picked_insert_accepted(migrated_engine: Engine) -> None:
    """Sanity: a correctly-shaped ``picked`` row passes every CHECK."""
    with migrated_engine.begin() as conn:
        conn.exec_driver_sql(
            "INSERT INTO picks "
            "(ticker, ts, rel_volume, pct_change, price, "
            "float_shares, news_present, headline_count, rank) "
            "VALUES ('ABCD', '2026-05-02T14:30:00+00:00', '12.5', '18.75', '3.42', "
            "8500000, 1, 4, 1)",
        )
        pick_id = conn.exec_driver_sql("SELECT id FROM picks").scalar_one()
        conn.exec_driver_sql(
            "INSERT INTO scanner_decisions "
            "(kind, decision_ts, ticker, pick_id, reason, gap_start, gap_end, rejection_reason) "
            "VALUES ('picked', '2026-05-02T14:30:00+00:00', 'ABCD', :pick_id, "
            "NULL, NULL, NULL, NULL)",
            {"pick_id": pick_id},
        )


def test_valid_stale_feed_insert_accepted(migrated_engine: Engine) -> None:
    with migrated_engine.begin() as conn:
        conn.exec_driver_sql(
            "INSERT INTO scanner_decisions "
            "(kind, decision_ts, ticker, pick_id, reason, gap_start, gap_end, rejection_reason) "
            "VALUES ('stale_feed', '2026-05-02T14:30:00+00:00', NULL, NULL, "
            "'feed stale by 7.0s', NULL, NULL, NULL)",
        )


def test_valid_feed_gap_insert_accepted(migrated_engine: Engine) -> None:
    with migrated_engine.begin() as conn:
        conn.exec_driver_sql(
            "INSERT INTO scanner_decisions "
            "(kind, decision_ts, ticker, pick_id, reason, gap_start, gap_end, rejection_reason) "
            "VALUES ('feed_gap', '2026-05-02T14:30:00+00:00', NULL, NULL, "
            "'reset', '2026-05-02T14:29:00+00:00', '2026-05-02T14:30:00+00:00', NULL)",
        )


def test_valid_rejected_insert_accepted(migrated_engine: Engine) -> None:
    with migrated_engine.begin() as conn:
        conn.exec_driver_sql(
            "INSERT INTO scanner_decisions "
            "(kind, decision_ts, ticker, pick_id, reason, gap_start, gap_end, rejection_reason) "
            "VALUES ('rejected', '2026-05-02T14:30:00+00:00', 'ABCD', NULL, "
            "NULL, NULL, NULL, 'rel_volume')",
        )


# ---------------------------- migration round-trip with seeded rows


def test_upgrade_then_downgrade_with_seeded_rows() -> None:
    """0001 -> seed one row of each kind -> upgrade head -> downgrade base.

    Ensures the 0002 CHECK migration applies forward AND reverse cleanly
    against a populated database. SQLite cannot ``ALTER TABLE ADD CHECK``
    directly, so the migration must use Alembic batch-mode (which rewrites
    as CREATE NEW + COPY + DROP OLD + RENAME). Round-trip with seeded data
    proves no rows orphaned in either direction.
    """
    engine = create_journal_engine("sqlite://")
    try:
        # Stage 1: upgrade to 0001 (pre-CHECK) and seed one row of each kind.
        with engine.begin() as conn:
            command.upgrade(_alembic_config(conn), "0001_initial")

        with Session(engine) as session:
            pick = Pick(
                ticker="ABCD",
                ts=datetime(2026, 5, 2, 14, 30, tzinfo=UTC),
                rel_volume=Decimal("12.5"),
                pct_change=Decimal("18.75"),
                price=Decimal("3.42"),
                float_shares=8_500_000,
                news_present=True,
                headline_count=4,
                rank=1,
            )
            session.add(pick)
            session.flush()
            session.add_all([
                ScannerDecisionRow(
                    kind=DecisionKind.PICKED,
                    decision_ts=datetime(2026, 5, 2, 14, 30, tzinfo=UTC),
                    ticker="ABCD",
                    pick_id=pick.id,
                ),
                ScannerDecisionRow(
                    kind=DecisionKind.STALE_FEED,
                    decision_ts=datetime(2026, 5, 2, 14, 31, tzinfo=UTC),
                    reason="feed stale by 7.0s",
                ),
                ScannerDecisionRow(
                    kind=DecisionKind.FEED_GAP,
                    decision_ts=datetime(2026, 5, 2, 14, 32, tzinfo=UTC),
                    reason="reset",
                    gap_start=datetime(2026, 5, 2, 14, 31, tzinfo=UTC),
                    gap_end=datetime(2026, 5, 2, 14, 32, tzinfo=UTC),
                ),
                ScannerDecisionRow(
                    kind=DecisionKind.REJECTED,
                    decision_ts=datetime(2026, 5, 2, 14, 33, tzinfo=UTC),
                    ticker="WXYZ",
                    rejection_reason=RejectionReason.REL_VOLUME,
                ),
            ])
            session.commit()

        # Stage 2: upgrade head (applies 0002 CHECK migration) on populated DB.
        with engine.begin() as conn:
            command.upgrade(_alembic_config(conn), "head")

        # All four seeded rows survived.
        with Session(engine) as session:
            count = session.query(ScannerDecisionRow).count()
            pick_count = session.query(Pick).count()
        assert count == 4
        assert pick_count == 1

        # Stage 3: downgrade back to 0001 with the rows still present.
        with engine.begin() as conn:
            command.downgrade(_alembic_config(conn), "0001_initial")

        with Session(engine) as session:
            count = session.query(ScannerDecisionRow).count()
            pick_count = session.query(Pick).count()
        assert count == 4, "downgrade lost decision rows"
        assert pick_count == 1, "downgrade lost pick rows"

    finally:
        engine.dispose()
