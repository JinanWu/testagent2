"""A18-02 Admin完整紀錄 isolated HTTP contract tests。"""

from pathlib import Path
import asyncio
import threading
import time

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from 繁中代理.發布介面.治理.管理查詢契約 import (
    管理員呼叫不存在錯誤,
    管理員呼叫完整詳情,
    管理員呼叫列表項目,
    管理員呼叫投影頁,
    管理員呼叫游標編解碼器,
    管理員呼叫查詢錯誤,
    管理員呼叫稽核錯誤,
    管理員拒絕稽核已提交,
)
from 繁中代理.發布介面.網頁工作階段 import 網頁使用者
from 繁中代理.發布介面.路由.管理稽核 import 建立管理稽核路由器, 管理員遮蔽回應
from 繁中代理.發布介面.生產管理稽核 import 延遲管理稽核服務, 安裝管理稽核資源
from 繁中代理.發布介面.asgi import 建立CP4ASGI應用程式
from 繁中代理.發布介面.設定 import 生產設定
from 繁中代理.發布介面.生產Published執行 import Published生產設定


class _列表:
    def __init__(self):
        self.次數 = 0

    def 列出管理員安全呼叫(self, 條件, 位置, /):
        self.次數 += 1
        assert 條件.端點識別碼 == "ep-1" and 位置 is None
        return 管理員呼叫投影頁((管理員呼叫列表項目(
            "inv-1", "ep-1", "ver-1", "req-1", "failed", "timeout",
            12.0, 10.0, 11.0, True,
        ),), None)


class _詳情:
    def 查詢管理員原始資料(self, *參數):
        raise AssertionError("list不得呼叫raw detail")


def _客戶端(角色: str):
    列表 = _列表()
    def session():
        return 網頁使用者("user-1", "alice", 角色)
    app = FastAPI()
    app.include_router(建立管理稽核路由器(
        列表, _詳情(), 管理員呼叫游標編解碼器(b"k" * 32), session,
    ))
    return TestClient(app), 列表


def test_A18_admin_GET_list只回安全metadata且無raw欄位():
    客戶端, 列表 = _客戶端("admin")
    回應 = 客戶端.get("/api/admin/endpoints/ep-1/invocations")
    assert 回應.status_code == 200
    assert 回應.json() == {"items": [{
        "invocation_id": "inv-1", "endpoint_id": "ep-1", "endpoint_version_id": "ver-1",
        "request_id": "req-1", "status": "failed", "error_code": "timeout",
        "latency_ms": 12.0, "created_at": 10.0, "completed_at": 11.0,
        "has_redactions": True,
    }], "next_cursor": None}
    assert 列表.次數 == 1
    assert not ({"input", "metadata", "output", "error", "usage"} & set(回應.text))


def test_A18_non_admin與client_claim在provider前固定403():
    客戶端, 列表 = _客戶端("member")
    回應 = 客戶端.get(
        "/api/admin/endpoints/ep-1/invocations?owner_id=user-1",
        headers={"X-Admin": "true", "X-User-Id": "admin-1", "Authorization": "Bearer fake"},
    )
    assert 回應.status_code == 403
    assert 回應.json() == {"detail": "只有管理者可查看完整呼叫紀錄"}
    assert 列表.次數 == 0


def test_A18_Admin_routes只接受GET且尾斜線不redirect():
    客戶端, _ = _客戶端("admin")
    assert 客戶端.post("/api/admin/endpoints/ep-1/invocations").status_code == 405
    assert 客戶端.get("/api/admin/endpoints/ep-1/invocations/", follow_redirects=False).status_code != 307


def test_A18_malformed_path固定422且不回顯敵對輸入():
    for 路徑 in (
        "/api/admin/endpoints/%24RAW_MARKER/invocations",
        "/api/admin/endpoints/ep-1/invocations/%24RAW_MARKER",
    ):
        客戶端, _ = _詳情客戶端(管理員呼叫完整詳情(_詳情資料()))
        回應 = 客戶端.get(路徑)
        assert 回應.status_code == 422
        assert 回應.json() == {"detail": {"code": "invalid_request"}}
        assert "RAW_MARKER" not in 回應.text


def _詳情資料():
    return {
        "invocation": {"id": "inv-1", "request_id": "req-1", "session_id": None},
        "endpoint_id": "ep-1", "endpoint_version_id": "ver-1", "credential_id": None,
        "message_id": None, "status": "failed", "input": {"prompt": "safe"},
        "metadata": {}, "output": None, "error": None, "usage": None,
        "metadata_size_bytes": None, "metadata_sha256": None, "latency_ms": 1.0,
        "pricing_version": None, "created_at": 10.0, "completed_at": 11.0,
        "run_events": [], "tool_calls": [], "redactions": [{
            "id": "redaction-1", "target_type": "metadata", "target_row_id": "inv-1",
            "json_path": "/secret", "reason": "privacy",
            "is_tombstone": True, "redacted_at": 9.0,
        }],
    }


class _可控詳情:
    def __init__(self, 結果):
        self.結果, self.呼叫 = 結果, []

    def 查詢管理員原始資料(self, *參數):
        self.呼叫.append(參數)
        if 參數[0] is False:
            if isinstance(self.結果, BaseException):
                raise self.結果
            return 管理員拒絕稽核已提交
        if isinstance(self.結果, BaseException):
            raise self.結果
        return self.結果


def _詳情客戶端(結果, 角色="admin"):
    詳情 = _可控詳情(結果)
    def session():
        return 網頁使用者("admin-1", "alice", 角色)
    app = FastAPI()
    app.include_router(建立管理稽核路由器(
        _列表(), 詳情, 管理員呼叫游標編解碼器(b"k" * 32), session,
        時鐘=lambda: 123.0, 請求識別碼工廠=lambda: "request-1",
        稽核事件識別碼工廠=lambda: "audit-1",
    ))
    return TestClient(app), 詳情


def test_A18_Admin_detail傳server_owned_audit資料且只序列化typed_DTO():
    客戶端, 詳情 = _詳情客戶端(管理員呼叫完整詳情(_詳情資料()))
    回應 = 客戶端.get("/api/admin/endpoints/ep-1/invocations/inv-1")
    assert 回應.status_code == 200 and 回應.json() == _詳情資料()
    assert 詳情.呼叫 == [(True, "admin-1", "request-1", "audit-1", 123.0, "ep-1", "inv-1")]


def test_A18_detail_non_admin先留下denied_audit再固定403且敵對query零audit():
    客戶端, 詳情 = _詳情客戶端(管理員呼叫完整詳情(_詳情資料()), "member")
    拒絕 = 客戶端.get("/api/admin/endpoints/ep-1/invocations/inv-1")
    assert 拒絕.status_code == 403
    assert 拒絕.json() == {"detail": "只有管理者可查看完整呼叫紀錄"}
    assert 詳情.呼叫 == [(False, "admin-1", "request-1", "audit-1", 123.0, "ep-1", "inv-1")]
    客戶端, 詳情 = _詳情客戶端(管理員呼叫完整詳情(_詳情資料()))
    assert 客戶端.get("/api/admin/endpoints/ep-1/invocations/inv-1?export=true").status_code == 422
    assert 詳情.呼叫 == []


def test_A18_detail_non_admin_denied_audit失敗時503():
    客戶端, 詳情 = _詳情客戶端(管理員呼叫稽核錯誤("RAW"), "member")
    回應 = 客戶端.get("/api/admin/endpoints/ep-1/invocations/inv-1")
    assert 回應.status_code == 503
    assert 回應.json() == {"detail": "呼叫紀錄暫時不可取得"}
    assert 詳情.呼叫 == [(False, "admin-1", "request-1", "audit-1", 123.0, "ep-1", "inv-1")]


def test_A18_detail_non_admin拒絕provider自稱查詢錯誤為已提交audit():
    客戶端, 詳情 = _詳情客戶端(管理員呼叫查詢錯誤("denied audit committed"), "member")
    回應 = 客戶端.get("/api/admin/endpoints/ep-1/invocations/inv-1")
    assert 回應.status_code == 500
    assert 回應.json() == {"detail": "呼叫紀錄不可取得"}
    assert 詳情.呼叫 == [(False, "admin-1", "request-1", "audit-1", 123.0, "ep-1", "inv-1")]


def test_A18_detail_redactions沿用canonical遮蔽shape且OpenAPI同界線():
    for 覆寫 in (
        {"json_path": "$.secret"},
        {"json_path": "/" + "x" * 257},
        {"json_path": "/" + "~0" * 200},
        {"reason": "x" * 257},
        {"reason": "Bearer secret"},
        {"reason": "中" + "a" * 64},
        {"is_tombstone": False},
    ):
        資料 = _詳情資料()
        資料["redactions"][0] = {**資料["redactions"][0], **覆寫}
        with pytest.raises(Exception):
            管理員呼叫完整詳情(資料)

    客戶端, _ = _詳情客戶端(管理員呼叫完整詳情(_詳情資料()))
    schemas = 客戶端.get("/openapi.json").json()["components"]["schemas"]
    schema = schemas["AdminRedaction"]["properties"]
    assert set(schema["target_type"]["enum"]) == {
        "invocation_input", "metadata", "output", "error", "run_event",
        "tool_arguments", "tool_result", "tool_error",
    }
    assert schema["json_path"]["maxLength"] == 4096
    assert schema["json_path"]["pattern"] == r"^(?:$|(?:/(?![^/]{257})(?:[^~/]|~[01]){0,256}){1,16})$"
    assert schema["reason"]["maxLength"] == 256
    assert "pattern" in schema["reason"]
    assert schema["is_tombstone"]["const"] is True
    detail_path = "/api/admin/endpoints/{endpoint_id}/invocations/{invocation_id}"
    detail_ref = 客戶端.get("/openapi.json").json()["paths"][detail_path]["get"]["responses"]["200"]["content"]["application/json"]["schema"]["$ref"]
    detail_schema = schemas[detail_ref.rsplit("/", 1)[1]]["properties"]
    assert {項.get("type") for 項 in detail_schema["metadata_size_bytes"]["anyOf"]} == {"integer", "null"}
    有效 = _詳情資料()["redactions"][0]
    for 覆寫 in (
        {"json_path": "/" + "~0" * 200},
        {"reason": "Bearer secret"},
        {"reason": "中" + "a" * 64},
    ):
        with pytest.raises(Exception):
            管理員遮蔽回應(**{**有效, **覆寫})


def test_A18_detail固定404_503_500且零內部訊息():
    案例 = (
        (管理員呼叫不存在錯誤("RAW"), 404, "找不到呼叫紀錄"),
        (管理員呼叫稽核錯誤("RAW"), 503, "呼叫紀錄暫時不可取得"),
        (管理員呼叫查詢錯誤("RAW"), 500, "呼叫紀錄不可取得"),
    )
    for 錯誤, 狀態, 訊息 in 案例:
        客戶端, _ = _詳情客戶端(錯誤)
        回應 = 客戶端.get("/api/admin/endpoints/ep-1/invocations/inv-1")
        assert 回應.status_code == 狀態 and 回應.json() == {"detail": 訊息}
        assert "RAW" not in 回應.text


def test_A18_list_strict_query_duplicate_unknown與forbidden皆422且provider零次():
    for 查詢 in ("owner_id=u", "raw_search=x", "export=true", "unknown=x", "limit=1&limit=2"):
        客戶端, 列表 = _客戶端("admin")
        回應 = 客戶端.get(f"/api/admin/endpoints/ep-1/invocations?{查詢}")
        assert 回應.status_code == 422 and 列表.次數 == 0


def test_A18_live_OpenAPI只有兩條Admin_logs_paths且operation_id唯一():
    客戶端, _ = _客戶端("admin")
    paths = 客戶端.get("/openapi.json").json()["paths"]
    admin = {路徑: 定義 for 路徑, 定義 in paths.items() if 路徑.startswith("/api/admin/")}
    assert set(admin) == {
        "/api/admin/endpoints/{endpoint_id}/invocations",
        "/api/admin/endpoints/{endpoint_id}/invocations/{invocation_id}",
    }
    assert all(set(定義) == {"get"} for 定義 in admin.values())
    ids = [定義["get"]["operationId"] for 定義 in admin.values()]
    assert len(ids) == len(set(ids)) == 2
    list_parameters = admin["/api/admin/endpoints/{endpoint_id}/invocations"]["get"]["parameters"]
    assert {(項["name"], 項["in"]) for 項 in list_parameters} == {
        ("endpoint_id", "path"), ("from_at", "query"), ("to_at", "query"),
        ("status", "query"), ("error_code", "query"), ("limit", "query"), ("cursor", "query"),
    }
    openapi = str(admin).lower()
    assert "export" not in openapi and "download" not in openapi and "raw_search" not in openapi
    list_ref = admin["/api/admin/endpoints/{endpoint_id}/invocations"]["get"]["responses"]["200"]["content"]["application/json"]["schema"]["$ref"]
    detail_ref = admin["/api/admin/endpoints/{endpoint_id}/invocations/{invocation_id}"]["get"]["responses"]["200"]["content"]["application/json"]["schema"]["$ref"]
    assert list_ref != detail_ref
    schemas = 客戶端.get("/openapi.json").json()["components"]["schemas"]
    list_schema = schemas[list_ref.rsplit("/", 1)[1]]
    assert not ({"input", "metadata", "output", "error", "usage"} & set(str(list_schema).lower()))
    for 定義 in admin.values():
        assert "503" in 定義["get"]["responses"]
        for 狀態 in ("401", "403", "422", "500", "503"):
            if 狀態 in 定義["get"]["responses"]:
                schema = 定義["get"]["responses"][狀態]["content"]["application/json"]["schema"]
                assert "$ref" in schema or "oneOf" in schema or "properties" in schema
        for 狀態 in ("401", "403", "500", "503"):
            schema = 定義["get"]["responses"][狀態]["content"]["application/json"]["schema"]
            assert schema["properties"]["detail"]["type"] == "string"
    detail_schema = schemas[detail_ref.rsplit("/", 1)[1]]
    for 欄位 in ("invocation", "run_events", "tool_calls"):
        assert "$ref" in str(detail_schema["properties"][欄位])


def test_A18管理稽核proxy在startup前與shutdown後fail_closed():
    代理 = 延遲管理稽核服務()
    for 操作 in (
        lambda: 代理.列出管理員安全呼叫(None, None),
        lambda: 代理.查詢管理員原始資料(True, "admin-1", "req-1", "audit-1", 1.0, "ep-1", "inv-1"),
    ):
        try:
            操作()
            assert False
        except RuntimeError as 錯誤:
            assert 錯誤.args == ("Published管理稽核服務不可用",)

    class 服務:
        def 列出管理員安全呼叫(self, *參數): return 參數
        def 查詢管理員原始資料(self, *參數): return 參數
    服務物件 = 服務()
    世代 = 代理.安裝(服務物件)
    assert 代理.列出管理員安全呼叫("query", None) == ("query", None)
    代理.清除(服務物件, 世代)
    try:
        代理.列出管理員安全呼叫("query", None)
        assert False
    except RuntimeError as 錯誤:
        assert 錯誤.args == ("Published管理稽核服務不可用",)


def test_A18_canonical_app建構零IO且OpenAPI掛載兩條Admin_GET(tmp_path, monkeypatch):
    def 禁止連線(*_參數, **_關鍵字):
        raise AssertionError("app construction不得開啟SQLite")
    monkeypatch.setattr("sqlite3.connect", 禁止連線)
    Web路徑 = (tmp_path / "web.sqlite3").resolve()
    Published路徑 = (tmp_path / "published.sqlite3").resolve()
    設定 = 生產設定(Web路徑, ("https://client.example",), "fake", "fake")
    發布 = Published生產設定(
        Published路徑, (tmp_path / "bundles").resolve(), lambda _庫: None, lambda: {"fake": object()},
    )
    app = 建立CP4ASGI應用程式(設定, 發布)
    paths = app.openapi()["paths"]
    assert "/api/admin/endpoints/{endpoint_id}/invocations" in paths
    assert "/api/admin/endpoints/{endpoint_id}/invocations/{invocation_id}" in paths
    me = next(r for r in app.routes if getattr(r, "path", None) == "/api/auth/me")
    admin = next(r for r in app.routes if getattr(r, "path", None) == "/api/admin/endpoints/{endpoint_id}/invocations")
    assert admin.dependant.dependencies[0].call.__canonical_dependency__ is me.dependant.dependencies[0].call
    回應 = TestClient(app).get("/api/admin/endpoints/ep-1/invocations")
    assert 回應.status_code == 401
    assert 回應.json() == {"detail": "需要登入"}


def test_A18_production_installer只使用Published路徑且失敗關閉主資源(tmp_path, monkeypatch):
    路徑 = (tmp_path / "published.sqlite3").resolve()
    捕捉 = []

    class 主資源:
        def __init__(self): self.關閉次數 = 0
        async def 關閉(self): self.關閉次數 += 1

    class 服務:
        def __init__(self, 收到路徑): 捕捉.append(收到路徑)
        def 列出管理員安全呼叫(self, *參數): return 參數
        def 查詢管理員原始資料(self, *參數): return 參數

    monkeypatch.setattr("繁中代理.發布介面.生產管理稽核.管理稽核提供者", 服務)
    主 = 主資源()
    代理 = 延遲管理稽核服務()
    資源 = asyncio.run(安裝管理稽核資源(主, 代理, 路徑))
    assert 捕捉 == [路徑]
    asyncio.run(資源.關閉())
    assert 主.關閉次數 == 1

    class 壞代理:
        def 安裝(self, _服務): raise RuntimeError("failed")
    主 = 主資源()
    try:
        asyncio.run(安裝管理稽核資源(主, 壞代理(), 路徑))
        assert False
    except RuntimeError:
        assert 主.關閉次數 == 1


def test_A18_Admin_proxy清除失敗仍關閉主資源(tmp_path, monkeypatch):
    class 主資源:
        def __init__(self): self.關閉次數 = 0
        async def 關閉(self): self.關閉次數 += 1

    class 服務:
        def __init__(self, _路徑): pass
        def 列出管理員安全呼叫(self, *參數): return 參數
        def 查詢管理員原始資料(self, *參數): return 參數

    monkeypatch.setattr("繁中代理.發布介面.生產管理稽核.管理稽核提供者", 服務)
    代理 = 延遲管理稽核服務()
    主 = 主資源()
    資源 = asyncio.run(安裝管理稽核資源(主, 代理, (tmp_path / "published.sqlite3").resolve()))

    def 壞清除(*_參數): raise RuntimeError("cleanup")
    monkeypatch.setattr(代理, "清除", 壞清除)
    try:
        asyncio.run(資源.關閉())
        assert False
    except RuntimeError as 錯誤:
        assert 錯誤.args == ("cleanup",) and 主.關閉次數 == 1
    try:
        代理.列出管理員安全呼叫(None, None)
        assert False
    except RuntimeError as 錯誤:
        assert 錯誤.args == ("Published管理稽核服務不可用",)


def test_A18_hostile_dependency錯誤不可穿透raw_status或body():
    from fastapi import HTTPException

    def 敵對相依():
        raise HTTPException(418, detail={"marker": "RAW_DEPENDENCY_SECRET"})

    app = FastAPI()
    app.include_router(建立管理稽核路由器(
        _列表(), _詳情(), 管理員呼叫游標編解碼器(b"k" * 32), 敵對相依,
    ))
    回應 = TestClient(app).get("/api/admin/endpoints/ep-1/invocations")
    assert 回應.status_code == 500
    assert 回應.json() == {"detail": "呼叫紀錄不可取得"}
    assert "RAW_DEPENDENCY_SECRET" not in 回應.text


def test_A18_canonical_dependency錯誤不可夾帶敵對headers():
    from fastapi import HTTPException

    for 狀態, detail, header in (
        (401, {"code": "unauthorized"}, {"X-Raw-Stage": "RAW_STAGE_SECRET"}),
        (503, {"code": "auth_unavailable"}, {"X-Error": "RAW_ERROR_SECRET"}),
    ):
        def 敵對相依():
            raise HTTPException(狀態, detail=detail, headers=header)

        app = FastAPI()
        app.include_router(建立管理稽核路由器(
            _列表(), _詳情(), 管理員呼叫游標編解碼器(b"k" * 32), 敵對相依,
        ))
        回應 = TestClient(app).get("/api/admin/endpoints/ep-1/invocations")
        assert 回應.status_code == 狀態
        預期訊息 = "需要登入" if 狀態 == 401 else "呼叫紀錄暫時不可取得"
        assert 回應.json() == {"detail": 預期訊息}
        assert "x-raw-stage" not in 回應.headers
        assert "x-error" not in 回應.headers


def test_A18_partial_install發布後拋錯必須撤銷authority(tmp_path, monkeypatch):
    class 主資源:
        def __init__(self): self.關閉次數 = 0
        async def 關閉(self): self.關閉次數 += 1

    class 服務:
        def __init__(self, _路徑): pass
        def 列出管理員安全呼叫(self, *參數): return 參數
        def 查詢管理員原始資料(self, *參數): return 參數

    class 發布後失敗代理(延遲管理稽核服務):
        def 安裝(self, 服務物件):
            super().安裝(服務物件)
            raise RuntimeError("after-publish")

    monkeypatch.setattr("繁中代理.發布介面.生產管理稽核.管理稽核提供者", 服務)
    主 = 主資源()
    代理 = 發布後失敗代理()
    try:
        asyncio.run(安裝管理稽核資源(主, 代理, (tmp_path / "published.sqlite3").resolve()))
        assert False
    except RuntimeError:
        assert 主.關閉次數 == 1
    try:
        代理.列出管理員安全呼叫("query", None)
        assert False
    except RuntimeError as 錯誤:
        assert 錯誤.args == ("Published管理稽核服務不可用",)


def test_A18_same_generation並行clear皆等待同一drain_terminal():
    進入 = threading.Event()
    釋放 = threading.Event()

    class 服務:
        def 列出管理員安全呼叫(self, *參數):
            進入.set()
            釋放.wait(2)
            return 參數
        def 查詢管理員原始資料(self, *參數): return 參數

    代理 = 延遲管理稽核服務()
    服務物件 = 服務()
    世代 = 代理.安裝(服務物件)
    租借執行緒 = threading.Thread(target=代理.列出管理員安全呼叫, args=("query", None))
    租借執行緒.start()
    assert 進入.wait(1)
    完成 = []
    清除甲 = threading.Thread(target=lambda: (代理.清除(服務物件, 世代), 完成.append("甲")))
    清除乙 = threading.Thread(target=lambda: (代理.清除(服務物件, 世代), 完成.append("乙")))
    清除甲.start()
    time.sleep(0.02)
    清除乙.start()
    time.sleep(0.05)
    assert 完成 == []
    釋放.set()
    for 執行緒 in (租借執行緒, 清除甲, 清除乙):
        執行緒.join(1)
    assert sorted(完成) == ["乙", "甲"]


def test_A18_stale_generation_clear不撤銷新provider且排空後可重裝():
    class 服務:
        def __init__(self, 名稱): self.名稱 = 名稱
        def 列出管理員安全呼叫(self, *_參數): return self.名稱
        def 查詢管理員原始資料(self, *_參數): return self.名稱

    代理 = 延遲管理稽核服務()
    舊服務 = 服務("舊")
    舊世代 = 代理.安裝(舊服務)
    代理.清除(舊服務, 舊世代)
    新服務 = 服務("新")
    新世代 = 代理.安裝(新服務)
    代理.清除(舊服務, 舊世代)
    assert 代理.列出管理員安全呼叫(None, None) == "新"
    代理.清除(新服務, 新世代)


def test_A18_provider建構失敗保留原錯且關閉主資源(tmp_path, monkeypatch):
    class 主資源:
        def __init__(self): self.關閉次數 = 0
        async def 關閉(self): self.關閉次數 += 1

    class 建構失敗:
        def __init__(self, _路徑): raise ValueError("provider-construction")

    monkeypatch.setattr("繁中代理.發布介面.生產管理稽核.管理稽核提供者", 建構失敗)
    主 = 主資源()
    try:
        asyncio.run(安裝管理稽核資源(主, 延遲管理稽核服務(), (tmp_path / "p.sqlite3").resolve()))
        assert False
    except ValueError as 錯誤:
        assert 錯誤.args == ("provider-construction",) and 主.關閉次數 == 1


def test_A18_production_public_clear_silent_noop仍由module_revoke_fail_closed(tmp_path, monkeypatch):
    class 主資源:
        def __init__(self): self.原清理次數 = 0
        def _執行關閉同步(self): self.原清理次數 += 1

    class 服務:
        def __init__(self, _路徑): pass
        def 列出管理員安全呼叫(self, *_參數): return "LIVE"
        def 查詢管理員原始資料(self, *_參數): return "LIVE"

    monkeypatch.setattr("繁中代理.發布介面.生產管理稽核.管理稽核提供者", 服務)
    主, 代理 = 主資源(), 延遲管理稽核服務()
    資源 = asyncio.run(安裝管理稽核資源(主, 代理, (tmp_path / "p.sqlite3").resolve()))
    monkeypatch.setattr(代理, "清除", lambda *_參數: None)
    資源._執行關閉同步()
    assert 主.原清理次數 == 1
    try:
        代理.列出管理員安全呼叫(None, None)
        assert False
    except RuntimeError as 錯誤:
        assert 錯誤.args == ("Published管理稽核服務不可用",)


def test_A18_startup普通錯誤不被cleanup普通錯誤覆蓋(tmp_path, monkeypatch):
    啟動錯誤 = RuntimeError("startup")

    class 主資源:
        async def 關閉(self): raise ValueError("cleanup")

    class 建構失敗:
        def __init__(self, _路徑): raise 啟動錯誤

    monkeypatch.setattr("繁中代理.發布介面.生產管理稽核.管理稽核提供者", 建構失敗)
    try:
        asyncio.run(安裝管理稽核資源(
            主資源(), 延遲管理稽核服務(), (tmp_path / "p.sqlite3").resolve(),
        ))
        assert False
    except BaseException as 錯誤:
        assert 錯誤 is 啟動錯誤


def test_A18_cleanup多個控制流程保留第一個identity且仍執行全部(tmp_path, monkeypatch):
    第一 = KeyboardInterrupt("public")
    第三 = GeneratorExit("published")
    次序 = []

    class 主資源:
        def _執行關閉同步(self):
            次序.append("published")
            raise 第三

    class 服務:
        def __init__(self, _路徑): pass
        def 列出管理員安全呼叫(self, *_參數): return "LIVE"
        def 查詢管理員原始資料(self, *_參數): return "LIVE"

    class 敵對代理(延遲管理稽核服務):
        def 清除(self, *_參數):
            次序.append("public")
            raise 第一

    monkeypatch.setattr("繁中代理.發布介面.生產管理稽核.管理稽核提供者", 服務)
    主, 代理 = 主資源(), 敵對代理()
    資源 = asyncio.run(安裝管理稽核資源(主, 代理, (tmp_path / "p.sqlite3").resolve()))

    try:
        資源._執行關閉同步()
        assert False
    except BaseException as 錯誤:
        assert 錯誤 is 第一
    assert 次序 == ["public", "published"]
    try:
        代理.列出管理員安全呼叫(None, None)
        assert False
    except RuntimeError as 錯誤:
        assert 錯誤.args == ("Published管理稽核服務不可用",)


def test_A18_startup_rollback多個控制流程保留第一個identity且仍執行全部(tmp_path, monkeypatch):
    第一 = KeyboardInterrupt("first-cleanup")
    第二 = SystemExit("second-cleanup")
    次序 = []

    class 主資源:
        async def 關閉(self):
            次序.append("main-close")
            raise 第二

    class 服務:
        def __init__(self, _路徑): pass
        def 列出管理員安全呼叫(self, *_參數): return "LIVE"
        def 查詢管理員原始資料(self, *_參數): return "LIVE"

    class 發布後失敗代理(延遲管理稽核服務):
        def 安裝(self, 服務物件):
            super().安裝(服務物件)
            raise RuntimeError("startup")

    def 壞撤銷(*_參數):
        次序.append("module-revoke")
        raise 第一

    monkeypatch.setattr("繁中代理.發布介面.生產管理稽核.管理稽核提供者", 服務)
    monkeypatch.setattr(延遲管理稽核服務, "_撤銷已發布服務", 壞撤銷)
    代理 = 發布後失敗代理()
    try:
        asyncio.run(安裝管理稽核資源(
            主資源(), 代理, (tmp_path / "p.sqlite3").resolve(),
        ))
        assert False
    except BaseException as 錯誤:
        assert 錯誤 is 第一
    assert 次序 == ["module-revoke", "main-close"]
    try:
        代理.列出管理員安全呼叫(None, None)
        assert False
    except RuntimeError as 錯誤:
        assert 錯誤.args == ("Published管理稽核服務不可用",)
