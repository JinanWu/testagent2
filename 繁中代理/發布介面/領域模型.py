"""發布介面公開參照 DTO 與安全稽核 metadata 領域模型。"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
import math
import re
from types import MappingProxyType
from typing import Any, Mapping
import unicodedata

from .嚴格JSON import 建立正規JSON, 解析嚴格JSON


JsonObject = dict[str, Any]
AuditMetadataScalar = bool | int | float | None
_AUDIT_METADATA_KEY_PATTERN = re.compile(r"[a-z][a-z0-9_]{0,63}")
_AUDIT_RESOURCE_TYPE_PATTERN = re.compile(r"[a-z][a-z0-9_.]{0,63}")
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
_AUDIT_REFERENCE_SECRET_PREFIX_PATTERN = re.compile(r"(?i)(?:pk_|sk[-_]|bearer)")
_AUDIT_REFERENCE_FULL_HEX_DIGEST_PATTERN = re.compile(r"(?i)[0-9a-f]{64}")
_AUDIT_ACTOR_TYPES = frozenset(("user", "service_account", "system"))


class AuditMetadataError(ValueError):
    """AuditMetadata 不符合公開安全契約時使用的固定錯誤型別。"""


class AuditReferenceError(ValueError):
    """稽核參照不符合公開安全契約時使用的固定錯誤型別。"""


class AuditEventError(ValueError):
    """AuditEvent 不符合公開安全契約時使用的固定錯誤型別。"""


class AuditReceiptError(ValueError):
    """AuditAppendReceipt 不符合公開安全契約時使用的固定錯誤型別。"""


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


@dataclass(frozen=True, init=False)
class AuditResourceRef:
    """公開稽核事件 resource 的最小安全參照。

    resource_type 是小寫資源分類 code，只接受 ``[a-z][a-z0-9_.]{0,63}``；
    resource_id 重用稽核參照安全 identifier 規則。兩個欄位都必須是 exact str，
    且都拒絕 raw secret、完整 digest 與本機路徑特徵。
    """

    resource_type: str | None
    resource_id: str | None

    def __init__(self, resource_type: str, resource_id: str) -> None:
        """驗證 resource 參照並建立 frozen DTO。

        所有 validation failure 都轉成固定 AuditReferenceError；錯誤路徑先清除
        原始輸入與 safe locals，再於 except 區塊外丟出，避免 production traceback
        locals 保留 secret、digest、path 或測試 marker。
        """
        錯誤 = False
        安全resource_type: str | None = None
        安全resource_id: str | None = None
        object.__setattr__(self, "resource_type", None)
        object.__setattr__(self, "resource_id", None)
        try:
            if _稽核資源型別合法(resource_type) and _稽核安全識別值合法(resource_id):
                安全resource_type = resource_type
                安全resource_id = resource_id
            else:
                錯誤 = True
        except Exception:
            錯誤 = True

        if 錯誤:
            resource_type = resource_id = 安全resource_type = 安全resource_id = None
            raise AuditReferenceError("AuditResourceRef 不符合公開契約")

        object.__setattr__(self, "resource_type", 安全resource_type)
        object.__setattr__(self, "resource_id", 安全resource_id)

    def to_json(self) -> JsonObject:
        """回傳固定鍵序 resource JSON，且每次都是 ordinary new dict。"""
        return {"resource_type": self.resource_type, "resource_id": self.resource_id}


@dataclass(frozen=True, init=False)
class AuditEvent:
    """公開稽核事件的安全不可變快照。

    所有識別值都使用稽核安全 identifier 規則；occurred_at 只接受 exact int/float
    且建構時正規化為 float。actor、resource 與 metadata 必須是 exact DTO 型別，
    避免 subclass 追加欄位後透過公開 JSON 外洩。
    """

    event_id: str
    occurred_at: float
    action: str
    outcome: str
    actor: AuditActorRef
    resource: AuditResourceRef
    request_id: str | None
    endpoint_id: str | None
    invocation_id: str | None
    metadata: AuditMetadata

    def __init__(
        self,
        *,
        event_id: str,
        occurred_at: int | float,
        action: str,
        outcome: str,
        actor: AuditActorRef,
        resource: AuditResourceRef,
        request_id: str | None = None,
        endpoint_id: str | None = None,
        invocation_id: str | None = None,
        metadata: AuditMetadata,
    ) -> None:
        """驗證稽核事件並建立 frozen DTO。

        任一 validation failure 都轉成固定 AuditEventError。錯誤路徑會先清除本
        frame 的輸入與暫存安全值，再於 except 區塊外丟出，避免 production
        traceback locals 留下 marker、secret、digest 或 path。
        """
        錯誤 = False
        安全event_id: str | None = None
        安全occurred_at: float = 0.0
        安全action: str | None = None
        安全outcome: str | None = None
        安全actor: AuditActorRef | None = None
        安全resource: AuditResourceRef | None = None
        安全request_id: str | None = None
        安全endpoint_id: str | None = None
        安全invocation_id: str | None = None
        安全metadata: AuditMetadata | None = None
        for 欄位, 值 in (
            ("event_id", None),
            ("occurred_at", 0.0),
            ("action", None),
            ("outcome", None),
            ("actor", None),
            ("resource", None),
            ("request_id", None),
            ("endpoint_id", None),
            ("invocation_id", None),
            ("metadata", None),
        ):
            object.__setattr__(self, 欄位, 值)
        try:
            if not _稽核安全識別值合法(event_id):
                錯誤 = True
            elif type(occurred_at) not in (int, float):
                錯誤 = True
            elif not math.isfinite(occurred_at) or occurred_at < 0:
                錯誤 = True
            elif type(occurred_at) is int and int(float(occurred_at)) != occurred_at:
                錯誤 = True
            elif not _稽核資源型別合法(action):
                錯誤 = True
            elif type(outcome) is not str or outcome not in ("success", "denied", "failed"):
                錯誤 = True
            elif type(actor) is not AuditActorRef:
                錯誤 = True
            elif type(resource) is not AuditResourceRef:
                錯誤 = True
            elif not _稽核可空安全識別值合法(request_id):
                錯誤 = True
            elif not _稽核可空安全識別值合法(endpoint_id):
                錯誤 = True
            elif not _稽核可空安全識別值合法(invocation_id):
                錯誤 = True
            elif type(metadata) is not AuditMetadata:
                錯誤 = True
            else:
                安全event_id = event_id
                安全occurred_at = float(occurred_at)
                安全action = action
                安全outcome = outcome
                安全actor = actor
                安全resource = resource
                安全request_id = request_id
                安全endpoint_id = endpoint_id
                安全invocation_id = invocation_id
                安全metadata = metadata
        except Exception:
            錯誤 = True

        if 錯誤:
            event_id = occurred_at = action = outcome = actor = resource = None
            request_id = endpoint_id = invocation_id = metadata = None
            安全occurred_at = None
            安全event_id = 安全action = 安全outcome = 安全actor = 安全resource = None
            安全request_id = 安全endpoint_id = 安全invocation_id = 安全metadata = None
            欄位 = 值 = None
            raise AuditEventError("AuditEvent 不符合公開契約") from None

        for 欄位, 值 in (
            ("event_id", 安全event_id),
            ("occurred_at", 安全occurred_at),
            ("action", 安全action),
            ("outcome", 安全outcome),
            ("actor", 安全actor),
            ("resource", 安全resource),
            ("request_id", 安全request_id),
            ("endpoint_id", 安全endpoint_id),
            ("invocation_id", 安全invocation_id),
            ("metadata", 安全metadata),
        ):
            object.__setattr__(self, 欄位, 值)

    def to_json(self) -> JsonObject:
        """回傳固定欄位順序與 ordinary nested dict 的稽核事件 JSON。"""
        assert self.actor is not None
        assert self.resource is not None
        assert self.metadata is not None
        return {
            "event_id": self.event_id,
            "occurred_at": self.occurred_at,
            "action": self.action,
            "outcome": self.outcome,
            "actor": self.actor.to_json(),
            "resource": self.resource.to_json(),
            "request_id": self.request_id,
            "endpoint_id": self.endpoint_id,
            "invocation_id": self.invocation_id,
            "metadata": self.metadata.to_json(),
        }


@dataclass(frozen=True, init=False)
class AuditAppendReceipt:
    """稽核事件 append 結果的公開安全 receipt。

    event_id 重用稽核安全 identifier 規則；committed 僅接受 exact bool。
    committed=True 表示事件已持久提交，sequence 必須是 exact int 且落在
    1..2**63-1；committed=False 表示未提交，sequence 必須為 None。
    """

    event_id: str
    committed: bool
    sequence: int | None

    def __init__(self, event_id: str, committed: bool, sequence: int | None) -> None:
        """驗證 receipt 並建立 frozen DTO。

        任一 validation failure 都轉成固定 AuditReceiptError。錯誤路徑會先把
        self 放入安全預設值，再清除原始輸入與安全暫存值，避免 production
        traceback locals 保留 raw secret、digest、path 或測試 marker。
        """
        錯誤 = False
        安全event_id: str | None = None
        安全committed: bool = False
        安全sequence: int | None = None
        object.__setattr__(self, "event_id", None)
        object.__setattr__(self, "committed", False)
        object.__setattr__(self, "sequence", None)
        try:
            if not _稽核安全識別值合法(event_id):
                錯誤 = True
            elif type(committed) is not bool:
                錯誤 = True
            elif committed:
                if type(sequence) is not int or sequence < 1 or sequence > 2**63 - 1:
                    錯誤 = True
                else:
                    安全event_id = event_id
                    安全committed = committed
                    安全sequence = sequence
            elif sequence is not None:
                錯誤 = True
            else:
                安全event_id = event_id
                安全committed = committed
                安全sequence = None
        except Exception:
            錯誤 = True

        if 錯誤:
            event_id = committed = sequence = None
            安全event_id = 安全committed = 安全sequence = None
            raise AuditReceiptError("AuditAppendReceipt 不符合公開契約") from None

        object.__setattr__(self, "event_id", 安全event_id)
        object.__setattr__(self, "committed", 安全committed)
        object.__setattr__(self, "sequence", 安全sequence)

    def to_json(self) -> JsonObject:
        """回傳固定鍵序 receipt JSON，且每次都是 ordinary new dict。"""
        return {
            "event_id": self.event_id,
            "committed": self.committed,
            "sequence": self.sequence,
        }


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


@dataclass(frozen=True, init=False)
class PublishedError(_公開DTO):
    """公開回應中的frozen錯誤摘要。

    ``to_json``是唯一公開serialization API；內部不可變快照不支援
    ``dataclasses.asdict``或``dataclasses.replace``。
    """

    code: str
    message: str
    __annotations__["details"] = Any
    locals()["details"] = field(default=None, repr=False)
    _細節正規JSON: str = field(default="{}", repr=False)

    def __init__(self, *位置參數: Any, **命名參數: Any) -> None:
        """驗證固定字串欄位，並建立 details 的深層不可變 JSON 快照。"""
        錯誤 = False
        未提供 = object()
        代碼: Any = 未提供
        訊息: Any = 未提供
        細節: Any = None
        原始細節: Any = None
        正規文字: str | None = None
        快照: Any = MappingProxyType({})
        計數 = [0, 0, 0]
        object.__setattr__(self, "code", "")
        object.__setattr__(self, "message", "")
        object.__setattr__(self, "details", MappingProxyType({}))
        object.__setattr__(self, "_細節正規JSON", "{}")
        try:
            if len(位置參數) > 3 or not set(命名參數).issubset({"code", "message", "details"}):
                錯誤 = True
            elif any(索引 < len(位置參數) and 名稱 in 命名參數 for 索引, 名稱 in enumerate(("code", "message", "details"))):
                錯誤 = True
            else:
                代碼 = 位置參數[0] if len(位置參數) > 0 else 命名參數.get("code", 未提供)
                訊息 = 位置參數[1] if len(位置參數) > 1 else 命名參數.get("message", 未提供)
                細節 = 位置參數[2] if len(位置參數) > 2 else 命名參數.get("details")
                原始細節 = {} if 細節 is None else 細節
            if 錯誤:
                pass
            elif not _PublishedError文字合法(代碼, 128):
                錯誤 = True
            elif not _PublishedError文字合法(訊息, 512):
                錯誤 = True
            elif type(原始細節) is not dict:
                錯誤 = True
            elif not _PublishedError細節合法(原始細節, 0, set(), 計數):
                錯誤 = True
            else:
                正規文字 = 建立正規JSON(原始細節)
                if len(正規文字.encode("utf-8")) > 32768:
                    錯誤 = True
                else:
                    快照 = _建立不可變JSON快照(原始細節)
        except Exception:
            錯誤 = True

        if 錯誤:
            位置參數 = ()
            命名參數 = {}
            未提供 = 代碼 = 訊息 = 細節 = 原始細節 = 正規文字 = 快照 = 計數 = None
            索引 = 名稱 = None
            raise ValueError("PublishedError 不符合公開契約") from None

        object.__setattr__(self, "code", 代碼)
        object.__setattr__(self, "message", 訊息)
        object.__setattr__(self, "details", 快照)
        assert 正規文字 is not None
        object.__setattr__(self, "_細節正規JSON", 正規文字)

    def to_json(self) -> JsonObject:
        """回傳固定鍵序與 fresh ordinary details containers。"""
        細節 = 解析嚴格JSON(self._細節正規JSON)
        if type(細節) is not dict:
            raise ValueError("PublishedError 不符合公開契約")
        return {
            "code": self.code,
            "message": self.message,
            "details": 細節,
        }


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
            正規錯誤 = None if error is None else _重建PublishedError(error)
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
                ("error", 正規錯誤),
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
            frozen_warnings = frozen_data = 正規錯誤 = 欄位 = 值 = 型別 = warning = None
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
            "error": None if self.error is None else PublishedError.to_json(self.error),
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


def _PublishedError文字合法(值: Any, 最大長度: int) -> bool:
    """確認 PublishedError 固定字串欄位為 bounded exact str 且無控制字元。"""
    return (
        type(值) is str
        and 0 < len(值) <= 最大長度
        and not any(unicodedata.category(字元) == "Cc" for 字元 in 值)
    )


def _PublishedError細節合法(
    值: Any,
    深度: int,
    路徑: set[int],
    計數: list[int],
) -> bool:
    """遞迴確認bounded exact JSON，並在serialize前限制節點與估計bytes。"""
    值型別 = type(值)
    計數[1] += 1
    if 計數[1] > 1024:
        return False
    if 值 is None or 值型別 is bool or 值型別 is int:
        計數[2] += 4 if 值 is None else len(str(值))
        return 計數[2] <= 32768
    if 值型別 is float:
        計數[2] += len(repr(值))
        return math.isfinite(值) and 計數[2] <= 32768
    if 值型別 is str:
        計數[2] += len(值.encode("utf-8")) + 2
        return len(值) <= 4096 and 計數[2] <= 32768
    if 值型別 not in (dict, list) or 深度 > 8:
        return False
    容器id = id(值)
    if 容器id in 路徑:
        return False
    路徑.add(容器id)
    try:
        計數[2] += 2
        if 計數[2] > 32768:
            return False
        if 值型別 is list:
            return all(_PublishedError細節合法(項目, 深度 + 1, 路徑, 計數) for 項目 in 值)
        for 鍵, 項目 in 值.items():
            計數[0] += 1
            if type(鍵) is not str or len(鍵) > 4096 or 計數[0] > 128:
                return False
            計數[2] += len(鍵.encode("utf-8")) + 3
            if 計數[2] > 32768:
                return False
            if not _PublishedError細節合法(項目, 深度 + 1, 路徑, 計數):
                return False
        return True
    finally:
        路徑.remove(容器id)


def _重建PublishedError(原始錯誤: Any) -> PublishedError:
    """只從exact scalar與canonical JSON文字重建可信公開錯誤。"""
    try:
        if type(原始錯誤) is not PublishedError:
            raise ValueError
        代碼 = 原始錯誤.code
        訊息 = 原始錯誤.message
        正規文字 = 原始錯誤._細節正規JSON
        if type(正規文字) is not str:
            raise ValueError
        細節 = 解析嚴格JSON(正規文字)
        if type(細節) is not dict:
            raise ValueError
        return PublishedError(代碼, 訊息, 細節)
    except Exception:
        原始錯誤 = 代碼 = 訊息 = 正規文字 = 細節 = None
        raise ValueError("PublishedError 不符合公開契約") from None


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


def _稽核可空安全識別值合法(值: Any) -> bool:
    """檢查 optional 稽核 identifier；None 表示未綁定，其他值沿用安全規則。"""
    if 值 is None:
        return True
    return _稽核安全識別值合法(值)


def _稽核資源型別合法(值: Any) -> bool:
    """檢查稽核 resource_type 是否為安全小寫公開 code。"""
    if type(值) is not str:
        return False
    if _AUDIT_RESOURCE_TYPE_PATTERN.fullmatch(值) is None:
        return False
    return not _稽核參照字串含不安全內容(值)


def _稽核參照字串含不安全內容(值: str) -> bool:
    """回傳字串是否帶有 raw secret、digest、路徑或空白特徵。"""
    if any(字元.isspace() for 字元 in 值):
        return True
    if "/" in 值 or "\\" in 值 or 值.startswith("~"):
        return True
    if _AUDIT_REFERENCE_SECRET_PREFIX_PATTERN.search(值) is not None:
        return True
    return _AUDIT_REFERENCE_FULL_HEX_DIGEST_PATTERN.search(值) is not None


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
