"""PUB P04 endpoint、v1 與 prepared credential 原子發布。"""

import json
import os
import sqlite3
import threading
from dataclasses import replace
from pathlib import Path

import pytest

from 繁中代理.發布介面 import 授權工具, 授權技能, 規劃權限快照
from 繁中代理.發布介面.資料庫 import 初始化發布介面資料庫
from 繁中代理.發布介面.規劃.綱要 import 規劃服務
from 繁中代理.發布介面.規劃.權限協調 import 權限協調器
from 繁中代理.發布介面.規劃 import 端點發布 as 發布模組
from 繁中代理.發布介面.規劃.端點發布 import (
    SQLite端點發布服務,
    已準備初始憑證,
    已準備發布識別,
    發布版本快照,
    端點發布結果,
    端點發布錯誤,
    端點發布耐久性未知,
    端點發布輸入錯誤,
)
from 繁中代理.發布介面.技能套件.發布器 import 套件發布收據


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


def _預配識別(**覆寫):
    """建立可局部覆寫的預配識別。參數：關鍵字覆寫。回傳：合法 DTO。
    例外：非法覆寫傳出輸入錯誤。副作用：無。
    """
    值 = dict(
        endpoint_id="endpoint-ready", version_id="version-ready",
        credential_id="credential-ready", service_account_id="account-ready",
        套件識別碼="bundle-ready", 稽核識別碼="audit-ready", created_at=20.0,
    )
    值.update(覆寫)
    return 已準備發布識別(**值)


def _套件收據(**覆寫):
    """建立可局部覆寫的套件收據。參數：關鍵字覆寫。回傳：收據 DTO。
    例外：建構錯誤原樣傳出。副作用：無。
    """
    值 = dict(
        套件識別碼="bundle-ready", 清單參照="bundle-ready/manifest.json",
        清單摘要="b" * 64, 套件雜湊="c" * 64, 總位元組數=12,
        路徑=Path("/published/bundle-ready"),
    )
    值.update(覆寫)
    return 套件發布收據(**值)


def _預配版本快照(**覆寫):
    """建立釘選套件投影的版本快照。參數：關鍵字覆寫。回傳：版本快照。
    例外：非法覆寫傳出輸入錯誤。副作用：無。
    """
    值 = dict(skill_bundle_manifest={
        "bundle_id": "bundle-ready", "manifest_reference": "bundle-ready/manifest.json",
        "manifest_digest": "b" * 64, "sha256": "c" * 64,
    })
    值.update(覆寫)
    return _版本快照(**值)


def test_已準備發布識別為exact不可變DTO且拒絕碰撞與非法時間():
    """驗證預配 DTO。參數：無。回傳：無。例外：契約違反由 pytest 回報。
    副作用：只配置記憶體物件。
    """
    識別 = _預配識別()
    assert 識別.__slots__ and not hasattr(識別, "__dict__")
    assert 識別.created_at == 20.0
    with pytest.raises(端點發布輸入錯誤, match="^端點發布輸入無效$"):
        _預配識別(稽核識別碼="endpoint-ready")
    with pytest.raises(端點發布輸入錯誤, match="^端點發布輸入無效$"):
        _預配識別(created_at=float("nan"))


def test_預配發布單交易建立圖形收據稽核且零callback(tmp_path):
    """驗證完整預配交易。參數：暫存目錄。回傳：無。例外：違約由 pytest 回報。
    副作用：建立並查詢測試資料庫。
    """
    path = tmp_path / "prepared.db"
    初始化發布介面資料庫(path)
    calls = []
    ciphertext = (b"PLAINTEXT-MARKER" + b"x" * 62)[:62]
    result = _服務(path, calls=calls).發布已準備圖形(
        "owner", _已確認草稿(), _預配版本快照(),
        _已準備憑證(key_ciphertext=ciphertext),
        _預配識別(), _套件收據(), 請求識別碼="request-1",
    )

    assert result == 端點發布結果("endpoint-ready", "version-ready", "credential-ready", "account-ready")
    assert calls == []
    connection = sqlite3.connect(path)
    bundle = connection.execute(
        "SELECT bundle_id,version_id,manifest_reference,manifest_digest,bundle_hash,published_at FROM published_skill_bundles"
    ).fetchone()
    audit = connection.execute(
        "SELECT id,event_id,occurred_at,action,outcome,actor_type,actor_id,resource_type,resource_id,request_id,endpoint_id,invocation_id,metadata_json,created_at FROM audit_events"
    ).fetchone()
    assert bundle == (
        "bundle-ready", "version-ready", "bundle-ready/manifest.json", "b" * 64,
        "c" * 64, 20.0,
    )
    assert audit[:12] == (
        "audit-ready", "audit-ready", 20.0, "endpoint_published", "success", "user",
        "owner", "published_endpoint", "endpoint-ready", "request-1", "endpoint-ready", None,
    )
    assert json.loads(audit[12]) == {
        "bundle_hash": "c" * 64, "bundle_id": "bundle-ready",
        "credential_id": "credential-ready", "service_account_id": "account-ready",
        "version_id": "version-ready", "version_number": 1,
    }
    assert audit[13] == 20.0
    assert connection.execute(
        "SELECT draft_id,endpoint_id,consumed_at FROM published_draft_consumptions"
    ).fetchone() == ("draft-p04", "endpoint-ready", 20.0)
    assert connection.execute(
        "SELECT version_id,publication_source,prompt_changed,skills_changed,tools_changed,model_changed,docs_changed "
        "FROM published_endpoint_version_metadata"
    ).fetchone() == ("version-ready", "initial_draft", 0, 0, 0, 0, 0)
    forbidden = ("回答問題", "請精確回答", "1234", "pub_", "a" * 64, "PLAINTEXT-MARKER")
    assert all(marker not in audit[12] for marker in forbidden)


@pytest.mark.parametrize("kind", ["bundle-id", "reference", "digest", "hash", "path", "request"])
def test_預配ID與receipt任何關係漂移皆零callback與零open(tmp_path, kind):
    """驗證關係漂移前置拒絕。參數：暫存目錄與漂移種類。回傳：無。
    例外：只接受固定輸入錯誤。副作用：不得開啟資料庫或呼叫工廠。
    """
    calls, opens = [], []
    ids, snapshot, receipt, request_id = _預配識別(), _預配版本快照(), _套件收據(), "request-1"
    if kind == "bundle-id": receipt = _套件收據(套件識別碼="bundle-other")
    elif kind == "reference": receipt = _套件收據(清單參照="bundle-ready/other.json")
    elif kind == "digest": receipt = _套件收據(清單摘要="d" * 64)
    elif kind == "hash": receipt = _套件收據(套件雜湊="d" * 64)
    elif kind == "path": receipt = _套件收據(路徑=Path("/published/other"))
    else: request_id = " bad "
    with pytest.raises(端點發布輸入錯誤, match="^端點發布輸入無效$"):
        _服務(tmp_path / "absent.db", calls=calls, connection_factory=lambda *a, **k: opens.append(1)).發布已準備圖形(
            "owner", _已確認草稿(), snapshot, _已準備憑證(), ids, receipt,
            請求識別碼=request_id,
        )
    assert calls == [] and opens == []


def test_連線工廠並行竄改原收據仍只寫入脫離快照(tmp_path, monkeypatch):
    """鎖定開啟邊界後的 hostile receipt mutation 與交易內二次關係驗證。

    參數：pytest 提供暫存目錄與模組替換工具。
    回傳：無；斷言兩次驗證使用不同收據身分，且資料庫只保存開啟前快照。
    例外：契約違反由 pytest 斷言回報；背景執行緒不得留下未處理例外。
    副作用：建立測試資料庫與一條竄改執行緒，並暫時包裝關係驗證函式。
    """
    資料庫 = tmp_path / "hostile-receipt.db"
    初始化發布介面資料庫(資料庫)
    原收據 = _套件收據()
    驗證呼叫 = []
    原驗證 = 發布模組._驗證預配關係

    def 記錄驗證(識別碼, 快照, 收據, 請求識別碼):
        """記錄驗證身分。參數：原驗證四參數。回傳：脫離收據。
        例外：原驗證例外原樣傳出。副作用：附加一筆記憶體紀錄。
        """
        驗證呼叫.append(收據)
        return 原驗證(識別碼, 快照, 收據, 請求識別碼)

    monkeypatch.setattr(發布模組, "_驗證預配關係", 記錄驗證)
    開始竄改 = threading.Event()
    完成竄改 = threading.Event()

    def 竄改原收據():
        """等待後竄改原收據。參數：無。回傳：無。例外：等待逾時由斷言回報。
        副作用：跨執行緒改寫測試 DTO slots 並設定事件。
        """
        assert 開始竄改.wait(5)
        object.__setattr__(原收據, "套件識別碼", "bundle-hostile")
        object.__setattr__(原收據, "清單參照", "bundle-hostile/manifest.json")
        object.__setattr__(原收據, "清單摘要", "d" * 64)
        object.__setattr__(原收據, "套件雜湊", "e" * 64)
        object.__setattr__(原收據, "總位元組數", 99)
        object.__setattr__(原收據, "路徑", Path("/published/bundle-hostile"))
        完成竄改.set()

    執行緒 = threading.Thread(target=竄改原收據)
    執行緒.start()

    def 連線工廠(*參數, **選項):
        """在開啟 SQLite 前等待竄改。參數：連線參數。回傳：SQLite 連線。
        例外：等待逾時由斷言回報。副作用：同步執行緒並開啟測試資料庫。
        """
        開始竄改.set()
        assert 完成竄改.wait(5)
        return sqlite3.connect(*參數, **選項)

    結果 = _服務(資料庫, connection_factory=連線工廠).發布已準備圖形(
        "owner", _已確認草稿(), _預配版本快照(), _已準備憑證(),
        _預配識別(), 原收據, 請求識別碼="request-1",
    )
    執行緒.join(5)

    assert not 執行緒.is_alive() and 結果.version_id == "version-ready"
    assert len(驗證呼叫) == 2 and 驗證呼叫[0] is 原收據 and 驗證呼叫[1] is not 原收據
    assert 驗證呼叫[1] == _套件收據()
    連線 = sqlite3.connect(資料庫)
    assert 連線.execute(
        "SELECT bundle_id,manifest_reference,manifest_digest,bundle_hash,total_bytes "
        "FROM published_skill_bundles"
    ).fetchone() == (
        "bundle-ready", "bundle-ready/manifest.json", "b" * 64, "c" * 64, 12,
    )
    稽核 = 連線.execute("SELECT id,request_id,metadata_json FROM audit_events").fetchone()
    assert 稽核[:2] == ("audit-ready", "request-1")
    assert json.loads(稽核[2])["bundle_hash"] == "c" * 64


def test_稽核末段失敗rollback全部新圖形與收據(tmp_path):
    """驗證稽核碰撞回滾。參數：暫存目錄。回傳：無。例外：只接受發布錯誤。
    副作用：建立資料庫、植入碰撞列並查詢回滾結果。
    """
    path = tmp_path / "audit-rollback.db"
    初始化發布介面資料庫(path)
    connection = sqlite3.connect(path)
    connection.execute(
        "INSERT INTO audit_events VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        ("audit-ready", "audit-ready", 1, "seed", "success", "system", None,
         "seed", "seed", None, None, None, "{}", 1),
    )
    connection.commit()
    connection.close()

    with pytest.raises(端點發布錯誤, match="^端點發布失敗$"):
        _服務(path).發布已準備圖形(
            "owner", _已確認草稿(), _預配版本快照(), _已準備憑證(),
            _預配識別(), _套件收據(), 請求識別碼=None,
        )
    connection = sqlite3.connect(path)
    for table in (
        "service_accounts", "published_endpoints", "published_endpoint_versions",
        "endpoint_credentials", "published_skill_bundles",
    ):
        assert connection.execute(f"SELECT count(*) FROM {table}").fetchone() == (0,)
    assert connection.execute("SELECT count(*) FROM audit_events").fetchone() == (1,)


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
    assert connection.execute(
        "SELECT draft_id,endpoint_id,consumed_at FROM published_draft_consumptions"
    ).fetchone() == ("draft-p04", "endpoint-1", 20.0)
    assert connection.execute(
        "SELECT version_id,publication_source,prompt_changed,skills_changed,tools_changed,model_changed,docs_changed "
        "FROM published_endpoint_version_metadata"
    ).fetchone() == ("version-1", "initial_draft", 0, 0, 0, 0, 0)
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
        connection.execute("INSERT INTO published_api_schema_migrations VALUES(13,'0013_unknown.sql',0)")
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


def _斷言預配八張逐值圖形(fresh: sqlite3.Connection) -> None:
    """逐欄驗證 ack-loss 後的 SA、端點、版本、消耗、metadata、憑證、收據與稽核。

    參數：``fresh`` 是 owner connection 關閉後另開的 canonical 連線。回傳：無。
    例外：任何欄值、pointer、manifest reference／digest 或 bundle hash 漂移即斷言失敗。
    副作用：只執行八張表的唯讀查詢。
    """
    assert fresh.execute(
        "SELECT id,created_at,disabled_at FROM service_accounts"
    ).fetchone() == ("account-ready", 20.0, None)
    assert fresh.execute(
        "SELECT id,owner_user_id,service_account_id,slug,status,current_version_id,created_at,updated_at,rate_limit_requests,rate_limit_window_seconds FROM published_endpoints"
    ).fetchone() == (
        "endpoint-ready", "owner", "account-ready", "customer-support", "active",
        "version-ready", 20.0, 20.0, 60, 60,
    )
    assert fresh.execute(
        "SELECT id,endpoint_id,version_number,original_requirement_text,system_prompt,allowed_skills_json,allowed_tools_json,tool_schema_snapshot_json,tool_runtime_revision,model_config_snapshot_json,retry_policy_json,skill_bundle_manifest_json,input_schema_json,response_schema_json,schema_changed,created_by_user_id,created_at FROM published_endpoint_versions"
    ).fetchone() == (
        "version-ready", "endpoint-ready", 1, "回答問題", "請精確回答",
        '["skill.one"]', '["tool.one"]', '{"tool.one":{"type":"object"}}',
        "runtime-1", '{"model":"test-model","temperature":0}', '{"max_attempts":1}',
        '{"bundle_id":"bundle-ready","manifest_digest":"' + "b" * 64
        + '","manifest_reference":"bundle-ready/manifest.json","sha256":"' + "c" * 64 + '"}',
        None, '{"type":"string"}', 0, "owner", 20.0,
    )
    assert fresh.execute(
        "SELECT draft_id,endpoint_id,consumed_at FROM published_draft_consumptions"
    ).fetchone() == ("draft-p04", "endpoint-ready", 20.0)
    assert fresh.execute(
        "SELECT version_id,publication_source,prompt_changed,skills_changed,tools_changed,model_changed,docs_changed FROM published_endpoint_version_metadata"
    ).fetchone() == ("version-ready", "initial_draft", 0, 0, 0, 0, 0)
    assert fresh.execute(
        "SELECT id,endpoint_id,name,purpose,key_version,key_nonce,key_ciphertext,key_hash,key_prefix,key_last4,expires_at,last_used_at,created_at,updated_at,revoked_at,ip_allowlist_json,rate_limit_requests,created_by_user_id,revision FROM endpoint_credentials"
    ).fetchone() == (
        "credential-ready", "endpoint-ready", "初始憑證", "呼叫端點", 1,
        b"n" * 12, b"c" * 62, "a" * 64, "pub_", "1234", 999.0, None,
        20.0, 20.0, None, '["203.0.113.1"]', 30, "owner", 0,
    )
    assert fresh.execute(
        "SELECT bundle_id,version_id,manifest_reference,manifest_digest,bundle_hash,total_bytes,state,published_at,reconciled_at FROM published_skill_bundles"
    ).fetchone() == (
        "bundle-ready", "version-ready", "bundle-ready/manifest.json", "b" * 64,
        "c" * 64, 12, "published", 20.0, None,
    )
    audit = fresh.execute(
        "SELECT id,event_id,occurred_at,action,outcome,actor_type,actor_id,resource_type,resource_id,request_id,endpoint_id,invocation_id,metadata_json,created_at FROM audit_events"
    ).fetchone()
    assert audit[:12] == (
        "audit-ready", "audit-ready", 20.0, "endpoint_published", "success", "user",
        "owner", "published_endpoint", "endpoint-ready", "request-1", "endpoint-ready", None,
    )
    assert json.loads(audit[12]) == {
        "bundle_hash": "c" * 64, "bundle_id": "bundle-ready",
        "credential_id": "credential-ready", "service_account_id": "account-ready",
        "version_id": "version-ready", "version_number": 1,
    }
    assert audit[13] == 20.0


def test_預配交易COMMIT已耐久但ack遺失以fresh完整postcondition判定成功(tmp_path):
    """真 COMMIT 後才遺失 acknowledgement 時，完整 canonical graph readback 應收斂成功。

    參數：``tmp_path`` 提供隔離 SQLite。回傳：無；以公開 connection factory 注入
    acknowledgement-loss。例外：若耐久結果被誤報或任一 canonical row 缺失即測試失敗。
    副作用：建立一個完整 v1 publication，並以另一條 fresh connection 驗證提交結果。
    """
    path = tmp_path / "commit-ack-loss.db"
    初始化發布介面資料庫(path)

    class Ack遺失連線(sqlite3.Connection):
        """只在真正 COMMIT 完成後拋出一次 ordinary acknowledgement-loss。"""

        def execute(self, sql, parameters=()):
            if sql == "COMMIT":
                super().execute(sql, parameters)
                raise sqlite3.OperationalError("acknowledgement-lost")
            return super().execute(sql, parameters)

    def connect(*args, **kwargs):
        """透過正式 connection factory 建立真實 SQLite subclass。"""
        return sqlite3.connect(*args, **kwargs, factory=Ack遺失連線)

    result = _服務(path, connection_factory=connect).發布已準備圖形(
        "owner", _已確認草稿(), _預配版本快照(), _已準備憑證(),
        _預配識別(), _套件收據(), 請求識別碼="request-1",
    )
    assert result == 端點發布結果(
        "endpoint-ready", "version-ready", "credential-ready", "account-ready",
    )
    with sqlite3.connect(path) as fresh:
        _斷言預配八張逐值圖形(fresh)


@pytest.mark.parametrize("控制型別", [KeyboardInterrupt, SystemExit, GeneratorExit])
def test_預配交易COMMIT已耐久後控制流程保留identity且八張圖形不撤銷(
    tmp_path, 控制型別,
):
    """真正提交後的系統中斷須原樣傳出，且不得把已耐久的八張關聯資料當作可撤銷。

    參數：``tmp_path`` 提供隔離資料庫；``控制型別`` 選擇三種正式系統中斷。
    回傳：無；原物件身分、參數與八張表各一筆皆由斷言固定。
    例外：預期原始系統中斷由 P04 原樣傳出；其他結果代表提交邊界分類漂移。
    副作用：建立完整第一版發布關聯，並在真正 COMMIT 後模擬成功回應遺失。
    """
    path = tmp_path / f"commit-control-{控制型別.__name__}.db"
    初始化發布介面資料庫(path)
    主要 = 控制型別("POST_COMMIT_CONTROL", "opaque")

    class 提交後控制連線(sqlite3.Connection):
        """完成真 COMMIT 後拋出指定原始系統中斷物件。"""

        def execute(self, sql, parameters=()):
            if sql == "COMMIT":
                super().execute(sql, parameters)
                raise 主要
            return super().execute(sql, parameters)

    def connect(*args, **kwargs):
        """透過正式連線工廠建立提交後中斷連線。"""
        return sqlite3.connect(*args, **kwargs, factory=提交後控制連線)

    with pytest.raises(控制型別) as caught:
        _服務(path, connection_factory=connect).發布已準備圖形(
            "owner", _已確認草稿(), _預配版本快照(), _已準備憑證(),
            _預配識別(), _套件收據(), 請求識別碼="request-1",
        )
    assert caught.value is 主要 and caught.value.args == ("POST_COMMIT_CONTROL", "opaque")
    with sqlite3.connect(path) as fresh:
        assert [fresh.execute(f"SELECT count(*) FROM {table}").fetchone()[0] for table in (
            "service_accounts", "published_endpoints", "published_endpoint_versions",
            "published_draft_consumptions", "published_endpoint_version_metadata",
            "endpoint_credentials", "published_skill_bundles", "audit_events",
        )] == [1] * 8


def test_COMMIT正常ack但fresh證明not_committed不得成功(tmp_path):
    """連線若正常回覆 COMMIT 卻實際 rollback，fresh 零圖形必須固定拒絕。

    參數：``tmp_path`` 隔離 SQLite。回傳：無。例外：公開服務只接受固定
    ``端點發布錯誤``。副作用：正式 connection factory 將 COMMIT seam 改為真
    ROLLBACK 後正常回傳，再由 canonical fresh connection 證明八張圖形均未提交。
    """
    path = tmp_path / "false-commit-ack.db"
    初始化發布介面資料庫(path)

    class 假正常確認連線(sqlite3.Connection):
        """在 COMMIT 呼叫點真正回滾，卻以 cursor 模擬正常 acknowledgement。"""

        def execute(self, sql, parameters=()):
            if sql == "COMMIT":
                return super().execute("ROLLBACK")
            return super().execute(sql, parameters)

    def connect(*args, **kwargs):
        """透過正式連線工廠建立保留其餘 SQLite semantics 的測試連線。"""
        return sqlite3.connect(*args, **kwargs, factory=假正常確認連線)

    with pytest.raises(端點發布錯誤, match="^端點發布失敗$"):
        _服務(path, connection_factory=connect).發布已準備圖形(
            "owner", _已確認草稿(), _預配版本快照(), _已準備憑證(),
            _預配識別(), _套件收據(), 請求識別碼="request-1",
        )
    with sqlite3.connect(path) as fresh:
        assert [fresh.execute(f"SELECT count(*) FROM {table}").fetchone()[0] for table in (
            "service_accounts", "published_endpoints", "published_endpoint_versions",
            "published_draft_consumptions", "published_endpoint_version_metadata",
            "endpoint_credentials", "published_skill_bundles", "audit_events",
        )] == [0] * 8


def test_COMMIT_ack遺失後資料庫authority替換回專用durability_unknown(tmp_path):
    """COMMIT 已發生但 canonical path 被替換時，不得把替代 DB 的零列假稱 rollback。

    參數：``tmp_path`` 提供原始與替代 SQLite。回傳：無。例外：只接受專用
    ``端點發布耐久性未知``。副作用：正式 connection factory 在真 COMMIT 後原子替換
    canonical path，以可達 TOCTOU 證明 fresh readback 無法判定原 inode 的 durability。
    """
    path = tmp_path / "commit-authority.db"
    replacement = tmp_path / "replacement.db"
    初始化發布介面資料庫(path)
    初始化發布介面資料庫(replacement)

    class Authority替換連線(sqlite3.Connection):
        """真 COMMIT 後替換 canonical DB path，再模擬 acknowledgement-loss。"""

        def execute(self, sql, parameters=()):
            if sql == "COMMIT":
                super().execute(sql, parameters)
                os.replace(replacement, path)
                raise sqlite3.OperationalError("acknowledgement-lost")
            return super().execute(sql, parameters)

    def connect(*args, **kwargs):
        """建立保留真 SQLite commit semantics 的 TOCTOU 連線。"""
        return sqlite3.connect(*args, **kwargs, factory=Authority替換連線)

    with pytest.raises(端點發布耐久性未知, match="^端點發布耐久性未知$"):
        _服務(path, connection_factory=connect).發布已準備圖形(
            "owner", _已確認草稿(), _預配版本快照(), _已準備憑證(),
            _預配識別(), _套件收據(), 請求識別碼="request-1",
        )
    assert sqlite3.connect(path).execute(
        "SELECT count(*) FROM published_endpoints"
    ).fetchone() == (0,)


@pytest.mark.parametrize("失敗前綴", [
    "INSERT INTO service_accounts",
    "INSERT INTO published_endpoints",
    "INSERT INTO published_endpoint_versions",
    "INSERT INTO published_draft_consumptions",
    "INSERT INTO published_endpoint_version_metadata",
    "INSERT INTO endpoint_credentials",
    "INSERT INTO published_skill_bundles",
    "INSERT INTO audit_events",
    "UPDATE published_endpoints SET current_version_id",
])
def test_預配交易每個graph_statement明確失敗完整rollback且pointer永遠最後(tmp_path, 失敗前綴):
    """SA 至 pointer 的每個正式 statement seam 都必須回滾八張 canonical 表。

    參數：``tmp_path`` 隔離資料庫；``失敗前綴`` 選擇公開 connection factory 攔截的
    真 SQL。回傳：無。例外：只接受固定、無鏈結 ``端點發布錯誤``。
    副作用：每案建立 fresh DB、命中指定 statement，並從另一連線讀回完整零圖形。
    """
    path = tmp_path / f"late-{abs(hash(失敗前綴))}.db"
    初始化發布介面資料庫(path)
    已見: list[str] = []

    class 精確失敗連線(sqlite3.Connection):
        """記錄 mutation 順序並在指定公開 SQL 前綴明確失敗。"""

        def execute(self, sql, parameters=()):
            if sql.startswith(("INSERT INTO ", "UPDATE published_endpoints")):
                已見.append(sql)
            if sql.startswith(失敗前綴):
                raise sqlite3.OperationalError("fixed-statement-failure")
            return super().execute(sql, parameters)

    def connect(*args, **kwargs):
        """建立有正式 SQLite 行為的失敗注入連線。"""
        return sqlite3.connect(*args, **kwargs, factory=精確失敗連線)

    with pytest.raises(端點發布錯誤, match="^端點發布失敗$") as caught:
        _服務(path, connection_factory=connect).發布已準備圖形(
            "owner", _已確認草稿(), _預配版本快照(), _已準備憑證(),
            _預配識別(), _套件收據(), 請求識別碼="request-1",
        )
    assert caught.value.__cause__ is None and caught.value.__context__ is None
    if 失敗前綴.startswith("UPDATE"):
        assert 已見[-1].startswith("UPDATE published_endpoints SET current_version_id")
    with sqlite3.connect(path) as fresh:
        assert [fresh.execute(f"SELECT count(*) FROM {table}").fetchone()[0] for table in (
            "service_accounts", "published_endpoints", "published_endpoint_versions",
            "published_draft_consumptions", "published_endpoint_version_metadata",
            "endpoint_credentials", "published_skill_bundles", "audit_events",
        )] == [0] * 8


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


def test_預配交易commit後ordinary_close失敗仍成功且全部durable(tmp_path):
    """驗證提交後一般關閉失敗。參數：暫存目錄。回傳：無。例外：違約由 pytest 回報。
    副作用：建立資料庫並注入一次關閉失敗。
    """
    path = tmp_path / "prepared-close-ordinary.db"
    初始化發布介面資料庫(path)
    result = _狀態服務(path, "CLOSE", RuntimeError("close-marker")).發布已準備圖形(
        "owner", _已確認草稿(), _預配版本快照(), _已準備憑證(),
        _預配識別(), _套件收據(), 請求識別碼=None,
    )
    assert result.endpoint_id == "endpoint-ready" and _狀態連線.close_calls == 1
    connection = sqlite3.connect(path)
    assert connection.execute("SELECT count(*) FROM published_skill_bundles").fetchone() == (1,)
    assert connection.execute("SELECT count(*) FROM audit_events").fetchone() == (1,)


@pytest.mark.parametrize("control", [KeyboardInterrupt("prepared-K"), SystemExit("prepared-S"), GeneratorExit("prepared-G")])
def test_預配交易commit後close控制保留exact身分與durable圖形(tmp_path, control):
    """驗證提交後關閉控制流。參數：暫存目錄與控制例外。回傳：無。
    例外：要求同一控制例外傳出。副作用：建立資料庫並注入關閉控制流。
    """
    path = tmp_path / f"prepared-close-{type(control).__name__}.db"
    初始化發布介面資料庫(path)
    with pytest.raises(type(control)) as caught:
        _狀態服務(path, "CLOSE", control).發布已準備圖形(
            "owner", _已確認草稿(), _預配版本快照(), _已準備憑證(),
            _預配識別(), _套件收據(), 請求識別碼=None,
        )
    assert caught.value is control and caught.value.args == control.args
    connection = sqlite3.connect(path)
    assert connection.execute("SELECT count(*) FROM published_endpoints").fetchone() == (1,)
    assert connection.execute("SELECT count(*) FROM published_skill_bundles").fetchone() == (1,)
    assert connection.execute("SELECT count(*) FROM audit_events").fetchone() == (1,)


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
