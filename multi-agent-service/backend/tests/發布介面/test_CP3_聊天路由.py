"""CP3 WEB-CHAT：exact `/api/chat` 路由契約測試。"""

from concurrent.futures import ThreadPoolExecutor
from threading import Event, Lock
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from 繁中代理.發布介面.Web代理服務 import Web服務不可用, 聊天回應
from 繁中代理.發布介面.網頁工作階段 import 網頁使用者
from 繁中代理.發布介面.路由.聊天 import 建立聊天路由器


class 假聊天服務:
    """記錄 route 傳入的權威 user 與 trim 後訊息。"""

    def __init__(self, 錯誤=None):
        self.錯誤 = 錯誤
        self.呼叫 = []

    def 聊天(self, 使用者識別碼, 訊息, 工作階段識別碼=None):
        self.呼叫.append((使用者識別碼, 訊息, 工作階段識別碼))
        if self.錯誤:
            raise self.錯誤
        return 聊天回應(工作階段識別碼 or "new-root", "回覆")


def _客戶端(服務, 呼叫):
    """以 caller-provided current-session 與 CSRF dependencies 建立 app。"""
    def 目前使用者():
        呼叫.append("session")
        return 網頁使用者("user-1", "alice", "member")

    def csrf使用者():
        呼叫.append("csrf")
        return 網頁使用者("user-1", "alice", "member")

    應用 = FastAPI()
    路由器 = 建立聊天路由器(服務, 目前使用者, csrf使用者)
    assert 路由器.prefix == "/api/chat"
    assert [路由.path for 路由 in 路由器.routes] == ["/api/chat"]
    應用.include_router(路由器)
    return TestClient(應用)


def test_聊天路由使用兩個canonical_dependencies並回最小DTO():
    """CP3-WEB-ROUTE-CHAT-01：identity 來自 session，mutation 必須經 CSRF。"""
    服務, 相依呼叫 = 假聊天服務(), []
    with _客戶端(服務, 相依呼叫) as 客戶端:
        回應 = 客戶端.post("/api/chat", json={"message": "  你好  ", "session_id": "root-1"})

    assert 回應.status_code == 200
    assert 相依呼叫 == ["session", "csrf"]
    assert 服務.呼叫 == [("user-1", "你好", "root-1")]
    assert 回應.json() == {"session_id": "root-1", "reply": {"role": "assistant", "content": "回覆"}}


def test_聊天路由嚴格body與固定不可用錯誤不洩漏例外():
    """CP3-WEB-ROUTE-CHAT-02：extra/空訊息 422，provider failure 固定 503。"""
    with _客戶端(假聊天服務(), []) as 客戶端:
        標記 = "BODY_TOP_SECRET"
        無效回應 = 客戶端.post("/api/chat", json={"message": "x", "extra": 標記})
        assert 無效回應.status_code == 422
        assert 無效回應.json() == {"detail": {"code": "invalid_request"}}
        assert 標記 not in 無效回應.text
        assert 客戶端.post("/api/chat", json={"message": "   "}).json() == {
            "detail": {"code": "invalid_request"}
        }

    服務 = 假聊天服務(Web服務不可用("provider secret"))
    with _客戶端(服務, []) as 客戶端:
        回應 = 客戶端.post("/api/chat", json={"message": "hello"})
    assert 回應.status_code == 503
    assert 回應.json() == {"detail": {"code": "chat_unavailable"}}
    assert "secret" not in 回應.text


def test_聊天路由拒絕重複JSON鍵():
    """Quality P2：strict JSON 不得接受 duplicate message/session_id keys。"""
    with _客戶端(假聊天服務(), []) as 客戶端:
        回應 = 客戶端.post(
            "/api/chat", content=b'{"message":"first","message":"second"}',
            headers={"content-type": "application/json"},
        )
    assert 回應.status_code == 422
    assert 回應.json() == {"detail": {"code": "invalid_request"}}


def test_同步聊天服務不阻塞ASGI事件迴圈():
    """Quality P1：兩個 slow sync provider 必須能同時進入 threadpool。"""
    class 慢服務(假聊天服務):
        def __init__(self):
            super().__init__()
            self.鎖 = Lock()
            self.進入數 = 0
            self.兩者已進入 = Event()

        def 聊天(self, 使用者識別碼, 訊息, 工作階段識別碼=None):
            with self.鎖:
                self.進入數 += 1
                if self.進入數 == 2:
                    self.兩者已進入.set()
            assert self.兩者已進入.wait(timeout=1)
            return 聊天回應("root", 訊息)

    服務 = 慢服務()
    with _客戶端(服務, []) as 客戶端, ThreadPoolExecutor(max_workers=2) as 執行器:
        結果 = list(執行器.map(lambda 訊息: 客戶端.post("/api/chat", json={"message": 訊息}), ("一", "二")))
    assert [回應.status_code for 回應 in 結果] == [200, 200]
