"""A23 管理 Web session 與 public API-key 兩種端點文件 GET routes。"""
from __future__ import annotations

import json
from typing import Any, Protocol

from fastapi import APIRouter, Depends, Request, Response as 注入回應
from fastapi.responses import JSONResponse, Response

from ..設定 import 網頁CSRFHeader名稱
from ..端點文件 import 端點文件投影, 渲染端點文件
from ..生產端點文件 import 文件憑證未授權, 文件服務失敗
from ..網頁工作階段 import 網頁使用者


class 端點文件服務(Protocol):
    def 讀取管理文件(self, *, 端點識別碼: str, 擁有者使用者識別碼: str, 管理者: bool) -> bytes | None: ...
    def 讀取金鑰文件(self, *, 短名: str, API金鑰: str) -> bytes: ...


_401 = {"detail": {"code": "docs_unauthorized"}}
_404 = {"detail": {"code": "endpoint_not_found"}}
_500 = {"detail": {"code": "docs_unavailable"}}
_422 = {"detail": {"code": "request_invalid"}}


def _代碼錯誤文件(代碼: str) -> dict[str, Any]:
    return {"content": {"application/json": {"schema": {
        "type": "object", "additionalProperties": False, "required": ["detail"],
        "properties": {"detail": {
            "type": "object", "additionalProperties": False, "required": ["code"],
            "properties": {"code": {"type": "string", "enum": [代碼]}},
        }},
    }}}}


_固定文件模板 = json.loads(渲染端點文件(端點文件投影(
    端點識別碼="template", 短名="template", 版本=1, 狀態="active",
    輸入綱要={}, 回應綱要={}, 端點請求上限=1, 端點窗口秒數=1,
)))
_文件錯誤列舉 = _固定文件模板["errors"]
_SESSION_ID綱要 = _固定文件模板["request_schema"]["properties"]["session_id"]
_METADATA綱要 = _固定文件模板["request_schema"]["properties"]["metadata"]
_CURL範例 = _固定文件模板["examples"]["curl"]
_PYTHON範例 = _固定文件模板["examples"]["python"]
_文件綱要 = {
    "type": "object", "additionalProperties": False,
    "required": ["endpoint", "invoke_url", "authentication", "request_schema", "response_schema", "rate_limit", "examples", "errors"],
    "properties": {
        "endpoint": {
            "type": "object", "additionalProperties": False,
            "required": ["id", "slug", "version", "status"],
            "properties": {
                "id": {"type": "string", "pattern": "^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$"},
                "slug": {"type": "string", "pattern": "^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$"},
                "version": {"type": "integer", "minimum": 1, "maximum": 2147483647},
                "status": {"type": "string", "enum": ["active", "disabled", "archived"]},
            },
        },
        "invoke_url": {"const": "${BASE_URL}/v1/endpoints/${ENDPOINT_SLUG}/invoke"},
        "authentication": {
            "type": "object", "additionalProperties": False,
            "required": ["scheme", "header"],
            "properties": {"scheme": {"const": "bearer"}, "header": {"const": "Authorization"}},
        },
        "request_schema": {
            "type": "object", "additionalProperties": False,
            "required": ["type", "additionalProperties", "required", "properties"],
            "properties": {
                "type": {"const": "object"}, "additionalProperties": {"const": False},
                "required": {"const": ["input"]},
                "properties": {
                    "type": "object", "additionalProperties": False,
                    "required": ["input", "session_id", "metadata"],
                    "properties": {
                        "input": {"type": "object"},
                        "session_id": {"const": _SESSION_ID綱要},
                        "metadata": {"const": _METADATA綱要},
                    },
                },
            },
        },
        "response_schema": {"type": "object"},
        "rate_limit": {
            "type": "object", "additionalProperties": False,
            "required": ["requests", "window_seconds"],
            "properties": {
                "requests": {"type": "integer", "minimum": 1, "maximum": 10000},
                "window_seconds": {"type": "integer", "minimum": 1, "maximum": 86400},
            },
        },
        "examples": {
            "type": "object", "additionalProperties": False,
            "required": ["curl", "python"],
            "properties": {"curl": {"const": _CURL範例}, "python": {"const": _PYTHON範例}},
        },
        "errors": {"type": "array", "const": _文件錯誤列舉},
    },
}
_管理文件回應: dict[int | str, dict[str, Any]] = {
    200: {"content": {"application/json": {"schema": _文件綱要}}},
    401: _代碼錯誤文件("unauthorized"), 404: _代碼錯誤文件("endpoint_not_found"),
    422: _代碼錯誤文件("request_invalid"), 500: _代碼錯誤文件("docs_unavailable"),
    503: _代碼錯誤文件("auth_unavailable"),
}


def 建立端點文件路由器(服務: 端點文件服務, 目前工作階段相依) -> tuple[APIRouter, APIRouter]:
    """以兩個canonical prefix建立零CSRF管理docs與single-Bearer key docs routes。"""
    if not callable(getattr(服務, "讀取管理文件", None)) or not callable(getattr(服務, "讀取金鑰文件", None)):
        raise ValueError("端點文件路由無效") from None
    if not callable(目前工作階段相依):
        raise ValueError("端點文件路由無效") from None
    管理路由器 = APIRouter(prefix="/api/published-endpoints")
    金鑰路由器 = APIRouter(prefix="/v1/endpoints")

    @管理路由器.get(
        "/{endpoint_id}/docs",
        operation_id="get_published_endpoint_docs",
        responses=_管理文件回應,
    )
    def 讀取管理文件(endpoint_id: str, request: Request, response: 注入回應,
                principal: 網頁使用者 = Depends(目前工作階段相依)) -> Response:
        if request.url.query or not _識別合法(endpoint_id):
            return _傳遞工作階段接續(response, JSONResponse(_422, status_code=422))
        try:
            if type(principal) is not 網頁使用者:
                raise ValueError
            owner = object.__getattribute__(principal, "識別碼")
            role = object.__getattribute__(principal, "角色")
            if not _識別合法(owner) or role not in ("member", "admin"):
                raise ValueError
            body = 服務.讀取管理文件(
                端點識別碼=endpoint_id, 擁有者使用者識別碼=owner, 管理者=role == "admin",
            )
            if body is None:
                return _傳遞工作階段接續(response, JSONResponse(_404, status_code=404))
            return _傳遞工作階段接續(response, _文件回應(body))
        except (KeyboardInterrupt, SystemExit, GeneratorExit):
            raise
        except BaseException:
            return _傳遞工作階段接續(response, JSONResponse(_500, status_code=500))

    @金鑰路由器.get(
        "/{slug}/docs",
        operation_id="get_endpoint_docs_with_api_key",
        responses={401: {}, 500: {}},
        openapi_extra={"parameters": [{
            "name": "Authorization", "in": "header", "required": True,
            "schema": {"type": "string", "pattern": "^Bearer pk_[A-Za-z0-9_-]{43}$", "maxLength": 53},
        }]},
    )
    def 讀取金鑰文件(slug: str, request: Request) -> Response:
        api_key = _讀取單一Bearer(request)
        if api_key is None or request.url.query or not _短名合法(slug):
            return JSONResponse(_401, status_code=401)
        try:
            return _文件回應(服務.讀取金鑰文件(短名=slug, API金鑰=api_key))
        except (KeyboardInterrupt, SystemExit, GeneratorExit):
            raise
        except 文件憑證未授權:
            return JSONResponse(_401, status_code=401)
        except BaseException:
            return JSONResponse(_500, status_code=500)

    return 管理路由器, 金鑰路由器


def _文件回應(body: object) -> Response:
    if type(body) is not bytes or not body.endswith(b"\n") or len(body) > 262_144:
        raise 文件服務失敗("端點文件服務失敗") from None
    try:
        document = json.loads(body)
        if type(document) is not dict:
            raise ValueError
        endpoint = document["endpoint"]
        request_schema = document["request_schema"]
        rate_limit = document["rate_limit"]
        if type(endpoint) is not dict or type(request_schema) is not dict or type(rate_limit) is not dict:
            raise ValueError
        properties = request_schema["properties"]
        if type(properties) is not dict:
            raise ValueError
        projection = 端點文件投影(
            端點識別碼=endpoint["id"], 短名=endpoint["slug"], 版本=endpoint["version"],
            狀態=endpoint["status"], 輸入綱要=properties["input"],
            回應綱要=document["response_schema"], 端點請求上限=rate_limit["requests"],
            端點窗口秒數=rate_limit["window_seconds"],
        )
        if 渲染端點文件(projection) != body:
            raise ValueError
        return Response(content=body, status_code=200, media_type="application/json")
    except (KeyboardInterrupt, SystemExit, GeneratorExit):
        raise
    except BaseException:
        raise 文件服務失敗("端點文件服務失敗") from None


def _傳遞工作階段接續(來源: 注入回應, 目標: Response) -> Response:
    """轉貼current-session recovery寫入注入Response的successor header與cookie。"""
    接續 = 來源.headers.get(網頁CSRFHeader名稱)
    if 接續 is not None:
        目標.headers[網頁CSRFHeader名稱] = 接續
    for 鍵, 值 in 來源.headers.raw:
        if 鍵.lower() == b"set-cookie":
            目標.headers.append(鍵.decode("latin-1"), 值.decode("latin-1"))
    return 目標


def _讀取單一Bearer(request: Request) -> str | None:
    values = [value for name, value in request.scope.get("headers", ()) if name.lower() == b"authorization"]
    if len(values) != 1 or len(values[0]) != 53:
        return None
    try:
        text = values[0].decode("ascii")
    except UnicodeDecodeError:
        return None
    if not text.startswith("Bearer pk_"):
        return None
    key = text[7:]
    if len(key) != 46 or not all(ch.isalnum() or ch in "_-" for ch in key[3:]):
        return None
    return key


def _識別合法(value: object) -> bool:
    return (type(value) is str and 1 <= len(value) <= 128 and value.strip() == value
            and all(ch.isascii() and (ch.isalnum() or ch in "_.:-") for ch in value))


def _短名合法(value: object) -> bool:
    return (type(value) is str and 1 <= len(value) <= 128 and value.strip() == value
            and all(ch.isascii() and (ch.isalnum() or ch in "_-") for ch in value))


__all__ = ("端點文件服務", "建立端點文件路由器")
