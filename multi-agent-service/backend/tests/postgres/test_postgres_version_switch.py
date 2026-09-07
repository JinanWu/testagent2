from contextlib import contextmanager
from datetime import datetime, timezone

from psycopg.types.json import Jsonb

from 繁中代理.交易儲存設定 import 交易儲存設定
from 繁中代理.發布介面.規劃.端點發布 import 發布版本快照
from 繁中代理.發布介面.PostgreSQL版本服務 import PostgreSQL版本配置服務


def settings():
    return 交易儲存設定(
        "postgres", "postgresql:///app?host=/cloudsql/proj:region:db", "proj:region:db", 0, 1, 1,
    )


class Cursor:
    def __init__(self, row=None, rowcount=1):
        self.row, self.rowcount = row, rowcount

    def fetchone(self):
        return self.row


class Connection:
    def __init__(self):
        self.calls = []

    def execute(self, sql, params=()):
        self.calls.append((sql, params))
        if "FROM published_endpoints" in sql and "FOR UPDATE" in sql:
            return Cursor({"owner_user_id": "owner", "status": "active", "current_version_id": "v-1"})
        if "SELECT version_number,input_schema" in sql:
            return Cursor({"version_number": 1, "input_schema": None, "response_schema": {}})
        return Cursor(rowcount=1)


class Unit:
    def __init__(self, conn):
        self.conn = conn

    @contextmanager
    def 交易(self):
        yield self.conn


def test_create_version_locks_endpoint_and_only_inserts_immutable_0001_row():
    snapshot = 發布版本快照(
        "requirement", "prompt", [], [], {}, "runtime-1", {}, {}, {}, None, {}, "owner",
    )
    service = PostgreSQL版本配置服務(settings(), lambda: "v-2", lambda: 20.0)
    conn = Connection()
    service._工作單元 = Unit(conn)
    result = service.配置("owner", "ep-1", snapshot)
    sql = "\n".join(statement for statement, _ in conn.calls)
    assert (result.version_id, result.version_number) == ("v-2", 2)
    assert "FOR UPDATE" in sql
    assert "INSERT INTO published_endpoint_versions" in sql
    assert "UPDATE published_endpoint_versions" not in sql
    insert = next(call for call in conn.calls if call[0].startswith("INSERT INTO published_endpoint_versions"))
    assert "allowed_skills," in insert[0] and "allowed_skills_json" not in insert[0]
    assert any(isinstance(value, Jsonb) for value in insert[1])
    assert isinstance(insert[1][-1], datetime) and insert[1][-1].tzinfo == timezone.utc
    assert all(statement.count("%s") == len(params) for statement, params in conn.calls)


def test_switch_uses_owner_scoped_current_pointer_cas_and_0001_audit_metadata():
    class SwitchConnection(Connection):
        def execute(self, sql, params=()):
            self.calls.append((sql, params))
            if "FROM published_endpoints" in sql:
                return Cursor({"owner_user_id": "owner", "status": "active", "current_version_id": "v-1"})
            if "SELECT version_number FROM" in sql:
                return Cursor({"version_number": 1})
            if "skill_bundle_manifest" in sql:
                return Cursor({"version_number": 2, "skill_bundle_manifest": {}})
            return Cursor({"current_version_id": "v-2"})

    service = PostgreSQL版本配置服務(settings(), lambda: "unused", lambda: 20.0)
    conn = SwitchConnection()
    service._工作單元 = Unit(conn)
    result = service.啟用("owner", "ep-1", "v-2", audit_id_factory=lambda: "audit-1")
    update = next((sql, params) for sql, params in conn.calls if sql.startswith("UPDATE published_endpoints"))
    audit = next((sql, params) for sql, params in conn.calls if sql.startswith("INSERT INTO audit_events"))
    assert result.old_version_id == "v-1" and result.new_version_id == "v-2"
    assert "owner_user_id=%s" in update[0] and "current_version_id=%s" in update[0]
    assert update[1][-1] == "v-1" and isinstance(update[1][1], datetime)
    assert "metadata," in audit[0] and "metadata_json" not in audit[0]
    assert isinstance(audit[1][-2], Jsonb)
    assert all(statement.count("%s") == len(params) for statement, params in conn.calls)


def test_switch_does_not_cross_owner_boundary():
    class OtherOwnerConnection(Connection):
        def execute(self, sql, params=()):
            self.calls.append((sql, params))
            if "FROM published_endpoints" in sql:
                return Cursor({"owner_user_id": "other", "status": "active", "current_version_id": "v-1"})
            return Cursor(rowcount=1)

    from 繁中代理.發布介面.規劃.版本服務 import 版本啟用存取錯誤

    service = PostgreSQL版本配置服務(settings(), lambda: "unused", lambda: 20.0)
    conn = OtherOwnerConnection()
    service._工作單元 = Unit(conn)
    try:
        service.啟用("owner", "ep-1", "v-2")
    except 版本啟用存取錯誤:
        pass
    else:
        raise AssertionError("cross-owner switch must be denied")
    assert not any(sql.startswith("UPDATE published_endpoints") for sql, _ in conn.calls)
