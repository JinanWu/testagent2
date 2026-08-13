"""INV I05 有界 external invoke FastAPI adapter。

參數：由 ``建立外部呼叫路由`` 接收同步編排器與有界 transport 設定。
返回值：提供單一 ``POST /v1/endpoints/{slug}/invoke`` 的 router。
例外：request ordinary failures 固定映射公開錯誤；控制流程例外保留 identity。
副作用：讀取有界 HTTP body，並把完整同步 invocation 委派至 Starlette threadpool。
"""

from __future__ import annotations

import json
import math
import time
import uuid
from typing import Any, Callable, Protocol, cast

from fastapi import APIRouter, Path, Request
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool

from ..呼叫.錯誤映射 import 錯誤映射結果
from ..呼叫.編排器 import 呼叫成功結果

_控制流程 = (KeyboardInterrupt, SystemExit, GeneratorExit)
_短名最大位元組 = 128
_金鑰最大位元組 = 4096
_JSON最大深度 = 8
_JSON最大節點 = 1024
_JSON最大容器項目 = 128
_JSON最大字串位元組 = 32768
_工作階段識別最大位元組 = 128
class 外部呼叫介面(Protocol):
    """I04 transport-neutral orchestrator 的最小注入契約。"""

    def 執行(self, *參數: object) -> object:
        """執行一次已解析的外部呼叫；A09-03 將統一 optional session transport。"""
class _請求拒絕(Exception):
    """只攜帶固定 HTTP 分類，不攜帶 untrusted value。"""

    def __init__(self, 狀態碼: int, 錯誤碼: str, 訊息: str) -> None:
        """保存固定且不含 request data 的分類。"""
        self.狀態碼, self.錯誤碼, self.訊息 = 狀態碼, 錯誤碼, 訊息


_請求無效 = (400, "invalid_request", "請求格式無效。")
_本文過大 = (413, "request_too_large", "請求本文過大。")
_金鑰無效 = (401, "invalid_api_key", "API key 無效。")
_內部錯誤 = (500, "internal_error", "伺服器內部錯誤。")
def 建立外部呼叫路由(
    編排器: 外部呼叫介面, *,
    請求識別產生器: Callable[[], str] | None = None,
    時鐘: Callable[[], int | float] | None = None,
    本文最大位元組: int = 65536,
) -> APIRouter:
    """建立只含精確 POST invoke path 的可掛載 router。

    參數：同步編排器、可選 request ID／clock factories 與正整數 body 上限。
    返回值：具固定 prefix 與單一 invoke endpoint 的 ``APIRouter``。
    例外：body 上限不合約時拋 ``ValueError``，不呼叫任何注入。
    副作用：只建立 route closure；不執行編排器或讀取 request。
    """
    if type(本文最大位元組) is not int or 本文最大位元組 < 1:
        raise ValueError("本文上限不符合契約") from None
    產生識別 = 請求識別產生器 or (lambda: f"req_{uuid.uuid4().hex}")
    讀取時間 = 時鐘 or time.time
    路由器 = APIRouter(prefix="/v1/endpoints")

    @路由器.post(
        "/{slug}/invoke",
        openapi_extra={"requestBody": {"required": True, "content": {"application/json": {"schema": {
            "type": "object", "required": ["input"], "additionalProperties": False,
            "properties": {
                "input": {},
                "session_id": {"anyOf": [{"type": "string", "maxLength": 128}, {"type": "null"}]},
                "metadata": {"anyOf": [{"type": "object"}, {"type": "null"}]},
            }
        }}}}},
    )
    async def 呼叫端點(請求: Request, 路徑短名: str = Path(alias="slug")) -> JSONResponse:
        """在 materialize JSON 前限制 bytes，嚴格解析後才呼叫 I04。

        參數：目前 ASGI request 與 FastAPI 解析的 endpoint slug。
        返回值：I04 成功／錯誤投影，或固定 request/internal error JSON response。
        例外：KISG 控制流程 identity 原樣傳出；ordinary failures 不跨 transport 邊界。
        副作用：讀取一次有界 body；preflight 後在 threadpool 執行完整同步編排。
        """
        短名 = 路徑短名
        原始本文 = 本文 = 金鑰 = 請求識別 = None
        輸入 = 中繼資料 = 結果 = 投影 = None
        try:
            _驗證短名(短名)
            金鑰 = _讀取API金鑰(請求)
            原始本文 = await _讀取有界本文(請求, 本文最大位元組)
            本文 = _解析本文(原始本文)
            原始本文 = None
            輸入 = 本文["input"]
            工作階段識別 = 本文.get("session_id")
            中繼資料 = 本文.get("metadata")
            本文 = None
            _驗證工作階段識別(工作階段識別)
            _驗證有界JSON(輸入)
            if 中繼資料 is not None:
                _驗證有界JSON(中繼資料)
            請求識別 = 產生識別()
            現在 = 讀取時間()
            if (type(請求識別) is not str or not 請求識別
                    or len(請求識別.encode("utf-8")) > 128
                    or type(現在) not in (int, float) or not math.isfinite(float(現在)) or 現在 < 0):
                raise ValueError
            if 工作階段識別 is None:
                結果 = await run_in_threadpool(
                    編排器.執行, 短名, 請求識別, 金鑰, 輸入, 中繼資料, 現在,
                )
            else:
                結果 = await run_in_threadpool(
                    編排器.執行, 短名, 請求識別, 金鑰, 輸入, 中繼資料, 現在,
                    工作階段識別=工作階段識別,
                )
            金鑰 = 輸入 = 工作階段識別 = 中繼資料 = 請求識別 = 現在 = None
            投影 = _轉換結果(結果)
            結果 = None
            return JSONResponse(
                content=投影["envelope"], status_code=投影["status_code"], headers=投影["headers"],
            )
        except _請求拒絕 as 拒絕:
            請求 = 短名 = 原始本文 = 本文 = 金鑰 = 請求識別 = 輸入 = 中繼資料 = 結果 = 投影 = None
            return _固定錯誤回應(拒絕.狀態碼, 拒絕.錯誤碼, 拒絕.訊息)
        except _控制流程:
            請求 = 短名 = 原始本文 = 本文 = 金鑰 = 請求識別 = 輸入 = 中繼資料 = 結果 = 投影 = None
            raise
        except BaseException:
            請求 = 短名 = 原始本文 = 本文 = 金鑰 = 請求識別 = 輸入 = 中繼資料 = 結果 = 投影 = None
            return _固定錯誤回應(*_內部錯誤)

    return 路由器
def _驗證短名(短名: str) -> None:
    """限制 path scalar，避免把無界字串交給 adapter。"""
    try:
        合法 = type(短名) is str and bool(短名) and len(短名.encode("utf-8")) <= _短名最大位元組
    except UnicodeError:
        合法 = False
    if not 合法:
        raise _請求拒絕(*_請求無效)
def _讀取API金鑰(請求: Request) -> str:
    """只接受單一 Authorization: Bearer，scheme 大小寫不寬鬆。"""
    值列 = [值 for 鍵, 值 in 請求.scope["headers"] if 鍵.lower() == b"authorization"]
    if len(值列) != 1 or len(值列[0]) > _金鑰最大位元組 + 7:
        raise _請求拒絕(*_金鑰無效)
    try:
        值 = 值列[0].decode("ascii")
    except UnicodeError:
        raise _請求拒絕(*_金鑰無效) from None
    if not 值.startswith("Bearer ") or len(值) <= 7:
        raise _請求拒絕(*_金鑰無效)
    金鑰 = 值[7:]
    if len(金鑰.encode("utf-8")) > _金鑰最大位元組 or any(字元.isspace() for 字元 in 金鑰):
        金鑰 = None
        raise _請求拒絕(*_金鑰無效)
    return 金鑰
async def _讀取有界本文(請求: Request, 上限: int) -> bytes:
    """先 gate exact decimal Content-Length，仍逐 chunk 計算真實 bytes。"""
    長度列 = [值 for 鍵, 值 in 請求.scope["headers"] if 鍵.lower() == b"content-length"]
    if len(長度列) > 1:
        raise _請求拒絕(*_請求無效)
    if 長度列:
        try:
            長度文字 = 長度列[0].decode("ascii")
        except UnicodeError:
            raise _請求拒絕(*_請求無效) from None
        if not 長度文字 or len(長度文字) > 20 or not 長度文字.isdecimal():
            raise _請求拒絕(*_請求無效)
        try:
            宣告長度 = int(長度文字)
        except ValueError:
            raise _請求拒絕(*_請求無效) from None
        if 宣告長度 > 上限:
            raise _請求拒絕(*_本文過大)
    區塊列, 合計 = [], 0
    async for 區塊 in 請求.stream():
        合計 += len(區塊)
        if 合計 > 上限:
            區塊列 = None
            raise _請求拒絕(*_本文過大)
        區塊列.append(區塊)
    return b"".join(區塊列)
def _解析本文(原始本文: bytes) -> dict[str, Any]:
    """嚴格 UTF-8/JSON，拒絕 duplicate key、nonfinite 與非 object contract。"""
    def 建立物件(項目列: list[tuple[str, object]]) -> dict[str, object]:
        """拒絕同層重複 key 並建立 exact dict。"""
        輸出 = {}
        for 鍵, 值 in 項目列:
            if 鍵 in 輸出:
                raise ValueError
            輸出[鍵] = 值
        return 輸出

    def 解析浮點(文字: str) -> float:
        """拒絕 overflow 成非有限值的數字。"""
        值 = float(文字)
        if not math.isfinite(值):
            raise ValueError
        return 值

    try:
        結果 = json.loads(
            原始本文.decode("utf-8"), object_pairs_hook=建立物件,
            parse_float=解析浮點, parse_constant=lambda _: (_ for _ in ()).throw(ValueError()),
        )
    except (UnicodeError, json.JSONDecodeError, ValueError, OverflowError, RecursionError):
        原始本文 = None
        raise _請求拒絕(*_請求無效) from None
    合法欄位 = frozenset({"input", "session_id", "metadata"})
    if type(結果) is not dict or "input" not in 結果 or not frozenset(結果) <= 合法欄位:
        結果 = None
        raise _請求拒絕(*_請求無效)
    if "metadata" in 結果 and 結果["metadata"] is not None and type(結果["metadata"]) is not dict:
        結果 = None
        raise _請求拒絕(*_請求無效)
    return 結果
def _驗證工作階段識別(工作階段識別: object) -> None:
    """接受 null 或原樣安全識別值，不 trim、不正規化。"""
    if 工作階段識別 is None:
        return
    try:
        合法 = (
            type(工作階段識別) is str
            and bool(工作階段識別)
            and not 工作階段識別[0].isspace()
            and not 工作階段識別[-1].isspace()
            and not any(ord(字元) < 32 or 127 <= ord(字元) <= 159 for 字元 in 工作階段識別)
            and len(工作階段識別.encode("utf-8")) <= _工作階段識別最大位元組
        )
    except (UnicodeError, IndexError):
        合法 = False
    if not 合法:
        工作階段識別 = None
        raise _請求拒絕(*_請求無效)
def _驗證有界JSON(根: object) -> None:
    """限制 input/metadata 深度、節點、容器寬度與字串 bytes。"""
    計數 = [0]

    def 走訪(值: object, 深度: int) -> None:
        """逐節點施加共享資源限制。"""
        計數[0] += 1
        if 計數[0] > _JSON最大節點 or 深度 > _JSON最大深度:
            raise _請求拒絕(*_請求無效)
        類型 = type(值)
        if 值 is None or 類型 in (bool, int):
            return
        if 類型 is float:
            if math.isfinite(值):
                return
            raise _請求拒絕(*_請求無效)
        if 類型 is str:
            if len(值.encode("utf-8")) <= _JSON最大字串位元組:
                return
            raise _請求拒絕(*_請求無效)
        if 類型 not in (list, dict) or len(值) > _JSON最大容器項目:
            raise _請求拒絕(*_請求無效)
        項目列 = 值 if 類型 is list else 值.items()
        for 項目 in 項目列:
            if 類型 is dict:
                鍵, 子值 = 項目
                if type(鍵) is not str or len(鍵.encode("utf-8")) > _JSON最大字串位元組:
                    raise _請求拒絕(*_請求無效)
                走訪(子值, 深度 + 1)
            else:
                走訪(項目, 深度 + 1)

    走訪(根, 0)
def _轉換結果(結果: object) -> dict[str, Any]:
    """只承認 exact I01 result objects 並重新驗證其 fresh serialization。"""
    if type(結果) is 呼叫成功結果:
        投影 = 呼叫成功結果.轉為JSON(cast(呼叫成功結果, 結果))
    elif type(結果) is 錯誤映射結果:
        投影 = 錯誤映射結果.轉為JSON(cast(錯誤映射結果, 結果))
    else:
        raise ValueError
    if (type(投影) is not dict or tuple(投影) != ("status_code", "headers", "envelope")
            or type(投影["status_code"]) is not int or type(投影["headers"]) is not dict
            or type(投影["envelope"]) is not dict):
        raise ValueError
    信封 = 投影["envelope"]
    欄位 = ("ok", "endpoint", "invocation", "data", "usage", "warnings", "error")
    if len(信封) != len(欄位) or frozenset(信封) != frozenset(欄位):
        raise ValueError
    投影["envelope"] = {欄位名: 信封[欄位名] for 欄位名 in 欄位}
    return 投影


def _固定錯誤回應(狀態碼: int, 錯誤碼: str, 訊息: str) -> JSONResponse:
    """建立不含 request values、owner context 或額外 headers 的 fresh 七欄信封。"""
    return JSONResponse(status_code=狀態碼, content={
        "ok": False, "endpoint": None, "invocation": None, "data": None,
        "usage": None, "warnings": [],
        "error": {"code": 錯誤碼, "message": 訊息, "details": {}},
    })
