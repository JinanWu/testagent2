"""A01 路由相依與回應政策的 callback-free 擷取及重建。"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from fastapi.dependencies.models import Dependant
from fastapi.params import Depends, Security
from fastapi.routing import APIRoute

from .安全形狀 import 形狀相同, 擷取形狀, 重建形狀
from .設定 import 路由設定錯誤訊息
from .路由生命週期 import 驗證預設生命週期

_最大節點 = 4096
_路由欄位 = (
    "response_model", "status_code", "response_model_include", "response_model_exclude",
    "response_model_by_alias", "response_model_exclude_unset",
    "response_model_exclude_defaults", "response_model_exclude_none", "response_class",
    "responses", "callbacks", "tags", "summary", "description", "response_description",
    "deprecated", "include_in_schema", "name", "openapi_extra",
    "generate_unique_id_function", "operation_id",
)


def _失敗():
    """統一拒絕不安全路由政策。"""
    raise ValueError(路由設定錯誤訊息)


def _字典(物件: Any) -> dict:
    """不執行 property 地讀 exact instance dict。"""
    值 = object.__getattribute__(物件, "__dict__")
    if type(值) is not dict:
        _失敗()
    return 值


def _相依宣告(值: Any):
    """以 call identity 與 exact scalar 擷取 Depends/Security。"""
    字典 = _字典(值)
    if type(值) is Depends:
        if set(dict.keys(字典)) != {"dependency", "use_cache"}:
            _失敗()
        範圍 = None
    elif type(值) is Security:
        if set(dict.keys(字典)) != {"dependency", "use_cache", "scopes"}:
            _失敗()
        原範圍 = dict.__getitem__(字典, "scopes")
        if type(原範圍) is not list:
            _失敗()
        範圍清單 = []
        for 項目 in 原範圍:
            if type(項目) is not str:
                _失敗()
            範圍清單.append(項目)
        範圍 = tuple(範圍清單)
    else:
        _失敗()
    快取 = dict.__getitem__(字典, "use_cache")
    if type(快取) is not bool:
        _失敗()
    return (type(值), dict.__getitem__(字典, "dependency"), 快取, 範圍)


def _重建相依宣告(描述):
    """建立 module-owned Depends/Security instance。"""
    類別, 呼叫, 快取, 範圍 = 描述
    if 類別 is Depends:
        return Depends(dependency=呼叫, use_cache=快取)
    return Security(dependency=呼叫, scopes=list(範圍), use_cache=快取)


def _相依樹(節點: Dependant, 計數: list[int]):
    """遞迴擷取 ordered Dependant tree，不比較 callable。"""
    if type(節點) is not Dependant:
        _失敗()
    計數[0] += 1
    if 計數[0] > _最大節點:
        _失敗()
    值 = _字典(節點)
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
    範圍原值 = dict.get(值, "security_scopes")
    if 範圍原值 is None:
        範圍 = None
    else:
        if type(範圍原值) is not list:
            _失敗()
        範圍清單 = []
        for 項目 in 範圍原值:
            if type(項目) is not str:
                _失敗()
            範圍清單.append(項目)
        範圍 = tuple(範圍清單)
    return (dict.get(值, "call"), 快取, *純量, 範圍, tuple(_相依樹(項目, 計數) for 項目 in 子節點))


def _相依樹相同(左, 右) -> bool:
    """以 call identity 與 exact ordered children 比較樹。"""
    if 左[0] is not 右[0] or 左[1:6] != 右[1:6] or len(左[6]) != len(右[6]):
        return False
    return all(_相依樹相同(甲, 乙) for 甲, 乙 in zip(左[6], 右[6]))


def 擷取路由器政策(路由器: APIRouter):
    """擷取 router/route Depends、Dependant tree 與完整回應序列化政策。"""
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
        for 名稱 in ("response_model_include", "response_model_exclude", "responses", "openapi_extra", "tags"):
            政策[名稱] = 擷取形狀(政策[名稱])
        樹 = _相依樹(dict.get(值, "dependant"), [0])
        結果.append((id(路由), 路徑, tuple(sorted(方法清單)), dict.get(值, "endpoint"), 全宣告[len(路由器宣告):], 樹, 政策))
    return (前綴, 路由器宣告, tuple(結果))


def _政策相同(目前, 預期) -> bool:
    """比較 response policy，identity 欄位永不呼叫 eq/hash。"""
    for 名稱 in _路由欄位:
        if 名稱 in ("response_model_include", "response_model_exclude", "responses", "openapi_extra", "tags"):
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
        for 名稱 in ("response_model_include", "response_model_exclude", "responses", "openapi_extra", "tags"):
            參數[名稱] = 重建形狀(參數[名稱])
        參數["dependencies"] = [_重建相依宣告(值) for 值 in 路由宣告]
        參數["methods"] = list(方法)
        安全.add_api_route(相對路徑, 端點, **參數)
    驗證預設生命週期(安全)
    return 安全


def 驗證政策(路由器: APIRouter, 預期, *, 要求路由身份: bool) -> None:
    """重播來源或驗證 sanitized/final router 的政策與依賴樹。"""
    目前 = 擷取路由器政策(路由器)
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
