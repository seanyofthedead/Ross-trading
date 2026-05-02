"""Atom A4 (#43) -- journal engine WAL + IMMEDIATE configuration.

Acceptance criterion: ``journal_mode=WAL`` and ``isolation_level=IMMEDIATE``
verified on engine create. WAL is observable on a file-based engine via
``PRAGMA journal_mode``; IMMEDIATE is observable via the begin-event
listener that issues ``BEGIN IMMEDIATE`` (SQLAlchemy 2.x overrides the
pysqlite driver's ``isolation_level`` at runtime, so the connect-arg
alone is not enough -- the listener does the substantive work).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest
from sqlalchemy import event
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ross_trading.journal.engine import (
    JOURNAL_CONNECT_ARGS,
    create_journal_engine,
)
from ross_trading.journal.models import Base, WatchlistEntry


def test_connect_args_pin_immediate() -> None:
    assert JOURNAL_CONNECT_ARGS == {"isolation_level": "IMMEDIATE"}


def test_journal_mode_is_wal_on_file_engine(tmp_path: Any) -> None:
    """``PRAGMA journal_mode=WAL`` only takes effect on file-backed DBs;
    in-memory remains 'memory'.
    """
    db_path = tmp_path / "journal.db"
    engine = create_journal_engine(f"sqlite:///{db_path}")
    try:
        with engine.connect() as conn:
            mode = conn.exec_driver_sql("PRAGMA journal_mode").scalar()
        assert isinstance(mode, str)
        assert mode.lower() == "wal"
    finally:
        engine.dispose()


def test_begin_emits_immediate() -> None:
    """The begin-event listener issues ``BEGIN IMMEDIATE`` -- captured via
    ``before_cursor_execute``.
    """
    engine = create_journal_engine("sqlite://")
    statements: list[str] = []

    @event.listens_for(engine, "before_cursor_execute")
    def _capture(  # type: ignore[no-untyped-def]
        _conn,
        _cursor,
        statement,
        _params,
        _context,
        _executemany,
    ) -> None:
        statements.append(statement.strip().upper())

    try:
        with engine.begin() as conn:
            conn.exec_driver_sql("SELECT 1")
        assert "BEGIN IMMEDIATE" in statements
    finally:
        engine.dispose()


def test_foreign_keys_pragma_enabled() -> None:
    """``PRAGMA foreign_keys = ON`` is set on every new connection."""
    engine = create_journal_engine("sqlite://")
    try:
        with engine.connect() as conn:
            value = conn.exec_driver_sql("PRAGMA foreign_keys").scalar()
        assert value == 1
    finally:
        engine.dispose()


def test_foreign_keys_enforced_rejects_dangling_pick_id() -> None:
    """The pragma is actually in effect: a dangling FK insert raises.

    Without ``PRAGMA foreign_keys = ON`` SQLite would accept a
    ``WatchlistEntry.pick_id`` referencing a non-existent ``picks.id`` row
    silently. With the pragma on, the flush raises ``IntegrityError``.
    """
    engine = create_journal_engine("sqlite://")
    Base.metadata.create_all(engine)
    try:
        with Session(engine) as session:
            entry = WatchlistEntry(
                ticker="ABCD",
                pick_id=999_999,  # no such Pick
                added_at=datetime(2026, 5, 2, 14, 31, tzinfo=UTC),
            )
            session.add(entry)
            with pytest.raises(IntegrityError):
                session.flush()
    finally:
        engine.dispose()
