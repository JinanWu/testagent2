"""Quality R2：Chat 與 session detail 的 65,536-byte 成功文字契約。"""

from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.exceptions import ResponseValidationError
from fastapi.testclient import TestClient

from 繁中代理.使用者 import 使用者上下文
from 繁中代理.發布介面.Web代理服務 import Web代理服務
from 繁中代理.發布介面.網頁工作階段 import 網頁使用者
from 繁中代理.發布介面.路由.工作階段 import 建立工作階段路由器
from 繁中代理.發布介面.路由.聊天 import 建立聊天路由器


class _工作階段庫:
    def __init__(self, 內容: str):
        self.內容 = 內容

    def 檢查工作階段存取(self, 工作階段識別碼, **_條件):
        return {"id": 工作階段識別碼, "user_id": "user-1", "source": "web"}

    def 取得工作階段譜系(self, _工作階段識別碼):
        return ["root"]

    def 解析Resume工作階段(self, _工作階段識別碼, **_條件):
        return "root"

    def 讀取工作階段(self, 工作階段識別碼):
        return {
            "id": 工作階段識別碼, "user_id": "user-1", "source": "web",
            "title": "標題", "updated_at": 1.0,
        }

    def 讀取訊息(self, _工作階段識別碼, **_條件):
        return [
            {"role": "user", "content": self.內容},
            {"role": "assistant", "content": self.內容},
        ]


class _使用者庫:
    def 建立使用者上下文(self, user_id=None):
        return 使用者上下文(user_id=user_id, username="alice", roles=["user"])


def _客戶端(內容: str) -> TestClient:
    庫 = _工作階段庫(內容)

    def 工廠(**_條件):
        return SimpleNamespace(執行使用者訊息=lambda *_參數: SimpleNamespace(
            最終回答=內容, 工作階段識別碼="root",
        ))

    服務 = Web代理服務(庫, _使用者庫(), 工廠)
    使用者 = lambda: 網頁使用者("user-1", "alice", "member")
    應用 = FastAPI()
    應用.include_router(建立聊天路由器(服務, 使用者, 使用者))
    應用.include_router(建立工作階段路由器(服務, 使用者))
    return TestClient(應用)


_成功文字 = [
    pytest.param("x" * 16_384, id="ascii-16384"),
    pytest.param("x" * 16_385, id="ascii-16385"),
    pytest.param("x" * 65_536, id="ascii-65536"),
    pytest.param("界" * 21_845 + "x", id="multibyte-65536-bytes"),
]
_超界文字 = [
    pytest.param("x" * 65_537, id="ascii-65537"),
    pytest.param("界" * 21_845 + "xx", id="multibyte-65537-bytes"),
]


@pytest.mark.parametrize("端點", ["chat", "session"])
@pytest.mark.parametrize("內容", _成功文字)
def test_成功文字byte邊界通過真實服務與HTTP回應驗證(端點, 內容):
    assert len(內容.encode("utf-8")) <= 65_536
    with _客戶端(內容) as 客戶端:
        try:
            回應 = (
                客戶端.post("/api/chat", json={"message": "hi"})
                if 端點 == "chat" else 客戶端.get("/api/sessions/root")
            )
        except ResponseValidationError as 錯誤:
            pytest.fail(f"不得依賴 ResponseValidationError：{錯誤}")
    assert 回應.status_code == 200
    if 端點 == "chat":
        assert 回應.json()["reply"]["content"] == 內容
    else:
        assert [項目["content"] for 項目 in 回應.json()["messages"]] == [內容, 內容]


@pytest.mark.parametrize("端點", ["chat", "session"])
@pytest.mark.parametrize("內容", _超界文字)
def test_超界成功文字由服務固定映射503且不是response_validation(端點, 內容):
    assert len(內容.encode("utf-8")) == 65_537
    with _客戶端(內容) as 客戶端:
        try:
            回應 = (
                客戶端.post("/api/chat", json={"message": "hi"})
                if 端點 == "chat" else 客戶端.get("/api/sessions/root")
            )
        except ResponseValidationError as 錯誤:
            pytest.fail(f"不得發生 ResponseValidationError：{錯誤}")
    assert 回應.status_code == 503
    錯誤碼 = "chat_unavailable" if 端點 == "chat" else "sessions_unavailable"
    assert 回應.json() == {"detail": {"code": 錯誤碼}}