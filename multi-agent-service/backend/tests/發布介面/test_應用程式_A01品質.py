"""AUTH A01 resource/state ownership 與 operation ID namespace 品質回歸。"""

import asyncio

import pytest
from fastapi import APIRouter, FastAPI
from fastapi.routing import APIRoute
from starlette.datastructures import State

from 繁中代理.發布介面.相依項 import 發布介面相依項
from 繁中代理.發布介面.應用程式 import 建立應用程式
from 繁中代理.發布介面.設定 import 啟動錯誤訊息, 路由設定錯誤訊息, 關閉錯誤訊息


class _資源:
    """允許 close boundary mutation 的記錄資源。"""

    def __init__(self, 事件):
        self.事件 = 事件
        self.次數 = 0

    async def 關閉(self):
        self.次數 += 1
        self.事件.append("close")


class _敵意描述器:
    """若錯誤動態 bind 就記錄 callback。"""

    次數 = 0

    def __get__(self, instance, owner):
        type(self).次數 += 1
        raise AssertionError("不得執行 descriptor")


class _敵意資源:
    """具有靜態可拒絕的非函式 close descriptor。"""

    關閉 = _敵意描述器()


class _字串子類(str):
    """operation ID 不可信 scalar subclass。"""


def _工廠(產品):
    """建立回傳指定 identity 的 async factory。"""

    async def 建立():
        return 產品

    return 建立


def _執行生命週期(應用程式, 主體=None):
    """直接執行 lifespan，保留 KISG identity。"""

    async def 執行():
        async with 應用程式.router.lifespan_context(應用程式):
            if 主體 is not None:
                await 主體(應用程式)

    return asyncio.run(執行())


def _路由器(路徑, *, operation_id=None):
    """建立可指定 explicit operation ID 的 canonical route。"""
    路由器 = APIRouter(prefix="/api/auth")
    路由器.add_api_route(路徑, lambda: None, methods=["GET"], operation_id=operation_id)
    return 路由器


@pytest.mark.parametrize("產品", [None, object()])
def test_無有效close_boundary的產品拒絕且先前資源rollback(產品):
    """None/ordinary product 都不得 publish，先前 entry 仍關閉一次。"""
    事件 = []
    先前 = _資源(事件)
    應用程式 = 建立應用程式(發布介面相依項((), (_工廠(先前), _工廠(產品))))
    with pytest.raises(RuntimeError, match=f"^{啟動錯誤訊息}$"):
        _執行生命週期(應用程式)
    assert 先前.次數 == 1 and 事件 == ["close"]
    assert not hasattr(應用程式.state, "發布介面相依項")
    assert not hasattr(應用程式.state, "發布介面資源")


def test_敵意close_descriptor靜態拒絕且callback為零():
    """close capture 不得觸發 product property/descriptor。"""
    _敵意描述器.次數 = 0
    應用程式 = 建立應用程式(發布介面相依項((), (_工廠(_敵意資源()),)))
    with pytest.raises(RuntimeError, match=f"^{啟動錯誤訊息}$"):
        _執行生命週期(應用程式)
    assert _敵意描述器.次數 == 0


def test_duplicate產品第二次拒絕且只關閉第一個entry一次():
    """identity 重複不使用 hash/eq，也不建立第二個 ownership entry。"""
    資源 = _資源([])
    應用程式 = 建立應用程式(發布介面相依項((), (_工廠(資源), _工廠(資源))))
    with pytest.raises(RuntimeError, match=f"^{啟動錯誤訊息}$"):
        _執行生命週期(應用程式)
    assert 資源.次數 == 1


def test_startup後close_attribute突變不能redirect捕捉的closer():
    """shutdown 永遠呼叫 startup 時捕捉的 class function binding。"""
    事件 = []
    資源 = _資源(事件)
    應用程式 = 建立應用程式(發布介面相依項((), (_工廠(資源),)))

    async def 主體(_):
        async def 陷阱():
            raise AssertionError("不得重新讀取 close attribute")

        資源.關閉 = 陷阱

    _執行生命週期(應用程式, 主體)
    assert 資源.次數 == 1 and 事件 == ["close"]


def test_body刪除與竄改public_state不影響內部ownership():
    """public state 不是 shutdown authority，corruption 仍被移除。"""
    第一個, 第二個 = _資源([]), _資源([])
    應用程式 = 建立應用程式(發布介面相依項((), (_工廠(第一個), _工廠(第二個))))

    async def 主體(app):
        del app.state.發布介面資源
        app.state.發布介面資源 = ("corrupt",)
        app.state.發布介面相依項 = "corrupt"

    _執行生命週期(應用程式, 主體)
    assert 第一個.次數 == 第二個.次數 == 1
    assert not hasattr(應用程式.state, "發布介面資源")
    assert not hasattr(應用程式.state, "發布介面相依項")


@pytest.mark.parametrize("錯誤", [RuntimeError("set-private"), SystemExit("set-control")])
def test_publication第二個set失敗會移除兩欄並rollback(monkeypatch, 錯誤):
    """partial publication 不得留下 state，set control 保留 identity。"""
    原始設定 = State.__setattr__
    次數 = 0

    def 失敗設定(self, key, value):
        nonlocal 次數
        次數 += 1
        if 次數 == 2:
            raise 錯誤
        return 原始設定(self, key, value)

    monkeypatch.setattr(State, "__setattr__", 失敗設定)
    資源 = _資源([])
    應用程式 = 建立應用程式(發布介面相依項((), (_工廠(資源),)))
    with pytest.raises(type(錯誤)) as 捕捉:
        _執行生命週期(應用程式)
    if isinstance(錯誤, SystemExit):
        assert 捕捉.value is 錯誤
    else:
        assert 捕捉.value.args == (啟動錯誤訊息,)
    assert 資源.次數 == 1 and 次數 == 2
    assert not hasattr(應用程式.state, "發布介面資源")
    assert not hasattr(應用程式.state, "發布介面相依項")


def test_state兩次remove皆嘗試且control優先於ordinary_close(monkeypatch):
    """第一個 state KISG 勝出，但不跳過第二次 remove 或 resource close。"""
    原始刪除 = State.__delattr__
    名稱清單 = []
    控制錯誤 = SystemExit("state-control")

    def 失敗刪除(self, key):
        名稱清單.append(key)
        if len(名稱清單) == 1:
            raise 控制錯誤
        return 原始刪除(self, key)

    monkeypatch.setattr(State, "__delattr__", 失敗刪除)
    資源 = _資源([])
    應用程式 = 建立應用程式(發布介面相依項((), (_工廠(資源),)))
    with pytest.raises(SystemExit) as 捕捉:
        _執行生命週期(應用程式)
    assert 捕捉.value is 控制錯誤
    assert 名稱清單 == ["發布介面資源", "發布介面相依項"]
    assert 資源.次數 == 1


def test_body_KISG勝過state_cleanup_KISG且資源仍關閉(monkeypatch):
    """body control 是 primary；state cleanup control loser 不得阻止 close。"""
    主體錯誤 = KeyboardInterrupt("body-control")
    monkeypatch.setattr(State, "__delattr__", lambda self, key: (_ for _ in ()).throw(SystemExit(key)))
    資源 = _資源([])
    應用程式 = 建立應用程式(發布介面相依項((), (_工廠(資源),)))

    async def 主體(_):
        raise 主體錯誤

    with pytest.raises(KeyboardInterrupt) as 捕捉:
        _執行生命週期(應用程式, 主體)
    assert 捕捉.value is 主體錯誤 and 資源.次數 == 1


def test_state普通cleanup錯誤映射固定unchained錯誤(monkeypatch):
    """ordinary state removal failure 參與固定 shutdown error policy。"""
    原始刪除 = State.__delattr__
    次數 = 0

    def 刪除(self, key):
        nonlocal 次數
        次數 += 1
        if 次數 == 1:
            raise RuntimeError("private-state")
        return 原始刪除(self, key)

    monkeypatch.setattr(State, "__delattr__", 刪除)
    應用程式 = 建立應用程式(發布介面相依項((), ()))
    with pytest.raises(RuntimeError, match=f"^{關閉錯誤訊息}$") as 捕捉:
        _執行生命週期(應用程式)
    assert 捕捉.value.__cause__ is None and 捕捉.value.__context__ is None
    assert 次數 == 2


def test_duplicate_explicit與autogenerated有效ID皆在return前拒絕():
    """不同 method/path 也共享一個全 app operation-ID namespace。"""
    第一個 = _路由器("/one", operation_id="same")
    第二個 = _路由器("/two", operation_id="same")
    with pytest.raises(ValueError, match=f"^{路由設定錯誤訊息}$"):
        建立應用程式(發布介面相依項((第一個, 第二個), ()))

    第一個, 第二個 = _路由器("/one"), _路由器("/two")
    setattr(第一個.routes[0], "unique_id", "same-auto")
    setattr(第二個.routes[0], "unique_id", "same-auto")
    with pytest.raises(ValueError, match=f"^{路由設定錯誤訊息}$"):
        建立應用程式(發布介面相依項((第一個, 第二個), ()))


@pytest.mark.parametrize("識別碼", ["", "x" * 257, _字串子類("subclass")])
def test_effective_operation_ID必須exact_nonempty_bounded_str(識別碼):
    """explicit effective ID 不接受空值、超界或 scalar subclass。"""
    with pytest.raises(ValueError, match=f"^{路由設定錯誤訊息}$"):
        建立應用程式(發布介面相依項((_路由器("/probe", operation_id=識別碼),), ()))


def test_include期間operation_ID突變由source_replay偵測(monkeypatch):
    """structural capture 同時封閉 explicit/effective ID mutation。"""
    路由器 = _路由器("/probe")
    原始include = FastAPI.include_router

    def include後突變(self, supplied_router, *args, **kwargs):
        原始include(self, supplied_router, *args, **kwargs)
        supplied_router.routes[0].operation_id = "mutated"

    monkeypatch.setattr(FastAPI, "include_router", include後突變)
    with pytest.raises(ValueError, match=f"^{路由設定錯誤訊息}$"):
        建立應用程式(發布介面相依項((路由器,), ()))


def test_include期間替換endpoint再還原仍拒絕且不回傳app(monkeypatch):
    """final route 必須保留 capture 時的 exact handler identity，而非只重播 source。"""
    路由器 = _路由器("/probe")
    來源路由 = 路由器.routes[0]
    assert isinstance(來源路由, APIRoute)
    原始端點 = 來源路由.endpoint
    原始include = FastAPI.include_router

    def 惡意端點():
        return {"malicious": True}

    def include期間替換(self, supplied_router, *args, **kwargs):
        替換路由 = supplied_router.routes[0]
        assert isinstance(替換路由, APIRoute)
        替換路由.endpoint = 惡意端點
        try:
            return 原始include(self, supplied_router, *args, **kwargs)
        finally:
            替換路由.endpoint = 原始端點

    monkeypatch.setattr(FastAPI, "include_router", include期間替換)
    結果 = None
    with pytest.raises(ValueError, match=f"^{路由設定錯誤訊息}$"):
        結果 = 建立應用程式(發布介面相依項((路由器,), ()))
    assert 結果 is None
    assert 來源路由.endpoint is 原始端點


def test_stateful_unique_ID_generator跨include漂移會拒絕():
    """source effective ID A 即使 final B 仍 unique，也不得 publication。"""
    呼叫清單 = []

    def 產生識別碼(_):
        呼叫清單.append(len(呼叫清單) + 1)
        return "source-A" if len(呼叫清單) == 1 else "final-B"

    路由器 = APIRouter(prefix="/api/auth", generate_unique_id_function=產生識別碼)
    路由器.add_api_route("/probe", lambda: None, methods=["GET"])
    assert 呼叫清單 == [1]
    with pytest.raises(ValueError, match=f"^{路由設定錯誤訊息}$"):
        建立應用程式(發布介面相依項((路由器,), ()))
    assert 呼叫清單 == [1, 2, 3]


def test_final_route保留callable_object身份與source有效ID():
    """ordinary deterministic include 保留 non-function handler identity 與 operation ID。"""
    class 處理器:
        def __call__(self):
            return {"ok": True}

        def __eq__(self, other):
            raise AssertionError("不得比較 endpoint")

        def __hash__(self):
            raise AssertionError("不得雜湊 endpoint")

    端點 = 處理器()
    路由器 = APIRouter(prefix="/api/auth")
    路由器.add_api_route("/probe", 端點, methods=["GET"])
    來源路由 = 路由器.routes[0]
    assert isinstance(來源路由, APIRoute)
    應用程式 = 建立應用程式(發布介面相依項((路由器,), ()))
    最終路由 = next(
        路由 for 路由 in 應用程式.routes if isinstance(路由, APIRoute) and 路由.path == "/api/auth/probe"
    )
    assert 最終路由.endpoint is 端點
    assert 最終路由.unique_id == 來源路由.unique_id


def test_traceback_marker_scanner_known_leaking_positive_control():
    """positive control 證明 fresh visited-set scanner 看得到直接與 self 可達 marker。"""
    from test_應用程式_A01生命週期 import _含標記

    標記 = "KNOWN-LEAKING-MARKER"
    資源 = _資源([])
    setattr(資源, "標記", 標記)
    assert _含標記(標記, 標記, set())
    assert _含標記(資源, 標記, set())
