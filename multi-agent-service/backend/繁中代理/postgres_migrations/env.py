from __future__ import annotations

import os

from alembic import context
from sqlalchemy import engine_from_config, pool

from 繁中代理.PostgreSQL遷移 import 驗證遷移DSN

config = context.config

target_metadata = None


def _設定網址() -> str:
    supplied = config.attributes.get("migration_dsn")
    dsn = 驗證遷移DSN(
        supplied if supplied is not None else os.environ.get("POSTGRES_MIGRATION_DSN")
    )
    config.set_main_option("sqlalchemy.url", dsn.replace("%", "%%"))
    return dsn


def run_migrations_offline() -> None:
    url = _設定網址()
    context.configure(url=url, target_metadata=target_metadata, literal_binds=True, dialect_opts={"paramstyle": "named"}, compare_type=True)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    _設定網址()
    connectable = engine_from_config(config.get_section(config.config_ini_section) or {}, prefix="sqlalchemy.", poolclass=pool.NullPool)
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata, compare_type=True)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
