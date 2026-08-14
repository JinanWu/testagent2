"""Admin-only 完整呼叫紀錄 HTTP adapter。"""

from __future__ import annotations

import math
import re
import secrets
import time
import inspect
from typing import Annotated, Any, Literal, Protocol, cast

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request, Response
from fastapi.responses import JSONResponse
from pydantic import AfterValidator, BeforeValidator, ConfigDict, Field, WithJsonSchema, create_model
from starlette.concurrency import run_in_threadpool

from ..治理.管理查詢契約 import (
    ADMIN_INVOCATION_DETAIL_PATH,
    ADMIN_INVOCATION_ERROR_CONTRACT,
    ADMIN_INVOCATION_FORBIDDEN_QUERY_KEYS,
    ADMIN_INVOCATION_LIST_PATH,
    ADMIN_INVOCATION_QUERY_KEYS,
    管理員呼叫不存在錯誤,
    管理員呼叫完整詳情,
    管理員呼叫投影頁,
    管理員呼叫查詢條件,
    管理員呼叫查詢錯誤,
    管理員呼叫游標編解碼器,
    管理員呼叫稽核錯誤,
    管理員拒絕稽核收據權威,
)
from ..網頁工作階段 import 網頁使用者
from ..治理.遮蔽 import 驗證遮蔽公開原因, 驗證遮蔽公開路徑

_控制流程 = (KeyboardInterrupt, SystemExit, GeneratorExit)
_識別碼格式 = r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$"
_遮蔽路徑格式 = r"^(?:$|(?:/(?![^/]{257})(?:[^~/]|~[01]){0,256}){1,16})$"
_遮蔽原因Schema格式 = (
    r"^(?![\u0009-\u000d\u001c-\u001f\u0020\u0085\u00a0\u1680\u2000-\u200a"
    r"\u2028\u2029\u202f\u205f\u3000\ufeff]*$)"
    r"(?![\s\S]*(?:[Bb][Ee][Aa][Rr][Ee][Rr]|(?:[Ss][Kk]|[Pp][Kk])[_-]"
    r"|(?:^|[^0-9A-Fa-f])[0-9A-Fa-f]{64}(?:$|[^0-9A-Fa-f])))[\s\S]{1,256}$"
)
_遮蔽路徑回應 = Annotated[
    str,
    AfterValidator(驗證遮蔽公開路徑),
    WithJsonSchema({"type": "string", "maxLength": 4096, "pattern": _遮蔽路徑格式}),
]
_遮蔽原因回應 = Annotated[
    str,
    AfterValidator(驗證遮蔽公開原因),
    WithJsonSchema({"type": "string", "minLength": 1, "maxLength": 256,
                    "pattern": _遮蔽原因Schema格式}),
]


def _驗證遮蔽回應識別碼(值: str, /) -> str:
    """驗證並返回與frontend一致的有界canonical識別碼。"""
    if type(值) is not str or re.fullmatch(_識別碼格式, 值) is None:
        raise ValueError
    return 值


def _驗證遮蔽回應時間(值: float, /) -> float:
    """驗證並返回finite且非負的遮蔽時間。"""
    if type(值) not in (int, float) or not math.isfinite(值) or 值 < 0:
        raise ValueError
    return 值


_遮蔽識別碼回應 = Annotated[
    str, BeforeValidator(_驗證遮蔽回應識別碼),
    WithJsonSchema({"type": "string", "pattern": _識別碼格式, "maxLength": 128}),
]
_遮蔽時間回應 = Annotated[
    float, BeforeValidator(_驗證遮蔽回應時間),
    WithJsonSchema({"type": "number", "minimum": 0}),
]


class 管理員安全列表提供者(Protocol):
    """安全metadata投影的最小介面。"""

    def 列出管理員安全呼叫(self, 條件: 管理員呼叫查詢條件, 位置, /) -> 管理員呼叫投影頁:
        """依exact query scope回傳安全投影頁。"""
        ...


class 管理員已稽核詳情提供者(Protocol):
    """audit-before-read detail gate的最小介面。"""

    def 查詢管理員原始資料(self, *參數) -> 管理員呼叫完整詳情:
        """以位置參數執行權威admin與稽核閘門。"""
        ...


管理員呼叫列表項目回應 = create_model(
    "AdminInvocationListItem", __config__=ConfigDict(extra="forbid"),
    **{名稱: 定義 for 名稱, 定義 in {
        "invocation_id": (str, ...), "endpoint_id": (str, ...), "endpoint_version_id": (str, ...),
        "request_id": (str, ...), "status": (str, ...), "error_code": (str | None, ...),
        "latency_ms": (float | None, ...), "created_at": (float, ...),
        "completed_at": (float | None, ...), "has_redactions": (bool, ...),
    }.items()},
)
管理員呼叫列表回應 = create_model(
    "AdminInvocationList", __config__=ConfigDict(extra="forbid"),
    **{"items": (list[管理員呼叫列表項目回應], ...), "next_cursor": (str | None, ...)},
)
def _代碼錯誤文件(代碼: str) -> dict[str, object]:
    """建立只允許exact code的inline OpenAPI response。"""
    return {"content": {"application/json": {"schema": {
        "type": "object", "additionalProperties": False, "required": ["detail"],
        "properties": {"detail": {"type": "object", "additionalProperties": False,
            "required": ["code"], "properties": {"code": {"type": "string", "enum": [代碼]}}}},
    }}}}


def _訊息錯誤文件(訊息: str) -> dict[str, object]:
    """建立只允許exact message的inline OpenAPI response。"""
    return {"content": {"application/json": {"schema": {
        "type": "object", "additionalProperties": False, "required": ["detail"],
        "properties": {"detail": {"type": "string", "enum": [訊息]}},
    }}}}


未授權錯誤文件 = _訊息錯誤文件("需要登入")
認證不可用錯誤文件 = _訊息錯誤文件("呼叫紀錄暫時不可取得")
驗證錯誤文件 = _代碼錯誤文件("invalid_request")
禁止錯誤文件 = _訊息錯誤文件("只有管理者可查看完整呼叫紀錄")
不存在錯誤文件 = _訊息錯誤文件("找不到呼叫紀錄")
查詢錯誤文件 = _訊息錯誤文件("呼叫紀錄不可取得")
稽核錯誤文件 = _訊息錯誤文件("呼叫紀錄暫時不可取得")
詳情不可用錯誤文件 = 稽核錯誤文件
管理員呼叫識別回應 = create_model(
    "AdminInvocationIdentity", __config__=ConfigDict(extra="forbid"),
    識別碼=(str, Field(alias="id")), 請求識別碼=(str, Field(alias="request_id")),
    工作階段識別碼=(str | None, Field(alias="session_id")),
)
管理員執行事件回應 = create_model(
    "AdminRunEvent", __config__=ConfigDict(extra="forbid"),
    識別碼=(str, Field(alias="id")), 序號=(int, Field(alias="sequence_number")),
    事件類型=(str, Field(alias="event_type")), 內容=(Any, Field(alias="payload")),
    建立時間=(float, Field(alias="created_at")),
)
管理員工具呼叫回應 = create_model(
    "AdminToolCall", __config__=ConfigDict(extra="forbid"),
    識別碼=(str, Field(alias="id")), 執行事件識別碼=(str | None, Field(alias="run_event_id")),
    序號=(int, Field(alias="sequence_number")), 工具名稱=(str, Field(alias="tool_name")),
    參數=(Any, Field(alias="arguments")), 結果狀態=(str, Field(alias="outcome")),
    結果=(Any, Field(alias="result")), 錯誤=(Any, Field(alias="error")),
    延遲毫秒=(float | None, Field(alias="latency_ms")),
    重試來源識別碼=(str | None, Field(alias="retry_of_tool_call_id")),
    建立時間=(float, Field(alias="created_at")),
)
管理員遮蔽回應 = create_model(
    "AdminRedaction", __config__=ConfigDict(extra="forbid"),
    **{名稱: 定義 for 名稱, 定義 in {
        "id": (_遮蔽識別碼回應, ...),
        "target_type": (Literal["invocation_input", "metadata", "output", "error", "run_event",
                                "tool_arguments", "tool_result", "tool_error"], ...),
        "target_row_id": (_遮蔽識別碼回應, ...),
        "json_path": (_遮蔽路徑回應, ...),
        "reason": (_遮蔽原因回應, ...),
        "is_tombstone": (Literal[True], ...), "redacted_at": (_遮蔽時間回應, ...),
    }.items()},
)
管理員呼叫詳情回應 = create_model(
    "AdminInvocationDetail", __config__=ConfigDict(extra="forbid"),
    **{名稱: 定義 for 名稱, 定義 in {
        "invocation": (管理員呼叫識別回應, ...), "endpoint_id": (str, ...),
        "endpoint_version_id": (str, ...), "credential_id": (str | None, ...),
        "message_id": (str | None, ...), "status": (str, ...), "input": (Any, ...),
        "metadata": (dict[str, Any] | None, ...), "output": (Any, ...), "error": (Any, ...),
        "usage": (Any, ...), "metadata_size_bytes": (int | None, ...), "metadata_sha256": (str | None, ...),
        "latency_ms": (float | None, ...), "pricing_version": (str | None, ...),
        "created_at": (float, ...), "completed_at": (float | None, ...),
        "run_events": (list[管理員執行事件回應], ...), "tool_calls": (list[管理員工具呼叫回應], ...),
        "redactions": (list[管理員遮蔽回應], ...),
    }.items()},
)


def 建立管理稽核路由器(
    列表提供者: 管理員安全列表提供者,
    詳情提供者: 管理員已稽核詳情提供者,
    游標編解碼器: 管理員呼叫游標編解碼器,
    目前工作階段相依,
    *,
    拒絕收據權威: 管理員拒絕稽核收據權威 | None = None,
    時鐘=time.time,
    請求識別碼工廠=lambda: "request-" + secrets.token_hex(16),
    稽核事件識別碼工廠=lambda: "audit-" + secrets.token_hex(16),
) -> APIRouter:
    """建立兩條canonical-session保護的Admin GET routes。

    參數：列表與詳情提供者實作A18-01 seam；游標編解碼器持有server authority；目前工作階段相依
    必須是canonical Web session dependency；三個工廠只產生server-owned資料。
    返回：只含list/detail GET且無export/download的``APIRouter``。
    例外：建構參數違反契約時固定``ValueError``；route failure映射固定HTTP錯誤。
    副作用：建構不執行I/O；provider只在request threadpool內呼叫。
    """
    if (not callable(目前工作階段相依) or not callable(時鐘)
            or not callable(請求識別碼工廠) or not callable(稽核事件識別碼工廠)
            or type(游標編解碼器) is not 管理員呼叫游標編解碼器
            or (拒絕收據權威 is not None
                and type(拒絕收據權威) is not 管理員拒絕稽核收據權威)):
        raise ValueError("管理稽核路由設定無效") from None
    try:
        相依參數數 = len(inspect.signature(目前工作階段相依).parameters)
    except (TypeError, ValueError):
        raise ValueError("管理稽核路由設定無效") from None
    if 相依參數數 not in (0, 2):
        raise ValueError("管理稽核路由設定無效") from None

    def 取得安全工作階段(請求: Request, 回應: Response) -> 網頁使用者:
        """呼叫canonical session seam並阻止未列入契約的HTTP錯誤穿透。"""
        try:
            if 相依參數數 == 0:
                return 目前工作階段相依()
            return 目前工作階段相依(請求, 回應)
        except HTTPException as 錯誤:
            if 錯誤.status_code == 401:
                _拋出固定錯誤(401)
            if 錯誤.status_code == 503:
                _拋出固定錯誤(503)
            _拋出固定錯誤(500)
        except _控制流程:
            raise
        except BaseException:
            _拋出固定錯誤(500)

    setattr(取得安全工作階段, "__canonical_dependency__", 目前工作階段相依)
    路由器 = APIRouter(prefix="/api/admin")
    列表路徑 = ADMIN_INVOCATION_LIST_PATH.removeprefix("/api/admin")
    詳情路徑 = ADMIN_INVOCATION_DETAIL_PATH.removeprefix("/api/admin")

    @路由器.get(
        列表路徑, operation_id="admin_list_endpoint_invocations", response_model=管理員呼叫列表回應,
        responses={401: 未授權錯誤文件, 403: 禁止錯誤文件, 422: 驗證錯誤文件,
                   500: 查詢錯誤文件, 503: 認證不可用錯誤文件},
    )
    async def 列出管理員呼叫(
        請求: Request,
        端點識別碼: Annotated[str, Path(alias="endpoint_id")],
        使用者: 網頁使用者 = Depends(取得安全工作階段),
        起始文件: Annotated[str | None, Query(alias="from_at", json_schema_extra={"type": "number", "minimum": 0})] = None,
        結束文件: Annotated[str | None, Query(alias="to_at", json_schema_extra={"type": "number", "minimum": 0})] = None,
        狀態文件: Annotated[str | None, Query(alias="status")] = None,
        錯誤碼文件: Annotated[str | None, Query(alias="error_code")] = None,
        數量文件: Annotated[str | None, Query(alias="limit", json_schema_extra={"type": "integer", "minimum": 1, "maximum": 100, "default": 50})] = None,
        游標文件: Annotated[str | None, Query(alias="cursor")] = None,
    ) -> JSONResponse:
        """驗證Admin與strict query後只讀安全metadata投影。"""
        del 起始文件, 結束文件, 狀態文件, 錯誤碼文件, 數量文件, 游標文件
        _確認管理員(使用者)
        _確認路徑識別碼(端點識別碼)
        條件, 游標 = _解析列表查詢(請求, 端點識別碼, 游標編解碼器)
        try:
            頁 = await run_in_threadpool(列表提供者.列出管理員安全呼叫, 條件, 游標)
            if type(頁) is not 管理員呼叫投影頁:
                raise ValueError
            下一頁 = None if 頁.下一頁位置 is None else 游標編解碼器.編碼(條件, 頁.下一頁位置)
            return JSONResponse({"items": [_序列化列表項目(項目) for 項目 in 頁.項目], "next_cursor": 下一頁})
        except _控制流程:
            raise
        except BaseException:
            _拋出固定錯誤(500)

    @路由器.get(
        詳情路徑, operation_id="admin_get_endpoint_invocation", response_model=管理員呼叫詳情回應,
        responses={401: 未授權錯誤文件, 403: 禁止錯誤文件, 404: 不存在錯誤文件,
                   422: 驗證錯誤文件, 500: 查詢錯誤文件, 503: 詳情不可用錯誤文件},
    )
    async def 取得管理員呼叫詳情(
        請求: Request,
        端點識別碼: Annotated[str, Path(alias="endpoint_id")],
        呼叫識別碼: Annotated[str, Path(alias="invocation_id")],
        使用者: 網頁使用者 = Depends(取得安全工作階段),
    ) -> Any:
        """只允許Admin，並把server-owned audit資料傳入A18-01 gate。"""
        安全使用者 = _確認工作階段使用者(使用者)
        _確認路徑識別碼(端點識別碼)
        _確認路徑識別碼(呼叫識別碼)
        if 請求.url.query:
            _拋出固定錯誤(422)
        請求識別碼: object = None
        事件識別碼: object = None
        發生時間: object = None
        try:
            請求識別碼 = 請求識別碼工廠()
            事件識別碼 = 稽核事件識別碼工廠()
            發生時間 = 時鐘()
            if not all(type(值) is str and 1 <= len(值) <= 128 for 值 in (請求識別碼, 事件識別碼)):
                raise ValueError
            if type(發生時間) not in (int, float) or not math.isfinite(發生時間) or 發生時間 < 0:
                raise ValueError
        except _控制流程:
            raise
        except BaseException:
            _拋出固定錯誤(500)
        安全請求識別碼 = cast(str, 請求識別碼)
        安全事件識別碼 = cast(str, 事件識別碼)
        安全發生時間 = cast(int | float, 發生時間)
        if 安全使用者.角色 != "admin":
            拒絕狀態 = 500
            try:
                拒絕結果 = await run_in_threadpool(
                    詳情提供者.查詢管理員原始資料,
                    False, 安全使用者.識別碼, 安全請求識別碼, 安全事件識別碼, 安全發生時間,
                    端點識別碼, 呼叫識別碼,
                )
                if (type(拒絕收據權威) is 管理員拒絕稽核收據權威
                        and 拒絕收據權威.驗證(
                            拒絕結果, 安全使用者.識別碼, 安全請求識別碼,
                            安全事件識別碼, 安全發生時間, 端點識別碼, 呼叫識別碼,
                        )):
                    拒絕狀態 = 403
            except 管理員呼叫稽核錯誤:
                拒絕狀態 = 503
            except _控制流程:
                raise
            except BaseException:
                拒絕狀態 = 500
            _拋出固定錯誤(拒絕狀態)
        try:
            詳情 = await run_in_threadpool(
                詳情提供者.查詢管理員原始資料,
                True, 安全使用者.識別碼, 安全請求識別碼, 安全事件識別碼, 安全發生時間,
                端點識別碼, 呼叫識別碼,
            )
            if type(詳情) is not 管理員呼叫完整詳情:
                raise ValueError
            return 詳情.建立JSON()
        except _控制流程:
            raise
        except 管理員呼叫不存在錯誤:
            _拋出固定錯誤(404)
        except 管理員呼叫稽核錯誤:
            _拋出固定錯誤(503)
        except 管理員呼叫查詢錯誤:
            _拋出固定錯誤(500)
        except BaseException:
            _拋出固定錯誤(500)

    @路由器.get(列表路徑 + "/", include_in_schema=False)
    async def 拒絕列表尾斜線() -> None:
        """Exact path contract不允許框架以307正規化尾斜線。"""
        raise HTTPException(status_code=404, detail=ADMIN_INVOCATION_ERROR_CONTRACT[404]) from None

    @路由器.get(詳情路徑 + "/", include_in_schema=False)
    async def 拒絕詳情尾斜線(
        端點識別碼: Annotated[str, Path(alias="endpoint_id")],
        呼叫識別碼: Annotated[str, Path(alias="invocation_id")],
    ) -> None:
        """Exact detail path contract不允許框架以307正規化尾斜線。"""
        del 端點識別碼, 呼叫識別碼
        raise HTTPException(status_code=404, detail=ADMIN_INVOCATION_ERROR_CONTRACT[404]) from None

    return 路由器


def _確認管理員(使用者: object) -> str:
    """由canonical dependency回傳值重建exact Admin principal。"""
    安全 = _確認工作階段使用者(使用者)
    if 安全.角色 != "admin":
        _拋出固定錯誤(403)
    return 安全.識別碼


def _確認工作階段使用者(使用者: object) -> 網頁使用者:
    """只接受canonical dependency產生的exact、可重建authenticated principal。"""
    if type(使用者) is not 網頁使用者:
        _拋出固定錯誤(403)
    安全 = None
    try:
        安全 = 網頁使用者(使用者.識別碼, 使用者.使用者名稱, 使用者.角色)
    except BaseException:
        _拋出固定錯誤(403)
    if type(安全) is not 網頁使用者:
        _拋出固定錯誤(403)
    return cast(網頁使用者, 安全)


def _確認路徑識別碼(值: object) -> str:
    """Route-owned exact validator；固定422且不回顯拒絕值。"""
    if type(值) is not str or re.fullmatch(_識別碼格式, 值) is None:
        _拋出固定錯誤(422)
    return cast(str, 值)


def _解析列表查詢(請求: Request, 端點識別碼: str, 編解碼器: 管理員呼叫游標編解碼器):
    """拒絕unknown/duplicate query並建立exact scope與驗簽位置。"""
    配對 = list(請求.query_params.multi_items())
    鍵 = [名稱 for 名稱, _ in 配對]
    if (any(名稱 not in ADMIN_INVOCATION_QUERY_KEYS for 名稱 in 鍵)
            or any(名稱 in ADMIN_INVOCATION_FORBIDDEN_QUERY_KEYS for 名稱 in 鍵)
            or len(鍵) != len(set(鍵))):
        _拋出固定錯誤(422)
    值 = dict(配對)
    try:
        起始 = None if "from_at" not in 值 else float(值["from_at"])
        結束 = None if "to_at" not in 值 else float(值["to_at"])
        數量 = 50 if "limit" not in 值 else int(值["limit"])
        條件 = 管理員呼叫查詢條件(
            端點識別碼, 起始, 結束, 值.get("status"), 值.get("error_code"), 數量,
        )
        位置 = None if "cursor" not in 值 else 編解碼器.解碼(值["cursor"], 條件)
        return 條件, 位置
    except _控制流程:
        raise
    except BaseException:
        _拋出固定錯誤(422)


def _序列化列表項目(項目) -> dict[str, object]:
    """只輸出A18-01 safe-list allowlist。"""
    return {
        "invocation_id": 項目.呼叫識別碼, "endpoint_id": 項目.端點識別碼,
        "endpoint_version_id": 項目.端點版本識別碼, "request_id": 項目.請求識別碼,
        "status": 項目.狀態, "error_code": 項目.錯誤碼, "latency_ms": 項目.延遲毫秒,
        "created_at": 項目.建立時間, "completed_at": 項目.完成時間,
        "has_redactions": 項目.是否有遮蔽,
    }


def _拋出固定錯誤(狀態碼: int):
    """清除內部例外鏈並回傳固定public detail。"""
    訊息 = ADMIN_INVOCATION_ERROR_CONTRACT[狀態碼]
    詳情內容: object = {"code": "invalid_request"} if 狀態碼 == 422 else 訊息
    raise HTTPException(status_code=狀態碼, detail=詳情內容) from None
