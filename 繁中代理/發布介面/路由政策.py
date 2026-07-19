"""A01 路由相依與回應政策的 callback-free 擷取及重建。"""

from __future__ import annotations

import types
from typing import Any, NoReturn

from fastapi import APIRouter
from fastapi.datastructures import DefaultPlaceholder
from fastapi.dependencies.models import Dependant
from fastapi.params import Depends, Security
from fastapi.routing import APIRoute

from .安全形狀 import 形狀相同, 擷取形狀, 重建形狀
from .設定 import 路由設定錯誤訊息
from .路由生命週期 import 驗證預設生命週期

_最大節點 = 4096
_舊Depends鍵 = frozenset({"dependency", "use_cache"})
_舊Security鍵 = frozenset({"dependency", "use_cache", "scopes"})
_新Depends鍵 = frozenset({"dependency", "use_cache", "scope"})
_新Security鍵 = frozenset({"dependency", "use_cache", "scope", "scopes"})
_舊節點鍵 = frozenset({
    "path_params", "query_params", "header_params", "cookie_params", "body_params",
    "dependencies", "security_requirements", "name", "call", "request_param_name",
    "websocket_param_name", "http_connection_param_name", "response_param_name",
    "background_tasks_param_name", "security_scopes_param_name", "security_scopes",
    "use_cache", "path", "cache_key",
})
_新節點鍵 = frozenset({
    "path_params", "query_params", "header_params", "cookie_params", "body_params",
    "dependencies", "name", "call", "request_param_name", "websocket_param_name",
    "http_connection_param_name", "response_param_name", "background_tasks_param_name",
    "security_scopes_param_name", "own_oauth_scopes", "parent_oauth_scopes",
    "use_cache", "path", "scope",
})
_路由欄位 = (
    "response_model", "status_code", "response_model_include", "response_model_exclude",
    "response_model_by_alias", "response_model_exclude_unset",
    "response_model_exclude_defaults", "response_model_exclude_none", "response_class",
    "responses", "callbacks", "tags", "summary", "description", "response_description",
    "deprecated", "include_in_schema", "name", "openapi_extra",
    "generate_unique_id_function", "operation_id",
)


def _失敗() -> NoReturn:
    """統一拒絕不安全路由政策。"""
    raise ValueError(路由設定錯誤訊息)


def _可接受字串(值: Any) -> bool:
    """驗證可選 bounded exact string，不呼叫 caller 方法。"""
    return 值 is None or (type(值) is str and len(值.encode("utf-8")) <= 16384)


def _可接受回應模型(值: Any) -> bool:
    """只保留 class 或 built-in list[class] GenericAlias identity。"""
    if 值 is None or isinstance(值, type):
        return True
    if type(值) is types.GenericAlias:
        原點 = object.__getattribute__(值, "__origin__")
        引數 = object.__getattribute__(值, "__args__")
        return 原點 is list and type(引數) is tuple and len(引數) == 1 and isinstance(引數[0], type)
    return False


def _字典(物件: Any) -> dict:
    """不執行 property 地讀 exact instance dict。"""
    值 = object.__getattribute__(物件, "__dict__")
    if type(值) is not dict:
        _失敗()
    return 值


def _判定框架形狀(相依鍵, 安全鍵, 節點鍵) -> str:
    """只接受已審核的 FastAPI 0.115/0.139 exact instance shapes。"""
    if 相依鍵 == _舊Depends鍵 and 安全鍵 == _舊Security鍵 and 節點鍵 == _舊節點鍵:
        return "舊"
    if 相依鍵 == _新Depends鍵 and 安全鍵 == _新Security鍵 and 節點鍵 == _新節點鍵:
        return "新"
    _失敗()


_框架形狀 = _判定框架形狀(
    frozenset(_字典(Depends())), frozenset(_字典(Security())), frozenset(_字典(Dependant()))
)


def _範圍(值: Any):
    """將 exact list[str] 安全轉為 module tuple；None 原樣保留。"""
    if 值 is None:
        return None
    if type(值) is not list:
        _失敗()
    結果 = []
    for 項目 in 值:
        if type(項目) is not str:
            _失敗()
        結果.append(項目)
    return tuple(結果)


def _相依宣告(值: Any):
    """以 call identity 與 exact scalar 擷取 Depends/Security。"""
    字典 = _字典(值)
    類別 = type(值)
    預期鍵 = _舊Depends鍵 if (_框架形狀, 類別) == ("舊", Depends) else None
    if (_框架形狀, 類別) == ("舊", Security):
        預期鍵 = _舊Security鍵
    elif (_框架形狀, 類別) == ("新", Depends):
        預期鍵 = _新Depends鍵
    elif (_框架形狀, 類別) == ("新", Security):
        預期鍵 = _新Security鍵
    if 預期鍵 is None or frozenset(dict.keys(字典)) != 預期鍵:
        _失敗()
    快取 = dict.__getitem__(字典, "use_cache")
    if type(快取) is not bool:
        _失敗()
    scope = dict.get(字典, "scope")
    if scope not in (None, "function", "request") or (scope is not None and type(scope) is not str):
        _失敗()
    範圍 = _範圍(dict.get(字典, "scopes")) if 類別 is Security else None
    return (類別, dict.__getitem__(字典, "dependency"), 快取, scope, 範圍)


def _重建相依宣告(描述):
    """建立 module-owned Depends/Security instance。"""
    類別, 呼叫, 快取, scope, 範圍 = 描述
    if 類別 is Depends:
        if _框架形狀 == "舊":
            return Depends(dependency=呼叫, use_cache=快取)
        return Depends(dependency=呼叫, use_cache=快取, scope=scope)
    scopes = None if 範圍 is None else list(範圍)
    if _框架形狀 == "舊":
        return Security(dependency=呼叫, scopes=scopes, use_cache=快取)
    return Security(dependency=呼叫, scopes=scopes, use_cache=快取, scope=scope)


def _相依樹(節點: Dependant, 計數: list[int]):
    """遞迴擷取 ordered Dependant tree，不比較 callable。"""
    if type(節點) is not Dependant:
        _失敗()
    計數[0] += 1
    if 計數[0] > _最大節點:
        _失敗()
    值 = _字典(節點)
    預期鍵 = _舊節點鍵 if _框架形狀 == "舊" else _新節點鍵
    if frozenset(dict.keys(值)) != 預期鍵:
        _失敗()
    子節點 = dict.get(值, "dependencies")
    if type(子節點) is not list:
        _失敗()
    純量 = []
    for 名稱 in ("name", "path", "security_scopes_param_name"):
        項目 = dict.get(值, 名稱)
        if 項目 is not None and type(項目) is not str:
            _失敗()
        純量.append(項目)
    快取 = dict.get(值, "use_cache")
    if type(快取) is not bool:
        _失敗()
    if _框架形狀 == "舊":
        自有範圍, 父範圍, scope = _範圍(dict.get(值, "security_scopes")), None, None
    else:
        自有範圍 = _範圍(dict.get(值, "own_oauth_scopes"))
        父範圍 = _範圍(dict.get(值, "parent_oauth_scopes"))
        scope = dict.get(值, "scope")
        if scope not in (None, "function", "request") or (scope is not None and type(scope) is not str):
            _失敗()
    return (dict.get(值, "call"), 快取, *純量, 自有範圍, 父範圍, scope, tuple(_相依樹(項目, 計數) for 項目 in 子節點))


def _相依樹相同(左, 右) -> bool:
    """以 call identity 與 exact ordered children 比較樹。"""
    if 左[0] is not 右[0] or 左[1:8] != 右[1:8] or len(左[8]) != len(右[8]):
        return False
    return all(_相依樹相同(甲, 乙) for 甲, 乙 in zip(左[8], 右[8]))


def 擷取路由器政策(路由器: APIRouter, *, 檢查生命週期: bool = True):
    """擷取 router/route Depends、Dependant tree 與完整回應序列化政策。"""
    if 檢查生命週期:
        驗證預設生命週期(路由器)
    路由器值 = _字典(路由器)
    前綴 = dict.get(路由器值, "prefix")
    if type(前綴) is not str:
        _失敗()
    宣告原值 = dict.get(路由器值, "dependencies")
    if type(宣告原值) is not list:
        _失敗()
    路由器宣告 = tuple(_相依宣告(值) for 值 in 宣告原值)
    結果 = []
    for 路由 in dict.get(路由器值, "routes", ()):
        if type(路由) is not APIRoute:
            _失敗()
        值 = _字典(路由)
        路徑 = dict.get(值, "path")
        方法原值 = dict.get(值, "methods")
        if type(路徑) is not str or type(方法原值) not in (set, frozenset) or not 方法原值:
            _失敗()
        方法清單 = []
        for 方法 in 方法原值:
            if type(方法) is not str:
                _失敗()
            方法清單.append(方法)
        宣告清單 = dict.get(值, "dependencies")
        if type(宣告清單) is not list or len(宣告清單) < len(路由器宣告):
            _失敗()
        全宣告 = tuple(_相依宣告(項目) for 項目 in 宣告清單)
        for 索引, 描述 in enumerate(路由器宣告):
            if 全宣告[索引][0] is not 描述[0] or 全宣告[索引][1] is not 描述[1] or 全宣告[索引][2:] != 描述[2:]:
                _失敗()
        政策 = {}
        for 名稱 in _路由欄位:
            政策[名稱] = dict.get(值, 名稱)
        if type(政策["callbacks"]) is not list or 政策["callbacks"]:
            _失敗()
        if type(政策["status_code"]) is bool or (政策["status_code"] is not None and (type(政策["status_code"]) is not int or not 100 <= 政策["status_code"] <= 599)):
            _失敗()
        for 名稱 in ("response_model_by_alias", "response_model_exclude_unset", "response_model_exclude_defaults", "response_model_exclude_none", "include_in_schema"):
            if type(政策[名稱]) is not bool:
                _失敗()
        if not _可接受回應模型(政策["response_model"]):
            _失敗()
        if not isinstance(政策["response_class"], (type, DefaultPlaceholder)):
            _失敗()
        if type(政策["generate_unique_id_function"]) is not DefaultPlaceholder and not callable(政策["generate_unique_id_function"]):
            _失敗()
        for 名稱 in ("summary", "description", "response_description", "name", "operation_id"):
            if not _可接受字串(政策[名稱]):
                _失敗()
        if 政策["deprecated"] is not None and type(政策["deprecated"]) is not bool:
            _失敗()
        模式 = {
            "response_model_include": "include", "response_model_exclude": "include",
            "responses": "responses", "openapi_extra": "json", "tags": "tags",
            "callbacks": "json",
        }
        for 名稱, 欄位模式 in 模式.items():
            政策[名稱] = 擷取形狀(政策[名稱], 欄位模式)
        樹 = _相依樹(dict.get(值, "dependant"), [0])
        結果.append((id(路由), 路徑, tuple(sorted(方法清單)), dict.get(值, "endpoint"), 全宣告[len(路由器宣告):], 樹, 政策))
    return (前綴, 路由器宣告, tuple(結果))


def _政策相同(目前, 預期) -> bool:
    """比較 response policy，identity 欄位永不呼叫 eq/hash。"""
    for 名稱 in _路由欄位:
        if 名稱 in ("response_model_include", "response_model_exclude", "responses", "openapi_extra", "tags", "callbacks"):
            if not 形狀相同(目前[名稱], 預期[名稱]):
                return False
        elif 名稱 in ("response_model", "response_class", "generate_unique_id_function"):
            if 目前[名稱] is not 預期[名稱]:
                return False
        elif type(目前[名稱]) is not type(預期[名稱]) or 目前[名稱] != 預期[名稱]:
            return False
    return True


def 建立安全路由器(擷取):
    """只由 module-owned snapshot 建立 canonical empty-lifecycle router。"""
    前綴, 路由器宣告, 路由清單 = 擷取
    安全 = APIRouter(prefix=前綴, dependencies=[_重建相依宣告(值) for 值 in 路由器宣告])
    for _, 路徑, 方法, 端點, 路由宣告, _, 政策 in 路由清單:
        相對路徑 = 路徑[len(前綴):]
        參數 = {名稱: 政策[名稱] for 名稱 in _路由欄位}
        for 名稱 in ("response_model_include", "response_model_exclude", "responses", "openapi_extra", "tags", "callbacks"):
            參數[名稱] = 重建形狀(參數[名稱])
        參數["dependencies"] = [_重建相依宣告(值) for 值 in 路由宣告]
        參數["methods"] = list(方法)
        安全.add_api_route(相對路徑, 端點, **參數)
    驗證預設生命週期(安全)
    return 安全


def 驗證政策(路由器: APIRouter, 預期, *, 要求路由身份: bool, 檢查生命週期: bool = True) -> None:
    """重播來源或驗證 sanitized/final router 的政策與依賴樹。"""
    目前 = 擷取路由器政策(路由器, 檢查生命週期=檢查生命週期)
    if 目前[0] != 預期[0] or len(目前[2]) != len(預期[2]) or len(目前[1]) != len(預期[1]):
        _失敗()
    for 現宣告, 預宣告 in zip(目前[1], 預期[1]):
        if 現宣告[0] is not 預宣告[0] or 現宣告[1] is not 預宣告[1] or 現宣告[2:] != 預宣告[2:]:
            _失敗()
    for 現, 預 in zip(目前[2], 預期[2]):
        if 要求路由身份 and 現[0] != 預[0]:
            _失敗()
        if 現[1:3] != 預[1:3] or 現[3] is not 預[3] or not _相依樹相同(現[5], 預[5]) or not _政策相同(現[6], 預[6]):
            _失敗()


def 驗證最終政策(應用路由清單: list, 預期清單) -> None:
    """將 final APIRoutes 映回各 snapshot，驗完整 dependency/response parity。"""
    for 預期 in 預期清單:
        前綴, 路由器宣告, 路由描述 = 預期
        操作清單 = tuple((描述[1], 描述[2]) for 描述 in 路由描述)
        最終路由 = []
        for 路由 in 應用路由清單:
            if type(路由) is APIRoute:
                路由字典 = _字典(路由)
                方法 = dict.get(路由字典, "methods")
                if type(方法) in (set, frozenset) and (
                    dict.get(路由字典, "path"), tuple(sorted(方法))
                ) in 操作清單:
                    最終路由.append(路由)
        檢視 = APIRouter(prefix=前綴, dependencies=[_重建相依宣告(值) for 值 in 路由器宣告])
        檢視.routes.extend(最終路由)
        驗證政策(檢視, 預期, 要求路由身份=False)
