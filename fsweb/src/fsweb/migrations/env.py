"""Alembic environment: migrations run as the owner role from FSWEB_DATABASE_URL."""

from __future__ import annotations

import os

from alembic import context
from sqlalchemy import Engine, create_engine, event, pool

from fsweb import models  # noqa: F401  (registers every table on Base.metadata)
from fsweb.db import Base

config = context.config
target_metadata = Base.metadata


def _url() -> str:
    return config.get_main_option("sqlalchemy.url") or os.environ.get("FSWEB_DATABASE_URL", "")


def run_migrations_offline() -> None:
    context.configure(url=_url(), target_metadata=target_metadata, literal_binds=True, compare_type=True)
    with context.begin_transaction():
        context.run_migrations()


def _engine() -> Engine:
    url = _url()
    if not url.startswith("sqlite"):
        return create_engine(url, poolclass=pool.NullPool)
    # The sqlite3 driver commits DDL as it goes; take over transaction control so an upgrade that fails part-way
    # leaves the desktop database exactly as it was (SQLite DDL is transactional). Foreign keys stay off on this
    # connection, as batch table rebuilds require.
    engine = create_engine(url, poolclass=pool.NullPool, connect_args={"isolation_level": None})

    @event.listens_for(engine, "begin")
    def _begin(conn: object) -> None:
        conn.exec_driver_sql("BEGIN IMMEDIATE")  # type: ignore[attr-defined]

    return engine


def run_migrations_online() -> None:
    engine = _engine()
    with engine.connect() as connection:
        # SQLite cannot ALTER most things in place: batch mode rebuilds the table (desktop edition upgrades)
        context.configure(connection=connection, target_metadata=target_metadata, compare_type=True,
                          render_as_batch=connection.dialect.name == "sqlite")
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
