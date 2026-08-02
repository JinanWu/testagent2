"""MGT M02 草稿、原子發布與不可變版本管理路由。

參數：模組工廠接收發布服務、權威身份與跨站防護相依項。
回傳：建立具嚴格 JSON、固定錯誤與明確 OpenAPI 位元組契約的管理路由器。
例外：設定失效時傳遞註冊例外；請求失敗由各公開處理器固定映射。
副作用：匯入只定義契約；工廠註冊路由，請求處理才消耗本文並呼叫服務。
"""

from __future__ import annotations

import math
import re
import struct
import time
from dataclasses import dataclass, field
from functools import partial
from inspect import signature
from typing import Annotated, Any, Literal, Protocol, cast, get_type_hints

from fastapi import APIRouter, Depends, HTTPException, Path, Request
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute
from jsonschema import Draft202012Validator
from pydantic import BaseModel, ConfigDict, Field, JsonValue, StrictStr, field_validator
from starlette.concurrency import run_in_threadpool

from 繁中代理.使用者 import 使用者上下文
from ..網頁工作階段 import 網頁使用者
from ..嚴格JSON import 解析嚴格JSON
from ..規劃.權限協調 import 授權選擇錯誤
from ..規劃.規劃器契約 import 規劃器不可用, 規劃器輸出無效
from ..規劃.綱要 import 規劃草稿

_識別格式 = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}\Z")
_錯誤對照 = {
    "invalid": (422, "管理操作輸入無效"),
    "draft_not_found": (404, "找不到發布草稿"),
    "endpoint_not_found": (404, "找不到發布端點"),
    "forbidden": (403, "沒有發布端點管理權限"),
    "status_conflict": (409, "發布端點狀態衝突"),
    "concurrency": (409, "發布端點已由其他操作更新"),
    "internal": (500, "發布管理服務失敗"),
}
_草稿本文綱要 = {
    "requestBody": {"required": True, "content": {"application/json": {"schema": {
        "type": "object", "additionalProperties": False,
        "required": ["original_requirement_text", "selected_skills", "response_mode"],
        "properties": {
            "original_requirement_text": {"type": "string", "minLength": 1, "x-maxUtf8Bytes": 16_384},
            "selected_skills": {
                "type": "array", "minItems": 1, "maxItems": 32, "uniqueItems": True,
                "items": {"type": "string", "minLength": 1, "maxLength": 128,
                          "pattern": r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$"},
            },
            "response_mode": {"type": "string", "enum": ["text", "structured"]},
        },
    }}}},
}
_發布本文綱要 = {
    "requestBody": {"required": True, "content": {"application/json": {"schema": {
        "type": "object", "additionalProperties": False,
        "required": ["draft_id", "slug", "configuration_confirmation"],
        "properties": {
            "draft_id": {"type": "string", "minLength": 1, "maxLength": 128,
                         "pattern": r"^[A-Za-z0-9_.:-]+$"},
            "slug": {"type": "string", "minLength": 1, "maxLength": 63,
                     "pattern": r"^[a-z0-9][a-z0-9-]*$"},
            "configuration_confirmation": {
                "type": "object", "maxProperties": 256,
                "propertyNames": {"type": "string", "x-maxUtf8Bytes": 256},
            },
        },
    }}}},
}
_版本本文綱要 = {
    "requestBody": {"required": True, "content": {"application/json": {"schema": {
        "type": "object", "additionalProperties": False, "required": ["configuration"],
        "properties": {"configuration": {
            "type": "object", "maxProperties": 256,
            "propertyNames": {"type": "string", "x-maxUtf8Bytes": 256},
        }},
    }}}},
}


def OpenAPI本文符合專案契約(綱要: dict[str, Any], 本文: Any) -> bool:
    """同時執行標準 JSON Schema 與專案 UTF-8 位元組擴充。

    參數：``綱要`` 是含 ``x-maxUtf8Bytes`` 的本文綱要；``本文`` 是候選 JSON 值。
    回傳：標準規則及所有字串與屬性名稱位元組上限皆通過時回傳 ``True``。
    例外：控制流例外原樣傳遞；綱要、編碼或驗證失效則回傳 ``False``。
    副作用：只走訪綱要與本文，不修改輸入或執行外部輸入輸出。
    """
    try:
        if type(綱要) is not dict:
            return False
        Draft202012Validator.check_schema(綱要)
        return Draft202012Validator(綱要).is_valid(本文) and _符合OpenAPI位元組(綱要, 本文)
    except (KeyboardInterrupt, SystemExit, GeneratorExit):
        raise
    except BaseException:
        return False


def _符合OpenAPI位元組(綱要: dict[str, Any], 值: Any) -> bool:
    """遞迴執行專案 ``x-maxUtf8Bytes`` 關鍵字。

    參數：``綱要`` 是目前JSON節點的OpenAPI綱要；``值`` 是待驗證的同層候選值。
    回傳：目前節點、物件屬性名稱、子屬性及陣列項目的UTF-8位元組上限全數通過時回傳``True``。
    例外：字串編碼或遞迴走訪遇到的例外原樣傳遞，由公開契約檢查器統一處理。
    副作用：只讀取綱要與候選值，不修改輸入，也不執行檔案、資料庫或網路操作。
    """
    上限 = 綱要.get("x-maxUtf8Bytes")
    if 上限 is not None and (type(上限) is not int or type(值) is not str or len(值.encode("utf-8")) > 上限):
        return False
    if type(值) is dict:
        名稱綱要 = 綱要.get("propertyNames", {})
        if type(名稱綱要) is dict and any(not _符合OpenAPI位元組(名稱綱要, 鍵) for 鍵 in 值):
            return False
        屬性 = 綱要.get("properties", {})
        額外 = 綱要.get("additionalProperties", {})
        for 鍵, 子值 in 值.items():
            子綱要 = 屬性.get(鍵, 額外) if type(屬性) is dict else 額外
            if type(子綱要) is dict and not _符合OpenAPI位元組(子綱要, 子值):
                return False
    elif type(值) is list and type(綱要.get("items")) is dict:
        return all(_符合OpenAPI位元組(綱要["items"], 子值) for 子值 in 值)
    return True


class _嚴格請求(BaseModel):
    """拒絕額外欄位與型別轉換的管理請求基底。"""

    model_config = ConfigDict(extra="forbid", strict=True, populate_by_name=False)

    @field_validator("*", mode="after")
    @classmethod
    def 驗證JSON資源(cls, 值: Any) -> Any:
        """限制巢狀 JSON 的深度、節點與字串 UTF-8 大小。"""
        if type(值) is dict:
            _檢查JSON(值, 0, [0])
        return 值


class 建立草稿請求(_嚴格請求):
    """Planner 需求與內容；只建立不可呼叫的暫存草稿。"""

    原始需求文字: Annotated[StrictStr, Field(alias="original_requirement_text", min_length=1, max_length=16_384)]
    規劃器內容: Annotated[dict[str, JsonValue], Field(alias="planner_content")]


class 安全建立草稿請求(_嚴格請求):
    """限制 CP4 客戶端只可提供建立草稿所需意圖。

    欄位：``原始需求文字`` 是需求；``選擇技能`` 是排序且唯一的技能名稱；``回應模式`` 是文字或結構化模式。
    回傳：驗證後得到不含工具、提示、規劃內容或限制設定的請求物件。
    例外：欄位型別、界限、格式、排序或唯一性不符時由 Pydantic 回報驗證例外。
    副作用：驗證只檢查輸入，無外部副作用。
    """

    原始需求文字: Annotated[StrictStr, Field(alias="original_requirement_text", min_length=1, max_length=16_384)]
    選擇技能: Annotated[list[StrictStr], Field(alias="selected_skills", min_length=1, max_length=32)]
    回應模式: Annotated[Literal["text", "structured"], Field(alias="response_mode")]

    @field_validator("原始需求文字")
    @classmethod
    def 驗證需求(cls, 值: str) -> str:
        """驗證需求文字的空白與 UTF-8 位元組界限。

        參數：``值`` 是 Pydantic 已完成基本型別與字元數檢查的需求文字。
        回傳：驗證後的原文字。
        例外：空字串、首尾空白或超過 16,384 個 UTF-8 位元組時拋出 ``ValueError``。
        副作用：無外部副作用，也不修改輸入。
        """
        if 值 != 值.strip() or not 值 or len(值.encode("utf-8")) > 16_384:
            raise ValueError("需求無效")
        return 值

    @field_validator("選擇技能")
    @classmethod
    def 驗證技能(cls, 值: list[str]) -> list[str]:
        """驗證技能名稱格式、排序與唯一性。

        參數：``值`` 是 Pydantic 已完成基本型別與數量檢查的技能串列。
        回傳：驗證後的原串列物件。
        例外：名稱格式不符、未排序或含重複值時拋出 ``ValueError``。
        副作用：無外部副作用，也不修改串列。
        """
        if any(_識別格式.fullmatch(名稱) is None for 名稱 in 值) or 值 != sorted(set(值)):
            raise ValueError("技能選擇無效")
        return 值


class 安全草稿服務(Protocol):
    """規範安全草稿路由唯一需要的伺服器端規劃服務。

    方法：``建立草稿`` 接收擁有者、需求、技能、模式及時間，回傳 ``規劃草稿``。
    例外：實作者可拋出授權、規劃器輸出或規劃器可用性例外。
    副作用：協定本身無副作用；實作者會建立並持久化草稿。
    """

    def 建立草稿(self, 擁有者識別碼: str, 原始需求: str, 選擇技能: tuple[str, ...], 回應模式: str, *, 現在: float) -> 規劃草稿:
        """建立伺服器端規劃草稿。

        參數：``擁有者識別碼`` 是權限主體；``原始需求`` 是需求；``選擇技能`` 是技能元組；
        ``回應模式`` 是回應模式；``現在`` 是建立時間。
        回傳：已保存的 ``規劃草稿``。
        例外：可能拋出 ``授權選擇錯誤``、``規劃器輸出無效``、``規劃器不可用`` 或控制流例外。
        副作用：實作者會查詢權限、呼叫規劃器並持久化草稿。
        """
        ...


class 發布端點請求(_嚴格請求):
    """使用者對草稿、slug 與配置的明確發布確認。"""

    草稿識別碼: Annotated[StrictStr, Field(alias="draft_id", min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_.:-]+$")]
    短名: Annotated[StrictStr, Field(alias="slug", min_length=1, max_length=63, pattern=r"^[a-z0-9][a-z0-9-]*$")]
    配置確認: Annotated[dict[str, JsonValue], Field(alias="configuration_confirmation")]


class 建立版本請求(_嚴格請求):
    """新不可變版本的完整配置確認。"""

    配置: Annotated[dict[str, JsonValue], Field(alias="configuration")]


@dataclass(frozen=True, slots=True)
class 規劃內容:
    """傳給整合服務的 detached Planner 投影。"""

    原始需求: str
    內容: dict[str, JsonValue]


@dataclass(frozen=True, slots=True)
class 發布確認:
    """傳給單一原子發布操作的確認投影。"""

    草稿識別碼: str
    短名: str
    配置: dict[str, JsonValue]


@dataclass(frozen=True, slots=True)
class 草稿建立結果:
    """草稿建立後唯一可公開的識別、期限與預覽。"""

    草稿識別碼: Annotated[str, Field(alias="draft_id")]
    到期時間: Annotated[float, Field(alias="expires_at")]
    預覽: Annotated[dict[str, JsonValue], Field(alias="preview")]


@dataclass(frozen=True, slots=True)
class 端點發布結果:
    """原子發布 receipt；初始明文金鑰只供本次成功回應。"""

    端點識別碼: Annotated[str, Field(alias="endpoint_id")]
    版本識別碼: Annotated[str, Field(alias="version_id")]
    版本編號: Annotated[int, Field(alias="version_number")]
    狀態: Annotated[str, Field(alias="status")]
    初始API金鑰: Annotated[str, Field(alias="initial_api_key")] = field(repr=False)


@dataclass(frozen=True, slots=True)
class 版本建立結果:
    """新版本與目前指標完成原子切換後的 receipt。"""

    端點識別碼: Annotated[str, Field(alias="endpoint_id")]
    版本識別碼: Annotated[str, Field(alias="version_id")]
    版本編號: Annotated[int, Field(alias="version_number")]
    目前版本識別碼: Annotated[str, Field(alias="current_version_id")]
    結構已變更: Annotated[bool, Field(alias="schema_changed")]


@dataclass(frozen=True, slots=True)
class 管理操作錯誤:
    """整合服務唯一可回傳的固定錯誤分類。"""

    種類: Literal["invalid", "draft_not_found", "endpoint_not_found", "forbidden", "status_conflict", "concurrency", "internal"]


class 發布管理服務(Protocol):
    """由整合層提供草稿與兩個原子寫入操作。

    參數：實作者的方法接收權威使用者識別碼及各操作所需資料。
    回傳：各方法回傳對應成功收據或固定的管理操作錯誤。
    例外：協定不攔截實作者例外；路由邊界會統一映射非控制流失敗。
    副作用：協定本身無副作用；實作者可保存草稿、端點及版本。
    """

    def 建立草稿(self, *, 擁有者使用者識別碼: str, 規劃: 規劃內容) -> 草稿建立結果 | 管理操作錯誤:
        """建立不具發布副作用的暫存草稿。"""
        ...

    def 原子發布(self, *, 擁有者使用者識別碼: str, 確認: 發布確認) -> 端點發布結果 | 管理操作錯誤:
        """以單一原子操作發布端點、首版與初始憑證。"""
        ...

    def 原子建立並切換版本(
        self, *, 擁有者使用者識別碼: str, 端點識別碼: str,
        配置: dict[str, JsonValue],
    ) -> 版本建立結果 | 管理操作錯誤:
        """由服務重查擁有者或管理者權限後原子建立版本並切換指標。

        參數：接收權威使用者識別碼、端點識別碼與已分離的版本配置。
        回傳：成功時回傳版本建立結果，拒絕時回傳固定管理操作錯誤。
        例外：實作者的非控制流例外由呼叫邊界轉成內部錯誤。
        副作用：權限通過時以單一交易建立不可變版本並切換目前版本指標。
        """
        ...


def 建立規劃發布路由器(服務: 發布管理服務, 身份依賴) -> APIRouter:
    """建立三條 session-identity 管理路由；成功 create 一律固定 201。"""
    路由器 = APIRouter()

    草稿處理器 = _綁定處理器(_建立發布草稿, 服務, 身份依賴, "建立發布草稿", "建立純草稿，不配置 endpoint、版本或憑證。")
    發布處理器 = _綁定處理器(_發布端點, 服務, 身份依賴, "發布端點", "只委派一次原子服務操作，回傳初始明文金鑰一次。")
    版本處理器 = _綁定處理器(_建立不可變版本, 服務, 身份依賴, "建立不可變版本", "由服務一次完成 owner/admin 授權、create-only insert 與 pointer switch。")
    路由器.post("/api/published-endpoints/draft", status_code=201, response_model=草稿建立結果)(草稿處理器)
    路由器.post("/api/published-endpoints", status_code=201, response_model=端點發布結果)(發布處理器)
    路由器.post("/api/published-endpoints/{endpoint_id}/versions", status_code=201, response_model=版本建立結果)(版本處理器)

    return 路由器


def 建立發布版本路由器(服務: 發布管理服務, 身份依賴) -> APIRouter:
    """建立只含發布與版本處理器的路由器。

    參數：``服務`` 是發布管理服務；``身份依賴`` 是工作階段身份相依項目。
    回傳：已註冊發布與版本端點的 ``APIRouter``。
    例外：處理器綁定或 FastAPI 路由註冊失敗時傳遞其例外。
    副作用：建立路由器、捕捉兩項依賴並註冊兩條路由；不執行服務操作。
    """
    路由器 = APIRouter()
    發布處理器 = _綁定處理器(_發布端點, 服務, 身份依賴, "發布端點", "只委派一次原子服務操作，回傳初始明文金鑰一次。")
    版本處理器 = _綁定處理器(_建立不可變版本, 服務, 身份依賴, "建立不可變版本", "由服務一次完成 owner/admin 授權、create-only insert 與 pointer switch。")
    路由器.post("/api/published-endpoints", status_code=201, response_model=端點發布結果)(發布處理器)
    路由器.post("/api/published-endpoints/{endpoint_id}/versions", status_code=201, response_model=版本建立結果)(版本處理器)
    return 路由器


def 建立安全草稿路由器(服務: 安全草稿服務, 目前工作階段相依, csrf相依, *, 時鐘=time.time) -> APIRouter:
    """建立正規目前工作階段與單次 CSRF 相容的草稿路由。

    參數：``服務`` 是安全草稿服務；``目前工作階段相依`` 提供身份；``csrf相依`` 驗證單次權杖；
    ``時鐘`` 提供建立時間。
    回傳：已註冊安全草稿端點的 ``APIRouter``。
    例外：FastAPI 路由註冊失敗時傳遞其例外；註冊形狀漂移時拋出 ``RuntimeError``；
    請求期間的例外由內部處理器映射。
    副作用：建立路由器、捕捉四項依賴、註冊一條路由，並清除處理器的執行期文件字串以固定
    OpenAPI 說明為空；不立即呼叫服務。
    """
    路由器 = APIRouter(prefix="/api/published-endpoints")

    @路由器.post(
        "/draft", status_code=201, response_model=草稿建立結果,
        openapi_extra=_草稿本文綱要,
    )
    async def 建立伺服器草稿(
        請求: Request,
        使用者: 網頁使用者 = Depends(目前工作階段相依),
        _csrf使用者: 網頁使用者 = Depends(csrf相依),
    ) -> JSONResponse:
        """驗證目前工作階段請求並建立伺服器端草稿。

        參數：``請求`` 提供待消耗的 HTTP 本文；``使用者`` 是目前工作階段身份；
        ``_csrf使用者`` 是觸發單次 CSRF 驗證但不另行使用的身份結果。
        回傳：狀態碼 201 且只含草稿識別碼、到期時間與預覽的 ``JSONResponse``。
        例外：身份契約失效映射為 HTTP 500；未授權、輸出失效與服務不可用分別映射為 HTTP 403、502、503；
        本文失效由解析器映射為 HTTP 422，控制流例外原樣傳遞。
        副作用：消耗請求本文、讀取工作階段與 CSRF 相依結果、讀取時鐘，並呼叫服務持久化草稿。
        """
        本文 = await _解析安全草稿本文(請求)
        使用者識別碼 = _重建網頁身份(使用者, _csrf使用者)
        try:
            草稿 = await run_in_threadpool(
                服務.建立草稿, 使用者識別碼, 本文.原始需求文字,
                tuple(本文.選擇技能), 本文.回應模式, 現在=時鐘(),
            )
            if type(草稿) is not 規劃草稿:
                raise ValueError
            return JSONResponse(status_code=201, content={
                "draft_id": 草稿.草稿識別碼,
                "expires_at": 草稿.到期時間,
                "preview": 草稿.綱要,
            })
        except (KeyboardInterrupt, SystemExit, GeneratorExit):
            raise
        except 授權選擇錯誤:
            raise HTTPException(status_code=403, detail={"code": "planning_not_authorized"}) from None
        except 規劃器輸出無效:
            raise HTTPException(status_code=502, detail={"code": "planner_output_invalid"}) from None
        except 規劃器不可用:
            raise HTTPException(status_code=503, detail={"code": "planner_unavailable"}) from None
        except BaseException:
            raise HTTPException(status_code=503, detail={"code": "planner_unavailable"}) from None

    建立伺服器草稿.__doc__ = None
    草稿路由 = 路由器.routes[-1]
    if type(草稿路由) is not APIRoute:
        raise RuntimeError("安全草稿路由註冊失敗")
    草稿路由.description = ""
    return 路由器


def 建立安全規劃發布路由器(
    草稿服務: 安全草稿服務,
    發布服務: 發布管理服務,
    目前工作階段相依,
    csrf相依,
    *,
    時鐘=time.time,
) -> APIRouter:
    """建立三條正式環境管理寫入路由的單一安全工廠。

    參數：接收草稿與發布服務、目前工作階段相依、跨站請求偽造防護相依及可替換時鐘。
    回傳：含草稿、發布與版本建立三條路由的 ``APIRouter``。
    例外：路由註冊失敗時傳遞例外；請求期間的輸入、身份及服務失敗由各處理器映射。
    副作用：建立路由器並捕捉服務與相依項；同步服務操作於請求期間移至工作執行緒。
    """
    路由器 = 建立安全草稿路由器(
        草稿服務, 目前工作階段相依, csrf相依, 時鐘=時鐘,
    )

    @路由器.post(
        "", status_code=201, response_model=端點發布結果,
        responses={403: {}, 404: {}, 409: {}, 422: {}, 500: {}},
        openapi_extra=_發布本文綱要,
    )
    async def 發布端點(
        請求: Request,
        使用者: 網頁使用者 = Depends(目前工作階段相依),
        _csrf使用者: 網頁使用者 = Depends(csrf相依),
    ) -> JSONResponse:
        """以權威工作階段使用者識別碼執行單一原子發布。

        參數：接收原始請求，以及目前工作階段與跨站防護相依回傳的身份。
        回傳：成功時回傳狀態碼 201 與發布收據。
        例外：本文、身份或服務結果失效時映射為固定 HTTP 錯誤。
        副作用：消耗本文、驗證兩份身份，並在工作執行緒呼叫一次發布服務。
        """
        本文 = 使用者識別碼 = 回應 = None
        try:
            本文 = await _解析管理本文(請求, 發布端點請求)
            使用者識別碼 = _重建網頁身份(使用者, _csrf使用者)
            回應 = await run_in_threadpool(_安全發布端點, 發布服務, 本文, 使用者識別碼)
            return 回應
        finally:
            請求 = 使用者 = _csrf使用者 = 本文 = 使用者識別碼 = 回應 = None

    @路由器.post(
        "/{endpoint_id}/versions", status_code=201,
        response_model=版本建立結果,
        responses={403: {}, 404: {}, 409: {}, 422: {}, 500: {}},
        openapi_extra=_版本本文綱要,
    )
    async def 建立不可變版本(
        請求: Request,
        端點識別碼: Annotated[
            str, Path(alias="endpoint_id", min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_.:-]+$")
        ],
        使用者: 網頁使用者 = Depends(目前工作階段相依),
        _csrf使用者: 網頁使用者 = Depends(csrf相依),
    ) -> JSONResponse:
        """只傳使用者識別碼，讓服務重查權限後建立版本。

        參數：接收原始請求、受限端點識別碼，以及兩個相依項回傳的身份。
        回傳：成功時回傳狀態碼 201 與新版本及目前版本指標收據。
        例外：本文、身份或服務結果失效時映射為固定 HTTP 錯誤。
        副作用：消耗本文、驗證兩份身份，並在工作執行緒呼叫一次版本服務。
        """
        本文 = 使用者識別碼 = 回應 = None
        try:
            本文 = await _解析管理本文(請求, 建立版本請求)
            使用者識別碼 = _重建網頁身份(使用者, _csrf使用者)
            回應 = await run_in_threadpool(
                _安全建立版本, 發布服務, 本文, 端點識別碼, 使用者識別碼,
            )
            return 回應
        finally:
            請求 = 端點識別碼 = 使用者 = _csrf使用者 = None
            本文 = 使用者識別碼 = 回應 = None

    return 路由器


def _重建網頁身份(使用者: object, csrf使用者: object) -> str:
    """要求兩個正規相依項回傳型別精確且同主體的網頁身份。

    參數：接收目前工作階段及跨站防護相依項各自回傳的候選身份。
    回傳：兩者皆有效且相同時回傳權威使用者識別碼。
    例外：型別、識別碼形狀或主體一致性失效時拋出 HTTP 500。
    副作用：無；不執行敵意物件的自訂相等或屬性存取協定。
    """
    if type(使用者) is not 網頁使用者 or type(csrf使用者) is not 網頁使用者:
        raise HTTPException(status_code=500, detail={"code": "identity_contract_invalid"})
    使用者識別碼 = object.__getattribute__(使用者, "識別碼")
    csrf識別碼 = object.__getattribute__(csrf使用者, "識別碼")
    if not _是識別(使用者識別碼) or not _是識別(csrf識別碼):
        raise HTTPException(status_code=500, detail={"code": "identity_contract_invalid"})
    相同 = str.__eq__(使用者識別碼, csrf識別碼)
    if type(相同) is not bool or not 相同:
        raise HTTPException(status_code=500, detail={"code": "identity_contract_invalid"})
    return 使用者識別碼


def _安全發布端點(服務: 發布管理服務, 請求: 發布端點請求, 使用者識別碼: str) -> JSONResponse:
    """在工作執行緒建立分離確認並封閉處理服務結果。

    參數：接收發布服務、已驗證請求及權威使用者識別碼。
    回傳：成功時回傳狀態碼 201 且欄位固定的發布收據。
    例外：固定服務錯誤映射為 HTTP 錯誤；控制流例外原樣傳遞。
    副作用：複製配置並只呼叫一次原子發布服務。
    """
    配置 = 確認 = 結果 = 內容 = 回應 = None
    try:
        配置 = _複製JSON物件(請求.配置確認)
        確認 = 發布確認(請求.草稿識別碼, 請求.短名, 配置)
        結果 = _呼叫服務(
            服務, "原子發布", _重建發布結果,
            擁有者使用者識別碼=使用者識別碼, 確認=確認,
        )
        if type(結果) is 管理操作錯誤:
            _拋出錯誤(結果)
        內容 = {
            "endpoint_id": 結果.端點識別碼, "version_id": 結果.版本識別碼,
            "version_number": 結果.版本編號, "status": 結果.狀態,
            "initial_api_key": 結果.初始API金鑰,
        }
        回應 = JSONResponse(status_code=201, content=內容)
        return 回應
    finally:
        服務 = 請求 = 使用者識別碼 = 配置 = 確認 = 結果 = 內容 = 回應 = None


def _安全建立版本(
    服務: 發布管理服務, 請求: 建立版本請求, 端點識別碼: str, 使用者識別碼: str,
) -> JSONResponse:
    """在工作執行緒只以使用者識別碼委派權威版本操作。

    參數：接收發布服務、已驗證請求、端點識別碼及權威使用者識別碼。
    回傳：成功時回傳狀態碼 201 且欄位固定的版本收據。
    例外：固定服務錯誤映射為 HTTP 錯誤；控制流例外原樣傳遞。
    副作用：複製配置並只呼叫一次原子建立與切換版本服務。
    """
    配置 = 結果 = 內容 = 回應 = None
    try:
        配置 = _複製JSON物件(請求.配置)
        結果 = _呼叫服務(
            服務, "原子建立並切換版本", _重建版本結果, (端點識別碼,),
            擁有者使用者識別碼=使用者識別碼, 端點識別碼=端點識別碼, 配置=配置,
        )
        if type(結果) is 管理操作錯誤:
            _拋出錯誤(結果)
        內容 = {
            "endpoint_id": 結果.端點識別碼, "version_id": 結果.版本識別碼,
            "version_number": 結果.版本編號, "current_version_id": 結果.目前版本識別碼,
            "schema_changed": 結果.結構已變更,
        }
        回應 = JSONResponse(status_code=201, content=內容)
        return 回應
    finally:
        服務 = 請求 = 端點識別碼 = 使用者識別碼 = 配置 = 結果 = 內容 = 回應 = None


async def _解析安全草稿本文(請求: Request) -> 安全建立草稿請求:
    """在嚴格 JSON 解析前以串流限制 HTTP 本文位元組數。

    參數：``請求`` 是待讀取且須宣告精確 ``application/json`` 內容型別的 FastAPI 請求。
    回傳：通過嚴格解析與 Pydantic 驗證的 ``安全建立草稿請求``。
    例外：內容型別、長度、編碼、JSON 或欄位驗證失敗映射為 HTTP 422；控制流例外原樣傳遞。
    副作用：完整消耗請求串流；不呼叫規劃服務或持久化資料。
    """
    try:
        if 請求.headers.get("content-type") != "application/json":
            raise ValueError
        宣告長度 = 請求.headers.get("content-length")
        if 宣告長度 is not None and (not 宣告長度.isascii() or not 宣告長度.isdigit() or int(宣告長度) > 32_768):
            raise ValueError
        片段們 = []
        長度 = 0
        async for 片段 in 請求.stream():
            長度 += len(片段)
            if 長度 > 32_768:
                raise ValueError
            片段們.append(片段)
        原始值 = 解析嚴格JSON(b"".join(片段們).decode("utf-8"))
        if type(原始值) is not dict:
            raise ValueError
        return 安全建立草稿請求.model_validate(原始值)
    except (KeyboardInterrupt, SystemExit, GeneratorExit):
        raise
    except BaseException:
        raise HTTPException(status_code=422, detail={"code": "invalid_request"}) from None


async def _解析管理本文(請求: Request, 模型: type[_嚴格請求]) -> _嚴格請求:
    """以 32 KiB 串流上限、精確內容型別與重複鍵安全解析管理本文。

    參數：接收待消耗的 HTTP 請求及目標嚴格請求模型。
    回傳：通過位元組上限、嚴格 JSON 與模型驗證的請求物件。
    例外：內容型別、長度、編碼、JSON 或欄位失效映射為 HTTP 422；控制流例外原樣傳遞。
    副作用：完整消耗請求串流；不呼叫管理服務。
    """
    try:
        if 請求.headers.get("content-type") != "application/json":
            raise ValueError
        宣告長度 = 請求.headers.get("content-length")
        if 宣告長度 is not None and (
            not 宣告長度.isascii() or not 宣告長度.isdigit() or int(宣告長度) > 32_768
        ):
            raise ValueError
        片段們: list[bytes] = []
        長度 = 0
        async for 片段 in 請求.stream():
            長度 += len(片段)
            if 長度 > 32_768:
                raise ValueError
            片段們.append(片段)
        原始值 = 解析嚴格JSON(b"".join(片段們).decode("utf-8"))
        if type(原始值) is not dict:
            raise ValueError
        return 模型.model_validate(原始值)
    except (KeyboardInterrupt, SystemExit, GeneratorExit):
        raise
    except BaseException:
        raise HTTPException(status_code=422, detail={"code": "invalid_request"}) from None


def _綁定處理器(處理器, 服務: 發布管理服務, 身份依賴, 名稱: str, 文件: str):
    """以 partial 隱藏服務參數，並保留原公開路由名稱與文件。"""
    已綁定 = partial(處理器, 服務)
    簽章 = signature(已綁定)
    型別提示 = get_type_hints(處理器, include_extras=True)
    參數 = [項目.replace(annotation=型別提示[項目.name], default=Depends(身份依賴) if 項目.name == "身份" else 項目.default) for 項目 in 簽章.parameters.values()]
    setattr(已綁定, "__name__", 名稱)
    setattr(已綁定, "__doc__", 文件)
    setattr(已綁定, "__signature__", 簽章.replace(parameters=參數, return_annotation=型別提示["return"]))
    return 已綁定


def _建立發布草稿(
    服務: 發布管理服務,
    請求: 建立草稿請求,
    身份: 使用者上下文,
) -> JSONResponse:
    """建立純草稿，並在離開公開處理器前清除所有傳遞參照。"""
    使用者 = 規劃 = 結果 = 安全 = 內容 = 回應 = None
    try:
        使用者, _ = _重建身份(身份)
        內容 = _複製JSON物件(請求.規劃器內容)
        規劃 = 規劃內容(請求.原始需求文字, 內容)
        結果 = _呼叫服務(服務, "建立草稿", _重建草稿結果, 擁有者使用者識別碼=使用者, 規劃=規劃)
        if type(結果) is 管理操作錯誤:
            _拋出錯誤(結果)
        安全 = 結果
        回應 = JSONResponse(status_code=201, content={"draft_id": 安全.草稿識別碼, "expires_at": 安全.到期時間, "preview": 安全.預覽})
        return 回應
    finally:
        服務 = 請求 = 身份 = 使用者 = 規劃 = 結果 = 安全 = 內容 = 回應 = None


def _發布端點(
    服務: 發布管理服務,
    請求: 發布端點請求,
    身份: 使用者上下文,
) -> JSONResponse:
    """只委派一次原子操作，成功回應保留金鑰而處理器不保留。"""
    使用者 = 確認 = 結果 = 安全 = 配置 = 金鑰 = 內容 = 回應 = None
    try:
        使用者, _ = _重建身份(身份)
        配置 = _複製JSON物件(請求.配置確認)
        確認 = 發布確認(請求.草稿識別碼, 請求.短名, 配置)
        結果 = _呼叫服務(服務, "原子發布", _重建發布結果, 擁有者使用者識別碼=使用者, 確認=確認)
        if type(結果) is 管理操作錯誤:
            _拋出錯誤(結果)
        安全 = 結果
        金鑰 = 安全.初始API金鑰
        內容 = {"endpoint_id": 安全.端點識別碼, "version_id": 安全.版本識別碼, "version_number": 1, "status": "active", "initial_api_key": 金鑰}
        回應 = JSONResponse(status_code=201, content=內容)
        return 回應
    finally:
        服務 = 請求 = 身份 = 使用者 = 確認 = 結果 = 安全 = 配置 = 金鑰 = 內容 = 回應 = None


def _建立不可變版本(
    服務: 發布管理服務,
    請求: 建立版本請求,
    端點識別碼: Annotated[str, Path(alias="endpoint_id", min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_.:-]+$")],
    身份: 使用者上下文,
) -> JSONResponse:
    """委派舊工廠的原子版本切換並清除所有傳遞參照。

    參數：接收服務、已驗證請求、路徑端點識別碼與權威工作階段身份。
    回傳：成功時回傳狀態碼 201 與固定欄位的版本切換收據。
    例外：服務錯誤映射為固定 HTTP 錯誤；控制流例外保持 identity 與參數原樣傳遞。
    副作用：複製配置、呼叫一次原子版本服務，離開時清除請求、身份、配置與回執別名。
    """
    使用者 = 配置 = 結果 = 安全 = 內容 = 回應 = None
    try:
        使用者, _ = _重建身份(身份)
        配置 = _複製JSON物件(請求.配置)
        結果 = _呼叫服務(服務, "原子建立並切換版本", _重建版本結果, (端點識別碼,), 擁有者使用者識別碼=使用者, 端點識別碼=端點識別碼, 配置=配置)
        if type(結果) is 管理操作錯誤:
            _拋出錯誤(結果)
        安全 = 結果
        內容 = {"endpoint_id": 安全.端點識別碼, "version_id": 安全.版本識別碼, "version_number": 安全.版本編號, "current_version_id": 安全.目前版本識別碼, "schema_changed": 安全.結構已變更}
        回應 = JSONResponse(status_code=201, content=內容)
        return 回應
    finally:
        服務 = 請求 = 端點識別碼 = 身份 = 使用者 = None
        配置 = 結果 = 安全 = 內容 = 回應 = None


def _呼叫服務(服務: 發布管理服務, 方法名稱: str, 重建器=None, 重建參數=(), **參數):
    """在同一 totalized 邊界呼叫服務並重建不可信回執。"""
    結果 = 方法 = 安全結果 = None
    控制流: list[BaseException] = []
    失敗 = False
    try:
        try:
            方法 = object.__getattribute__(服務, 方法名稱)
            結果 = 方法(**參數)
            if type(結果) is 管理操作錯誤:
                安全結果 = _重建管理錯誤(結果)
            elif 重建器 is None:
                安全結果 = 結果
            else:
                安全結果 = 重建器(結果, *重建參數)
        except (KeyboardInterrupt, SystemExit, GeneratorExit) as 錯誤:
            控制流.append(錯誤)
        except BaseException:
            失敗 = True
        if 控制流:
            安全結果 = None
            _重拋控制流(控制流)
        if 失敗:
            安全結果 = None
            _無效結果()
        return 安全結果
    finally:
        服務 = 方法名稱 = 重建器 = 重建參數 = 參數 = 方法 = 結果 = 安全結果 = None


def _重拋控制流(暫存: list[BaseException]) -> None:
    """pop 後重拋原控制流，使 production frame 不保留其 args。"""
    raise 暫存.pop()


def _重建管理錯誤(來源: 管理操作錯誤) -> 管理操作錯誤:
    """重建 exact error receipt，拒絕竄改種類。"""
    種類 = object.__getattribute__(來源, "種類")
    if type(種類) is not str or 種類 not in _錯誤對照:
        raise ValueError
    return 管理操作錯誤(種類)


def _拋出錯誤(錯誤: 管理操作錯誤) -> None:
    """只接受模組自有 exact error DTO 並套固定映射。"""
    try:
        種類 = object.__getattribute__(錯誤, "種類")
        狀態, 訊息 = _錯誤對照[種類]
    except BaseException:
        raise HTTPException(status_code=500, detail="發布管理服務失敗") from None
    raise HTTPException(status_code=狀態, detail=訊息)


def _重建身份(身份: 使用者上下文) -> tuple[str, bool]:
    """沿用 M01 的精確可信 session identity 契約。"""
    身份類型 = 使用者 = 管理者 = 結果 = None
    try:
        身份類型 = type(身份)
        if 身份類型 is not 使用者上下文:
            raise HTTPException(status_code=500, detail="使用者身份不符合契約")
        使用者 = object.__getattribute__(身份, "user_id")
        管理者 = object.__getattribute__(身份, "is_admin")
        if not _是識別(使用者) or type(管理者) is not bool:
            raise HTTPException(status_code=500, detail="使用者身份不符合契約")
        結果 = (使用者, 管理者)
        return 結果
    finally:
        身份 = 身份類型 = 使用者 = 管理者 = 結果 = None


def _重建草稿結果(來源: object) -> 草稿建立結果:
    """驗證並重建模組自有的安全草稿結果。"""
    識別 = 到期 = 預覽 = 安全結果 = None
    控制流: list[BaseException] = []
    失敗 = type(來源) is not 草稿建立結果
    try:
        try:
            if not 失敗:
                識別 = object.__getattribute__(來源, "草稿識別碼")
                到期 = object.__getattribute__(來源, "到期時間")
                預覽 = object.__getattribute__(來源, "預覽")
                失敗 = not _是識別(識別) or not _是時間(到期)
            if not 失敗:
                安全結果 = 草稿建立結果(識別, 到期, _複製JSON物件(預覽))
        except (KeyboardInterrupt, SystemExit, GeneratorExit) as 錯誤:
            控制流.append(錯誤)
        except BaseException:
            失敗 = True
        if 控制流:
            安全結果 = None
            _重拋控制流(控制流)
        if 失敗:
            安全結果 = None
            _無效結果()
        return 安全結果
    finally:
        來源 = 識別 = 到期 = 預覽 = 安全結果 = None


def _重建發布結果(來源: object) -> 端點發布結果:
    """先驗證所有非秘密槽，最後一次讀取並立即封裝初始金鑰。"""
    端點 = 版本 = 編號 = 狀態 = 金鑰 = 安全結果 = None
    控制流: list[BaseException] = []
    失敗 = type(來源) is not 端點發布結果
    try:
        try:
            if not 失敗:
                端點 = object.__getattribute__(來源, "端點識別碼")
                版本 = object.__getattribute__(來源, "版本識別碼")
                編號 = object.__getattribute__(來源, "版本編號")
                狀態 = object.__getattribute__(來源, "狀態")
                失敗 = not _是識別(端點) or not _是識別(版本) or type(編號) is not int or 編號 != 1 or type(狀態) is not str or 狀態 != "active"
            if not 失敗:
                金鑰 = object.__getattribute__(來源, "初始API金鑰")
                if _是金鑰(金鑰):
                    安全結果 = 端點發布結果(端點, 版本, 編號, 狀態, 金鑰)
                else:
                    失敗 = True
        except (KeyboardInterrupt, SystemExit, GeneratorExit) as 錯誤:
            控制流.append(錯誤)
        except BaseException:
            失敗 = True
        if 控制流:
            安全結果 = None
            _重拋控制流(控制流)
        if 失敗:
            安全結果 = None
            _無效結果()
        return 安全結果
    finally:
        來源 = 端點 = 版本 = 編號 = 狀態 = 金鑰 = 安全結果 = None


def _重建版本結果(來源: object, 端點識別碼: str) -> 版本建立結果:
    """逐槽精確驗證並重建與 authoritative 路徑端點一致的結果。"""
    回執端點 = 版本 = 編號 = 目前版本 = 已變更 = 安全結果 = None
    回執端點類型 = 版本類型 = 編號類型 = 目前版本類型 = 已變更類型 = None
    端點比較 = 版本比較 = None
    控制流: list[BaseException] = []
    失敗 = type(來源) is not 版本建立結果
    try:
        try:
            if not 失敗:
                回執端點 = 版本建立結果.端點識別碼.__get__(來源, 版本建立結果)
                回執端點類型 = type(回執端點)
                失敗 = 回執端點類型 is not str or not _是識別(回執端點)
            if not 失敗:
                失敗 = type(端點識別碼) is not str or not _是識別(端點識別碼)
            if not 失敗:
                端點比較 = str.__eq__(回執端點, 端點識別碼)
                失敗 = type(端點比較) is not bool or not 端點比較
            if not 失敗:
                版本 = 版本建立結果.版本識別碼.__get__(來源, 版本建立結果)
                版本類型 = type(版本)
                失敗 = 版本類型 is not str or not _是識別(版本)
            if not 失敗:
                編號 = 版本建立結果.版本編號.__get__(來源, 版本建立結果)
                編號類型 = type(編號)
                失敗 = 編號類型 is not int or 編號 < 2 or 編號 > 2_147_483_647
            if not 失敗:
                目前版本 = 版本建立結果.目前版本識別碼.__get__(來源, 版本建立結果)
                目前版本類型 = type(目前版本)
                失敗 = 目前版本類型 is not str or not _是識別(目前版本)
            if not 失敗:
                版本比較 = str.__eq__(目前版本, 版本)
                失敗 = type(版本比較) is not bool or not 版本比較
            if not 失敗:
                已變更 = 版本建立結果.結構已變更.__get__(來源, 版本建立結果)
                已變更類型 = type(已變更)
                失敗 = 已變更類型 is not bool
            if not 失敗:
                安全結果 = 版本建立結果(回執端點, 版本, 編號, 目前版本, 已變更)
        except (KeyboardInterrupt, SystemExit, GeneratorExit) as 錯誤:
            控制流.append(錯誤)
        except BaseException:
            失敗 = True
        if 控制流:
            安全結果 = None
            _重拋控制流(控制流)
        if 失敗:
            安全結果 = None
            _無效結果()
        return 安全結果
    finally:
        來源 = 端點識別碼 = 回執端點 = 版本 = 編號 = 目前版本 = 已變更 = None
        回執端點類型 = 版本類型 = 編號類型 = 目前版本類型 = 已變更類型 = None
        端點比較 = 版本比較 = 安全結果 = 錯誤 = None


def _無效結果() -> None:
    """以固定且不洩漏內容的服務失敗中止。"""
    raise HTTPException(status_code=500, detail="發布管理服務失敗") from None


def _是識別(值: Any) -> bool:
    """判斷值是否為有界 ASCII 識別碼。"""
    值類型 = 符合 = 結果 = None
    try:
        值類型 = type(值)
        if 值類型 is not str:
            結果 = False
        else:
            符合 = _識別格式.fullmatch(值)
            結果 = 符合 is not None
        return 結果
    finally:
        值 = 值類型 = 符合 = 結果 = None


def _是時間(值: Any) -> bool:
    """判斷值是否為非負有限時間。"""
    值類型 = 有限 = 結果 = None
    try:
        值類型 = type(值)
        if 值類型 not in (int, float):
            結果 = False
        else:
            有限 = math.isfinite(值)
            結果 = 有限 and 值 >= 0
        return 結果
    except (OverflowError, ValueError):
        結果 = False
        return 結果
    finally:
        值 = 值類型 = 有限 = 結果 = None


def _是金鑰(值: Any) -> bool:
    """判斷值是否符合初始 API 金鑰的基本界限。"""
    值類型 = 字元長度 = 去空白值 = 去空白相同 = 編碼值 = 位元長度 = 結果 = None
    try:
        值類型 = type(值)
        if 值類型 is not str:
            結果 = False
        else:
            字元長度 = str.__len__(值)
            if not 16 <= 字元長度 <= 512:
                結果 = False
            else:
                去空白值 = str.strip(值)
                去空白相同 = str.__eq__(去空白值, 值)
                if type(去空白相同) is not bool or not 去空白相同:
                    結果 = False
                else:
                    編碼值 = str.encode(值, "utf-8")
                    位元長度 = bytes.__len__(編碼值)
                    結果 = 位元長度 <= 2_048
        return 結果
    except UnicodeError:
        結果 = False
        return 結果
    finally:
        值 = 值類型 = 字元長度 = 去空白值 = 去空白相同 = None
        編碼值 = 位元長度 = 結果 = None


def _檢查JSON(值: JsonValue, 深度: int, 計數: list[int]) -> None:
    """檢查 Pydantic 已建立的 exact JSON tree 資源界限。"""
    計數[0] += 1
    if 計數[0] > 2_000 or 深度 > 24:
        raise ValueError("JSON 超出資源限制")
    if type(值) is str and len(值.encode("utf-8")) > 16_384:
        raise ValueError("JSON 字串過長")
    if type(值) is float and not math.isfinite(值):
        raise ValueError("JSON 數字無效")
    if type(值) is int and 值.bit_length() > 4096:
        raise ValueError("JSON 整數過大")
    if type(值) is list:
        for 項目 in 值:
            _檢查JSON(項目, 深度 + 1, 計數)
    elif type(值) is dict:
        if len(值) > 256:
            raise ValueError("JSON object 過大")
        for 鍵, 項目 in 值.items():
            if type(鍵) is not str or len(鍵.encode("utf-8")) > 256:
                raise ValueError("JSON key 無效")
            _檢查JSON(項目, 深度 + 1, 計數)


def _複製JSON物件(值: object) -> dict[str, JsonValue]:
    """單次有界遍歷建立快照，再重播來源描述器以拒絕並行變更。"""
    物件 = 安全快照 = 擷取 = 計數 = 結果 = 錯誤 = None
    控制流: list[BaseException] = []
    失敗 = False
    try:
        try:
            失敗 = type(值) is not dict
            if not 失敗:
                物件 = cast(dict[str, JsonValue], 值)
                擷取 = []
                計數 = [0]
                安全快照 = _建立JSON快照(物件, 0, 計數, 擷取)
                _重播JSON容器(擷取)
        except (KeyboardInterrupt, SystemExit, GeneratorExit) as 錯誤:
            控制流.append(錯誤)
        except BaseException as 錯誤:
            失敗 = True
        if 控制流:
            安全快照 = None
            _重拋控制流(控制流)
        if 失敗 or type(安全快照) is not dict:
            安全快照 = None
            _無效結果()
        結果 = cast(dict[str, JsonValue], 安全快照)
        return 結果
    finally:
        值 = 物件 = 安全快照 = 擷取 = 計數 = 結果 = 錯誤 = None


def _建立JSON快照(值, 深度: int, 計數: list[int], 擷取: list[tuple]) -> JsonValue:
    """以 built-in descriptors 驗證並複製，同時記錄來源形狀與子值。"""
    安全值 = 子項 = 鍵們 = 觀察值 = 部分 = 來源 = None
    長度 = 索引 = 鍵 = 值類型 = 編碼值 = 捕捉紀錄 = None
    鍵值對 = 鍵項迭代器 = 多餘項目 = None
    try:
        來源 = 值
        計數[0] += 1
        if 計數[0] > 2_000 or 深度 > 24:
            raise ValueError("JSON 超出資源限制")
        值類型 = type(來源)
        if 來源 is None or 值類型 is bool:
            安全值 = 來源
        elif 值類型 is str:
            編碼值 = 來源.encode("utf-8")
            if len(編碼值) > 16_384:
                raise ValueError("JSON 字串過長")
            安全值 = 來源
        elif 值類型 is int:
            if 來源.bit_length() > 4096:
                raise ValueError("JSON 整數過大")
            安全值 = 來源
        elif 值類型 is float:
            if not math.isfinite(來源):
                raise ValueError("JSON 數字無效")
            安全值 = 來源
        elif 值類型 is list:
            長度 = list.__len__(來源)
            if 長度 > 2_000:
                raise ValueError("JSON list 過大")
            部分 = []
            觀察值 = []
            索引 = 0
            while 索引 < 長度:
                子項 = list.__getitem__(來源, 索引)
                觀察值.append(子項)
                部分.append(_建立JSON快照(子項, 深度 + 1, 計數, 擷取))
                索引 += 1
            捕捉紀錄 = (來源, None, tuple(觀察值))
            擷取.append(捕捉紀錄)
            安全值 = 部分
        elif 值類型 is dict:
            長度 = dict.__len__(來源)
            if 長度 > 256:
                raise ValueError("JSON object 過大")
            部分 = {}
            鍵們 = []
            觀察值 = []
            鍵項迭代器 = iter(dict.items(來源))
            索引 = 0
            while 索引 < 長度:
                鍵值對 = next(鍵項迭代器, None)
                if type(鍵值對) is not tuple or tuple.__len__(鍵值對) != 2:
                    raise ValueError("JSON object 已變更")
                鍵 = tuple.__getitem__(鍵值對, 0)
                子項 = tuple.__getitem__(鍵值對, 1)
                值類型 = type(鍵)
                if 值類型 is not str:
                    raise ValueError("JSON key 無效")
                編碼值 = str.encode(鍵, "utf-8")
                if bytes.__len__(編碼值) > 256:
                    raise ValueError("JSON key 無效")
                鍵們.append(鍵)
                觀察值.append(子項)
                dict.__setitem__(部分, 鍵, _建立JSON快照(子項, 深度 + 1, 計數, 擷取))
                索引 += 1
            多餘項目 = next(鍵項迭代器, None)
            if 多餘項目 is not None:
                raise ValueError("JSON object 已變更")
            鍵們 = tuple(鍵們)
            捕捉紀錄 = (來源, 鍵們, tuple(觀察值))
            擷取.append(捕捉紀錄)
            安全值 = 部分
        else:
            raise ValueError("JSON 值無效")
        return 安全值
    finally:
        值 = 來源 = 安全值 = 子項 = 鍵們 = 觀察值 = 部分 = None
        計數 = 擷取 = 長度 = 索引 = 鍵 = 值類型 = 編碼值 = 捕捉紀錄 = None
        鍵值對 = 鍵項迭代器 = 多餘項目 = None


def _重播JSON容器(擷取: list[tuple]) -> None:
    """有界重讀每個來源容器，確認形狀、順序與每個子項未變。"""
    紀錄 = 來源 = 鍵們 = 觀察值 = 原值 = 現值 = 鍵 = 現鍵 = None
    紀錄索引 = 索引 = 現長度 = 擷取長度 = 鍵迭代器 = 鍵值對 = 多餘項目 = None
    現鍵們 = 現觀察值 = 編碼值 = 比較結果 = None
    try:
        紀錄索引 = 0
        while 紀錄索引 < len(擷取):
            紀錄 = 擷取[紀錄索引]
            來源, 鍵們, 觀察值 = 紀錄
            現長度 = list.__len__(來源) if 鍵們 is None else dict.__len__(來源)
            if 鍵們 is None:
                if 現長度 != len(觀察值):
                    raise ValueError("JSON list 已變更")
                索引 = 0
                while 索引 < len(觀察值):
                    原值 = 觀察值[索引]
                    現值 = list.__getitem__(來源, 索引)
                    比較結果 = _JSON子項相同(原值, 現值)
                    if 比較結果 is not True:
                        raise ValueError("JSON 容器已變更")
                    索引 += 1
            else:
                擷取長度 = tuple.__len__(鍵們)
                if 現長度 != 擷取長度:
                    raise ValueError("JSON object 已變更")
                if 現長度 > 256:
                    raise ValueError("JSON object 過大")
                現鍵們 = []
                現觀察值 = []
                鍵迭代器 = iter(dict.items(來源))
                索引 = 0
                while 索引 < 現長度:
                    鍵值對 = next(鍵迭代器, None)
                    if type(鍵值對) is not tuple or tuple.__len__(鍵值對) != 2:
                        raise ValueError("JSON object 已變更")
                    現鍵 = tuple.__getitem__(鍵值對, 0)
                    現值 = tuple.__getitem__(鍵值對, 1)
                    if type(現鍵) is not str:
                        raise ValueError("JSON key 無效")
                    編碼值 = str.encode(現鍵, "utf-8")
                    if bytes.__len__(編碼值) > 256:
                        raise ValueError("JSON key 無效")
                    現鍵們.append(現鍵)
                    現觀察值.append(現值)
                    索引 += 1
                多餘項目 = next(鍵迭代器, None)
                if 多餘項目 is not None or len(現鍵們) != len(鍵們):
                    raise ValueError("JSON object 已變更")
                索引 = 0
                while 索引 < len(鍵們):
                    鍵 = 鍵們[索引]
                    現鍵 = 現鍵們[索引]
                    if type(鍵) is not str or type(現鍵) is not str:
                        raise ValueError("JSON key 無效")
                    比較結果 = str.__eq__(鍵, 現鍵)
                    if type(比較結果) is not bool or not 比較結果:
                        raise ValueError("JSON object 已變更")
                    原值 = 觀察值[索引]
                    現值 = 現觀察值[索引]
                    if not _JSON子項相同(原值, 現值):
                        raise ValueError("JSON 容器已變更")
                    索引 += 1
            紀錄索引 += 1
    finally:
        擷取 = 紀錄 = 來源 = 鍵們 = 觀察值 = 原值 = 現值 = 鍵 = 現鍵 = None
        紀錄索引 = 索引 = 現長度 = 擷取長度 = 鍵迭代器 = 鍵值對 = 多餘項目 = None
        現鍵們 = 現觀察值 = 編碼值 = 比較結果 = None


def _JSON子項相同(原值, 現值) -> bool:
    """容器比較 identity；精確純量比較型別與不遺失的值。"""
    原類型 = 現類型 = 原浮點位元 = 現浮點位元 = 比較結果 = None
    try:
        原類型 = type(原值)
        現類型 = type(現值)
        if 原類型 in (list, dict):
            比較結果 = 現值 is 原值
        elif 現類型 is not 原類型:
            比較結果 = False
        elif 原類型 is float:
            原浮點位元 = struct.pack("!d", 原值)
            現浮點位元 = struct.pack("!d", 現值)
            比較結果 = 原浮點位元 == 現浮點位元
        else:
            比較結果 = 現值 == 原值
        return 比較結果
    finally:
        原值 = 現值 = 原類型 = 現類型 = None
        原浮點位元 = 現浮點位元 = 比較結果 = None
