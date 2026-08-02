"""PUB P04 endpoint、v1 與 prepared credential 原子發布。"""

import json
import os
import sqlite3
import threading
from dataclasses import replace

import pytest

from 繁中代理.發布介面 import 授權工具, 授權技能, 規劃權限快照
from 繁中代理.發布介面.資料庫 import 初始化發布介面資料庫
from 繁中代理.發布介面.規劃.綱要 import 規劃服務
from 繁中代理.發布介面.規劃.權限協調 import 權限協調器
from 繁中代理.發布介面.規劃 import 端點發布 as 發布模組
from 繁中代理.發布介面.規劃.端點發布 import (
    SQLite端點發布服務,
    已準備初始憑證,
    發布版本快照,
    端點發布結果,
    端點發布錯誤,
    端點發布輸入錯誤,
)


def _版本快照(**覆寫):
    """建立最小合法 v1 快照。"""
    值 = {
        "original_requirement_text": "回答問題",
        "system_prompt": "請精確回答",
        "allowed_skills": ["skill.one"],
        "allowed_tools": ["tool.one"],
        "tool_schema_snapshot": {"tool.one": {"type": "object"}},
        "tool_runtime_revision": "runtime-1",
        "model_config_snapshot": {"model": "test-model", "temperature": 0},
        "retry_policy": {"max_attempts": 1},
        "skill_bundle_manifest": {"sha256": "a" * 64},
        "input_schema": None,
        "response_schema": {"type": "string"},
        "created_by_user_id": "owner",
    }
    值.update(覆寫)
    return 發布版本快照(**值)


def test_版本快照建立單次detached精確JSON樹且repr不含內容():
    """呼叫端後續修改不得改變 prepared snapshot。"""
    模型 = {"model": "test-model", "nested": [1]}
    快照 = _版本快照(model_config_snapshot=模型)
    模型["nested"].append(2)

    assert 快照.model_config_snapshot == {"model": "test-model", "nested": [1]}
    assert "test-model" not in repr(快照)
    assert 快照.__slots__ and not hasattr(快照, "__dict__")


@pytest.mark.parametrize(
    "覆寫",
    [
        {"allowed_skills": ("skill.one",)},
        {"model_config_snapshot": {"provider_token": "RAW-MARKER"}},

        {"retry_policy": {"value": float("inf")}},
    ],
)
def test_版本快照拒絕subclass非有限與任何raw_secret欄位(覆寫):
    """所有 hostile snapshot 都以固定無鏈結錯誤拒絕。"""
    with pytest.raises(端點發布輸入錯誤, match="^端點發布輸入無效$") as 錯誤:
        _版本快照(**覆寫)
    assert 錯誤.value.__cause__ is None and 錯誤.value.__context__ is None


def test_JSON_Schema可描述api_key但模型設定不可攜帶同名憑證():
    """秘密鍵名只在實際 credential-bearing config context 禁止。"""
    結構 = {"type": "object", "properties": {"api_key": {"type": "string"}}}
    assert _版本快照(tool_schema_snapshot={"tool.one": 結構}, input_schema=結構, response_schema=結構)
    with pytest.raises(端點發布輸入錯誤, match="^端點發布輸入無效$"):
        _版本快照(model_config_snapshot={"api_key": "RAW-MARKER"})


def _已準備憑證(**覆寫):
    """建立不含 plaintext 的 prepared credential。"""
    值 = {
        "name": "初始憑證", "purpose": "呼叫端點",
        "key_version": 1, "key_nonce": b"n" * 12, "key_ciphertext": b"c" * 62,
        "key_hash": "a" * 64, "key_prefix": "pub_", "key_last4": "1234",
        "expires_at": 999.0, "ip_allowlist": ["203.0.113.1"],
        "rate_limit_requests": 30,
        "created_by_user_id": "owner",
    }
    值.update(覆寫)
    return 已準備初始憑證(**值)


def test_prepared憑證無raw_key欄位且repr與結果只揭露識別碼():
    """P04 的結構與公開結果不得形成 plaintext 傳遞面。"""
    憑證 = _已準備憑證()
    結果 = 端點發布結果("endpoint-1", "version-1", "credential-1", "account-1")

    assert "raw_key" not in 憑證.__slots__
    assert "cccc" not in repr(憑證) and "aaaa" not in repr(憑證)
    assert 結果.version_number == 1 and 結果.status == "active"
    assert not hasattr(結果, "secret_ciphertext") and not hasattr(結果, "key_prefix")


@pytest.mark.parametrize(
    "覆寫",
    [
        {"key_nonce": bytearray(b"n" * 12)}, {"key_ciphertext": b""}, {"key_hash": "g" * 64},
        {"expires_at": float("inf")}, {"key_last4": "123"},
        {"rate_limit_requests": True}, {"ip_allowlist": ("203.0.113.1",)},
    ],
)
def test_prepared憑證exact驗證所有敏感與生命週期欄位(覆寫):
    """P06 以前只接受已準備且 exact 的 encrypted material。"""
    with pytest.raises(端點發布輸入錯誤, match="^端點發布輸入無效$"):
        _已準備憑證(**覆寫)


def _已確認草稿(*, owner="owner", slug="customer-support", 現在=10):
    """建立含 P03 confirmation 的 detached draft。"""
    服務 = 規劃服務(存續秒數=100, 識別碼產生器=lambda: "draft-p04")
    服務.建立草稿(owner, "回答問題", {"system_prompt": "請精確回答"}, 現在=1)
    服務.確認發布值(
        owner, "draft-p04", slug=slug, response_schema={"type": "string"}, docs="文件",
        endpoint_limit=60, credential_limit=30, 現在=2,
    )
    return 服務.讀取已確認草稿(owner, "draft-p04", 現在=現在)


def _已確認授權草稿(*, owner="owner", tools=True):
    """經既有 FND DTO/協調器建立 P02 摘要，再走 P03 確認。"""
    class 查詢器:
        def 查詢規劃權限(self, _owner, /):
            工具 = (授權工具("tool.one", "tool-r1"), 授權工具("tool.two", "tool-r2")) if tools else ()
            技能 = (授權技能("skill.one", "摘要一", "a" * 64), 授權技能("skill.two", "摘要二", "b" * 64))
            return 規劃權限快照("perm-r1", 技能, 工具)
    服務 = 規劃服務(存續秒數=100, 識別碼產生器=lambda: "draft-authorized")
    工具名稱 = ("tool.one", "tool.two") if tools else ()
    服務.建立授權草稿(權限協調器(查詢器()), owner, "回答問題", {"system_prompt": "請精確回答"}, ("skill.one", "skill.two"), 工具名稱, 現在=1)
    服務.確認發布值(owner, "draft-authorized", slug="authorized", response_schema={"type": "string"}, docs="文件", endpoint_limit=60, credential_limit=30, 現在=2)
    return 服務.讀取已確認草稿(owner, "draft-authorized", 現在=10)


def _授權版本快照(*, tools=True, **覆寫):
    值 = {
        "allowed_skills": ["skill.one", "skill.two"],
        "allowed_tools": ["tool.one", "tool.two"] if tools else [],
        "skill_bundle_manifest": {"permission_revision": "perm-r1", "skills": [{"name": "skill.one", "content_sha256_reference": "a" * 64}, {"name": "skill.two", "content_sha256_reference": "b" * 64}], "p05": "保留"},
        "tool_schema_snapshot": {"tool.two": {"revision": "tool-r2", "type": "object"}, "tool.one": {"revision": "tool-r1", "type": "object"}} if tools else {},
    }
    值.update(覆寫)
    return _版本快照(**值)


def _服務(path, *, ids=("endpoint-1", "version-1", "credential-1", "account-1"), connection_factory=sqlite3.connect, calls=None):
    """建立 deterministic P04 service 並可觀察 callback 次數。"""
    callbacks = [] if calls is None else calls
    factories = []
    for value in ids:
        def factory(value=value):
            callbacks.append(value)
            return value
        factories.append(factory)
    def clock():
        callbacks.append("clock")
        return 20.0
    return SQLite端點發布服務(path, *factories, clock, connection_factory)


@pytest.mark.parametrize("tools", [True, False])
def test_授權草稿發布保留精確能力修訂且接受語意相同key順序(tmp_path, tools):
    path = tmp_path / f"authorized-{tools}.db"
    初始化發布介面資料庫(path)
    snapshot = _授權版本快照(tools=tools)
    reversed_skills = []
    for item in snapshot.skill_bundle_manifest["skills"]:
        reversed_skills.append({"content_sha256_reference": item["content_sha256_reference"], "name": item["name"]})
    manifest = dict(snapshot.skill_bundle_manifest)
    manifest["skills"] = reversed_skills
    snapshot = replace(snapshot, skill_bundle_manifest=manifest)

    result = _服務(path).發布("owner", _已確認授權草稿(tools=tools), snapshot, _已準備憑證(), 10)

    assert result.version_id == "version-1"
    row = sqlite3.connect(path).execute(
        "SELECT allowed_skills_json,allowed_tools_json,skill_bundle_manifest_json,tool_schema_snapshot_json FROM published_endpoint_versions"
    ).fetchone()
    assert json.loads(row[0]) == ["skill.one", "skill.two"]
    assert json.loads(row[1]) == (["tool.one", "tool.two"] if tools else [])
    assert json.loads(row[2]) == manifest
    assert json.loads(row[3]) == snapshot.tool_schema_snapshot


def _漂移快照(case):
    skills = [{"name": "skill.one", "content_sha256_reference": "a" * 64}, {"name": "skill.two", "content_sha256_reference": "b" * 64}]
    tools = {"tool.one": {"revision": "tool-r1"}, "tool.two": {"revision": "tool-r2"}}
    manifest = {"permission_revision": "perm-r1", "skills": skills}
    overrides = {}
    if case == "allowed-skill-name": overrides["allowed_skills"] = ["skill.one", "skill.other"]
    elif case == "allowed-skill-order": overrides["allowed_skills"] = ["skill.two", "skill.one"]
    elif case == "allowed-tool-name": overrides["allowed_tools"] = ["tool.one", "tool.other"]
    elif case == "allowed-tool-order": overrides["allowed_tools"] = ["tool.two", "tool.one"]
    elif case == "manifest-missing-permission": manifest.pop("permission_revision")
    elif case == "manifest-wrong-permission": manifest["permission_revision"] = "perm-r2"
    elif case == "skills-missing": manifest["skills"] = skills[:1]
    elif case == "skills-extra": manifest["skills"] = skills + [{"name": "skill.three", "content_sha256_reference": "c" * 64}]
    elif case == "skills-order": manifest["skills"] = list(reversed(skills))
    elif case == "skill-extra-key": manifest["skills"][0] = {**skills[0], "extra": True}
    elif case == "skill-wrong-ref": manifest["skills"][0] = {**skills[0], "content_sha256_reference": "c" * 64}
    elif case == "skill-wrong-name": manifest["skills"][0] = {**skills[0], "name": "skill.other"}
    elif case == "tools-missing": tools.pop("tool.two")
    elif case == "tools-extra": tools["tool.three"] = {"revision": "tool-r3"}
    elif case == "tool-missing-revision": tools["tool.one"] = {"type": "object"}
    elif case == "tool-wrong-revision": tools["tool.one"] = {"revision": "tool-r9"}
    overrides.setdefault("skill_bundle_manifest", manifest)
    overrides.setdefault("tool_schema_snapshot", tools)
    return _授權版本快照(**overrides)


@pytest.mark.parametrize("case", [
    "allowed-skill-name", "allowed-skill-order", "allowed-tool-name", "allowed-tool-order",
    "manifest-missing-permission", "manifest-wrong-permission", "skills-missing", "skills-extra",
    "skills-order", "skill-extra-key", "skill-wrong-ref", "skill-wrong-name", "tools-missing",
    "tools-extra", "tool-missing-revision", "tool-wrong-revision",
])
def test_能力投影任何漂移皆零ID時鐘與open(tmp_path, case):
    calls = []
    opens = []
    with pytest.raises(端點發布輸入錯誤, match="^端點發布輸入無效$"):
        _服務(tmp_path / "absent.db", calls=calls, connection_factory=lambda *a, **k: opens.append(1)).發布(
            "owner", _已確認授權草稿(), _漂移快照(case), _已準備憑證(), 10,
        )
    assert calls == [] and opens == []


def test_成功原子建立完整圖形canonical快照與不可變v1(tmp_path):
    """四列、current pointer、NULL lifecycle 與 immutable trigger 同時成立。"""
    path = tmp_path / "published.db"
    初始化發布介面資料庫(path)
    calls = []
    result = _服務(path, calls=calls).發布("owner", _已確認草稿(), _版本快照(), _已準備憑證(), 10)

    assert result == 端點發布結果("endpoint-1", "version-1", "credential-1", "account-1")
    assert calls == ["endpoint-1", "version-1", "credential-1", "account-1", "clock"]
    connection = sqlite3.connect(path)
    endpoint = connection.execute("SELECT id,owner_user_id,service_account_id,slug,status,current_version_id,rate_limit_requests,rate_limit_window_seconds FROM published_endpoints").fetchone()
    version = connection.execute("SELECT version_number,allowed_skills_json,model_config_snapshot_json,response_schema_json,schema_changed,created_at FROM published_endpoint_versions").fetchone()
    credential = connection.execute("SELECT endpoint_id,key_version,key_nonce,key_ciphertext,key_hash,last_used_at,revoked_at,ip_allowlist_json,created_at,updated_at,revision FROM endpoint_credentials").fetchone()
    assert endpoint == ("endpoint-1", "owner", "account-1", "customer-support", "active", "version-1", 60, 60)
    assert version == (1, '["skill.one"]', '{"model":"test-model","temperature":0}', '{"type":"string"}', 0, 20.0)
    assert credential == ("endpoint-1", 1, b"n" * 12, b"c" * 62, "a" * 64, None, None, '["203.0.113.1"]', 20.0, 20.0, 0)
    with pytest.raises(sqlite3.DatabaseError, match="immutable"):
        connection.execute("UPDATE published_endpoint_versions SET system_prompt='changed'")


class _失敗連線(sqlite3.Connection):
    """在指定第 N 個圖形 statement 前失敗並記錄 close 次數。"""
    失敗序號 = 0
    close_calls = 0
    mutations = 0

    def execute(self, sql, parameters=()):
        if sql.startswith(("INSERT INTO service_accounts", "INSERT INTO published_endpoints", "INSERT INTO published_endpoint_versions", "UPDATE published_endpoints", "INSERT INTO endpoint_credentials")):
            type(self).mutations += 1
            if type(self).mutations == type(self).失敗序號:
                raise sqlite3.OperationalError("marker-sensitive-db-error")
        return super().execute(sql, parameters)

    def close(self):
        type(self).close_calls += 1
        return super().close()


@pytest.mark.parametrize("失敗序號", range(1, 6))
def test_每個statement失敗皆rollback四類圖形且close一次(tmp_path, 失敗序號):
    path = tmp_path / "statement.db"
    初始化發布介面資料庫(path)
    _失敗連線.失敗序號 = 失敗序號
    _失敗連線.mutations = _失敗連線.close_calls = 0
    def connect(*args, **kwargs):
        return sqlite3.connect(*args, **kwargs, factory=_失敗連線)

    with pytest.raises(端點發布錯誤, match="^端點發布失敗$") as error:
        _服務(path, connection_factory=connect).發布("owner", _已確認草稿(), _版本快照(), _已準備憑證(), 10)
    assert error.value.__cause__ is None and error.value.__context__ is None
    assert _失敗連線.close_calls == 1
    connection = sqlite3.connect(path)
    for table in ("service_accounts", "published_endpoints", "published_endpoint_versions", "endpoint_credentials"):
        assert connection.execute(f"SELECT count(*) FROM {table}").fetchone() == (0,)


def test_duplicate_slug與各種重複ID皆不留下第二張殘圖(tmp_path):
    """UNIQUE/FK 邊界失敗都保留第一張完整圖，第二張零殘留。"""
    path = tmp_path / "duplicates.db"
    初始化發布介面資料庫(path)
    _服務(path).發布("owner", _已確認草稿(), _版本快照(), _已準備憑證(), 10)
    cases = [
        ("same-slug", ("endpoint-2", "version-2", "credential-2", "account-2")),
        ("other-slug", ("endpoint-1", "version-2", "credential-2", "account-2")),
        ("other-slug", ("endpoint-2", "version-1", "credential-2", "account-2")),
        ("other-slug", ("endpoint-2", "version-2", "credential-1", "account-2")),
        ("other-slug", ("endpoint-2", "version-2", "credential-2", "account-1")),
    ]
    for slug, ids in cases:
        draft = _已確認草稿(slug="customer-support" if slug == "same-slug" else slug)
        with pytest.raises(端點發布錯誤):
            _服務(path, ids=ids).發布("owner", draft, _版本快照(), _已準備憑證(), 10)
        connection = sqlite3.connect(path)
        counts = [connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0] for table in ("service_accounts", "published_endpoints", "published_endpoint_versions", "endpoint_credentials")]
        assert counts == [1, 1, 1, 1]


def _未確認草稿(owner="owner"):
    服務 = 規劃服務(存續秒數=100, 識別碼產生器=lambda: "draft-unconfirmed")
    return 服務.建立草稿(owner, "回答問題", {"system_prompt": "請精確回答"}, 現在=1)


def test_preflight各種交叉不一致皆零callback與零open(tmp_path):
    """權限、期限、確認與完整 published-v1 投影漂移都在副作用前 fail closed。"""
    path = tmp_path / "never-open.db"
    calls = []
    opens = []
    def connect(*args, **kwargs):
        opens.append(1)
        return sqlite3.connect(*args, **kwargs)
    cases = [
        ("owner", _未確認草稿(), _版本快照(), _已準備憑證(), 10),
        ("foreign", _已確認草稿(), _版本快照(created_by_user_id="foreign"), _已準備憑證(created_by_user_id="foreign"), 10),
        ("owner", _已確認草稿(), _版本快照(), _已準備憑證(), 200),
        ("owner", _已確認草稿(), _版本快照(original_requirement_text="不同需求"), _已準備憑證(), 10),
        ("owner", _已確認草稿(), _版本快照(system_prompt="不同提示"), _已準備憑證(), 10),
        ("owner", _已確認草稿(), _版本快照(response_schema={"type": "number"}), _已準備憑證(), 10),
        ("owner", _已確認草稿(), _版本快照(created_by_user_id="other"), _已準備憑證(), 10),
        ("owner", _已確認草稿(), _版本快照(), _已準備憑證(created_by_user_id="other"), 10),
        ("owner", _已確認草稿(), _版本快照(), _已準備憑證(rate_limit_requests=31), 10),

    ]
    for args in cases:
        with pytest.raises(端點發布輸入錯誤, match="^端點發布輸入無效$") as error:
            _服務(path, calls=calls, connection_factory=connect).發布(*args)
        assert error.value.__cause__ is None and error.value.__context__ is None
    assert calls == [] and opens == [] and not path.exists()


def test_偽造草稿世代確認與狀態皆在open前拒絕(tmp_path):
    path = tmp_path / "forged.db"
    calls = []
    for 欄位, 值 in (("_世代", 99), ("狀態", "published")):
        draft = _已確認草稿()
        object.__setattr__(draft, 欄位, 值)
        with pytest.raises(端點發布輸入錯誤):
            _服務(path, calls=calls).發布("owner", draft, _版本快照(), _已準備憑證(), 10)
    draft = _已確認草稿()
    object.__setattr__(draft.發布確認, "草稿世代", 99)
    with pytest.raises(端點發布輸入錯誤):
        _服務(path, calls=calls).發布("owner", draft, _版本快照(), _已準備憑證(), 10)
    assert calls == [] and not path.exists()


@pytest.mark.parametrize("kind", [
    "missing", "symlink", "schema", "credential-schema", "ledger-gap",
    "ledger-extra", "ledger-rename", "ledger-duplicate",
])
def test_資料庫路徑與完整schema漂移拒絕且不建立不follow(tmp_path, kind):
    real = tmp_path / "real.db"
    path = real
    if kind != "missing":
        初始化發布介面資料庫(real)
    if kind == "symlink":
        path = tmp_path / "link.db"
        path.symlink_to(real)
    elif kind == "schema":
        connection = sqlite3.connect(real)
        connection.execute("DROP TRIGGER published_endpoint_versions_no_update")
        connection.commit()
        connection.close()
    elif kind == "credential-schema":
        connection = sqlite3.connect(real)
        connection.execute("DROP TRIGGER finite_endpoint_credentials_insert")
        connection.commit()
        connection.close()
    elif kind == "ledger-gap":
        connection = sqlite3.connect(real)
        connection.execute("DELETE FROM published_api_schema_migrations WHERE version=5")
        connection.commit()
        connection.close()
    elif kind == "ledger-extra":
        connection = sqlite3.connect(real)
        connection.execute("INSERT INTO published_api_schema_migrations VALUES(12,'0012_unknown.sql',0)")
        connection.commit()
        connection.close()
    elif kind == "ledger-rename":
        connection = sqlite3.connect(real)
        connection.execute("UPDATE published_api_schema_migrations SET name='0011_renamed.sql' WHERE version=11")
        connection.commit()
        connection.close()
    elif kind == "ledger-duplicate":
        connection = sqlite3.connect(real)
        connection.execute("ALTER TABLE published_api_schema_migrations RENAME TO migration_ledger_old")
        connection.execute("CREATE TABLE published_api_schema_migrations(version INTEGER,name TEXT,applied_at REAL)")
        connection.execute("INSERT INTO published_api_schema_migrations SELECT * FROM migration_ledger_old")
        connection.execute("INSERT INTO published_api_schema_migrations VALUES(11,'0011_重建空憑證為CRED結構.sql',0)")
        connection.commit()
        connection.close()
    with pytest.raises(端點發布錯誤, match="^端點發布失敗$") as error:
        _服務(path).發布("owner", _已確認草稿(), _版本快照(), _已準備憑證(), 10)
    assert error.value.__cause__ is None and error.value.__context__ is None
    if kind == "missing":
        assert not path.exists()
    if kind == "symlink":
        assert path.is_symlink()


def test_connect後路徑被替換即拒絕且不寫入替代檔(tmp_path):
    path = tmp_path / "target.db"
    replacement = tmp_path / "replacement.db"
    初始化發布介面資料庫(path)
    初始化發布介面資料庫(replacement)
    def connect(*args, **kwargs):
        connection = sqlite3.connect(*args, **kwargs)
        os.replace(replacement, path)
        return connection
    with pytest.raises(端點發布錯誤):
        _服務(path, connection_factory=connect).發布("owner", _已確認草稿(), _版本快照(), _已準備憑證(), 10)
    connection = sqlite3.connect(path)
    assert connection.execute("SELECT count(*) FROM published_endpoints").fetchone() == (0,)


class _CallbackError(BaseException):
    pass


@pytest.mark.parametrize("failure_index", range(5))
@pytest.mark.parametrize("error_type", [ValueError, _CallbackError])
def test_每個factory與clock失敗皆不open且固定fresh(tmp_path, failure_index, error_type):
    calls = []
    opens = []
    def callback(index, value):
        def run():
            calls.append(index)
            if index == failure_index:
                raise error_type("callback-marker")
            return value
        return run
    callbacks = [callback(i, value) for i, value in enumerate(("e", "v", "c", "a"))]
    clock = callback(4, 20.0)
    def connect(*args, **kwargs):
        opens.append(1)
        return sqlite3.connect(*args, **kwargs)
    service = SQLite端點發布服務(tmp_path / "absent.db", *callbacks, clock, connect)
    with pytest.raises(端點發布錯誤, match="^端點發布失敗$") as error:
        service.發布("owner", _已確認草稿(), _版本快照(), _已準備憑證(), 10)
    assert error.value.__cause__ is None and error.value.__context__ is None
    assert calls == list(range(failure_index + 1)) and opens == []


@pytest.mark.parametrize("control", [KeyboardInterrupt("K"), SystemExit("S"), GeneratorExit("G")])
def test_callback控制流保留exact身分與args且不open(tmp_path, control):
    opens = []
    def fail():
        raise control
    service = SQLite端點發布服務(tmp_path / "absent.db", fail, lambda: "v", lambda: "c", lambda: "a", lambda: 20.0, lambda *a, **k: opens.append(1))
    with pytest.raises(type(control)) as error:
        service.發布("owner", _已確認草稿(), _版本快照(), _已準備憑證(), 10)
    assert error.value is control and error.value.args == control.args and opens == []


class _狀態連線(sqlite3.Connection):
    """注入 transaction/cleanup failure 並記錄精確狀態轉移。"""
    fail_at = None
    fail_value = None
    rollback_calls = 0
    close_calls = 0
    began = False

    def execute(self, sql, parameters=()):
        if sql == "BEGIN IMMEDIATE":
            type(self).began = True
        if sql.startswith("SELECT version,name") or sql.startswith("SELECT type,name"):
            assert type(self).began
        if sql == "ROLLBACK":
            type(self).rollback_calls += 1
        if sql == type(self).fail_at:
            raise type(self).fail_value
        return super().execute(sql, parameters)

    def close(self):
        type(self).close_calls += 1
        if type(self).fail_at == "CLOSE":
            raise type(self).fail_value
        return super().close()


def _狀態服務(path, fail_at, fail_value):
    _狀態連線.fail_at = fail_at
    _狀態連線.fail_value = fail_value
    _狀態連線.rollback_calls = _狀態連線.close_calls = 0
    _狀態連線.began = False
    def connect(*args, **kwargs):
        return sqlite3.connect(*args, **kwargs, factory=_狀態連線)
    return _服務(path, connection_factory=connect)


def test_commit失敗rollback一次close一次且固定錯誤(tmp_path):
    path = tmp_path / "commit.db"
    初始化發布介面資料庫(path)
    with pytest.raises(端點發布錯誤) as error:
        _狀態服務(path, "COMMIT", sqlite3.OperationalError("commit-marker")).發布(
            "owner", _已確認草稿(), _版本快照(), _已準備憑證(), 10,
        )
    assert error.value.__cause__ is None and error.value.__context__ is None
    assert (_狀態連線.rollback_calls, _狀態連線.close_calls) == (1, 1)
    assert sqlite3.connect(path).execute("SELECT count(*) FROM published_endpoints").fetchone() == (0,)


def test_rollback與close普通失敗不取代primary且不重複cleanup(tmp_path):
    path = tmp_path / "cleanup.db"
    初始化發布介面資料庫(path)
    class CleanupConnection(_狀態連線):
        def execute(self, sql, parameters=()):
            if sql == "ROLLBACK":
                type(self).rollback_calls += 1
                raise RuntimeError("rollback-marker")
            if sql.startswith("INSERT INTO service_accounts"):
                raise RuntimeError("primary-marker")
            return super().execute(sql, parameters)
        def close(self):
            type(self).close_calls += 1
            raise RuntimeError("close-marker")
    CleanupConnection.rollback_calls = CleanupConnection.close_calls = 0
    CleanupConnection.began = False
    def connect(*args, **kwargs):
        return sqlite3.connect(*args, **kwargs, factory=CleanupConnection)
    with pytest.raises(端點發布錯誤) as error:
        _服務(path, connection_factory=connect).發布("owner", _已確認草稿(), _版本快照(), _已準備憑證(), 10)
    assert error.value.__cause__ is None and error.value.__context__ is None
    assert (CleanupConnection.rollback_calls, CleanupConnection.close_calls) == (1, 1)


@pytest.mark.parametrize("control", [KeyboardInterrupt("primary-K"), SystemExit("primary-S"), GeneratorExit("primary-G")])
def test_交易primary控制流優先於rollback與close失敗且身分不變(tmp_path, control):
    path = tmp_path / "control.db"
    初始化發布介面資料庫(path)
    class ControlConnection(_狀態連線):
        def execute(self, sql, parameters=()):
            if sql == "ROLLBACK":
                type(self).rollback_calls += 1
                raise RuntimeError("rollback-marker")
            if sql.startswith("INSERT INTO service_accounts"):
                raise control
            return super().execute(sql, parameters)
        def close(self):
            type(self).close_calls += 1
            raise RuntimeError("close-marker")
    ControlConnection.rollback_calls = ControlConnection.close_calls = 0
    ControlConnection.began = False
    def connect(*args, **kwargs):
        return sqlite3.connect(*args, **kwargs, factory=ControlConnection)
    with pytest.raises(type(control)) as error:
        _服務(path, connection_factory=connect).發布("owner", _已確認草稿(), _版本快照(), _已準備憑證(), 10)
    assert error.value is control and error.value.args == control.args
    assert (ControlConnection.rollback_calls, ControlConnection.close_calls) == (1, 1)


def test_commit後ordinary_close失敗仍回傳成功且資料durable(tmp_path):
    path = tmp_path / "close-ordinary.db"
    初始化發布介面資料庫(path)
    result = _狀態服務(path, "CLOSE", RuntimeError("close-marker")).發布(
        "owner", _已確認草稿(), _版本快照(), _已準備憑證(), 10,
    )
    assert result.endpoint_id == "endpoint-1" and _狀態連線.close_calls == 1
    assert sqlite3.connect(path).execute("SELECT count(*) FROM published_endpoints").fetchone() == (1,)


@pytest.mark.parametrize("control", [KeyboardInterrupt("close-K"), SystemExit("close-S"), GeneratorExit("close-G")])
def test_commit後close控制流傳播且已提交列保留(tmp_path, control):
    path = tmp_path / "close-control.db"
    初始化發布介面資料庫(path)
    with pytest.raises(type(control)) as error:
        _狀態服務(path, "CLOSE", control).發布("owner", _已確認草稿(), _版本快照(), _已準備憑證(), 10)
    assert error.value is control and _狀態連線.close_calls == 1
    assert sqlite3.connect(path).execute("SELECT count(*) FROM published_endpoints").fetchone() == (1,)


def test_schema查詢期間BEGIN_IMMEDIATE封鎖競爭DDL(tmp_path):
    path = tmp_path / "schema-lock.db"
    初始化發布介面資料庫(path)
    entered = threading.Event()
    release = threading.Event()
    class GatedConnection(sqlite3.Connection):
        def execute(self, sql, parameters=()):
            if sql.startswith("SELECT type,name"):
                entered.set()
                assert release.wait(5)
            return super().execute(sql, parameters)
    def connect(*args, **kwargs):
        return sqlite3.connect(*args, **kwargs, factory=GatedConnection)
    outcome = []
    def publish():
        try:
            outcome.append(_服務(path, connection_factory=connect).發布("owner", _已確認草稿(), _版本快照(), _已準備憑證(), 10))
        except BaseException as error:
            outcome.append(error)
    thread = threading.Thread(target=publish)
    thread.start()
    assert entered.wait(5)
    competitor = sqlite3.connect(path, timeout=0.05, isolation_level=None)
    try:
        with pytest.raises(sqlite3.OperationalError, match="locked"):
            competitor.execute("DROP TABLE endpoint_credentials")
    finally:
        competitor.close()
        release.set()
    thread.join(5)
    assert not thread.is_alive() and len(outcome) == 1 and type(outcome[0]) is 端點發布結果


def test_兩連線同slug競爭恰一winner且每表一列(tmp_path):
    path = tmp_path / "concurrent.db"
    初始化發布介面資料庫(path)
    barrier = threading.Barrier(2)
    outcomes = []
    def worker(index):
        barrier.wait()
        ids = tuple(f"{kind}-{index}" for kind in ("endpoint", "version", "credential", "account"))
        try:
            outcomes.append(_服務(path, ids=ids).發布("owner", _已確認草稿(), _版本快照(), _已準備憑證(), 10))
        except BaseException as error:
            outcomes.append(error)
    threads = [threading.Thread(target=worker, args=(index,)) for index in (1, 2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(10)
    assert all(not thread.is_alive() for thread in threads)
    assert sum(type(item) is 端點發布結果 for item in outcomes) == 1
    assert sum(type(item) is 端點發布錯誤 for item in outcomes) == 1
    connection = sqlite3.connect(path)
    counts = [connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0] for table in ("service_accounts", "published_endpoints", "published_endpoint_versions", "endpoint_credentials")]
    assert counts == [1, 1, 1, 1]


class _PostOpenCursor:
    def __init__(self, cursor, control):
        self.cursor = cursor
        self.control = control

    def fetchall(self):
        raise self.control


class _PostOpenConnection(sqlite3.Connection):
    stage = None
    control = None
    close_failure = None
    close_calls = 0

    def execute(self, sql, parameters=()):
        if sql == "PRAGMA database_list" and type(self).stage == "pragma":
            raise type(self).control
        cursor = super().execute(sql, parameters)
        if sql == "PRAGMA database_list" and type(self).stage == "fetchall":
            return _PostOpenCursor(cursor, type(self).control)
        return cursor

    def close(self):
        type(self).close_calls += 1
        if type(self).close_failure is not None:
            raise type(self).close_failure
        return super().close()


@pytest.mark.parametrize("stage,control", [
    ("lstat", KeyboardInterrupt("post-K")), ("pragma", KeyboardInterrupt("post-I")),
    ("fetchall", SystemExit("post-S")), ("stat", GeneratorExit("post-G")),
])
def test_post_open四個邊界控制流精確傳播close一次且零寫入(tmp_path, monkeypatch, stage, control):
    path = tmp_path / f"post-open-{stage}.db"
    初始化發布介面資料庫(path)
    original_lstat = type(path).lstat
    original_stat = 發布模組.os.stat
    _PostOpenConnection.stage = stage
    _PostOpenConnection.control = control
    _PostOpenConnection.close_failure = None
    _PostOpenConnection.close_calls = 0

    def connect(*args, **kwargs):
        connection = sqlite3.connect(*args, **kwargs, factory=_PostOpenConnection)
        if stage == "lstat":
            monkeypatch.setattr(type(path), "lstat", lambda self: (_ for _ in ()).throw(control))
        elif stage == "stat":
            class OSProxy:
                @staticmethod
                def stat(target):
                    raise control
            monkeypatch.setattr(發布模組, "os", OSProxy)
        return connection

    with pytest.raises(type(control)) as error:
        _服務(path, connection_factory=connect).發布("owner", _已確認草稿(), _版本快照(), _已準備憑證(), 10)
    monkeypatch.setattr(type(path), "lstat", original_lstat)
    monkeypatch.setattr(發布模組.os, "stat", original_stat, raising=False)
    assert error.value is control and error.value.args == control.args
    assert error.value.__cause__ is None and error.value.__context__ is None
    _assert_production_frames_clean(error.value, "post-", {"_驗證已開啟資料庫路徑", "發布"})
    assert _PostOpenConnection.close_calls == 1
    assert sqlite3.connect(path).execute("SELECT count(*) FROM published_endpoints").fetchone() == (0,)


@pytest.mark.parametrize("close_failure", [RuntimeError("cleanup"), KeyboardInterrupt("cleanup-K")])
def test_post_open_cleanup失敗或控制流不取代primary(tmp_path, close_failure):
    path = tmp_path / "post-open-cleanup.db"
    初始化發布介面資料庫(path)
    primary = SystemExit("primary-S")
    _PostOpenConnection.stage = "pragma"
    _PostOpenConnection.control = primary
    _PostOpenConnection.close_failure = close_failure
    _PostOpenConnection.close_calls = 0
    def connect(*args, **kwargs):
        return sqlite3.connect(*args, **kwargs, factory=_PostOpenConnection)
    with pytest.raises(SystemExit) as error:
        _服務(path, connection_factory=connect).發布("owner", _已確認草稿(), _版本快照(), _已準備憑證(), 10)
    assert error.value is primary and _PostOpenConnection.close_calls == 1


def _含標記(value, marker, visited):
    identity = id(value)
    if identity in visited:
        return False
    visited.add(identity)
    if type(value) is str:
        return marker in value
    if type(value) is bytes:
        return marker.encode() in value
    if type(value) is dict:
        return any(_含標記(item, marker, visited) for pair in dict.items(value) for item in pair)
    if type(value) in (list, tuple, set, frozenset):
        return any(_含標記(item, marker, visited) for item in value)
    if isinstance(value, BaseException):
        return (_含標記(value.args, marker, visited) or _含標記(value.__cause__, marker, visited)
                or _含標記(value.__context__, marker, visited) or _含標記(value.__dict__, marker, visited))
    defaults = getattr(value, "__defaults__", None)
    if type(defaults) is tuple and _含標記(defaults, marker, visited):
        return True
    closure = getattr(value, "__closure__", None)
    if type(closure) is tuple:
        for cell in closure:
            try:
                if _含標記(cell.cell_contents, marker, visited):
                    return True
            except ValueError:
                pass
    slots = getattr(type(value), "__slots__", ())
    if type(slots) is str:
        slots = (slots,)
    if type(slots) in (tuple, list):
        for name in slots:
            try:
                if _含標記(object.__getattribute__(value, name), marker, visited):
                    return True
            except (AttributeError, TypeError):
                pass
    try:
        attributes = object.__getattribute__(value, "__dict__")
    except (AttributeError, TypeError):
        attributes = None
    if type(attributes) is dict and attributes and _含標記(attributes, marker, visited):
        return True
    if type(value).__module__ == "pathlib":
        return marker in str(value)
    return False


def _assert_production_frames_clean(error, marker, required):
    names = set()
    traceback = error.__traceback__
    while traceback is not None:
        frame = traceback.tb_frame
        if frame.f_code.co_filename.endswith("端點發布.py"):
            names.add(frame.f_code.co_name)
            for value in tuple(frame.f_locals.values()):
                assert not _含標記(value, marker, set()), (frame.f_code.co_name, value)
        traceback = traceback.tb_next
    assert required <= names


def test_遞迴標記oracle涵蓋字串bytes容器slots例外圖與closure():
    marker = "TRACEP04"
    dto = _已準備憑證(key_ciphertext=(f"x-{marker}".encode() + b"x" * 62)[:62])
    cause = RuntimeError({"nested": [marker.encode()]})
    error = ValueError("outer")
    error.__cause__ = cause
    closure = (lambda captured=marker: captured)
    for value in (marker, marker.encode(), {"x": [marker]}, dto, error, closure):
        assert _含標記(value, marker, set())
    assert not _含標記({"safe": [b"clean"]}, marker, set())


@pytest.mark.parametrize("control", [KeyboardInterrupt("callback-K"), SystemExit("callback-S"), GeneratorExit("callback-G")])
def test_callback控制流所有production_frames遞迴清除closure與外層敏感物(control, tmp_path):
    marker = "TRACEP04"
    path = tmp_path / f"{marker}.db"
    credential = _已準備憑證(key_ciphertext=(marker.encode() + b"x" * 62)[:62])
    values = iter((f"{marker}-e", f"{marker}-v", f"{marker}-c", f"{marker}-a"))
    def factory():
        return next(values)
    def clock(captured=marker):
        assert captured == marker
        raise control
    service = SQLite端點發布服務(path, factory, factory, factory, factory, clock)
    with pytest.raises(type(control)) as caught:
        service.發布("owner", _已確認草稿(), _版本快照(), credential, 10)
    assert caught.value is control and caught.value.args == control.args
    _assert_production_frames_clean(caught.value, marker, {"_呼叫發布callbacks", "發布"})


@pytest.mark.parametrize("control", [KeyboardInterrupt("json-K"), SystemExit("json-S"), GeneratorExit("json-G")])
def test_JSON遞迴控制流清除每層copy重建與DTO_post_init(control, monkeypatch):
    marker = "TRACEP04"
    original = 發布模組.math.isfinite
    def fail_at_nested(value):
        if value == 1.25:
            raise control
        return original(value)
    monkeypatch.setattr(發布模組.math, "isfinite", fail_at_nested)
    with pytest.raises(type(control)) as caught:
        _版本快照(response_schema={"outer": [{"marker": marker, "value": 1.25}]})
    assert caught.value is control and caught.value.args == control.args
    _assert_production_frames_clean(
        caught.value, marker, {"_複製JSON節點", "_建立JSON副本", "_重建版本快照", "__post_init__"},
    )


class _SQLControlConnection(sqlite3.Connection):
    stage = None
    control = None
    close_calls = 0

    def execute(self, sql, parameters=()):
        if type(self).stage == "insert" and sql.startswith("INSERT INTO service_accounts"):
            raise type(self).control
        if type(self).stage == "commit" and sql == "COMMIT":
            raise type(self).control
        return super().execute(sql, parameters)

    def close(self):
        type(self).close_calls += 1
        return super().close()


@pytest.mark.parametrize("stage,control,required", [
    ("insert", KeyboardInterrupt("sql-K"), {"_執行一列"}),
    ("commit", SystemExit("sql-S"), set()),
    ("json", GeneratorExit("sql-G"), {"_正規JSON"}),
])
def test_SQL與外層控制流清除JSON密文hash路徑ID與交易locals(tmp_path, monkeypatch, stage, control, required):
    marker = "TRACEP04"
    path = tmp_path / f"{marker}-{stage}.db"
    初始化發布介面資料庫(path)
    snapshot = _版本快照(allowed_skills=[marker], retry_policy={"marker": marker})
    credential = _已準備憑證(key_ciphertext=(marker.encode() + b"x" * 62)[:62])
    _SQLControlConnection.stage = stage
    _SQLControlConnection.control = control
    _SQLControlConnection.close_calls = 0
    original_dumps = 發布模組.json.dumps
    opened = [False]
    class JSONProxy:
        @staticmethod
        def dumps(value, **kwargs):
            if stage == "json" and opened[0] and _含標記(value, marker, set()):
                raise control
            return original_dumps(value, **kwargs)
    monkeypatch.setattr(發布模組, "json", JSONProxy)
    def connect(*args, **kwargs):
        opened[0] = True
        return sqlite3.connect(*args, **kwargs, factory=_SQLControlConnection)
    service = _服務(path, ids=(f"{marker}-e", f"{marker}-v", f"{marker}-c", f"{marker}-a"), connection_factory=connect)
    with pytest.raises(type(control)) as caught:
        service.發布("owner", _已確認草稿(), snapshot, credential, 10)
    assert caught.value is control and caught.value.args == control.args
    _assert_production_frames_clean(caught.value, marker, required | {"_驗證並寫入", "發布"})
    assert _SQLControlConnection.close_calls == 1
    assert sqlite3.connect(path).execute("SELECT count(*) FROM published_endpoints").fetchone() == (0,)


def _assert_exception_graph_clean(error, marker):
    assert not _含標記(error.__cause__, marker, set())
    assert not _含標記(error.__context__, marker, set())


@pytest.mark.parametrize("cleanup", [KeyboardInterrupt("CLEANUP"), SystemExit("CLEANUP"), GeneratorExit("CLEANUP")])
def test_post_open_ordinary_primary後close控制精確勝出且圖無primary(tmp_path, cleanup):
    path = tmp_path / "post-open-distinct.db"
    初始化發布介面資料庫(path)
    _PostOpenConnection.stage = "pragma"
    _PostOpenConnection.control = RuntimeError("PRIMARY-PATH-SECRET")
    _PostOpenConnection.close_failure = cleanup
    _PostOpenConnection.close_calls = 0
    def connect(*args, **kwargs):
        return sqlite3.connect(*args, **kwargs, factory=_PostOpenConnection)
    with pytest.raises(type(cleanup)) as caught:
        _服務(path, connection_factory=connect).發布("owner", _已確認草稿(), _版本快照(), _已準備憑證(), 10)
    assert caught.value is cleanup and caught.value.args == ("CLEANUP",)
    assert cleanup.__cause__ is None and cleanup.__context__ is None
    _assert_exception_graph_clean(cleanup, "PRIMARY-PATH-SECRET")
    _assert_production_frames_clean(cleanup, "PRIMARY-PATH-SECRET", {"_驗證已開啟資料庫路徑", "發布"})
    _assert_production_frames_clean(cleanup, "CLEANUP", {"_拋出清理控制"})
    assert _PostOpenConnection.close_calls == 1


class _DistinctCleanupConnection(sqlite3.Connection):
    primary = rollback_failure = close_failure = None
    rollback_calls = close_calls = 0

    def execute(self, sql, parameters=()):
        if sql.startswith("INSERT INTO service_accounts"):
            raise type(self).primary
        if sql == "ROLLBACK":
            type(self).rollback_calls += 1
            if type(self).rollback_failure is not None:
                raise type(self).rollback_failure
        return super().execute(sql, parameters)

    def close(self):
        type(self).close_calls += 1
        if type(self).close_failure is not None:
            raise type(self).close_failure
        return super().close()


def _distinct服務(path, primary, rollback_failure, close_failure):
    _DistinctCleanupConnection.primary = primary
    _DistinctCleanupConnection.rollback_failure = rollback_failure
    _DistinctCleanupConnection.close_failure = close_failure
    _DistinctCleanupConnection.rollback_calls = _DistinctCleanupConnection.close_calls = 0
    def connect(*args, **kwargs):
        return sqlite3.connect(*args, **kwargs, factory=_DistinctCleanupConnection)
    return _服務(path, connection_factory=connect)


def _assert_distinct_cleanup(path, expected, absent_markers):
    assert expected.__cause__ is None and expected.__context__ is None
    for marker in absent_markers:
        _assert_exception_graph_clean(expected, marker)
        _assert_production_frames_clean(expected, marker, {"_驗證並寫入", "發布"})
    assert (_DistinctCleanupConnection.rollback_calls, _DistinctCleanupConnection.close_calls) == (1, 1)
    assert sqlite3.connect(path).execute("SELECT count(*) FROM published_endpoints").fetchone() == (0,)


def test_ordinary_insert後rollback控制勝過close控制且distinct_graph乾淨(tmp_path):
    path = tmp_path / "rollback-control.db"
    初始化發布介面資料庫(path)
    rollback = KeyboardInterrupt("ROLLBACK-CLEANUP")
    close = SystemExit("CLOSE-CLEANUP")
    with pytest.raises(KeyboardInterrupt) as caught:
        _distinct服務(path, RuntimeError("PRIMARY-INSERT-SECRET"), rollback, close).發布(
            "owner", _已確認草稿(), _版本快照(), _已準備憑證(), 10,
        )
    assert caught.value is rollback and rollback.args == ("ROLLBACK-CLEANUP",)
    _assert_distinct_cleanup(path, rollback, ("PRIMARY-INSERT-SECRET", "CLOSE-CLEANUP"))


def test_ordinary_insert與ordinary_rollback後close控制勝出(tmp_path):
    path = tmp_path / "close-control-precommit.db"
    初始化發布介面資料庫(path)
    close = SystemExit("CLOSE-CLEANUP")
    with pytest.raises(SystemExit) as caught:
        _distinct服務(path, RuntimeError("PRIMARY-INSERT-SECRET"), RuntimeError("ROLLBACK-ORDINARY"), close).發布(
            "owner", _已確認草稿(), _版本快照(), _已準備憑證(), 10,
        )
    assert caught.value is close
    _assert_distinct_cleanup(path, close, ("PRIMARY-INSERT-SECRET", "ROLLBACK-ORDINARY"))


def test_BEGIN_ordinary失敗不rollback且close控制勝出(tmp_path):
    path = tmp_path / "begin-close-control.db"
    初始化發布介面資料庫(path)
    close = SystemExit("BEGIN-CLOSE-CLEANUP")
    class BeginFailureConnection(_DistinctCleanupConnection):
        def execute(self, sql, parameters=()):
            if sql == "BEGIN IMMEDIATE":
                raise RuntimeError("BEGIN-PRIMARY-SECRET")
            return sqlite3.Connection.execute(self, sql, parameters)
    BeginFailureConnection.close_failure = close
    BeginFailureConnection.rollback_calls = BeginFailureConnection.close_calls = 0
    def connect(*args, **kwargs):
        return sqlite3.connect(*args, **kwargs, factory=BeginFailureConnection)
    with pytest.raises(SystemExit) as caught:
        _服務(path, connection_factory=connect).發布("owner", _已確認草稿(), _版本快照(), _已準備憑證(), 10)
    assert caught.value is close and (BeginFailureConnection.rollback_calls, BeginFailureConnection.close_calls) == (0, 1)
    _assert_exception_graph_clean(close, "BEGIN-PRIMARY-SECRET")
    _assert_production_frames_clean(close, "BEGIN-PRIMARY-SECRET", {"_驗證並寫入", "發布"})


@pytest.mark.parametrize("primary", [KeyboardInterrupt("PRIMARY-K"), SystemExit("PRIMARY-S"), GeneratorExit("PRIMARY-G")])
def test_primary控制既有secret鏈勝過rollback與close控制並去鏈(tmp_path, primary):
    path = tmp_path / f"primary-{type(primary).__name__}.db"
    初始化發布介面資料庫(path)
    primary.__cause__ = RuntimeError("PRIMARY-CHAIN-SECRET")
    with pytest.raises(type(primary)) as caught:
        _distinct服務(path, primary, KeyboardInterrupt("ROLLBACK-CLEANUP"), SystemExit("CLOSE-CLEANUP")).發布(
            "owner", _已確認草稿(), _版本快照(), _已準備憑證(), 10,
        )
    assert caught.value is primary
    _assert_distinct_cleanup(path, primary, ("PRIMARY-CHAIN-SECRET", "ROLLBACK-CLEANUP", "CLOSE-CLEANUP"))


def test_commit_success_close控制預鏈被清除且資料durable(tmp_path):
    path = tmp_path / "durable-close-chain.db"
    初始化發布介面資料庫(path)
    close = SystemExit("DURABLE-CLOSE")
    close.__cause__ = RuntimeError("CLOSE-CHAIN-SECRET")
    with pytest.raises(SystemExit) as caught:
        _狀態服務(path, "CLOSE", close).發布("owner", _已確認草稿(), _版本快照(), _已準備憑證(), 10)
    assert caught.value is close and close.__cause__ is None and close.__context__ is None
    _assert_exception_graph_clean(close, "CLOSE-CHAIN-SECRET")
    _assert_production_frames_clean(close, "CLOSE-CHAIN-SECRET", {"_驗證並寫入", "發布"})
    assert sqlite3.connect(path).execute("SELECT count(*) FROM published_endpoints").fetchone() == (1,)
