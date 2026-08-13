"""MGT M01 擁有者與管理者發布端點查詢契約測試。"""

import inspect
import base64
import hashlib
import hmac
import json
import sys
import threading
from dataclasses import FrozenInstanceError
from types import MethodType

from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
import pytest

from 繁中代理.使用者 import 使用者上下文
from 繁中代理.發布介面.路由 import 規劃發布 as 規劃發布模組
from 繁中代理.發布介面.路由.端點查詢 import (
    建立端點查詢路由器,
    端點列表回應,
    端點列表項目,
    端點安全詳情,
)
from 繁中代理.發布介面.路由.規劃發布 import (
    _呼叫服務,
    _重建發布結果,
    _重建版本結果,
    建立規劃發布路由器,
    建立草稿請求,
    發布端點請求,
    建立版本請求,
    規劃內容,
    發布確認,
    草稿建立結果,
    端點發布結果,
    版本建立結果,
    管理操作錯誤,
)
from 繁中代理.發布介面.治理.管理查詢契約 import (
    ADMIN_INVOCATION_AUDIT_ACTION,
    ADMIN_INVOCATION_DETAIL_PATH,
    ADMIN_INVOCATION_ERROR_CONTRACT,
    ADMIN_INVOCATION_FORBIDDEN_QUERY_KEYS,
    ADMIN_INVOCATION_LIST_PATH,
    ADMIN_INVOCATION_METHOD,
    ADMIN_INVOCATION_QUERY_KEYS,
    ADMIN_INVOCATION_REJECT_DUPLICATE_QUERY_KEYS,
    管理員呼叫列表結果,
    管理員呼叫列表項目,
    建立管理員呼叫完整詳情,
    管理員呼叫查詢條件,
    管理員呼叫游標位置,
    管理員呼叫游標錯誤,
    管理員呼叫游標編解碼器,
)


class 假查詢服務:
    """以權威 owner 欄位模擬整合層的決定性查詢服務。"""

    def __init__(self):
        """建立兩位 owner 的三筆安全投影與呼叫紀錄。"""
        self.列表呼叫 = []
        self.詳情呼叫 = []
        self.資料 = {
            "e1": 端點安全詳情("e1", "owner-a", "alpha", "active", "v1", 1, 1.0, 4.0),
            "e2": 端點安全詳情("e2", "owner-a", "beta", "disabled", None, None, 2.0, 5.0),
            "e3": 端點安全詳情("e3", "owner-b", "gamma", "archived", "v3", 3, 3.0, 6.0),
        }

    def 列出端點(self, *, 擁有者使用者識別碼, 管理者查詢全部, 數量上限, 游標):
        """依 authoritative owner/admin 範圍與 pN 游標回傳一頁。"""
        self.列表呼叫.append((擁有者使用者識別碼, 管理者查詢全部, 數量上限, 游標))
        資料 = sorted(self.資料.values(), key=lambda 項目: 項目.端點識別碼)
        if not 管理者查詢全部:
            資料 = [項目 for 項目 in 資料 if 項目.擁有者使用者識別碼 == 擁有者使用者識別碼]
        起點 = int(游標[1:]) if 游標 else 0
        頁面 = 資料[起點 : 起點 + 數量上限]
        下一頁 = f"p{起點 + 數量上限}" if 起點 + 數量上限 < len(資料) else None
        return 端點列表回應(
            tuple(
                端點列表項目(
                    項目.端點識別碼,
                    項目.短名,
                    項目.狀態,
                    項目.目前版本識別碼,
                    項目.目前版本編號,
                    項目.更新時間,
                )
                for 項目 in 頁面
            ),
            下一頁,
        )

    def 讀取端點(self, *, 端點識別碼, 擁有者使用者識別碼, 管理者查詢全部):
        """外人與不存在皆回傳相同 None，管理者可讀全部。"""
        self.詳情呼叫.append((端點識別碼, 擁有者使用者識別碼, 管理者查詢全部))
        項目 = self.資料.get(端點識別碼)
        if 項目 is None or (not 管理者查詢全部 and 項目.擁有者使用者識別碼 != 擁有者使用者識別碼):
            return None
        return 項目


def _建立客戶端(身份, 服務=None):
    """建立 redirect_slashes=False 的真實 TestClient app。"""
    服務 = 服務 or 假查詢服務()
    app = FastAPI(redirect_slashes=False)
    app.include_router(建立端點查詢路由器(服務, lambda: 身份))
    return TestClient(app), 服務


def _身份(user_id="owner-a", is_admin=False):
    """建立測試用可信身份依賴結果。"""
    return 使用者上下文(user_id=user_id, is_admin=is_admin)


def test_擁有者預設列表隔離且身份header無法覆寫():
    """預設 scope=owner，只把注入 user_id 傳入服務。"""
    客戶端, 服務 = _建立客戶端(_身份())
    回應 = 客戶端.get("/api/published-endpoints", headers={"x-user-id": "owner-b", "x-admin": "true"})

    assert 回應.status_code == 200
    assert [項目["endpoint_id"] for 項目 in 回應.json()["items"]] == ["e1", "e2"]
    assert 服務.列表呼叫 == [("owner-a", False, 20, None)]


def test_擁有者空列表與決定性分頁游標():
    """空 owner 回空頁，limit/cursor 則逐頁且不重複。"""
    空客戶端, _ = _建立客戶端(_身份("nobody"))
    assert 空客戶端.get("/api/published-endpoints").json() == {"items": [], "next_cursor": None}

    客戶端, 服務 = _建立客戶端(_身份())
    第一頁 = 客戶端.get("/api/published-endpoints?limit=1").json()
    第二頁 = 客戶端.get(f"/api/published-endpoints?limit=1&cursor={第一頁['next_cursor']}").json()
    assert ([第一頁["items"][0]["endpoint_id"], 第二頁["items"][0]["endpoint_id"]], 第二頁["next_cursor"]) == (["e1", "e2"], None)
    assert 服務.列表呼叫[-1] == ("owner-a", False, 1, "p1")


@pytest.mark.parametrize("查詢", ["limit=0", "limit=101", "limit=9" + "9" * 5000, "cursor=bad%20cursor", "scope=other", "scope=owner&scope=all"])
def test_列表拒絕敵意或超界分頁與範圍(查詢):
    """框架邊界拒絕超界數字、巨大整數、非法游標與未知 scope。"""
    客戶端, 服務 = _建立客戶端(_身份())
    assert 客戶端.get(f"/api/published-endpoints?{查詢}").status_code == 422
    assert 服務.列表呼叫 == []


def test_管理者預設仍只查自己且明確all才查全部():
    """管理身份不會隱式擴大預設 owner 範圍。"""
    客戶端, 服務 = _建立客戶端(_身份(is_admin=True))
    assert len(客戶端.get("/api/published-endpoints").json()["items"]) == 2
    assert len(客戶端.get("/api/published-endpoints?scope=all").json()["items"]) == 3
    assert [呼叫[1] for 呼叫 in 服務.列表呼叫] == [False, True]


def test_非管理者all固定403且不呼叫全域查詢():
    """非管理者明確 all 固定拒絕，不可靜默降級。"""
    客戶端, 服務 = _建立客戶端(_身份())
    回應 = 客戶端.get("/api/published-endpoints?scope=all")
    assert 回應.status_code == 403
    assert 回應.json() == {"detail": "只有管理者可查詢全部發布端點"}
    assert 服務.列表呼叫 == []


def test_拒絕owner_id與其他未宣告篩選且零查詢():
    """呼叫者不能提供任意 owner_id 或重複 scope 之外參數。"""
    客戶端, 服務 = _建立客戶端(_身份(is_admin=True))
    assert 客戶端.get("/api/published-endpoints?owner_id=owner-b").status_code == 422
    assert 客戶端.get("/api/published-endpoints?status=active").status_code == 422
    assert 服務.列表呼叫 == []


def test_詳情允許擁有者及管理者查看且只回安全基本欄位():
    """管理者可 detail 全部；回應不含 M03/M04/M06 敏感或原始欄位。"""
    擁有者客戶端, _ = _建立客戶端(_身份())
    管理客戶端, 管理服務 = _建立客戶端(_身份(is_admin=True))
    擁有者資料 = 擁有者客戶端.get("/api/published-endpoints/e1").json()
    管理資料 = 管理客戶端.get("/api/published-endpoints/e3").json()
    安全欄位 = {"endpoint_id", "owner_user_id", "slug", "status", "current_version_id", "current_version_number", "created_at", "updated_at"}
    assert set(擁有者資料) == set(管理資料) == 安全欄位
    assert 管理服務.詳情呼叫 == [("e3", "owner-a", True)]
    assert not ({"credentials", "metrics", "docs", "diagnostics", "logs", "system_prompt", "raw"} & set(管理資料))


def test_外人與不存在詳情完全相同404防止IDOR():
    """foreign 與 missing 的 status/body 一致。"""
    客戶端, 服務 = _建立客戶端(_身份())
    外人 = 客戶端.get("/api/published-endpoints/e3")
    不存在 = 客戶端.get("/api/published-endpoints/nope")
    assert (外人.status_code, 外人.json()) == (不存在.status_code, 不存在.json()) == (404, {"detail": "找不到發布端點"})
    assert len(服務.詳情呼叫) == 2


def test_方法路徑OpenAPI與尾斜線契約精確():
    """只註冊兩條 GET，redirect_slashes=False 不接受尾斜線。"""
    客戶端, _ = _建立客戶端(_身份())
    assert 客戶端.post("/api/published-endpoints").status_code == 405
    assert 客戶端.get("/api/published-endpoint").status_code == 404
    assert 客戶端.get("/api/published-endpoints/").status_code == 404
    paths = 客戶端.get("/openapi.json").json()["paths"]
    assert set(paths) == {"/api/published-endpoints", "/api/published-endpoints/{endpoint_id}"}
    assert set(paths["/api/published-endpoints"]) == {"get"}
    參數名稱 = {參數["name"] for 參數 in paths["/api/published-endpoints"]["get"]["parameters"]}
    assert 參數名稱 == {"scope", "limit", "cursor"}


def test_詳情識別碼有界且非法值零服務查詢():
    """路徑識別碼只接受短 ASCII token，避免超長與控制字串耗用資源。"""
    客戶端, 服務 = _建立客戶端(_身份())
    assert 客戶端.get("/api/published-endpoints/bad%20id").status_code == 422
    assert 客戶端.get("/api/published-endpoints/" + "x" * 129).status_code == 422
    assert 服務.詳情呼叫 == []


@pytest.mark.parametrize("模式", ["dict", "oversized", "exception", "http_exception"])
def test_敵意adapter結果與例外固定500且不洩漏(模式):
    """不接受 dict/超量 DTO，服務例外 marker 不進回應。"""
    marker = "SECRET_M01_DO_NOT_LEAK"

    class 敵意服務(假查詢服務):
        """回傳三種不可信 adapter 結果。"""

        def 列出端點(self, **kwargs):
            """依模式回傳非法結果或丟含 marker 例外。"""
            if 模式 == "dict":
                return {"items": []}
            if 模式 == "oversized":
                項目 = 端點列表項目("e", "s", "active", None, None, 1.0)
                return 端點列表回應((項目, 項目), None)
            if 模式 == "exception":
                raise ValueError(marker)
            raise HTTPException(status_code=418, detail=marker)

    客戶端, _ = _建立客戶端(_身份(), 敵意服務())
    回應 = 客戶端.get("/api/published-endpoints?limit=1")
    assert 回應.status_code == 500
    assert 回應.json() == {"detail": "管理查詢服務回傳無效"}
    assert marker not in 回應.text


class 假發布管理服務:
    """只記錄三個整合操作，讓路由副作用可被精確觀察。"""

    def __init__(self):
        """建立呼叫紀錄與可替換結果。"""
        self.草稿呼叫 = []
        self.發布呼叫 = []
        self.版本呼叫 = []
        self.草稿結果 = 草稿建立結果("draft-1", 2000.0, {"summary": "safe"})
        self.發布結果 = 端點發布結果("endpoint-1", "version-1", 1, "active", "pak_INITIAL_SECRET_123")
        self.版本結果 = 版本建立結果("endpoint-1", "version-2", 2, "version-2", True)

    def 建立草稿(self, **參數):
        """記錄純草稿委派。"""
        self.草稿呼叫.append(參數)
        return self.草稿結果

    def 原子發布(self, **參數):
        """以單一呼叫代表 endpoint/v1/credential/audit 交易。"""
        self.發布呼叫.append(參數)
        return self.發布結果

    def 原子建立並切換版本(self, **參數):
        """以單一呼叫代表 immutable insert 與 pointer switch。"""
        self.版本呼叫.append(參數)
        return self.版本結果


def _建立整合客戶端(身份=None, 發布服務=None):
    """建立同時掛載既有 M01 與 M02 的真實 app。"""
    身份 = 身份 or _身份()
    查詢服務 = 假查詢服務()
    發布服務 = 發布服務 or 假發布管理服務()
    app = FastAPI(redirect_slashes=False)
    app.include_router(建立端點查詢路由器(查詢服務, lambda: 身份))
    app.include_router(建立規劃發布路由器(發布服務, lambda: 身份))
    return TestClient(app, raise_server_exceptions=False), 發布服務, app


def _草稿body():
    """回傳最小合法且包含 planner 內容的草稿 body。"""
    return {"original_requirement_text": "建立摘要服務", "planner_content": {"skills": ["summarize"], "outline": {"system_prompt": "safe"}}}


def _發布body():
    """回傳最小合法發布確認 body。"""
    return {"draft_id": "draft-1", "slug": "summary-api", "configuration_confirmation": {"response_schema": {"type": "object"}}}


def test_M02草稿固定201且只有草稿投影並零發布副作用():
    """草稿傳 owner/planner，回應不含 endpoint、credential 或 API key。"""
    客戶端, 服務, _ = _建立整合客戶端()
    回應 = 客戶端.post("/api/published-endpoints/draft", json=_草稿body(), headers={"x-user-id": "attacker"})
    assert (回應.status_code, 回應.json()) == (201, {"draft_id": "draft-1", "expires_at": 2000.0, "preview": {"summary": "safe"}})
    assert set(回應.json()) == {"draft_id", "expires_at", "preview"}
    assert 服務.草稿呼叫[0]["擁有者使用者識別碼"] == "owner-a"
    assert 服務.草稿呼叫[0]["規劃"].內容 == _草稿body()["planner_content"]
    assert 服務.發布呼叫 == 服務.版本呼叫 == []


def test_M02發布只呼叫一個原子方法且初始金鑰成功時只出現一次():
    """固定 201 envelope；route 不拆 endpoint/version/credential/audit 操作。"""
    客戶端, 服務, _ = _建立整合客戶端()
    回應 = 客戶端.post("/api/published-endpoints", json=_發布body())
    assert 回應.status_code == 201
    assert 回應.json() == {"endpoint_id": "endpoint-1", "version_id": "version-1", "version_number": 1, "status": "active", "initial_api_key": "pak_INITIAL_SECRET_123"}
    assert 回應.text.count("pak_INITIAL_SECRET_123") == 1
    assert len(服務.發布呼叫) == 1
    assert 服務.草稿呼叫 == 服務.版本呼叫 == []


@pytest.mark.parametrize("管理者", [False, True])
def test_M02版本只傳權威使用者識別碼給單一原子切換操作(管理者):
    """用戶端角色不是授權依據；服務以權威使用者識別碼重查權限。"""
    客戶端, 服務, _ = _建立整合客戶端(_身份(is_admin=管理者))
    回應 = 客戶端.post("/api/published-endpoints/endpoint-1/versions", json={"configuration": {"system_prompt": "new"}})
    assert (回應.status_code, 回應.json()) == (201, {"endpoint_id": "endpoint-1", "version_id": "version-2", "version_number": 2, "current_version_id": "version-2", "schema_changed": True})
    assert 服務.版本呼叫 == [{"擁有者使用者識別碼": "owner-a", "端點識別碼": "endpoint-1", "配置": {"system_prompt": "new"}}]


@pytest.mark.parametrize(
    "路徑,body",
    [
        ("/api/published-endpoints/draft", {}),
        ("/api/published-endpoints/draft", {**_草稿body(), "extra": 1}),
        ("/api/published-endpoints", {**_發布body(), "slug": 7}),
        ("/api/published-endpoints", {**_發布body(), "configuration_confirmation": []}),
        ("/api/published-endpoints/endpoint-1/versions", {"configuration": "bad"}),
        ("/api/published-endpoints/endpoint-1/versions", {"configuration": {}, "old_version": "v1"}),
    ],
)
def test_M02拒絕缺少額外錯型且服務零呼叫(路徑, body):
    """三個 exact Pydantic body 都 forbid extras 且 strict。"""
    客戶端, 服務, _ = _建立整合客戶端()
    assert 客戶端.post(路徑, json=body).status_code == 422
    assert 服務.草稿呼叫 == 服務.發布呼叫 == 服務.版本呼叫 == []


@pytest.mark.parametrize("種類,狀態", [("invalid", 422), ("draft_not_found", 404), ("endpoint_not_found", 404), ("forbidden", 403), ("status_conflict", 409), ("concurrency", 409), ("internal", 500)])
def test_M02固定領域錯誤映射且不含key欄位(種類, 狀態):
    """錯誤只由 module-owned DTO 映射，不接受任意 HTTP status/detail。"""
    服務 = 假發布管理服務()
    服務.發布結果 = 管理操作錯誤(種類)
    客戶端, _, _ = _建立整合客戶端(發布服務=服務)
    回應 = 客戶端.post("/api/published-endpoints", json=_發布body())
    assert 回應.status_code == 狀態
    assert set(回應.json()) == {"detail"}
    assert not ({"api_key", "initial_api_key", "credential", "key"} & set(回應.json()))


@pytest.mark.parametrize("例外", [ValueError("SECRET_KEY_MARKER"), HTTPException(status_code=418, detail="SECRET_KEY_MARKER")])
def test_M02服務例外固定500且不穿透HTTPException或秘密(例外):
    """callback 的任意一般例外都成固定 500，不洩漏 marker。"""
    class 失敗服務(假發布管理服務):
        def 原子發布(self, **參數):
            raise 例外

    客戶端, _, _ = _建立整合客戶端(發布服務=失敗服務())
    回應 = 客戶端.post("/api/published-endpoints", json=_發布body())
    assert (回應.status_code, 回應.json()) == (500, {"detail": "發布管理服務失敗"})
    assert "SECRET_KEY_MARKER" not in 回應.text


def test_M02敵意結果與端點不符固定500且不回初始key():
    """exact result 仍須逐槽重建，版本 endpoint mismatch fail closed。"""
    服務 = 假發布管理服務()
    服務.發布結果 = {"initial_api_key": "SECRET_KEY_MARKER"}
    客戶端, _, _ = _建立整合客戶端(發布服務=服務)
    回應 = 客戶端.post("/api/published-endpoints", json=_發布body())
    assert 回應.status_code == 500 and "SECRET_KEY_MARKER" not in 回應.text
    服務.版本結果 = 版本建立結果("other", "version-2", 2, "version-2", False)
    assert 客戶端.post("/api/published-endpoints/endpoint-1/versions", json={"configuration": {}}).status_code == 500

    竄改結果 = 端點發布結果("endpoint-1", "version-1", 1, "active", "SECRET_KEY_MARKER_123")
    object.__setattr__(竄改結果, "狀態", "disabled")
    服務.發布結果 = 竄改結果
    竄改回應 = 客戶端.post("/api/published-endpoints", json=_發布body())
    assert 竄改回應.status_code == 500 and "SECRET_KEY_MARKER" not in 竄改回應.text

    class 結果子類(端點發布結果):
        pass

    服務.發布結果 = 結果子類("endpoint-1", "version-1", 1, "active", "SECRET_KEY_MARKER_123")
    assert 客戶端.post("/api/published-endpoints", json=_發布body()).status_code == 500


def test_M02_KISG控制流不會被固定500吞掉():
    """直接呼叫 route endpoint，證明 Python 終止控制流保持原型別。"""
    class 中斷服務(假發布管理服務):
        def 建立草稿(self, **參數):
            raise KeyboardInterrupt("STOP")

    _, _, app = _建立整合客戶端(發布服務=中斷服務())
    endpoint = next(route.endpoint for route in app.routes if getattr(route, "path", None) == "/api/published-endpoints/draft" and "POST" in route.methods)
    with pytest.raises(KeyboardInterrupt, match="STOP"):
        endpoint(建立草稿請求(**_草稿body()), _身份())


def test_M01_M02整合路徑方法OpenAPI尾斜線且無重複():
    """同一 redirect_slashes=False app 精確提供 M01 GET 與 M02 POST。"""
    客戶端, _, app = _建立整合客戶端()
    paths = 客戶端.get("/openapi.json").json()["paths"]
    assert set(paths["/api/published-endpoints"]) == {"get", "post"}
    assert set(paths["/api/published-endpoints/draft"]) == {"post"}
    assert set(paths["/api/published-endpoints/{endpoint_id}/versions"]) == {"post"}
    assert 客戶端.get("/api/published-endpoints/draft").status_code == 404
    assert 客戶端.post("/api/published-endpoints/draft/").status_code == 404
    assert 客戶端.post("/api/published-endpoint", json={}).status_code == 404
    operation_ids = [操作["operationId"] for path in paths.values() for 操作 in path.values() if "operationId" in 操作]
    assert len(operation_ids) == len(set(operation_ids))
    schemas = 客戶端.get("/openapi.json").json()["components"]["schemas"].values()
    欄位集合 = {frozenset(schema.get("properties", {})) for schema in schemas}
    assert frozenset({"endpoint_id", "version_id", "version_number", "status", "initial_api_key"}) in 欄位集合
    assert frozenset({"endpoint_id", "version_id", "version_number", "current_version_id", "schema_changed"}) in 欄位集合


def _正規化OpenAPI綱要(結構, 綱要):
    """展開 local ref／單一 allOf wrapper，並移除非語意 title／description。"""
    if "$ref" in 綱要:
        參照 = 綱要["$ref"]
        前綴 = "#/components/schemas/"
        assert 參照.startswith(前綴)
        已展開 = _正規化OpenAPI綱要(
            結構, 結構["components"]["schemas"][參照[len(前綴):]],
        )
        同層限制 = _正規化OpenAPI綱要(
            結構, {鍵: 值 for 鍵, 值 in 綱要.items() if 鍵 != "$ref"},
        )
        return 已展開 if not 同層限制 else {"allOf": [已展開, 同層限制]}
    if len(綱要.get("allOf", ())) == 1:
        已展開 = _正規化OpenAPI綱要(結構, 綱要["allOf"][0])
        同層限制 = _正規化OpenAPI綱要(
            結構, {鍵: 值 for 鍵, 值 in 綱要.items() if 鍵 != "allOf"},
        )
        return 已展開 if not 同層限制 else {"allOf": [已展開, 同層限制]}
    return {
        鍵: (
            _正規化OpenAPI綱要(結構, 值) if type(值) is dict
            else [_正規化OpenAPI綱要(結構, 項目) if type(項目) is dict else 項目 for 項目 in 值]
            if type(值) is list else 值
        )
        for 鍵, 值 in 綱要.items() if 鍵 not in {"title", "description"}
    }


def test_M02_OpenAPI綱要正規化接受inline_local_ref與單一allOf包裝():
    """同一公開語意不因 framework 選擇 inline、local ref 或單一 allOf 表示而漂移。"""
    結構 = {"components": {"schemas": {
        "任意Opaque名稱": {
            "title": "非語意名稱", "description": "非語意文件", "type": "string",
            "minLength": 1,
        },
    }}}
    預期 = {"type": "string", "minLength": 1}
    assert _正規化OpenAPI綱要(結構, {**預期, "title": "Inline"}) == 預期
    assert _正規化OpenAPI綱要(
        結構, {"$ref": "#/components/schemas/任意Opaque名稱"},
    ) == 預期
    assert _正規化OpenAPI綱要(
        結構, {
            "allOf": [{"$ref": "#/components/schemas/任意Opaque名稱"}],
            "description": "Wrapper",
        },
    ) == 預期
    assert _正規化OpenAPI綱要(
        結構, {
            "$ref": "#/components/schemas/任意Opaque名稱",
            "maxLength": 8,
        },
    ) == {"allOf": [預期, {"maxLength": 8}]}


def test_M02_OpenAPI以可審查語意快照鎖定公開契約而不綁框架bytes():
    """鎖定三條 M02 公開契約，不綁 FastAPI／Pydantic 產生器的非語意 bytes。"""
    app = FastAPI(redirect_slashes=False)
    app.include_router(建立規劃發布路由器(假發布管理服務(), lambda: _身份()))
    結構 = TestClient(app).get("/openapi.json").json()
    契約 = {
        "/api/published-endpoints/draft": {
            "operationId": "建立發布草稿_api_published_endpoints_draft_post",
            "description": "建立純草稿，不配置 endpoint、版本或憑證。",
            "request": {
                "original_requirement_text": {"type": "string", "minLength": 1, "maxLength": 16384},
                "planner_content": {"type": "object", "additionalProperties": {}},
            },
            "response": {
                "draft_id": {"type": "string"}, "expires_at": {"type": "number"},
                "preview": {"type": "object", "additionalProperties": {}},
            },
        },
        "/api/published-endpoints": {
            "operationId": "發布端點_api_published_endpoints_post",
            "description": "只委派一次原子服務操作，回傳初始明文金鑰一次。",
            "request": {
                "draft_id": {
                    "type": "string", "minLength": 1, "maxLength": 128,
                    "pattern": "^[A-Za-z0-9_.:-]+$",
                },
                "slug": {
                    "type": "string", "minLength": 1, "maxLength": 63,
                    "pattern": "^[a-z0-9][a-z0-9-]*$",
                },
                "configuration_confirmation": {"type": "object", "additionalProperties": {}},
            },
            "response": {
                "endpoint_id": {"type": "string"}, "version_id": {"type": "string"},
                "version_number": {"type": "integer"}, "status": {"type": "string"},
                "initial_api_key": {"type": "string"},
            },
        },
        "/api/published-endpoints/{endpoint_id}/versions": {
            "operationId": "建立不可變版本_api_published_endpoints__endpoint_id__versions_post",
            "description": "由服務一次完成 owner/admin 授權、create-only insert 與 pointer switch。",
            "request": {"configuration": {"type": "object", "additionalProperties": {}}},
            "response": {
                "endpoint_id": {"type": "string"}, "version_id": {"type": "string"},
                "version_number": {"type": "integer"}, "current_version_id": {"type": "string"},
                "schema_changed": {"type": "boolean"},
            },
        },
    }
    assert set(結構["paths"]) == set(契約)
    for 路徑, 預期 in 契約.items():
        assert set(結構["paths"][路徑]) == {"post"}
        操作 = 結構["paths"][路徑]["post"]
        assert 操作["operationId"] == 預期["operationId"]
        assert 操作["description"] == 預期["description"]
        assert "partial application" not in 操作["description"]
        assert 操作["requestBody"]["required"] is True
        assert set(操作["responses"]) == {"201", "422"}
        請求內容 = 操作["requestBody"]["content"]
        成功內容 = 操作["responses"]["201"]["content"]
        驗證錯誤內容 = 操作["responses"]["422"]["content"]
        assert set(請求內容) == set(成功內容) == set(驗證錯誤內容) == {"application/json"}

        請求綱要 = _正規化OpenAPI綱要(結構, 請求內容["application/json"]["schema"])
        回應綱要 = _正規化OpenAPI綱要(結構, 成功內容["application/json"]["schema"])
        assert set(請求綱要) == {"type", "properties", "required", "additionalProperties"}
        assert 請求綱要["type"] == "object" and 請求綱要["additionalProperties"] is False
        assert set(請求綱要["required"]) == set(預期["request"])
        assert 請求綱要["properties"] == 預期["request"]
        assert set(回應綱要) == {"type", "properties", "required"}
        assert 回應綱要["type"] == "object"
        assert set(回應綱要["required"]) == set(預期["response"])
        assert 回應綱要["properties"] == 預期["response"]

        驗證錯誤綱要 = _正規化OpenAPI綱要(
            結構, 驗證錯誤內容["application/json"]["schema"],
        )
        assert 驗證錯誤綱要 == {
            "type": "object",
            "properties": {"detail": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "loc": {
                            "type": "array", "items": {
                                "anyOf": [{"type": "string"}, {"type": "integer"}],
                            },
                        },
                        "msg": {"type": "string"},
                        "type": {"type": "string"},
                    },
                    "required": ["loc", "msg", "type"],
                },
            }},
        }

    版本參數 = 結構["paths"]["/api/published-endpoints/{endpoint_id}/versions"]["post"]["parameters"]
    assert len(版本參數) == 1
    assert {鍵: 版本參數[0][鍵] for 鍵 in ("name", "in", "required")} == {
        "name": "endpoint_id", "in": "path", "required": True,
    }
    assert _正規化OpenAPI綱要(結構, 版本參數[0]["schema"]) == {
        "type": "string", "minLength": 1, "maxLength": 128,
        "pattern": "^[A-Za-z0-9_.:-]+$",
    }


class _敵意字串(str):
    """不得被當成可信內建字串的子類。"""

    雜湊呼叫 = 0
    相等呼叫 = 0
    不等呼叫 = 0
    編碼呼叫 = 0

    def __hash__(self):
        type(self).雜湊呼叫 += 1
        return str.__hash__(self)

    def __eq__(self, 其他):
        type(self).相等呼叫 += 1
        raise AssertionError("不得呼叫敵意 __eq__")

    def __ne__(self, 其他):
        type(self).不等呼叫 += 1
        raise AssertionError("不得呼叫敵意 __ne__")

    def encode(self, *參數, **關鍵字):
        type(self).編碼呼叫 += 1
        raise AssertionError("不得呼叫敵意 encode")


class _敵意整數(int):
    """不得被當成可信內建整數的子類。"""


class _敵意基底例外(BaseException):
    """代表非 Python 終止控制流的一般 BaseException。"""


def _含marker(值, marker, 已見=None):
    """只沿 exact-known DTO、容器與例外 args 尋找 marker。"""
    if 已見 is None:
        已見 = set()
    if id(值) in 已見:
        return False
    已見.add(id(值))
    if isinstance(值, str):
        return str.__contains__(值, marker)
    if type(值) is bytes:
        return marker.encode("utf-8") in 值
    if type(值) in (list, tuple, dict):
        項目 = dict.items(值) if type(值) is dict else enumerate(值)
        return any(_含marker(鍵, marker, 已見) or _含marker(內容, marker, 已見) for 鍵, 內容 in 項目)
    if type(值) in (規劃內容, 發布確認, 草稿建立結果, 端點發布結果, 版本建立結果, 管理操作錯誤):
        類型 = type(值)
        return any(_含marker(槽.__get__(值, 類型), marker, 已見) for 槽 in (type.__getattribute__(類型, 名稱) for 名稱 in 類型.__slots__))
    if type(值) in (建立草稿請求, 發布端點請求, 建立版本請求):
        return any(_含marker(object.__getattribute__(值, 名稱), marker, 已見) for 名稱 in type(值).model_fields)
    if type(值) is 使用者上下文:
        return _含marker(object.__getattribute__(值, "user_id"), marker, 已見)
    if type(值) in (假查詢服務, 假發布管理服務):
        欄位 = ("資料",) if type(值) is 假查詢服務 else ("草稿結果", "發布結果", "版本結果")
        return any(_含marker(object.__getattribute__(值, 名稱), marker, 已見) for 名稱 in 欄位)
    if type(值) is MethodType:
        return _含marker(MethodType.__self__.__get__(值, MethodType), marker, 已見)
    if type(值) is JSONResponse:
        return _含marker(object.__getattribute__(值, "body"), marker, 已見) or _含marker(object.__getattribute__(值, "raw_headers"), marker, 已見)
    if isinstance(值, BaseException):
        return _含marker(值.args, marker, 已見)
    return False


def _生產traceback不含marker(錯誤, marker):
    """每個 production local 使用全新 visited，避免 id 重用假陰性。"""
    traceback物件 = 錯誤.__traceback__
    while traceback物件 is not None:
        frame = traceback物件.tb_frame
        if frame.f_globals.get("__name__") == "繁中代理.發布介面.路由.規劃發布":
            for 區域值 in tuple(frame.f_locals.values()):
                assert not _含marker(區域值, marker, set()), frame.f_code.co_name
        traceback物件 = traceback物件.tb_next


def _程式行號(函式, 唯一片段):
    """由 production 原始碼找出唯一 trace line，避免硬編行號。"""
    原始碼, 起始行 = inspect.getsourcelines(函式)
    行號 = [起始行 + 索引 for 索引, 內容 in enumerate(原始碼) if 唯一片段 in 內容]
    assert len(行號) == 1
    return 行號[0]


def _以行追蹤中斷(目標, 唯一片段, 原例外, 呼叫):
    """只在指定 production code/line 丟一次原 KISG，並必定還原 trace。"""
    目標行 = _程式行號(目標, 唯一片段)
    原追蹤 = sys.gettrace()
    已觸發 = []

    def 追蹤(frame, event, arg):
        if not 已觸發 and event == "line" and frame.f_code is 目標.__code__ and frame.f_lineno == 目標行:
            已觸發.append((frame.f_code, frame.f_lineno))
            raise 原例外
        return 追蹤

    sys.settrace(追蹤)
    try:
        呼叫()
    except BaseException:
        assert 已觸發 == [(目標.__code__, 目標行)]
        raise
    finally:
        sys.settrace(原追蹤)


def _traceback框架(錯誤):
    """以名稱索引 traceback frames，供清理後 exact-local 斷言。"""
    結果 = {}
    traceback物件 = 錯誤.__traceback__
    while traceback物件 is not None:
        結果.setdefault(traceback物件.tb_frame.f_code.co_name, []).append(traceback物件.tb_frame)
        traceback物件 = traceback物件.tb_next
    return 結果


def test_M02_traceback掃描器正向控制可抓直接鍵值與假生產框架():
    """證明 marker oracle 真的沿 dict key/value、bytes 與 production local 掃描。"""
    marker = "MARKER_ORACLE_POSITIVE"
    assert _含marker({marker + "_KEY": marker + "_VALUE"}, marker, set())
    assert _含marker(marker.encode(), marker, set())
    命名空間 = {"__name__": "繁中代理.發布介面.路由.規劃發布"}
    exec("def 假生產洩漏(標記, 錯誤):\n    敏感 = {'key': 標記}\n    raise 錯誤", 命名空間)
    原例外 = KeyboardInterrupt("CONTROL")
    with pytest.raises(KeyboardInterrupt) as 捕捉:
        命名空間["假生產洩漏"](marker, 原例外)
    with pytest.raises(AssertionError, match="假生產洩漏"):
        _生產traceback不含marker(捕捉.value, marker)


def test_M02_traceback掃描器正向控制涵蓋精確DTO方法服務與JSON回應():
    """每個 marker 只經新增的 exact-known 路徑可達，避免 traceback 隱私假陰性。"""
    marker = "MARKER_SCANNER_EXACT_PATH"
    錯誤 = 管理操作錯誤("internal")
    object.__setattr__(錯誤, "種類", marker)
    服務 = 假發布管理服務()
    服務.草稿結果 = 草稿建立結果("draft-1", 1.0, {"only": marker})
    案例 = [
        規劃內容(marker, {}),
        發布確認("draft-1", "safe", {"only": marker}),
        錯誤,
        服務,
        服務.建立草稿,
        JSONResponse(content={"only": marker}),
        JSONResponse(content={"safe": True}, headers={"x-private-marker": marker}),
    ]
    assert all(_含marker(值, marker, set()) for 值 in 案例)


@pytest.mark.parametrize("例外類型", [KeyboardInterrupt, SystemExit, GeneratorExit])
@pytest.mark.parametrize(
    "目標名稱,唯一片段,敏感區域",
    [
        ("_建立JSON快照", "捕捉紀錄 = (來源, 鍵們", {"值", "安全值", "子項", "鍵們", "觀察值", "部分", "來源", "鍵", "值類型", "編碼值", "捕捉紀錄", "計數", "擷取"}),
        ("_重播JSON容器", "if not _JSON子項相同(原值, 現值)", {"擷取", "紀錄", "來源", "鍵們", "觀察值", "原值", "現值", "鍵", "鍵迭代器", "現鍵們"}),
        ("_JSON子項相同", "比較結果 = 現值 == 原值", {"原值", "現值", "原類型", "現類型", "原浮點位元", "現浮點位元", "比較結果"}),
    ],
    ids=["build", "replay", "compare"],
)
def test_M02真實快照遍歷比較KISG清除所有敏感區域(例外類型, 目標名稱, 唯一片段, 敏感區域):
    """行追蹤命中實際 snapshot→replay→compare 內部，而非替換整個 helper。"""
    marker = "MARKER_TRUTHFUL_INTERNAL_KISG"
    鍵 = marker + "_PARENT_KEY"
    值 = marker + "_DETACHED_VALUE"
    來源 = 草稿建立結果("draft-1", 2000.0, {鍵: 值})
    原例外 = 例外類型(marker, 目標名稱)
    目標 = getattr(規劃發布模組, 目標名稱)

    with pytest.raises(例外類型) as 捕捉:
        _以行追蹤中斷(目標, 唯一片段, 原例外, lambda: 規劃發布模組._重建草稿結果(來源))
    來源 = 鍵 = 值 = 目標 = None

    assert 捕捉.value is 原例外
    assert 捕捉.value.args == (marker, 目標名稱)
    assert 捕捉.value.__cause__ is None and 捕捉.value.__context__ is None
    框架 = _traceback框架(捕捉.value)
    assert {"_重建草稿結果", "_複製JSON物件", 目標名稱} <= set(框架)
    目標區域 = 框架[目標名稱][-1].f_locals
    assert 敏感區域 <= set(目標區域)
    assert all(目標區域[名稱] is None for 名稱 in 敏感區域)
    _生產traceback不含marker(捕捉.value, marker)


def _深層預覽():
    """建立超過服務回執資源上限的 exact list tree。"""
    值 = "x"
    for _ in range(25):
        值 = [值]
    return {"deep": 值}


@pytest.mark.parametrize(
    "欄位,惡意值",
    [
        ("到期時間", 10**10000),
        ("到期時間", float("nan")),
        ("預覽", _深層預覽()),
        ("預覽", {str(索引): 索引 for 索引 in range(257)}),
        ("草稿識別碼", _敵意字串("draft-1")),
    ],
    ids=["huge-time", "nan-time", "deep-preview", "wide-preview", "str-subclass"],
)
def test_M02草稿回執所有重建失敗固定JSON500且只呼叫一次(欄位, 惡意值):
    """精確 DTO 內的算術、資源與 built-in subclass 都被 totalize。"""
    服務 = 假發布管理服務()
    object.__setattr__(服務.草稿結果, 欄位, 惡意值)
    客戶端, _, _ = _建立整合客戶端(發布服務=服務)
    回應 = 客戶端.post("/api/published-endpoints/draft", json=_草稿body())
    assert (回應.status_code, 回應.json()) == (500, {"detail": "發布管理服務失敗"})
    assert len(服務.草稿呼叫) == 1


@pytest.mark.parametrize(
    "結果",
    [
        端點發布結果("endpoint-1", "version-1", _敵意整數(1), "active", "MARKER_INITIAL_KEY_123"),
        版本建立結果("endpoint-1", "version-2", 10**10000, "version-2", True),
        版本建立結果("endpoint-1", "version-2", 2, "wrong", False),
    ],
    ids=["publish-int-subclass", "version-huge-int", "version-mismatch"],
)
def test_M02發布與版本畸形精確回執固定JSON500(結果):
    """發布與版本 exact receipt 亦不可逃出 framework generic error body。"""
    服務 = 假發布管理服務()
    路徑, body = "/api/published-endpoints", _發布body()
    if type(結果) is 端點發布結果:
        服務.發布結果 = 結果
    else:
        服務.版本結果 = 結果
        路徑, body = "/api/published-endpoints/endpoint-1/versions", {"configuration": {}}
    客戶端, _, _ = _建立整合客戶端(發布服務=服務)
    回應 = 客戶端.post(路徑, json=body)
    assert (回應.status_code, 回應.json()) == (500, {"detail": "發布管理服務失敗"})
    assert len(服務.發布呼叫) + len(服務.版本呼叫) == 1
    assert "MARKER_INITIAL_KEY" not in 回應.text
    assert "MARKER_INITIAL_KEY" not in repr(回應.headers)


def test_M02初始金鑰在其他欄位失敗時不留於生產traceback():
    """先驗證非秘密槽；失敗前後 production locals 都不保留明文 key。"""
    marker = "MARKER_REAL_INITIAL_KEY_987654321"
    回執 = 端點發布結果("endpoint-1", "version-1", 1, "disabled", marker)
    assert marker not in repr(回執)
    assert _含marker(marker, marker, set())
    assert _含marker(回執, marker, set())
    with pytest.raises(HTTPException) as 捕捉:
        _重建發布結果(回執)
    回執 = None
    assert (捕捉.value.status_code, 捕捉.value.detail) == (500, "發布管理服務失敗")
    assert marker not in repr(捕捉.value)
    _生產traceback不含marker(捕捉.value, marker)


@pytest.mark.parametrize("例外類型", [KeyboardInterrupt, SystemExit, GeneratorExit])
def test_M02_KISG身份參數與服務邊界traceback隱私(例外類型):
    """控制流保持 identity/args，呼叫邊界 locals 不保留 marker。"""
    marker = "MARKER_KISG_INITIAL_KEY_24680"
    原例外 = 例外類型(marker)

    class 控制服務:
        def 建立草稿(self, **參數):
            raise 原例外

    with pytest.raises(例外類型) as 捕捉:
        _呼叫服務(控制服務(), "建立草稿", 擁有者使用者識別碼="owner-a")
    assert 捕捉.value is 原例外 and 捕捉.value.args == (marker,)
    _生產traceback不含marker(捕捉.value, marker)


def test_M02自訂BaseException也固定500():
    """只有 KISG 穿透；其他 BaseException 一律為固定公開錯誤。"""
    class 失敗服務:
        def 建立草稿(self, **參數):
            raise _敵意基底例外("MARKER_HOSTILE_BASE")

    with pytest.raises(HTTPException) as 捕捉:
        _呼叫服務(失敗服務(), "建立草稿")
    assert (捕捉.value.status_code, 捕捉.value.detail) == (500, "發布管理服務失敗")


def _在預覽重播前變更(monkeypatch, 預覽, 變更):
    """以 replay 私有 seam 與 Events 決定性命中 snapshot/replay 間隙。"""
    已到重播 = threading.Event()
    已完成變更 = threading.Event()
    執行緒錯誤 = []
    原重播 = 規劃發布模組._重播JSON容器

    def 門控重播(擷取):
        if not any(紀錄[0] is 預覽 for 紀錄 in 擷取):
            return 原重播(擷取)
        已到重播.set()
        if not 已完成變更.wait(2):
            raise RuntimeError("mutation thread timeout")
        return 原重播(擷取)

    def 執行變更():
        try:
            if not 已到重播.wait(2):
                raise RuntimeError("snapshot did not reach replay")
            變更(預覽)
        except BaseException as 錯誤:
            執行緒錯誤.append(錯誤)
        finally:
            已完成變更.set()

    monkeypatch.setattr(規劃發布模組, "_重播JSON容器", 門控重播)
    執行緒 = threading.Thread(target=執行變更)
    執行緒.start()
    return 執行緒, 已到重播, 執行緒錯誤


def _重設敵意字串計數():
    """把插入敵意 dict key 本身必然觸發的 hash 隔離在受測邊界前。"""
    _敵意字串.雜湊呼叫 = _敵意字串.相等呼叫 = _敵意字串.不等呼叫 = _敵意字串.編碼呼叫 = 0


def _敵意字串計數():
    """回傳 hash/eq/ne/encode 可觀察 callback 次數。"""
    return (_敵意字串.雜湊呼叫, _敵意字串.相等呼叫, _敵意字串.不等呼叫, _敵意字串.編碼呼叫)


def _替換成敵意鍵(來源, 原鍵, 敵意鍵):
    """先完成 delete+insert，再重設計數，避免把 mutation 自身誤算成 production。"""
    _重設敵意字串計數()
    值 = dict.__getitem__(來源, 原鍵)
    dict.__delitem__(來源, 原鍵)
    dict.__setitem__(來源, 敵意鍵, 值)
    assert _敵意字串計數() == (1, 0, 0, 0)
    _重設敵意字串計數()


def _證明敵意鍵oracle會觸發(來源, 原鍵):
    """隔離後故意用不安全 lookup，證明同一 dict 的零計數不是空 oracle。"""
    with pytest.raises(AssertionError, match="不得呼叫敵意 __eq__"):
        dict.__getitem__(來源, 原鍵)
    assert _敵意字串.相等呼叫 > 0


def test_M02建構時並行敵意字典鍵先精確拒絕且零callback():
    """在真實 items 建構行前換鍵；插入 hash 隔離後不觸發 hash/eq/ne。"""
    marker = "MARKER_HOSTILE_BUILD_KEY"
    原鍵 = marker + "_SAFE"
    敵意鍵 = _敵意字串(原鍵)
    預覽 = {原鍵: "safe"}
    來源 = 草稿建立結果("draft-1", 2000.0, 預覽)
    目標 = 規劃發布模組._建立JSON快照
    目標行 = _程式行號(目標, "鍵項迭代器 = iter(dict.items(來源))")
    原追蹤 = sys.gettrace()
    已變更 = []

    def 追蹤(frame, event, arg):
        if (not 已變更 and event == "line" and frame.f_code is 目標.__code__
                and frame.f_lineno == 目標行 and frame.f_locals.get("來源") is 預覽):
            _替換成敵意鍵(預覽, 原鍵, 敵意鍵)
            已變更.append((frame.f_code, frame.f_lineno))
        return 追蹤

    sys.settrace(追蹤)
    try:
        with pytest.raises(HTTPException) as 捕捉:
            規劃發布模組._重建草稿結果(來源)
    finally:
        sys.settrace(原追蹤)

    assert sys.gettrace() is 原追蹤
    assert 已變更 == [(目標.__code__, 目標行)]
    assert (捕捉.value.status_code, 捕捉.value.detail) == (500, "發布管理服務失敗")
    assert _敵意字串計數() == (0, 0, 0, 0)
    來源 = 目標 = None
    _生產traceback不含marker(捕捉.value, marker)
    _證明敵意鍵oracle會觸發(預覽, 原鍵)


def test_M02重播時並行敵意字典鍵真實重播零callback且不發布(monkeypatch):
    """wrapper 只在委派 ORIGINAL 前換鍵；真實 replay frame 精確拒絕且固定 500。"""
    marker = "MARKER_HOSTILE_REPLAY_KEY"
    原鍵 = marker + "_SAFE"
    敵意鍵 = _敵意字串(原鍵)
    預覽 = {原鍵: "safe"}
    服務 = 假發布管理服務()
    服務.草稿結果 = 草稿建立結果("draft-1", 2000.0, 預覽)
    原重播 = 規劃發布模組._重播JSON容器
    原重播呼叫 = []

    def 變更後重播(擷取):
        if any(紀錄[0] is 預覽 for 紀錄 in 擷取):
            _替換成敵意鍵(預覽, 原鍵, 敵意鍵)
            原重播呼叫.append(True)
        return 原重播(擷取)

    monkeypatch.setattr(規劃發布模組, "_重播JSON容器", 變更後重播)
    客戶端, _, _ = _建立整合客戶端(發布服務=服務)
    回應 = 客戶端.post("/api/published-endpoints/draft", json=_草稿body())

    assert 原重播呼叫 == [True]
    assert len(服務.草稿呼叫) == 1
    assert (回應.status_code, 回應.json()) == (500, {"detail": "發布管理服務失敗"})
    assert marker not in 回應.text
    assert _敵意字串計數() == (0, 0, 0, 0)
    _證明敵意鍵oracle會觸發(預覽, 原鍵)


@pytest.mark.parametrize("原始數量,變更後數量", [(0, 1), (0, 256), (1, 0)], ids=["grow-one", "grow-limit", "shrink"])
def test_M02字典重播大小不符先於items遍歷與敵意callback(monkeypatch, 原始數量, 變更後數量):
    """captured/current 長度不等時，真實 replay 不呼叫該來源 items 或鍵 callback。"""
    marker = "MARKER_SIZE_MISMATCH"
    原鍵 = marker + "_SAFE"
    巢狀 = {} if 原始數量 == 0 else {原鍵: "old"}
    預覽 = {"nested": 巢狀}
    服務 = 假發布管理服務()
    服務.草稿結果 = 草稿建立結果("draft-1", 2000.0, 預覽)
    原重播 = 規劃發布模組._重播JSON容器
    items呼叫 = []

    def 變更後重播(擷取):
        if not any(紀錄[0] is 巢狀 for 紀錄 in 擷取):
            return 原重播(擷取)
        if 變更後數量 == 0:
            dict.clear(巢狀)
        else:
            敵意鍵 = _敵意字串(marker + "_HOSTILE")
            dict.__setitem__(巢狀, 敵意鍵, "new")
            for 索引 in range(變更後數量 - 1):
                dict.__setitem__(巢狀, f"added-{索引}", 索引)
            assert _敵意字串.雜湊呼叫 == 1
        _重設敵意字串計數()
        原分析 = sys.getprofile()

        def 分析(frame, event, arg):
            if event == "c_call" and getattr(arg, "__name__", None) == "items" and getattr(arg, "__self__", None) is 巢狀:
                items呼叫.append(arg)
            return 分析

        sys.setprofile(分析)
        try:
            return 原重播(擷取)
        finally:
            sys.setprofile(原分析)

    monkeypatch.setattr(規劃發布模組, "_重播JSON容器", 變更後重播)
    客戶端, _, _ = _建立整合客戶端(發布服務=服務)
    回應 = 客戶端.post("/api/published-endpoints/draft", json=_草稿body())

    assert len(服務.草稿呼叫) == 1
    assert (回應.status_code, 回應.json()) == (500, {"detail": "發布管理服務失敗"})
    assert marker not in 回應.text
    assert items呼叫 == []
    assert _敵意字串計數() == (0, 0, 0, 0)


def test_M02字典鍵未變更時建構重播成功():
    """相同 exact str keys 的正向控制仍發布完整 detached 預覽。"""
    預覽 = {"first": "safe", "nested": {"second": 2}}
    來源 = 草稿建立結果("draft-1", 2000.0, 預覽)
    安全 = 規劃發布模組._重建草稿結果(來源)
    assert 安全.預覽 == 預覽
    assert 安全.預覽 is not 預覽 and 安全.預覽["nested"] is not 預覽["nested"]


@pytest.mark.parametrize(
    "預覽,變更",
    [
        ({"first": {"value": "old"}, "last": "same"}, lambda 值: dict.__setitem__(值, "first", {"value": "new"})),
        ({"first": "old", "last": "same"}, lambda 值: dict.__setitem__(值, "first", "new")),
        ({"items": [1, 2]}, lambda 值: list.append(dict.__getitem__(值, "items"), 3)),
        ({"first": 1, "last": 2}, lambda 值: dict.__setitem__(值, "first", dict.pop(值, "first"))),
    ],
    ids=["visited-child", "scalar", "size", "order"],
)
def test_M02預覽snapshot重播拒絕並行替換大小與順序(monkeypatch, 預覽, 變更):
    """已複製子項後的 retained-source mutation 固定 500，絕不回混合預覽。"""
    服務 = 假發布管理服務()
    服務.草稿結果 = 草稿建立結果("draft-1", 2000.0, 預覽)
    執行緒, 已到重播, 執行緒錯誤 = _在預覽重播前變更(monkeypatch, 預覽, 變更)
    客戶端, _, _ = _建立整合客戶端(發布服務=服務)

    回應 = 客戶端.post("/api/published-endpoints/draft", json=_草稿body())
    執行緒.join(2)

    assert 已到重播.is_set() and not 執行緒.is_alive() and 執行緒錯誤 == []
    assert len(服務.草稿呼叫) == 1
    assert (回應.status_code, 回應.json()) == (500, {"detail": "發布管理服務失敗"})
    assert "old" not in 回應.text and "new" not in 回應.text


def test_M02穩定預覽回傳精確detached快照且來源後續變更無影響():
    """穩定來源不誤判，重建結果與 HTTP body 都不再別名服務容器。"""
    預覽 = {"summary": "safe", "items": [1, {"ok": True}]}
    來源 = 草稿建立結果("draft-1", 2000.0, 預覽)
    安全 = 規劃發布模組._重建草稿結果(來源)
    服務 = 假發布管理服務()
    服務.草稿結果 = 來源
    客戶端, _, _ = _建立整合客戶端(發布服務=服務)
    回應 = 客戶端.post("/api/published-endpoints/draft", json=_草稿body())

    list.append(dict.__getitem__(預覽, "items"), "late")
    dict.__setitem__(預覽, "summary", "changed")

    預期 = {"summary": "safe", "items": [1, {"ok": True}]}
    assert type(安全.預覽) is dict and type(安全.預覽["items"]) is list
    assert 安全.預覽 == 預期 and 回應.json()["preview"] == 預期
    assert 回應.status_code == 201 and len(服務.草稿呼叫) == 1


@pytest.mark.parametrize("錯誤", [RuntimeError("MUTATION_RUNTIME"), _敵意基底例外("MUTATION_CUSTOM")])
def test_M02預覽重播一般錯誤固定500且清除來源(monkeypatch, 錯誤):
    """descriptor/replay 的 RuntimeError 與非 KISG BaseException 都 fail closed。"""
    marker = 錯誤.args[0]
    來源 = 草稿建立結果("draft-1", 2000.0, {"marker": marker})

    def 失敗重播(擷取):
        raise 錯誤

    monkeypatch.setattr(規劃發布模組, "_重播JSON容器", 失敗重播)
    with pytest.raises(HTTPException) as 捕捉:
        規劃發布模組._重建草稿結果(來源)
    來源 = None
    assert (捕捉.value.status_code, 捕捉.value.detail) == (500, "發布管理服務失敗")
    _生產traceback不含marker(捕捉.value, marker)


@pytest.mark.parametrize("例外類型", [KeyboardInterrupt, SystemExit, GeneratorExit])
def test_M02預覽重播KISG保持原例外且清除來源(monkeypatch, 例外類型):
    """snapshot/replay 清理後仍精確傳遞 Python 控制流 identity 與 args。"""
    marker = "MUTATION_KISG_MARKER"
    原例外 = 例外類型(marker)
    來源 = 草稿建立結果("draft-1", 2000.0, {"marker": marker})

    def 中斷重播(擷取):
        raise 原例外

    monkeypatch.setattr(規劃發布模組, "_重播JSON容器", 中斷重播)
    with pytest.raises(例外類型) as 捕捉:
        規劃發布模組._重建草稿結果(來源)
    來源 = None
    assert 捕捉.value is 原例外 and 捕捉.value.args == (marker,)
    _生產traceback不含marker(捕捉.value, marker)


def test_M02版本回執先拒絕敵意端點字串且零比較callback():
    """回執端點 exact-type/bounds 驗證先於 authoritative equality。"""
    _敵意字串.相等呼叫 = _敵意字串.不等呼叫 = 0
    回執 = 版本建立結果(_敵意字串("endpoint-1"), "version-2", 2, "version-2", True)
    with pytest.raises(HTTPException) as 捕捉:
        _重建版本結果(回執, "endpoint-1")
    assert (捕捉.value.status_code, 捕捉.value.detail) == (500, "發布管理服務失敗")
    assert (_敵意字串.相等呼叫, _敵意字串.不等呼叫) == (0, 0)


@pytest.mark.parametrize("例外類型", [KeyboardInterrupt, SystemExit, GeneratorExit])
def test_M02版本實際重建填滿槽後KISG清理且無生成器框架(例外類型):
    """sys.settrace 在所有回執槽已讀後中斷，逐框證明清理與無 genexpr。"""
    marker = "MARKER_VERSION_REBUILD_KISG"
    回執 = 版本建立結果(marker, marker + "-v2", 2, marker + "-v2", True)
    原例外 = 例外類型(marker, "version-rebuild")
    with pytest.raises(例外類型) as 捕捉:
        _以行追蹤中斷(_重建版本結果, "已變更類型 = type(已變更)", 原例外, lambda: _重建版本結果(回執, marker))
    回執 = None
    assert 捕捉.value is 原例外 and 捕捉.value.args == (marker, "version-rebuild")
    assert 捕捉.value.__cause__ is None and 捕捉.value.__context__ is None
    框架 = _traceback框架(捕捉.value)
    assert "_重建版本結果" in 框架
    assert all(frame.f_code.co_name != "<genexpr>" for frames in 框架.values() for frame in frames if frame.f_globals.get("__name__") == 規劃發布模組.__name__)
    _生產traceback不含marker(捕捉.value, marker)


def _公開處理器(app, path):
    """取出指定 POST endpoint callable。"""
    return next(route.endpoint for route in app.routes if getattr(route, "path", None) == path and "POST" in route.methods)


def _直接處理器輸入(操作, marker):
    """建立 marker-bearing valid request、session 與直接呼叫參數。"""
    身份 = _身份(marker)
    if 操作 == "draft":
        請求 = 建立草稿請求(original_requirement_text=marker, planner_content={"marker": marker})
        return "/api/published-endpoints/draft", 請求, 身份, (請求, 身份)
    if 操作 == "publish":
        請求 = 發布端點請求(draft_id=marker, slug="safe-slug", configuration_confirmation={"marker": marker})
        return "/api/published-endpoints", 請求, 身份, (請求, 身份)
    請求 = 建立版本請求(configuration={"marker": marker})
    return "/api/published-endpoints/{endpoint_id}/versions", 請求, 身份, (請求, marker, 身份)


@pytest.mark.parametrize("操作", ["draft", "publish", "version"])
@pytest.mark.parametrize("例外類型", [KeyboardInterrupt, SystemExit, GeneratorExit])
def test_M02三公開處理器服務KISG傳遞且transitive清理(操作, 例外類型):
    """直接 handler 矩陣掃描 request、session、service receipt。"""
    marker = "MARKER_HANDLER_SERVICE_" + 操作
    原例外 = 例外類型(marker, 操作)

    class 中斷服務(假發布管理服務):
        def 建立草稿(self, **參數): raise 原例外
        def 原子發布(self, **參數): raise 原例外
        def 原子建立並切換版本(self, **參數): raise 原例外

    服務 = 中斷服務()
    服務.草稿結果 = 草稿建立結果(marker, 2000.0, {"marker": marker})
    服務.發布結果 = 端點發布結果(marker, marker + "-v1", 1, "active", marker + "_KEY_123456")
    服務.版本結果 = 版本建立結果(marker, marker + "-v2", 2, marker + "-v2", True)
    _, _, app = _建立整合客戶端(_身份(marker), 服務)
    path, 請求, 身份, 參數 = _直接處理器輸入(操作, marker)
    assert _含marker(請求, marker, set()) and _含marker(身份, marker, set())
    assert _含marker((服務.草稿結果, 服務.發布結果, 服務.版本結果), marker, set())
    處理器 = _公開處理器(app, path)
    with pytest.raises(例外類型) as 捕捉:
        處理器(*參數)
    服務 = 請求 = 身份 = 參數 = 處理器 = app = None
    assert 捕捉.value is 原例外 and 捕捉.value.args == (marker, 操作)
    assert 捕捉.value.__cause__ is None and 捕捉.value.__context__ is None
    _生產traceback不含marker(捕捉.value, marker)


@pytest.mark.parametrize("操作", ["draft", "publish", "version"])
def test_M02三公開處理器重建內層KISG仍清理(monkeypatch, 操作):
    """服務成功後由 reconstruction path 中斷，不只測 _呼叫服務。"""
    marker = "MARKER_HANDLER_REBUILD_" + 操作
    原例外 = KeyboardInterrupt(marker, 操作)
    服務 = 假發布管理服務()
    服務.草稿結果 = 草稿建立結果(marker, 2000.0, {"marker": marker})
    服務.發布結果 = 端點發布結果(marker, marker + "-v1", 1, "active", marker + "_KEY_123456")
    服務.版本結果 = 版本建立結果(marker, marker + "-v2", 2, marker + "-v2", True)
    重建器名稱 = {"draft": "_重建草稿結果", "publish": "_重建發布結果", "version": "_重建版本結果"}[操作]

    def 中斷重建(*參數): raise 原例外

    monkeypatch.setattr(規劃發布模組, 重建器名稱, 中斷重建)
    _, _, app = _建立整合客戶端(_身份(marker), 服務)
    path, 請求, 身份, 參數 = _直接處理器輸入(操作, marker)
    with pytest.raises(KeyboardInterrupt) as 捕捉:
        _公開處理器(app, path)(*參數)
    服務 = 請求 = 身份 = 參數 = app = None
    assert 捕捉.value is 原例外 and 捕捉.value.__context__ is None
    _生產traceback不含marker(捕捉.value, marker)


@pytest.mark.parametrize("例外類型", [KeyboardInterrupt, SystemExit, GeneratorExit])
def test_M02發布讀取金鑰後JSONResponse_KISG不留明文(monkeypatch, 例外類型):
    """constructor 在 key 已放入 payload 後中斷，handler 仍清除 aliases。"""
    marker = "MARKER_JSONRESPONSE_KEY_123456"
    原例外 = 例外類型(marker, "json-response")
    服務 = 假發布管理服務()
    服務.發布結果 = 端點發布結果("endpoint-1", "version-1", 1, "active", marker)

    def 中斷回應(*參數, **關鍵字):
        assert _含marker(關鍵字["content"], marker, set())
        raise 原例外

    monkeypatch.setattr(規劃發布模組, "JSONResponse", 中斷回應)
    _, _, app = _建立整合客戶端(_身份(marker), 服務)
    path, 請求, 身份, 參數 = _直接處理器輸入("publish", marker)
    assert _含marker(服務.發布結果, marker, set()) and _含marker(marker, marker, set())
    with pytest.raises(例外類型) as 捕捉:
        _公開處理器(app, path)(*參數)
    服務 = 請求 = 身份 = 參數 = app = None
    assert 捕捉.value is 原例外 and 捕捉.value.args == (marker, "json-response")
    assert 捕捉.value.__cause__ is None and 捕捉.value.__context__ is None
    _生產traceback不含marker(捕捉.value, marker)


_巢狀清理案例 = [
    ("key", "direct"), ("key", "publish"),
    ("identifier", "direct"), ("identifier", "draft"),
    ("identifier", "publish"), ("identifier", "version"),
    ("identity", "direct"), ("identity", "draft"),
    ("identity", "publish"), ("identity", "version"),
]


@pytest.mark.parametrize("例外類型", [KeyboardInterrupt, SystemExit, GeneratorExit])
@pytest.mark.parametrize("目標種類,操作", _巢狀清理案例)
def test_M02真實巢狀金鑰識別身份KISG逐框清理(例外類型, 目標種類, 操作):
    """精確 code/line 注入 direct helper 與三公開 handler，逐框掃描 KISG。"""
    marker = "MARKER_NESTED_" + 目標種類.upper() + "_" + 操作.upper()
    原例外 = 例外類型(marker, 目標種類, 操作)
    目標 = {
        "key": 規劃發布模組._是金鑰,
        "identifier": 規劃發布模組._是識別,
        "identity": 規劃發布模組._重建身份,
    }[目標種類]
    唯一片段 = {
        "key": "位元長度 = bytes.__len__(編碼值)",
        "identifier": "結果 = 符合 is not None",
        "identity": "結果 = (使用者, 管理者)",
    }[目標種類]
    敏感區域 = {
        "key": {"值", "值類型", "字元長度", "去空白值", "去空白相同", "編碼值", "位元長度", "結果"},
        "identifier": {"值", "值類型", "符合", "結果"},
        "identity": {"身份", "身份類型", "使用者", "管理者", "結果"},
    }[目標種類]
    服務 = app = 請求 = 身份 = 參數 = 處理器 = None
    if 操作 == "direct":
        if 目標種類 == "key":
            呼叫 = lambda: 目標(marker + "_KEY_123456")
        elif 目標種類 == "identifier":
            呼叫 = lambda: 目標(marker)
        else:
            身份 = _身份(marker)
            assert _含marker(身份, marker, set())
            呼叫 = lambda: 目標(身份)
    else:
        服務 = 假發布管理服務()
        服務.草稿結果 = 草稿建立結果(marker, 2000.0, {"marker": marker})
        服務.發布結果 = 端點發布結果(marker, marker + "-v1", 1, "active", marker + "_KEY_123456")
        服務.版本結果 = 版本建立結果(marker, marker + "-v2", 2, marker + "-v2", True)
        _, _, app = _建立整合客戶端(_身份(marker), 服務)
        path, 請求, 身份, 參數 = _直接處理器輸入(操作, marker)
        assert _含marker(身份, marker, set())
        if 目標種類 == "key":
            assert _含marker(服務.發布結果, marker, set())
        處理器 = _公開處理器(app, path)
        呼叫 = lambda: 處理器(*參數)

    with pytest.raises(例外類型) as 捕捉:
        _以行追蹤中斷(目標, 唯一片段, 原例外, 呼叫)
    服務 = app = 請求 = 身份 = 參數 = 處理器 = 呼叫 = 目標 = None

    assert 捕捉.value is 原例外
    assert 捕捉.value.args == (marker, 目標種類, 操作)
    assert 捕捉.value.__cause__ is None and 捕捉.value.__context__ is None
    框架 = _traceback框架(捕捉.value)
    目標名稱 = {"key": "_是金鑰", "identifier": "_是識別", "identity": "_重建身份"}[目標種類]
    assert 目標名稱 in 框架
    目標區域 = 框架[目標名稱][-1].f_locals
    assert 敏感區域 <= set(目標區域)
    assert all(目標區域[名稱] is None for 名稱 in 敏感區域)
    if 操作 != "direct":
        處理器名稱 = {"draft": "_建立發布草稿", "publish": "_發布端點", "version": "_建立不可變版本"}[操作]
        assert 處理器名稱 in 框架
    _生產traceback不含marker(捕捉.value, marker)


@pytest.mark.parametrize("例外類型", [KeyboardInterrupt, SystemExit, GeneratorExit])
@pytest.mark.parametrize("公開路徑", [False, True], ids=["direct", "handler"])
def test_M02版本成功結果返回行KISG仍清除回執(例外類型, 公開路徑):
    """成功 DTO 已建立後的 return-line 中斷也由同一 finally 清除。"""
    marker = "MARKER_VERSION_RETURN_KISG"
    原例外 = 例外類型(marker, "version-return")
    回執 = 版本建立結果(marker, marker + "-v2", 2, marker + "-v2", True)
    服務 = app = 請求 = 身份 = 參數 = 處理器 = None
    if 公開路徑:
        服務 = 假發布管理服務()
        服務.版本結果 = 回執
        _, _, app = _建立整合客戶端(_身份(marker), 服務)
        path, 請求, 身份, 參數 = _直接處理器輸入("version", marker)
        處理器 = _公開處理器(app, path)
        呼叫 = lambda: 處理器(*參數)
    else:
        呼叫 = lambda: _重建版本結果(回執, marker)

    with pytest.raises(例外類型) as 捕捉:
        _以行追蹤中斷(_重建版本結果, "return 安全結果", 原例外, 呼叫)
    回執 = 服務 = app = 請求 = 身份 = 參數 = 處理器 = 呼叫 = None
    assert 捕捉.value is 原例外 and 捕捉.value.args == (marker, "version-return")
    assert 捕捉.value.__cause__ is None and 捕捉.value.__context__ is None
    框架 = _traceback框架(捕捉.value)
    區域 = 框架["_重建版本結果"][-1].f_locals
    敏感 = {"來源", "端點識別碼", "回執端點", "版本", "目前版本", "安全結果"}
    assert all(區域[名稱] is None for 名稱 in 敏感)
    if 公開路徑:
        assert {"_呼叫服務", "_建立不可變版本"} <= set(框架)
    _生產traceback不含marker(捕捉.value, marker)


@pytest.mark.parametrize("例外類型", [KeyboardInterrupt, SystemExit, GeneratorExit])
@pytest.mark.parametrize("公開路徑", [False, True], ids=["direct", "preview-handler"])
@pytest.mark.parametrize(
    "唯一片段",
    ["失敗 = type(值) is not dict", "return 結果"],
    ids=["before-snapshot", "success-return"],
)
def test_M02外層快照真實行KISG單一finally逐框清理(例外類型, 公開路徑, 唯一片段):
    """outer helper 的進入與成功返回行皆清除輸入、快照、擷取及結果別名。"""
    marker = "MARKER_OUTER_SNAPSHOT_CLEANUP"
    原例外 = 例外類型(marker, 公開路徑, 唯一片段)
    來源 = {marker + "_KEY": {"value": marker}}
    服務 = app = 請求 = 身份 = 參數 = 處理器 = 回執 = None
    if 公開路徑:
        服務 = 假發布管理服務()
        回執 = 草稿建立結果(marker, 2000.0, 來源)
        服務.草稿結果 = 回執
        _, _, app = _建立整合客戶端(_身份(marker), 服務)
        path, 請求, 身份, 參數 = _直接處理器輸入("draft", marker)
        處理器 = _公開處理器(app, path)
        呼叫 = lambda: 處理器(*參數)
    else:
        呼叫 = lambda: 規劃發布模組._複製JSON物件(來源)

    目標 = 規劃發布模組._複製JSON物件
    目標行 = _程式行號(目標, 唯一片段)
    原追蹤 = sys.gettrace()
    已觸發 = []

    def 追蹤(frame, event, arg):
        if (not 已觸發 and event == "line" and frame.f_code is 目標.__code__
                and frame.f_lineno == 目標行 and frame.f_locals.get("值") is 來源):
            已觸發.append((frame.f_code, frame.f_lineno))
            raise 原例外
        return 追蹤

    sys.settrace(追蹤)
    try:
        with pytest.raises(例外類型) as 捕捉:
            呼叫()
        assert 已觸發 == [(目標.__code__, 目標行)]
    finally:
        sys.settrace(原追蹤)
    來源 = 服務 = app = 請求 = 身份 = 參數 = 處理器 = 回執 = 呼叫 = 目標 = None

    assert 捕捉.value is 原例外
    assert 捕捉.value.args == (marker, 公開路徑, 唯一片段)
    assert 捕捉.value.__cause__ is None and 捕捉.value.__context__ is None
    框架 = _traceback框架(捕捉.value)
    assert "_複製JSON物件" in 框架
    區域 = 框架["_複製JSON物件"][-1].f_locals
    敏感 = {"值", "物件", "安全快照", "擷取", "計數", "結果", "錯誤"}
    assert 敏感 <= set(區域)
    assert all(區域[名稱] is None for 名稱 in 敏感)
    if 公開路徑:
        assert {"_重建草稿結果", "_呼叫服務", "_建立發布草稿"} <= set(框架)
    _生產traceback不含marker(捕捉.value, marker)


def test_A18管理員呼叫route與query_allowlist唯一且禁止export():
    """A18-01只凍結兩條GET path與六個allowlisted query keys。"""
    assert ADMIN_INVOCATION_LIST_PATH == "/api/admin/endpoints/{endpoint_id}/invocations"
    assert ADMIN_INVOCATION_METHOD == "GET"
    assert ADMIN_INVOCATION_DETAIL_PATH == (
        "/api/admin/endpoints/{endpoint_id}/invocations/{invocation_id}"
    )
    assert ADMIN_INVOCATION_QUERY_KEYS == frozenset(
        {"from_at", "to_at", "status", "error_code", "limit", "cursor"}
    )
    assert ADMIN_INVOCATION_FORBIDDEN_QUERY_KEYS == frozenset(
        {"owner_id", "raw_search", "export", "sort"}
    )
    assert ADMIN_INVOCATION_REJECT_DUPLICATE_QUERY_KEYS is True
    assert ADMIN_INVOCATION_AUDIT_ACTION == "audit.detail.view"
    assert all(禁止 not in (ADMIN_INVOCATION_LIST_PATH + ADMIN_INVOCATION_DETAIL_PATH).lower()
               for 禁止 in ("export", "download", "search"))
    assert ADMIN_INVOCATION_ERROR_CONTRACT == {
        401: "需要登入",
        403: "只有管理者可查看完整呼叫紀錄",
        404: "找不到呼叫紀錄",
        422: None,
        503: "呼叫紀錄暫時不可取得",
        500: "呼叫紀錄不可取得",
    }


def test_A18安全列表DTO固定欄位且拒絕raw與可變容器():
    """List DTO只含營運metadata，不含任何raw payload或secret-bearing欄位。"""
    項目 = 管理員呼叫列表項目(
        "inv-1", "ep-1", "ver-1", "req-1", "failed", "schema_invalid",
        12.5, 10.0, 11.0, True,
    )
    結果 = 管理員呼叫列表結果((項目,), "signed-cursor")
    assert set(項目.__slots__) == {
        "呼叫識別碼", "端點識別碼", "端點版本識別碼", "請求識別碼", "狀態",
        "錯誤碼", "延遲毫秒", "建立時間", "完成時間", "是否有遮蔽",
    }
    assert set(結果.__slots__) == {"項目", "下一頁游標"}
    assert not hasattr(項目, "__dict__") and not hasattr(結果, "__dict__")
    for 禁止 in ("input", "metadata", "output", "error", "usage", "arguments", "result",
               "credential_id", "api_key", "authorization", "cookie", "path"):
        assert 禁止 not in repr(項目).lower()
    with pytest.raises((FrozenInstanceError, AttributeError)):
        項目.狀態 = "succeeded"
    with pytest.raises(ValueError):
        管理員呼叫列表結果([項目], None)


def test_A18游標簽章綁定endpoint_filters_limit_position且拒絕tamper():
    """Cursor不能跨endpoint/filter/window重放，也不能由client竄改position。"""
    codec = 管理員呼叫游標編解碼器(b"k" * 32)
    條件 = 管理員呼叫查詢條件("ep-1", 1.0, 20.0, "failed", "schema_invalid", 25)
    位置 = 管理員呼叫游標位置(10.0, "inv-1")
    cursor = codec.編碼(條件, 位置)

    assert codec.解碼(cursor, 條件) == 位置
    for 其他條件 in (
        管理員呼叫查詢條件("ep-2", 1.0, 20.0, "failed", "schema_invalid", 25),
        管理員呼叫查詢條件("ep-1", 2.0, 20.0, "failed", "schema_invalid", 25),
        管理員呼叫查詢條件("ep-1", 1.0, 20.0, "succeeded", "schema_invalid", 25),
        管理員呼叫查詢條件("ep-1", 1.0, 20.0, "failed", "schema_invalid", 24),
    ):
        with pytest.raises(管理員呼叫游標錯誤):
            codec.解碼(cursor, 其他條件)
    竄改 = cursor[:-1] + ("A" if cursor[-1] != "A" else "B")
    with pytest.raises(管理員呼叫游標錯誤):
        codec.解碼(竄改, 條件)
    object.__setattr__(條件, "端點識別碼", "ep-tampered")
    with pytest.raises(管理員呼叫游標錯誤):
        codec.解碼(cursor, 條件)


def test_A18游標拒絕有效HMAC但JSON非canonical的token():
    """Signature只證明bytes真實；decode仍須拒絕空白／key順序等非canonical表示。"""
    金鑰 = b"k" * 32
    codec = 管理員呼叫游標編解碼器(金鑰)
    條件 = 管理員呼叫查詢條件("ep-1", 1.0, 20.0, "failed", None, 25)
    編碼 = lambda 值: base64.urlsafe_b64encode(值).rstrip(b"=").decode("ascii")
    payload = {"position": [10.0, "inv-1"],
               "scope": ["ep-1", 1.0, 20.0, "failed", None, 25], "v": 1}
    for 非canonical in (
        json.dumps(payload, ensure_ascii=True).encode("ascii"),
        b'{"v":1,"scope":["ep-1",1.0,20.0,"failed",null,25],'
        b'"position":[10.0,"inv-1"]}',
        b'{"position":[10.0,"inv-1"],"scope":["ep-1",1e0,20.0,"failed",null,25],"v":1}',
    ):
        簽章 = hmac.new(金鑰, 非canonical, hashlib.sha256).digest()
        token = 編碼(非canonical) + "." + 編碼(簽章)
        with pytest.raises(管理員呼叫游標錯誤):
            codec.解碼(token, 條件)


def test_A18完整詳情由module_owned_DTO深複製且repr零raw():
    """A18-02只能透過exact rebuild seam序列化已稽核raw provider結果。"""
    原始 = {
        "invocation": {"id": "inv-1", "request_id": "req-1", "session_id": None},
        "endpoint_id": "ep-1", "endpoint_version_id": "ver-1", "credential_id": None,
        "message_id": None, "status": "failed", "input": {"raw": "RAW_MARKER"},
        "metadata": {}, "output": None, "error": {"code": "timeout"}, "usage": None,
        "metadata_size_bytes": 1, "metadata_sha256": "a" * 64, "latency_ms": 1.0,
        "pricing_version": None, "created_at": 1.0, "completed_at": 2.0,
        "run_events": [], "tool_calls": [],
    }
    詳情 = 建立管理員呼叫完整詳情(原始)
    assert "RAW_MARKER" not in repr(詳情)
    第一份 = 詳情.建立JSON()
    原始["input"]["raw"] = "MUTATED"  # type: ignore[index]
    第一份["input"]["raw"] = "REUSED"  # type: ignore[index]
    assert 詳情.建立JSON()["input"] == {"raw": "RAW_MARKER"}
    for 破壞 in ({**原始, "extra": 1}, {鍵: 值 for 鍵, 值 in 原始.items() if 鍵 != "input"}):
        with pytest.raises(Exception) as 錯誤:
            建立管理員呼叫完整詳情(破壞)
        assert "RAW_MARKER" not in repr(錯誤.value)


def test_A18完整詳情逐欄bounded且內部儲存不可變():
    """拒絕巢狀schema漂移、超深JSON與直接slot竄改。"""
    基本 = {
        "invocation": {"id": "inv-1", "request_id": "req-1", "session_id": None},
        "endpoint_id": "ep-1", "endpoint_version_id": "ver-1", "credential_id": None,
        "message_id": None, "status": "failed", "input": {}, "metadata": {},
        "output": None, "error": None, "usage": None, "metadata_size_bytes": 0,
        "metadata_sha256": None, "latency_ms": None, "pricing_version": None,
        "created_at": 1.0, "completed_at": 2.0, "run_events": [], "tool_calls": [],
    }
    for 破壞 in (
        {**基本, "invocation": "not-an-object"},
        {**基本, "run_events": "not-a-list"},
        {**基本, "tool_calls": 999},
        {**基本, "run_events": [{"id": "run-1"}]},
    ):
        with pytest.raises(Exception) as 錯誤:
            建立管理員呼叫完整詳情(破壞)
        assert type(錯誤.value) is not RecursionError

    深值: object = None
    for _ in range(5000):
        深值 = [深值]
    with pytest.raises(Exception) as 錯誤:
        建立管理員呼叫完整詳情({**基本, "input": 深值})
    assert type(錯誤.value) is not RecursionError
    for 過量 in ([None] * 4097, "x" * 1_048_577):
        with pytest.raises(Exception):
            建立管理員呼叫完整詳情({**基本, "input": 過量})

    詳情 = 建立管理員呼叫完整詳情(基本)
    with pytest.raises((AttributeError, TypeError)):
        詳情._內容 = b"mutated"
    object.__setattr__(詳情, "_內容", b"not-json")
    with pytest.raises(Exception):
        詳情.建立JSON()


def test_A18完整詳情保留合法raw_JSON但禁止治理secret與filesystem_path():
    """Full Logs允許scalar raw；敏感key、平台API key與filesystem path必須fail closed。"""
    基本 = {
        "invocation": {"id": "inv-1", "request_id": "req-1", "session_id": None},
        "endpoint_id": "ep-1", "endpoint_version_id": "ver-1", "credential_id": None,
        "message_id": None, "status": "failed", "input": 7, "metadata": {},
        "output": 3.14, "error": False, "usage": [1], "metadata_size_bytes": 2,
        "metadata_sha256": "a" * 64, "latency_ms": 1.0, "pricing_version": None,
        "created_at": 1.0, "completed_at": 2.0, "run_events": [], "tool_calls": [],
    }
    assert 建立管理員呼叫完整詳情(基本).建立JSON()["input"] == 7
    for 敏感 in (
        {"metadata": {"Authorization": "Bearer secret"}},
        {"input": {"nested": {"api-key": "secret"}}},
        {"output": {"filesystem_path": "/private/data"}},
        {"error": {"path": "/Users/private/secret.txt"}},
        {"usage": {"provider_secret": "secret"}},
        {"input": "pk_" + "A" * 43},
    ):
        with pytest.raises(Exception):
            建立管理員呼叫完整詳情({**基本, **敏感})
    with pytest.raises(Exception):
        建立管理員呼叫完整詳情({**基本, "metadata": "not-an-object"})
