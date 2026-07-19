"""AUTH A01 route inventory 與 forged composition 回歸測試。"""

import asyncio

import pytest
from fastapi import APIRouter, FastAPI
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from 繁中代理.發布介面.相依項 import 發布介面相依項
from 繁中代理.發布介面.應用程式 import 建立應用程式
from 繁中代理.發布介面.設定 import 啟動錯誤訊息, 路由設定錯誤訊息


class _集合子類(set):
    """不可信 methods collection subclass。"""


class _字串子類(str):
    """不可信 HTTP method scalar subclass。"""


class _tuple子類(tuple):
    """若 boundary 誤迭代 forged tuple 即留下證據。"""

    迭代次數 = 0

    def __iter__(self):
        type(self).迭代次數 += 1
        raise AssertionError("不得迭代")


class _路由器子類(APIRouter):
    """拒絕可覆寫 framework attributes 的 router subclass。"""


def _路由器(方法="GET"):
    """建立單一路由的 canonical router。"""
    路由器 = APIRouter(prefix="/api/auth")
    路由器.add_api_route("/probe", lambda: {"ok": True}, methods=[方法])
    return 路由器


def _偽造相依項(路由器清單, 工廠清單):
    """繞過 frozen dataclass constructor 寫入 slot。"""
    相依項 = object.__new__(發布介面相依項)
    object.__setattr__(相依項, "路由器清單", 路由器清單)
    object.__setattr__(相依項, "資源工廠清單", 工廠清單)
    return 相依項


@pytest.mark.parametrize(
    "方法集合",
    [set(), _集合子類({"GET"}), {"get"}, {"CUSTOM"}, {_字串子類("GET")}],
)
def test_methods必須是非空exact_builtin與允許大寫token(方法集合):
    """空、subclass、小寫、custom token 與 scalar subclass 全數 fail closed。"""
    路由器 = _路由器()
    路由器.routes[0].methods = 方法集合
    with pytest.raises(ValueError, match=f"^{路由設定錯誤訊息}$"):
        建立應用程式(發布介面相依項((路由器,), ()))


def test_普通GET不自動增加HEAD也不虛構OPTIONS():
    """Inventory 與實際 framework GET route 維持 exact GET。"""
    應用程式 = 建立應用程式(發布介面相依項((_路由器(),), ()))
    路由 = next(路由 for 路由 in 應用程式.routes if isinstance(路由, APIRoute) and 路由.path.endswith("probe"))
    assert 路由.methods == {"GET"}
    with TestClient(應用程式) as 客戶端:
        assert 客戶端.get("/api/auth/probe").status_code == 200
        assert 客戶端.head("/api/auth/probe").status_code == 405
        assert 客戶端.options("/api/auth/probe").status_code == 405


def test_空methods若直接掛載會truthfully接受所有方法但factory拒絕():
    """證明 Starlette 空集合是 fail-open，且 canonical factory 不會 publish。"""
    路由器 = _路由器()
    路由 = 路由器.routes[0]
    路由.methods.clear()
    with pytest.raises(ValueError):
        建立應用程式(發布介面相依項((路由器,), ()))

    不安全應用 = FastAPI()
    不安全應用.router.routes.append(路由)
    with TestClient(不安全應用) as 客戶端:
        for 方法 in ("GET", "POST", "HEAD", "OPTIONS"):
            assert 客戶端.request(方法, "/api/auth/probe").status_code == 200


def test_include期間methods突變會在return前拒絕(monkeypatch):
    """source replay 與 final inventory 封閉 validation/include TOCTOU。"""
    路由器 = _路由器()
    原始include = FastAPI.include_router

    def include後清空(self, supplied_router, *args, **kwargs):
        原始include(self, supplied_router, *args, **kwargs)
        supplied_router.routes[0].methods.clear()

    monkeypatch.setattr(FastAPI, "include_router", include後清空)
    with pytest.raises(ValueError, match=f"^{路由設定錯誤訊息}$"):
        建立應用程式(發布介面相依項((路由器,), ()))


@pytest.mark.parametrize("欄位", ["路由器清單", "資源工廠清單"])
@pytest.mark.parametrize("不安全值", [[], _tuple子類(())])
def test_forged容器拒絕list與tuple子類且零迭代零副作用(欄位, 不安全值):
    """slot descriptor 讀值後先 exact-type gate，不觸發 hostile iterator。"""
    _tuple子類.迭代次數 = 0
    呼叫次數 = 0

    async def 工廠():
        nonlocal 呼叫次數
        呼叫次數 += 1

    路由器值 = 不安全值 if 欄位 == "路由器清單" else ()
    工廠值 = 不安全值 if 欄位 == "資源工廠清單" else (工廠,)
    with pytest.raises(ValueError):
        建立應用程式(_偽造相依項(路由器值, 工廠值))
    assert 呼叫次數 == 0
    assert _tuple子類.迭代次數 == 0


def test_exact_router政策拒絕子類且零routes():
    """安全政策凍結為 exact APIRouter。"""
    路由器 = _路由器子類(prefix="/api/auth")
    路由器.add_api_route("/probe", lambda: None, methods=["GET"])
    with pytest.raises(ValueError):
        建立應用程式(發布介面相依項((路由器,), ()))


def test_原container欄位後續替換不影響captured路由與factory():
    """app 僅使用 fresh module-owned container/tuples。"""
    事件 = []

    class 資源:
        async def 關閉(self):
            事件.append("close")

    async def 原工廠():
        事件.append("start")
        return 資源()

    async def 陷阱工廠():
        raise AssertionError("不得呼叫")

    原相依項 = 發布介面相依項((_路由器(),), (原工廠,))
    應用程式 = 建立應用程式(原相依項)
    object.__setattr__(原相依項, "路由器清單", (_路由器("POST"),))
    object.__setattr__(原相依項, "資源工廠清單", (陷阱工廠,))
    with TestClient(應用程式) as 客戶端:
        assert 客戶端.get("/api/auth/probe").status_code == 200
        assert 客戶端.post("/api/auth/probe").status_code == 405
    assert 事件 == ["start", "close"]


def test_lifespan前重新驗證factory_callable():
    """組裝後 callable contract 被替換時 startup fail closed 且零呼叫。"""
    class 工廠:
        呼叫次數 = 0

        async def __call__(self):
            type(self).呼叫次數 += 1

    工廠物件 = 工廠()
    應用程式 = 建立應用程式(發布介面相依項((), (工廠物件,)))
    工廠.__call__ = None

    async def 執行():
        async with 應用程式.router.lifespan_context(應用程式):
            pass

    with pytest.raises(RuntimeError, match=f"^{啟動錯誤訊息}$"):
        asyncio.run(執行())
    assert 工廠.呼叫次數 == 0
