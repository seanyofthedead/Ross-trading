"""Atom A4 (#43) -- alembic upgrade head + downgrade base round trip.

Runs the migration tree against an in-memory SQLite engine via a shared
connection (passed through ``Config.attributes['connection']``) so the
session-scoped fixture matches the ``HistoricalCache`` testing pattern.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import inspect

from ross_trading.journal.engine import create_journal_engine

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
