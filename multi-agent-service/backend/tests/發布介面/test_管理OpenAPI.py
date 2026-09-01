"""Management API runtime authority與OpenAPI契約雙向一致。"""
from __future__ import annotations

import os
import sys
from pathlib import Path

from fastapi import Depends, FastAPI
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient
from 繁中代理.使用者 import 使用者庫
from 繁中代理.發布介面.OpenAPI相依權限 import _登錄Canonical相依封裝, 讀取Canonical相依封裝
from 繁中代理.發布介面.管理OpenAPI import 套用ManagementOpenAPI
from 繁中代理.發布介面.路由.網頁認證 import 是模組目前工作階段相依項

sys.path.insert(0, str(Path(__file__).resolve().parent))
import asgi
from test_CP4_CanonicalASGI整合 import _設定Canonical環境


_COOKIE_ONLY = {
    ("GET", "/api/published-endpoints"),
    ("GET", "/api/published-endpoints/{endpoint_id}"),
    ("GET", "/api/published-endpoints/{endpoint_id}/credentials"),
    ("GET", "/api/published-endpoints/{endpoint_id}/docs"),
    ("GET", "/api/published-endpoints/{endpoint_id}/metrics"),
    ("GET", "/api/published-endpoints/{endpoint_id}/diagnostics"),
    ("GET", "/api/admin/endpoints/{endpoint_id}/invocations"),
    ("GET", "/api/admin/endpoints/{endpoint_id}/invocations/{invocation_id}"),
}
_COOKIE_CSRF = {
    ("POST", "/api/published-endpoints/draft"),
    ("POST", "/api/published-endpoints"),
    ("POST", "/api/published-endpoints/{endpoint_id}/versions"),
    ("POST", "/api/published-endpoints/{endpoint_id}/credentials"),
    ("POST", "/api/published-endpoints/{endpoint_id}/credentials/{credential_id}/revoke"),
    ("POST", "/api/admin/published-endpoints/{endpoint_id}/invocations/{invocation_id}/redactions"),
}


class _環境:
    def setenv(self, key, value):
        os.environ[key] = value
    def delenv(self, key, raising=False):
        os.environ.pop(key, None)


def _建立(tmp_path):
    _設定Canonical環境(tmp_path, _環境())
    app = asgi.建立應用程式()
    return app, app.openapi()


def test_management_OpenAPI以dependency_identity產生exact_AND_security(tmp_path):
    app, spec = _建立(tmp_path)
    assert spec["components"]["securitySchemes"] == {
        "WebSessionCookie": {"type": "apiKey", "in": "cookie", "name": "published_web_session"},
        "WebCSRFHeader": {"type": "apiKey", "in": "header", "name": "X-CSRF-Token"},
    }
    assert len(_COOKIE_ONLY | _COOKIE_CSRF) == 14
    for method, path in _COOKIE_ONLY:
        assert spec["paths"][path][method.lower()]["security"] == [{"WebSessionCookie": []}]
    for method, path in _COOKIE_CSRF:
        operation = spec["paths"][path][method.lower()]
        assert operation["security"] == [{"WebSessionCookie": [], "WebCSRFHeader": []}]
        parameters = {(item["in"], item["name"]): item for item in operation.get("parameters", [])}
        assert parameters[("header", "X-CSRF-Token")]["required"] is True

    routes = {(next(iter(route.methods)), route.path): route for route in app.routes
              if isinstance(route, APIRoute) and route.include_in_schema}
    assert (_COOKIE_ONLY | _COOKIE_CSRF) <= set(routes)


def test_management_OpenAPI文件化successor_header(tmp_path):
    _, spec = _建立(tmp_path)
    for method, path in _COOKIE_ONLY | _COOKIE_CSRF:
        responses = spec["paths"][path][method.lower()]["responses"]
        for status, response in responses.items():
            if method == "POST":
                should_have = status != "401"
                if status == "403":
                    should_have = path in {
                        "/api/published-endpoints/draft", "/api/published-endpoints",
                        "/api/published-endpoints/{endpoint_id}/versions",
                    }
                if status == "503":
                    should_have = path == "/api/published-endpoints/draft"
                assert ("X-CSRF-Token" in response.get("headers", {})) is should_have, (method, path, status)
            if method == "GET" and status.startswith("2"):
                assert "X-CSRF-Token" in response.get("headers", {}), (method, path, status)
    publish_503 = spec["paths"]["/api/published-endpoints/draft"]["post"]["responses"]["503"]
    description = publish_503["headers"]["X-CSRF-Token"]["description"]
    assert "post-consumption" in description and "pre-consumption auth_unavailable" in description
    publish_403 = spec["paths"]["/api/published-endpoints/draft"]["post"]["responses"]["403"]
    description = publish_403["headers"]["X-CSRF-Token"]["description"]
    assert "planning_not_authorized" in description and "pre-consumption csrf_invalid" in description


def test_management_OpenAPI_status與response_schema完整(tmp_path):
    _, spec = _建立(tmp_path)
    expected = {
        ("POST", "/api/published-endpoints/draft"): {201, 401, 403, 422, 500, 502, 503},
        ("POST", "/api/published-endpoints"): {201, 401, 403, 404, 409, 422, 500, 503},
        ("POST", "/api/published-endpoints/{endpoint_id}/versions"): {201, 401, 403, 404, 409, 422, 500, 503},
        ("GET", "/api/published-endpoints"): {200, 401, 403, 422, 500, 503},
        ("GET", "/api/published-endpoints/{endpoint_id}"): {200, 401, 404, 422, 500, 503},
        ("GET", "/api/published-endpoints/{endpoint_id}/docs"): {200, 401, 404, 422, 500, 503},
    }
    for (method, path), statuses in expected.items():
        responses = spec["paths"][path][method.lower()]["responses"]
        assert set(map(int, responses)) == statuses
    for method, path in _COOKIE_ONLY | _COOKIE_CSRF:
        for status, response in spec["paths"][path][method.lower()]["responses"].items():
            if status == "204":
                continue
            schema = response.get("content", {}).get("application/json", {}).get("schema")
            assert type(schema) is dict and schema, (method, path, status)


def test_path驗證若發生在CSRF消耗後仍交付successor(tmp_path):
    app, _ = _建立(tmp_path)
    users = 使用者庫(tmp_path / "web.sqlite3")
    try:
        users.建立使用者("alice", "correct horse")
    finally:
        users.連線.close()
    with TestClient(app, base_url="https://client.example", raise_server_exceptions=False) as client:
        login = client.post(
            "/api/auth/login", json={"username": "alice", "password": "correct horse"},
        )
        assert login.status_code == 200
        original = login.json()["csrf_token"]
        response = client.post(
            "/api/published-endpoints/bad%21/versions",
            headers={"X-CSRF-Token": original},
            json={"configuration": {}},
        )
        assert response.status_code == 422
        successor = response.headers.get("X-CSRF-Token")
        assert type(successor) is str and successor and successor != original
    schema = app.openapi()["paths"]["/api/published-endpoints/{endpoint_id}/versions"]["post"]["responses"]["422"]["content"]["application/json"]["schema"]
    assert {"$ref": "#/components/schemas/HTTPValidationError"} in schema["anyOf"]


def test_foreign_canonical_attribute不得偽造session權限(tmp_path):
    canonical, _ = _建立(tmp_path)
    docs_route = next(
        route for route in canonical.routes
        if isinstance(route, APIRoute) and route.path == "/api/published-endpoints/{endpoint_id}/docs"
    )
    current = next(
        dependency.call for dependency in docs_route.dependant.dependencies
        if 是模組目前工作階段相依項(dependency.call)
    )

    def spoof():
        return None

    setattr(spoof, "__canonical_dependency__", current)
    hostile = FastAPI()

    @hostile.get("/api/published-endpoints/spoof", dependencies=[Depends(spoof)])
    def fake_management_route():
        return {"ok": True}

    套用ManagementOpenAPI(hostile)
    operation = hostile.openapi()["paths"]["/api/published-endpoints/spoof"]["get"]
    assert "security" not in operation


def test_hostile_hash與equality不得冒充已登錄wrapper():
    def canonical():
        return None

    def wrapper():
        return None

    _登錄Canonical相依封裝(wrapper, canonical)

    class Hostile:
        def __call__(self):
            return None

        def __hash__(self):
            return hash(wrapper)

        def __eq__(self, other):
            return other is wrapper

    assert 讀取Canonical相依封裝(Hostile()) is None
    assert 讀取Canonical相依封裝(wrapper) is canonical
