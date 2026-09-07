from __future__ import annotations

import os
import re
from pathlib import Path
from urllib.parse import parse_qsl, unquote, urlsplit

允許驅動 = {"postgresql", "postgresql+psycopg"}
安全TCP查詢鍵 = frozenset({"sslmode", "connect_timeout", "application_name"})
禁止查詢覆寫鍵 = frozenset({
    "host", "hostaddr", "port", "dbname", "user", "password", "passfile",
    "service", "servicefile",
})
_環境覆寫鍵 = re.compile(r"PG[A-Z0-9_]*\Z")


def _解析查詢(query: str) -> list[tuple[str, str]]:
    try:
        pairs = parse_qsl(query, keep_blank_values=True, strict_parsing=True)
    except (TypeError, ValueError, UnicodeError):
        raise ValueError("POSTGRES_MIGRATION_DSN query 格式無效") from None
    lowered = [key.lower() for key, _ in pairs]
    if len(lowered) != len(set(lowered)):
        raise ValueError("POSTGRES_MIGRATION_DSN query key 不可重複")
    if any(key in 禁止查詢覆寫鍵 for key in lowered):
        raise ValueError("POSTGRES_MIGRATION_DSN query 不可覆寫連線 authority")
    return pairs


def 驗證遷移DSN(值: object) -> str:
    if any(_環境覆寫鍵.fullmatch(key) for key in os.environ):
        raise ValueError("遷移環境含禁止的 PostgreSQL process override")
    if type(值) is not str or not 值.strip():
        raise ValueError("POSTGRES_MIGRATION_DSN 必須明確設定")
    dsn = 值.strip()
    try:
        parsed = urlsplit(dsn)
    except Exception:
        raise ValueError("POSTGRES_MIGRATION_DSN 格式無效") from None
    if parsed.scheme not in 允許驅動 or not parsed.path or parsed.path == "/":
        raise ValueError("POSTGRES_MIGRATION_DSN 必須指定 PostgreSQL 驅動與資料庫名稱")
    if "#" in dsn or parsed.username is None:
        raise ValueError("POSTGRES_MIGRATION_DSN 不允許 fragment，且必須指定遷移身分")
    try:
        port = parsed.port
    except ValueError:
        raise ValueError("POSTGRES_MIGRATION_DSN authority 格式無效") from None

    try:
        raw_pairs = parse_qsl(parsed.query, keep_blank_values=True, strict_parsing=True)
    except (TypeError, ValueError, UnicodeError):
        raise ValueError("POSTGRES_MIGRATION_DSN query 格式無效") from None
    socket_pairs = [(key, value) for key, value in raw_pairs if key.lower() == "host"]
    if socket_pairs:
        if parsed.hostname is not None or port is not None:
            raise ValueError("Cloud SQL socket DSN authority 不可含 hostname 或 port")
        if len(raw_pairs) != 1 or len(socket_pairs) != 1:
            raise ValueError("Cloud SQL socket DSN query 必須精確只含 host")
        key, socket_path = socket_pairs[0]
        if (
            key != "host"
            or parsed.query != f"host={socket_path}"
            or not socket_path.startswith("/cloudsql/")
            or socket_path == "/cloudsql/"
        ):
            raise ValueError("Cloud SQL socket DSN host 格式無效")
        connection_name = socket_path.removeprefix("/cloudsql/")
        connection_parts = connection_name.split(":")
        if len(connection_parts) != 3 or not all(connection_parts) or "/" in connection_name:
            raise ValueError("Cloud SQL socket DSN host 格式無效")
    else:
        pairs = _解析查詢(parsed.query) if parsed.query else []
        if parsed.hostname is None:
            raise ValueError("TCP migration DSN 必須指定 authority hostname")
        if any(key.lower() not in 安全TCP查詢鍵 for key, _ in pairs):
            raise ValueError("TCP migration DSN query 含未允許的 key")

    if not unquote(parsed.path[1:]) or "/" in unquote(parsed.path[1:]):
        raise ValueError("POSTGRES_MIGRATION_DSN 資料庫名稱無效")
    return dsn


def alembic設定檔() -> Path:
    return Path(__file__).resolve().parents[1] / "alembic.ini"


def 建立Alembic設定(dsn: str | None = None):
    from alembic.config import Config
    value = 驗證遷移DSN(dsn if dsn is not None else os.environ.get("POSTGRES_MIGRATION_DSN"))
    cfg = Config(str(alembic設定檔()))
    # env.py 是唯一將已驗證 DSN 放進 SQLAlchemy 設定的位置；attributes 不經
    # ConfigParser interpolation，也避免 programmatic 呼叫被 process env 覆蓋。
    cfg.attributes["migration_dsn"] = value
    return cfg


def 升級到最新(dsn: str | None = None) -> None:
    from alembic import command
    command.upgrade(建立Alembic設定(dsn), "head")
