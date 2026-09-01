"""CP3-COMP：認證別名與瀏覽器組裝邊界測試。"""

from fastapi import APIRouter, Depends
from fastapi.testclient import TestClient
import pytest

from 繁中代理.使用者 import 使用者庫
from 繁中代理.發布介面.相依項 import 發布介面相依項
from 繁中代理.發布介面.應用程式 import 建立網頁應用程式
from 繁中代理.發布介面.設定 import 限制登入請求Middleware, 網頁安全設定
from 繁中代理.發布介面.網頁工作階段 import 網頁工作階段服務
from 繁中代理.發布介面.路由.網頁認證 import (
    建立CSRF相依項,
    建立SQLite帳密驗證器,
    建立網頁認證路由器,
)


def _建立認證元件(tmp_path):
    """建立具有使用者與Web session schema的測試認證元件。"""
    路徑 = tmp_path / "cp3-auth.sqlite3"
    使用者們 = 使用者庫(路徑)
    使用者們.建立使用者("alice", "correct horse")
    遷移 = (__import__("pathlib").Path(__file__).parents[2] / "繁中代理/發布介面/遷移/0005_建立網頁工作階段.sql").read_text()
    使用者們.連線.executescript(遷移)
    使用者們.連線.close()
    設定 = 網頁安全設定(("http://localhost:5173",), Cookie安全=False, 工作階段有效秒數=60)
    服務 = 網頁工作階段服務(路徑, 有效秒數=60)
    return 路徑, 設定, 服務


def test_允許清單只接受CP3_exact_prefix(tmp_path):
    """CP3 sessions/skills可組裝，近似或子前綴仍fail closed。"""
    _, 設定, 服務 = _建立認證元件(tmp_path)
    認證 = 建立網頁認證路由器(服務, lambda *_: None, 設定=設定)
    for 前綴 in ("/api/sessions", "/api/skills"):
        路由器 = APIRouter(prefix=前綴)
        路由器.add_api_route("", lambda: {}, methods=["GET"])
        建立網頁應用程式(發布介面相依項((認證, 路由器), ()), 設定)
    for 前綴 in ("/api/session", "/api/skills-extra", "/api/sessions/private"):
        路由器 = APIRouter(prefix=前綴)
        路由器.add_api_route("", lambda: {}, methods=["GET"])
        try:
            建立網頁應用程式(發布介面相依項((認證, 路由器), ()), 設定)
        except ValueError as 錯誤:
            assert str(錯誤) == "發布介面路由設定無效"
        else:
            raise AssertionError("近似前綴不可進入composition")


def test_me與session共用current_session及rotation(tmp_path):
    """兩路由共用同一dependency identity，且missing CSRF皆使用相同rotation。"""
    路徑, 設定, 服務 = _建立認證元件(tmp_path)
    路由器 = 建立網頁認證路由器(服務, 建立SQLite帳密驗證器(路徑), 設定=設定)
    session = next(路由 for 路由 in 路由器.routes if 路由.path.endswith("/session"))
    me = next(路由 for 路由 in 路由器.routes if 路由.path.endswith("/me"))
    assert session.endpoint is me.endpoint
    assert session.dependant.dependencies[0].call is me.dependant.dependencies[0].call
    應用 = 建立網頁應用程式(發布介面相依項((路由器,), ()), 設定)
    with TestClient(應用) as 客戶端:
        assert 客戶端.get("/api/auth/me").status_code == 401
        登入 = 客戶端.post("/api/auth/login", json={"username": "alice", "password": "correct horse"})
        客戶端.cookies.delete("published_web_csrf", path="/api")
        回應 = 客戶端.get("/api/auth/me")
        assert 回應.status_code == 200
        assert 回應.json()["csrf_token"] == 回應.headers["X-CSRF-Token"]


def test_chat_body_only在pre_parser受16KiB限制(tmp_path):
    """exact chat path過大固定422，其他body route不受誤傷。"""
    _, 設定, 服務 = _建立認證元件(tmp_path)
    認證 = 建立網頁認證路由器(服務, lambda *_: None, 設定=設定)
    聊天 = APIRouter(prefix="/api/chat")
    csrf = 建立CSRF相依項(服務, 設定)
    聊天.add_api_route("", lambda _=Depends(csrf): {}, methods=["POST"])
    管理 = APIRouter(prefix="/api/admin")
    管理.add_api_route("/echo", lambda: {}, methods=["POST"], dependencies=[Depends(csrf)])
    應用 = 建立網頁應用程式(發布介面相依項((認證, 聊天, 管理), ()), 設定)
    with TestClient(應用) as 客戶端:
        過大 = 客戶端.post("/api/chat", content=b"x" * 16_385)
        assert 過大.status_code == 422
        assert 過大.json() == {"detail": {"code": "request_invalid"}}
        assert 客戶端.post("/api/admin/echo", content=b"x" * 16_385).status_code == 401


def test_本文上限政策不可外部改寫且不同app各自封存():
    """政策內容不可改寫，且每個middleware實例持有獨立封存快照。"""
    第一個 = 限制登入請求Middleware(object())
    第二個 = 限制登入請求Middleware(object())

    assert 第一個._本文上限政策 is not 第二個._本文上限政策
    with pytest.raises(TypeError):
        第一個._本文上限政策[("POST", "/api/chat")] = 1
    assert 第二個._本文上限政策[("POST", "/api/chat")] == 16_384
