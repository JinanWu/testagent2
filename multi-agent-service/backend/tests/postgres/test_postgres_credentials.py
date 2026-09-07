from contextlib import contextmanager
from datetime import datetime, timezone

from psycopg.types.json import Jsonb

from 繁中代理.交易儲存設定 import 交易儲存設定
from 繁中代理.發布介面.憑證.加密 import AESGCM憑證封套
from 繁中代理.發布介面.憑證管理契約 import 憑證建立命令
from 繁中代理.發布介面.憑證.PostgreSQL儲存庫 import PostgreSQL憑證儲存庫


def settings():
    return 交易儲存設定(
        "postgres", "postgresql:///app?host=/cloudsql/proj:region:db", "proj:region:db", 0, 1, 1,
    )


class Cursor:
    def __init__(self, row=None, rows=(), rowcount=1):
        self.row, self.rows, self.rowcount = row, rows, rowcount

    def fetchone(self):
        return self.row

    def fetchall(self):
        return self.rows


class Connection:
    def __init__(self):
        self.calls = []

    def execute(self, sql, params=()):
        self.calls.append((sql, params))
        if "SELECT status FROM published_endpoints" in sql:
            return Cursor({"status": "active"})
        return Cursor(rowcount=1)


class Unit:
    def __init__(self, conn):
        self.conn = conn

    @contextmanager
    def 交易(self):
        yield self.conn


def envelope():
    return AESGCM憑證封套(
        {1: b"k" * 32}, 1, 隨機位元組=lambda n: (b"a" * 32 if n == 32 else b"n" * 12),
    )


def test_create_returns_plaintext_once_but_0001_sql_only_gets_bytea_hash_preview_ciphertext():
    service = PostgreSQL憑證儲存庫(
        settings(), envelope(), clock=lambda: 10.0, id_factory=lambda: "cred-1",
    )
    conn = Connection()
    service._工作單元 = Unit(conn)
    receipt = service.建立憑證(
        端點識別碼="ep-1", 擁有者使用者識別碼="owner",
        請求=憑證建立命令("client", "invoke", 100.0, (), 30),
    )
    insert_sql, insert_params = next(call for call in conn.calls if call[0].startswith("INSERT INTO endpoint_credentials"))
    assert receipt.初始金鑰.startswith("pk_")
    assert receipt.初始金鑰 not in repr(receipt)
    assert receipt.初始金鑰 not in insert_params
    assert "key_hash" in insert_sql and "key_prefix" in insert_sql and "revoked_at" in insert_sql
    assert "ip_allowlist" in insert_sql and "ip_allowlist_json" not in insert_sql
    assert "rate_limit_window_seconds" in insert_sql
    assert len(insert_params[7]) == 64 and insert_params[8] == receipt.金鑰前綴
    assert isinstance(insert_params[5], bytes) and isinstance(insert_params[6], bytes)
    assert isinstance(insert_params[10], datetime) and insert_params[10].tzinfo == timezone.utc
    assert isinstance(insert_params[11], Jsonb)
    assert all(statement.count("%s") == len(params) for statement, params in conn.calls)


def test_revoke_locks_composite_scope_and_uses_schema_supported_null_cas():
    class RevokeConnection(Connection):
        def execute(self, sql, params=()):
            self.calls.append((sql, params))
            if "FOR UPDATE OF c" in sql:
                return Cursor({"owner_user_id": "owner", "revoked_at": None})
            return Cursor(rowcount=1)

    service = PostgreSQL憑證儲存庫(
        settings(), envelope(), clock=lambda: 20.0,
        event_id_factory=lambda: "audit-1",
    )
    conn = RevokeConnection()
    service._工作單元 = Unit(conn)
    receipt = service.撤銷憑證(
        端點識別碼="ep-1", 憑證識別碼="cred-1", 擁有者使用者識別碼="owner",
        是否管理者=False, 請求識別碼="request-1",
    )
    update = next(call for call in conn.calls if call[0].startswith("UPDATE endpoint_credentials"))
    audit = next(call for call in conn.calls if call[0].startswith("INSERT INTO audit_events"))
    assert receipt.撤銷時間 == 20.0 and not receipt.是否已撤銷
    assert "id=%s" in update[0] and "endpoint_id=%s" in update[0] and "revoked_at IS NULL" in update[0]
    assert "revision" not in update[0] and "updated_at" not in update[0]
    assert isinstance(update[1][0], datetime) and update[1][0].tzinfo == timezone.utc
    assert "metadata," in audit[0] and isinstance(audit[1][-2], Jsonb)
    assert all(statement.count("%s") == len(params) for statement, params in conn.calls)


def test_owner_isolation_stops_before_credential_insert():
    class OtherOwnerConnection(Connection):
        def execute(self, sql, params=()):
            self.calls.append((sql, params))
            if "SELECT status FROM published_endpoints" in sql:
                return Cursor(None)
            return Cursor(rowcount=1)

    from 繁中代理.發布介面.憑證管理契約 import 找不到端點憑證錯誤

    service = PostgreSQL憑證儲存庫(
        settings(), envelope(), clock=lambda: 10.0, id_factory=lambda: "cred-1",
    )
    conn = OtherOwnerConnection()
    service._工作單元 = Unit(conn)
    try:
        service.建立憑證(
            端點識別碼="ep-1", 擁有者使用者識別碼="other",
            請求=憑證建立命令("client", "invoke", 100.0, (), 30),
        )
    except 找不到端點憑證錯誤:
        pass
    else:
        raise AssertionError("cross-owner credential creation must be denied")
    assert not any(sql.startswith("INSERT INTO endpoint_credentials") for sql, _ in conn.calls)
