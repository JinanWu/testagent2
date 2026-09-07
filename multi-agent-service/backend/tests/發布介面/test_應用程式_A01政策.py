"""AUTH A01 dependency、response 與 lifespan ABA 政策回歸。"""

from contextlib import asynccontextmanager

import pytest
from fastapi import APIRouter, Depends, FastAPI
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient
from pydantic import BaseModel, Field

from 繁中代理.發布介面.相依項 import 發布介面相依項
from 繁中代理.發布介面.應用程式 import 建立應用程式
from 繁中代理.發布介面.設定 import 路由設定錯誤訊息


class _輸出(BaseModel):
    """驗證 response model 與 alias 序列化政策。"""

    公開: str = Field(alias="public")
    秘密: str = Field(alias="secret")
    可省略: str | None = Field(default=None, alias="optional")


class _敵意相依:
    """任何 equality/hash callback 都是政策邊界缺陷。"""

    def __init__(self):
        self.雜湊次數 = 0
        self.比較次數 = 0
        self.呼叫次數 = 0

    def __call__(self):
        self.呼叫次數 += 1

    def __hash__(self):
        self.雜湊次數 += 1
        raise AssertionError("不得雜湊 dependency callback")

    def __eq__(self, other):
        if other is type or other is object:
            return False
        self.比較次數 += 1
        raise AssertionError("不得比較 dependency callback")


class _資源:
    """記錄 app-owned factory resource lifecycle。"""

    def __init__(self, 事件):
        self.事件 = 事件

    async def 關閉(self):
        self.事件.append("resource-close")


def _回應路由器(**參數):
    """建立有完整 response policy 的單一路由。"""
    路由器 = APIRouter(prefix="/api/auth")

    def 端點():
        return {"public": "shown", "secret": "hidden", "optional": None}

    預設 = dict(
        response_model=_輸出,
        response_model_include={"公開"},
        response_model_exclude={"秘密"},
        response_model_by_alias=True,
        response_model_exclude_unset=True,
        response_model_exclude_defaults=True,
        response_model_exclude_none=True,
    )
    預設.update(參數)
    路由器.add_api_route("/probe", 端點, methods=["GET"], **預設)
    return 路由器


def _include期間ABA(monkeypatch, 突變):
    """只在 framework include 期間突變 sanitized route，再完整還原。"""
    原始include = FastAPI.include_router

    def 包裝(self, supplied_router, *args, **kwargs):
        還原 = 突變(supplied_router.routes[0])
        try:
            return 原始include(self, supplied_router, *args, **kwargs)
        finally:
            還原()

    monkeypatch.setattr(FastAPI, "include_router", 包裝)


def test_route_level_auth_Depends移除ABA拒絕且來源還原(monkeypatch):
    """include 取得的 weakened dependency graph 不得因 source restore 而通過。"""
    呼叫次數 = 0

    def 驗證身分():
        nonlocal 呼叫次數
        呼叫次數 += 1

    宣告 = Depends(驗證身分)
    路由器 = APIRouter(prefix="/api/auth")
    路由器.add_api_route("/probe", lambda: None, methods=["GET"], dependencies=[宣告])

    def 突變(路由):
        原值 = list(路由.dependencies)
        路由.dependencies.clear()
        return lambda: 路由.dependencies.extend(原值)

    _include期間ABA(monkeypatch, 突變)
    with pytest.raises(ValueError, match=f"^{路由設定錯誤訊息}$"):
        建立應用程式(發布介面相依項((路由器,), ()))
    assert 路由器.routes[0].dependencies == [宣告]
    assert 呼叫次數 == 0


def test_dependency_callable_swap_ABA拒絕且敵意hash_eq皆零(monkeypatch):
    """call identity 漂移以 is 偵測，不執行 callback equality/hash。"""
    原相依 = _敵意相依()
    替換相依 = _敵意相依()
    宣告 = Depends(原相依)
    路由器 = APIRouter(prefix="/api/auth")
    路由器.add_api_route("/probe", lambda: None, methods=["GET"], dependencies=[宣告])

    def 突變(路由):
        安全宣告 = 路由.dependencies[0]
        原呼叫 = 安全宣告.dependency
        安全宣告.dependency = 替換相依
        return lambda: setattr(安全宣告, "dependency", 原呼叫)

    _include期間ABA(monkeypatch, 突變)
    with pytest.raises(ValueError, match=f"^{路由設定錯誤訊息}$"):
        建立應用程式(發布介面相依項((路由器,), ()))
    assert 原相依.雜湊次數 == 原相依.比較次數 == 原相依.呼叫次數 == 0
    assert 替換相依.雜湊次數 == 替換相依.比較次數 == 替換相依.呼叫次數 == 0


def test_response_model暫時None_ABA拒絕(monkeypatch):
    """include 期間移除 response model 所產生的 final fields 必須拒絕。"""
    路由器 = _回應路由器()

    def 突變(路由):
        原值 = 路由.response_model
        路由.response_model = None
        return lambda: setattr(路由, "response_model", 原值)

    _include期間ABA(monkeypatch, 突變)
    with pytest.raises(ValueError, match=f"^{路由設定錯誤訊息}$"):
        建立應用程式(發布介面相依項((路由器,), ()))


@pytest.mark.parametrize(
    ("欄位", "暫時值"),
    [
        ("response_model_include", None),
        ("response_model_exclude", None),
        ("response_model_by_alias", False),
        ("response_model_exclude_unset", False),
        ("response_model_exclude_defaults", False),
        ("response_model_exclude_none", False),
    ],
)
def test_完整response_serialization_policy_ABA拒絕(monkeypatch, 欄位, 暫時值):
    """installed FastAPI 的 include/exclude/alias/exclude_* 全數 final parity。"""
    路由器 = _回應路由器()

    def 突變(路由):
        原值 = getattr(路由, 欄位)
        setattr(路由, 欄位, 暫時值)
        return lambda: setattr(路由, 欄位, 原值)

    _include期間ABA(monkeypatch, 突變)
    with pytest.raises(ValueError, match=f"^{路由設定錯誤訊息}$"):
        建立應用程式(發布介面相依項((路由器,), ()))


@pytest.mark.parametrize("生命週期種類", ["startup", "shutdown", "lifespan"])
def test_source_router生命週期在factory_preflight拒絕且callback零(生命週期種類):
    """任何 caller-owned lifecycle 都在 app/factory callback 前 fail closed。"""
    次數 = 0

    async def callback():
        nonlocal 次數
        次數 += 1

    @asynccontextmanager
    async def lifespan(_):
        nonlocal 次數
        次數 += 1
        yield

    參數 = {"lifespan": lifespan} if 生命週期種類 == "lifespan" else {f"on_{生命週期種類}": [callback]}
    路由器 = APIRouter(prefix="/api/auth", **參數)
    路由器.add_api_route("/probe", lambda: None, methods=["GET"])
    工廠次數 = 0

    async def 工廠():
        nonlocal 工廠次數
        工廠次數 += 1

    with pytest.raises(ValueError, match=f"^{路由設定錯誤訊息}$"):
        建立應用程式(發布介面相依項((路由器,), (工廠,)))
    assert 次數 == 工廠次數 == 0


def test_include期間caller_router生命週期突變不能進final(monkeypatch):
    """include 只接觸 module-owned sanitized router；caller mutation 不發布 callback。"""
    callback次數 = 0
    事件 = []
    來源 = APIRouter(prefix="/api/auth")
    來源.add_api_route("/probe", lambda: {"ok": True}, methods=["GET"])
    原始include = FastAPI.include_router

    async def caller_callback():
        nonlocal callback次數
        callback次數 += 1

    def 包裝(self, supplied_router, *args, **kwargs):
        assert supplied_router is not 來源
        來源.on_startup.append(caller_callback)
        return 原始include(self, supplied_router, *args, **kwargs)

    async def 工廠():
        事件.append("resource-start")
        return _資源(事件)

    monkeypatch.setattr(FastAPI, "include_router", 包裝)
    應用程式 = 建立應用程式(發布介面相依項((來源,), (工廠,)))
    with TestClient(應用程式) as 客戶端:
        assert 客戶端.get("/api/auth/probe").json() == {"ok": True}
    assert callback次數 == 0
    assert 事件 == ["resource-start", "resource-close"]


def test_普通dependency_response_model序列化與OpenAPI皆保留():
    """正向路徑執行依賴、response serialization 並發布 model schema。"""
    相依次數 = 0

    def 驗證身分():
        nonlocal 相依次數
        相依次數 += 1

    路由器 = _回應路由器(dependencies=[Depends(驗證身分)])
    應用程式 = 建立應用程式(發布介面相依項((路由器,), ()))
    with TestClient(應用程式) as 客戶端:
        回應 = 客戶端.get("/api/auth/probe")
        規格 = 客戶端.get("/openapi.json").json()
    assert 回應.status_code == 200 and 回應.json() == {"public": "shown"}
    assert 相依次數 == 1
    操作 = 規格["paths"]["/api/auth/probe"]["get"]
    assert 操作["responses"]["200"]["content"]["application/json"]["schema"]
