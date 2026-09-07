from __future__ import annotations

import importlib.util
from pathlib import Path
import re
import sys
from types import ModuleType, SimpleNamespace

BACKEND = Path(__file__).resolve().parents[2]
MIGRATION = BACKEND / "繁中代理/postgres_migrations/versions/0001_full_product_schema.py"


def _load():
    original = sys.modules.get("alembic")
    fake = ModuleType("alembic")
    setattr(fake, "op", SimpleNamespace(execute=lambda _statement: None))
    sys.modules["alembic"] = fake
    try:
        spec = importlib.util.spec_from_file_location("full_pg_schema", MIGRATION)
        assert spec and spec.loader
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    finally:
        if original is None:
            sys.modules.pop("alembic", None)
        else:
            sys.modules["alembic"] = original


def test_schema_covers_every_durable_domain() -> None:
    from 繁中代理.儲存契約 import 耐久領域
    migration = _load()
    assert set(migration.DOMAIN_TABLES) == set(耐久領域)
    assert len(migration.DOMAIN_TABLES) == 33
    created = {m.group(1) for sql in migration.DDL if (m := re.match(r"CREATE TABLE ([a-z_]+)", sql))}
    assert set(migration.DOMAIN_TABLES.values()) <= created


def test_schema_uses_postgresql_durable_types_and_constraints() -> None:
    sql = "\n".join(_load().DDL + _load().INDEX_DDL).lower()
    assert "jsonb" in sql
    assert "timestamptz" in sql
    assert "references users" in sql
    assert "references published_endpoints" in sql
    assert "check(" in sql or "check (" in sql
    assert "using gin" in sql
    assert "retention" in sql
    assert "unique index uq_messages_session_active_index" in sql
    assert "where active=true" in sql
    assert "state in ('active','stale','archived')" in sql
    endpoint = next(item.lower() for item in _load().DDL if item.startswith("CREATE TABLE published_endpoints"))
    assert "rate_limit_requests integer not null default 60 check(rate_limit_requests>0)" in endpoint
    assert "rate_limit_window_seconds integer not null default 60 check(rate_limit_window_seconds>0)" in endpoint


def test_single_root_revision_and_runtime_has_no_ddl() -> None:
    migration = _load()
    assert migration.revision == "0001_full_product_schema"
    assert migration.down_revision is None
    for runtime in (BACKEND / "繁中代理/PostgreSQL遷移.py", BACKEND / "繁中代理/PostgreSQL健康.py"):
        text = runtime.read_text(encoding="utf-8").upper()
        assert "CREATE TABLE" not in text
        assert "ALTER TABLE" not in text
