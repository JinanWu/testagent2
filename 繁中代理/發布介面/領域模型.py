"""發布介面公開參照 DTO 領域模型。"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from types import MappingProxyType
from typing import Any, Mapping

from .嚴格JSON import 建立正規JSON, 解析嚴格JSON


JsonObject = dict[str, Any]

class _公開DTO:
    """提供公開 DTO 共用的 JSON 輸出行為。"""

    def to_json(self) -> JsonObject:
        """回傳公開契約 JSON 物件。

        這個方法沒有參數，會依 dataclass 欄位順序產生新的 dict；不會修改實例，
        也不會觸發外部副作用。實例若不是 dataclass，會由 dataclasses.asdict
        拋出 TypeError。
        """
        return asdict(self)


@dataclass(frozen=True)
class EndpointRef(_公開DTO):
    """公開端點版本的最小參照。"""

    id: str
    slug: str
    version: int


@dataclass(frozen=True)
class InvocationRef(_公開DTO):
    """公開呼叫紀錄的最小參照。"""

    id: str
    request_id: str
    session_id: str | None = None


@dataclass(frozen=True)
class PublishedUsage(_公開DTO):
    """公開回應中的用量摘要。"""

    total_tokens: int | None = None


@dataclass(frozen=True)
class PublishedWarning(_公開DTO):
    """公開回應中的非致命警告。"""

    code: str
    message: str


@dataclass(frozen=True)
class PublishedError(_公開DTO):
    """公開回應中的錯誤摘要。"""

    code: str
    message: str


@dataclass(frozen=True, init=False)
class InvokeEnvelope:
    """公開呼叫結果信封，固定成功與失敗回應的共同外部契約。"""

    ok: bool
    endpoint: EndpointRef | None
    invocation: InvocationRef | None
    data: Any
    usage: PublishedUsage | None
    warnings: tuple[PublishedWarning, ...]
    error: PublishedError | None

    def __init__(
        self,
        *,
        ok: bool,
        endpoint: EndpointRef | None,
        invocation: InvocationRef | None,
        data: Any = None,
        usage: PublishedUsage | None = None,
        warnings: tuple[PublishedWarning, ...] | list[PublishedWarning] | None = None,
        error: PublishedError | None = None,
    ) -> None:
        """驗證信封 invariant，並對 data 建立深層不可變 JSON 快照。"""
        try:
            if type(ok) is not bool:
                raise ValueError("InvokeEnvelope 狀態不符合公開契約")
            for 值, 型別 in (
                (endpoint, EndpointRef),
                (invocation, InvocationRef),
                (usage, PublishedUsage),
                (error, PublishedError),
            ):
                if 值 is not None and not isinstance(值, 型別):
                    raise ValueError("InvokeEnvelope DTO 不符合公開契約")

            if warnings is None:
                frozen_warnings = ()
            else:
                frozen_warnings = tuple(warnings)
                if not all(isinstance(warning, PublishedWarning) for warning in frozen_warnings):
                    raise ValueError("InvokeEnvelope 警告不符合公開契約")
            if ok:
                if endpoint is None or invocation is None or error is not None:
                    raise ValueError("InvokeEnvelope 成功狀態不符合公開契約")
                frozen_data = _建立不可變JSON快照(data)
            else:
                if error is None or data is not None or usage is not None:
                    raise ValueError("InvokeEnvelope 失敗狀態不符合公開契約")
                frozen_data = None

            for 欄位, 值 in (
                ("ok", ok),
                ("endpoint", endpoint),
                ("invocation", invocation),
                ("data", frozen_data),
                ("usage", usage),
                ("warnings", frozen_warnings),
                ("error", error),
            ):
                object.__setattr__(self, 欄位, 值)
        except Exception:
            for 欄位, 值 in (
                ("ok", False),
                ("endpoint", None),
                ("invocation", None),
                ("data", None),
                ("usage", None),
                ("warnings", ()),
                ("error", None),
            ):
                object.__setattr__(self, 欄位, 值)
            ok = endpoint = invocation = data = usage = warnings = error = None
            frozen_warnings = frozen_data = 欄位 = 值 = 型別 = None
            raise

    def to_json(self) -> JsonObject:
        """回傳固定鍵序且只含 ordinary JSON container 的公開信封。"""
        return {
            "ok": self.ok,
            "endpoint": None if self.endpoint is None else self.endpoint.to_json(),
            "invocation": None if self.invocation is None else self.invocation.to_json(),
            "data": _解凍JSON值(self.data),
            "usage": None if self.usage is None else self.usage.to_json(),
            "warnings": [warning.to_json() for warning in self.warnings],
            "error": None if self.error is None else self.error.to_json(),
        }


@dataclass(frozen=True)
class ServiceAccountSnapshotRef(_公開DTO):
    """服務帳號權限快照的公開參照。"""

    service_account_id: str
    endpoint_version_id: str
    permission_snapshot_digest: str


def _建立不可變JSON快照(資料: Any) -> Any:
    """重用嚴格 JSON 審核後建立深層不可變快照。"""
    正規文字: str | None = None
    解析結果: Any = None
    try:
        正規文字 = 建立正規JSON(資料)
        解析結果 = 解析嚴格JSON(正規文字)
        return _凍結JSON值(解析結果)
    except Exception:
        資料 = 正規文字 = 解析結果 = None
        raise


def _凍結JSON值(值: Any) -> Any:
    """將已審核 JSON value 轉為 tuple 與 read-only mapping 組成的快照。"""
    if isinstance(值, list):
        return tuple(_凍結JSON值(項目) for 項目 in 值)
    if isinstance(值, dict):
        return MappingProxyType({鍵: _凍結JSON值(項目) for 鍵, 項目 in 值.items()})
    return 值


def _解凍JSON值(值: Any) -> Any:
    """將內部不可變 JSON 快照轉回 ordinary dict、list 與 scalar。"""
    if isinstance(值, tuple):
        return [_解凍JSON值(項目) for 項目 in 值]
    if isinstance(值, Mapping):
        return {鍵: _解凍JSON值(項目) for 鍵, 項目 in 值.items()}
    return 值
