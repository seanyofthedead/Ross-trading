"""Engine + session factory for the scanner journal.

Configures the SQLite DBAPI for ``BEGIN IMMEDIATE`` semantics and enables
WAL journaling on every new connection. SQLAlchemy 2.x manages
transactions itself and overrides the pysqlite driver's
``isolation_level`` at runtime, so the connect-arg alone is not enough
to actually emit ``BEGIN IMMEDIATE`` -- a ``begin`` event listener does
the substantive work; the connect-arg keeps the configuration
discoverable to tooling that introspects DBAPI args.

In-memory SQLite (``sqlite://`` or ``sqlite:///:memory:``) is detected
and switched to ``StaticPool`` so a single connection is shared across
the engine's lifetime. Without this, separate connections each see an
independent in-memory database, which breaks tests that create the
schema once and then query through the ORM.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Final

from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

if TYPE_CHECKING:
    from sqlalchemy.engine import Engine

JOURNAL_CONNECT_ARGS: Final[dict[str, str]] = {"isolation_level": "IMMEDIATE"}

_IN_MEMORY_URLS: Final[frozenset[str]] = frozenset({"sqlite://", "sqlite:///:memory:"})


def create_journal_engine(
    url: str = "sqlite:///:memory:",
    *,
    echo: bool = False,
) -> Engine:
    """Create a journal :class:`Engine` with WAL + IMMEDIATE configured."""
    is_memory = url in _IN_MEMORY_URLS
    connect_args: dict[str, Any] = dict(JOURNAL_CONNECT_ARGS)
    kwargs: dict[str, Any] = {"echo": echo}
    if is_memory:
        kwargs["poolclass"] = StaticPool
        connect_args["check_same_thread"] = False
    kwargs["connect_args"] = connect_args

    engine = create_engine(url, **kwargs)

    @event.listens_for(engine, "connect")
    def _enable_wal(
        dbapi_connection: Any,
        _connection_record: Any,
    ) -> None:
        cursor = dbapi_connection.cursor()
        try:
            cursor.execute("PRAGMA journal_mode=WAL")
        finally:
            cursor.close()

    @event.listens_for(engine, "begin")
    def _begin_immediate(conn: Any) -> None:
        conn.exec_driver_sql("BEGIN IMMEDIATE")

    return engine


def create_session_factory(engine: Engine) -> sessionmaker[Session]:
    """Bind a default :class:`sessionmaker` to ``engine``."""
    return sessionmaker(bind=engine, expire_on_commit=False)
