"""發布介面遷移 SQL splitter 與 authorizer primitive 測試。"""

import concurrent.futures
import sqlite3
import threading
from pathlib import Path

import pytest

from 繁中代理.發布介面.遷移執行器 import 拆分遷移SQL, 執行遷移, 遷移SQL錯誤, 遷移執行錯誤, 遷移授權狀態, 遷移項目, 驗證遷移SQL


秘密標記 = "唯一SQL_SECRET_MARKER_不可外洩"
PRODUCTION_MODULE = "繁中代理.發布介面.遷移執行器"
核心遷移檔 = Path("繁中代理/發布介面/遷移/0001_建立發布端點核心.sql")
憑證稽核遷移檔 = Path("繁中代理/發布介面/遷移/0002_建立憑證與稽核.sql")
呼叫事件遷移檔 = Path("繁中代理/發布介面/遷移/0003_建立呼叫事件與工具紀錄.sql")
快照欄位 = (
    "original_requirement_text",
    "system_prompt",
    "allowed_skills_json",
    "allowed_tools_json",
    "tool_schema_snapshot_json",
    "tool_runtime_revision",
    "model_config_snapshot_json",
    "retry_policy_json",
    "skill_bundle_manifest_json",
    "response_schema_json",
)


def _錯誤不含SQL標記(錯誤):
    assert 秘密標記 not in str(錯誤)
    assert 秘密標記 not in repr(錯誤)
    assert 錯誤.__cause__ is None
    assert 錯誤.__context__ is None


def _production_traceback_locals不含SQL標記(錯誤):
    traceback = 錯誤.__traceback__
    checked = False
    while traceback is not None:
        frame = traceback.tb_frame
        if frame.f_globals.get("__name__") == PRODUCTION_MODULE:
            checked = True
            for 名稱, 值 in frame.f_locals.items():
                assert 秘密標記 not in repr(值), 名稱
        traceback = traceback.tb_next
    assert checked


def _assert_sanitized_exception(錯誤):
    _錯誤不含SQL標記(錯誤)
    _production_traceback_locals不含SQL標記(錯誤)


def _查詢(db, sql):
    with sqlite3.connect(db) as 連線:
        return 連線.execute(sql).fetchall()


def _套用核心遷移(db):
    sql = 核心遷移檔.read_text(encoding="utf-8")
    assert all(禁用 not in sql.upper() for 禁用 in ("COMMIT", "PRAGMA", "ATTACH"))
    項目 = 遷移項目(1, 核心遷移檔.name, sql)
    assert 執行遷移(db, [項目]) == (1,)
    assert 執行遷移(db, [項目]) == ()
    assert _查詢(db, "SELECT version,name FROM published_api_schema_migrations") == [(1, 核心遷移檔.name)]


def _套用憑證稽核遷移(db):
    遷移 = []
    for 版本, 檔案 in ((1, 核心遷移檔), (2, 憑證稽核遷移檔)):
        sql = 檔案.read_text(encoding="utf-8")
        assert all(禁用 not in sql.upper() for 禁用 in ("COMMIT", "PRAGMA", "ATTACH"))
        遷移.append(遷移項目(版本, 檔案.name, sql))
    assert 執行遷移(db, 遷移) == (1, 2)
    assert 執行遷移(db, 遷移) == ()
    assert _查詢(db, "SELECT version,name FROM published_api_schema_migrations ORDER BY version") == [(1, 核心遷移檔.name), (2, 憑證稽核遷移檔.name)]


def _套用呼叫事件遷移(db):
    遷移 = []
    for 版本, 檔案 in ((1, 核心遷移檔), (2, 憑證稽核遷移檔), (3, 呼叫事件遷移檔)):
        sql = 檔案.read_text(encoding="utf-8")
        assert all(禁用 not in sql.upper() for 禁用 in ("COMMIT", "PRAGMA", "ATTACH"))
        遷移.append(遷移項目(版本, 檔案.name, sql))
    assert 執行遷移(db, 遷移) == (1, 2, 3)
    assert 執行遷移(db, 遷移) == ()
    assert _查詢(db, "SELECT version,name FROM published_api_schema_migrations ORDER BY version") == [
        (1, 核心遷移檔.name),
        (2, 憑證稽核遷移檔.name),
        (3, 呼叫事件遷移檔.name),
    ]


def _欄位(db, table):
    return [row[1] for row in _查詢(db, f"SELECT * FROM pragma_table_info('{table}')")]


def _寫入服務帳號端點版本(db):
    with sqlite3.connect(db) as 連線:
        連線.execute("PRAGMA foreign_keys=ON")
        連線.executemany("INSERT INTO service_accounts(id,created_at) VALUES (?,?)", [("sa1", 1.0), ("sa2", 1.0)])
        連線.executemany(
            "INSERT INTO published_endpoints(id,owner_user_id,service_account_id,slug,status,created_at,updated_at) VALUES (?,?,?,?,?,?,?)",
            [("ep1", "u1", "sa1", "one", "active", 1.0, 1.0), ("ep2", "u2", "sa2", "two", "disabled", 1.0, 1.0)],
        )
        values = {
            "id": "v1",
            "endpoint_id": "ep1",
            "version_number": 1,
            "input_schema_json": None,
            "schema_changed": 0,
            "created_by_user_id": "u1",
            "created_at": 2.0,
            **{欄: f"{欄}:snapshot" for 欄 in 快照欄位},
        }
        cols = tuple(values)
        連線.execute(
            f"INSERT INTO published_endpoint_versions({','.join(cols)}) VALUES ({','.join('?' for _ in cols)})",
            tuple(values.values()),
        )
        values["id"] = "v2"
        values["version_number"] = 2
        values["schema_changed"] = 1
        連線.execute(
            f"INSERT INTO published_endpoint_versions({','.join(cols)}) VALUES ({','.join('?' for _ in cols)})",
            tuple(values.values()),
        )


def _有效憑證(**覆寫):
    值 = {
        "id": 覆寫.pop("id", "cred"),
        "endpoint_id": "ep1",
        "name": "prod key",
        "purpose": "server to server",
        "secret_ciphertext": b"cipher",
        "encryption_key_id": "kms-key",
        "verification_hash": 覆寫.pop("verification_hash", b"vh"),
        "key_prefix": "pk_live",
        "key_last4": "1234",
        "expires_at": 10,
        "last_used_at": None,
        "revoked_at": None,
        "inactive_disabled_at": None,
        "ip_allowlist_json": "[]",
        "rate_limit_requests": 5,
        "rate_limit_window_seconds": 60,
        "created_by_user_id": "u1",
        "created_at": 2,
    }
    值.update(覆寫)
    return 值


def _新增憑證(連線, **覆寫):
    值 = _有效憑證(**覆寫)
    cols = tuple(值)
    連線.execute(
        f"INSERT INTO endpoint_credentials({','.join(cols)}) VALUES ({','.join('?' for _ in cols)})",
        tuple(值.values()),
    )


def _寫入呼叫事件fixture(db):
    _寫入服務帳號端點版本(db)
    with sqlite3.connect(db) as 連線:
        連線.execute("PRAGMA foreign_keys=ON")
        values = {
            "id": "v3",
            "endpoint_id": "ep2",
            "version_number": 1,
            "input_schema_json": None,
            "schema_changed": 0,
            "created_by_user_id": "u2",
            "created_at": 2.0,
            **{欄: f"{欄}:snapshot" for 欄 in 快照欄位},
        }
        cols = tuple(values)
        連線.execute(f"INSERT INTO published_endpoint_versions({','.join(cols)}) VALUES ({','.join('?' for _ in cols)})", tuple(values.values()))
        _新增憑證(連線, id="c1", endpoint_id="ep1", verification_hash=b"c1")
        _新增憑證(連線, id="c2", endpoint_id="ep2", verification_hash=b"c2")


def _新增呼叫(連線, **覆寫):
    值 = {
        "id": 覆寫.pop("id", "inv1"),
        "endpoint_id": "ep1",
        "endpoint_version_id": "v1",
        "credential_id": "c1",
        "request_id": 覆寫.pop("request_id", "req1"),
        "status": "succeeded",
        "input_json": '{"input":1}',
        "created_at": 3,
        "completed_at": 4,
    }
    值.update(覆寫)
    cols = tuple(值)
    連線.execute(f"INSERT INTO endpoint_invocations({','.join(cols)}) VALUES ({','.join('?' for _ in cols)})", tuple(值.values()))


def _新增執行事件(連線, **覆寫):
    值 = {"id": 覆寫.pop("id", "ev1"), "invocation_id": "inv1", "sequence_number": 1, "event_type": "start", "payload_json": '{"ok":true}', "created_at": 3}
    值.update(覆寫)
    cols = tuple(值)
    連線.execute(f"INSERT INTO run_events({','.join(cols)}) VALUES ({','.join('?' for _ in cols)})", tuple(值.values()))


def _新增工具呼叫(連線, **覆寫):
    值 = {
        "id": 覆寫.pop("id", "tc1"),
        "invocation_id": "inv1",
        "run_event_id": "ev1",
        "sequence_number": 1,
        "tool_name": "lookup",
        "arguments_json": '{"q":"x"}',
        "outcome": "success",
        "result_json": '{"answer":1}',
        "latency_ms": 1,
        "created_at": 3,
    }
    值.update(覆寫)
    cols = tuple(值)
    連線.execute(f"INSERT INTO endpoint_tool_calls({','.join(cols)}) VALUES ({','.join('?' for _ in cols)})", tuple(值.values()))


def test_呼叫事件遷移建立表欄位索引且無明文欄(tmp_path):
    db = tmp_path / "invocation_schema.db"
    _套用呼叫事件遷移(db)
    _寫入呼叫事件fixture(db)

    assert {"endpoint_invocations", "run_events", "endpoint_tool_calls"} <= set(name for (name,) in _查詢(db, "SELECT name FROM sqlite_master WHERE type='table'"))
    assert {"metadata_sha256", "pricing_version", "usage_json", "error_json"} <= set(_欄位(db, "endpoint_invocations"))
    for table in ("endpoint_invocations", "run_events", "endpoint_tool_calls"):
        assert not any(禁 in 欄.lower() for 欄 in _欄位(db, table) for 禁 in ("raw_api_key", "plaintext", "password", "secret"))
    索引 = set(_查詢(db, "SELECT name FROM sqlite_master WHERE type='index' AND name NOT LIKE 'sqlite_autoindex_%'"))
    assert {
        ("uq_endpoint_credentials_id_endpoint",),
        ("idx_endpoint_invocations_endpoint_created",),
        ("idx_endpoint_invocations_status_created",),
        ("idx_endpoint_invocations_credential_created",),
        ("idx_endpoint_tool_calls_invocation_created",),
    } <= 索引
    assert ("idx_run_events_invocation_order",) not in 索引
    with sqlite3.connect(db) as 連線:
        連線.execute("PRAGMA foreign_keys=ON")
        _新增呼叫(連線)
        _新增執行事件(連線)
        _新增工具呼叫(連線)
        assert 連線.execute("PRAGMA foreign_key_check").fetchall() == []


def test_呼叫事件invocation核心約束與ownership(tmp_path):
    db = tmp_path / "invocation_constraints.db"
    _套用呼叫事件遷移(db)
    _寫入呼叫事件fixture(db)
    with sqlite3.connect(db) as 連線:
        連線.execute("PRAGMA foreign_keys=ON")
        _新增呼叫(連線, id="external", request_id="external", credential_id=None, session_id=None, message_id=None)
        _新增呼叫(連線, id="ok", request_id="ok", metadata_json='{"m":1}', output_json='[]', error_json='{"e":1}', usage_json='{"u":1}', metadata_size_bytes=0, latency_ms=0)
        for 覆寫 in (
            {"id": "cross_version", "request_id": "bad1", "endpoint_id": "ep2", "endpoint_version_id": "v1", "credential_id": "c2"},
            {"id": "cross_credential", "request_id": "bad2", "credential_id": "c2"},
            {"id": "dupe_request", "request_id": "ok"},
            {"id": "bad_status", "request_id": "bad3", "status": "paused"},
            {"id": "bad_input", "request_id": "bad4", "input_json": "{bad"},
            {"id": "bad_latency", "request_id": "bad5", "latency_ms": -1},
            {"id": "bad_completed", "request_id": "bad6", "completed_at": 2},
            {"id": "bad_size", "request_id": "bad7", "metadata_size_bytes": 1.5},
        ):
            with pytest.raises(sqlite3.IntegrityError):
                _新增呼叫(連線, **覆寫)
        assert 連線.execute("PRAGMA foreign_key_check").fetchall() == []


def test_呼叫事件run_events與tool_calls順序JSON與同invocation約束(tmp_path):
    db = tmp_path / "events_tools.db"
    _套用呼叫事件遷移(db)
    _寫入呼叫事件fixture(db)
    with sqlite3.connect(db) as 連線:
        連線.execute("PRAGMA foreign_keys=ON")
        _新增呼叫(連線, id="inv1", request_id="r1")
        _新增呼叫(連線, id="inv2", request_id="r2")
        _新增執行事件(連線, id="ev1", invocation_id="inv1", sequence_number=1)
        _新增執行事件(連線, id="ev2", invocation_id="inv2", sequence_number=1)
        _新增工具呼叫(連線, id="tc1", invocation_id="inv1", run_event_id="ev1", sequence_number=1)
        _新增工具呼叫(連線, id="tc2", invocation_id="inv1", run_event_id=None, sequence_number=2, outcome="error", result_json=None, error_json='{"message":"boom"}', retry_of_tool_call_id="tc1")
        for fn, 覆寫 in (
            (_新增執行事件, {"id": "bad_seq", "sequence_number": 0}),
            (_新增執行事件, {"id": "bad_payload", "payload_json": "[]"}),
            (_新增執行事件, {"id": "dupe_event_seq", "sequence_number": 1}),
            (_新增工具呼叫, {"id": "bad_event_owner", "run_event_id": "ev2", "sequence_number": 3}),
            (_新增工具呼叫, {"id": "bad_retry_owner", "retry_of_tool_call_id": "tc1", "invocation_id": "inv2", "run_event_id": "ev2"}),
            (_新增工具呼叫, {"id": "bad_success", "sequence_number": 4, "result_json": None}),
            (_新增工具呼叫, {"id": "bad_error", "sequence_number": 5, "outcome": "error", "error_json": None}),
            (_新增工具呼叫, {"id": "bad_args", "sequence_number": 6, "arguments_json": "[]"}),
            (_新增工具呼叫, {"id": "dupe_tool_seq", "sequence_number": 1}),
        ):
            with pytest.raises(sqlite3.IntegrityError):
                fn(連線, **覆寫)
        assert 連線.execute("PRAGMA foreign_key_check").fetchall() == []


def _新增稽核事件(連線, **覆寫):
    值 = {
        "id": 覆寫.pop("id", "audit"),
        "actor_type": "user",
        "actor_id": "u1",
        "action": "credential.view",
        "target_type": "endpoint_credential",
        "target_id": "cred",
        "endpoint_id": "ep1",
        "request_id": "req1",
        "metadata_json": '{"ok":true}',
        "created_at": 3,
    }
    值.update(覆寫)
    cols = tuple(值)
    連線.execute(f"INSERT INTO audit_events({','.join(cols)}) VALUES ({','.join('?' for _ in cols)})", tuple(值.values()))


def test_憑證稽核遷移建立表欄位索引且無明文或scope欄(tmp_path):
    db = tmp_path / "credential_audit.db"
    _套用憑證稽核遷移(db)
    _寫入服務帳號端點版本(db)

    assert {"endpoint_credentials", "audit_events"} <= set(name for (name,) in _查詢(db, "SELECT name FROM sqlite_master WHERE type='table'"))
    assert {"rate_limit_requests", "rate_limit_window_seconds"} <= set(_欄位(db, "published_endpoints"))
    for table in ("endpoint_credentials", "audit_events"):
        assert not any(禁 in 欄.lower() for 欄 in _欄位(db, table) for 禁 in ("raw_key", "plaintext", "password", "scope", "payload"))

    索引 = set(_查詢(db, "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name IN ('endpoint_credentials','audit_events')"))
    assert {("idx_endpoint_credentials_endpoint_list",), ("idx_audit_events_target_time",), ("idx_audit_events_endpoint_time",)} <= 索引
    with sqlite3.connect(db) as 連線:
        連線.execute("PRAGMA foreign_keys=ON")
        _新增憑證(連線, id="c1", verification_hash=b"one")
        _新增憑證(連線, id="c2", verification_hash=b"two")
        _新增憑證(連線, id="c3", endpoint_id="ep2", verification_hash=b"one")
        with pytest.raises(sqlite3.IntegrityError):
            _新增憑證(連線, id="c4", verification_hash=b"one")
        with pytest.raises(sqlite3.IntegrityError):
            _新增憑證(連線, id="missing", endpoint_id="missing", verification_hash=b"missing")
        assert 連線.execute("PRAGMA foreign_key_check").fetchall() == []


def test_憑證欄位約束與端點層rate_limit預設相容既有資料(tmp_path):
    db = tmp_path / "credential_constraints.db"
    _套用核心遷移(db)
    with sqlite3.connect(db) as 連線:
        連線.execute("PRAGMA foreign_keys=ON")
        _寫入服務帳號端點版本(db)
        連線.execute("UPDATE published_endpoints SET current_version_id='v1' WHERE id='ep1'")
    端點原列 = _查詢(db, "SELECT id,owner_user_id,service_account_id,slug,status,current_version_id,created_at,updated_at FROM published_endpoints ORDER BY id")
    版本原列 = _查詢(db, "SELECT * FROM published_endpoint_versions ORDER BY id")
    assert 執行遷移(db, [遷移項目(2, 憑證稽核遷移檔.name, 憑證稽核遷移檔.read_text(encoding="utf-8"))]) == (2,)
    assert _查詢(db, "SELECT id,owner_user_id,service_account_id,slug,status,current_version_id,created_at,updated_at FROM published_endpoints ORDER BY id") == 端點原列
    assert _查詢(db, "SELECT * FROM published_endpoint_versions ORDER BY id") == 版本原列
    assert _查詢(db, "SELECT current_version_id FROM published_endpoints WHERE id='ep1'") == [("v1",)]
    assert _查詢(db, "SELECT rate_limit_requests,rate_limit_window_seconds FROM published_endpoints ORDER BY id") == [(60, 60), (60, 60)]
    assert ("idx_published_endpoints_owner_status",) in _查詢(db, "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='published_endpoints'")
    assert {("published_endpoint_versions_no_update",), ("published_endpoint_versions_no_delete",), ("published_endpoints_rate_limit_positive_before_insert",), ("published_endpoints_rate_limit_positive_before_update",)} <= set(_查詢(db, "SELECT name FROM sqlite_master WHERE type='trigger'"))

    with sqlite3.connect(db) as 連線:
        連線.execute("PRAGMA foreign_keys=ON")
        _新增憑證(連線, id="ok")
        for i, 覆寫 in enumerate(
            [
                {"name": " "},
                {"name": "x" * 121},
                {"purpose": ""},
                {"purpose": "x" * 1001},
                {"secret_ciphertext": b""},
                {"encryption_key_id": " "},
                {"verification_hash": b""},
                {"key_prefix": ""},
                {"key_prefix": "x" * 33},
                {"key_last4": "123"},
                {"expires_at": -1},
                {"last_used_at": -1},
                {"ip_allowlist_json": "{}"},
                {"rate_limit_requests": 1.5},
                {"rate_limit_window_seconds": 0},
            ],
            start=1,
        ):
            with pytest.raises(sqlite3.IntegrityError):
                值 = {"id": f"bad{i}", "verification_hash": f"bad{i}".encode(), **覆寫}
                _新增憑證(連線, **值)
        with pytest.raises(sqlite3.IntegrityError):
            連線.execute("UPDATE published_endpoints SET rate_limit_requests=0 WHERE id='ep1'")
        with pytest.raises(sqlite3.IntegrityError):
            連線.execute("UPDATE published_endpoints SET rate_limit_window_seconds=1.2 WHERE id='ep1'")
        with pytest.raises(sqlite3.DatabaseError):
            連線.execute("UPDATE published_endpoint_versions SET system_prompt='changed' WHERE id='v1'")
        with pytest.raises(sqlite3.DatabaseError):
            連線.execute("DELETE FROM published_endpoint_versions WHERE id='v1'")
        連線.executemany("INSERT INTO service_accounts(id,created_at) VALUES (?,?)", [("sa3", 1.0), ("sa4", 1.0)])
        with pytest.raises(sqlite3.IntegrityError, match="published endpoint rate limits must be positive integers"):
            連線.execute("INSERT INTO published_endpoints(id,owner_user_id,service_account_id,slug,status,created_at,updated_at,rate_limit_requests) VALUES (?,?,?,?,?,?,?,?)", ("bad_zero", "u3", "sa3", "bad-zero", "active", 1.0, 1.0, 0))
        with pytest.raises(sqlite3.IntegrityError, match="published endpoint rate limits must be positive integers"):
            連線.execute("INSERT INTO published_endpoints(id,owner_user_id,service_account_id,slug,status,created_at,updated_at,rate_limit_window_seconds) VALUES (?,?,?,?,?,?,?,?)", ("bad_float", "u4", "sa4", "bad-float", "active", 1.0, 1.0, 1.5))
        assert 連線.execute("PRAGMA foreign_key_check").fetchall() == []


def test_稽核事件object_metadata_actor規則與append_only(tmp_path):
    db = tmp_path / "audit.db"
    _套用憑證稽核遷移(db)
    _寫入服務帳號端點版本(db)

    with sqlite3.connect(db) as 連線:
        連線.execute("PRAGMA foreign_keys=ON")
        _新增稽核事件(連線, id="a1")
        _新增稽核事件(連線, id="a2", actor_type="system", actor_id=None, endpoint_id=None, metadata_json='{"reason":"timer"}')
        assert 連線.execute("SELECT actor_type,actor_id,metadata_json FROM audit_events ORDER BY id").fetchall() == [
            ("user", "u1", '{"ok":true}'),
            ("system", None, '{"reason":"timer"}'),
        ]
        for 覆寫 in ({"id": "bad_actor", "actor_id": None}, {"id": "bad_meta", "metadata_json": "[]"}, {"id": "bad_time", "created_at": -1}):
            with pytest.raises(sqlite3.IntegrityError):
                _新增稽核事件(連線, **覆寫)
        with pytest.raises(sqlite3.DatabaseError):
            連線.execute("UPDATE audit_events SET action='changed' WHERE id='a1'")
        with pytest.raises(sqlite3.DatabaseError):
            連線.execute("DELETE FROM audit_events WHERE id='a1'")
        assert 連線.execute("PRAGMA foreign_key_check").fetchall() == []


def test_核心遷移建立表欄位索引且服務帳號不含認證秘密欄(tmp_path):
    db = tmp_path / "core.db"
    _套用核心遷移(db)

    assert {"service_accounts", "published_endpoints", "published_endpoint_versions"} <= set(
        name for (name,) in _查詢(db, "SELECT name FROM sqlite_master WHERE type='table'")
    )
    assert _欄位(db, "service_accounts") == ["id", "created_at", "disabled_at"]
    assert not any(禁 in 欄.lower() for 欄 in _欄位(db, "service_accounts") for 禁 in ("password", "session", "token", "secret"))
    assert "current_version_id" in _欄位(db, "published_endpoints")
    assert set(快照欄位) <= set(_欄位(db, "published_endpoint_versions"))
    索引 = _查詢(db, "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name IN ('published_endpoints','published_endpoint_versions')")
    assert ("idx_published_endpoints_owner_status",) in 索引
    assert ("idx_published_endpoint_versions_endpoint_order",) not in 索引


def test_核心遷移版本資料與約束及目前版本指標(tmp_path):
    db = tmp_path / "contracts.db"
    _套用核心遷移(db)
    _寫入服務帳號端點版本(db)

    assert _查詢(db, "SELECT tool_schema_snapshot_json,response_schema_json FROM published_endpoint_versions WHERE id='v1'") == [
        ("tool_schema_snapshot_json:snapshot", "response_schema_json:snapshot")
    ]
    with sqlite3.connect(db) as 連線:
        連線.execute("PRAGMA foreign_keys=ON")
        with pytest.raises(sqlite3.IntegrityError):
            連線.execute(
                "INSERT INTO published_endpoints(id,owner_user_id,service_account_id,slug,status,created_at,updated_at) VALUES (?,?,?,?,?,?,?)",
                ("ep3", "u3", "sa1", "three", "active", 1, 1),
            )
        with pytest.raises(sqlite3.IntegrityError):
            連線.execute(
                "INSERT INTO published_endpoint_versions(id,endpoint_id,version_number,schema_changed,created_by_user_id,created_at,original_requirement_text,system_prompt,allowed_skills_json,allowed_tools_json,tool_schema_snapshot_json,tool_runtime_revision,model_config_snapshot_json,retry_policy_json,skill_bundle_manifest_json,response_schema_json) SELECT 'dupe','ep1',1,0,'u1',3,original_requirement_text,system_prompt,allowed_skills_json,allowed_tools_json,tool_schema_snapshot_json,tool_runtime_revision,model_config_snapshot_json,retry_policy_json,skill_bundle_manifest_json,response_schema_json FROM published_endpoint_versions WHERE id='v1'"
            )
        with pytest.raises(sqlite3.IntegrityError, match="status IN"):
            連線.execute("UPDATE published_endpoints SET status='paused' WHERE id='ep1'")
        with pytest.raises(sqlite3.IntegrityError, match="schema_changed IN"):
            連線.execute(
                "INSERT INTO published_endpoint_versions(id,endpoint_id,version_number,schema_changed,created_by_user_id,created_at,original_requirement_text,system_prompt,allowed_skills_json,allowed_tools_json,tool_schema_snapshot_json,tool_runtime_revision,model_config_snapshot_json,retry_policy_json,skill_bundle_manifest_json,response_schema_json) SELECT 'badbool','ep1',3,2,'u1',3,original_requirement_text,system_prompt,allowed_skills_json,allowed_tools_json,tool_schema_snapshot_json,tool_runtime_revision,model_config_snapshot_json,retry_policy_json,skill_bundle_manifest_json,response_schema_json FROM published_endpoint_versions WHERE id='v1'"
            )
        with pytest.raises(sqlite3.IntegrityError, match="version_number <> 1 OR schema_changed = 0"):
            連線.execute(
                "INSERT INTO published_endpoint_versions(id,endpoint_id,version_number,schema_changed,created_by_user_id,created_at,original_requirement_text,system_prompt,allowed_skills_json,allowed_tools_json,tool_schema_snapshot_json,tool_runtime_revision,model_config_snapshot_json,retry_policy_json,skill_bundle_manifest_json,response_schema_json) SELECT 'badv1','ep2',1,1,'u2',3,original_requirement_text,system_prompt,allowed_skills_json,allowed_tools_json,tool_schema_snapshot_json,tool_runtime_revision,model_config_snapshot_json,retry_policy_json,skill_bundle_manifest_json,response_schema_json FROM published_endpoint_versions WHERE id='v1'"
            )
        with pytest.raises(sqlite3.IntegrityError, match="typeof\\(version_number\\) = 'integer'"):
            連線.execute(
                "INSERT INTO published_endpoint_versions(id,endpoint_id,version_number,schema_changed,created_by_user_id,created_at,original_requirement_text,system_prompt,allowed_skills_json,allowed_tools_json,tool_schema_snapshot_json,tool_runtime_revision,model_config_snapshot_json,retry_policy_json,skill_bundle_manifest_json,response_schema_json) SELECT 'badtype','ep1',1.5,0,'u1',3,original_requirement_text,system_prompt,allowed_skills_json,allowed_tools_json,tool_schema_snapshot_json,tool_runtime_revision,model_config_snapshot_json,retry_policy_json,skill_bundle_manifest_json,response_schema_json FROM published_endpoint_versions WHERE id='v1'"
            )
        連線.execute("UPDATE published_endpoints SET current_version_id='v2', status='archived', updated_at=3 WHERE id='ep1'")
        for ptr in ("v1", "missing"):
            with pytest.raises(sqlite3.IntegrityError):
                連線.execute("UPDATE published_endpoints SET current_version_id=? WHERE id='ep2'", (ptr,))
        assert 連線.execute("PRAGMA foreign_key_check").fetchall() == []


def test_核心遷移版本不可變但端點可更新且無secret快照欄(tmp_path):
    db = tmp_path / "immutable.db"
    _套用核心遷移(db)
    _寫入服務帳號端點版本(db)
    before = _查詢(db, "SELECT * FROM published_endpoint_versions WHERE id='v1'")

    with sqlite3.connect(db) as 連線:
        連線.execute("PRAGMA foreign_keys=ON")
        with pytest.raises(sqlite3.DatabaseError):
            連線.execute("UPDATE published_endpoint_versions SET system_prompt='changed' WHERE id='v1'")
        with pytest.raises(sqlite3.DatabaseError):
            連線.execute("DELETE FROM published_endpoint_versions WHERE id='v1'")
        連線.execute("UPDATE published_endpoints SET status='disabled', current_version_id='v1', updated_at=4 WHERE id='ep1'")

    assert _查詢(db, "SELECT * FROM published_endpoint_versions WHERE id='v1'") == before
    assert _查詢(db, "SELECT status,current_version_id FROM published_endpoints WHERE id='ep1'") == [("disabled", "v1")]
    assert not any("secret" in 欄.lower() for 欄 in _欄位(db, "published_endpoint_versions"))


def test_拆分遷移SQL切分基本多陳述並strip():
    assert 拆分遷移SQL("  CREATE TABLE a(id INTEGER); \n INSERT INTO a VALUES(1);  ") == (
        "CREATE TABLE a(id INTEGER);",
        "INSERT INTO a VALUES(1);",
    )


def test_拆分遷移SQL保留字串與註解內分號不誤切():
    腳本 = """
    INSERT INTO log VALUES('a;b', "c;d");
    -- comment ; stays with next statement
    INSERT INTO log VALUES(2 /* block ; comment */);
    """

    assert 拆分遷移SQL(腳本) == (
        """INSERT INTO log VALUES('a;b', "c;d");""",
        "-- comment ; stays with next statement\n    INSERT INTO log VALUES(2 /* block ; comment */);",
    )


def test_拆分遷移SQL不切開trigger_body內分號():
    腳本 = """
    CREATE TRIGGER trg AFTER INSERT ON a
    BEGIN
      INSERT INTO b VALUES(new.id);
      INSERT INTO c VALUES('x;y');
    END;
    CREATE INDEX idx_a_id ON a(id);
    """

    assert 拆分遷移SQL(腳本) == (
        "CREATE TRIGGER trg AFTER INSERT ON a\n    BEGIN\n      INSERT INTO b VALUES(new.id);\n      INSERT INTO c VALUES('x;y');\n    END;",
        "CREATE INDEX idx_a_id ON a(id);",
    )


def test_拆分遷移SQL允許尾端line與block_comment且空腳本回空tuple():
    assert 拆分遷移SQL("CREATE TABLE a(id); -- trailing ; comment\n/* tail ; */") == ("CREATE TABLE a(id);",)
    assert 拆分遷移SQL(" \n -- only ; comment\n /* block ; only */ \n") == ()


@pytest.mark.parametrize("腳本", ["SELECT 1", "CREATE TABLE a(id); /* unclosed"])
def test_拆分遷移SQL拒絕未完成尾端SQL_未關閉comment_非字串(腳本):
    with pytest.raises(遷移SQL錯誤):
        拆分遷移SQL(腳本)


def test_拆分遷移SQL非字串錯誤固定():
    with pytest.raises(遷移SQL錯誤) as 錯誤:
        拆分遷移SQL(b"SELECT 1;")

    assert str(錯誤.value) == "遷移 SQL 不符合契約"


@pytest.mark.parametrize(
    "腳本",
    [
        f"SELECT 1 /* {秘密標記}",
        f"SELECT '{秘密標記}'",
        {"sql": 秘密標記},
    ],
)
def test_拆分遷移SQL錯誤traceback_locals不保留raw腳本(腳本):
    with pytest.raises(遷移SQL錯誤) as 錯誤:
        拆分遷移SQL(腳本)

    _assert_sanitized_exception(錯誤.value)


@pytest.mark.parametrize("陳述", ["CREATE TABLE ;", "SELECT FROM;", "SELECT 'unterminated;"])
def test_驗證遷移SQL拒絕parser語法與不完整輸入(陳述):
    with pytest.raises(遷移SQL錯誤):
        驗證遷移SQL([陳述])


@pytest.mark.parametrize("陳述", ["SELECT * FROM later_table;", "SELECT missing_column FROM later_table;", "INSERT INTO later_table VALUES (?);"])
def test_驗證遷移SQL允許語意依賴稍後建立(陳述):
    驗證遷移SQL([陳述])


@pytest.mark.parametrize("陳述", ["BEGIN;", "END;", "COMMIT;", "ROLLBACK;", "SAVEPOINT s;", "RELEASE s;"])
def test_驗證遷移SQL拒絕交易與savepoint_opcode(陳述):
    with pytest.raises(遷移SQL錯誤):
        驗證遷移SQL([陳述])


@pytest.mark.parametrize("陳述", ["ATTACH 'x.db' AS x;", "DETACH x;", "PRAGMA foreign_keys = ON;"])
def test_驗證遷移SQL拒絕attach_detach_pragma(陳述):
    with pytest.raises(遷移SQL錯誤):
        驗證遷移SQL([陳述])


def test_驗證遷移SQL不因字串或註解中的禁用詞拒絕():
    驗證遷移SQL(
        [
            "SELECT 'BEGIN COMMIT SAVEPOINT PRAGMA ATTACH DETACH';",
            "-- ROLLBACK;\nSELECT 1;",
        ]
    )


def test_遷移授權狀態只在真opcode拒絕且記錄類型():
    狀態 = 遷移授權狀態()

    驗證遷移SQL(["SELECT 'PRAGMA';"])
    assert 狀態.拒絕類型 is None

    with pytest.raises(遷移SQL錯誤):
        驗證遷移SQL(["PRAGMA user_version;"], 授權狀態=狀態)

    assert 狀態.拒絕類型 == "PRAGMA"


def test_驗證遷移SQL重用授權狀態不被前次拒絕污染():
    狀態 = 遷移授權狀態()

    with pytest.raises(遷移SQL錯誤):
        驗證遷移SQL(["PRAGMA user_version;"], 授權狀態=狀態)

    驗證遷移SQL(["SELECT * FROM later_table;"], 授權狀態=狀態)
    assert 狀態.拒絕類型 is None


@pytest.mark.parametrize(
    "陳述",
    [
        f"SELECT FROM {秘密標記};",
        f"PRAGMA {秘密標記};",
    ],
)
def test_驗證遷移SQL錯誤不含整段SQL且錯誤路徑可重複使用(陳述):
    with pytest.raises(遷移SQL錯誤) as 錯誤:
        驗證遷移SQL([陳述])

    _assert_sanitized_exception(錯誤.value)
    驗證遷移SQL(["SELECT 1;"])


def test_驗證遷移SQL非字串陳述traceback_locals不保留raw_sql():
    陳述 = {"sql": 秘密標記}
    with pytest.raises(遷移SQL錯誤) as 錯誤:
        驗證遷移SQL([陳述])

    _assert_sanitized_exception(錯誤.value)
    驗證遷移SQL(["SELECT 1;"])


def test_執行遷移依版本排序_ledger_fields_回傳_applied_且同名重跑先skip(tmp_path):
    db = tmp_path / "app.db"
    清單 = [
        遷移項目(2, "second", "INSERT INTO t VALUES(2);"),
        遷移項目(1, "first", "CREATE TABLE t(id INTEGER PRIMARY KEY); INSERT INTO t VALUES(1);"),
    ]

    assert 執行遷移(db, 清單) == (1, 2)
    assert _查詢(db, "SELECT id FROM t ORDER BY id") == [(1,), (2,)]
    ledger = _查詢(db, "SELECT version,name,typeof(applied_at) FROM published_api_schema_migrations ORDER BY version")
    assert ledger == [(1, "first", "real"), (2, "second", "real")]
    assert 執行遷移(db, [遷移項目(1, "first", f"CREATE TABLE broken_{秘密標記}(")]) == ()


@pytest.mark.parametrize(
    "清單",
    [
        None,
        {"sql": 秘密標記},
        f"not a list {秘密標記}",
        秘密標記.encode(),
        [None],
        [{"sql": 秘密標記}],
        [object()],
        [遷移項目(1, "a", "CREATE TABLE a(id);"), 遷移項目(1, "b", "CREATE TABLE b(id);")],
        [遷移項目(0, "a", "CREATE TABLE a(id);")],
        [遷移項目(True, "a", "CREATE TABLE a(id);")],
        [遷移項目(1, "  ", "CREATE TABLE a(id);")],
        [遷移項目(1, "a", {"sql": 秘密標記})],
    ],
)
def test_執行遷移拒絕不合法輸入且不套用任何schema(tmp_path, 清單):
    db = tmp_path / "bad.db"

    with pytest.raises(遷移執行錯誤) as 錯誤:
        執行遷移(db, 清單)

    assert str(錯誤.value) == "遷移項目不符合契約"
    _assert_sanitized_exception(錯誤.value)
    assert not db.exists() or _查詢(db, "SELECT name FROM sqlite_master WHERE type='table'") == []


def test_執行遷移一般sqlite錯誤整筆rollback且保留原錯誤型別(tmp_path):
    db = tmp_path / "rollback.db"
    sql = f"CREATE TABLE t(id INTEGER PRIMARY KEY); INSERT INTO missing VALUES(1); /* {秘密標記} */"

    with pytest.raises(sqlite3.OperationalError) as 錯誤:
        執行遷移(db, [遷移項目(1, "bad", sql)])

    _assert_sanitized_exception(錯誤.value)
    assert _查詢(db, "SELECT name FROM sqlite_master WHERE type='table'") == []


@pytest.mark.parametrize(
    "sql",
    [
        f"CREATE TABLE broken_{秘密標記}(;",
        f"SELECT 1; /* {秘密標記}",
    ],
)
def test_執行遷移_parser與splitter錯誤traceback_locals不保留raw_sql(tmp_path, sql):
    db = tmp_path / "parser.db"

    with pytest.raises(遷移SQL錯誤) as 錯誤:
        執行遷移(db, [遷移項目(1, "bad", sql)])

    _assert_sanitized_exception(錯誤.value)


@pytest.mark.parametrize("陳述", ["COMMIT;", "END;", "ROLLBACK;", "BEGIN;", "SAVEPOINT s;", "RELEASE s;"])
def test_執行遷移交易控制由authorizer固定錯誤且無schema_ledger殘留(tmp_path, 陳述):
    db = tmp_path / "forbidden.db"

    with pytest.raises(遷移執行錯誤) as 錯誤:
        執行遷移(db, [遷移項目(1, "bad", f"CREATE TABLE a(id); {陳述} SELECT '{秘密標記}';")])

    assert str(錯誤.value) == "遷移 SQL 包含禁止操作"
    _assert_sanitized_exception(錯誤.value)
    assert _查詢(db, "SELECT name FROM sqlite_master WHERE type='table'") == []


def test_執行遷移外鍵違反rollback且成功後新連線foreign_key_check_empty(tmp_path):
    db = tmp_path / "fk.db"
    建表 = "CREATE TABLE p(id INTEGER PRIMARY KEY); CREATE TABLE c(pid INTEGER REFERENCES p(id));"
    assert 執行遷移(db, [遷移項目(1, "schema", 建表)]) == (1,)

    with pytest.raises(sqlite3.IntegrityError):
        執行遷移(db, [遷移項目(2, "bad_fk", "INSERT INTO c VALUES(9);")])

    assert _查詢(db, "SELECT * FROM c") == []
    assert _查詢(db, "SELECT version FROM published_api_schema_migrations ORDER BY version") == [(1,)]
    assert 執行遷移(db, [遷移項目(2, "ok_fk", "INSERT INTO p VALUES(9); INSERT INTO c VALUES(9);")]) == (2,)
    assert _查詢(db, "PRAGMA foreign_key_check") == []


def test_執行遷移同版本不同名稱rollback且診斷不含SQL(tmp_path):
    db = tmp_path / "rename.db"
    執行遷移(db, [遷移項目(1, "old", "CREATE TABLE t(id);")])

    with pytest.raises(遷移執行錯誤) as 錯誤:
        執行遷移(db, [遷移項目(1, "new", f"CREATE TABLE leak_{秘密標記}(id);")])

    assert "1" in str(錯誤.value) and "old" in str(錯誤.value) and "new" in str(錯誤.value)
    _assert_sanitized_exception(錯誤.value)
    assert _查詢(db, "SELECT name FROM sqlite_master WHERE type='table' AND name='t'") == [("t",)]


def test_執行遷移同版本多thread只套用一次且不hang(tmp_path):
    db = tmp_path / "race.db"
    barrier = threading.Barrier(6)
    項目 = 遷移項目(1, "race", "CREATE TABLE t(id INTEGER PRIMARY KEY); INSERT INTO t VALUES(1);")

    def run_one():
        barrier.wait(timeout=5)
        return 執行遷移(db, [項目])

    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
        futures = [pool.submit(run_one) for _ in range(6)]
        results = [f.result(timeout=10) for f in futures]

    assert sorted(results) == [(), (), (), (), (), (1,)]
    assert _查詢(db, "SELECT COUNT(*) FROM t") == [(1,)]
    assert _查詢(db, "SELECT COUNT(*) FROM published_api_schema_migrations WHERE version=1") == [(1,)]


def test_執行遷移authorizer_cleanup後續正常migration可成功(tmp_path):
    db = tmp_path / "cleanup.db"
    with pytest.raises(遷移執行錯誤):
        執行遷移(db, [遷移項目(1, "bad", "SAVEPOINT s;")])

    assert 執行遷移(db, [遷移項目(1, "ok", "CREATE TABLE ok(id);")]) == (1,)
