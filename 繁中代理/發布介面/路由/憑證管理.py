"""端點範圍的 API Key 列出、建立與撤銷 HTTP 路由。"""

from __future__ import annotations

import math
import secrets
import time
from typing import Annotated, Any, NoReturn

from fastapi import APIRouter, Depends, HTTPException, Path, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, StrictFloat, StrictInt, StrictStr, field_validator
from starlette.concurrency import run_in_threadpool

from ..嚴格JSON import 解析嚴格JSON
from ..網頁工作階段 import 網頁使用者
from ..設定 import 網頁CSRFHeader名稱
from ..憑證管理契約 import (
    一次性憑證建立收據,
    找不到端點憑證錯誤,
    憑證建立命令,
    憑證列表結果,
    憑證撤銷收據,
    憑證管理操作錯誤,
    憑證管理服務,
    端點生命週期衝突錯誤,
    序列化一次性憑證建立收據,
    序列化憑證列表,
)

_識別碼格式 = r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$"
_本文位元上限 = 32_768
_控制例外 = (KeyboardInterrupt, SystemExit, GeneratorExit)
_建立本文綱要 = {
    "requestBody": {"required": True, "content": {"application/json": {"schema": {
        "type": "object", "additionalProperties": False,
        "required": ["name", "purpose", "expires_at", "ip_allowlist", "rate_limit_requests"],
        "properties": {
            "name": {"type": "string", "minLength": 1, "maxLength": 256},
            "purpose": {"type": "string", "minLength": 1, "maxLength": 2048},
            "expires_at": {"type": "number", "minimum": 0},
            "ip_allowlist": {"type": "array", "maxItems": 256, "items": {"type": "string"}},
            "rate_limit_requests": {"type": "integer", "minimum": 1, "maximum": 10_000},
        },
    }}}},
}


class 憑證建立HTTP請求(BaseModel):
    """拒絕額外欄位與型別轉換的 create request。"""

    model_config = ConfigDict(extra="forbid", strict=True, populate_by_name=False)

    名稱: Annotated[StrictStr, Field(alias="name", min_length=1, max_length=256)]
    用途: Annotated[StrictStr, Field(alias="purpose", min_length=1, max_length=2048)]
    到期時間: Annotated[StrictFloat | StrictInt, Field(alias="expires_at", ge=0)]
    IP允許清單: Annotated[list[StrictStr], Field(alias="ip_allowlist", max_length=256)]
    速率限制請求數: Annotated[StrictInt, Field(alias="rate_limit_requests", ge=1, le=10_000)]

    @field_validator("名稱", "用途")
    @classmethod
    def 驗證安全文字(cls, 值: str) -> str:
        """拒絕空白、控制字元與疑似秘密文字。"""
        小寫 = 值.lower()
        if 值 != 值.strip() or any(ord(字元) < 32 for 字元 in 值) or any(
            標記 in 小寫 for 標記 in ("pk_", "sk_", "sk-", "bearer")
        ) or (len(值) == 64 and all(字元 in "0123456789abcdef" for 字元 in 小寫)):
            raise ValueError("文字無效")
        return 值

    @field_validator("到期時間")
    @classmethod
    def 驗證有限時間(cls, 值: float | int) -> float | int:
        """拒絕非有限時間。"""
        if not math.isfinite(float(值)):
            raise ValueError("時間無效")
        return 值


def 建立憑證管理路由器(
    服務: 憑證管理服務,
    目前工作階段相依,
    csrf相依,
    *,
    時鐘=time.time,
    請求識別碼工廠=lambda: "request-" + secrets.token_hex(16),
) -> APIRouter:
    """建立 owner-scoped list/create 與 owner/admin revoke 三條路由。

    描述：以 canonical Web session 與 mutation CSRF dependencies 保護管理操作；同步服務只在
    request threadpool 執行，所有 ordinary failure 映射成固定 public code。
    參數：``服務`` 提供三項管理能力；兩個相依項提供權威身份；``時鐘`` 與 request-id 工廠可測試替換。
    返回值：prefix 為 ``/api/published-endpoints`` 且只含三條 credential routes 的路由器。
    """
    路由器 = APIRouter(prefix="/api/published-endpoints")

    @路由器.get(
        "/{endpoint_id}/credentials",
        responses={401: {}, 404: {}, 422: {}, 500: {}},
    )
    async def 列出端點憑證(
        請求: Request,
        端點識別碼: Annotated[str, Path(alias="endpoint_id", pattern=_識別碼格式)],
        使用者: 網頁使用者 = Depends(目前工作階段相依),
    ) -> JSONResponse:
        """列出權威 session owner 的 safe credential summaries。"""
        _拒絕查詢參數(請求)
        使用者識別碼, _ = _重建身份(使用者)
        try:
            結果 = await run_in_threadpool(
                服務.列出憑證, 端點識別碼=端點識別碼,
                擁有者使用者識別碼=使用者識別碼,
            )
            if type(結果) is not 憑證列表結果:
                raise ValueError
            return JSONResponse(status_code=200, content=序列化憑證列表(結果))
        except _控制例外:
            raise
        except 找不到端點憑證錯誤:
            _拋出HTTP錯誤(404, "credential_not_found")
        except BaseException:
            _拋出HTTP錯誤(500, "credential_management_failed")

    @路由器.post(
        "/{endpoint_id}/credentials", status_code=201,
        responses={401: {}, 403: {}, 404: {}, 409: {}, 422: {}, 500: {}},
        openapi_extra=_建立本文綱要,
    )
    async def 建立端點憑證(
        請求: Request,
        端點識別碼: Annotated[str, Path(alias="endpoint_id", pattern=_識別碼格式)],
        使用者: 網頁使用者 = Depends(目前工作階段相依),
        _csrf使用者: 網頁使用者 = Depends(csrf相依),
        回應: Response = None,
    ) -> JSONResponse:
        """建立 additional credential 並只在 durable success 回傳一次明文。"""
        _拒絕查詢參數(請求)
        本文 = await _解析建立本文(請求)
        使用者識別碼, _ = _重建雙重身份(使用者, _csrf使用者)
        現在 = _讀取時間(時鐘)
        if float(本文.到期時間) <= 現在:
            _拋出HTTP錯誤(422, "invalid_request")
        try:
            命令 = 憑證建立命令(
                本文.名稱, 本文.用途, float(本文.到期時間), tuple(本文.IP允許清單),
                本文.速率限制請求數,
            )
        except _控制例外:
            raise
        except BaseException:
            _拋出HTTP錯誤(422, "invalid_request")
        try:
            結果 = await run_in_threadpool(
                服務.建立憑證, 端點識別碼=端點識別碼,
                擁有者使用者識別碼=使用者識別碼, 請求=命令,
            )
            if type(結果) is not 一次性憑證建立收據:
                raise ValueError
            目標 = JSONResponse(status_code=201, content=序列化一次性憑證建立收據(結果))
            return _傳遞CSRF接續(回應, 目標)
        except _控制例外:
            raise
        except 找不到端點憑證錯誤:
            _拋出HTTP錯誤(404, "credential_not_found")
        except 端點生命週期衝突錯誤:
            _拋出HTTP錯誤(409, "endpoint_status_conflict")
        except (ValueError, 憑證管理操作錯誤):
            _拋出HTTP錯誤(500, "credential_management_failed")
        except BaseException:
            _拋出HTTP錯誤(500, "credential_management_failed")

    @路由器.post(
        "/{endpoint_id}/credentials/{credential_id}/revoke", status_code=204,
        responses={401: {}, 403: {}, 404: {}, 422: {}, 500: {}},
    )
    async def 撤銷端點憑證(
        請求: Request,
        端點識別碼: Annotated[str, Path(alias="endpoint_id", pattern=_識別碼格式)],
        憑證識別碼: Annotated[str, Path(alias="credential_id", pattern=_識別碼格式)],
        使用者: 網頁使用者 = Depends(目前工作階段相依),
        _csrf使用者: 網頁使用者 = Depends(csrf相依),
        回應: Response = None,
    ) -> Response:
        """以 composite scope 執行可稽核且 idempotent 的撤銷。"""
        _拒絕查詢參數(請求)
        if 請求.headers.get("content-length") not in (None, "0"):
            _拋出HTTP錯誤(422, "invalid_request")
        使用者識別碼, 是否管理者 = _重建雙重身份(使用者, _csrf使用者)
        try:
            請求識別碼 = 請求識別碼工廠()
            if type(請求識別碼) is not str or not 1 <= len(請求識別碼) <= 128:
                raise ValueError
            結果 = await run_in_threadpool(
                服務.撤銷憑證, 端點識別碼=端點識別碼, 憑證識別碼=憑證識別碼,
                擁有者使用者識別碼=使用者識別碼, 是否管理者=是否管理者,
                請求識別碼=請求識別碼,
            )
            if type(結果) is not 憑證撤銷收據:
                raise ValueError
            return _傳遞CSRF接續(回應, Response(status_code=204))
        except _控制例外:
            raise
        except 找不到端點憑證錯誤:
            _拋出HTTP錯誤(404, "credential_not_found")
        except BaseException:
            _拋出HTTP錯誤(500, "credential_management_failed")

    return 路由器


def _重建身份(使用者: object) -> tuple[str, bool]:
    """重建權威 Web principal；role 只供既有 admin revoke capability。"""
    if type(使用者) is not 網頁使用者:
        _拋出HTTP錯誤(500, "credential_management_failed")
    識別碼 = object.__getattribute__(使用者, "識別碼")
    角色 = object.__getattribute__(使用者, "角色")
    if type(識別碼) is not str or not 1 <= len(識別碼) <= 128 or type(角色) is not str:
        _拋出HTTP錯誤(500, "credential_management_failed")
    return 識別碼, 角色 == "admin"


def _重建雙重身份(使用者: object, csrf使用者: object) -> tuple[str, bool]:
    """要求 session 與 single-use CSRF 回傳同一 authoritative principal。"""
    識別碼, 是否管理者 = _重建身份(使用者)
    csrf識別碼, _ = _重建身份(csrf使用者)
    if 識別碼 != csrf識別碼:
        _拋出HTTP錯誤(500, "credential_management_failed")
    return 識別碼, 是否管理者


def _拒絕查詢參數(請求: Request) -> None:
    """三條管理路由皆拒絕任何 query 或 duplicate query。"""
    if 請求.url.query:
        _拋出HTTP錯誤(422, "invalid_request")


async def _解析建立本文(請求: Request) -> 憑證建立HTTP請求:
    """以 strict JSON、exact content type 與 32 KiB 上限解析 create body。"""
    try:
        if 請求.headers.get("content-type") != "application/json":
            raise ValueError
        宣告長度 = 請求.headers.get("content-length")
        if 宣告長度 is not None and (
            not 宣告長度.isascii() or not 宣告長度.isdigit() or int(宣告長度) > _本文位元上限
        ):
            raise ValueError
        片段清單: list[bytes] = []
        長度 = 0
        async for 片段 in 請求.stream():
            長度 += len(片段)
            if 長度 > _本文位元上限:
                raise ValueError
            片段清單.append(片段)
        原始值: Any = 解析嚴格JSON(b"".join(片段清單).decode("utf-8"))
        if type(原始值) is not dict:
            raise ValueError
        return 憑證建立HTTP請求.model_validate(原始值)
    except _控制例外:
        raise
    except BaseException:
        _拋出HTTP錯誤(422, "invalid_request")


def _讀取時間(時鐘) -> float:
    """只接受 authoritative clock 的有限非負數值。"""
    try:
        值 = 時鐘()
        if type(值) not in (int, float) or not math.isfinite(float(值)) or 值 < 0:
            raise ValueError
        return float(值)
    except _控制例外:
        raise
    except BaseException:
        _拋出HTTP錯誤(500, "credential_management_failed")


def _傳遞CSRF接續(來源: Response | None, 目標: Response) -> Response:
    """把 canonical CSRF dependency 的 successor header/cookie 移到實際回應。"""
    if 來源 is None:
        return 目標
    接續 = 來源.headers.get(網頁CSRFHeader名稱)
    if 接續 is not None:
        目標.headers[網頁CSRFHeader名稱] = 接續
    for 鍵, 值 in 來源.headers.raw:
        if 鍵.lower() == b"set-cookie":
            目標.headers.append(鍵.decode("latin-1"), 值.decode("latin-1"))
    return 目標


def _拋出HTTP錯誤(狀態碼: int, 代碼: str) -> NoReturn:
    """只建立固定 detail code，不保留原始例外或秘密資料。"""
    raise HTTPException(status_code=狀態碼, detail={"code": 代碼}) from None
