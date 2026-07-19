"""CP3 WEB-SESSION：exact sessions 路由契約測試。"""

from fastapi import FastAPI
from fastapi.testclient import TestClient

from 繁中代理.發布介面.Web代理服務 import (
    Web服務不可用,
    Web資源不存在,
    工作階段列表項目,
    工作階段詳情,
)
from 繁中代理.發布介面.網頁工作階段 import 網頁使用者
from 繁中代理.發布介面.路由.工作階段 import 建立工作階段路由器


class 假工作階段服務:
    """記錄權威 user、limit 與 logical root 查詢。"""

    def __init__(self, 錯誤=None):
        self.錯誤, self.呼叫 = 錯誤, []

    def 列出工作階段(self, 使用者識別碼, 數量上限=20):
        self.呼叫.append(("list", 使用者識別碼, 數量上限))
        if self.錯誤:
            raise self.錯誤
        return (工作階段列表項目("root-1", "標題", 10.0, 2),)

    def 讀取工作階段(self, 使用者識別碼, 根工作階段識別碼):
        self.呼叫.append(("detail", 使用者識別碼, 根工作階段識別碼))
        if self.錯誤:
            raise self.錯誤
        return 工作階段詳情("root-1", "標題", 10.0, (("user", "問"), ("assistant", "答")))


def _客戶端(服務):
    """用 caller-injected current-session dependency 建立測試 app。"""
    def 目前使用者():
        return 網頁使用者("user-1", "alice", "member")

    應用 = FastAPI()
    路由器 = 建立工作階段路由器(服務, 目前使用者)
    assert 路由器.prefix == "/api/sessions"
    assert [路由.path for 路由 in 路由器.routes] == ["/api/sessions", "/api/sessions/{session_id}"]
    應用.include_router(路由器)
    return TestClient(應用)


def test_工作階段路由只使用current_session並回固定allowlist():
    """CP3-WEB-ROUTE-SESSION-01：list/detail 只信任 current-session user。"""
    服務 = 假工作階段服務()
    with _客戶端(服務) as 客戶端:
        列表 = 客戶端.get("/api/sessions?limit=7")
        詳情 = 客戶端.get("/api/sessions/root-1")

    assert 服務.呼叫 == [("list", "user-1", 7), ("detail", "user-1", "root-1")]
    assert 列表.json() == {"sessions": [{"id": "root-1", "title": "標題", "updated_at": 10.0, "message_count": 2}]}
    assert 詳情.json() == {"session": {"id": "root-1", "title": "標題", "updated_at": 10.0}, "messages": [{"role": "user", "content": "問"}, {"role": "assistant", "content": "答"}]}


def test_工作階段路由限制參數並固定映射404與503():
    """CP3-WEB-ROUTE-SESSION-02：invalid limit 422；scope failure 不洩漏文字。"""
    with _客戶端(假工作階段服務()) as 客戶端:
        for 路徑, 標記 in (
            ("/api/sessions?limit=QUERY_TOP_SECRET", "QUERY_TOP_SECRET"),
            ("/api/sessions/" + "PATH_TOP_SECRET" * 10, "PATH_TOP_SECRET"),
        ):
            無效回應 = 客戶端.get(路徑)
            assert 無效回應.status_code == 422
            assert 無效回應.json() == {"detail": {"code": "invalid_request"}}
            assert 標記 not in 無效回應.text
        assert 客戶端.get("/api/sessions?limit=0").status_code == 422
        assert 客戶端.get("/api/sessions?limit=51").status_code == 422
        超長值 = "9" * 5000
        超長回應 = 客戶端.get(f"/api/sessions?limit={超長值}")
        assert 超長回應.status_code == 422
        assert 超長回應.json() == {"detail": {"code": "invalid_request"}}

    with _客戶端(假工作階段服務(Web資源不存在("owner secret"))) as 客戶端:
        回應 = 客戶端.get("/api/sessions/root-1")
    assert 回應.status_code == 404
    assert 回應.json() == {"detail": {"code": "session_not_found"}}
    assert "secret" not in 回應.text

    with _客戶端(假工作階段服務(Web服務不可用("db secret"))) as 客戶端:
        回應 = 客戶端.get("/api/sessions")
    assert 回應.status_code == 503
    assert 回應.json() == {"detail": {"code": "sessions_unavailable"}}


def test_工作階段繁中路徑參數維持session_id別名():
    """Quality P2：Python 參數使用繁中，OpenAPI path alias 仍是 frozen session_id。"""
    with _客戶端(假工作階段服務()) as 客戶端:
        參數 = 客戶端.get("/openapi.json").json()["paths"]["/api/sessions/{session_id}"]["get"]["parameters"]
    assert [(項目["name"], 項目["in"]) for 項目 in 參數] == [("session_id", "path")]
