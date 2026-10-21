"""Desktop edition: SQLite databases are versioned with Alembic, so an installed copy upgrades in place and keeps
its data; the models and the migration scripts must describe the same schema on SQLite too."""

from __future__ import annotations

from pathlib import Path

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import DatabaseError

from fsweb import models  # noqa: F401  (registers every table)
from fsweb.db import Base, Database
from fsweb.manage import FIRST_REVISION, alembic_config, migrate_sqlite


def _url(tmp_path: Path, name: str = "fsweb.sqlite3") -> str:
    return "sqlite:///" + (tmp_path / name).as_posix()


def _version(url: str) -> str | None:
    with create_engine(url).connect() as c:
        return MigrationContext.configure(c).get_current_revision()


def _head(url: str) -> str | None:
    return ScriptDirectory.from_config(alembic_config(url)).get_current_head()


def _assert_audit_is_append_only(url: str) -> None:
    eng = create_engine(url)
    with eng.begin() as c:
        c.execute(text("INSERT INTO audit_event (id, tenant_id, seq, ts, action, outcome, details, prev_event_hash, event_hash, created_at) "
                       "VALUES ('a1', 't1', 1, '2026-10-21 00:00:00', 'test.event', 'ok', '{}', '0', '1', '2026-10-21 00:00:00')"))
    with pytest.raises(DatabaseError, match="append-only"), eng.begin() as c:
        c.execute(text("UPDATE audit_event SET action = 'tampered' WHERE id = 'a1'"))


def test_new_database_is_created_and_stamped(tmp_path: Path) -> None:
    url = _url(tmp_path)
    assert migrate_sqlite(Database(url), url) == "created"
    assert _version(url) == _head(url)
    _assert_audit_is_append_only(url)
    assert migrate_sqlite(Database(url), url) == "upgraded"  # the next start: nothing to do
    assert _version(url) == _head(url)


def test_installed_database_is_upgraded_and_keeps_its_data(tmp_path: Path) -> None:
    """A copy installed at the first release (revision 0001) upgrades to the newest schema without losing rows."""
    url = _url(tmp_path)
    command.upgrade(alembic_config(url), FIRST_REVISION)
    with create_engine(url).begin() as c:
        c.execute(text("INSERT INTO tenant (id, name, settings_json, created_at) VALUES ('t1', 'Corvus Advisors', '{}', '2026-10-21 00:00:00')"))
    assert migrate_sqlite(Database(url), url) == "upgraded"
    assert _version(url) == _head(url)
    with create_engine(url).connect() as c:
        assert c.execute(text("SELECT name FROM tenant WHERE id = 't1'")).scalar_one() == "Corvus Advisors"
    _assert_audit_is_append_only(url)


def test_unversioned_database_is_taken_as_the_first_revision(tmp_path: Path) -> None:
    """Databases created before revisions were recorded (create_all at the 0001 schema) are upgraded, not recreated."""
    url = _url(tmp_path)
    command.upgrade(alembic_config(url), FIRST_REVISION)
    with create_engine(url).begin() as c:
        c.execute(text("DROP TABLE alembic_version"))
    assert migrate_sqlite(Database(url), url) == "upgraded"
    assert _version(url) == _head(url)


def test_models_and_migrations_agree_on_sqlite(tmp_path: Path) -> None:
    url = _url(tmp_path)
    command.upgrade(alembic_config(url), "head")
    eng = create_engine(url)
    with eng.connect() as c:
        diffs = compare_metadata(MigrationContext.configure(c, opts={"compare_type": False}), Base.metadata)
    assert diffs == [], f"models and migrations differ on SQLite: {diffs[:5]}"
    assert set(inspect(eng).get_table_names()) >= set(Base.metadata.tables)


def test_failed_upgrade_leaves_the_database_untouched(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The whole SQLite upgrade is one transaction: a migration that fails part-way changes nothing."""
    import shutil

    from fsweb.manage import migrations_dir

    scripts = tmp_path / "migrations"
    shutil.copytree(migrations_dir(), scripts, ignore=shutil.ignore_patterns("__pycache__"))
    url = _url(tmp_path)
    head = _head(url)
    (scripts / "versions" / "9999_broken.py").write_text(
        "from alembic import op\nimport sqlalchemy as sa\n\n"
        f"revision = '9999'\ndown_revision = {head!r}\nbranch_labels = None\ndepends_on = None\n\n"
        "def upgrade() -> None:\n"
        "    with op.batch_alter_table('tenant') as b:\n"
        "        b.add_column(sa.Column('half_done', sa.Integer(), nullable=True))\n"
        "    raise RuntimeError('migration failed part-way')\n\n"
        "def downgrade() -> None:\n    pass\n", encoding="utf-8")
    migrate_sqlite(Database(url), url)  # a current database at the real head
    monkeypatch.setenv("FSWEB_ALEMBIC_DIR", str(scripts))
    with pytest.raises(RuntimeError, match="part-way"):
        migrate_sqlite(Database(url), url)
    eng = create_engine(url)
    assert "half_done" not in {c["name"] for c in inspect(eng).get_columns("tenant")}
    assert _version(url) == head


def test_database_from_a_newer_release_is_refused(tmp_path: Path) -> None:
    from fsweb.manage import SchemaError, sqlite_schema_state

    url = _url(tmp_path)
    db = Database(url)
    assert sqlite_schema_state(db, url) == "new"
    migrate_sqlite(db, url)
    assert sqlite_schema_state(db, url) == "current"
    with create_engine(url).begin() as c:
        c.execute(text("UPDATE alembic_version SET version_num = 'f9e8d7c6b5a4'"))
    assert sqlite_schema_state(db, url) == "ahead"
    with pytest.raises(SchemaError, match="newer version"):
        migrate_sqlite(db, url)
