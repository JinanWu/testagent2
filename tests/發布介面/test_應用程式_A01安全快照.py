"""A01 FastAPI 版本形狀與巢狀政策安全快照回歸。"""

import pytest
from fastapi import APIRouter, Depends, FastAPI, Security
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient
from pydantic import BaseModel

from 繁中代理.發布介面.相依項 import 發布介面相依項
from 繁中代理.發布介面.應用程式 import 建立應用程式
from 繁中代理.發布介面.設定 import 路由設定錯誤訊息
from 繁中代理.發布介面.路由政策 import (
    _判定框架形狀,
    _快取鍵,
    _新Depends鍵,
    _新Security鍵,
    _新節點鍵,
    _框架形狀,
    _舊Depends鍵,
    _舊Security鍵,
    _舊節點鍵,
)


class _輸出(BaseModel):
    """驗證 class 與 list[class] response model identity。"""

    value: str


class _敵意葉:
    """不安全政策葉的 callback 計數器。"""

    def __init__(self):
        self.hash次數 = self.eq次數 = self.getattr次數 = 0

    def __hash__(self):
        self.hash次數 += 1
        raise AssertionError("不得 hash")

    def __eq__(self, other):
        self.eq次數 += 1
        raise AssertionError("不得 eq")

    def __getattr__(self, name):
        self.getattr次數 += 1
        raise AssertionError("不得 getattr")


class _敵意字串(str):
    """不得在 exact str gate 前比較。"""

    def __new__(cls, value):
        結果 = super().__new__(cls, value)
        結果.eq次數 = 0
        return 結果

    def __eq__(self, other):
        self.eq次數 += 1
        raise AssertionError("不得 eq")


class _敵意集合:
    """框架形狀 oracle 不得比較 arbitrary container。"""

    def __init__(self):
        self.eq次數 = 0

    def __eq__(self, other):
        self.eq次數 += 1
        raise AssertionError("不得 eq")


def _單一路由(**參數):
    """建立可注入政策的 canonical router。"""
    路由器 = APIRouter(prefix="/api/auth")
    路由器.add_api_route("/probe", lambda: {"value": "ok"}, methods=["GET"], **參數)
    return 路由器


def _include期間ABA(monkeypatch, 突變):
    """在 sanitized route include 期間暫時突變，再還原來源。"""
    原始 = FastAPI.include_router

    def 包裝(self, supplied_router, *args, **kwargs):
        還原 = 突變(supplied_router.routes[0])
        try:
            return 原始(self, supplied_router, *args, **kwargs)
        finally:
            還原()

    monkeypatch.setattr(FastAPI, "include_router", 包裝)


def test_相容性oracle只接受兩個已審核exact_shapes():
    """plain frozenset oracle 同時鎖定 0.115.6 與 0.139.2。"""
    assert _判定框架形狀(_舊Depends鍵, _舊Security鍵, _舊節點鍵) == "舊"
    assert _判定框架形狀(_新Depends鍵, _新Security鍵, _新節點鍵) == "新"
    assert _框架形狀 in ("舊", "新")
    with pytest.raises(ValueError, match=f"^{路由設定錯誤訊息}$"):
        _判定框架形狀(_舊Depends鍵 | {"unknown"}, _舊Security鍵, _舊節點鍵)


def test_相容性oracle拒絕arbitrary_container且eq零():
    """exact built-in gate 必須先於任何集合比較。"""
    敵意 = _敵意集合()
    with pytest.raises(ValueError, match=f"^{路由設定錯誤訊息}$"):
        _判定框架形狀(敵意, _舊Security鍵, _舊節點鍵)
    assert 敵意.eq次數 == 0


def test_新版scope_metadata先做exact_str_gate且eq零():
    """衍生 cache scope 不得先用 membership 觸發 hostile equality。"""
    呼叫 = object()
    敵意 = _敵意字串("request")
    with pytest.raises(ValueError, match=f"^{路由設定錯誤訊息}$"):
        _快取鍵((呼叫, (), 敵意), 呼叫, True)
    assert 敵意.eq次數 == 0


def test_generator_dependency_real_TestClient_cleanup_scope正常():
    """ordinary yield dependency 維持 request cleanup 語意。"""
    事件 = []

    def 相依():
        事件.append("enter")
        try:
            yield
        finally:
            事件.append("exit")

    應用程式 = 建立應用程式(發布介面相依項((_單一路由(dependencies=[Depends(相依)]),), ()))
    with TestClient(應用程式) as 客戶端:
        assert 客戶端.get("/api/auth/probe").json() == {"value": "ok"}
    assert 事件 == ["enter", "exit"]


def test_Security_OAuth_scope_include_ABA拒絕(monkeypatch):
    """Security scopes 暫時降權即使 restore 仍由 final graph metadata 拒絕。"""
    def 驗證():
        return None

    路由器 = _單一路由(dependencies=[Security(驗證, scopes=["admin"])])

    def 突變(路由):
        宣告 = 路由.dependencies[0]
        原值 = 宣告.scopes
        宣告.scopes = []
        return lambda: setattr(宣告, "scopes", 原值)

    _include期間ABA(monkeypatch, 突變)
    with pytest.raises(ValueError, match=f"^{路由設定錯誤訊息}$"):
        建立應用程式(發布介面相依項((路由器,), ()))


def test_yield_scope_include_ABA在新FastAPI拒絕(monkeypatch):
    """0.139 的 function/request scope 亦被 declaration 與 final graph 雙重鎖定。"""
    if _框架形狀 != "新":
        pytest.skip("FastAPI 0.115 沒有 dependency scope")

    def 相依():
        yield

    路由器 = _單一路由(dependencies=[Depends(相依, scope="request")])

    def 突變(路由):
        宣告 = 路由.dependencies[0]
        原值 = 宣告.scope
        宣告.scope = "function"
        return lambda: setattr(宣告, "scope", 原值)

    _include期間ABA(monkeypatch, 突變)
    with pytest.raises(ValueError, match=f"^{路由設定錯誤訊息}$"):
        建立應用程式(發布介面相依項((路由器,), ()))


def test_nested_caller政策先脫離且persistent_mutation由replay拒絕(monkeypatch):
    """sanitized copy 不別名 caller builtins，來源持續突變仍 fail closed。"""
    responses = {418: {"description": "teapot", "headers": {"X-Probe": {"schema": {"type": "string"}}}}}
    路由器 = _單一路由(responses=responses, openapi_extra={"x-meta": {"items": ["a"]}})
    來源responses = 路由器.routes[0].responses
    原始 = FastAPI.include_router

    def 包裝(self, supplied_router, *args, **kwargs):
        安全路由 = supplied_router.routes[0]
        assert 安全路由.responses is not 來源responses
        assert 安全路由.responses[418] is not 來源responses[418]
        來源responses[418]["headers"]["X-Probe"]["schema"]["type"] = "integer"
        return 原始(self, supplied_router, *args, **kwargs)

    monkeypatch.setattr(FastAPI, "include_router", 包裝)
    with pytest.raises(ValueError, match=f"^{路由設定錯誤訊息}$"):
        建立應用程式(發布介面相依項((路由器,), ()))


@pytest.mark.parametrize("欄位", ["responses", "openapi_extra", "tags", "response_model_include"])
def test_custom_mutable_leaf預檢拒絕且hash_eq_getattr零(欄位):
    """四種 nested policy 都不得把 arbitrary leaf 當 identity fallback。"""
    葉 = _敵意葉()
    值 = {
        "responses": {400: {"description": 葉}},
        "openapi_extra": {"x-hostile": [葉]},
        "tags": [葉],
        "response_model_include": [葉],
    }[欄位]
    路由器 = _單一路由(**{欄位: 值})
    葉.hash次數 = 葉.eq次數 = 葉.getattr次數 = 0
    with pytest.raises(ValueError, match=f"^{路由設定錯誤訊息}$"):
        建立應用程式(發布介面相依項((路由器,), ()))
    assert 葉.hash次數 == 葉.eq次數 == 葉.getattr次數 == 0


def test_nested政策正向保留完整OpenAPI與list_model():
    """安全 responses/openapi/tags/include 仍保留 Pydantic schemas。"""
    模型 = list[_輸出]
    路由器 = _單一路由(
        response_model=模型,
        responses={404: {"model": _輸出, "description": "missing"}},
        tags=["auth"],
        openapi_extra={"x-meta": {"enabled": True, "weights": [1, 2.5]}},
    )
    應用程式 = 建立應用程式(發布介面相依項((路由器,), ()))
    路由 = next(項目 for 項目 in 應用程式.routes if isinstance(項目, APIRoute) and 項目.path.endswith("probe"))
    assert 路由.response_model is 模型
    with TestClient(應用程式) as 客戶端:
        操作 = 客戶端.get("/openapi.json").json()["paths"]["/api/auth/probe"]["get"]
    assert 操作["tags"] == ["auth"] and 操作["x-meta"]["weights"] == [1, 2.5]
    assert 操作["responses"]["404"]["content"]["application/json"]["schema"]
