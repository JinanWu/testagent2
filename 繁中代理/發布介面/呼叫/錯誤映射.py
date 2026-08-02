"""將內部呼叫失敗轉成穩定且與傳輸層無關的公開結果。"""

from __future__ import annotations

from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any

from ..契約 import 建立失敗信封
from ..領域模型 import EndpointRef, InvocationRef, InvokeEnvelope, PublishedError


class 錯誤映射錯誤(RuntimeError):
    """輸入無法安全映射時使用的固定公開錯誤。"""


_錯誤契約 = {
    "endpoint_not_found": (404, "找不到 endpoint slug。"),
    "invalid_api_key": (401, "API key 無效。"),
    "api_key_expired": (401, "API key 已過期。"),
    "endpoint_disabled": (403, "Endpoint 已停用。"),
    "endpoint_archived": (410, "Endpoint 已封存。"),
    "input_schema_invalid": (422, "Input 不符合 schema。"),
    "model_output_schema_invalid": (502, "模型輸出不符合 response schema。"),
    "rate_limit_exceeded": (429, "呼叫頻率超過限制。"),
    "model_timeout": (504, "模型供應商逾時。"),
    "tool_execution_failed": (502, "工具執行失敗。"),
    "tool_timeout": (504, "工具執行逾時。"),
    "endpoint_misconfigured": (500, "Endpoint 設定錯誤。"),
    "internal_error": (500, "伺服器內部錯誤。"),
}
_控制流程 = (KeyboardInterrupt, SystemExit, GeneratorExit)


@dataclass(frozen=True, init=False)
class 錯誤映射結果:
    """保存 HTTP 語意、不可變標頭與 exact 公開信封的傳輸中立結果。"""

    __annotations__["status_code"] = int
    locals()["status_code"] = field()
    __annotations__["envelope"] = InvokeEnvelope
    locals()["envelope"] = field()
    __annotations__["headers"] = Any
    locals()["headers"] = field(repr=False)
    _標頭項目: tuple[tuple[str, str], ...] = field(repr=False)

    def __init__(
        self,
        狀態碼: int,
        信封: InvokeEnvelope,
        標頭: dict[str, str] | None = None,
    ) -> None:
        """驗證狀態、信封與標頭並建立 detached frozen snapshot。"""
        是否失敗 = False
        安全項目: tuple[tuple[str, str], ...] = ()
        安全信封 = None
        原始標頭: Any = None
        捕捉錯誤: BaseException | None = None
        錯誤碼 = 錯誤資料 = 端點值 = 呼叫值 = None
        安全端點 = 安全呼叫 = 公開錯誤 = None
        安全細節: dict[str, Any] = {}
        預期標頭: dict[str, str] = {}
        try:
            if type(狀態碼) is not int or type(信封) is not InvokeEnvelope:
                是否失敗 = True
            elif 信封.ok is not False or 信封.data is not None or 信封.usage is not None:
                是否失敗 = True
            elif type(信封.warnings) is not tuple or 信封.warnings:
                是否失敗 = True
            elif type(信封.error) is not PublishedError:
                是否失敗 = True
            elif 標頭 is not None and type(標頭) is not dict:
                是否失敗 = True
            else:
                錯誤資料 = PublishedError.to_json(信封.error)
                錯誤碼 = 錯誤資料["code"]
                原始標頭 = {} if 標頭 is None else 標頭
                安全項目 = tuple(原始標頭.items())
                if any(type(鍵) is not str or type(值) is not str for 鍵, 值 in 安全項目):
                    是否失敗 = True
                elif type(錯誤碼) is not str or 錯誤碼 not in _錯誤契約:
                    是否失敗 = True
                elif 狀態碼 != _錯誤契約[錯誤碼][0] or 錯誤資料["message"] != _錯誤契約[錯誤碼][1]:
                    是否失敗 = True
                elif 錯誤碼 == "endpoint_not_found":
                    是否失敗 = 信封.endpoint is not None or 信封.invocation is not None
                elif type(信封.endpoint) is not EndpointRef or type(信封.invocation) is not InvocationRef:
                    是否失敗 = True
                else:
                    端點值 = (信封.endpoint.id, 信封.endpoint.slug, 信封.endpoint.version)
                    呼叫值 = (信封.invocation.id, 信封.invocation.request_id, 信封.invocation.session_id)
                    if not _參照純量合法(端點值, 呼叫值):
                        是否失敗 = True
                    else:
                        安全端點 = EndpointRef(*端點值)
                        安全呼叫 = InvocationRef(*呼叫值)
                if not 是否失敗 and 錯誤碼 == "rate_limit_exceeded":
                    細節失敗, 安全細節, 預期標頭 = _驗證限流細節(錯誤資料["details"])
                    是否失敗 = 細節失敗 or dict(安全項目) != 預期標頭
                elif not 是否失敗:
                    安全細節 = 錯誤資料["details"]
                    是否失敗 = type(安全細節) is not dict or bool(安全細節) or bool(安全項目)
                if not 是否失敗:
                    公開錯誤 = PublishedError(錯誤碼, _錯誤契約[錯誤碼][1], 安全細節)
                    安全信封 = 建立失敗信封(公開錯誤, endpoint=安全端點, invocation=安全呼叫)
        except BaseException as 捕捉錯誤:
            if isinstance(捕捉錯誤, _控制流程):
                狀態碼 = 信封 = 標頭 = 原始標頭 = 安全項目 = 安全信封 = None  # type: ignore[assignment]
                錯誤碼 = 錯誤資料 = 端點值 = 呼叫值 = None
                安全端點 = 安全呼叫 = 公開錯誤 = None
                安全細節 = 預期標頭 = None  # type: ignore[assignment]
                _清理控制流程(捕捉錯誤)
                raise
            是否失敗 = True
        if 是否失敗 or type(安全信封) is not InvokeEnvelope:
            狀態碼 = 信封 = 標頭 = 原始標頭 = 安全項目 = 安全信封 = 捕捉錯誤 = None  # type: ignore[assignment]
            錯誤碼 = 錯誤資料 = 端點值 = 呼叫值 = None
            安全端點 = 安全呼叫 = 公開錯誤 = None
            安全細節 = 預期標頭 = None  # type: ignore[assignment]
            raise 錯誤映射錯誤("錯誤映射失敗") from None
        object.__setattr__(self, "status_code", 狀態碼)
        object.__setattr__(self, "envelope", 安全信封)
        object.__setattr__(self, "headers", MappingProxyType(dict(安全項目)))
        object.__setattr__(self, "_標頭項目", 安全項目)

    def 轉為JSON(self) -> dict[str, Any]:
        """重新驗證own state後，每次建立fresh ordinary transport snapshot。"""
        狀態碼 = 信封 = 標頭項目 = 安全結果 = 輸出 = None
        控制流程: BaseException | None = None
        是否失敗 = False
        try:
            狀態碼 = self.status_code
            信封 = self.envelope
            標頭項目 = self._標頭項目
            if (
                type(標頭項目) is not tuple
                or any(
                    type(項目) is not tuple
                    or len(項目) != 2
                    or type(項目[0]) is not str
                    or type(項目[1]) is not str
                    for 項目 in 標頭項目
                )
            ):
                是否失敗 = True
            else:
                安全結果 = 錯誤映射結果(狀態碼, 信封, dict(標頭項目))
                輸出 = {
                    "status_code": 安全結果.status_code,
                    "headers": dict(安全結果._標頭項目),
                    "envelope": InvokeEnvelope.to_json(安全結果.envelope),
                }
        except _控制流程 as 捕捉控制:
            _清理控制流程(捕捉控制)
            控制流程 = 捕捉控制
            捕捉控制 = None
        except BaseException:
            是否失敗 = True
        self = 狀態碼 = 信封 = 標頭項目 = 安全結果 = None
        if 控制流程 is not None:
            控制盒 = [控制流程]
            控制流程 = None
            _重拋控制流程(控制盒.pop())
        if 是否失敗 or type(輸出) is not dict:
            輸出 = None
            raise 錯誤映射錯誤("錯誤映射失敗") from None
        return 輸出


setattr(錯誤映射結果, "to_json", 錯誤映射結果.轉為JSON)


def 映射呼叫錯誤(*位置參數: Any, **命名參數: Any) -> 錯誤映射結果:
    """只依 allowlist code 建立固定訊息、狀態、細節與失敗信封。"""
    未提供 = object()
    是否失敗 = False
    控制流程: BaseException | None = None
    結果 = None
    錯誤碼 = 位置參數[0] if 位置參數 else 命名參數.get("code", 未提供)
    try:
        if (
            len(位置參數) > 1
            or any(名稱 not in {"code", "endpoint", "invocation", "details"} for 名稱 in 命名參數)
            or (位置參數 and "code" in 命名參數)
        ):
            是否失敗 = True
        else:
            結果 = _建立錯誤映射結果(
                錯誤碼,
                端點=命名參數.get("endpoint"),
                呼叫=命名參數.get("invocation"),
                細節=命名參數.get("details"),
            )
    except _控制流程 as 捕捉控制:
        控制流程 = 捕捉控制
        _清理控制流程(控制流程)
    except BaseException:
        是否失敗 = True
    位置參數 = ()
    命名參數 = {}
    錯誤碼 = 未提供 = None
    if 控制流程 is not None:
        控制盒 = [控制流程]
        控制流程 = 捕捉控制 = None
        _重拋控制流程(控制盒.pop())
    if 是否失敗 or type(結果) is not 錯誤映射結果:
        結果 = None
        raise 錯誤映射錯誤("錯誤映射失敗") from None
    return 結果


def _建立錯誤映射結果(
    錯誤碼: Any,
    *,
    端點: Any,
    呼叫: Any,
    細節: Any,
) -> 錯誤映射結果:
    """驗證已解析參數並建立安全映射結果。"""
    是否失敗 = False
    控制流程: BaseException | None = None
    安全端點 = 安全呼叫 = None
    安全細節: dict[str, Any] = {}
    標頭: dict[str, str] = {}
    結果 = None
    端點值 = 呼叫值 = None
    狀態碼 = 訊息 = 公開錯誤 = 信封 = None
    捕捉控制: BaseException | None = None
    try:
        if type(錯誤碼) is not str or 錯誤碼 not in _錯誤契約:
            是否失敗 = True
        elif 錯誤碼 == "endpoint_not_found":
            if 端點 is not None or 呼叫 is not None:
                是否失敗 = True
        elif type(端點) is not EndpointRef or type(呼叫) is not InvocationRef:
            是否失敗 = True
        else:
            端點值 = (端點.id, 端點.slug, 端點.version)
            呼叫值 = (呼叫.id, 呼叫.request_id, 呼叫.session_id)
            if not _參照純量合法(端點值, 呼叫值):
                是否失敗 = True
            else:
                安全端點 = EndpointRef(*端點值)
                安全呼叫 = InvocationRef(*呼叫值)
        if not 是否失敗:
            if type(細節) is not dict and 細節 is not None:
                是否失敗 = True
            elif 錯誤碼 == "rate_limit_exceeded":
                是否失敗, 安全細節, 標頭 = _驗證限流細節(細節)
            elif 細節 is not None and len(細節) != 0:
                是否失敗 = True
        if not 是否失敗:
            狀態碼, 訊息 = _錯誤契約[錯誤碼]
            公開錯誤 = PublishedError(錯誤碼, 訊息, 安全細節)
            信封 = 建立失敗信封(公開錯誤, endpoint=安全端點, invocation=安全呼叫)
            結果 = 錯誤映射結果(狀態碼, 信封, 標頭)
    except _控制流程 as 捕捉控制:
        控制流程 = 捕捉控制
        _清理控制流程(控制流程)
    except BaseException:
        是否失敗 = True
    if 是否失敗 or 控制流程 is not None or type(結果) is not 錯誤映射結果:
        錯誤碼 = 端點 = 呼叫 = 細節 = None
        安全端點 = 安全呼叫 = 安全細節 = 標頭 = 結果 = None  # type: ignore[assignment]
        端點值 = 呼叫值 = 狀態碼 = 訊息 = 公開錯誤 = 信封 = None
        if 控制流程 is not None:
            控制盒 = [控制流程]
            控制流程 = 捕捉控制 = None
            _重拋控制流程(控制盒.pop())
        raise 錯誤映射錯誤("錯誤映射失敗") from None
    return 結果


def _參照純量合法(端點值: tuple[Any, ...], 呼叫值: tuple[Any, ...]) -> bool:
    """驗證重建公開參照所需的 exact scalar。"""
    return (
        type(端點值[0]) is str and bool(端點值[0])
        and type(端點值[1]) is str and bool(端點值[1])
        and type(端點值[2]) is int and 端點值[2] >= 1
        and type(呼叫值[0]) is str and bool(呼叫值[0])
        and type(呼叫值[1]) is str and bool(呼叫值[1])
        and (呼叫值[2] is None or type(呼叫值[2]) is str)
    )


def _驗證限流細節(細節: Any) -> tuple[bool, dict[str, Any], dict[str, str]]:
    """驗證限流專用 exact dict 並建立 detached details 與標頭。"""
    if type(細節) is not dict:
        return True, {}, {}
    鍵集合 = tuple(細節)
    if any(type(鍵) is not str for 鍵 in 鍵集合):
        return True, {}, {}
    if len(鍵集合) != 2 or frozenset(鍵集合) != {"scope", "retry_after_seconds"}:
        return True, {}, {}
    範圍 = 細節["scope"]
    秒數 = 細節["retry_after_seconds"]
    if type(範圍) is not str or 範圍 not in ("endpoint", "credential"):
        return True, {}, {}
    if type(秒數) is not int or not 0 <= 秒數 <= 60:
        return True, {}, {}
    return False, {"scope": 範圍, "retry_after_seconds": 秒數}, {"Retry-After": str(秒數)}


def _重拋控制流程(控制: BaseException) -> None:
    """移除舊 traceback 並保留控制流程 identity 與 args。"""
    try:
        BaseException.__setattr__(控制, "__traceback__", None)
        raise 控制
    except _控制流程:
        控制 = None  # type: ignore[assignment]
        raise


def _清理控制流程(控制: BaseException) -> None:
    """不呼叫敵對subclass override地清除cause/context。"""
    BaseException.__setattr__(控制, "__cause__", None)
    BaseException.__setattr__(控制, "__context__", None)
    BaseException.__setattr__(控制, "__suppress_context__", True)
