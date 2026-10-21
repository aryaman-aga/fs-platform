"""Database plumbing: engines, sessions, portable column types and the RLS context.

PostgreSQL is the system of record (Mode A). Every transaction opened on behalf
of a user first sets ``app.tenant_id``, ``app.user_id`` and ``app.scope`` with
``set_config(..., is_local => true)``; the row-level-security policies created by
the initial migration read those settings (defence in depth behind the API's own
RBAC/ABAC checks). SQLite is supported for the offline edition (Mode C) and fast
tests; it has no RLS, so the API checks are the only control there.
"""

from __future__ import annotations

import threading
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, ClassVar

from sqlalchemy import JSON, DateTime, create_engine, event, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker
from sqlalchemy.pool import NullPool
from sqlalchemy.types import TypeDecorator

JSONType = JSON().with_variant(JSONB(), "postgresql")


def utcnow() -> datetime:
    return datetime.now(UTC)


def new_id() -> str:
    return str(uuid.uuid4())


class UTCDateTime(TypeDecorator[datetime]):
    """Timezone-aware UTC datetimes on every backend (SQLite stores naive UTC)."""

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect: Any) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            value = value.replace(tzinfo=UTC)
        value = value.astimezone(UTC)
        if dialect.name == "sqlite":
            return value.replace(tzinfo=None)
        return value

    def process_result_value(self, value: datetime | None, dialect: Any) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)


class Base(DeclarativeBase):
    type_annotation_map: ClassVar[dict[Any, Any]] = {dict[str, Any]: JSONType, list[Any]: JSONType}


@dataclass(frozen=True)
class RLSContext:
    """Values the PostgreSQL RLS policies read. ``scope``:

    * ``user``   – a normal request: tenant rows + clients the user is assigned to;
    * ``firm``   – a Firm Admin request: master data and audit of the whole tenant;
    * ``auth``   – pre-authentication lookups (login, session, invite tables only);
    * ``system`` – workers and the admin CLI.
    """

    tenant_id: str | None = None
    user_id: str | None = None
    scope: str = "user"


SYSTEM = RLSContext(scope="system")
AUTH = RLSContext(scope="auth")


def _apply_rls(connection: Connection, ctx: RLSContext) -> None:
    connection.execute(
        text(
            "SELECT set_config('app.tenant_id', :t, true), set_config('app.user_id', :u, true), "
            "set_config('app.scope', :s, true)"
        ),
        {"t": ctx.tenant_id or "", "u": ctx.user_id or "", "s": ctx.scope},
    )


@event.listens_for(Session, "after_begin")
def _session_after_begin(session: Session, transaction: Any, connection: Connection) -> None:
    ctx = session.info.get("rls")
    if isinstance(ctx, RLSContext) and connection.dialect.name == "postgresql":
        _apply_rls(connection, ctx)


def set_rls(session: Session, ctx: RLSContext) -> None:
    """Change the RLS context of a session, including the transaction in progress."""
    session.info["rls"] = ctx
    if session.in_transaction() and session.get_bind().dialect.name == "postgresql":
        _apply_rls(session.connection(), ctx)


def _make_engine(url: str) -> Engine:
    if url.startswith("sqlite"):
        eng = create_engine(url, connect_args={"check_same_thread": False, "timeout": 30}, poolclass=NullPool)

        @event.listens_for(eng, "connect")
        def _sqlite_pragmas(dbapi_conn: Any, _rec: Any) -> None:
            # Take over transaction control: every transaction is BEGIN IMMEDIATE so
            # writers are serialised up front (no stale-snapshot upgrade failures,
            # and the audit chain cannot fork). Fine for Mode C / test volumes.
            dbapi_conn.isolation_level = None
            cur = dbapi_conn.cursor()
            cur.execute("PRAGMA foreign_keys=ON")
            cur.execute("PRAGMA journal_mode=WAL")
            cur.execute("PRAGMA busy_timeout=30000")
            cur.close()

        @event.listens_for(eng, "begin")
        def _sqlite_begin(conn: Connection) -> None:
            conn.exec_driver_sql("BEGIN IMMEDIATE")

        return eng
    return create_engine(url, pool_pre_ping=True, pool_size=10, max_overflow=20)


class Database:
    """Owner engine (migrations, CLI) and runtime engine (API/worker, RLS-bound role)."""

    def __init__(self, owner_url: str, app_url: str | None = None) -> None:
        self.owner_url = owner_url
        self.app_url = app_url or owner_url
        self.owner_engine = _make_engine(owner_url)
        self.engine = self.owner_engine if self.app_url == owner_url else _make_engine(self.app_url)
        self._factory = sessionmaker(bind=self.engine, expire_on_commit=False, autoflush=True)
        self._owner_factory = sessionmaker(bind=self.owner_engine, expire_on_commit=False, autoflush=True)
        self.dialect = self.engine.dialect.name
        # SQLite serialises writers per database; the audit chain also serialises
        # appends in-process so concurrent requests never fork the chain.
        self.audit_lock = threading.Lock()

    @property
    def is_postgres(self) -> bool:
        return self.dialect == "postgresql"

    def session(self, ctx: RLSContext | None = None) -> Session:
        s = self._factory()
        if ctx is not None:
            s.info["rls"] = ctx
        return s

    def owner_session(self, ctx: RLSContext = SYSTEM) -> Session:
        s = self._owner_factory()
        s.info["rls"] = ctx
        return s

    @contextmanager
    def scope(self, ctx: RLSContext | None = None) -> Iterator[Session]:
        s = self.session(ctx)
        try:
            yield s
            s.commit()
        except BaseException:
            s.rollback()
            raise
        finally:
            s.close()

    def create_all(self) -> None:
        """Create the schema directly (SQLite/tests). Postgres uses Alembic migrations."""
        from fsweb import models  # noqa: F401  (register tables)

        Base.metadata.create_all(self.owner_engine)
        if self.owner_engine.dialect.name == "sqlite":
            with self.owner_engine.begin() as conn:
                for stmt in SQLITE_AUDIT_TRIGGERS:
                    conn.exec_driver_sql(stmt)

    def dispose(self) -> None:
        self.engine.dispose()
        if self.owner_engine is not self.engine:
            self.owner_engine.dispose()


SQLITE_AUDIT_TRIGGERS = [
    "CREATE TRIGGER IF NOT EXISTS audit_event_no_update BEFORE UPDATE ON audit_event "
    "BEGIN SELECT RAISE(ABORT, 'audit_event is append-only'); END;",
    "CREATE TRIGGER IF NOT EXISTS audit_event_no_delete BEFORE DELETE ON audit_event "
    "BEGIN SELECT RAISE(ABORT, 'audit_event is append-only'); END;",
]
