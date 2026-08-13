import concurrent.futures
import hashlib
import json
import sqlite3
from contextlib import closing
from types import MappingProxyType, MethodType

import pytest

from 繁中代理.發布介面.憑證.儲存庫 import (
    SQLite憑證儲存庫, 憑證儲存錯誤, 註冊憑證SQLite函式,
)
from 繁中代理.發布介面.憑證.加密 import AESGCM密文, AESGCM憑證封套
from 繁中代理.發布介面.憑證.服務 import (
    SQLite憑證揭露服務, SQLite憑證撤銷服務, SQLite憑證驗證服務,
    明文憑證結果,
    憑證刷新狀態, 憑證揭露錯誤, 憑證撤銷錯誤,
    憑證驗證結果, 憑證驗證狀態, 憑證驗證錯誤,
)
from 繁中代理.發布介面.領域模型 import (
    AuditActorRef, AuditAppendReceipt, AuditEvent, AuditMetadata, AuditResourceRef,
)
from 繁中代理.發布介面.資料庫 import 初始化發布介面資料庫
from 繁中代理.發布介面.領域模型 import WebOwnerPrincipal


def _建立端點資料庫(db):
    初始化發布介面資料庫(db)
    with sqlite3.connect(db) as connection:
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("INSERT INTO service_accounts(id,created_at) VALUES ('sa1',1),('sa2',1)")
        connection.execute(
            "INSERT INTO published_endpoints(id,owner_user_id,service_account_id,slug,status,created_at,updated_at) "
            "VALUES ('ep1','owner-1','sa1','one','active',1,1),('ep2','owner-2','sa2','two','active',1,1)"
        )


def test_owner建立credential只回傳一次明文且DB保存canonical密文(tmp_path):
    db = tmp_path / "credentials.sqlite3"
    _建立端點資料庫(db)
    vault = AESGCM憑證封套({1: b"1" * 32, 2: b"2" * 32}, 2)
    repository = SQLite憑證儲存庫(db, vault, clock=lambda: 100.0, id_factory=lambda: "cred-1")
    result = repository.建立(
        "ep1", WebOwnerPrincipal("owner-1"), name="production", purpose="partner integration",
        expires_at=200.0, ip_allowlist=("192.0.2.9/24", "192.0.2.0/24", "2001:db8::1"),
        rate_limit_requests=80,
    )
    assert result.ip_allowlist == ("192.0.2.0/24", "2001:db8::1")
    assert result.api_key not in repr(result) and result.api_key.encode() not in db.read_bytes()
    with sqlite3.connect(db) as connection:
        row = connection.execute(
            "SELECT key_version,key_nonce,key_ciphertext,key_hash,key_prefix,key_last4,ip_allowlist_json,"
            "created_by_user_id,rate_limit_requests FROM endpoint_credentials"
        ).fetchone()
    assert row[:1] == (2,) and len(row[1]) == 12 and len(row[2]) == 62
    assert row[3:6] == (
        hashlib.sha256(result.api_key.encode()).hexdigest(), result.key_prefix, result.key_last4,
    )
    assert json.loads(row[6]) == list(result.ip_allowlist) and row[7:] == ("owner-1", 80)
    assert vault.解密(AESGCM密文(row[0], row[1], row[2]), "ep1", "cred-1") == result.api_key


@pytest.mark.parametrize(
    "overrides",
    [
        {"name": " "}, {"purpose": "Bearer pasted"}, {"expires_at": 100.0},
        {"rate_limit_requests": 0}, {"ip_allowlist": ("not-an-ip",)},
        {"ip_allowlist": ("fe80::1%eth0",)},
    ],
)
def test_invalid_metadata在產生或寫入secret前fail_closed(tmp_path, overrides):
    db = tmp_path / "invalid.sqlite3"
    _建立端點資料庫(db)
    random_calls = []
    vault = AESGCM憑證封套(
        {1: b"k" * 32}, 1,
        隨機位元組=lambda length: random_calls.append(length) or b"r" * length,
    )
    repository = SQLite憑證儲存庫(db, vault, clock=lambda: 100.0)
    payload = {"name": "name", "purpose": "purpose", "expires_at": 200.0, **overrides}
    with pytest.raises(憑證儲存錯誤, match="憑證建立失敗"):
        repository.建立("ep1", WebOwnerPrincipal("owner-1"), **payload)
    with sqlite3.connect(db) as connection:
        assert connection.execute("SELECT COUNT(*) FROM endpoint_credentials").fetchone() == (0,)
    assert random_calls == []


def test_foreign_owner與duplicate_id皆rollback(tmp_path):
    db = tmp_path / "rollback.sqlite3"
    _建立端點資料庫(db)
    repository = SQLite憑證儲存庫(
        db, AESGCM憑證封套({1: b"k" * 32}, 1), clock=lambda: 100.0, id_factory=lambda: "same-id"
    )
    kwargs = {"name": "name", "purpose": "purpose", "expires_at": 200.0}
    with pytest.raises(憑證儲存錯誤):
        repository.建立("ep1", WebOwnerPrincipal("owner-2"), **kwargs)
    for status in ("disabled", "archived"):
        with sqlite3.connect(db) as connection:
            connection.execute("UPDATE published_endpoints SET status=? WHERE id='ep1'", (status,))
        with pytest.raises(憑證儲存錯誤):
            repository.建立("ep1", WebOwnerPrincipal("owner-1"), **kwargs)
    with sqlite3.connect(db) as connection:
        connection.execute("UPDATE published_endpoints SET status='active' WHERE id='ep1'")
    first = repository.建立("ep1", WebOwnerPrincipal("owner-1"), **kwargs)
    with pytest.raises(憑證儲存錯誤):
        repository.建立("ep1", WebOwnerPrincipal("owner-1"), **kwargs)
    with sqlite3.connect(db) as connection:
        assert connection.execute("SELECT COUNT(*) FROM endpoint_credentials").fetchone() == (1,)
        assert connection.execute("SELECT key_hash FROM endpoint_credentials").fetchone()[0] == hashlib.sha256(
            first.api_key.encode()
        ).hexdigest()


def _新增端點版本(db):
    columns = (
        "id,endpoint_id,version_number,original_requirement_text,system_prompt,allowed_skills_json,"
        "allowed_tools_json,tool_schema_snapshot_json,tool_runtime_revision,model_config_snapshot_json,"
        "retry_policy_json,skill_bundle_manifest_json,input_schema_json,response_schema_json,schema_changed,"
        "created_by_user_id,created_at"
    )
    with sqlite3.connect(db) as connection:
        for version in (1, 2):
            values = (
                f"v{version}", "ep1", version, "requirement", "prompt", "[]", "[]", "{}", "runtime-1",
                "{}", "{}", "{}", None, "{}", int(version != 1), "owner-1", 10.0,
            )
            connection.execute(f"INSERT INTO published_endpoint_versions({columns}) VALUES ({','.join('?' * 17)})", values)
        connection.execute("UPDATE published_endpoints SET current_version_id='v1' WHERE id='ep1'")


def test_endpoint_bound_key即時解析current_version且驗證不刷新last_used(tmp_path):
    db = tmp_path / "verify.sqlite3"
    _建立端點資料庫(db)
    _新增端點版本(db)
    vault = AESGCM憑證封套({1: b"k" * 32}, 1)
    created = SQLite憑證儲存庫(
        db, vault, clock=lambda: 100.0, id_factory=lambda: "verified-credential"
    ).建立("ep1", WebOwnerPrincipal("owner-1"), name="name", purpose="purpose", expires_at=200.0)
    service = SQLite憑證驗證服務(db, clock=lambda: 150.0)
    first = service.驗證("ep1", created.api_key)
    assert (first.status, first.current_version_id, first.endpoint_status) == (
        憑證驗證狀態.有效, "v1", "active",
    )
    with sqlite3.connect(db) as connection:
        connection.execute("UPDATE published_endpoints SET current_version_id='v2' WHERE id='ep1'")
    assert service.驗證("ep1", created.api_key).current_version_id == "v2"
    unknown = vault.產生並加密("ep1", "unknown-credential").api_key
    assert service.驗證("ep2", created.api_key) == service.驗證("ep1", unknown) == first.invalid()
    with sqlite3.connect(db) as connection:
        assert connection.execute("SELECT last_used_at FROM endpoint_credentials").fetchone() == (None,)
        connection.execute(
            "UPDATE endpoint_credentials SET key_hash=?", (hashlib.sha256(b"x").hexdigest(),)
        )
    assert service.驗證("ep1", "x").status is 憑證驗證狀態.無效


@pytest.mark.parametrize("now, expected", [(199.999, 憑證驗證狀態.有效), (200.0, 憑證驗證狀態.已過期), (201.0, 憑證驗證狀態.已過期)])
def test_expiry使用now大於等於boundary(tmp_path, now, expected):
    db = tmp_path / "expiry.sqlite3"
    _建立端點資料庫(db)
    created = SQLite憑證儲存庫(
        db, AESGCM憑證封套({1: b"k" * 32}, 1), clock=lambda: 100.0, id_factory=lambda: "expiry-key"
    ).建立("ep1", WebOwnerPrincipal("owner-1"), name="name", purpose="purpose", expires_at=200.0)
    assert SQLite憑證驗證服務(db, clock=lambda: now).驗證("ep1", created.api_key).status is expected


def test_revoked優先於expired且infra_error不洩漏raw_key(tmp_path):
    db = tmp_path / "revoked.sqlite3"
    _建立端點資料庫(db)
    created = SQLite憑證儲存庫(
        db, AESGCM憑證封套({1: b"k" * 32}, 1), clock=lambda: 100.0, id_factory=lambda: "revoked-key"
    ).建立("ep1", WebOwnerPrincipal("owner-1"), name="name", purpose="purpose", expires_at=200.0)
    with sqlite3.connect(db) as connection:
        connection.execute("UPDATE endpoint_credentials SET revoked_at=150 WHERE id='revoked-key'")
    result = SQLite憑證驗證服務(db, clock=lambda: 201.0).驗證("ep1", created.api_key)
    assert result.status is 憑證驗證狀態.已撤銷 and created.api_key not in repr(result)
    unknown = AESGCM憑證封套({1: b"z" * 32}, 1).產生並加密("ep1", "unknown").api_key
    for candidate in (created.api_key, unknown):
        with pytest.raises(憑證驗證錯誤):
            SQLite憑證驗證服務(db, clock=lambda: float("nan")).驗證("ep1", candidate)
    missing = tmp_path / "missing.sqlite3"
    with pytest.raises(憑證驗證錯誤) as error:
        SQLite憑證驗證服務(missing).驗證("ep1", created.api_key)
    assert not missing.exists()
    traceback = error.value.__traceback__
    while traceback is not None:
        if "/憑證/服務.py" in traceback.tb_frame.f_code.co_filename:
            assert created.api_key not in repr(traceback.tb_frame.f_locals)
        traceback = traceback.tb_next


INACTIVITY_SECONDS = 15_552_000


@pytest.mark.parametrize(
    "offset,expected",
    [
        (INACTIVITY_SECONDS - 1, 憑證驗證狀態.有效),
        (INACTIVITY_SECONDS, 憑證驗證狀態.無效),
        (INACTIVITY_SECONDS + 1, 憑證驗證狀態.無效),
    ],
)
def test_連續180日未使用在exact_boundary停用(tmp_path, offset, expected):
    db = tmp_path / "inactive.sqlite3"
    _建立端點資料庫(db)
    created = SQLite憑證儲存庫(
        db, AESGCM憑證封套({1: b"k" * 32}, 1), clock=lambda: 100.0, id_factory=lambda: "inactive-key"
    ).建立(
        "ep1", WebOwnerPrincipal("owner-1"), name="name", purpose="purpose",
        expires_at=INACTIVITY_SECONDS * 3,
    )
    result = SQLite憑證驗證服務(db, clock=lambda: 100.0 + offset).驗證("ep1", created.api_key)
    assert result.status is expected
    assert 憑證驗證狀態.無效.value == "invalid_api_key"


def test_D19A_authenticated進pipeline即使disabled_archived或retry仍單調刷新(tmp_path):
    db = tmp_path / "refresh.sqlite3"
    _建立端點資料庫(db)
    created = SQLite憑證儲存庫(
        db, AESGCM憑證封套({1: b"k" * 32}, 1), clock=lambda: 100.0, id_factory=lambda: "refresh-key"
    ).建立("ep1", WebOwnerPrincipal("owner-1"), name="name", purpose="purpose", expires_at=INACTIVITY_SECONDS * 3)
    at = 100.0 + INACTIVITY_SECONDS - 1
    service = SQLite憑證驗證服務(db, clock=lambda: at)
    authentication = service.驗證("ep1", created.api_key)
    for endpoint_status in ("disabled", "archived"):
        with sqlite3.connect(db) as connection:
            connection.execute("UPDATE published_endpoints SET status=? WHERE id='ep1'", (endpoint_status,))
        assert service.刷新已認證使用(authentication, at) in (憑證刷新狀態.已刷新, 憑證刷新狀態.無變更)
    assert service.刷新已認證使用(authentication, at - 10) is 憑證刷新狀態.無變更
    assert SQLite憑證驗證服務(db, clock=lambda: at + 100).驗證("ep1", created.api_key).status is 憑證驗證狀態.有效


def test_invalid_expired_revoked永不刷新且write_failure不掩蓋(tmp_path):
    db = tmp_path / "no-refresh.sqlite3"
    _建立端點資料庫(db)
    created = SQLite憑證儲存庫(
        db, AESGCM憑證封套({1: b"k" * 32}, 1), clock=lambda: 100.0, id_factory=lambda: "no-refresh-key"
    ).建立("ep1", WebOwnerPrincipal("owner-1"), name="name", purpose="purpose", expires_at=200.0)
    service = SQLite憑證驗證服務(db, clock=lambda: 201.0)
    assert service.刷新已認證使用(service.驗證("ep1", created.api_key), 201.0) is 憑證刷新狀態.已略過
    with sqlite3.connect(db) as connection:
        connection.execute("UPDATE endpoint_credentials SET revoked_at=150 WHERE id='no-refresh-key'")
    assert service.刷新已認證使用(service.驗證("ep1", created.api_key), 150.0) is 憑證刷新狀態.已略過
    assert service.刷新已認證使用(憑證驗證結果.invalid(), 150.0) is 憑證刷新狀態.已略過


def test_concurrent_refresh取max且DB_failure回typed_result(tmp_path):
    db = tmp_path / "concurrent-refresh.sqlite3"
    _建立端點資料庫(db)
    created = SQLite憑證儲存庫(
        db, AESGCM憑證封套({1: b"k" * 32}, 1), clock=lambda: 100.0, id_factory=lambda: "concurrent-key"
    ).建立("ep1", WebOwnerPrincipal("owner-1"), name="name", purpose="purpose", expires_at=INACTIVITY_SECONDS * 3)
    service = SQLite憑證驗證服務(db, clock=lambda: 500.0)
    authentication = service.驗證("ep1", created.api_key)
    forged = 憑證驗證結果(
        憑證驗證狀態.有效, credential_id="concurrent-key", endpoint_id="ep1",
    )
    assert service.刷新已認證使用(forged, 200.0) is 憑證刷新狀態.已略過
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = tuple(pool.map(lambda at: service.刷新已認證使用(authentication, at), (200.0, 300.0)))
    assert all(outcome in (憑證刷新狀態.已刷新, 憑證刷新狀態.無變更) for outcome in outcomes)
    with sqlite3.connect(db) as connection:
        assert connection.execute("SELECT last_used_at FROM endpoint_credentials").fetchone() == (300.0,)
        connection.execute(
            "CREATE TRIGGER block_refresh BEFORE UPDATE OF last_used_at ON endpoint_credentials "
            "BEGIN SELECT RAISE(ABORT,'blocked'); END"
        )
    assert service.刷新已認證使用(authentication, 400.0) is 憑證刷新狀態.失敗
    with sqlite3.connect(db) as connection:
        assert connection.execute("SELECT last_used_at FROM endpoint_credentials").fetchone() == (300.0,)
        connection.execute("DROP TRIGGER block_refresh")
        connection.execute(
            "UPDATE endpoint_credentials SET last_used_at=1000,updated_at=1000 WHERE id='concurrent-key'"
        )
    with pytest.raises(憑證驗證錯誤):
        service.驗證("ep1", created.api_key)
    assert service.刷新已認證使用(authentication, 400.0) is 憑證刷新狀態.已略過


class _RevealAuditSink:
    def __init__(self, committed, order, after_append=None):
        self.committed = committed
        self.order = order
        self.calls = []
        self.after_append = after_append

    def append_audit_event(self, event, /):
        self.order.append("audit")
        self.calls.append(event)
        if self.after_append is not None:
            self.after_append()
        return AuditAppendReceipt(event.event_id, self.committed, 1 if self.committed else None)


class _SQLiteRevealAuditSink:
    def __init__(self, db, order):
        self.db = db
        self.order = order
        self.calls = []

    def append_audit_event(self, event, /):
        with closing(sqlite3.connect(self.db)) as connection:
            with connection:
                註冊憑證SQLite函式(connection)
                cursor = connection.execute(
                    "INSERT INTO audit_events(event_id,occurred_at,action,outcome,actor_type,actor_id,"
                    "resource_type,resource_id,request_id,endpoint_id,invocation_id,metadata_json,created_at) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        event.event_id, event.occurred_at, event.action, event.outcome,
                        event.actor.actor_type, event.actor.actor_id, event.resource.resource_type,
                        event.resource.resource_id, event.request_id, event.endpoint_id,
                        event.invocation_id, json.dumps(event.metadata.to_json(), separators=(",", ":")),
                        event.occurred_at,
                    ),
                )
                sequence = cursor.lastrowid
                cursor.close()
        self.order.append("audit")
        self.calls.append(event)
        return AuditAppendReceipt(event.event_id, True, sequence)


def _建立可揭露憑證(tmp_path):
    db = tmp_path / "reveal.sqlite3"
    _建立端點資料庫(db)
    vault = AESGCM憑證封套({1: b"k" * 32}, 1)
    created = SQLite憑證儲存庫(
        db, vault, clock=lambda: 100.0, id_factory=lambda: "reveal-key"
    ).建立("ep1", WebOwnerPrincipal("owner-1"), name="name", purpose="purpose", expires_at=1000.0)
    return db, vault, created


def test_credential_create_missing_DB不建立空檔(tmp_path):
    missing = tmp_path / "missing" / "credentials.sqlite3"
    repository = SQLite憑證儲存庫(
        missing, AESGCM憑證封套({1: b"k" * 32}, 1), clock=lambda: 100.0,
    )
    with pytest.raises(憑證儲存錯誤, match="憑證建立失敗"):
        repository.建立(
            "ep1", WebOwnerPrincipal("owner-1"), name="name", purpose="purpose",
            expires_at=1000.0,
        )
    assert not missing.exists() and not missing.parent.exists()


def test_owner_reveal必須audit_commit在decrypt之前且DTO_repr無明文(tmp_path, monkeypatch):
    db, vault, created = _建立可揭露憑證(tmp_path)
    order = []
    sink = _SQLiteRevealAuditSink(db, order)
    decrypt = vault.解密
    def decrypt_spy(envelope, endpoint_id, credential_id):
        with closing(sqlite3.connect(db)) as connection:
            assert connection.execute("SELECT COUNT(*) FROM audit_events").fetchone() == (1,)
        order.append("decrypt")
        return decrypt(envelope, endpoint_id, credential_id)
    monkeypatch.setattr(vault, "解密", decrypt_spy)
    result = SQLite憑證揭露服務(
        db, vault, sink, clock=lambda: 150.0, event_id_factory=lambda: "audit-reveal-1"
    ).揭露("ep1", "reveal-key", WebOwnerPrincipal("owner-1"), "request-1")
    assert order == ["audit", "decrypt"] and result.api_key == created.api_key
    assert created.api_key not in repr(result)
    event = sink.calls[0]
    assert (event.action, event.actor.actor_id, event.resource.resource_id) == (
        "credential.reveal_attempt", "owner-1", "reveal-key",
    )
    assert created.api_key not in repr(event.to_json())


class _RevealCustomBase(BaseException): pass


class _RevealKeyboardInterrupt(KeyboardInterrupt):
    def __setattr__(self, name, value):
        raise RuntimeError("不得呼叫控制例外覆寫")


class _RevealSystemExit(SystemExit):
    def __setattr__(self, name, value):
        raise RuntimeError("不得呼叫控制例外覆寫")


class _RevealGeneratorExit(GeneratorExit):
    def __setattr__(self, name, value):
        raise RuntimeError("不得呼叫控制例外覆寫")


class _RevealConnectionProxy:
    def __init__(self, connection, *, rollback_error=None, close_error=None):
        self.connection = connection
        self.rollback_error = rollback_error
        self.close_error = close_error
        self.execute_calls = []
        self.close_calls = 0

    @property
    def in_transaction(self):
        return self.connection.in_transaction

    def execute(self, sql, parameters=()):
        statement = sql.strip().upper()
        self.execute_calls.append(statement)
        if statement == "ROLLBACK" and self.rollback_error is not None:
            raise self.rollback_error
        return self.connection.execute(sql, parameters)

    def close(self):
        self.close_calls += 1
        self.connection.close()
        if self.close_error is not None:
            raise self.close_error


def _揭露標記可達(value, markers, visited):
    identity = id(value)
    if identity in visited:
        return False
    visited.add(identity)
    if type(value) in (str, bytes):
        return value in markers
    if type(value) in (tuple, list, set, frozenset):
        return any(_揭露標記可達(item, markers, visited) for item in value)
    if type(value) in (dict, MappingProxyType):
        return any(
            _揭露標記可達(item, markers, visited)
            for pair in value.items() for item in pair
        )
    if type(value) in (
        RuntimeError, _RevealCustomBase, KeyboardInterrupt, SystemExit, GeneratorExit,
        _RevealKeyboardInterrupt, _RevealSystemExit, _RevealGeneratorExit, 憑證揭露錯誤,
    ):
        return any(
            _揭露標記可達(item, markers, visited)
            for item in (value.args, value.__cause__, value.__context__)
        )
    if type(value) is _RevealConnectionProxy:
        return _揭露標記可達(value.__dict__, markers, visited)
    if type(value) is SQLite憑證揭露服務:
        names = ("_database", "_envelope", "_audit_sink", "_clock", "_event_id_factory")
        return any(_揭露標記可達(object.__getattribute__(value, name), markers, visited) for name in names)
    if type(value) is AESGCM憑證封套:
        # Production envelope keeps master-key bytes behind opaque crypto primitives.
        return False
    if type(value) is MethodType:
        return _揭露標記可達(object.__getattribute__(value, "__self__"), markers, visited)
    if type(value) is AESGCM密文:
        names = ("key_version", "nonce", "ciphertext")
        return any(_揭露標記可達(object.__getattribute__(value, name), markers, visited) for name in names)
    if type(value) is 明文憑證結果:
        names = ("credential_id", "api_key", "key_prefix", "key_last4")
        return any(_揭露標記可達(object.__getattribute__(value, name), markers, visited) for name in names)
    if type(value) is AuditEvent:
        names = (
            "event_id", "occurred_at", "action", "outcome", "actor", "resource",
            "request_id", "endpoint_id", "invocation_id", "metadata",
        )
        return any(_揭露標記可達(object.__getattribute__(value, name), markers, visited) for name in names)
    if type(value) is AuditAppendReceipt:
        names = ("event_id", "committed", "sequence")
        return any(_揭露標記可達(object.__getattribute__(value, name), markers, visited) for name in names)
    if type(value) is AuditActorRef:
        names = ("actor_type", "actor_id")
        return any(_揭露標記可達(object.__getattribute__(value, name), markers, visited) for name in names)
    if type(value) is AuditResourceRef:
        names = ("resource_type", "resource_id")
        return any(_揭露標記可達(object.__getattribute__(value, name), markers, visited) for name in names)
    if type(value) is AuditMetadata:
        return _揭露標記可達(object.__getattribute__(value, "_資料"), markers, visited)
    if type(value) is WebOwnerPrincipal:
        return _揭露標記可達(object.__getattribute__(value, "user_id"), markers, visited)
    return False


def _斷言揭露控制已清理(error, absent_markers, frame_markers):
    assert error.__cause__ is None and error.__context__ is None
    assert not _揭露標記可達(error, absent_markers, set())
    traceback = error.__traceback__
    while traceback is not None:
        if "/繁中代理/" in traceback.tb_frame.f_code.co_filename:
            if traceback.tb_frame.f_code.co_name == "揭露":
                assert traceback.tb_frame.f_locals["主要控制盒"] == []
                assert traceback.tb_frame.f_locals["清理控制盒"] == []
            for local in tuple(traceback.tb_frame.f_locals.values()):
                assert not _揭露標記可達(local, frame_markers, set()), traceback.tb_frame.f_code.co_name
        traceback = traceback.tb_next


def _揭露真實敏感標記(db, created, request_id, event_id):
    """取出真實authoritative row標記，避免close-path oracle只檢查控制字串。"""
    with closing(sqlite3.connect(db)) as connection:
        row = connection.execute(
            "SELECT key_ciphertext,key_hash,key_prefix,key_last4 FROM endpoint_credentials WHERE id=?",
            ("reveal-key",),
        ).fetchone()
    assert row is not None
    return (b"k" * 32, created.api_key, *row, "ep1", "reveal-key", "owner-1", request_id, event_id)


def test_揭露oracle可偵測exact敏感DTO別名():
    """Positive controls證明plaintext、ciphertext及audit graph不會成為漏檢別名。"""
    ciphertext = AESGCM密文(1, b"nonce-oracle", b"cipher-oracle")
    plaintext = 明文憑證結果("credential-oracle", "plaintext-oracle", "prefix-oracle", "last4")
    receipt = AuditAppendReceipt("receipt-oracle", True, 1)
    event = AuditEvent(
        event_id="event-oracle", occurred_at=1,
        action="credential.reveal_attempt", outcome="success",
        actor=AuditActorRef("user", "owner-oracle"),
        resource=AuditResourceRef("endpoint_credential", "credential-oracle"),
        request_id="request-oracle", endpoint_id="endpoint-oracle",
        metadata=AuditMetadata(),
    )
    method_master_marker = b"method-master-key-marker-32byte!"
    real_bound_decrypt = AESGCM憑證封套({1: method_master_marker}, 1).解密
    assert not _揭露標記可達(real_bound_decrypt, (method_master_marker,), set())
    for value, marker in (
        (ciphertext, b"cipher-oracle"),
        (plaintext, "plaintext-oracle"),
        (receipt, "receipt-oracle"),
        (event, "request-oracle"),
        (event, "owner-oracle"),
        (event, "credential-oracle"),
    ):
        assert _揭露標記可達(value, (marker,), set())


@pytest.mark.parametrize(
    "error_type",
    [KeyboardInterrupt, SystemExit, GeneratorExit,
     _RevealKeyboardInterrupt, _RevealSystemExit, _RevealGeneratorExit],
)
def test_reveal初始唯讀close控制只嘗試一次且不audit_decrypt(tmp_path, monkeypatch, error_type):
    db, vault, created = _建立可揭露憑證(tmp_path)
    sensitive_markers = _揭露真實敏感標記(db, created, "request-initial", "audit-never")
    real_connect = sqlite3.connect
    winner_marker = f"initial-close-{error_type.__name__}"
    chain_markers = ("initial-old-cause", "initial-old-context")
    winner = error_type(winner_marker)
    BaseException.__setattr__(winner, "__cause__", RuntimeError(chain_markers[0]))
    BaseException.__setattr__(winner, "__context__", RuntimeError(chain_markers[1]))
    proxies = []

    def connect(database, *args, **kwargs):
        connection = real_connect(database, *args, **kwargs)
        if "?mode=ro" not in str(database):
            return connection
        proxy = _RevealConnectionProxy(connection, close_error=winner)
        proxies.append(proxy)
        return proxy

    monkeypatch.setattr(sqlite3, "connect", connect)
    order = []
    sink = _RevealAuditSink(True, order)
    monkeypatch.setattr(vault, "解密", lambda *_args: order.append("decrypt"))
    service = SQLite憑證揭露服務(
        db, vault, sink, clock=lambda: 150.0, event_id_factory=lambda: "audit-never",
    )
    with pytest.raises(error_type) as caught:
        service.揭露("ep1", "reveal-key", WebOwnerPrincipal("owner-1"), "request-initial")
    assert caught.value is winner and caught.value.args == (winner_marker,)
    assert len(proxies) == 1 and proxies[0].close_calls == 1
    assert order == [] and sink.calls == []
    _斷言揭露控制已清理(
        winner,
        (*chain_markers, *sensitive_markers),
        (winner_marker, *chain_markers, *sensitive_markers),
    )


def _執行揭露交易清理案例(tmp_path, monkeypatch, primary, rollback, close_error):
    db, vault, created = _建立可揭露憑證(tmp_path)
    sensitive_markers = _揭露真實敏感標記(db, created, "request-cleanup", "audit-cleanup")
    real_connect = sqlite3.connect
    proxies = []

    def connect(database, *args, **kwargs):
        connection = real_connect(database, *args, **kwargs)
        if "?mode=rw" not in str(database):
            return connection
        proxy = _RevealConnectionProxy(
            connection, rollback_error=rollback, close_error=close_error,
        )
        proxies.append(proxy)
        return proxy

    monkeypatch.setattr(sqlite3, "connect", connect)
    order = []
    sink = _SQLiteRevealAuditSink(db, order)
    if primary is not None:
        monkeypatch.setattr(vault, "解密", lambda *_args: (_ for _ in ()).throw(primary))
    service = SQLite憑證揭露服務(
        db, vault, sink, clock=lambda: 150.0, event_id_factory=lambda: "audit-cleanup",
    )
    with pytest.raises(BaseException) as caught:
        service.揭露("ep1", "reveal-key", WebOwnerPrincipal("owner-1"), "request-cleanup")
    assert len(proxies) == 1 and proxies[0].close_calls == 1
    return caught.value, proxies[0], db, order, sensitive_markers


def test_reveal原始decrypt控制勝過rollback與close控制(tmp_path, monkeypatch):
    primary = _RevealGeneratorExit("decrypt-primary")
    rollback = _RevealKeyboardInterrupt("rollback-loser")
    close_error = _RevealSystemExit("close-loser")
    BaseException.__setattr__(primary, "__cause__", RuntimeError("primary-old-cause"))
    caught, proxy, _db, order, sensitive_markers = _執行揭露交易清理案例(
        tmp_path, monkeypatch, primary, rollback, close_error,
    )
    assert caught is primary and order == ["audit"]
    assert proxy.execute_calls.count("ROLLBACK") == 1
    _斷言揭露控制已清理(
        caught,
        ("rollback-loser", "close-loser", "primary-old-cause", *sensitive_markers),
        ("decrypt-primary", "rollback-loser", "close-loser", "primary-old-cause", *sensitive_markers),
    )


@pytest.mark.parametrize("with_rollback", [True, False])
def test_reveal普通decrypt失敗由rollback再close控制依序勝出(tmp_path, monkeypatch, with_rollback):
    primary = RuntimeError("ordinary-primary")
    rollback = _RevealKeyboardInterrupt("rollback-winner") if with_rollback else None
    close_error = _RevealSystemExit("close-winner")
    expected = rollback if with_rollback else close_error
    loser = "close-winner" if with_rollback else "ordinary-primary"
    caught, proxy, _db, order, sensitive_markers = _執行揭露交易清理案例(
        tmp_path, monkeypatch, primary, rollback, close_error,
    )
    assert caught is expected and order == ["audit"]
    assert proxy.execute_calls.count("ROLLBACK") == 1
    _斷言揭露控制已清理(
        caught, (loser, *sensitive_markers),
        ("ordinary-primary", "rollback-winner", "close-winner", *sensitive_markers),
    )


def test_reveal成功commit後close控制勝出且audit持久可見(tmp_path, monkeypatch):
    close_error = _RevealSystemExit("committed-close")
    caught, proxy, db, order, sensitive_markers = _執行揭露交易清理案例(
        tmp_path, monkeypatch, None, None, close_error,
    )
    assert caught is close_error and order == ["audit"]
    assert proxy.execute_calls.count("COMMIT") == 1
    assert proxy.execute_calls.count("ROLLBACK") == 0
    with closing(sqlite3.connect(db)) as connection:
        assert connection.execute(
            "SELECT action FROM audit_events WHERE event_id='audit-cleanup'"
        ).fetchone() == ("credential.reveal_attempt",)
    _斷言揭露控制已清理(
        caught, sensitive_markers, ("committed-close", *sensitive_markers),
    )


@pytest.mark.parametrize("boundary", ["audit", "reread", "decrypt"])
@pytest.mark.parametrize(
    "error_type,is_control",
    [(RuntimeError, False), (_RevealCustomBase, False), (KeyboardInterrupt, True),
     (SystemExit, True), (GeneratorExit, True), (_RevealKeyboardInterrupt, True),
     (_RevealSystemExit, True), (_RevealGeneratorExit, True)],
)
def test_reveal所有邊界先清除master_key與request_plaintext才固定失敗或重拋控制(tmp_path, monkeypatch, boundary, error_type, is_control):
    master_marker = b"M" * 32
    request_marker = "request-trace-marker"
    db = tmp_path / f"trace-{boundary}-{error_type.__name__}.sqlite3"
    _建立端點資料庫(db)
    vault = AESGCM憑證封套({1: master_marker}, 1, 隨機位元組=lambda length: b"P" * length)
    repository = SQLite憑證儲存庫(db, vault, clock=lambda: 100.0, id_factory=lambda: "reveal-trace-key")
    created = repository.建立(
        "ep1", WebOwnerPrincipal("owner-1"), name="name", purpose="purpose", expires_at=1000.0,
    )
    markers = (master_marker, request_marker, created.api_key)
    raised = error_type(markers)
    sink = _RevealAuditSink(True, [])
    service = SQLite憑證揭露服務(db, vault, sink, clock=lambda: 150.0, event_id_factory=lambda: "audit-trace")
    if boundary == "audit":
        monkeypatch.setattr(sink, "append_audit_event", lambda _event: (_ for _ in ()).throw(raised))
    elif boundary == "reread":
        original = service._讀取owner憑證
        calls = 0
        def reread(connection, endpoint_id, credential_id, owner_user_id):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise raised
            return original(connection, endpoint_id, credential_id, owner_user_id)
        monkeypatch.setattr(service, "_讀取owner憑證", reread)
    else:
        monkeypatch.setattr(vault, "解密", lambda *_args: (_ for _ in ()).throw(raised))

    with pytest.raises(BaseException) as caught:
        service.揭露("ep1", "reveal-trace-key", WebOwnerPrincipal("owner-1"), request_marker)
    if is_control:
        assert caught.value is raised and caught.value.args == (markers,)
    else:
        assert type(caught.value) is 憑證揭露錯誤 and caught.value.args == ("憑證揭露失敗",)
        assert caught.value.__cause__ is None and caught.value.__context__ is None
    traceback = caught.value.__traceback__
    while traceback is not None:
        if "/繁中代理/" in traceback.tb_frame.f_code.co_filename:
            for local in tuple(traceback.tb_frame.f_locals.values()):
                assert not _揭露標記可達(local, markers, set()), traceback.tb_frame.f_code.co_name
        traceback = traceback.tb_next


def test_foreign_owner與audit_commit_failure都不decrypt(tmp_path, monkeypatch):
    db, vault, _created = _建立可揭露憑證(tmp_path)
    order = []
    sink = _RevealAuditSink(False, order)
    def decrypt_spy(_envelope, _endpoint_id, _credential_id):
        order.append("decrypt")
        return "should-not-return"
    monkeypatch.setattr(vault, "解密", decrypt_spy)
    service = SQLite憑證揭露服務(
        db, vault, sink, clock=lambda: 150.0, event_id_factory=lambda: "audit-reveal-2"
    )
    with pytest.raises(憑證揭露錯誤, match="憑證揭露失敗"):
        service.揭露("ep1", "reveal-key", WebOwnerPrincipal("owner-2"), "request-2")
    assert order == [] and sink.calls == []
    with pytest.raises(憑證揭露錯誤, match="憑證揭露失敗"):
        service.揭露("ep1", "reveal-key", WebOwnerPrincipal("owner-1"), "request-2")
    assert order == ["audit"] and len(sink.calls) == 1


@pytest.mark.parametrize("lifecycle", ["revoked", "expired", "inactive"])
def test_revoked_expired_inactive都不可audit或decrypt(tmp_path, monkeypatch, lifecycle):
    db, vault, _created = _建立可揭露憑證(tmp_path)
    now = 150.0
    with sqlite3.connect(db) as connection:
        if lifecycle == "revoked":
            connection.execute(
                "UPDATE endpoint_credentials SET revoked_at=120,updated_at=120 WHERE id='reveal-key'"
            )
        elif lifecycle == "expired":
            now = 1000.0
        else:
            now = 15_552_100.0
            connection.execute(
                "UPDATE endpoint_credentials SET expires_at=20000000,updated_at=20000000 "
                "WHERE id='reveal-key'"
            )
    order = []
    sink = _RevealAuditSink(True, order)

    def decrypt_spy(_envelope, _endpoint_id, _credential_id):
        order.append("decrypt")
        return "should-not-return"

    monkeypatch.setattr(vault, "解密", decrypt_spy)
    service = SQLite憑證揭露服務(
        db, vault, sink, clock=lambda: now, event_id_factory=lambda: "audit-lifecycle"
    )
    with pytest.raises(憑證揭露錯誤):
        service.揭露("ep1", "reveal-key", WebOwnerPrincipal("owner-1"), "request-life")
    assert order == [] and sink.calls == []


def test_audit後撤銷會鎖定重驗並拒絕decrypt(tmp_path, monkeypatch):
    db, vault, _created = _建立可揭露憑證(tmp_path)
    order = []

    def revoke_after_audit():
        with sqlite3.connect(db) as connection:
            connection.execute(
                "UPDATE endpoint_credentials SET revoked_at=140,updated_at=140 WHERE id='reveal-key'"
            )

    sink = _RevealAuditSink(True, order, revoke_after_audit)

    def decrypt_spy(_envelope, _endpoint_id, _credential_id):
        order.append("decrypt")
        return "should-not-return"

    monkeypatch.setattr(vault, "解密", decrypt_spy)
    service = SQLite憑證揭露服務(
        db, vault, sink, clock=lambda: 150.0, event_id_factory=lambda: "audit-race"
    )
    with pytest.raises(憑證揭露錯誤):
        service.揭露("ep1", "reveal-key", WebOwnerPrincipal("owner-1"), "request-race")
    assert order == ["audit"] and len(sink.calls) == 1


def test_plaintext必須與hash_prefix_last4一致(tmp_path, monkeypatch):
    db, vault, _created = _建立可揭露憑證(tmp_path)
    with sqlite3.connect(db) as connection:
        connection.execute(
            "UPDATE endpoint_credentials SET key_prefix='pk_AAAAAAAAA' WHERE id='reveal-key'"
        )
    order = []
    sink = _RevealAuditSink(True, order)
    decrypt = vault.解密

    def decrypt_spy(envelope, endpoint_id, credential_id):
        order.append("decrypt")
        return decrypt(envelope, endpoint_id, credential_id)

    monkeypatch.setattr(vault, "解密", decrypt_spy)
    service = SQLite憑證揭露服務(
        db, vault, sink, clock=lambda: 150.0, event_id_factory=lambda: "audit-corrupt"
    )
    with pytest.raises(憑證揭露錯誤):
        service.揭露("ep1", "reveal-key", WebOwnerPrincipal("owner-1"), "request-corrupt")
    assert order == ["audit", "decrypt"]


def test_tampered_ciphertext先audit再fail_closed且traceback無raw(tmp_path, monkeypatch):
    db, vault, created = _建立可揭露憑證(tmp_path)
    with sqlite3.connect(db) as connection:
        ciphertext = connection.execute("SELECT key_ciphertext FROM endpoint_credentials").fetchone()[0]
        connection.execute(
            "UPDATE endpoint_credentials SET key_ciphertext=?",
            (ciphertext[:-1] + bytes([ciphertext[-1] ^ 1]),),
        )
    order = []
    sink = _RevealAuditSink(True, order)
    decrypt = vault.解密
    def decrypt_spy(envelope, endpoint_id, credential_id):
        order.append("decrypt")
        return decrypt(envelope, endpoint_id, credential_id)
    monkeypatch.setattr(vault, "解密", decrypt_spy)
    service = SQLite憑證揭露服務(
        db, vault, sink, clock=lambda: 150.0, event_id_factory=lambda: "audit-reveal-3"
    )
    with pytest.raises(憑證揭露錯誤) as error:
        service.揭露("ep1", "reveal-key", WebOwnerPrincipal("owner-1"), "request-3")
    assert order == ["audit", "decrypt"] and created.api_key not in repr(error.value)
    traceback = error.value.__traceback__
    while traceback is not None:
        if "/憑證/服務.py" in traceback.tb_frame.f_code.co_filename:
            assert created.api_key not in repr(traceback.tb_frame.f_locals)
        traceback = traceback.tb_next


def _建立撤銷測試憑證(db, endpoint_id, owner_id, credential_id):
    vault = AESGCM憑證封套({1: b"r" * 32}, 1)
    return SQLite憑證儲存庫(
        db, vault, clock=lambda: 100.0, id_factory=lambda: credential_id,
    ).建立(
        endpoint_id, WebOwnerPrincipal(owner_id), name="revoke", purpose="lifecycle",
        expires_at=1000.0,
    )


def test_owner_revoke與already_revoked皆atomic_audited且後續驗證為revoked(tmp_path):
    db = tmp_path / "revoke.sqlite3"
    _建立端點資料庫(db)
    created = _建立撤銷測試憑證(db, "ep1", "owner-1", "revoke-1")
    event_ids = iter(("audit-revoke-1", "audit-revoke-2"))
    service = SQLite憑證撤銷服務(
        db, clock=lambda: 150.0, event_id_factory=lambda: next(event_ids),
    )
    first = service.撤銷(
        "ep1", "revoke-1", WebOwnerPrincipal("owner-1"), "request-revoke-1",
    )
    second = service.撤銷(
        "ep1", "revoke-1", WebOwnerPrincipal("owner-1"), "request-revoke-2",
    )
    assert (first.revoked_at, first.already_revoked) == (150.0, False)
    assert (second.revoked_at, second.already_revoked) == (150.0, True)
    with sqlite3.connect(db) as connection:
        credential = connection.execute(
            "SELECT revoked_at,revision FROM endpoint_credentials WHERE id='revoke-1'"
        ).fetchone()
        audits = connection.execute(
            "SELECT action,metadata_json FROM audit_events ORDER BY rowid"
        ).fetchall()
    assert credential == (150.0, 1)
    assert [row[0] for row in audits] == ["credential.revoke", "credential.revoke"]
    assert [json.loads(row[1])["already_revoked"] for row in audits] == [False, True]
    verified = SQLite憑證驗證服務(db, clock=lambda: 151.0).驗證("ep1", created.api_key)
    assert verified.status is 憑證驗證狀態.已撤銷


def test_revoke_composite_scope_owner與admin_policy(tmp_path):
    db = tmp_path / "revoke-scope.sqlite3"
    _建立端點資料庫(db)
    _建立撤銷測試憑證(db, "ep2", "owner-2", "revoke-2")
    service = SQLite憑證撤銷服務(
        db, clock=lambda: 150.0, event_id_factory=lambda: "audit-admin",
    )
    for endpoint_id in ("ep2", "ep1"):
        with pytest.raises(憑證撤銷錯誤, match="憑證撤銷失敗"):
            service.撤銷(
                endpoint_id, "revoke-2", WebOwnerPrincipal("owner-1"), "request-denied",
            )
    with sqlite3.connect(db) as connection:
        assert connection.execute("SELECT COUNT(*) FROM audit_events").fetchone() == (0,)
        assert connection.execute(
            "SELECT revoked_at FROM endpoint_credentials WHERE id='revoke-2'"
        ).fetchone() == (None,)
    result = service.撤銷(
        "ep2", "revoke-2", WebOwnerPrincipal("admin-1"), "request-admin",
        actor_is_admin=True,
    )
    assert result.already_revoked is False
    with sqlite3.connect(db) as connection:
        metadata = json.loads(connection.execute("SELECT metadata_json FROM audit_events").fetchone()[0])
    assert metadata == {"already_revoked": False, "admin": True}


def test_revoke_audit_insert_failure會rollbackcredential_update(tmp_path):
    db = tmp_path / "revoke-rollback.sqlite3"
    _建立端點資料庫(db)
    _建立撤銷測試憑證(db, "ep1", "owner-1", "revoke-a")
    _建立撤銷測試憑證(db, "ep1", "owner-1", "revoke-b")
    service = SQLite憑證撤銷服務(
        db, clock=lambda: 150.0, event_id_factory=lambda: "audit-duplicate",
    )
    service.撤銷("ep1", "revoke-a", WebOwnerPrincipal("owner-1"), "request-a")
    with pytest.raises(憑證撤銷錯誤, match="憑證撤銷失敗"):
        service.撤銷("ep1", "revoke-b", WebOwnerPrincipal("owner-1"), "request-b")
    with sqlite3.connect(db) as connection:
        assert connection.execute(
            "SELECT revoked_at,revision FROM endpoint_credentials WHERE id='revoke-b'"
        ).fetchone() == (None, 0)
        assert connection.execute("SELECT COUNT(*) FROM audit_events").fetchone() == (1,)


def test_revoke_missing_DB不建立空檔(tmp_path):
    missing = tmp_path / "missing-revoke" / "published.sqlite3"
    service = SQLite憑證撤銷服務(missing, clock=lambda: 150.0)
    with pytest.raises(憑證撤銷錯誤, match="憑證撤銷失敗"):
        service.撤銷("ep1", "cred-1", WebOwnerPrincipal("owner-1"), "request-missing")
    assert not missing.exists() and not missing.parent.exists()
