"""Durable record of agent decisions, backed by SQLAlchemy 2.x + Alembic.

Phase 2 -- Atom A4 (#43). Storage layer for the scanner journal: ``Pick``,
``WatchlistEntry``, ``ScannerDecision``. The writer (A5 / #44) lands on top
of :class:`ross_trading.scanner.decisions.DecisionSink`; A6 (#45) and A7
(#46) read back through this same model surface.

Why SQLAlchemy here when ``data/cache.py`` uses raw ``sqlite3``? The
inconsistency is intentional and documented at both sites:

* ``data/cache.py`` is a hot-path, append-only analytic cache (daily
  volumes, EMAs). The schema is small and stable; raw ``sqlite3`` keeps it
  dependency-light and sub-millisecond.
* This module is the durable record of agent decisions across versions.
  The schema *will* evolve (rank weights, new filters, post-hoc
  enrichment), and the data outlives the agent process. SQLAlchemy 2.x
  typed ORM + Alembic gives us migration ergonomics today and a Postgres
  swap option later if we outgrow SQLite.

Do not "harmonize" the two without a concrete need -- the cost
(complexity, runtime overhead, lost type safety on one side or the other)
outweighs the win (one fewer dependency surface).
"""
