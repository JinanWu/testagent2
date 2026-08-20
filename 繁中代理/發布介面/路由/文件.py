"""A23 管理 Web session 與 public API-key 兩種端點文件 GET routes。"""
from __future__ import annotations

from typing import Protocol

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, Response

from ..生產端點文件 import 文件憑證未授權, 文件服務失敗
from ..網頁工作階段 import 網頁使用者


class 端點文件服務(Protocol):
    def 讀取管理文件(self, *, 端點識別碼: str, 擁有者使用者識別碼: str, 管理者: bool) -> bytes | None: ...
    def 讀取金鑰文件(self, *, 短名: str, API金鑰: str) -> bytes: ...


_401 = {"detail": {"code": "docs_unauthorized"}}
_404 = {"detail": {"code": "endpoint_not_found"}}
_500 = {"detail": {"code": "docs_unavailable"}}
_422 = {"detail": {"code": "request_invalid"}}


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
        responses={401: {}, 404: {}, 422: {}, 500: {}},
    )
    def 讀取管理文件(endpoint_id: str, request: Request,
                principal: 網頁使用者 = Depends(目前工作階段相依)) -> Response:
        if request.url.query or not _識別合法(endpoint_id):
            return JSONResponse(_422, status_code=422)
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
                return JSONResponse(_404, status_code=404)
            return _文件回應(body)
        except (KeyboardInterrupt, SystemExit, GeneratorExit):
            raise
        except BaseException:
            return JSONResponse(_500, status_code=500)

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
    return Response(content=body, status_code=200, media_type="application/json")


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
