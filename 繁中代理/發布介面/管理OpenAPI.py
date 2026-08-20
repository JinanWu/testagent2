"""由已驗證runtime dependency identity正規化Management OpenAPI auth契約。"""
from __future__ import annotations

from copy import deepcopy
from typing import Callable

from fastapi import FastAPI
from fastapi.routing import APIRoute

from .OpenAPI相依權限 import 讀取Canonical相依封裝
from .設定 import 網頁CSRFHeader名稱, 網頁工作階段Cookie名稱
from .治理.管理遮蔽治理 import 是管理遮蔽CSRF相依項
from .路由.網頁認證 import 是模組CSRF相依項, 是模組目前工作階段相依項

_SESSION_SCHEME = "WebSessionCookie"
_CSRF_SCHEME = "WebCSRFHeader"
_CSRF_PARAMETER = {
    "name": 網頁CSRFHeader名稱,
    "in": "header",
    "required": True,
    "schema": {"type": "string", "minLength": 32, "maxLength": 512},
}
_SUCCESSOR_HEADER = {
    "description": "Successor single-use CSRF token when rotated",
    "schema": {"type": "string", "minLength": 32, "maxLength": 512},
}


def _代碼錯誤回應(代碼: str) -> dict[str, object]:
    """建立canonical dependency固定detail.code response schema。"""
    return {"description": 代碼, "content": {"application/json": {"schema": {
        "type": "object", "additionalProperties": False, "required": ["detail"],
        "properties": {"detail": {
            "type": "object", "additionalProperties": False, "required": ["code"],
            "properties": {"code": {"type": "string", "enum": [代碼]}},
        }},
    }}}}


def _確保代碼錯誤回應(回應們: dict, 狀態: str, 代碼: str) -> None:
    """只在route未提供實質schema時補canonical dependency response。"""
    目前 = 回應們.get(狀態)
    schema = 目前.get("content", {}).get("application/json", {}).get("schema") if type(目前) is dict else None
    if type(schema) is not dict or not schema:
        回應們[狀態] = _代碼錯誤回應(代碼)


def _展開相依呼叫(路由: APIRoute) -> tuple[object, ...]:
    待查 = [路由.dependant]
    呼叫們 = []
    while 待查:
        節點 = 待查.pop()
        呼叫們.append(節點.call)
        待查.extend(節點.dependencies)
    return tuple(呼叫們)


def _是目前工作階段或封裝(呼叫: object) -> bool:
    已訪 = set()
    目前 = 呼叫
    while 目前 is not None and id(目前) not in 已訪:
        已訪.add(id(目前))
        if 是模組目前工作階段相依項(目前):
            return True
        目前 = 讀取Canonical相依封裝(目前)
    return False


def _讀取權限(路由: APIRoute) -> tuple[bool, bool]:
    有工作階段 = False
    有CSRF = False
    for 呼叫 in _展開相依呼叫(路由):
        if 是管理遮蔽CSRF相依項(呼叫):
            有工作階段 = 有CSRF = True
        elif 是模組CSRF相依項(呼叫):
            有工作階段 = 有CSRF = True
        elif _是目前工作階段或封裝(呼叫):
            有工作階段 = True
    return 有工作階段, 有CSRF


def _是管理路由(路由: APIRoute) -> bool:
    """Management scope只排除公開／auth；authority仍完全由dependency identity判定。"""
    return (
        路由.include_in_schema
        and not 路由.path.startswith("/api/auth")
        and (路由.path.startswith("/api/published-endpoints") or 路由.path.startswith("/api/admin"))
    )


def _補接續文件(操作: dict[str, object], *, 有CSRF: bool) -> None:
    def 收集列舉(值: object) -> set[str]:
        if type(值) is dict:
            結果 = {
                項目 for 項目 in 值.get("enum", []) if type(項目) is str
            } if type(值.get("enum")) is list else set()
            for 子值 in 值.values():
                結果.update(收集列舉(子值))
            return 結果
        if type(值) is list:
            結果: set[str] = set()
            for 子值 in 值:
                結果.update(收集列舉(子值))
            return 結果
        return set()

    回應們 = 操作.get("responses")
    if type(回應們) is not dict:
        return
    for 狀態, 回應 in 回應們.items():
        if type(回應) is not dict:
            continue
        if 有CSRF:
            if 狀態 == "401":
                continue
            代碼們 = 收集列舉(回應)
            if 狀態 == "403" and not (代碼們 - {"csrf_invalid", "admin_required"}):
                continue
            if 狀態 == "503" and not (代碼們 - {"auth_unavailable"}):
                continue
        elif not str(狀態).startswith("2"):
            continue
        標頭們 = 回應.setdefault("headers", {})
        if type(標頭們) is dict:
            successor = deepcopy(_SUCCESSOR_HEADER)
            if 有CSRF and 狀態 == "403":
                successor["description"] = (
                    "Optional successor after successful CSRF consumption; present for "
                    "post-consumption authorization failures such as planning_not_authorized "
                    "and absent for pre-consumption csrf_invalid."
                )
            if 有CSRF and 狀態 == "503":
                successor["description"] = (
                    "Optional successor after successful CSRF consumption; present for "
                    "post-consumption failures such as planner_unavailable and absent for "
                    "pre-consumption auth_unavailable."
                )
            標頭們.setdefault(網頁CSRFHeader名稱, successor)


def 套用ManagementOpenAPI(應用程式: FastAPI) -> None:
    """安裝純schema post-processor；不變更runtime route、dependency或response。"""
    原始OpenAPI: Callable[[], dict] = 應用程式.openapi
    路由索引 = {
        (方法.lower(), 路由.path): 路由
        for 路由 in 應用程式.routes if isinstance(路由, APIRoute) and _是管理路由(路由)
        for 方法 in 路由.methods
    }
    快取: dict | None = None

    def 建立OpenAPI() -> dict:
        nonlocal 快取
        if 快取 is not None:
            return 快取
        綱要 = 原始OpenAPI()
        有保護操作 = False
        for 路徑, 路徑項目 in 綱要.get("paths", {}).items():
            if type(路徑項目) is not dict:
                continue
            for 方法, 操作 in 路徑項目.items():
                路由 = 路由索引.get((方法, 路徑))
                if 路由 is None or type(操作) is not dict:
                    continue
                有工作階段, 有CSRF = _讀取權限(路由)
                if not 有工作階段:
                    continue
                有保護操作 = True
                操作["security"] = [{_SESSION_SCHEME: [], **({_CSRF_SCHEME: []} if 有CSRF else {})}]
                呼叫們 = _展開相依呼叫(路由)
                有直接工作階段 = any(是模組目前工作階段相依項(呼叫) for 呼叫 in 呼叫們)
                有直接CSRF = any(是模組CSRF相依項(呼叫) for 呼叫 in 呼叫們)
                回應們 = 操作.setdefault("responses", {})
                if type(回應們) is dict and 有直接工作階段:
                    _確保代碼錯誤回應(回應們, "401", "unauthorized")
                    _確保代碼錯誤回應(回應們, "503", "auth_unavailable")
                if type(回應們) is dict and 有直接CSRF:
                    _確保代碼錯誤回應(回應們, "403", "csrf_invalid")
                if 有CSRF:
                    參數們 = 操作.setdefault("parameters", [])
                    if type(參數們) is list and not any(
                        type(項) is dict and 項.get("in") == "header" and 項.get("name") == 網頁CSRFHeader名稱
                        for 項 in 參數們
                    ):
                        參數們.append(deepcopy(_CSRF_PARAMETER))
                _補接續文件(操作, 有CSRF=有CSRF)
        if 有保護操作:
            元件 = 綱要.setdefault("components", {})
            安全綱要 = 元件.setdefault("securitySchemes", {})
            安全綱要[_SESSION_SCHEME] = {
                "type": "apiKey", "in": "cookie", "name": 網頁工作階段Cookie名稱,
            }
            安全綱要[_CSRF_SCHEME] = {
                "type": "apiKey", "in": "header", "name": 網頁CSRFHeader名稱,
            }
        快取 = 綱要
        return 綱要

    應用程式.openapi = 建立OpenAPI


__all__ = ("套用ManagementOpenAPI",)
