"""Atom A4 (#43) -- alembic upgrade head + downgrade base round trip.

Runs the migration tree against an in-memory SQLite engine via a shared
connection (passed through ``Config.attributes['connection']``) so the
session-scoped fixture matches the ``HistoricalCache`` testing pattern.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import inspect
from sqlalchemy.orm import Session

from ross_trading.journal.engine import create_journal_engine
from ross_trading.journal.models import (
    DecisionKind,
    RejectionReason,
    ScannerDecision,
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
_EXPECTED_TABLES = frozenset({"picks", "watchlist_entries", "scanner_decisions"})


def _alembic_config(connection: Connection) -> Config:
    cfg = Config()
    cfg.set_main_option("script_location", str(_MIGRATIONS_DIR))
    cfg.attributes["connection"] = connection
    return cfg


@pytest.fixture(scope="session")
def migrated_engine() -> Iterator[Engine]:
    """Engine with the journal schema applied via ``alembic upgrade head``.

    Session-scoped: the migration runs once and downstream tests reuse the
    schema.
    """
    engine = create_journal_engine("sqlite://")
    with engine.begin() as conn:
        command.upgrade(_alembic_config(conn), "head")
    try:
        yield engine
    finally:
        engine.dispose()


def test_upgrade_head_creates_expected_tables(migrated_engine: Engine) -> None:
    with migrated_engine.connect() as conn:
        names = set(inspect(conn).get_table_names())
    assert names >= _EXPECTED_TABLES
    assert "alembic_version" in names


def test_upgrade_head_then_downgrade_base() -> None:
    engine = create_journal_engine("sqlite://")
    try:
        with engine.begin() as conn:
            cfg = _alembic_config(conn)
            command.upgrade(cfg, "head")
            after_upgrade = set(inspect(conn).get_table_names())
            assert after_upgrade >= _EXPECTED_TABLES

            command.downgrade(cfg, "base")
            after_downgrade = set(inspect(conn).get_table_names())
            assert _EXPECTED_TABLES.isdisjoint(after_downgrade)
    finally:
        engine.dispose()


def test_orm_inserts_persist_lowercase_enum_values_against_migration(
    migrated_engine: Engine,
) -> None:
    """Regression: ORM must write enum ``.value`` strings, not ``.name``.

    The migration declares ``decision_kind`` and ``rejection_reason`` as
    lowercase literals (``picked``, ``rel_volume`` ...). Without
    ``values_callable`` on the model's ``Enum`` columns SA persists the
    member name (``PICKED``), which the migration's CHECK constraint
    rejects at runtime.

    Inserts a ``PICKED`` row and a ``REJECTED`` + ``REL_VOLUME`` row via
    an ORM session bound to the alembic-migrated engine, then reads the
    raw column values back through ``exec_driver_sql`` to confirm the
    lowercase ``.value`` strings -- not the uppercase ``.name``s -- hit
    the database.
    """
    with Session(migrated_engine) as session:
        picked = ScannerDecision(
            kind=DecisionKind.PICKED,
            decision_ts=datetime(2026, 5, 2, 14, 30, tzinfo=UTC),
            ticker="ABCD",
        )
        rejected = ScannerDecision(
            kind=DecisionKind.REJECTED,
            decision_ts=datetime(2026, 5, 2, 14, 31, tzinfo=UTC),
            ticker="WXYZ",
            rejection_reason=RejectionReason.REL_VOLUME,
        )
        session.add_all([picked, rejected])
        session.commit()
        picked_id = picked.id
        rejected_id = rejected.id

    with migrated_engine.connect() as conn:
        picked_kind = conn.exec_driver_sql(
            "SELECT kind FROM scanner_decisions WHERE id = ?",
            (picked_id,),
        ).scalar_one()
        rejected_kind = conn.exec_driver_sql(
            "SELECT kind FROM scanner_decisions WHERE id = ?",
            (rejected_id,),
        ).scalar_one()
        rejected_reason = conn.exec_driver_sql(
            "SELECT rejection_reason FROM scanner_decisions WHERE id = ?",
            (rejected_id,),
        ).scalar_one()

    assert picked_kind == "picked"
    assert rejected_kind == "rejected"
    assert rejected_reason == "rel_volume"
