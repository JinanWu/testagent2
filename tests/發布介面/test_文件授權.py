"""A23 backend docs：deterministic renderer、雙身份授權與唯讀 production wiring。"""
from __future__ import annotations

import json
import os
import hashlib
import sqlite3
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest
from fastapi import FastAPI, Request, Response
from fastapi.testclient import TestClient
from jsonschema import Draft202012Validator

from 繁中代理.使用者 import 使用者庫
from 繁中代理.發布介面.端點文件 import 端點文件投影, 渲染端點文件
from 繁中代理.發布介面.網頁工作階段 import 網頁使用者
from 繁中代理.發布介面.路由.文件 import 建立端點文件路由器
from 繁中代理.發布介面.資料庫 import 初始化發布介面資料庫
from 繁中代理.發布介面.憑證.儲存庫 import 註冊憑證SQLite函式
from 繁中代理.發布介面.憑證.服務 import SQLite憑證驗證服務, 憑證驗證結果, 憑證驗證狀態
from 繁中代理.發布介面.生產端點文件 import (
    SQLite端點文件服務, 延遲端點文件服務, 文件憑證未授權, 文件服務失敗,
)
from 繁中代理.發布介面.設定 import 生產設定
from 繁中代理.發布介面.生產Published執行 import Published生產設定, 生產Controller建構器
from 繁中代理.發布介面.生產組裝 import 建立生產應用程式


def _投影(**覆寫):
    值 = {
        "端點識別碼": "endpoint-public",
        "短名": "demo-agent",
        "版本": 3,
        "狀態": "active",
        "輸入綱要": {"type": "object", "additionalProperties": False, "properties": {"question": {"type": "string"}}, "required": ["question"]},
        "回應綱要": {"type": "object", "additionalProperties": False, "properties": {"answer": {"type": "string"}}, "required": ["answer"]},
        "端點請求上限": 60,
        "端點窗口秒數": 60,
    }
    值.update(覆寫)
    return 端點文件投影(**值)


def test_renderer產生exact_canonical_utf8_json且只有公開欄位():
    rendered = 渲染端點文件(_投影())
    assert type(rendered) is bytes and rendered.endswith(b"\n")
    assert rendered == 渲染端點文件(_投影())
    document = json.loads(rendered)
    assert list(document) == [
        "endpoint", "invoke_url", "authentication", "request_schema",
        "response_schema", "rate_limit", "examples", "errors",
    ]
    assert document["endpoint"] == {"id": "endpoint-public", "slug": "demo-agent", "version": 3, "status": "active"}
    assert document["invoke_url"] == "${BASE_URL}/v1/endpoints/${ENDPOINT_SLUG}/invoke"
    assert document["authentication"] == {"scheme": "bearer", "header": "Authorization"}
    assert document["request_schema"] == {
        "type": "object", "required": ["input"], "additionalProperties": False,
        "properties": {
            "input": _投影().輸入綱要,
            "session_id": {
                "anyOf": [{"type": "string", "maxLength": 128}, {"type": "null"}],
                "x-utf8-max-bytes": 128,
                "description": "Optional Published session identifier；上限 128 UTF-8 bytes。",
            },
            "metadata": {"anyOf": [{"type": "object"}, {"type": "null"}]},
        },
    }
    assert document["rate_limit"] == {"requests": 60, "window_seconds": 60}
    serial = rendered.decode("utf-8")
    for literal in ("${BASE_URL}", "${ENDPOINT_ID}", "${ENDPOINT_SLUG}", "${API_KEY}", "${SESSION_ID}"):
        assert literal in serial
    assert "urllib.request" in document["examples"]["python"]
    assert "requests" not in document["examples"]["python"]
    assert "${API_KEY}" in document["examples"]["curl"]
    assert "${API_KEY}" in document["examples"]["python"]
    assert "Bearer ***" not in document["examples"]["curl"]
    assert "cookie" not in serial.lower() and "csrf" not in serial.lower()


def test_renderer_errors與現有invoke公開contract一致():
    errors = {item["code"]: (item["status"], item["message"]) for item in json.loads(渲染端點文件(_投影()))["errors"]}
    assert errors == {
        "endpoint_not_found": (404, "找不到 endpoint slug。"),
        "invalid_api_key": (401, "API key 無效。"),
        "api_key_expired": (401, "API key 已過期。"),
        "endpoint_disabled": (403, "Endpoint 已停用。"),
        "endpoint_archived": (410, "Endpoint 已封存。"),
        "input_schema_invalid": (422, "Input 不符合 schema。"),
        "model_output_schema_invalid": (502, "模型輸出不符合 response schema。"),
        "rate_limit_exceeded": (429, "呼叫頻率超過限制。"),
        "model_timeout": (504, "模型供應商逾時。"),
        "tool_execution_failed": (502, "工具執行失敗。"),
        "tool_timeout": (504, "工具執行逾時。"),
        "endpoint_misconfigured": (500, "Endpoint 設定錯誤。"),
        "internal_error": (500, "伺服器內部錯誤。"),
    }


@pytest.mark.parametrize("欄位,綱要", [
    ("輸入綱要", []),
    ("回應綱要", {"type": float("nan")}),
])
def test_renderer拒絕malformed綱要(欄位, 綱要):
    with pytest.raises(ValueError, match="端點文件投影無效"):
        _投影(**{欄位: 綱要})


def test_renderer允許security語彙作合法schema欄位名稱():
    schema = {
        "type": "object",
        "description": "Caller supplies password and CSRF fields.",
        "properties": {
            "password": {"type": "string"},
            "api_key": {"type": "string"},
            "csrf_token": {"type": "string"},
        },
    }
    assert _投影(輸入綱要=schema).輸入綱要 == schema
    assert _投影(輸入綱要={
        "type": "string", "x-password-policy": "minimum 12 characters",
    }).輸入綱要["x-password-policy"] == "minimum 12 characters"


@pytest.mark.parametrize("敏感值", [
    "/Users/private/secret",
    "/etc/testagent2/production.env",
    "/usr/local/etc/production.env",
    "/Library/Application Support/TestAgent/secret.key",
    "C:\\ProgramData\\TestAgent\\secret.key",
    "config:/usr/local/etc/production.env",
    "config:/Library/Application Support/TestAgent/secret.key",
    "path:C:\\ProgramData\\TestAgent\\secret.key",
    "ghp_" + "A" * 36,
    "github_pat_" + "A" * 82,
    "glpat-" + "A" * 20,
    "AIza" + "A" * 35,
    "npm_" + "A" * 36,
    "sk-" + "A" * 40,
    "AKIA" + "A" * 16,
    "-----BEGIN PRIVATE KEY-----",
    "postgresql://user:pass@db.example/prod",
    "api_key=" + "A" * 24,
])
def test_renderer拒絕credential或filesystem_path值(敏感值):
    with pytest.raises(ValueError, match="端點文件投影無效"):
        _投影(輸入綱要={"type": "object", "description": 敏感值})


def test_renderer拒絕敏感property中的default_secret但允許其名稱():
    for secret_schema in (
        {"default": "hunter2"},
        {"default": 12_345_678},
        {"allOf": [{"default": "hunter2"}]},
        {"anyOf": [{"default": 12_345_678}]},
        {"oneOf": [{"const": "hunter2"}]},
        {"prefixItems": [{"examples": ["hunter2"]}]},
        {"properties": {"value": {"default": "hunter2"}}},
        {"items": [{"properties": {"value": {"default": "hunter2"}}}]},
    ):
        with pytest.raises(ValueError, match="端點文件投影無效"):
            _投影(輸入綱要={
                "type": "object", "properties": {"password": secret_schema},
            })


def test_renderer不把一般URL中的同名path_segment誤判為filesystem_path():
    schema = {"type": "string", "description": "https://docs.example/usr/local/reference"}
    assert _投影(輸入綱要=schema).輸入綱要 == schema


def test_renderer跨hashseed與TZ完全deterministic(tmp_path: Path):
    script = """
from 繁中代理.發布介面.端點文件 import 端點文件投影, 渲染端點文件
p=端點文件投影(端點識別碼='e',短名='s',版本=1,狀態='active',輸入綱要={'type':'object','properties':{'b':{'type':'number'},'a':{'type':'string'}}},回應綱要={'type':'object'},端點請求上限=7,端點窗口秒數=11)
import sys
sys.stdout.buffer.write(渲染端點文件(p))
"""
    outputs = []
    for seed, tz in (("1", "UTC"), ("987654", "Pacific/Honolulu")):
        env = dict(os.environ, PYTHONHASHSEED=seed, TZ=tz)
        outputs.append(subprocess.check_output([sys.executable, "-c", script], cwd=Path(__file__).parents[2], env=env))
    assert outputs[0] == outputs[1]


class _分類器:
    def __init__(self, result: 憑證驗證結果) -> None:
        self.result = result
        self.calls: list[tuple[str, str]] = []

    def 驗證(self, endpoint_id: str, presented_api_key: str) -> 憑證驗證結果:
        self.calls.append((endpoint_id, presented_api_key))
        return self.result


def _建立文件資料庫(tmp_path: Path, *, status="active"):
    tmp_path.mkdir(parents=True, exist_ok=True)
    db = (tmp_path / "published.sqlite3").resolve()
    初始化發布介面資料庫(db)
    connection = sqlite3.connect(db)
    connection.execute("PRAGMA foreign_keys=ON")
    connection.execute("INSERT INTO service_accounts VALUES('sa-docs',0,NULL)")
    connection.execute(
        "INSERT INTO published_endpoints(id,owner_user_id,service_account_id,slug,status,current_version_id,created_at,updated_at,rate_limit_requests,rate_limit_window_seconds) VALUES('endpoint-public','owner-a','sa-docs','demo-agent',?,NULL,0,0,60,60)",
        (status,),
    )
    connection.execute(
        "INSERT INTO published_endpoint_versions VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        ("version-public", "endpoint-public", 3, "req", "prompt", "[]", "[]", "{}", "rev", "{}", "{}", "{}",
         '{"additionalProperties":false,"properties":{"question":{"type":"string"}},"required":["question"],"type":"object"}',
         '{"additionalProperties":false,"properties":{"answer":{"type":"string"}},"required":["answer"],"type":"object"}',
         1, "owner-a", 0),
    )
    connection.execute("UPDATE published_endpoints SET current_version_id='version-public' WHERE id='endpoint-public'")
    connection.commit()
    return db, connection


def _有效分類(status="active", *, endpoint="endpoint-public", version="version-public", rate=60):
    return 憑證驗證結果(
        憑證驗證狀態.有效, "credential-public", endpoint, status, version, 10, rate, b"proof",
    )


def test_SQLite_provider_owner自己與admin可讀歷史狀態_foreign_missing同None(tmp_path: Path):
    db, connection = _建立文件資料庫(tmp_path, status="archived")
    provider = SQLite端點文件服務(db, _分類器(_有效分類("archived")))
    own = provider.讀取管理文件(端點識別碼="endpoint-public", 擁有者使用者識別碼="owner-a", 管理者=False)
    admin = provider.讀取管理文件(端點識別碼="endpoint-public", 擁有者使用者識別碼="admin", 管理者=True)
    assert own == admin and json.loads(own)["endpoint"]["status"] == "archived"
    assert provider.讀取管理文件(端點識別碼="endpoint-public", 擁有者使用者識別碼="owner-b", 管理者=False) is None
    assert provider.讀取管理文件(端點識別碼="missing", 擁有者使用者識別碼="owner-a", 管理者=False) is None
    connection.close()


def test_SQLite_provider_key使用既有classifier且所有身份失敗統一未授權(tmp_path: Path):
    db, connection = _建立文件資料庫(tmp_path)
    classifier = _分類器(_有效分類())
    provider = SQLite端點文件服務(db, classifier)
    body = provider.讀取金鑰文件(短名="demo-agent", API金鑰="pk_" + "A" * 43)
    assert json.loads(body)["endpoint"]["id"] == "endpoint-public"
    assert classifier.calls == [("endpoint-public", "pk_" + "A" * 43)]
    for result in (
        憑證驗證結果.invalid(),
        憑證驗證結果(憑證驗證狀態.已過期),
        憑證驗證結果(憑證驗證狀態.已撤銷),
    ):
        provider = SQLite端點文件服務(db, _分類器(result))
        with pytest.raises(文件憑證未授權, match="文件憑證未授權"):
            provider.讀取金鑰文件(短名="demo-agent", API金鑰="pk_" + "A" * 43)
    connection.close()
    for endpoint_status in ("disabled", "archived"):
        status_db, status_connection = _建立文件資料庫(tmp_path / endpoint_status, status=endpoint_status)
        provider = SQLite端點文件服務(status_db, _分類器(_有效分類(endpoint_status)))
        with pytest.raises(文件憑證未授權, match="文件憑證未授權"):
            provider.讀取金鑰文件(短名="demo-agent", API金鑰="pk_" + "A" * 43)
        status_connection.close()
    with pytest.raises(文件憑證未授權):
        SQLite端點文件服務(db, classifier).讀取金鑰文件(短名="missing", API金鑰="pk_" + "A" * 43)


@pytest.mark.parametrize("mismatch", [
    _有效分類(endpoint="other"), _有效分類(version="other"), _有效分類(rate=61),
])
def test_SQLite_provider交叉驗證classifier_snapshot_drift固定500(tmp_path: Path, mismatch):
    db, connection = _建立文件資料庫(tmp_path)
    provider = SQLite端點文件服務(db, _分類器(mismatch))
    with pytest.raises(文件服務失敗, match="端點文件服務失敗"):
        provider.讀取金鑰文件(短名="demo-agent", API金鑰="pk_" + "A" * 43)
    connection.close()


def test_SQLite_provider文件GET不造成任何資料庫寫入(tmp_path: Path):
    db, connection = _建立文件資料庫(tmp_path)
    before = connection.execute("SELECT total_changes(),data_version FROM pragma_data_version").fetchone()
    provider = SQLite端點文件服務(db, _分類器(_有效分類()))
    provider.讀取管理文件(端點識別碼="endpoint-public", 擁有者使用者識別碼="owner-a", 管理者=False)
    provider.讀取金鑰文件(短名="demo-agent", API金鑰="pk_" + "A" * 43)
    after = connection.execute("SELECT total_changes(),data_version FROM pragma_data_version").fetchone()
    assert before == after
    connection.close()


def test_provider_constructor_zero_io且proxy_generation_safe_ABA(tmp_path: Path, monkeypatch):
    target = (tmp_path / "does-not-exist.sqlite3").resolve()
    service = SQLite端點文件服務(target, _分類器(_有效分類()))
    assert not target.exists()
    proxy = 延遲端點文件服務()
    g1 = proxy.安裝(service)
    proxy.清除(service, g1)
    g2 = proxy.安裝(service)
    assert g2 != g1
    calls = []
    monkeypatch.setattr(
        service, "讀取管理文件", lambda **kwargs: calls.append(kwargs) or b"docs\n",
    )
    proxy.清除(service, g1)
    assert proxy.讀取管理文件(
        端點識別碼="x", 擁有者使用者識別碼="y", 管理者=False,
    ) == b"docs\n"
    assert len(calls) == 1
    proxy.清除(service, g2)
    with pytest.raises(文件服務失敗):
        proxy.讀取管理文件(端點識別碼="x", 擁有者使用者識別碼="y", 管理者=False)


def test_proxy兩個shutdown_caller皆等待active_lease(tmp_path: Path, monkeypatch):
    """同generation的concurrent clear共用drain，不能有caller提早返回。"""
    service = SQLite端點文件服務(
        (tmp_path / "missing.sqlite3").resolve(), _分類器(_有效分類()),
    )
    entered = threading.Event()
    release = threading.Event()
    def blocking_read(**_kwargs) -> bytes:
        entered.set()
        assert release.wait(2)
        return b"docs\n"
    monkeypatch.setattr(service, "讀取管理文件", blocking_read)
    proxy = 延遲端點文件服務()
    generation = proxy.安裝(service)
    request = threading.Thread(target=lambda: proxy.讀取管理文件(
        端點識別碼="x", 擁有者使用者識別碼="y", 管理者=False,
    ))
    request.start()
    assert entered.wait(1)
    finished = []
    clearers = [threading.Thread(
        target=lambda index=index: (proxy.清除(service, generation), finished.append(index)),
    ) for index in range(2)]
    for clearer in clearers:
        clearer.start()
    assert finished == []
    release.set()
    request.join(2)
    for clearer in clearers:
        clearer.join(2)
    assert not request.is_alive() and all(not clearer.is_alive() for clearer in clearers)
    assert sorted(finished) == [0, 1]


class _HTTP服務:
    def __init__(self):
        self.management = []
        self.keys = []
        self.management_result = 渲染端點文件(_投影())
        self.key_result = 渲染端點文件(_投影())

    def 讀取管理文件(self, **kwargs):
        self.management.append(kwargs)
        return self.management_result

    def 讀取金鑰文件(self, **kwargs):
        self.keys.append(kwargs)
        return self.key_result


def _HTTP應用(service, principal=網頁使用者("owner-a", "owner", "member")):
    app = FastAPI()
    dependency = lambda: principal
    routers = 建立端點文件路由器(service, dependency)
    for router in routers:
        app.include_router(router)
    return app, dependency


def test_HTTP_owner與admin共用canonical_session且零CSRF_foreign_missing固定404():
    service = _HTTP服務()
    app, dependency = _HTTP應用(service)
    route = next(route for route in app.routes if getattr(route, "path", "") == "/api/published-endpoints/{endpoint_id}/docs")
    assert [item.call for item in route.dependant.dependencies] == [dependency]
    with TestClient(app) as client:
        response = client.get("/api/published-endpoints/endpoint-public/docs")
        assert response.status_code == 200 and response.content.endswith(b"\n")
    assert service.management == [{"端點識別碼": "endpoint-public", "擁有者使用者識別碼": "owner-a", "管理者": False}]

    admin_service = _HTTP服務()
    admin_app, _ = _HTTP應用(admin_service, 網頁使用者("admin-a", "admin", "admin"))
    with TestClient(admin_app) as client:
        assert client.get("/api/published-endpoints/endpoint-public/docs").status_code == 200
    assert admin_service.management[0]["管理者"] is True

    for result in (None,):
        denied = _HTTP服務(); denied.management_result = result
        denied_app, _ = _HTTP應用(denied)
        with TestClient(denied_app) as client:
            assert client.get("/api/published-endpoints/foreign/docs").status_code == 404


def test_HTTP_owner文件轉貼session_recovery_successor_header():
    service = _HTTP服務()
    app = FastAPI()

    def recovery_session(_request: Request, response: Response):
        response.headers["X-CSRF-Token"] = "successor-token"
        return 網頁使用者("owner-a", "owner", "member")

    management, public = 建立端點文件路由器(service, recovery_session)
    app.include_router(management)
    app.include_router(public)
    with TestClient(app) as client:
        response = client.get("/api/published-endpoints/endpoint-public/docs")
    assert response.status_code == 200
    assert response.headers["X-CSRF-Token"] == "successor-token"


def test_HTTP文件final_release拒絕非canonical_renderer_bytes():
    service = _HTTP服務()
    service.management_result = b"null\n"
    service.key_result = b"null\n"
    app, _ = _HTTP應用(service)
    bearer = "Bearer pk_" + "A" * 43
    with TestClient(app) as client:
        management = client.get("/api/published-endpoints/endpoint-public/docs")
        public = client.get("/v1/endpoints/demo-agent/docs", headers={"Authorization": bearer})
    assert management.status_code == 500
    assert management.json() == {"detail": {"code": "docs_unavailable"}}
    assert public.status_code == 500
    assert public.json() == {"detail": {"code": "docs_unavailable"}}


def test_HTTP_owner文件OpenAPI拒絕只有頂層八鍵的非canonical形狀():
    service = _HTTP服務()
    app, _ = _HTTP應用(service)
    schema = app.openapi()["paths"]["/api/published-endpoints/{endpoint_id}/docs"]["get"]["responses"]["200"]["content"]["application/json"]["schema"]
    invalid = {
        "endpoint": {}, "invoke_url": {}, "authentication": {}, "request_schema": {},
        "response_schema": {}, "rate_limit": {}, "examples": {}, "errors": {},
    }
    Draft202012Validator(schema).validate(json.loads(service.management_result))
    assert list(Draft202012Validator(schema).iter_errors(invalid))


@pytest.mark.parametrize("欄位", ["session_id", "metadata"])
def test_HTTP_owner文件OpenAPI拒絕非canonical固定request子綱要(欄位):
    service = _HTTP服務()
    app, _ = _HTTP應用(service)
    schema = app.openapi()["paths"]["/api/published-endpoints/{endpoint_id}/docs"]["get"]["responses"]["200"]["content"]["application/json"]["schema"]
    document = json.loads(service.management_result)
    document["request_schema"]["properties"][欄位] = {}
    assert list(Draft202012Validator(schema).iter_errors(document))


@pytest.mark.parametrize("欄位", ["curl", "python"])
def test_HTTP_owner文件OpenAPI拒絕非canonical固定example(欄位):
    service = _HTTP服務()
    app, _ = _HTTP應用(service)
    schema = app.openapi()["paths"]["/api/published-endpoints/{endpoint_id}/docs"]["get"]["responses"]["200"]["content"]["application/json"]["schema"]
    document = json.loads(service.management_result)
    document["examples"][欄位] = "not-canonical"
    assert list(Draft202012Validator(schema).iter_errors(document))


def test_HTTP_key只接受單一Bearer_header_query_cookie替代皆固定401():
    service = _HTTP服務()
    app, _ = _HTTP應用(service)
    valid = "Bearer pk_" + "A" * 43
    with TestClient(app) as client:
        ok = client.get("/v1/endpoints/demo-agent/docs", headers={"Authorization": valid})
        assert ok.status_code == 200
        client.cookies.set("api_key", "pk_" + "A" * 43)
        cookie_only = client.get("/v1/endpoints/demo-agent/docs")
        client.cookies.clear()
        denied = (
            client.get("/v1/endpoints/demo-agent/docs"),
            client.get("/v1/endpoints/demo-agent/docs?api_key=pk_" + "A" * 43),
            cookie_only,
            client.get("/v1/endpoints/demo-agent/docs", headers={"Authorization": "Basic abc"}),
        )
        assert [response.status_code for response in denied] == [401, 401, 401, 401]
    # ASGI raw duplicate header must not be normalized into a valid single authority.
    async def duplicate_receive():
        return {"type": "http.request", "body": b"", "more_body": False}
    sent = []
    async def capture_send(message):
        sent.append(message)
    scope = {"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1", "method": "GET",
             "scheme": "http", "path": "/v1/endpoints/demo-agent/docs", "raw_path": b"/v1/endpoints/demo-agent/docs",
             "query_string": b"", "headers": [(b"authorization", valid.encode()), (b"authorization", valid.encode())],
             "client": ("test", 1), "server": ("test", 80), "root_path": ""}
    import asyncio
    asyncio.run(app(scope, duplicate_receive, capture_send))
    assert next(message for message in sent if message["type"] == "http.response.start")["status"] == 401


def test_HTTP_key_denials與operational_corruption映射fixed_status():
    service = _HTTP服務()
    app, _ = _HTTP應用(service)
    header = {"Authorization": "Bearer pk_" + "A" * 43}
    service.key_result = None
    def unauthorized(**kwargs):
        raise 文件憑證未授權("marker-secret")
    service.讀取金鑰文件 = unauthorized
    with TestClient(app, raise_server_exceptions=False) as client:
        denied = client.get("/v1/endpoints/demo-agent/docs", headers=header)
        assert denied.status_code == 401 and "marker-secret" not in denied.text
    def broken(**kwargs):
        raise 文件服務失敗("marker-operational")
    service.讀取金鑰文件 = broken
    with TestClient(app, raise_server_exceptions=False) as client:
        failed = client.get("/v1/endpoints/demo-agent/docs", headers=header)
        assert failed.status_code == 500 and "marker-operational" not in failed.text


def test_canonical_lifespan真session與Bearer讀同一文件且shutdown撤銷proxy(tmp_path: Path):
    """正式CP4組裝啟動真SQLite authority，兩種身份讀同一bytes，結束後proxy fail closed。"""
    web_db = tmp_path / "web.sqlite3"
    published_db = tmp_path / "published.sqlite3"
    bundle_root = tmp_path / "bundles"
    bundle_root.mkdir()
    users = 使用者庫(web_db)
    users.建立使用者("docs-owner", "correct horse battery", roles=["member"])
    owner_id = users.連線.execute(
        "SELECT id FROM users WHERE username='docs-owner'"
    ).fetchone()[0]
    users.連線.close()
    web = 生產設定(
        web_db, ("http://localhost:5173",), "fake", "fake",
        Cookie安全=False, 工作階段有效秒數=3600,
    )
    published = Published生產設定(
        published_db, bundle_root, lambda _tools: None, lambda: {"fake": object()}, 60.0,
    )
    builder = 生產Controller建構器(published)
    app = 建立生產應用程式(web, builder)
    raw_key = "pk_" + "A" * 43
    created_at = time.time()

    paths = app.openapi()["paths"]
    assert tuple(paths["/api/published-endpoints/{endpoint_id}/docs"]) == ("get",)
    assert tuple(paths["/v1/endpoints/{slug}/docs"]) == ("get",)

    with TestClient(app) as client:
        connection = sqlite3.connect(published_db)
        註冊憑證SQLite函式(connection)
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("INSERT INTO service_accounts VALUES('sa-docs',0,NULL)")
        connection.execute(
            "INSERT INTO published_endpoints(id,owner_user_id,service_account_id,slug,status,current_version_id,created_at,updated_at,rate_limit_requests,rate_limit_window_seconds) "
            "VALUES('endpoint-public',?,'sa-docs','demo-agent','active',NULL,0,0,60,60)",
            (owner_id,),
        )
        connection.execute(
            "INSERT INTO published_endpoint_versions VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            ("version-public", "endpoint-public", 3, "req", "prompt", "[]", "[]", "{}", "rev", "{}", "{}", "{}",
             '{"additionalProperties":false,"properties":{"question":{"type":"string"}},"required":["question"],"type":"object"}',
             '{"additionalProperties":false,"properties":{"answer":{"type":"string"}},"required":["answer"],"type":"object"}',
             1, owner_id, 0),
        )
        connection.execute(
            "UPDATE published_endpoints SET current_version_id='version-public' WHERE id='endpoint-public'"
        )
        connection.execute(
            "INSERT INTO endpoint_credentials(id,endpoint_id,name,purpose,key_version,key_nonce,key_ciphertext,key_hash,key_prefix,key_last4,expires_at,last_used_at,created_at,updated_at,revoked_at,ip_allowlist_json,rate_limit_requests,created_by_user_id,revision) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            ("credential-docs", "endpoint-public", "Docs credential", "Read endpoint docs", 1,
             b"N" * 12, b"C" * 62, hashlib.sha256(raw_key.encode("ascii")).hexdigest(),
             raw_key[:12], raw_key[-4:], 4_000_000_000, None, created_at, created_at,
             None, "[]", 10, owner_id, 0),
        )
        connection.commit()
        assert SQLite憑證驗證服務(published_db).驗證(
            "endpoint-public", raw_key,
        ).status is 憑證驗證狀態.有效
        before = connection.execute(
            "SELECT last_used_at,updated_at FROM endpoint_credentials WHERE id='credential-docs'"
        ).fetchone()

        login = client.post(
            "/api/auth/login", json={"username": "docs-owner", "password": "correct horse battery"},
        )
        assert login.status_code == 200
        owner = client.get("/api/published-endpoints/endpoint-public/docs")
        key_holder = client.get(
            "/v1/endpoints/demo-agent/docs", headers={"Authorization": f"Bearer {raw_key}"},
        )
        assert owner.status_code == key_holder.status_code == 200
        assert owner.content == key_holder.content
        assert connection.execute(
            "SELECT last_used_at,updated_at FROM endpoint_credentials WHERE id='credential-docs'"
        ).fetchone() == before
        connection.close()

    with pytest.raises(文件服務失敗, match="端點文件服務失敗"):
        builder._Published._端點文件代理.讀取管理文件(
            端點識別碼="endpoint-public", 擁有者使用者識別碼=owner_id, 管理者=False,
        )
