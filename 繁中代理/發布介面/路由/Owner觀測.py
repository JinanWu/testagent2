"""Owner-only 端點指標與安全 invocation HTTP adapter。"""

from __future__ import annotations

import inspect
import re
from typing import Annotated, Literal, cast

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request, Response
from fastapi.exceptions import RequestValidationError
from pydantic import BaseModel, ConfigDict, Field
from starlette.concurrency import run_in_threadpool

from ..治理.觀測契約 import (
    安全錯誤排行,
    定價版本成本,
    延遲摘要,
    指標查詢成功,
    每日端點指標,
    用量摘要,
    端點不可見結果,
    端點指標,
    端點觀測查詢服務,
    診斷查詢成功,
    診斷用量,
    診斷項目,
    診斷頁,
    觀測視窗,
)
from ..治理.觀測供應器 import 端點觀測游標錯誤
from ..網頁工作階段 import 網頁使用者

_控制流程 = (KeyboardInterrupt, SystemExit, GeneratorExit)
_識別碼格式 = re.compile(r"[A-Za-z0-9_-]{1,128}\Z")
_允許指標查詢 = frozenset(("window_seconds",))
_允許診斷查詢 = frozenset(("window_seconds", "limit", "cursor"))


class _嚴格模型(BaseModel):
    """拒絕額外欄位與型別coercion的HTTP邊界模型。"""
    model_config = ConfigDict(extra="forbid", strict=True)


class 擁有者觀測視窗(_嚴格模型):
    """Owner HTTP指標的固定UTC時間窗。"""
    開始時間: float = Field(alias="start_at", ge=0, allow_inf_nan=False)
    結束時間: float = Field(alias="end_at", ge=0, allow_inf_nan=False)
    時區: Literal["UTC"] = Field(alias="timezone")


class 擁有者延遲摘要(_嚴格模型):
    """Owner HTTP指標的安全延遲摘要。"""
    樣本數: int = Field(alias="sample_count", ge=0)
    平均: float | None = Field(alias="average", ge=0, allow_inf_nan=False)
    中位數: float | None = Field(alias="p50", ge=0, allow_inf_nan=False)
    第九十五百分位: float | None = Field(alias="p95", ge=0, allow_inf_nan=False)
    最大值: float | None = Field(alias="maximum", ge=0, allow_inf_nan=False)


class 擁有者用量摘要(_嚴格模型):
    """Owner HTTP指標的安全token用量摘要。"""
    樣本數: int = Field(alias="sample_count", ge=0)
    輸入Token數: int = Field(alias="input_tokens", ge=0)
    輸出Token數: int = Field(alias="output_tokens", ge=0)
    Token總數: int = Field(alias="total_tokens", ge=0)


class 擁有者定價成本(_嚴格模型):
    """Owner HTTP指標的歷史定價版本成本。"""
    定價版本: str = Field(alias="pricing_version", min_length=1, max_length=128, pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
    預估美元成本: str = Field(alias="estimated_cost_usd", min_length=1, max_length=66, pattern=r"^(?:0|[1-9][0-9]{0,36})(?:\.[0-9]{1,28})?$")


class 擁有者每日指標(_嚴格模型):
    """Owner HTTP指標的單一UTC日期bucket。"""
    日期: str = Field(alias="date", pattern=r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$")
    呼叫數: int = Field(alias="invocation_count", ge=0)
    終態數: int = Field(alias="terminal_count", ge=0)
    錯誤數: int = Field(alias="error_count", ge=0)
    用量Token總數: int = Field(alias="usage_total_tokens", ge=0)
    預估美元成本: str = Field(alias="estimated_cost_usd", min_length=1, max_length=66)


class 擁有者錯誤排行(_嚴格模型):
    """Owner HTTP指標的安全錯誤碼排行。"""
    錯誤碼: str = Field(alias="error_code", min_length=1, max_length=128, pattern=r"^[a-z][a-z0-9_.-]{0,127}$")
    次數: int = Field(alias="count", ge=1)


class 擁有者指標回應(_嚴格模型):
    """Owner metrics exact外部回應模型。"""
    端點識別碼: str = Field(alias="endpoint_id", min_length=1, max_length=128, pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
    視窗: 擁有者觀測視窗 = Field(alias="window")
    呼叫數: int = Field(alias="invocation_count", ge=0)
    終態數: int = Field(alias="terminal_count", ge=0)
    錯誤數: int = Field(alias="error_count", ge=0)
    錯誤率: float = Field(alias="error_rate", ge=0, le=1, allow_inf_nan=False)
    延遲毫秒: 擁有者延遲摘要 = Field(alias="latency_ms")
    用量: 擁有者用量摘要 = Field(alias="usage")
    預估美元成本: str = Field(alias="estimated_cost_usd", min_length=1, max_length=66)
    定價版本成本: list[擁有者定價成本] = Field(alias="cost_by_pricing_version", max_length=4096)
    每日: list[擁有者每日指標] = Field(alias="daily", max_length=31)
    錯誤排行: list[擁有者錯誤排行] = Field(alias="top_errors", max_length=10)


class 擁有者診斷用量(_嚴格模型):
    """Owner diagnostics單筆安全token總數。"""
    Token總數: int = Field(alias="total_tokens", ge=0)


class 擁有者診斷項目(_嚴格模型):
    """Owner diagnostics單筆安全allowlist。"""
    呼叫識別碼: str = Field(alias="invocation_id", min_length=1, max_length=128)
    請求識別碼: str = Field(alias="request_id", min_length=1, max_length=128)
    端點版本識別碼: str = Field(alias="endpoint_version_id", min_length=1, max_length=128)
    狀態: Literal["pending", "running", "succeeded", "failed", "rate_limited", "invalid_api_key"] = Field(alias="status")
    錯誤碼: str | None = Field(alias="error_code", default=None, max_length=512)
    Schema路徑: str | None = Field(alias="schema_path", default=None, max_length=512)
    延遲毫秒: float | None = Field(alias="latency_ms", default=None, ge=0, allow_inf_nan=False)
    用量: 擁有者診斷用量 | None = Field(alias="usage")
    工具名稱: list[str] = Field(alias="tool_names", max_length=4096)
    建立時間: float = Field(alias="created_at", ge=0, allow_inf_nan=False)
    完成時間: float | None = Field(alias="completed_at", default=None, ge=0, allow_inf_nan=False)
    遮蔽欄位: list[Literal["error_code", "schema_path"]] = Field(alias="redacted_fields", max_length=2)


class 擁有者診斷回應(_嚴格模型):
    """Owner diagnostics exact外部回應模型。"""
    項目: list[擁有者診斷項目] = Field(alias="items", max_length=100)
    下一頁游標: str | None = Field(alias="next_cursor", default=None, min_length=1, max_length=1024)


def 建立Owner觀測路由器(服務: 端點觀測查詢服務, 目前工作階段相依) -> APIRouter:
    """建立只由 canonical session 授權的 Owner GET routes；建構期零 I/O。"""
    if not callable(目前工作階段相依):
        raise ValueError("Owner觀測路由設定無效") from None
    try:
        相依參數數 = len(inspect.signature(目前工作階段相依).parameters)
    except (TypeError, ValueError):
        raise ValueError("Owner觀測路由設定無效") from None
    if 相依參數數 not in (0, 2):
        raise ValueError("Owner觀測路由設定無效") from None

    def 取得安全工作階段(請求: Request, 回應: Response) -> 網頁使用者:
        """重建 canonical dependency 錯誤，不繼承敵對 detail 或 headers。"""
        try:
            使用者 = 目前工作階段相依() if 相依參數數 == 0 else 目前工作階段相依(請求, 回應)
            if type(使用者) is not 網頁使用者:
                raise ValueError
            return 網頁使用者(使用者.識別碼, 使用者.使用者名稱, 使用者.角色)
        except HTTPException as 錯誤:
            if 錯誤.status_code == 401:
                raise HTTPException(401, "需要登入") from None
            if 錯誤.status_code == 503:
                raise HTTPException(500, "端點觀測不可取得") from None
            raise HTTPException(500, "端點觀測不可取得") from None
        except _控制流程:
            raise
        except BaseException:
            raise HTTPException(500, "端點觀測不可取得") from None

    setattr(取得安全工作階段, "__canonical_dependency__", 目前工作階段相依)
    路由器 = APIRouter(prefix="/api/published-endpoints")
    錯誤文件 = {
        401: _錯誤文件("需要登入"), 404: _錯誤文件("找不到發布端點"),
        500: _錯誤文件("端點觀測不可取得"),
    }

    @路由器.get(
        "/{endpoint_id}/metrics", operation_id="owner_get_endpoint_metrics",
        response_model=擁有者指標回應, responses=錯誤文件,
    )
    async def 讀取Owner指標(
        請求: Request,
        端點識別碼: Annotated[str, Path(alias="endpoint_id")],
        視窗文件: Annotated[str, Query(alias="window_seconds", json_schema_extra={"type": "integer", "minimum": 1, "maximum": 2592000})],
        使用者: 網頁使用者 = Depends(取得安全工作階段),
    ) -> dict[str, object]:
        """只以canonical session owner讀取單一端點安全指標。"""
        _驗證請求(請求, 端點識別碼, _允許指標查詢)
        視窗秒數 = _解析正整數(視窗文件, 2_592_000)
        try:
            結果 = await run_in_threadpool(
                服務.讀取端點指標,
                擁有者使用者識別碼=使用者.識別碼, 是否管理者=False,
                端點識別碼=端點識別碼, 視窗秒數=視窗秒數,
            )
            if type(結果) is 端點不可見結果:
                raise HTTPException(404, "找不到發布端點") from None
            if type(結果) is not 指標查詢成功:
                raise ValueError
            return _釋放指標(結果.指標)
        except HTTPException:
            raise
        except _控制流程:
            raise
        except BaseException:
            raise HTTPException(500, "端點觀測不可取得") from None

    @路由器.get(
        "/{endpoint_id}/diagnostics", operation_id="owner_list_endpoint_diagnostics",
        response_model=擁有者診斷回應, responses=錯誤文件,
    )
    async def 列出Owner診斷(
        請求: Request,
        端點識別碼: Annotated[str, Path(alias="endpoint_id")],
        視窗文件: Annotated[str, Query(alias="window_seconds", json_schema_extra={"type": "integer", "minimum": 1, "maximum": 2592000})],
        數量文件: Annotated[str, Query(alias="limit", json_schema_extra={"type": "integer", "minimum": 1, "maximum": 100})],
        游標文件: Annotated[str | None, Query(alias="cursor", json_schema_extra={"minLength": 1, "maxLength": 1024})] = None,
        使用者: 網頁使用者 = Depends(取得安全工作階段),
    ) -> dict[str, object]:
        """只以canonical session owner列出單一端點安全診斷。"""
        _驗證請求(請求, 端點識別碼, _允許診斷查詢)
        視窗秒數 = _解析正整數(視窗文件, 2_592_000)
        數量 = _解析正整數(數量文件, 100)
        if 游標文件 is not None and (type(游標文件) is not str or not 1 <= len(游標文件) <= 1024):
            _拋出驗證錯誤()
        try:
            結果 = await run_in_threadpool(
                服務.列出端點診斷,
                擁有者使用者識別碼=使用者.識別碼, 是否管理者=False,
                端點識別碼=端點識別碼, 視窗秒數=視窗秒數,
                數量上限=數量, 游標=游標文件,
            )
            if type(結果) is 端點不可見結果:
                raise HTTPException(404, "找不到發布端點") from None
            if type(結果) is not 診斷查詢成功:
                raise ValueError
            return _釋放診斷(結果.頁)
        except HTTPException:
            raise
        except 端點觀測游標錯誤:
            _拋出驗證錯誤()

        except _控制流程:
            raise
        except BaseException:
            raise HTTPException(500, "端點觀測不可取得") from None

    for 尾路徑 in ("/{endpoint_id}/metrics/", "/{endpoint_id}/diagnostics/"):
        路由器.add_api_route(
            尾路徑, lambda: (_ for _ in ()).throw(HTTPException(404, "找不到發布端點")),
            methods=["GET"], include_in_schema=False,
        )
    return 路由器


def _驗證請求(請求: Request, 端點識別碼: object, 允許: frozenset[str]) -> None:
    """以 route-owned fixed validator 拒絕 malformed path、unknown 與 duplicate query。"""
    if type(端點識別碼) is not str or _識別碼格式.fullmatch(端點識別碼) is None:
        _拋出驗證錯誤()
    配對 = list(請求.query_params.multi_items())
    名稱們 = [名稱 for 名稱, _ in 配對]
    if any(名稱 not in 允許 for 名稱 in 名稱們) or len(名稱們) != len(set(名稱們)):
        _拋出驗證錯誤()


def _解析正整數(值: object, 上限: int) -> int:
    """解析 canonical ASCII decimal query，拒絕 coercion、符號與前導零。"""
    if (type(值) is not str or not 值.isascii() or not 值.isdecimal()
            or 值.startswith("0") or not 1 <= len(值) <= 7):
        _拋出驗證錯誤()
    數量 = int(cast(str, 值))
    if not 1 <= 數量 <= 上限:
        _拋出驗證錯誤()
    return 數量


def _拋出驗證錯誤():
    """拋出不回顯敵對輸入的固定422 validation error。"""
    raise RequestValidationError([{
        "type": "value_error", "loc": ("request",), "msg": "Value error, invalid request",
        "input": None, "ctx": {"error": "invalid request"},
    }]) from None


def _錯誤文件(訊息: str) -> dict[str, object]:
    """建立只含固定detail enum的OpenAPI錯誤模型。"""
    return {"content": {"application/json": {"schema": {
        "type": "object", "additionalProperties": False, "required": ["detail"],
        "properties": {"detail": {"type": "string", "enum": [訊息]}},
    }}}}


def _釋放指標(候選: object) -> dict[str, object]:
    """在最後 HTTP seam 深度重建完整 metrics DTO 與 strict response tree。"""
    if type(候選) is not 端點指標:
        raise ValueError
    安全 = 端點指標(
        候選.endpoint_id, 觀測視窗(候選.window.start_at, 候選.window.end_at, 候選.window.timezone),
        候選.invocation_count, 候選.terminal_count, 候選.error_count, 候選.error_rate,
        延遲摘要(候選.latency_ms.sample_count, 候選.latency_ms.average, 候選.latency_ms.p50,
             候選.latency_ms.p95, 候選.latency_ms.maximum),
        用量摘要(候選.usage.sample_count, 候選.usage.input_tokens, 候選.usage.output_tokens, 候選.usage.total_tokens),
        候選.estimated_cost_usd,
        tuple(定價版本成本(項.pricing_version, 項.estimated_cost_usd) for 項 in 候選.cost_by_pricing_version),
        tuple(每日端點指標(項.date, 項.invocation_count, 項.terminal_count, 項.error_count,
                      項.usage_total_tokens, 項.estimated_cost_usd) for 項 in 候選.daily),
        tuple(安全錯誤排行(項.error_code, 項.count) for 項 in 候選.top_errors),
    )
    原始 = {
        "endpoint_id": 安全.endpoint_id,
        "window": {"start_at": float(安全.window.start_at), "end_at": float(安全.window.end_at), "timezone": "UTC"},
        "invocation_count": 安全.invocation_count, "terminal_count": 安全.terminal_count,
        "error_count": 安全.error_count, "error_rate": 安全.error_rate,
        "latency_ms": {"sample_count": 安全.latency_ms.sample_count, "average": 安全.latency_ms.average,
                       "p50": 安全.latency_ms.p50, "p95": 安全.latency_ms.p95, "maximum": 安全.latency_ms.maximum},
        "usage": {"sample_count": 安全.usage.sample_count, "input_tokens": 安全.usage.input_tokens,
                  "output_tokens": 安全.usage.output_tokens, "total_tokens": 安全.usage.total_tokens},
        "estimated_cost_usd": 安全.estimated_cost_usd,
        "cost_by_pricing_version": [{"pricing_version": 項.pricing_version,
                                     "estimated_cost_usd": 項.estimated_cost_usd} for 項 in 安全.cost_by_pricing_version],
        "daily": [{"date": 項.date, "invocation_count": 項.invocation_count, "terminal_count": 項.terminal_count,
                   "error_count": 項.error_count, "usage_total_tokens": 項.usage_total_tokens,
                   "estimated_cost_usd": 項.estimated_cost_usd} for 項 in 安全.daily],
        "top_errors": [{"error_code": 項.error_code, "count": 項.count} for 項 in 安全.top_errors],
    }
    return cast(dict[str, object], 擁有者指標回應.model_validate(原始, strict=True).model_dump(mode="json", by_alias=True))


def _釋放診斷(候選: object) -> dict[str, object]:
    """逐子項重建 owner-safe allowlist，拒絕同型別 post-construction poisoning。"""
    if type(候選) is not 診斷頁:
        raise ValueError
    項目們 = []
    for 項 in 候選.items:
        if type(項) is not 診斷項目:
            raise ValueError
        用量 = None if 項.usage is None else 診斷用量(項.usage.total_tokens)
        安全 = 診斷項目(項.invocation_id, 項.request_id, 項.endpoint_version_id, 項.status,
                    項.error_code, 項.schema_path, 項.latency_ms, 用量, tuple(項.tool_names),
                    項.created_at, 項.completed_at, tuple(項.redacted_fields))
        項目們.append({
            "invocation_id": 安全.invocation_id, "request_id": 安全.request_id,
            "endpoint_version_id": 安全.endpoint_version_id, "status": 安全.status,
            "error_code": 安全.error_code, "schema_path": 安全.schema_path,
            "latency_ms": 安全.latency_ms,
            "usage": None if 安全.usage is None else {"total_tokens": 安全.usage.total_tokens},
            "tool_names": list(安全.tool_names), "created_at": float(安全.created_at),
            "completed_at": None if 安全.completed_at is None else float(安全.completed_at),
            "redacted_fields": list(安全.redacted_fields),
        })
    原始 = {"items": 項目們, "next_cursor": 候選.next_cursor}
    return cast(dict[str, object], 擁有者診斷回應.model_validate(原始, strict=True).model_dump(mode="json", by_alias=True))


__all__ = ("建立Owner觀測路由器", "擁有者指標回應", "擁有者診斷回應")
