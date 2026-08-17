"""A20-04 deep governance Admin irreversible redaction route contract tests。"""
import asyncio
import itertools
import json
import os
import sqlite3
from dataclasses import replace
from pathlib import Path

import pytest
from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from 繁中代理.使用者 import 使用者庫
from 繁中代理.發布介面.資料庫 import 初始化發布介面資料庫
from 繁中代理.發布介面.治理.管理遮蔽治理 import (
    管理遮蔽授權,
    管理遮蔽收據,
    管理遮蔽治理權限,
    是管理遮蔽CSRF相依項,
)
from 繁中代理.發布介面.治理.遮蔽 import SQLite不可逆遮蔽服務
from 繁中代理.發布介面.治理.遮蔽命令 import SQLite遮蔽命令服務
from 繁中代理.發布介面.設定 import 網頁安全設定
from 繁中代理.發布介面.網頁工作階段 import (
    網頁使用者,
    網頁工作階段服務,
    網頁認證不可用,
)
from 繁中代理.發布介面.路由.管理遮蔽 import 建立管理遮蔽路由器
from 繁中代理.發布介面.路由.網頁認證 import 建立SQLite帳密驗證器, 建立網頁認證路由器

路徑 = "/api/admin/published-endpoints/endpoint-1/invocations/invocation-1/redactions"
本文 = {
    "target_type": "tool_result",
    "target_row_id": "tool-call-1",
    "json_path": "/result/secret",
    "reason": "approved privacy request",
}


def _建立認證資料庫(path: Path) -> None:
    users = 使用者庫(path)
    users.建立使用者("alice", "correct horse", roles=["admin"])
    users.建立使用者("bob", "member password", roles=[])
    migration = (
        Path(__file__).parents[2]
        / "繁中代理/發布介面/遷移/0005_建立網頁工作階段.sql"
    ).read_text()
    users.連線.executescript(migration)
    users.連線.close()


def _建立資料庫(path: Path) -> None:
    初始化發布介面資料庫(path)
    with sqlite3.connect(path) as db:
        db.execute("PRAGMA foreign_keys=ON")
        db.execute("INSERT INTO service_accounts(id,created_at) VALUES('service-1',0)")
        db.execute(
            "INSERT INTO published_endpoints(id,owner_user_id,service_account_id,slug,status,created_at,updated_at) "
            "VALUES('endpoint-1','owner-1','service-1','slug','active',0,0)"
        )
        db.execute(
            "INSERT INTO published_endpoint_versions(id,endpoint_id,version_number,original_requirement_text,"
            "system_prompt,allowed_skills_json,allowed_tools_json,tool_schema_snapshot_json,tool_runtime_revision,"
            "model_config_snapshot_json,retry_policy_json,skill_bundle_manifest_json,input_schema_json,"
            "response_schema_json,schema_changed,created_by_user_id,created_at) "
            "VALUES('version-1','endpoint-1',1,'r','p','[]','[]','{}','runtime','{}','{}','{}',NULL,'{}',0,'owner-1',0)"
        )
        db.execute("UPDATE published_endpoints SET current_version_id='version-1' WHERE id='endpoint-1'")
        db.execute(
            "INSERT INTO endpoint_invocations(id,endpoint_id,endpoint_version_id,request_id,status,input_json,"
            "created_at) VALUES('invocation-1','endpoint-1','version-1','request-1','succeeded','{}',0)"
        )
        db.execute(
            "INSERT INTO endpoint_tool_calls(id,invocation_id,sequence_number,tool_name,arguments_json,outcome,"
            "result_json,created_at) VALUES('tool-call-1','invocation-1',1,'tool','{}','success',"
            "'{\"result\":{\"secret\":\"RAW_A20_ROUTE\"}}',0)"
        )


def _建立客戶端(
    tmp_path: Path,
    *,
    安裝: bool = True,
    傳遞伺服器例外: bool = False,
):
    認證路徑 = tmp_path / "auth.sqlite3"
    path = tmp_path / "published.sqlite3"
    _建立認證資料庫(認證路徑)
    _建立資料庫(path)
    設定 = 網頁安全設定(("http://localhost:5173",), Cookie安全=False, 工作階段有效秒數=60)
    sessions = 網頁工作階段服務(認證路徑, 有效秒數=60)
    authority = 管理遮蔽治理權限(sessions, 設定)
    if 安裝:
        遮蔽序號 = itertools.count(1)
        稽核序號 = itertools.count(1)
        請求序號 = itertools.count(1)
        authority.安裝(
            SQLite不可逆遮蔽服務(os.path.realpath(path)),
            SQLite遮蔽命令服務(
                遮蔽識別碼工廠=lambda: f"redaction-{next(遮蔽序號)}",
                稽核事件識別碼工廠=lambda: f"audit-{next(稽核序號)}",
                請求識別碼工廠=lambda: f"request-redaction-{next(請求序號)}",
                時鐘=lambda: 100.0,
            ),
        )
    router = 建立管理遮蔽路由器(authority)
    app = FastAPI(redirect_slashes=False)
    app.include_router(建立網頁認證路由器(
        sessions, 建立SQLite帳密驗證器(認證路徑), 設定=設定,
    ))
    app.include_router(router)
    return TestClient(
        app,
        raise_server_exceptions=傳遞伺服器例外,
    ), router, authority, path


def _登入(client: TestClient, username="alice", password="correct horse") -> str:
    response = client.post("/api/auth/login", json={"username": username, "password": password})
    assert response.status_code == 200
    return response.json()["csrf_token"]


def _post(client: TestClient, csrf: str, *, key="idem-1", body=None, path=路徑):
    return client.post(
        path,
        headers={"Idempotency-Key": key, "X-CSRF-Token": csrf},
        json=本文 if body is None else body,
    )


def test_A20_04_exact_route只接受一個module_owned_authority與嚴格OpenAPI(tmp_path):
    client, router, authority, _ = _建立客戶端(tmp_path)
    assert router.prefix == "/api/admin"
    route = router.routes[0]
    assert type(route) is APIRoute
    assert route.path == "/api/admin/published-endpoints/{endpoint_id}/invocations/{invocation_id}/redactions"
    assert route.methods == {"POST"}
    assert len(route.dependant.dependencies) == 1
    assert 是管理遮蔽CSRF相依項(route.dependant.dependencies[0].call, authority)

    spec = client.app.openapi()["paths"][route.path]["post"]
    assert set(spec["responses"]) == {"200", "400", "401", "403", "404", "409", "422", "500", "503"}
    expected = {
        "400": {"invalid_request"}, "401": {"unauthorized"},
        "403": {"admin_required", "csrf_invalid"}, "404": {"invocation_not_found"},
        "409": {"idempotency_conflict", "redaction_conflict"},
        "422": {"redaction_validation_failed"}, "500": {"redaction_failed"},
        "503": {"auth_unavailable"},
    }
    for status, codes in expected.items():
        schema = spec["responses"][status]["content"]["application/json"]["schema"]
        assert set(schema["properties"]["detail"]["properties"]["code"]["enum"]) == codes
    body_schema = spec["requestBody"]["content"]["application/json"]["schema"]
    assert body_schema["additionalProperties"] is False
    assert set(body_schema["properties"]) == {"target_type", "target_row_id", "json_path", "reason"}
    parameters = {(item["name"], item["in"]): item for item in spec["parameters"]}
    assert parameters[("Idempotency-Key", "header")]["required"] is True
    assert parameters[("X-CSRF-Token", "header")]["required"] is True
    assert parameters[("X-CSRF-Token", "header")]["schema"] == {
        "type": "string", "minLength": 32, "maxLength": 512,
        "pattern": "^[A-Za-z0-9_\\-]+$",
    }

    with client:
        csrf = _登入(client)
        response = _post(client, csrf)
    assert response.status_code == 200
    assert response.json() == {
        "redaction_id": "redaction-1", "invocation_id": "invocation-1",
        "target_type": "tool_result", "target_row_id": "tool-call-1",
        "json_path": "/result/secret", "original_sha256": response.json()["original_sha256"],
        "reason": "approved privacy request", "actor": {"type": "admin", "id": response.json()["actor"]["id"]},
        "audit_event_id": "audit-1", "is_tombstone": True, "redacted_at": 100.0,
    }
    assert len(response.json()["original_sha256"]) == 64


def test_A20_04_route建構拒絕duck_typed_provider():
    class 偽治理:
        def 執行(self, *_a, **_k):
            return None
    with pytest.raises(ValueError, match="^管理遮蔽路由設定無效$"):
        建立管理遮蔽路由器(偽治理())


@pytest.mark.parametrize("duplicate", [
    [("Content-Type", "application/json"), ("Content-Type", "application/json")],
    [("Content-Length", "143"), ("Content-Length", "143")],
    [("Idempotency-Key", "idem-1"), ("Idempotency-Key", "idem-1")],
])
def test_A20_04_raw_duplicate_headers固定400且零治理mutation(tmp_path, duplicate):
    client, _, _, path = _建立客戶端(tmp_path)
    with client:
        csrf = _登入(client)
        headers = [("Idempotency-Key", "idem-1"), ("Content-Type", "application/json"),
                   ("X-CSRF-Token", csrf)]
        name = duplicate[0][0].lower()
        headers = [item for item in headers if item[0].lower() != name] + duplicate
        response = client.post(路徑, headers=headers, content=json.dumps(本文).encode())
    assert response.status_code == 400
    assert response.json() == {"detail": {"code": "invalid_request"}}
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT count(*) FROM endpoint_redactions").fetchone() == (0,)


def test_A20_04超長Content_Length固定400且零治理mutation(tmp_path):
    client, _, _, path = _建立客戶端(tmp_path)
    with client:
        csrf = _登入(client)
        response = client.post(
            路徑,
            headers=[
                ("Idempotency-Key", "idem-1"),
                ("Content-Type", "application/json"),
                ("Content-Length", "9" * 5000),
                ("X-CSRF-Token", csrf),
            ],
            content=json.dumps(本文).encode(),
        )
    assert response.status_code == 400
    assert response.json() == {"detail": {"code": "invalid_request"}}
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT count(*) FROM endpoint_redactions").fetchone() == (0,)


@pytest.mark.parametrize("額外標頭", [
    [(b"content-length", b"1")],
    [(b"content-length", b"ACTUAL"), (b"transfer-encoding", b"chunked")],
])
def test_A20_04_declared_streamed_length不符或TE加CL固定400且零mutation(tmp_path, 額外標頭):
    _, router, _, path = _建立客戶端(tmp_path)
    route = router.routes[0]
    assert type(route) is APIRoute
    body = json.dumps(本文).encode()
    headers = [
        (b"content-type", b"application/json"),
        (b"idempotency-key", b"idem-framing"),
    ] + [(name, str(len(body)).encode() if value == b"ACTUAL" else value) for name, value in 額外標頭]
    sent = False

    async def receive():
        nonlocal sent
        if sent:
            return {"type": "http.disconnect"}
        sent = True
        return {"type": "http.request", "body": body, "more_body": False}

    request = Request({
        "type": "http", "method": "POST", "path": 路徑,
        "query_string": b"", "headers": headers,
    }, receive)

    async def run():
        with pytest.raises(HTTPException) as caught:
            await route.endpoint(
                request, "endpoint-1", "invocation-1", Response(),
                管理遮蔽授權(網頁使用者("admin-1", "alice", "admin")),
            )
        assert caught.value.status_code == 400
        assert caught.value.detail == {"code": "invalid_request"}

    asyncio.run(run())
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT count(*) FROM endpoint_redactions").fetchone() == (0,)


def test_A20_04_existing_target但JSON_pointer不存在固定422且整筆回滾(tmp_path):
    client, _, _, path = _建立客戶端(tmp_path)
    with client:
        csrf = _登入(client)
        response = _post(client, csrf, body={**本文, "json_path": "/result/missing"})
    assert response.status_code == 422
    assert response.json() == {"detail": {"code": "redaction_validation_failed"}}
    assert response.headers["X-CSRF-Token"]
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT count(*) FROM redaction_idempotency_commands").fetchone() == (0,)
        assert db.execute("SELECT count(*) FROM endpoint_redactions").fetchone() == (0,)
        assert db.execute("SELECT count(*) FROM audit_events").fetchone() == (0,)


def test_A20_04_auth與CSRF_runtime_codes精確可達(monkeypatch, tmp_path):
    client, _, _, _ = _建立客戶端(tmp_path)
    with client:
        missing = _post(client, "x" * 32)
        csrf = _登入(client)
        invalid = _post(client, "x" * 32)
        original = 網頁工作階段服務.驗證身份

        def unavailable(self, token):
            del self, token
            raise 網頁認證不可用("auth_unavailable")

        monkeypatch.setattr(網頁工作階段服務, "驗證身份", unavailable)
        unavailable_response = _post(client, csrf)
        monkeypatch.setattr(網頁工作階段服務, "驗證身份", original)
    assert missing.status_code == 401
    assert missing.json() == {"detail": {"code": "unauthorized"}}
    assert invalid.status_code == 403
    assert invalid.json() == {"detail": {"code": "csrf_invalid"}}
    assert unavailable_response.status_code == 503
    assert unavailable_response.json() == {"detail": {"code": "auth_unavailable"}}


def test_A20_04_principal_drift消耗後固定500且保留successor(monkeypatch, tmp_path):
    client, _, _, _ = _建立客戶端(tmp_path)
    original = 網頁工作階段服務.輪替

    def drift(self, token, csrf):
        result = original(self, token, csrf)
        return replace(result, 使用者=網頁使用者(result.使用者.識別碼, "changed", "admin"))

    monkeypatch.setattr(網頁工作階段服務, "輪替", drift)
    with client:
        csrf = _登入(client)
        response = _post(client, csrf)
    assert response.status_code == 500
    assert response.json() == {"detail": {"code": "redaction_failed"}}
    assert response.headers["X-CSRF-Token"]
    assert "published_web_csrf=" in response.headers["set-cookie"]


@pytest.mark.parametrize("field", [
    "is_admin", "actor", "actor_id", "redaction_id", "audit_event_id",
    "request_id", "redacted_at", "original_sha256", "original_value",
])
def test_A20_04_body不能claim_authority或internal_fields(tmp_path, field):
    client, _, _, _ = _建立客戶端(tmp_path)
    with client:
        csrf = _登入(client)
        response = _post(client, csrf, body={**本文, field: "FORGED"})
    assert response.status_code == 422
    assert response.json() == {"detail": {"code": "redaction_validation_failed"}}
    assert "FORGED" not in response.text
    assert response.headers["X-CSRF-Token"]


def test_A20_04_non_admin_bad_CSRF仍admin_first且不輪替(tmp_path):
    client, _, _, _ = _建立客戶端(tmp_path)
    認證路徑 = tmp_path / "auth.sqlite3"
    with client:
        csrf = _登入(client, "bob", "member password")
        with sqlite3.connect(認證路徑) as db:
            before = db.execute("SELECT csrf_token_hash FROM web_sessions WHERE revoked_at IS NULL").fetchone()[0]
        response = _post(client, "x" * 32)
        with sqlite3.connect(認證路徑) as db:
            after = db.execute("SELECT csrf_token_hash FROM web_sessions WHERE revoked_at IS NULL").fetchone()[0]
    assert response.status_code == 403
    assert response.json() == {"detail": {"code": "admin_required"}}
    assert before == after
    assert csrf


def test_A20_04_non_admin缺少CSRF_cookie仍admin_first且不輪替(tmp_path):
    client, _, _, _ = _建立客戶端(tmp_path)
    認證路徑 = tmp_path / "auth.sqlite3"
    with client:
        _登入(client, "bob", "member password")
        client.cookies.delete("published_web_csrf", path="/api")
        with sqlite3.connect(認證路徑) as db:
            before = db.execute(
                "SELECT csrf_token_hash,last_seen_at FROM web_sessions WHERE revoked_at IS NULL"
            ).fetchone()
        response = _post(client, "x" * 32)
        with sqlite3.connect(認證路徑) as db:
            after = db.execute(
                "SELECT csrf_token_hash,last_seen_at FROM web_sessions WHERE revoked_at IS NULL"
            ).fetchone()
    assert response.status_code == 403
    assert response.json() == {"detail": {"code": "admin_required"}}
    assert after == before


def test_A20_04_not_found與兩種conflict只由治理sealed_outcome映射(tmp_path):
    client, _, _, _ = _建立客戶端(tmp_path)
    with client:
        csrf = _登入(client)
        missing = _post(client, csrf, path=路徑.replace("invocation-1", "missing-invocation"))
        csrf = missing.headers["X-CSRF-Token"]
        first = _post(client, csrf)
        csrf = first.headers["X-CSRF-Token"]
        idem = _post(client, csrf, body={**本文, "reason": "different approved reason"})
        csrf = idem.headers["X-CSRF-Token"]
        target = _post(client, csrf, key="idem-2")
    assert missing.status_code == 404 and missing.json() == {"detail": {"code": "invocation_not_found"}}
    assert first.status_code == 200
    assert idem.status_code == 409 and idem.json() == {"detail": {"code": "idempotency_conflict"}}
    assert target.status_code == 409 and target.json() == {"detail": {"code": "redaction_conflict"}}


def test_A20_04_uninstalled治理固定500且保留CSRF_successor(tmp_path):
    client, _, _, _ = _建立客戶端(tmp_path, 安裝=False)
    with client:
        csrf = _登入(client)
        response = _post(client, csrf)
    assert response.status_code == 500
    assert response.json() == {"detail": {"code": "redaction_failed"}}
    assert response.headers["X-CSRF-Token"]
    assert "published_web_csrf=" in response.headers["set-cookie"]


def test_A20_04_receipt_invocation_binding缺失或錯誤不得由URL合成(monkeypatch, tmp_path):
    client, _, _, _ = _建立客戶端(tmp_path)

    def poisoned(_self, _command, **_kwargs):
        return 管理遮蔽收據(
            "redaction-1", "foreign-invocation", "tool_result", "tool-call-1",
            "/result/secret", "a" * 64, "approved privacy request", "admin-1",
            "audit-1", True, 100.0,
        )

    monkeypatch.setattr(SQLite不可逆遮蔽服務, "執行命令", poisoned)
    with client:
        csrf = _登入(client)
        response = _post(client, csrf)
    assert response.status_code == 500
    assert response.json() == {"detail": {"code": "redaction_failed"}}


def test_A20_04_threadpool_CancelledError保持exact_identity(monkeypatch, tmp_path):
    cancellation = asyncio.CancelledError("CANCEL_A20")

    def cancel(_self, _command, **_kwargs):
        raise cancellation

    monkeypatch.setattr(SQLite不可逆遮蔽服務, "執行命令", cancel)
    _, router, _, _ = _建立客戶端(tmp_path)
    route = router.routes[0]
    assert type(route) is APIRoute
    body = json.dumps(本文).encode()
    sent = False

    async def receive():
        nonlocal sent
        if sent:
            return {"type": "http.disconnect"}
        sent = True
        return {"type": "http.request", "body": body, "more_body": False}

    request = Request({
        "type": "http", "method": "POST", "path": 路徑,
        "query_string": b"", "headers": [
            (b"content-type", b"application/json"),
            (b"content-length", str(len(body)).encode("ascii")),
            (b"idempotency-key", b"idem-cancel"),
        ],
    }, receive)

    async def run():
        with pytest.raises(asyncio.CancelledError) as caught:
            await route.endpoint(
                request, "endpoint-1", "invocation-1", Response(),
                管理遮蔽授權(網頁使用者("admin-1", "alice", "admin")),
            )
        assert caught.value is cancellation

    asyncio.run(run())
