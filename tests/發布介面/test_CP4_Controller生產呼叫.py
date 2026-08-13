"""CP4 Controller production composition 與 live HTTP acceptance。"""
import json
import sqlite3
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from 繁中代理.工具 import 工具定義
from 繁中代理.使用者 import 使用者庫
from 繁中代理.發布介面.asgi import 建立CP4ASGI應用程式
from 繁中代理.發布介面.設定 import 生產設定
from 繁中代理.發布介面.生產Published執行 import Published生產設定
from 繁中代理.發布介面.生產Published管理 import Planner生產設定
from 繁中代理.發布介面.資料庫 import 初始化發布介面資料庫
from 繁中代理.發布介面.領域模型 import WebOwnerPrincipal
from 繁中代理.發布介面.技能套件.發布器 import 技能套件發布器
from 繁中代理.發布介面.技能套件.儲存庫 import 套件收據儲存庫
from 繁中代理.發布介面.憑證.儲存庫 import SQLite憑證儲存庫
from 繁中代理.發布介面.憑證.加密 import AESGCM憑證封套
from 繁中代理.發布介面.規劃.規劃器供應商 import 決定性假規劃器
from 繁中代理.發布介面.執行期.工具發布庫 import 工具發布描述, 工具發布註冊
from 繁中代理.發布介面.執行期.模型契約 import 模型回應快照


def _正規(值):
    """建立與 production schema 一致的 canonical JSON。"""
    return json.dumps(值, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


class _模型:
    """符合 genuine Published provider protocol 的可觀測模型。"""
    def __init__(self, 文字='{"answer":"CP4"}'):
        self.文字, self.calls = 文字, []

    def 產生發布回應(self, **參數):
        """記錄 production executor 傳入值並回傳 genuine runtime DTO。"""
        self.calls.append(參數)
        return 模型回應快照(self.文字, "stop", {"total_tokens": 3}, [])


def _設定(db: Path, bundles: Path, installer, factory):
    """建立 explicit CP3 與 CP4 immutable settings。"""
    Web資料庫 = db.with_name("web.sqlite3")
    生產 = 生產設定(Web資料庫, ("https://client.example",), "fake", "fake", None, None)
    return 生產, Published生產設定(db, bundles, installer, factory)


def _建立live環境(tmp_path, *, provider="fake"):
    """用正式 publisher、credential repository 與 SQLite schema 建立 live fixture。"""
    bundles, source = tmp_path / "bundles", tmp_path / "skill"
    source.mkdir()
    (source / "SKILL.md").write_text("CP4 live skill", encoding="utf-8")
    receipt = 技能套件發布器(bundles).發布(
        套件識別碼="bundle-1", 端點識別碼="ep-1", 端點版本識別碼="ver-1",
        版本號碼=1, 建立時間=1.0, 建立者識別碼="owner-1", 技能表={"cp4": source},
    )
    db = tmp_path / "cp4.sqlite3"
    初始化發布介面資料庫(db)
    parameters = {"type": "object", "properties": {"value": {"type": "integer"}},
                  "required": ["value"], "additionalProperties": False}
    tools = {"lookup": {"revision": "rev-1", "description": "pinned lookup", "parameters": parameters}}
    model_config = {"provider": provider, "model": "model-1", "temperature": 0.0,
                    "max_tokens": 20, "timeout_seconds": 3.0,
                    "structured_output": True, "schema_retry_count": 1}
    manifest = (receipt.路徑 / "manifest.json").read_text(encoding="utf-8")
    with sqlite3.connect(db) as connection:
        connection.execute("INSERT INTO service_accounts VALUES('sa-1',1,NULL)")
        connection.execute(
            "INSERT INTO published_endpoints VALUES(?,?,?,?,?,?,?,?,?,?)",
            ("ep-1", "owner-1", "sa-1", "demo", "active", None, 1, 1, 60, 60),
        )
        connection.execute(
            "INSERT INTO published_endpoint_versions VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            ("ver-1", "ep-1", 1, "需求", "固定提示", "[]", _正規(["lookup"]),
             _正規(tools), "release-1", _正規(model_config), "{}", manifest,
             _正規({"type": "object", "required": ["question"]}),
             _正規({"type": "object", "required": ["answer"]}), 0, "owner-1", 1),
        )
        套件收據儲存庫(connection).新增(版本識別碼="ver-1", 收據=receipt, 發布時間=2.0)
        connection.execute("UPDATE published_endpoints SET current_version_id='ver-1' WHERE id='ep-1'")
    now = time.time()
    credential = SQLite憑證儲存庫(
        db, AESGCM憑證封套({1: b"k" * 32}, 1), clock=lambda: now,
        id_factory=lambda: "cred-1",
    ).建立("ep-1", WebOwnerPrincipal("owner-1"), name="live", purpose="CP4 acceptance",
           expires_at=now + 86_400, rate_limit_requests=60)
    model = _模型()
    calls = []

    def install(repository):
        """在 startup 安裝 exact pinned release 一次。"""
        calls.append("installer")
        repository.登錄發布(工具發布描述("release-1", (工具發布註冊(
            "rev-1", 工具定義("lookup", "pinned lookup", parameters,
                              lambda args: {"value": args["value"]}),
        ),)))

    def models():
        """在 startup 建立 detached model registry 一次。"""
        calls.append("models")
        return {"fake": model}

    production, published = _設定(db, bundles, install, models)
    app = 建立CP4ASGI應用程式(production, published)
    app.state.重建canonical應用程式 = lambda: 建立CP4ASGI應用程式(production, published)
    return db, app, credential.api_key, model, calls


def test_app_construction零callback且不建立資料庫(tmp_path):
    """CP4-COMP-01：import/app construction 沒有 installer/provider/DB side effect。"""
    db, calls = tmp_path / "untouched.sqlite3", []
    production, published = _設定(
        db, tmp_path / "missing-bundles", lambda _: calls.append("installer"),
        lambda: calls.append("models") or {"fake": object()},
    )
    app = 建立CP4ASGI應用程式(production, published)
    assert calls == [] and not db.exists() and not db.with_name("web.sqlite3").exists()
    assert list(app.openapi()["paths"]).count("/v1/endpoints/{slug}/invoke") == 1
    assert tuple(app.openapi()["paths"]["/v1/endpoints/{slug}/invoke"]) == ("post",)


def test_management路由只依explicit設定公開可用能力(tmp_path):
    """CP4-COMP-04：invoke／draft／create route inventory 必須符合 explicit composition。

    參數：``tmp_path`` 提供三組隔離設定所需的 absolute paths。
    返回值：無；三種設定的 OpenAPI inventory 皆符合公開能力。
    例外：route 宣告不存在的 authority 或漏掛完整 management 時 assertion 失敗。
    副作用：只建構應用並讀 OpenAPI，不呼叫工廠、不建立資料庫或目錄。
    """
    Web設定 = 生產設定(
        tmp_path / "web.sqlite3", ("https://client.example",), "fake", "fake", None, None,
    )
    共用參數 = (
        tmp_path / "published.sqlite3", tmp_path / "bundles",
        lambda _工具庫: None, lambda: {"fake": object()},
    )
    invoke應用 = 建立CP4ASGI應用程式(Web設定, Published生產設定(*共用參數))
    invoke路徑 = invoke應用.openapi()["paths"]
    assert tuple(invoke路徑["/api/published-endpoints/draft"]) == ("post",)
    assert "/api/published-endpoints" not in invoke路徑

    Planner設定 = Planner生產設定(
        "release-1", lambda 路徑: 使用者庫(路徑), lambda: 決定性假規劃器(), 3600.0,
    )
    draft應用 = 建立CP4ASGI應用程式(
        Web設定, Published生產設定(*共用參數, Planner設定=Planner設定),
    )
    draft路徑 = draft應用.openapi()["paths"]
    assert tuple(draft路徑["/api/published-endpoints/draft"]) == ("post",)
    assert "/api/published-endpoints" not in draft路徑

    完整應用 = 建立CP4ASGI應用程式(
        Web設定,
        Published生產設定(
            *共用參數, Planner設定=Planner設定,
            憑證封套工廠=lambda: AESGCM憑證封套({1: b"k" * 32}, 1),
        ),
    )
    完整路徑 = 完整應用.openapi()["paths"]
    assert tuple(完整路徑["/api/published-endpoints/draft"]) == ("post",)
    assert tuple(完整路徑["/api/published-endpoints"]) == ("post",)


def test_憑證管理可獨立Planner設定且建構不呼叫注入(tmp_path):
    """CP4-COMP-05：A07 management key authority 不再錯綁 Planner authority。

    參數：``tmp_path`` 提供不會在 construction 讀取的 absolute Published paths。
    返回值：無；設定可建立且 OpenAPI 公開三條 credential routes。
    例外：組裝或 route inventory 漂移時 assertion 失敗。
    副作用：不得呼叫 envelope factory、installer、model factory 或建立檔案。
    """
    呼叫: list[str] = []
    Web設定 = 生產設定(
        tmp_path / "web.sqlite3", ("https://client.example",), "fake", "fake", None, None,
    )
    Published設定 = Published生產設定(
        tmp_path / "published.sqlite3", tmp_path / "bundles",
        lambda _工具庫: 呼叫.append("installer"),
        lambda: 呼叫.append("models") or {"fake": object()},
        憑證封套工廠=lambda: 呼叫.append("envelope") or AESGCM憑證封套(
            {1: b"k" * 32}, 1,
        ),
    )
    應用 = 建立CP4ASGI應用程式(Web設定, Published設定)
    路徑 = 應用.openapi()["paths"]
    assert set(路徑["/api/published-endpoints/{endpoint_id}/credentials"]) == {"get", "post"}
    assert set(路徑["/api/published-endpoints/{endpoint_id}/credentials/{credential_id}/revoke"]) == {"post"}
    assert 呼叫 == []


def test_startup安裝器與模型工廠exact_once並在shutdown清除(tmp_path):
    """CP4-COMP-02／LIFE-01：startup 一次，shutdown detach Published resource。"""
    db, bundles, calls = tmp_path / "empty.sqlite3", tmp_path / "bundles", []
    production, published = _設定(
        db, bundles, lambda _: calls.append("installer"),
        lambda: calls.append("models") or {"fake": object()},
    )
    bundles.mkdir()
    app = 建立CP4ASGI應用程式(production, published)
    with TestClient(app) as client:
        assert client.get("/openapi.json").status_code == 200
        assert calls == ["installer", "models"]
        resources = app.state.發布介面資源
        assert len(resources) == 2 and resources[1]._已關閉 is False
    assert resources[1]._已關閉 is True and resources[1]._編排器 is None
    assert calls == ["installer", "models"]


def test_Web與Published共用資料庫時startup固定拒絕且零注入(tmp_path):
    """CP4-COMP-03：Web與Published不得共用schema authority或先呼叫外部注入。"""
    資料庫, 呼叫 = tmp_path / "shared.sqlite3", []
    生產 = 生產設定(資料庫, ("https://client.example",), "fake", "fake", None, None)
    發布 = Published生產設定(
        資料庫, tmp_path / "bundles", lambda _: 呼叫.append("installer"),
        lambda: 呼叫.append("models") or {"fake": object()},
    )
    應用程式 = 建立CP4ASGI應用程式(生產, 發布)
    try:
        with TestClient(應用程式):
            raise AssertionError("共用資料庫不得啟動")
    except RuntimeError as 錯誤:
        assert str(錯誤) == "發布介面啟動失敗"
    assert 呼叫 == []


def test_live_HTTP200使用exact版本並完成ledger(tmp_path):
    """CP4-INVOKE-01／LOG-01：全真實 SQLite、bundle、credential 與 HTTP path 成功。"""
    db, app, key, model, calls = _建立live環境(tmp_path)
    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.post(
            "/v1/endpoints/demo/invoke", headers={"Authorization": f"Bearer {key}"},
            json={"input": {"question": "CP4"}},
        )
    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True and body["endpoint"] == {"id": "ep-1", "slug": "demo", "version": 1}
    assert body["data"] == {"answer": "CP4"} and calls == ["installer", "models"]
    assert len(model.calls) == 1
    with sqlite3.connect(db) as connection:
        invocation = connection.execute(
            "SELECT status,endpoint_version_id,output_json,error_json FROM endpoint_invocations"
        ).fetchone()
        events = connection.execute(
            "SELECT sequence_number,event_type,payload_json FROM run_events ORDER BY sequence_number"
        ).fetchall()
    assert invocation[:2] == ("succeeded", "ver-1") and json.loads(invocation[2]) == {"answer": "CP4"}
    assert invocation[3] is None and events == [(1, "model_attempt", '{"attempt":1,"kind":"success","schema_valid":true}')]


def test_missing_provider固定拒絕且零模型呼叫(tmp_path):
    """CP4-RUNTIME-02：snapshot provider 未登錄時不得 fallback。"""
    _, app, key, model, _ = _建立live環境(tmp_path, provider="missing")
    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.post("/v1/endpoints/demo/invoke",
                               headers={"Authorization": f"Bearer {key}"},
                               json={"input": {"question": "CP4"}})
    assert response.status_code == 500
    assert response.json()["error"]["code"] == "endpoint_misconfigured" and model.calls == []
