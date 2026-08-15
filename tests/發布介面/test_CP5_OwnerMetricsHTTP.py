"""A19-02 Owner metrics／safe diagnostics HTTP 與 canonical wiring 測試。"""

import hashlib

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from 繁中代理.發布介面.網頁工作階段 import 網頁使用者
from 繁中代理.發布介面.治理.觀測契約 import (
    安全錯誤排行,
    定價版本成本,
    延遲摘要,
    指標查詢成功,
    每日端點指標,
    用量摘要,
    端點不可見結果,
    端點指標,
    診斷查詢成功,
    診斷用量,
    診斷項目,
    診斷頁,
    觀測視窗,
)
from 繁中代理.發布介面.治理.觀測供應器 import 端點觀測游標錯誤
from 繁中代理.發布介面.路由.Owner觀測 import 建立Owner觀測路由器
from 繁中代理.發布介面.asgi import 建立CP4ASGI應用程式
from 繁中代理.發布介面.設定 import 生產設定
from 繁中代理.發布介面.生產Published執行 import Published生產設定


class 假觀測服務:
    def __init__(self):
        self.呼叫 = []
        self.不可見 = False
        self.失敗: BaseException | None = None

    def 讀取端點指標(self, **參數):
        self.呼叫.append(("metrics", 參數))
        if self.失敗 is not None:
            raise self.失敗
        if self.不可見:
            return 端點不可見結果()
        return 指標查詢成功(端點指標(
            參數["端點識別碼"], 觀測視窗(0.0, 86400.0), 1, 1, 1, 1.0,
            延遲摘要(1, 3.0, 3.0, 3.0, 3.0), 用量摘要(1, 2, 3, 5), "0.1",
            (定價版本成本("v1", "0.1"),),
            (每日端點指標("1970-01-01", 1, 1, 1, 5, "0.1"),),
            (安全錯誤排行("timeout", 1),),
        ))

    def 列出端點診斷(self, **參數):
        self.呼叫.append(("invocations", 參數))
        if self.失敗 is not None:
            raise self.失敗
        if self.不可見:
            return 端點不可見結果()
        return 診斷查詢成功(診斷頁((診斷項目(
            "inv-1", "req-1", "ver-1", "failed", "timeout", None, 3.0,
            診斷用量(5), (), 10.0, 11.0, (),
        ),), "opaque"))


def _客戶端(服務=None, *, 使用者=None):
    服務 = 服務 or 假觀測服務()
    使用者 = 使用者 or 網頁使用者("owner-1", "alice", "member")
    app = FastAPI()
    app.include_router(建立Owner觀測路由器(服務, lambda: 使用者))
    return TestClient(app, raise_server_exceptions=False), 服務


def test_A19_Owner_metrics與diagnostics只使用canonical_session_owner且typed_safe_response():
    客戶端, 服務 = _客戶端()
    指標 = 客戶端.get("/api/published-endpoints/ep-1/metrics?window_seconds=86400")
    assert 指標.status_code == 200
    assert 指標.json()["daily"] == [{
        "date": "1970-01-01", "invocation_count": 1, "terminal_count": 1,
        "error_count": 1, "usage_total_tokens": 5, "estimated_cost_usd": "0.1",
    }]
    assert 指標.json()["top_errors"] == [{"error_code": "timeout", "count": 1}]
    頁 = 客戶端.get("/api/published-endpoints/ep-1/diagnostics?window_seconds=604800&limit=25")
    assert 頁.status_code == 200
    assert 頁.json() == {"items": [{
        "invocation_id": "inv-1", "request_id": "req-1", "endpoint_version_id": "ver-1",
        "status": "failed", "error_code": "timeout", "schema_path": None,
        "latency_ms": 3.0, "usage": {"total_tokens": 5}, "tool_names": [],
        "created_at": 10.0, "completed_at": 11.0, "redacted_fields": [],
    }], "next_cursor": "opaque"}
    assert 服務.呼叫 == [
        ("metrics", {"擁有者使用者識別碼": "owner-1", "是否管理者": False,
                     "端點識別碼": "ep-1", "視窗秒數": 86400}),
        ("invocations", {"擁有者使用者識別碼": "owner-1", "是否管理者": False,
                         "端點識別碼": "ep-1", "視窗秒數": 604800,
                         "數量上限": 25, "游標": None}),
    ]
    assert not ({"input", "metadata", "output", "error_json"} & set(指標.text + 頁.text))


def test_A19_foreign與missing固定相同404且client_claim皆422_provider零次():
    服務 = 假觀測服務()
    服務.不可見 = True
    客戶端, _ = _客戶端(服務)
    外人 = 客戶端.get("/api/published-endpoints/foreign/metrics?window_seconds=86400")
    缺少 = 客戶端.get("/api/published-endpoints/missing/metrics?window_seconds=86400")
    assert (外人.status_code, 外人.json()) == (404, {"detail": "找不到發布端點"})
    assert 外人.content == 缺少.content
    for 查詢 in ("window_seconds=86400&owner_id=owner-1", "window_seconds=86400&scope=all",
               "window_seconds=86400&admin=true", "window_seconds=86400&raw=true",
               "window_seconds=86400&window_seconds=604800"):
        新服務 = 假觀測服務()
        回應 = _客戶端(新服務)[0].get(f"/api/published-endpoints/ep-1/metrics?{查詢}")
        assert 回應.status_code == 422 and 新服務.呼叫 == []
        assert 查詢 not in 回應.text


def test_A19_未登入與敵對dependency皆固定且不繼承headers():
    for 相依, 狀態, 訊息 in (
        (lambda: (_ for _ in ()).throw(HTTPException(401, "RAW", headers={"X-Raw": "secret"})), 401, "需要登入"),
        (lambda: (_ for _ in ()).throw(HTTPException(418, "RAW")), 500, "端點觀測不可取得"),
    ):
        app = FastAPI()
        app.include_router(建立Owner觀測路由器(假觀測服務(), 相依))
        回應 = TestClient(app, raise_server_exceptions=False).get(
            "/api/published-endpoints/ep-1/metrics?window_seconds=86400"
        )
        assert (回應.status_code, 回應.json()) == (狀態, {"detail": 訊息})
        assert "x-raw" not in 回應.headers and "RAW" not in 回應.text


def test_A19_strict_window_limit_cursor與malformed_path不回顯敵對輸入():
    for 路徑 in (
        "/api/published-endpoints/%24RAW_MARKER/metrics?window_seconds=86400",
        "/api/published-endpoints/ep-1/metrics?window_seconds=RAW_MARKER",
        "/api/published-endpoints/ep-1/diagnostics?window_seconds=86400&limit=0",
        "/api/published-endpoints/ep-1/diagnostics?window_seconds=86400&limit=50&cursor=" + "x" * 1025,
    ):
        客戶端, 服務 = _客戶端()
        回應 = 客戶端.get(路徑)
        assert 回應.status_code == 422 and 服務.呼叫 == []
        assert "RAW_MARKER" not in 回應.text and "x" * 64 not in 回應.text


def test_A19_live_OpenAPI_exact_GET_paths與safe_schema():
    客戶端, _ = _客戶端()
    規格 = 客戶端.get("/openapi.json").json()
    路徑們 = {k: v for k, v in 規格["paths"].items() if k.startswith("/api/published-endpoints/")}
    assert set(路徑們) == {
        "/api/published-endpoints/{endpoint_id}/metrics",
        "/api/published-endpoints/{endpoint_id}/diagnostics",
    }
    assert all(set(v) == {"get"} for v in 路徑們.values())
    assert {(p["name"], p["in"]) for p in 路徑們["/api/published-endpoints/{endpoint_id}/metrics"]["get"]["parameters"]} == {
        ("endpoint_id", "path"), ("window_seconds", "query"),
    }
    assert {(p["name"], p["in"]) for p in 路徑們["/api/published-endpoints/{endpoint_id}/diagnostics"]["get"]["parameters"]} == {
        ("endpoint_id", "path"), ("window_seconds", "query"), ("limit", "query"), ("cursor", "query"),
    }
    文字 = str(路徑們).lower()
    for 禁止 in ("owner_id", "scope", "admin", "error_json", "metadata", "raw"):
        assert 禁止 not in 文字
    operation_ids = [operation["operationId"] for path in 規格["paths"].values() for operation in path.values()]
    assert len(operation_ids) == len(set(operation_ids))


def test_A19_cursor_typed_failure映射422且GET_only與尾斜線不redirect():
    服務 = 假觀測服務()
    服務.失敗 = 端點觀測游標錯誤("RAW_CURSOR_MARKER")
    客戶端, _ = _客戶端(服務)
    路徑 = "/api/published-endpoints/ep-1/diagnostics?window_seconds=86400&limit=50&cursor=opaque"
    回應 = 客戶端.get(路徑)
    assert 回應.status_code == 422 and "RAW_CURSOR_MARKER" not in 回應.text
    for exact in (
        "/api/published-endpoints/ep-1/metrics?window_seconds=86400",
        "/api/published-endpoints/ep-1/diagnostics?window_seconds=86400&limit=50",
    ):
        不允許 = 客戶端.post(exact)
        assert 不允許.status_code == 405 and 不允許.headers["allow"] == "GET"
    尾斜線 = 客戶端.get(
        "/api/published-endpoints/ep-1/diagnostics/?window_seconds=86400&limit=50",
        follow_redirects=False,
    )
    assert 尾斜線.status_code == 404 and "location" not in 尾斜線.headers


def test_A19_required_integer_query缺漏與非canonical格式皆422_provider零次():
    for 路徑 in (
        "/api/published-endpoints/ep-1/metrics",
        "/api/published-endpoints/ep-1/metrics?window_seconds=01",
        "/api/published-endpoints/ep-1/metrics?window_seconds=%2B1",
        "/api/published-endpoints/ep-1/diagnostics?window_seconds=86400",
        "/api/published-endpoints/ep-1/diagnostics?window_seconds=86400&limit=01",
    ):
        客戶端, 服務 = _客戶端()
        回應 = 客戶端.get(路徑)
        assert 回應.status_code == 422 and 服務.呼叫 == []


def test_A19_canonical_app建構零IO且明示stable_key才掛載Owner_routes(tmp_path, monkeypatch):
    monkeypatch.setattr("sqlite3.connect", lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("不得I/O")))
    設定 = 生產設定((tmp_path / "web.db").resolve(), ("https://client.example",), "fake", "fake")
    金鑰 = hashlib.sha256(b"deployment-owned-stable-key").digest()
    發布 = Published生產設定(
        (tmp_path / "published.db").resolve(), (tmp_path / "bundles").resolve(),
        lambda _庫: None, lambda: {"fake": object()}, Owner觀測游標金鑰=金鑰,
    )
    app = 建立CP4ASGI應用程式(設定, 發布)
    assert "/api/published-endpoints/{endpoint_id}/metrics" in app.openapi()["paths"]
    assert "/api/published-endpoints/{endpoint_id}/diagnostics" in app.openapi()["paths"]
    me = next(r for r in app.routes if getattr(r, "path", None) == "/api/auth/me")
    owner = next(r for r in app.routes if getattr(r, "path", None) == "/api/published-endpoints/{endpoint_id}/metrics")
    assert owner.dependant.dependencies[0].call.__canonical_dependency__ is me.dependant.dependencies[0].call
    回應 = TestClient(app).get("/api/published-endpoints/ep-1/metrics?window_seconds=86400")
    assert (回應.status_code, 回應.json()) == (401, {"detail": "需要登入"})
