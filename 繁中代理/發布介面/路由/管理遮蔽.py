"""Admin-only irreversible redaction HTTP adapter。"""
from __future__ import annotations

import asyncio
import re
from typing import Any, Literal, NoReturn, cast

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictFloat, StrictStr, field_validator
from starlette.concurrency import run_in_threadpool
from starlette.responses import JSONResponse

from ..嚴格JSON import 解析嚴格JSON
from ..治理.管理遮蔽治理 import (
    管理遮蔽不存在,
    管理遮蔽內部失敗,
    管理遮蔽冪等衝突,
    管理遮蔽成功,
    管理遮蔽授權,
    管理遮蔽治理權限,
    管理遮蔽目標衝突,
    管理遮蔽請求,
    管理遮蔽收據,
)
from ..治理.遮蔽 import 驗證遮蔽公開欄位
from ..設定 import 網頁CSRFHeader名稱

_控制流程 = (asyncio.CancelledError, KeyboardInterrupt, SystemExit, GeneratorExit)
_本文位元上限 = 16_384
_識別碼格式 = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
_目標類型 = Literal[
    "invocation_input", "metadata", "output", "error", "run_event",
    "tool_arguments", "tool_result", "tool_error",
]


class 管理遮蔽HTTP請求(BaseModel):
    """只含caller可選target/path/reason的strict request DTO。"""

    model_config = ConfigDict(extra="forbid", strict=True)
    target_type: _目標類型
    target_row_id: StrictStr = Field(min_length=1, max_length=128, pattern=_識別碼格式.pattern)
    json_path: StrictStr = Field(max_length=4096)
    reason: StrictStr = Field(min_length=1, max_length=256)

    @field_validator("reason")
    @classmethod
    def 驗證原因位元(cls, 值: str) -> str:
        正規值 = 值.strip()
        if not 正規值 or len(正規值.encode("utf-8")) > 256:
            raise ValueError
        return 正規值

    @field_validator("json_path")
    @classmethod
    def 驗證路徑(cls, 值: str) -> str:
        驗證遮蔽公開欄位("invocation_input", 值, "privacy")
        return 值


class 管理遮蔽Actor回應(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    type: Literal["admin"]
    id: StrictStr = Field(min_length=1, max_length=128, pattern=_識別碼格式.pattern)


class 管理遮蔽成功回應(BaseModel):
    """完整且不含原文的immutable receipt transport shape。"""

    model_config = ConfigDict(extra="forbid", strict=True)
    redaction_id: StrictStr = Field(min_length=1, max_length=128, pattern=_識別碼格式.pattern)
    invocation_id: StrictStr = Field(min_length=1, max_length=128, pattern=_識別碼格式.pattern)
    target_type: _目標類型
    target_row_id: StrictStr = Field(min_length=1, max_length=128, pattern=_識別碼格式.pattern)
    json_path: StrictStr = Field(max_length=4096)
    original_sha256: StrictStr = Field(min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$")
    reason: StrictStr = Field(min_length=1, max_length=256)
    actor: 管理遮蔽Actor回應
    audit_event_id: StrictStr = Field(min_length=1, max_length=128, pattern=_識別碼格式.pattern)
    is_tombstone: StrictBool
    redacted_at: StrictFloat = Field(ge=0, allow_inf_nan=False)

    @field_validator("is_tombstone")
    @classmethod
    def 驗證墓碑(cls, 值: bool) -> bool:
        if 值 is not True:
            raise ValueError
        return 值

    @field_validator("reason")
    @classmethod
    def 驗證公開原因(cls, 值: str) -> str:
        驗證遮蔽公開欄位("invocation_input", "", 值)
        if 值 != 值.strip() or len(值.encode("utf-8")) > 256:
            raise ValueError
        return 值

    @field_validator("json_path")
    @classmethod
    def 驗證公開路徑(cls, 值: str) -> str:
        驗證遮蔽公開欄位("invocation_input", 值, "privacy")
        return 值


_錯誤代碼 = {
    400: ("invalid_request",),
    401: ("unauthorized",),
    403: ("admin_required", "csrf_invalid"),
    404: ("invocation_not_found",),
    409: ("idempotency_conflict", "redaction_conflict"),
    422: ("redaction_validation_failed",),
    500: ("redaction_failed",),
    503: ("auth_unavailable",),
}


def _錯誤文件(狀態碼: int) -> dict[str, object]:
    return {
        "description": "/".join(_錯誤代碼[狀態碼]),
        "content": {"application/json": {"schema": {
            "type": "object", "additionalProperties": False, "required": ["detail"],
            "properties": {"detail": {
                "type": "object", "additionalProperties": False, "required": ["code"],
                "properties": {"code": {"type": "string", "enum": list(_錯誤代碼[狀態碼])}},
            }},
        }}},
    }


_本文OpenAPI = {
    "parameters": [{
        "name": "Idempotency-Key", "in": "header", "required": True,
        "schema": {"type": "string", "minLength": 1, "maxLength": 128,
                   "pattern": _識別碼格式.pattern},
    }],
    "requestBody": {
        "required": True,
        "content": {"application/json": {"schema": 管理遮蔽HTTP請求.model_json_schema()}},
    },
}


def 建立管理遮蔽路由器(治理權限: 管理遮蔽治理權限) -> APIRouter:
    """建立只接受exact deep governance authority的Admin redaction POST。"""
    if type(治理權限) is not 管理遮蔽治理權限:
        raise ValueError("管理遮蔽路由設定無效") from None

    路由器 = APIRouter(prefix="/api/admin")

    @路由器.post(
        "/published-endpoints/{endpoint_id}/invocations/{invocation_id}/redactions",
        operation_id="admin_redact_endpoint_invocation",
        response_model=管理遮蔽成功回應,
        responses={狀態: _錯誤文件(狀態) for 狀態 in _錯誤代碼},
        openapi_extra=_本文OpenAPI,
    )
    async def 遮蔽管理員呼叫資料(
        請求: Request,
        endpoint_id: str,
        invocation_id: str,
        回應: Response,
        授權: 管理遮蔽授權 = Depends(治理權限.授權相依項),
    ) -> JSONResponse:
        """authority-first後驗transport，並只映射sealed governance outcomes。"""
        管理員識別碼 = 授權.使用者.識別碼
        if 請求.url.query:
            _拋出固定錯誤(400, "invalid_request", 回應)
        端點識別碼 = _驗證識別碼(endpoint_id, 422, 回應)
        呼叫識別碼 = _驗證識別碼(invocation_id, 422, 回應)
        冪等鍵 = _讀取原始標頭(請求, 回應)
        本文 = await _解析本文(請求, 回應)
        try:
            驗證遮蔽公開欄位(本文.target_type, 本文.json_path, 本文.reason)
        except _控制流程:
            raise
        except BaseException:
            _拋出固定錯誤(422, "redaction_validation_failed", 回應)
        治理請求 = 管理遮蔽請求(
            管理員識別碼, 冪等鍵, 端點識別碼, 呼叫識別碼,
            本文.target_type, 本文.target_row_id, 本文.json_path, 本文.reason,
        )
        try:
            結果 = await run_in_threadpool(治理權限.執行, 治理請求)
        except _控制流程:
            raise
        except BaseException:
            _拋出固定錯誤(500, "redaction_failed", 回應)
        if type(結果) is 管理遮蔽不存在:
            _拋出固定錯誤(404, "invocation_not_found", 回應)
        if type(結果) is 管理遮蔽冪等衝突:
            _拋出固定錯誤(409, "idempotency_conflict", 回應)
        if type(結果) is 管理遮蔽目標衝突:
            _拋出固定錯誤(409, "redaction_conflict", 回應)
        if type(結果) is 管理遮蔽內部失敗 or type(結果) is not 管理遮蔽成功:
            _拋出固定錯誤(500, "redaction_failed", 回應)
        try:
            公開收據 = _重建收據(
                結果.收據, 管理員識別碼, 呼叫識別碼,
                本文.target_type, 本文.target_row_id, 本文.json_path, 本文.reason,
            )
            目標 = JSONResponse(status_code=200, content=公開收據)
            return _傳遞CSRF接續(回應, 目標)
        except _控制流程:
            raise
        except HTTPException:
            raise
        except BaseException:
            _拋出固定錯誤(500, "redaction_failed", 回應)

    return 路由器


def _驗證識別碼(值: object, 狀態碼: int, 回應: Response, 代碼: str = "redaction_validation_failed") -> str:
    if type(值) is not str or _識別碼格式.fullmatch(值) is None:
        _拋出固定錯誤(狀態碼, 代碼, 回應)
    return cast(str, 值)


def _原始標頭值(請求: Request, 名稱: bytes) -> list[bytes]:
    return [值 for 鍵, 值 in 請求.scope.get("headers", ()) if 鍵.lower() == 名稱]


def _讀取原始標頭(請求: Request, 回應: Response) -> str:
    """在body前拒絕重複representation、framing與idempotency headers。"""
    content_types = _原始標頭值(請求, b"content-type")
    content_lengths = _原始標頭值(請求, b"content-length")
    keys = _原始標頭值(請求, b"idempotency-key")
    if len(content_types) != 1 or content_types[0] != b"application/json":
        _拋出固定錯誤(400, "invalid_request", 回應)
    if len(content_lengths) > 1 or len(keys) != 1:
        _拋出固定錯誤(400, "invalid_request", 回應)
    if content_lengths:
        raw = content_lengths[0]
        if (
            not raw
            or len(raw) > len(str(_本文位元上限))
            or not raw.isascii()
            or not raw.isdigit()
            or int(raw) > _本文位元上限
        ):
            _拋出固定錯誤(400, "invalid_request", 回應)
    try:
        key = keys[0].decode("ascii")
    except UnicodeDecodeError:
        _拋出固定錯誤(400, "invalid_request", 回應)
    return _驗證識別碼(key, 400, 回應, "invalid_request")


async def _解析本文(請求: Request, 回應: Response) -> 管理遮蔽HTTP請求:
    try:
        片段清單: list[bytes] = []
        長度 = 0
        async for 片段 in 請求.stream():
            長度 += len(片段)
            if 長度 > _本文位元上限:
                raise ValueError
            片段清單.append(片段)
        原始值: Any = 解析嚴格JSON(b"".join(片段清單).decode("utf-8"))
    except _控制流程:
        raise
    except BaseException:
        _拋出固定錯誤(400, "invalid_request", 回應)
    try:
        return 管理遮蔽HTTP請求.model_validate(原始值, strict=True)
    except _控制流程:
        raise
    except BaseException:
        _拋出固定錯誤(422, "redaction_validation_failed", 回應)


def _重建收據(
    收據: 管理遮蔽收據,
    管理員識別碼: str,
    呼叫識別碼: str,
    目標類型: str,
    目標列識別碼: str,
    JSON路徑: str,
    原因: str,
) -> dict[str, object]:
    """只從exact immutable governance receipt重建HTTP response。"""
    if type(收據) is not 管理遮蔽收據 or (
        收據.呼叫識別碼 != 呼叫識別碼
        or 收據.目標類型 != 目標類型
        or 收據.目標列識別碼 != 目標列識別碼
        or 收據.JSON路徑 != JSON路徑
        or 收據.原因 != 原因
        or 收據.管理員識別碼 != 管理員識別碼
    ):
        raise ValueError
    候選 = {
        "redaction_id": 收據.遮蔽識別碼,
        "invocation_id": 收據.呼叫識別碼,
        "target_type": 收據.目標類型,
        "target_row_id": 收據.目標列識別碼,
        "json_path": 收據.JSON路徑,
        "original_sha256": 收據.原值SHA256,
        "reason": 收據.原因,
        "actor": {"type": "admin", "id": 收據.管理員識別碼},
        "audit_event_id": 收據.稽核事件識別碼,
        "is_tombstone": 收據.是墓碑,
        "redacted_at": 收據.遮蔽時間,
    }
    已驗證 = 管理遮蔽成功回應.model_validate(候選, strict=True)
    return cast(dict[str, object], 已驗證.model_dump(mode="json"))


def _傳遞CSRF接續(來源: Response, 目標: JSONResponse) -> JSONResponse:
    接續 = 來源.headers.get(網頁CSRFHeader名稱)
    if 接續 is not None:
        目標.headers[網頁CSRFHeader名稱] = 接續
    for 值 in 來源.headers.getlist("set-cookie"):
        目標.headers.append("set-cookie", 值)
    return 目標


def _拋出固定錯誤(狀態碼: int, 代碼: str, 來源: Response) -> NoReturn:
    標頭: dict[str, str] = {}
    接續 = 來源.headers.get(網頁CSRFHeader名稱)
    if 接續 is not None:
        標頭[網頁CSRFHeader名稱] = 接續
    cookies = 來源.headers.getlist("set-cookie")
    if cookies:
        標頭["set-cookie"] = cookies[-1]
    raise HTTPException(狀態碼, {"code": 代碼}, headers=標頭 or None) from None
