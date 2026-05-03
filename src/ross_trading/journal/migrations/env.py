"""Alembic environment for the scanner journal.

Configured with ``render_as_batch=True`` because SQLite cannot perform
arbitrary ``ALTER TABLE`` statements; Alembic's batch-mode rewrites the
operation as ``CREATE TABLE new + COPY + DROP old + RENAME``. We keep
batch mode on for Postgres too -- it is a no-op there but keeps every
revision portable.

Tests pass an existing ``Connection`` via
``Config.attributes['connection']`` so migrations run against the
session-scoped in-memory engine. The file-config path is only used by
the CLI (``alembic upgrade head``).
"""

from __future__ import annotations

from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from ross_trading.journal.models import Base

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Honor ``alembic -x sqlalchemy.url=...`` so CI can target a temp DB without
# rewriting alembic.ini. ``x_arguments`` is the documented escape hatch.
_x_args = context.get_x_argument(as_dictionary=True)
if "sqlalchemy.url" in _x_args:
    config.set_main_option("sqlalchemy.url", _x_args["sqlalchemy.url"])

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """Render SQL to stdout without a live DBAPI connection."""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        render_as_batch=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations against a live engine or an injected connection."""
    injected = config.attributes.get("connection")
    if injected is not None:
        context.configure(
            connection=injected,
            target_metadata=target_metadata,
            render_as_batch=True,
        )
        with context.begin_transaction():
            context.run_migrations()
        return

    section = config.get_section(config.config_ini_section, {})
    connectable = engine_from_config(
        section,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            render_as_batch=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
