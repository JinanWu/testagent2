"""發布介面公開參照 DTO 與安全稽核 metadata 領域模型。"""

from __future__ import annotations

from dataclasses import asdict, dataclass
import math
import re
from types import MappingProxyType
from typing import Any, Mapping

from .嚴格JSON import 建立正規JSON, 解析嚴格JSON


JsonObject = dict[str, Any]
AuditMetadataScalar = bool | int | float | None
_AUDIT_METADATA_KEY_PATTERN = re.compile(r"[a-z][a-z0-9_]{0,63}")
_AUDIT_METADATA_SENSITIVE_KEY_PARTS = frozenset(
    {
        "raw",
        "plaintext",
        "secret",
        "token",
        "password",
        "api_key",
        "cipher",
        "master",
        "private",
        "filesystem",
        "path",
        "hash",
        "sha256",
        "full_hash",
        "schema_path",
    }
)
_AUDIT_SAFE_IDENTIFIER_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}")
_AUDIT_REFERENCE_SECRET_PREFIX_PATTERN = re.compile(r"(?i)(?:pk_|sk-|bearer)")
_AUDIT_REFERENCE_FULL_HEX_DIGEST_PATTERN = re.compile(r"(?i)[0-9a-f]{64}")
_AUDIT_ACTOR_TYPES = frozenset(("user", "service_account", "system"))


class AuditMetadataError(ValueError):
    """AuditMetadata 不符合公開安全契約時使用的固定錯誤型別。"""


class AuditReferenceError(ValueError):
    """稽核參照不符合公開安全契約時使用的固定錯誤型別。"""


class _公開DTO:
    """提供公開 DTO 共用的 JSON 輸出行為。"""

    def to_json(self) -> JsonObject:
        """回傳公開契約 JSON 物件。

        這個方法沒有參數，會依 dataclass 欄位順序產生新的 dict；不會修改實例，
        也不會觸發外部副作用。實例若不是 dataclass，會由 dataclasses.asdict
        拋出 TypeError。
        """
        return asdict(self)


@dataclass(frozen=True, init=False)
class AuditMetadata:
    """公開稽核 metadata 的安全快照。

    只接受 key 為受限格式的字串，value 為 bool、int、finite float 或 None。
    建構時會複製輸入 mapping，內部以 read-only mapping 保存，避免呼叫端後續
    mutation 影響公開契約輸出。
    """

    _資料: Mapping[str, AuditMetadataScalar]

    def __init__(self, metadata: Mapping[str, AuditMetadataScalar] | None = None) -> None:
        """驗證 metadata 並建立不可變 defensive snapshot。

        所有 validation failure 都轉為固定 AuditMetadataError 訊息，且在丟出前清除
        本 frame 的輸入、key 與 value locals，避免敏感字串被 production traceback
        locals 保留。
        """
        錯誤 = False
        項目列: tuple[tuple[Any, Any], ...] | None = None
        快照: dict[str, AuditMetadataScalar] | None = None
        已見鍵: set[str] | None = None
        鍵: Any = None
        值: Any = None
        try:
            if metadata is None:
                項目列 = ()
            elif isinstance(metadata, Mapping):
                項目列 = tuple(metadata.items())
            else:
                錯誤 = True
                項目列 = ()
        except Exception:
            object.__setattr__(self, "_資料", MappingProxyType({}))
            錯誤 = True

        if not 錯誤:
            try:
                assert 項目列 is not None
                快照 = {}
                已見鍵 = set()
                for 鍵, 值 in 項目列:
                    if not _稽核Metadata鍵合法(鍵) or not _稽核Metadata值合法(值):
                        錯誤 = True
                        break
                    if 鍵 in 已見鍵:
                        錯誤 = True
                        break
                    已見鍵.add(鍵)
                    快照[鍵] = 值
            except Exception:
                錯誤 = True

        if 錯誤:
            object.__setattr__(self, "_資料", MappingProxyType({}))
            metadata = 項目列 = 快照 = 已見鍵 = 鍵 = 值 = None
            raise AuditMetadataError("AuditMetadata 不符合公開契約")

        object.__setattr__(self, "_資料", MappingProxyType(快照 if 快照 is not None else {}))

    def to_json(self) -> dict[str, AuditMetadataScalar]:
        """回傳依原始順序建立的 ordinary new dict。

        此方法沒有參數與外部副作用；不會拋出contract例外，修改回傳dict也不會改變
        內部不可變快照。
        """
        return dict(self._資料)


@dataclass(frozen=True, init=False)
class AuditActorRef:
    """公開稽核事件 actor 的最小安全參照。

    actor_type 僅接受 exact str enum：user、service_account、system。user 與
    service_account 必須提供安全 actor_id；system 則必須使用 None，避免把系統動作
    誤綁到任意外部識別值。
    """

    actor_type: str | None
    actor_id: str | None

    def __init__(self, actor_type: str, actor_id: str | None) -> None:
        """驗證 actor 參照並建立 frozen DTO。

        所有 validation failure 都轉成固定 AuditReferenceError；錯誤路徑不保留原始
        輸入、raw secret、hash 或 path 片段於領域模型 frame locals。
        """
        錯誤 = False
        安全actor_type: str | None = None
        安全actor_id: str | None = None
        object.__setattr__(self, "actor_type", None)
        object.__setattr__(self, "actor_id", None)
        try:
            if type(actor_type) is not str or actor_type not in _AUDIT_ACTOR_TYPES:
                錯誤 = True
            elif actor_type == "system":
                if actor_id is not None:
                    錯誤 = True
                else:
                    安全actor_type = actor_type
                    安全actor_id = None
            elif _稽核安全識別值合法(actor_id):
                安全actor_type = actor_type
                安全actor_id = actor_id
            else:
                錯誤 = True
        except Exception:
            錯誤 = True

        if 錯誤:
            actor_type = actor_id = 安全actor_type = 安全actor_id = None
            raise AuditReferenceError("AuditActorRef 不符合公開契約")

        object.__setattr__(self, "actor_type", 安全actor_type)
        object.__setattr__(self, "actor_id", 安全actor_id)

    def to_json(self) -> JsonObject:
        """回傳固定鍵序 actor JSON，且每次都是 ordinary new dict。"""
        return {"actor_type": self.actor_type, "actor_id": self.actor_id}


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
                if 值 is not None and type(值) is not 型別:
                    raise ValueError("InvokeEnvelope DTO 不符合公開契約")

            if warnings is None:
                frozen_warnings = ()
            else:
                frozen_warnings = tuple(warnings)
                for warning in frozen_warnings:
                    if type(warning) is not PublishedWarning:
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
            frozen_warnings = frozen_data = 欄位 = 值 = 型別 = warning = None
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


def _稽核Metadata鍵合法(鍵: Any) -> bool:
    """檢查單一metadata key是否安全。

    參數可為任意值；回傳格式與敏感片段檢查結果，不拋出contract例外，也沒有
    外部副作用。
    """
    if type(鍵) is not str:
        return False
    if _AUDIT_METADATA_KEY_PATTERN.fullmatch(鍵) is None:
        return False
    return not any(敏感片段 in 鍵 for 敏感片段 in _AUDIT_METADATA_SENSITIVE_KEY_PARTS)


def _稽核Metadata值合法(值: Any) -> bool:
    """檢查單一metadata value是否為允許的exact scalar。

    參數可為任意值；回傳型別與有限數檢查結果，不拋出contract例外，也沒有
    外部副作用。
    """
    if 值 is None:
        return True
    if type(值) is bool:
        return True
    if type(值) is int:
        return True
    if type(值) is float:
        return math.isfinite(值)
    return False


def _稽核安全識別值合法(值: Any) -> bool:
    """檢查稽核參照 identifier 是否可公開保存。

    參數可為任意值；只回傳 bool，不拋出 contract 例外，也不保存輸入值。合法值
    需為 exact str、長度 1..128、符合安全字元白名單，並拒絕常見 raw secret、
    PEM marker、完整 64 hex digest、本機路徑與任何 whitespace。
    """
    if type(值) is not str:
        return False
    if _AUDIT_SAFE_IDENTIFIER_PATTERN.fullmatch(值) is None:
        return False
    return not _稽核參照字串含不安全內容(值)


def _稽核參照字串含不安全內容(值: str) -> bool:
    """回傳字串是否帶有 raw secret、digest、路徑或空白特徵。"""
    if any(字元.isspace() for 字元 in 值):
        return True
    if "/" in 值 or "\\" in 值 or 值.startswith("~"):
        return True
    if _AUDIT_REFERENCE_SECRET_PREFIX_PATTERN.match(值) is not None:
        return True
    return _AUDIT_REFERENCE_FULL_HEX_DIGEST_PATTERN.fullmatch(值) is not None


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
