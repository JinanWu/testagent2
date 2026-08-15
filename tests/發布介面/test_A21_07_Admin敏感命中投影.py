"""A21-07 canonical Admin detail 敏感命中投影與 OpenAPI closure。"""

from __future__ import annotations

import json
import sqlite3
import copy
from contextlib import closing

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from 繁中代理.發布介面.治理 import 查詢投影 as 查詢投影模組
from 繁中代理.發布介面.治理.查詢投影 import SQLite呼叫查詢投影
from 繁中代理.發布介面.治理.管理查詢契約 import (
    ADMIN_INVOCATION_DETAIL_FIELDS,
    管理員呼叫查詢錯誤,
    管理員呼叫稽核錯誤,
    管理員呼叫完整詳情,
    管理員呼叫游標編解碼器,
    管理員拒絕稽核收據權威,
    建立管理員呼叫完整詳情,
)
from 繁中代理.發布介面.網頁工作階段 import 網頁使用者
from 繁中代理.發布介面.資料庫 import 初始化發布介面資料庫
from 繁中代理.發布介面.路由.管理稽核 import 建立管理稽核路由器
from 繁中代理.發布介面.生產管理稽核 import 管理稽核提供者


紅線標記 = "A21_RAW_SECRET_MARKER"
AdminAuditGate = getattr(查詢投影模組, "\u7ba1\u7406\u54e1\u539f\u59cb\u8cc7\u6599\u7a3d\u6838\u9598\u9580")


@pytest.fixture
def 呼叫資料庫(tmp_path):
    路徑 = (tmp_path / "published.sqlite3").resolve()
    初始化發布介面資料庫(路徑)
    with closing(sqlite3.connect(路徑)) as 連線, 連線:
        連線.execute("PRAGMA foreign_keys=ON")
        連線.execute("INSERT INTO service_accounts VALUES('sa-a21',1,NULL)")
        連線.execute(
            "INSERT INTO published_endpoints VALUES(?,?,?,?,?,?,?,?,?,?)",
            ("ep-a21", "owner-a21", "sa-a21", "a21", "active", None, 1, 1, 60, 60),
        )
        連線.execute(
            "INSERT INTO published_endpoint_versions VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            ("ver-a21", "ep-a21", 1, "req", "prompt", "[]", "[]", "{}", "rev", "{}",
             "{}", "{}", None, "{}", 0, "owner-a21", 1),
        )
        連線.execute("UPDATE published_endpoints SET current_version_id='ver-a21'")
        連線.execute(
            "INSERT INTO endpoint_invocations(id,endpoint_id,endpoint_version_id,request_id,status,"
            "input_json,metadata_json,output_json,created_at,completed_at) "
            "VALUES('inv-a21','ep-a21','ver-a21','req-a21','succeeded','{}','{}','{}',2,3)"
        )
        # IDs刻意與 sequence反向，證明排序使用tool sequence而非ID。
        連線.execute(
            "INSERT INTO endpoint_tool_calls(id,invocation_id,sequence_number,tool_name,arguments_json,"
            "outcome,result_json,created_at) VALUES('tool-z','inv-a21',1,'one','{}','success','{}',2)"
        )
        連線.execute(
            "INSERT INTO endpoint_tool_calls(id,invocation_id,sequence_number,tool_name,arguments_json,"
            "outcome,result_json,created_at) VALUES('tool-a','inv-a21',2,'two','{}','success','{}',2)"
        )
    return 路徑


def _新增命中(路徑, *, 命中ID, 目標, 工具ID=None, 路徑值="", 開始=0, 結束=1,
          偵測器="format_detector", 時間=5.0):
    稽核ID = f"audit-{命中ID}"
    中繼 = json.dumps({
        "warning_code": "sensitive_data_detected", "target": 目標,
        "detector_type": 偵測器, "json_path": 路徑值, "start": 開始, "end": 結束,
    }, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    with sqlite3.connect(路徑) as 連線:
        連線.execute("PRAGMA foreign_keys=ON")
        連線.execute(
            "INSERT INTO audit_events VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (稽核ID, 稽核ID, 時間, "published_api.sensitive_data_detected", "success",
             "system", None, "invocation", "inv-a21", None, "ep-a21", "inv-a21", 中繼, 時間),
        )
        連線.execute(
            "INSERT INTO invocation_sensitive_hits(id,invocation_id,tool_call_id,target_type,"
            "detector_type,json_path,start_offset,end_offset,audit_event_id,detected_at) "
            "VALUES(?,?,?,?,?,?,?,?,?,?)",
            (命中ID, "inv-a21", 工具ID, 目標, 偵測器, 路徑值, 開始, 結束, 稽核ID, 時間),
        )


def test_A21_07_Admin_detail只增單一exact_sensitive_hits並依tool_sequence排序(呼叫資料庫):
    for 參數 in (
        dict(命中ID="hit-tool-2", 目標="tool_result", 工具ID="tool-a", 路徑值="/a", 開始=4, 結束=5),
        dict(命中ID="hit-response", 目標="response_data", 路徑值="/z", 開始=2, 結束=3),
        dict(命中ID="hit-tool-1", 目標="tool_result", 工具ID="tool-z", 路徑值="/z", 開始=8, 結束=9),
        dict(命中ID="hit-input-b", 目標="input", 路徑值="/b", 開始=3, 結束=4),
        dict(命中ID="hit-input-a", 目標="input", 路徑值="/a", 開始=1, 結束=2),
    ):
        _新增命中(呼叫資料庫, **參數)

    原始 = SQLite呼叫查詢投影(str(呼叫資料庫)).查詢管理員原始資料(
        True, "ep-a21", "inv-a21"
    )
    assert set(原始) == ADMIN_INVOCATION_DETAIL_FIELDS
    assert [項["id"] for 項 in 原始["sensitive_hits"]] == [
        "hit-input-a", "hit-input-b", "hit-response", "hit-tool-1", "hit-tool-2",
    ]
    assert all(set(項) == {
        "id", "target", "tool_call_id", "detector_type", "json_path",
        "start", "end", "detected_at",
    } for 項 in 原始["sensitive_hits"])
    assert not ({"audit_event_id", "raw", "value", "snippet", "hash", "credential_id",
                 "session_id", "message_id"} & set().union(*map(set, 原始["sensitive_hits"])))
    DTO = 管理員呼叫完整詳情(原始)
    assert DTO.建立JSON()["sensitive_hits"] == 原始["sensitive_hits"]
    assert 紅線標記 not in repr(DTO)


def test_A21_07_zero_hits是空陣列且Owner投影沒有命中欄位(呼叫資料庫):
    投影 = SQLite呼叫查詢投影(str(呼叫資料庫))
    assert 投影.查詢管理員原始資料(True, "ep-a21", "inv-a21")["sensitive_hits"] == []
    Owner = 投影.查詢擁有者安全詳情("owner-a21", "ep-a21", "inv-a21").建立JSON()
    assert "sensitive_hits" not in Owner


def test_A21_07_audit_sink失敗時detail_callback與hit_DB_read都是零(呼叫資料庫):
    呼叫次數 = []

    class 失敗Sink:
        def append(self, _event):
            raise RuntimeError(紅線標記)

    def detail(_endpoint, _invocation):
        呼叫次數.append(1)
        return SQLite呼叫查詢投影(str(呼叫資料庫)).查詢管理員原始資料(
            True, "ep-a21", "inv-a21"
        )

    閤門 = AdminAuditGate(
        失敗Sink(), detail, lambda _endpoint, _invocation: True,
    )
    with pytest.raises(管理員呼叫稽核錯誤) as 錯誤:
        閤門.查詢管理員原始資料(
            True, "admin-a21", "request-a21", "audit-view-a21", 9.0, "ep-a21", "inv-a21"
        )
    assert 呼叫次數 == []
    assert 紅線標記 not in repr(錯誤.value)


@pytest.mark.parametrize("drift", ["column", "foreign-key", "audit-cardinality"])
def test_A21_07_hit_schema_FK_audit_cardinality漂移全部fail_closed(
    呼叫資料庫, drift,
):
    _新增命中(呼叫資料庫, 命中ID="hit-main", 目標="input")
    with sqlite3.connect(呼叫資料庫) as 連線:
        if drift == "column":
            連線.execute("ALTER TABLE invocation_sensitive_hits ADD COLUMN leaked_raw TEXT")
        elif drift == "foreign-key":
            連線.execute("PRAGMA foreign_keys=OFF")
            連線.execute("ALTER TABLE invocation_sensitive_hits RENAME TO old_hits")
            連線.execute("CREATE TABLE invocation_sensitive_hits AS SELECT * FROM old_hits")
        else:
            連線.execute("DROP TRIGGER audit_events_no_update")
            連線.execute(
                "UPDATE audit_events SET action='wrong.action' WHERE id='audit-hit-main'"
            )
    with pytest.raises(管理員呼叫查詢錯誤) as 錯誤:
        SQLite呼叫查詢投影(str(呼叫資料庫)).查詢管理員原始資料(
            True, "ep-a21", "inv-a21"
        )
    assert 錯誤.value.args == ("呼叫紀錄不可取得",)
    assert 紅線標記 not in repr(錯誤.value)


def _空詳情():
    return {
        "invocation": {"id": "inv-a21", "request_id": "req-a21", "session_id": None},
        "endpoint_id": "ep-a21", "endpoint_version_id": "ver-a21", "credential_id": None,
        "message_id": None, "status": "succeeded", "input": {}, "metadata": {}, "output": {},
        "error": None, "usage": None, "metadata_size_bytes": None, "metadata_sha256": None,
        "latency_ms": None, "pricing_version": None, "created_at": 2.0, "completed_at": 3.0,
        "run_events": [], "tool_calls": [], "redactions": [], "sensitive_hits": [],
    }


def test_A21_07_DTO拒絕禁止欄位與無界rows_path_offset_time():
    基本命中 = {
        "id": "hit-a21", "target": "input", "tool_call_id": None,
        "detector_type": "format_detector", "json_path": "/safe", "start": 0,
        "end": 1, "detected_at": 5.0,
    }
    案例 = []
    for 覆寫 in (
        {"raw": 紅線標記}, {"json_path": "/" + "中" * 2731}, {"start": -1},
        {"end": 2**53}, {"detected_at": 253_402_300_800}, {"target": "credential"},
    ):
        案例.append([{**基本命中, **覆寫}])
    案例.append([dict(基本命中) for _ in range(1025)])
    for 命中們 in 案例:
        資料 = copy.deepcopy(_空詳情())
        資料["sensitive_hits"] = 命中們
        with pytest.raises(管理員呼叫查詢錯誤) as 錯誤:
            建立管理員呼叫完整詳情(資料)
        assert 錯誤.value.args == ("呼叫紀錄不可取得",)
        assert 紅線標記 not in repr(錯誤.value)


def test_A21_07_existing_detail_route經production_provider回傳命中且提交view_audit(呼叫資料庫):
    _新增命中(呼叫資料庫, 命中ID="hit-http", 目標="metadata", 路徑值="/safe")
    權威 = 管理員拒絕稽核收據權威(b"r" * 32)
    provider = 管理稽核提供者(呼叫資料庫, 權威)
    app = FastAPI()
    app.include_router(建立管理稽核路由器(
        provider, provider, 管理員呼叫游標編解碼器(b"k" * 32),
        lambda: 網頁使用者("admin-a21", "admin", "admin"), 拒絕收據權威=權威,
        時鐘=lambda: 10.0, 請求識別碼工廠=lambda: "request-view-a21",
        稽核事件識別碼工廠=lambda: "audit-view-a21",
    ))
    回應 = TestClient(app).get("/api/admin/endpoints/ep-a21/invocations/inv-a21")
    assert 回應.status_code == 200
    assert 回應.json()["sensitive_hits"] == [{
        "id": "hit-http", "target": "metadata", "tool_call_id": None,
        "detector_type": "format_detector", "json_path": "/safe", "start": 0,
        "end": 1, "detected_at": 5.0,
    }]
    with sqlite3.connect(呼叫資料庫) as 連線:
        assert 連線.execute(
            "SELECT action,outcome,endpoint_id,invocation_id,metadata_json FROM audit_events "
            "WHERE id='audit-view-a21'"
        ).fetchone() == ("audit.detail.view", "success", "ep-a21", "inv-a21", "{}")


def test_A21_07_query_header_body_actor_claim都不能覆寫server_session_role():
    權威 = 管理員拒絕稽核收據權威(b"r" * 32)

    class Provider:
        def __init__(self):
            self.calls = []

        def 列出管理員安全呼叫(self, *_args):
            raise AssertionError

        def 查詢管理員原始資料(self, *args):
            self.calls.append(args)
            if args[0] is False:
                return 權威.簽發(*args[1:])
            raise AssertionError

    def client(session):
        provider = Provider()
        app = FastAPI()
        app.include_router(建立管理稽核路由器(
            provider, provider, 管理員呼叫游標編解碼器(b"k" * 32), session,
            拒絕收據權威=權威, 時鐘=lambda: 10.0,
            請求識別碼工廠=lambda: "request-claim-a21",
            稽核事件識別碼工廠=lambda: "audit-claim-a21",
        ))
        return TestClient(app), provider

    路徑 = "/api/admin/endpoints/ep-a21/invocations/inv-a21"
    會員端, provider = client(lambda: 網頁使用者("member-a21", "member", "user"))
    回應 = 會員端.request(
        "GET", 路徑 + "?actor_id=admin-a21&admin=true",
        headers={"X-Admin": "true", "X-Actor-Id": "admin-a21", "Content-Type": "application/json"},
        content=json.dumps({"actor_id": "admin-a21", "admin": True}),
    )
    assert 回應.status_code == 403
    assert len(provider.calls) == 1 and provider.calls[0][0:2] == (False, "member-a21")

    def 未登入():
        raise HTTPException(status_code=401, detail="hostile")

    未登入端, provider = client(未登入)
    回應 = 未登入端.request(
        "GET", 路徑 + "?admin=true", headers={"X-Admin": "true"},
        content=json.dumps({"admin": True}),
    )
    assert 回應.status_code == 401 and 回應.json() == {"detail": "需要登入"}
    assert provider.calls == []


def test_A21_07_live_OpenAPI_detail命中item_exact_enum且禁止additionalProperties():
    權威 = 管理員拒絕稽核收據權威(b"r" * 32)

    class Provider:
        def 列出管理員安全呼叫(self, *_args):
            raise AssertionError

        def 查詢管理員原始資料(self, *args):
            return 管理員呼叫完整詳情(_空詳情()) if args[0] else 權威.簽發(*args[1:])

    app = FastAPI()
    provider = Provider()
    app.include_router(建立管理稽核路由器(
        provider, provider, 管理員呼叫游標編解碼器(b"k" * 32),
        lambda: 網頁使用者("admin-a21", "admin", "admin"), 拒絕收據權威=權威,
    ))
    規格 = TestClient(app).get("/openapi.json").json()
    詳情參照 = 規格["paths"][
        "/api/admin/endpoints/{endpoint_id}/invocations/{invocation_id}"
    ]["get"]["responses"]["200"]["content"]["application/json"]["schema"]["$ref"]
    詳情 = 規格["components"]["schemas"][詳情參照.rsplit("/", 1)[1]]
    assert list(鍵 for 鍵 in 詳情["properties"] if 鍵 == "sensitive_hits") == ["sensitive_hits"]
    item參照 = 詳情["properties"]["sensitive_hits"]["items"]["$ref"]
    item = 規格["components"]["schemas"][item參照.rsplit("/", 1)[1]]
    assert item["additionalProperties"] is False
    assert set(item["properties"]) == {
        "id", "target", "tool_call_id", "detector_type", "json_path", "start", "end", "detected_at",
    }
    assert item["properties"]["target"]["enum"] == [
        "input", "metadata", "response_data", "tool_arguments", "tool_result",
    ]
    assert item["required"] == [
        "id", "target", "tool_call_id", "detector_type", "json_path", "start", "end", "detected_at",
    ]
    assert "/api/published-endpoints/{endpoint_id}/invocations/{invocation_id}" not in 規格["paths"]
