from __future__ import annotations

from pathlib import Path
from contextlib import nullcontext
import logging
import runpy
import sys
from types import ModuleType, SimpleNamespace
from unittest.mock import MagicMock, call

import pytest

BACKEND = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(BACKEND))

from 繁中代理.PostgreSQL健康 import PostgreSQL未就緒, 檢查PostgreSQL就緒
from 繁中代理.PostgreSQL遷移 import 建立Alembic設定, 驗證遷移DSN


def _connection(one, revisions):
    connection = MagicMock()
    connection.execute.side_effect = [
        MagicMock(fetchone=MagicMock(return_value=(one,))),
        MagicMock(fetchall=MagicMock(return_value=[(value,) for value in revisions])),
    ]
    return connection


def test_migration_dsn_fails_closed_and_redacts() -> None:
    with pytest.raises(ValueError) as exc:
        驗證遷移DSN("not-a-dsn:secret")
    assert "secret" not in str(exc.value)
    with pytest.raises(ValueError):
        驗證遷移DSN(None)


def test_migration_dsn_accepts_explicit_postgres_identity() -> None:
    value = "postgresql+psycopg://migrator:p%40ss@db.internal/product?sslmode=require"
    assert 驗證遷移DSN(value) == value


@pytest.mark.parametrize("key", ("PGOPTIONS", "PGSERVICE", "PGPASSWORD"))
def test_migration_rejects_every_ambient_libpq_override_without_echo(monkeypatch, key) -> None:
    secret = "AMBIENT_SECRET"
    monkeypatch.setenv(key, secret)
    with pytest.raises(ValueError) as caught:
        驗證遷移DSN("postgresql+psycopg://migrator:dsn-secret@db/product")
    assert secret not in str(caught.value) and "dsn-secret" not in str(caught.value)


@pytest.mark.parametrize("key", (
    "host", "hostaddr", "port", "dbname", "user", "password", "passfile",
    "service", "servicefile",
))
def test_migration_dsn_rejects_query_authority_overrides_without_secret_echo(key: str) -> None:
    secret = "query-override-secret"
    dsn = f"postgresql+psycopg://migrator:p%40ss@db.internal/product?{key}={secret}"
    with pytest.raises(ValueError) as exc:
        驗證遷移DSN(dsn)
    assert secret not in str(exc.value)


def test_migration_dsn_accepts_only_exact_cloud_sql_socket_query() -> None:
    exact = (
        "postgresql+psycopg://migrator:p%40ss@/product"
        "?host=/cloudsql/project:asia-east1:instance"
    )
    assert 驗證遷移DSN(exact) == exact
    rejected = (
        exact + "&sslmode=require",
        exact.replace("@/product", "@db.internal/product"),
        exact.replace("?host=", "?HOST="),
        exact.replace("host=/cloudsql/", "host=%2Fcloudsql%2F"),
        exact.replace("/cloudsql/project:asia-east1:instance", "/tmp/postgres"),
    )
    for dsn in rejected:
        with pytest.raises(ValueError):
            驗證遷移DSN(dsn)


def test_programmatic_dsn_without_env_reaches_alembic_env_configuration(monkeypatch) -> None:
    dsn = "postgresql+psycopg://migrator:p%40ss@db.internal/product?sslmode=require"
    monkeypatch.delenv("POSTGRES_MIGRATION_DSN", raising=False)
    cfg = 建立Alembic設定(dsn)
    assert cfg.attributes["migration_dsn"] == dsn
    assert cfg.get_main_option("sqlalchemy.url") == ""

    configure = MagicMock()
    context = SimpleNamespace(
        config=cfg,
        configure=configure,
        begin_transaction=nullcontext,
        run_migrations=MagicMock(),
        is_offline_mode=lambda: True,
    )
    fake_alembic = ModuleType("alembic")
    setattr(fake_alembic, "context", context)
    fake_sqlalchemy = ModuleType("sqlalchemy")
    setattr(fake_sqlalchemy, "engine_from_config", MagicMock())
    setattr(fake_sqlalchemy, "pool", SimpleNamespace(NullPool=object()))
    monkeypatch.setitem(sys.modules, "alembic", fake_alembic)
    monkeypatch.setitem(sys.modules, "sqlalchemy", fake_sqlalchemy)

    runpy.run_path(str(BACKEND / "繁中代理/postgres_migrations/env.py"))
    assert configure.call_args.kwargs["url"] == dsn


def test_alembic_env_does_not_mutate_process_logging(monkeypatch) -> None:
    dsn = "postgresql+psycopg://migrator:p%40ss@db.internal/product"
    cfg = 建立Alembic設定(dsn)
    context = SimpleNamespace(config=cfg, configure=MagicMock(), begin_transaction=nullcontext,
                              run_migrations=MagicMock(), is_offline_mode=lambda: True)
    fake_alembic = ModuleType("alembic"); setattr(fake_alembic, "context", context)
    fake_sqlalchemy = ModuleType("sqlalchemy")
    setattr(fake_sqlalchemy, "engine_from_config", MagicMock())
    setattr(fake_sqlalchemy, "pool", SimpleNamespace(NullPool=object()))
    monkeypatch.setitem(sys.modules, "alembic", fake_alembic)
    monkeypatch.setitem(sys.modules, "sqlalchemy", fake_sqlalchemy)
    root = logging.getLogger(); sentinel = logging.NullHandler()
    old_handlers, old_level = list(root.handlers), root.level
    watched = logging.getLogger("r1.sentinel"); old_disabled = watched.disabled
    root.handlers[:] = [sentinel]; root.setLevel(logging.ERROR); watched.disabled = True
    try:
        runpy.run_path(str(BACKEND / "繁中代理/postgres_migrations/env.py"))
        assert root.handlers == [sentinel] and root.level == logging.ERROR and watched.disabled is True
    finally:
        root.handlers[:] = old_handlers; root.setLevel(old_level); watched.disabled = old_disabled


def test_readiness_checks_select_one_and_exact_head() -> None:
    connection = _connection(1, ["0001_full_product_schema"])
    檢查PostgreSQL就緒(connection, "0001_full_product_schema")
    assert connection.execute.call_args_list == [call("SELECT 1"), call("SELECT version_num FROM alembic_version")]


def test_readiness_rejects_revision_drift() -> None:
    connection = _connection(1, ["old"])
    with pytest.raises(PostgreSQL未就緒):
        檢查PostgreSQL就緒(connection, "0001_full_product_schema")


@pytest.mark.parametrize("rows", ([], ["0001_full_product_schema", "other_head"]))
def test_readiness_rejects_zero_or_multiple_ledger_rows(rows) -> None:
    connection = _connection(1, rows)
    with pytest.raises(PostgreSQL未就緒):
        檢查PostgreSQL就緒(connection, "0001_full_product_schema")


def test_readiness_redacts_database_errors() -> None:
    connection = MagicMock()
    connection.execute.side_effect = RuntimeError("postgresql://user:secret@host/db")
    with pytest.raises(PostgreSQL未就緒) as exc:
        檢查PostgreSQL就緒(connection)
    assert "secret" not in str(exc.value)
