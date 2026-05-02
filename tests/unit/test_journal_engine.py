"""Atom A4 (#43) -- journal engine WAL + IMMEDIATE configuration.

Acceptance criterion: ``journal_mode=WAL`` and ``isolation_level=IMMEDIATE``
verified on engine create. WAL is observable on a file-based engine via
``PRAGMA journal_mode``; IMMEDIATE is observable via the begin-event
listener that issues ``BEGIN IMMEDIATE`` (SQLAlchemy 2.x overrides the
pysqlite driver's ``isolation_level`` at runtime, so the connect-arg
alone is not enough -- the listener does the substantive work).
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import event

from ross_trading.journal.engine import (
    JOURNAL_CONNECT_ARGS,
    create_journal_engine,
)


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
