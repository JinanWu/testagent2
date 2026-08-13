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
def _建立錯誤綱要(*代碼: str) -> dict[str, object]:
    """描述：建立只允許指定public error codes的OpenAPI response綱要。
    參數：``代碼``為該status允許的exact code集合。
    返回值：不允許額外欄位且code使用enum的response綱要。
    """
    return {
        "description": "固定錯誤代碼",
        "content": {"application/json": {"schema": {
            "type": "object", "additionalProperties": False, "required": ["detail"],
            "properties": {"detail": {"type": "object", "additionalProperties": False,
                "required": ["code"], "properties": {"code": {"type": "string", "enum": list(代碼)}}}},
        }}},
    }


_未認證錯誤綱要 = _建立錯誤綱要("unauthorized")
_CSRF錯誤綱要 = _建立錯誤綱要("csrf_invalid")
_找不到錯誤綱要 = _建立錯誤綱要("credential_not_found")
_衝突錯誤綱要 = _建立錯誤綱要("endpoint_status_conflict")
_無效請求錯誤綱要 = _建立錯誤綱要("invalid_request")
_管理失敗錯誤綱要 = _建立錯誤綱要("credential_management_failed")
_摘要綱要 = {
    "type": "object", "additionalProperties": False,
    "required": [
        "credential_id", "name", "purpose", "key_prefix", "key_last4", "status",
        "expires_at", "last_used_at", "created_at", "revoked_at", "ip_allowlist",
        "rate_limit_requests",
    ],
    "properties": {
        "credential_id": {"type": "string"}, "name": {"type": "string"},
        "purpose": {"type": "string"}, "key_prefix": {"type": "string"},
        "key_last4": {"type": "string"},
        "status": {"type": "string", "enum": ["active", "inactive", "expired", "revoked"]},
        "expires_at": {"type": "number"}, "last_used_at": {"anyOf": [{"type": "number"}, {"type": "null"}]},
        "created_at": {"type": "number"}, "revoked_at": {"anyOf": [{"type": "number"}, {"type": "null"}]},
        "ip_allowlist": {"type": "array", "items": {"type": "string"}},
        "rate_limit_requests": {"type": "integer"},
    },
}
_列表回應 = {"description": "安全憑證清單", "content": {"application/json": {"schema": {
    "type": "object", "additionalProperties": False, "required": ["items"],
    "properties": {"items": {"type": "array", "items": _摘要綱要}},
}}}}
_建立回應綱要 = {**_摘要綱要, "required": [*_摘要綱要["required"], "initial_api_key"],
    "properties": {**_摘要綱要["properties"], "initial_api_key": {"type": "string"}}}
_建立回應 = {"description": "一次性建立收據", "content": {"application/json": {"schema": _建立回應綱要}}}
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
    """拒絕額外欄位與型別轉換的 create request。

    描述：拒絕額外欄位與型別轉換的 create request。
    參數：建構資料由類別欄位或建構器簽章明確提供，不讀取隱含輸入。
    返回值：可供呼叫端使用的``憑證建立HTTP請求``類型或實例。
    """

    model_config = ConfigDict(extra="forbid", strict=True, populate_by_name=False)

    名稱: Annotated[StrictStr, Field(alias="name", min_length=1, max_length=256)]
    用途: Annotated[StrictStr, Field(alias="purpose", min_length=1, max_length=2048)]
    到期時間: Annotated[StrictFloat | StrictInt, Field(alias="expires_at", ge=0)]
    IP允許清單: Annotated[list[StrictStr], Field(alias="ip_allowlist", max_length=256)]
    速率限制請求數: Annotated[StrictInt, Field(alias="rate_limit_requests", ge=1, le=10_000)]

    @field_validator("名稱", "用途")
    @classmethod
    def 驗證安全文字(cls, 值: str) -> str:
        """拒絕空白、控制字元與疑似秘密文字。

        描述：拒絕空白、控制字元與疑似秘密文字。
        參數：``值``。
        返回值：通過格式與秘密標記檢查的原始文字。

        """
        小寫 = 值.lower()
        if 值 != 值.strip() or any(ord(字元) < 32 for 字元 in 值) or any(
            標記 in 小寫 for 標記 in ("pk_", "sk_", "sk-", "bearer")
        ) or (len(值) == 64 and all(字元 in "0123456789abcdef" for 字元 in 小寫)):
            raise ValueError("文字無效")
        return 值

    @field_validator("到期時間")
    @classmethod
    def 驗證有限時間(cls, 值: float | int) -> float | int:
        """拒絕非有限時間。

        描述：拒絕非有限時間。
        參數：``值``。
        返回值：通過有限值檢查的原始整數或浮點時間。

        """
        if not math.isfinite(float(值)):
            raise ValueError("時間無效")
        return 值


def 建立憑證管理路由器(
    服務: 憑證管理服務,
    目前工作階段相依,
    CSRF相依,
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
        responses={200: _列表回應, 401: _未認證錯誤綱要, 404: _找不到錯誤綱要,
                   422: _無效請求錯誤綱要, 500: _管理失敗錯誤綱要},
    )
    async def 列出端點憑證(
        請求: Request,
        端點識別碼: Annotated[str, Path(alias="endpoint_id")],
        使用者: 網頁使用者 = Depends(目前工作階段相依),
    ) -> JSONResponse:
        """列出權威 session owner 的 safe credential summaries。

        描述：列出權威 session owner 的 safe credential summaries。
        參數：``請求``、``端點識別碼``、``使用者``。
        返回值：狀態碼200且本文只含安全憑證摘要列表的JSON回應。

        """
        端點識別碼 = _驗證識別碼(端點識別碼)
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
        responses={201: _建立回應, 401: _未認證錯誤綱要, 403: _CSRF錯誤綱要,
                   404: _找不到錯誤綱要, 409: _衝突錯誤綱要,
                   422: _無效請求錯誤綱要, 500: _管理失敗錯誤綱要},
        openapi_extra=_建立本文綱要,
    )
    async def 建立端點憑證(
        請求: Request,
        端點識別碼: Annotated[str, Path(alias="endpoint_id")],
        使用者: 網頁使用者 = Depends(目前工作階段相依),
        _csrf使用者: 網頁使用者 = Depends(CSRF相依),
        回應: Response = None,
    ) -> JSONResponse:
        """建立 additional credential 並只在 durable success 回傳一次明文。

        描述：建立 additional credential 並只在 durable success 回傳一次明文。
        參數：``請求``、``端點識別碼``、``使用者``、``_csrf使用者``、``回應``。
        返回值：狀態碼201、只揭露一次初始金鑰並攜帶CSRF接續的JSON回應。

        """
        端點識別碼 = _驗證識別碼(端點識別碼, 回應)
        _拒絕查詢參數(請求, 回應)
        本文 = await _解析建立本文(請求, 回應)
        使用者識別碼, _ = _重建雙重身份(使用者, _csrf使用者)
        現在 = _讀取時間(時鐘)
        if float(本文.到期時間) <= 現在:
            _拋出HTTP錯誤(422, "invalid_request", 回應)
        try:
            命令 = 憑證建立命令(
                本文.名稱, 本文.用途, float(本文.到期時間), tuple(本文.IP允許清單),
                本文.速率限制請求數,
            )
        except _控制例外:
            raise
        except BaseException:
            _拋出HTTP錯誤(422, "invalid_request", 回應)
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
            _拋出HTTP錯誤(404, "credential_not_found", 回應)
        except 端點生命週期衝突錯誤:
            _拋出HTTP錯誤(409, "endpoint_status_conflict", 回應)
        except (ValueError, 憑證管理操作錯誤):
            _拋出HTTP錯誤(500, "credential_management_failed", 回應)
        except BaseException:
            _拋出HTTP錯誤(500, "credential_management_failed", 回應)

    @路由器.post(
        "/{endpoint_id}/credentials/{credential_id}/revoke", status_code=204,
        responses={204: {"description": "撤銷成功，無回應本文"}, 401: _未認證錯誤綱要,
                   403: _CSRF錯誤綱要, 404: _找不到錯誤綱要,
                   422: _無效請求錯誤綱要, 500: _管理失敗錯誤綱要},
    )
    async def 撤銷端點憑證(
        請求: Request,
        端點識別碼: Annotated[str, Path(alias="endpoint_id")],
        憑證識別碼: Annotated[str, Path(alias="credential_id")],
        使用者: 網頁使用者 = Depends(目前工作階段相依),
        _csrf使用者: 網頁使用者 = Depends(CSRF相依),
        回應: Response = None,
    ) -> Response:
        """以 composite scope 執行可稽核且 idempotent 的撤銷。

        描述：以 composite scope 執行可稽核且 idempotent 的撤銷。
        參數：``請求``、``端點識別碼``、``憑證識別碼``、``使用者``、``_csrf使用者``、``回應``。
        返回值：狀態碼204、無本文且攜帶CSRF接續的回應。

        """
        端點識別碼 = _驗證識別碼(端點識別碼, 回應)
        憑證識別碼 = _驗證識別碼(憑證識別碼, 回應)
        _拒絕查詢參數(請求, 回應)
        await _要求空本文(請求, 回應)
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
            _拋出HTTP錯誤(404, "credential_not_found", 回應)
        except BaseException:
            _拋出HTTP錯誤(500, "credential_management_failed", 回應)

    return 路由器


def _重建身份(使用者: object) -> tuple[str, bool]:
    """重建權威 Web principal；role 只供既有 admin revoke capability。

    描述：重建權威 Web principal；role 只供既有 admin revoke capability。
    參數：``使用者``。
    返回值：權威使用者識別碼及是否具管理者角色的二元組。

    """
    if type(使用者) is not 網頁使用者:
        _拋出HTTP錯誤(500, "credential_management_failed")
    識別碼 = object.__getattribute__(使用者, "識別碼")
    角色 = object.__getattribute__(使用者, "角色")
    if type(識別碼) is not str or not 1 <= len(識別碼) <= 128 or type(角色) is not str:
        _拋出HTTP錯誤(500, "credential_management_failed")
    return 識別碼, 角色 == "admin"


def _重建雙重身份(使用者: object, csrf使用者: object) -> tuple[str, bool]:
    """要求 session 與 single-use CSRF 回傳同一 authoritative principal。

    描述：要求 session 與 single-use CSRF 回傳同一 authoritative principal。
    參數：``使用者``、``csrf使用者``。
    返回值：兩個身份一致時的權威使用者識別碼及管理者旗標。

    """
    識別碼, 是否管理者 = _重建身份(使用者)
    csrf識別碼, csrf是否管理者 = _重建身份(csrf使用者)
    if 識別碼 != csrf識別碼 or 是否管理者 != csrf是否管理者:
        _拋出HTTP錯誤(500, "credential_management_failed")
    return 識別碼, 是否管理者


def _驗證識別碼(值: object, 回應: Response | None = None) -> str:
    """描述：在handler內固定path格式錯誤，避免框架回顯輸入。
    參數：``值``為path原值；``回應``攜帶CSRF接續。
    返回值：格式合法的識別碼。
    """
    import re
    if type(值) is not str or re.fullmatch(_識別碼格式, 值) is None:
        _拋出HTTP錯誤(422, "invalid_request", 回應)
    return 值


def _拒絕查詢參數(請求: Request, 回應: Response | None = None) -> None:
    """三條管理路由皆拒絕任何 query 或 duplicate query。

    描述：三條管理路由皆拒絕任何 query 或 duplicate query。
    參數：``請求``。
    返回值：無；查詢字串為空時完成檢查，否則拋出固定HTTP錯誤。

    """
    if 請求.url.query:
        _拋出HTTP錯誤(422, "invalid_request", 回應)


async def _解析建立本文(請求: Request, 回應: Response | None = None) -> 憑證建立HTTP請求:
    """以 strict JSON、exact content type 與 32 KiB 上限解析 create body。

    描述：以 strict JSON、exact content type 與 32 KiB 上限解析 create body。
    參數：``請求``。
    返回值：通過本文大小、媒體型別、嚴格JSON及欄位驗證的建立請求。

    """
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
        _拋出HTTP錯誤(422, "invalid_request", 回應)


async def _要求空本文(請求: Request, 回應: Response | None) -> None:
    """描述：逐段驗證request stream byte-exact empty。
    參數：``請求``提供ASGI stream；``回應``攜帶CSRF接續。
    返回值：本文確實為空時回傳None。
    """
    try:
        async for 片段 in 請求.stream():
            if 片段:
                _拋出HTTP錯誤(422, "invalid_request", 回應)
    except _控制例外:
        raise
    except HTTPException:
        raise
    except BaseException:
        _拋出HTTP錯誤(422, "invalid_request", 回應)


def _讀取時間(時鐘) -> float:
    """只接受 authoritative clock 的有限非負數值。

    描述：只接受 authoritative clock 的有限非負數值。
    參數：``時鐘``。
    返回值：權威時鐘產生的有限非負浮點秒數。

    """
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
    """把 canonical CSRF dependency 的 successor header/cookie 移到實際回應。

    描述：把 canonical CSRF dependency 的 successor header/cookie 移到實際回應。
    參數：``來源``、``目標``。
    返回值：已複製CSRF successor header及cookie的目標回應；無來源時原樣回傳目標。

    """
    if 來源 is None:
        return 目標
    接續 = 來源.headers.get(網頁CSRFHeader名稱)
    if 接續 is not None:
        目標.headers[網頁CSRFHeader名稱] = 接續
    for 鍵, 值 in 來源.headers.raw:
        if 鍵.lower() == b"set-cookie":
            目標.headers.append(鍵.decode("latin-1"), 值.decode("latin-1"))
    return 目標


def _拋出HTTP錯誤(狀態碼: int, 代碼: str, 來源: Response | None = None) -> NoReturn:
    """建立固定錯誤，並保留已輪替的 CSRF successor header/cookie。

    描述：建立固定錯誤，並保留已輪替的 CSRF successor header/cookie。
    參數：``狀態碼``、``代碼``、``來源``。
    返回值：不返回；固定拋出帶公開錯誤代碼及可用CSRF接續的HTTP例外。

    """
    標頭: dict[str, str] = {}
    if 來源 is not None:
        接續 = 來源.headers.get(網頁CSRFHeader名稱)
        if 接續 is not None:
            標頭[網頁CSRFHeader名稱] = 接續
        Cookie清單 = 來源.headers.getlist("set-cookie")
        if Cookie清單:
            標頭["set-cookie"] = Cookie清單[-1]
    raise HTTPException(
        status_code=狀態碼, detail={"code": 代碼}, headers=標頭 or None,
    ) from None
