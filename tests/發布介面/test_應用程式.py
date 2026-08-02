"""AUTH A01 canonical FastAPI application factory 與 lifespan 測試。"""

import asyncio
import inspect

import pytest
from fastapi import APIRouter
from fastapi.testclient import TestClient
from fastapi.routing import APIRoute

import 繁中代理.發布介面.應用程式 as 應用程式模組
from 繁中代理.發布介面.相依項 import 發布介面相依項
from 繁中代理.發布介面.應用程式 import 建立應用程式
from 繁中代理.發布介面.設定 import 啟動錯誤訊息, 關閉錯誤訊息


def _空相依項(*工廠):
    """建立沒有 capability router 的 exact composition。"""
    return 發布介面相依項((), tuple(工廠))


class _記錄資源:
    """記錄關閉次數與順序的測試資源。"""

    def __init__(self, 名稱, 事件, 錯誤=None):
        self.名稱 = 名稱
        self.事件 = 事件
        self.錯誤 = 錯誤
        self.關閉次數 = 0

    async def 關閉(self):
        """記錄 exact-once close 並可拋出指定錯誤。"""
        self.關閉次數 += 1
        self.事件.append(f"close:{self.名稱}")
        if self.錯誤 is not None:
            raise self.錯誤


def _資源工廠(資源, 事件):
    """建立會回傳指定資源的 async factory。"""

    async def 建立資源():
        """記錄 startup 順序。"""
        事件.append(f"start:{資源.名稱}")
        return 資源

    return 建立資源


def _失敗工廠(錯誤, 事件):
    """建立拋出指定錯誤的 async factory。"""

    async def 建立失敗資源():
        """記錄失敗 startup。"""
        事件.append("start:fail")
        raise 錯誤

    return 建立失敗資源


def _探針路由器(前綴, 路徑="/probe", 方法="GET"):
    """建立 route inventory 測試 router。"""
    路由器 = APIRouter(prefix=前綴)

    async def 取得探針():
        """回傳固定探針。"""
        return {"probe": "ok"}

    路由器.add_api_route(路徑, 取得探針, methods=[方法])
    return 路由器


def test_health_openapi與未公開路由固定():
    """foundation 只公開 health 與 OpenAPI JSON，且不做 slash redirect。"""
    應用程式 = 建立應用程式(_空相依項())
    with TestClient(應用程式) as 客戶端:
        回應 = 客戶端.get("/healthz")
        assert 回應.status_code == 200
        assert 回應.json() == {"status": "ok"}
        for 路徑 in ("/healthz/", "/unknown", "/docs", "/redoc"):
            assert 客戶端.get(路徑, follow_redirects=False).status_code == 404
        assert 客戶端.get("/openapi.json").status_code == 200

    assert {getattr(路由, "path") for 路由 in 應用程式.routes if hasattr(路由, "path")} == {
        "/healthz", "/openapi.json"
    }
    規格 = 應用程式.openapi()
    assert set(規格["paths"]) == {"/healthz"}
    assert 規格["info"] == {"title": "繁中代理發布介面", "version": "0.1.0"}


def test_兩個factory應用程式並行隔離exact相依項與資源():
    """獨立 app 不共享 dependency、resource 或 request-global state。"""
    事件一, 事件二 = [], []
    資源一 = _記錄資源("one", 事件一)
    資源二 = _記錄資源("two", 事件二)
    相依項一 = _空相依項(_資源工廠(資源一, 事件一))
    相依項二 = _空相依項(_資源工廠(資源二, 事件二))
    應用程式一 = 建立應用程式(相依項一)
    應用程式二 = 建立應用程式(相依項二)

    with TestClient(應用程式一), TestClient(應用程式二):
        assert 應用程式一.state.發布介面相依項.路由器清單 == 相依項一.路由器清單
        assert 應用程式二.state.發布介面相依項.路由器清單 == 相依項二.路由器清單
        assert 應用程式一.state.發布介面資源 == (資源一,)
        assert 應用程式二.state.發布介面資源 == (資源二,)

    assert 資源一.關閉次數 == 資源二.關閉次數 == 1
    assert not hasattr(應用程式模組, "app")


def test_router掛載與獨立建立的openapi決定性():
    """允許未來 auth、management 與 invoke routers，且跨 app 規格一致。"""
    前綴清單 = ("/api/auth", "/api/admin", "/v1/endpoints")
    第一組 = tuple(_探針路由器(前綴) for 前綴 in 前綴清單)
    第二組 = tuple(_探針路由器(前綴) for 前綴 in 前綴清單)
    第一個 = 建立應用程式(發布介面相依項(第一組, ()))
    第二個 = 建立應用程式(發布介面相依項(第二組, ()))

    assert 第一個.openapi() == 第二個.openapi()
    assert set(第一個.openapi()["paths"]) == {
        "/healthz", "/api/auth/probe", "/api/admin/probe", "/v1/endpoints/probe"
    }


def test_duplicate_method_path與api_auth_collision在publication前拒絕():
    """FastAPI 不得以 first-handler/OpenAPI overwrite 接受 collision。"""
    第一個 = _探針路由器("/api/auth")
    第二個 = _探針路由器("/api/auth")
    相依項 = 發布介面相依項((第一個, 第二個), ())

    with pytest.raises(ValueError, match="^發布介面路由設定無效$"):
        建立應用程式(相依項)

    assert not hasattr(相依項, "應用程式")


def test_same_path不同method合法且mutated_namespace拒絕():
    """operation uniqueness 包含 method；但 mutable router 仍在 factory 時重驗。"""
    讀取 = _探針路由器("/api/auth", 方法="GET")
    寫入 = _探針路由器("/api/auth", 方法="POST")
    應用程式 = 建立應用程式(發布介面相依項((讀取, 寫入), ()))
    assert set(應用程式.openapi()["paths"]["/api/auth/probe"]) == {"get", "post"}

    assert isinstance(讀取.routes[0], APIRoute)
    讀取.routes[0].path = "/api/authentic/probe"
    with pytest.raises(ValueError):
        建立應用程式(發布介面相依項((讀取,), ()))


def test_startup失敗會反向exact_once清理並固定一般錯誤():
    """部分 startup 失敗不得 publication，已建立資源反向關閉一次。"""
    事件 = []
    第一個 = _記錄資源("one", 事件)
    第二個 = _記錄資源("two", 事件)
    相依項 = _空相依項(
        _資源工廠(第一個, 事件),
        _資源工廠(第二個, 事件),
        _失敗工廠(RuntimeError("private-startup"), 事件),
    )

    with pytest.raises(RuntimeError, match=f"^{啟動錯誤訊息}$") as 錯誤:
        with TestClient(建立應用程式(相依項)):
            pass

    assert 錯誤.value.__cause__ is None and 錯誤.value.__context__ is None
    assert 事件 == ["start:one", "start:two", "start:fail", "close:two", "close:one"]
    assert (第一個.關閉次數, 第二個.關閉次數) == (1, 1)


def test_shutdown反向全清理且一般錯誤固定():
    """一個 close ordinary failure 不阻止其餘 close，外部只見固定錯誤。"""
    事件 = []
    第一個 = _記錄資源("one", 事件)
    第二個 = _記錄資源("two", 事件, RuntimeError("private-close"))
    客戶端 = TestClient(建立應用程式(_空相依項(
        _資源工廠(第一個, 事件), _資源工廠(第二個, 事件)
    )))

    with pytest.raises(RuntimeError, match=f"^{關閉錯誤訊息}$") as 錯誤:
        with 客戶端:
            pass

    assert 錯誤.value.__cause__ is None and 錯誤.value.__context__ is None
    assert 事件[-2:] == ["close:two", "close:one"]
    assert (第一個.關閉次數, 第二個.關閉次數) == (1, 1)


@pytest.mark.parametrize("錯誤", [KeyboardInterrupt("k"), SystemExit("s"), GeneratorExit("g")])
def test_startup_KISG保留identity與args(錯誤):
    """startup KISG 不得被一般化或被 cleanup ordinary failure 蓋掉。"""
    事件 = []
    資源 = _記錄資源("one", 事件, RuntimeError("cleanup"))
    相依項 = _空相依項(_資源工廠(資源, 事件), _失敗工廠(錯誤, 事件))
    應用程式 = 建立應用程式(相依項)

    async def 執行生命週期():
        """直接執行 lifespan，避免 TestClient/AnyIO 改寫 KISG。"""
        async with 應用程式.router.lifespan_context(應用程式):
            pass

    with pytest.raises(type(錯誤)) as 捕捉:
        asyncio.run(執行生命週期())

    assert 捕捉.value is 錯誤
    assert 捕捉.value.args == 錯誤.args
    assert 資源.關閉次數 == 1


@pytest.mark.parametrize("錯誤", [KeyboardInterrupt("k"), SystemExit("s"), GeneratorExit("g")])
def test_shutdown_KISG保留identity_args且仍完整反向清理(錯誤):
    """shutdown KISG 優先於 ordinary cleanup，且不阻止其餘資源關閉。"""
    事件 = []
    第一個 = _記錄資源("one", 事件, RuntimeError("ordinary"))
    第二個 = _記錄資源("two", 事件, 錯誤)
    應用程式 = 建立應用程式(_空相依項(
        _資源工廠(第一個, 事件), _資源工廠(第二個, 事件)
    ))

    async def 執行生命週期():
        """直接執行完整 startup/shutdown。"""
        async with 應用程式.router.lifespan_context(應用程式):
            pass

    with pytest.raises(type(錯誤)) as 捕捉:
        asyncio.run(執行生命週期())
    assert 捕捉.value is 錯誤 and 捕捉.value.args == 錯誤.args
    assert 事件[-2:] == ["close:two", "close:one"]
    assert 第一個.關閉次數 == 第二個.關閉次數 == 1


def test_container與factory皆拒絕不安全composition形狀():
    """沒有 optional admin fallback，且 public factory 只接受 exact container。"""
    with pytest.raises(TypeError):
        建立應用程式()  # type: ignore[call-arg]
    with pytest.raises(ValueError):
        發布介面相依項([], ())  # type: ignore[arg-type]

    class 相依項子類(發布介面相依項):
        """模擬可竄改的 composition subclass。"""

    with pytest.raises(ValueError):
        建立應用程式(相依項子類((), ()))

    assert list(inspect.signature(建立應用程式).parameters) == ["相依項"]
