"""AUTH A02 E1 canonical current-session hook 與 TTL coherence。"""
import inspect

import pytest
from fastapi import Depends
from fastapi.testclient import TestClient

from 繁中代理.使用者 import 使用者庫
from 繁中代理.發布介面 import 建立目前工作階段相依項 as 套件目前工作階段相依項
from 繁中代理.發布介面.相依項 import 發布介面相依項
from 繁中代理.發布介面.應用程式 import 建立網頁應用程式
from 繁中代理.發布介面.設定 import 網頁安全設定
from 繁中代理.發布介面.網頁工作階段 import 網頁使用者, 網頁工作階段服務
from 繁中代理.發布介面.路由 import (
    建立SQLite帳密驗證器,
    建立目前工作階段相依項,
    建立網頁認證路由器,
)
import 繁中代理.發布介面.路由.網頁認證 as 認證模組


def _建立資料庫(tmp_path):
    路徑 = tmp_path / "e1.sqlite3"
    使用者們 = 使用者庫(路徑)
    alice = 使用者們.建立使用者("alice", "correct horse", roles=["admin"])
    遷移 = (
        __import__("pathlib").Path(__file__).parents[2]
        / "繁中代理/發布介面/遷移/0005_建立網頁工作階段.sql"
    ).read_text()
    使用者們.連線.executescript(遷移)
    使用者們.連線.close()
    return 路徑, alice


def test_目前工作階段相依項公開identity與exact_signature():
    """package/route API 是同一 callable，且只要求 exact injected service/settings。"""
    assert 套件目前工作階段相依項 is 建立目前工作階段相依項
    簽名 = inspect.signature(建立目前工作階段相依項)
    assert list(簽名.parameters) == ["服務", "設定"]
    assert all(參數.kind is inspect.Parameter.POSITIONAL_OR_KEYWORD for 參數 in 簽名.parameters.values())


def test_GET_session實際使用canonical相依且recovery同步header_cookie(tmp_path, monkeypatch):
    """actual route 只透過 canonical hook restore 一次，rotation successor 一致。"""
    路徑, _ = _建立資料庫(tmp_path)
    設定 = 網頁安全設定(("http://localhost:5173",), Cookie安全=False, 工作階段有效秒數=60)
    服務 = 網頁工作階段服務(路徑, 有效秒數=60)
    捕捉 = []
    原工廠 = 認證模組.建立目前工作階段相依項

    def 捕捉工廠(注入服務, 注入設定):
        相依 = 原工廠(注入服務, 注入設定)
        捕捉.append(相依)
        return 相依

    monkeypatch.setattr(認證模組, "建立目前工作階段相依項", 捕捉工廠)
    路由器 = 建立網頁認證路由器(服務, 建立SQLite帳密驗證器(路徑), 設定=設定)
    session路由 = next(路由 for 路由 in 路由器.routes if 路由.path == "/api/auth/session")
    assert [節點.call for 節點 in session路由.dependant.dependencies] == 捕捉
    應用 = 建立網頁應用程式(發布介面相依項((路由器,), ()), 設定)

    原恢復 = 服務.恢復
    呼叫 = []
    def 計數恢復(*參數):
        呼叫.append(參數)
        return 原恢復(*參數)
    服務.恢復 = 計數恢復

    with TestClient(應用) as 客戶端:
        登入 = 客戶端.post("/api/auth/login", json={"username": "alice", "password": "correct horse"})
        舊csrf = 登入.json()["csrf_token"]
        客戶端.cookies.delete("published_web_csrf", path="/api")
        恢復 = 客戶端.get("/api/auth/session")
    assert 恢復.status_code == 200 and len(呼叫) == 1
    新csrf = 恢復.headers["X-CSRF-Token"]
    assert 新csrf != 舊csrf
    assert 恢復.json()["csrf_token"] == 新csrf
    assert 恢復.cookies["published_web_csrf"] == 新csrf


def test_目前工作階段相依只回傳最小principal(tmp_path):
    """A03 adapter point 不夾帶 service/result/authorization state。"""
    路徑, alice = _建立資料庫(tmp_path)
    設定 = 網頁安全設定(("http://localhost:5173",), Cookie安全=False, 工作階段有效秒數=60)
    服務 = 網頁工作階段服務(路徑, 時鐘=lambda: 1000.0, 有效秒數=60)
    發行 = 服務.發行(網頁使用者(alice["id"], "alice", "admin"))
    相依 = 建立目前工作階段相依項(服務, 設定)
    from fastapi import Request, Response
    請求 = Request({"type": "http", "headers": [(b"cookie", (
        f"published_web_session={發行.工作階段權杖}; published_web_csrf={發行.CSRF權杖}"
    ).encode())]})
    assert 相依(請求, Response()) == 網頁使用者(alice["id"], "alice", "admin")


def test_TTL_mismatch_route與app皆在callback前固定拒絕(tmp_path):
    """service authoritative TTL 必須與 cookie TTL exact 相等，且 mismatch callback zero。"""
    路徑, _ = _建立資料庫(tmp_path)
    六十秒 = 網頁安全設定(("http://localhost:5173",), Cookie安全=False, 工作階段有效秒數=60)
    一百二十秒 = 網頁安全設定(("http://localhost:5173",), Cookie安全=False, 工作階段有效秒數=120)
    服務 = 網頁工作階段服務(路徑, 有效秒數=60)
    呼叫 = []
    def 驗證器(*參數):
        呼叫.append(參數)
        raise AssertionError

    服務.讀取有效秒數 = lambda: (_ for _ in ()).throw(AssertionError("hostile shadow"))
    with pytest.raises(ValueError, match="^Web認證設定無效$"):
        建立網頁認證路由器(服務, 驗證器, 設定=一百二十秒)
    assert 呼叫 == []

    路由器 = 建立網頁認證路由器(服務, 驗證器, 設定=六十秒)
    with pytest.raises(ValueError, match="^發布介面路由設定無效$"):
        建立網頁應用程式(發布介面相依項((路由器,), ()), 一百二十秒)
    assert 呼叫 == []
