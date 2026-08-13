"""管理員呼叫紀錄查詢的 transport-neutral 契約。

描述：凍結 Admin list/detail paths、safe list DTO、查詢條件與簽章游標。
參數／欄位：所有公開 DTO 只接受 exact、有界且可序列化的安全 metadata。
返回值：可供 HTTP adapter 與 SQLite projection 共用的 immutable contract objects。
例外：非法 DTO 或游標固定轉成 ``ValueError``／``管理員呼叫游標錯誤``。
副作用：游標編碼器只持有行程內 HMAC authority，不執行 I/O。
"""

from __future__ import annotations

import base64
from dataclasses import dataclass
import hashlib
import hmac
import json
import math
import re
from typing import cast


ADMIN_INVOCATION_LIST_PATH = "/api/admin/endpoints/{endpoint_id}/invocations"
ADMIN_INVOCATION_METHOD = "GET"
ADMIN_INVOCATION_DETAIL_PATH = (
    "/api/admin/endpoints/{endpoint_id}/invocations/{invocation_id}"
)
ADMIN_INVOCATION_QUERY_KEYS = frozenset(
    {"from_at", "to_at", "status", "error_code", "limit", "cursor"}
)
ADMIN_INVOCATION_AUDIT_ACTION = "audit.detail.view"
ADMIN_INVOCATION_ERROR_CONTRACT = {
    401: "需要登入",
    403: "只有管理者可查看完整呼叫紀錄",
    404: "找不到呼叫紀錄",
    422: None,
    503: "呼叫紀錄暫時不可取得",
    500: "呼叫紀錄不可取得",
}
ADMIN_INVOCATION_DETAIL_FIELDS = frozenset({
    "invocation", "endpoint_id", "endpoint_version_id", "credential_id", "message_id",
    "status", "input", "metadata", "output", "error", "usage", "metadata_size_bytes",
    "metadata_sha256", "latency_ms", "pricing_version", "created_at", "completed_at",
    "run_events", "tool_calls",
})

_識別格式 = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}\Z")
_錯誤碼格式 = re.compile(r"[a-z][a-z0-9_.-]{0,127}\Z")
_游標格式 = re.compile(r"[A-Za-z0-9_-]{1,2048}\.[A-Za-z0-9_-]{43}\Z")
_狀態集合 = frozenset(
    {"pending", "running", "succeeded", "failed", "rate_limited", "invalid_api_key"}
)
_控制流程 = (KeyboardInterrupt, SystemExit, GeneratorExit)


class 管理員呼叫游標錯誤(ValueError):
    """游標格式、簽章或查詢scope不符合凍結契約。"""


class 查詢投影錯誤(RuntimeError):
    """查詢投影無法安全授權或驗證資料庫時的相容基類。"""


class 管理員呼叫不存在錯誤(查詢投影錯誤):
    """Exact endpoint/invocation pairing不存在；供adapter固定映射404。"""


class 管理員呼叫查詢錯誤(查詢投影錯誤):
    """查詢參數、資料或provider無法安全驗證；供adapter固定映射500。"""


class 管理員呼叫稽核錯誤(查詢投影錯誤):
    """Audit event未取得可信committed receipt；供adapter固定映射503。"""


class 管理員呼叫完整詳情:
    """Admin-only bounded raw detail；repr不揭露內容且序列化回傳新副本。"""

    __slots__ = ("_值",)

    def __init__(self, 值: dict[str, object], /) -> None:
        """只接受exact allowlist dict並複製canonical JSON containers。"""
        if type(值) is not dict or set(值) != ADMIN_INVOCATION_DETAIL_FIELDS:
            raise ValueError("管理員呼叫完整詳情無效") from None
        self._值 = {鍵: _複製詳情值(項) for 鍵, 項 in 值.items()}

    def __repr__(self) -> str:
        """避免raw payload因log/debug repr外洩。"""
        return "管理員呼叫完整詳情([REDACTED])"

    def 建立JSON(self) -> dict[str, object]:
        """建立供trusted Admin HTTP adapter使用的全新raw response dict。"""
        return {鍵: _複製詳情值(值) for 鍵, 值 in self._值.items()}


def 建立管理員呼叫完整詳情(原始投影: dict[str, object], /) -> 管理員呼叫完整詳情:
    """把既有已稽核raw provider dict重建為module-owned exact DTO。"""
    try:
        return 管理員呼叫完整詳情(原始投影)
    except _控制流程:
        raise
    except BaseException:
        raise 管理員呼叫查詢錯誤("呼叫紀錄不可取得") from None


def _複製詳情值(值: object) -> object:
    """深層複製exact JSON tree，拒絕動態容器、非有限數值與非字串鍵。"""
    if 值 is None or type(值) in (str, bool, int):
        return 值
    if type(值) is float:
        if not math.isfinite(cast(float, 值)):
            raise ValueError("管理員呼叫完整詳情無效") from None
        return 值
    if type(值) is list:
        return [_複製詳情值(項) for 項 in cast(list[object], 值)]
    if type(值) is dict:
        原始 = cast(dict[object, object], 值)
        if any(type(鍵) is not str for 鍵 in 原始):
            raise ValueError("管理員呼叫完整詳情無效") from None
        return {cast(str, 鍵): _複製詳情值(項) for 鍵, 項 in 原始.items()}
    raise ValueError("管理員呼叫完整詳情無效") from None


def _是識別碼(值: object) -> bool:
    """驗證可公開、單行且有界的 canonical identifier。"""
    return type(值) is str and _識別格式.fullmatch(值) is not None


def _是有限時間(值: object) -> bool:
    """只接受非負有限 exact int/float，拒絕 bool。"""
    if type(值) not in (int, float):
        return False
    數值 = cast(int | float, 值)
    return math.isfinite(數值) and 數值 >= 0


def _是可空有限時間(值: object) -> bool:
    """驗證可空時間。"""
    return 值 is None or _是有限時間(值)


def _是可空錯誤碼(值: object) -> bool:
    """驗證safe error code，不接受內部訊息。"""
    return 值 is None or (type(值) is str and _錯誤碼格式.fullmatch(值) is not None)


@dataclass(frozen=True, slots=True)
class 管理員呼叫查詢條件:
    """簽章cursor必須綁定的完整safe list query scope。"""

    端點識別碼: str
    起始時間: float | None
    結束時間: float | None
    狀態: str | None
    錯誤碼: str | None
    數量上限: int

    def __post_init__(self) -> None:
        """驗證 endpoint、window、filters與1..100 limit。"""
        if (not _是識別碼(self.端點識別碼)
                or not _是可空有限時間(self.起始時間)
                or not _是可空有限時間(self.結束時間)
                or (self.起始時間 is not None and self.結束時間 is not None
                    and self.起始時間 > self.結束時間)
                or (self.狀態 is not None
                    and (type(self.狀態) is not str or self.狀態 not in _狀態集合))
                or not _是可空錯誤碼(self.錯誤碼)
                or type(self.數量上限) is not int or not 1 <= self.數量上限 <= 100):
            raise ValueError("管理員呼叫查詢條件無效") from None


@dataclass(frozen=True, slots=True)
class 管理員呼叫游標位置:
    """固定created_at DESC、id DESC排序的下一頁位置。"""

    建立時間: float
    呼叫識別碼: str

    def __post_init__(self) -> None:
        """驗證有限時間及呼叫識別碼。"""
        if not _是有限時間(self.建立時間) or not _是識別碼(self.呼叫識別碼):
            raise ValueError("管理員呼叫游標位置無效") from None


@dataclass(frozen=True, slots=True)
class 管理員呼叫列表項目:
    """Admin list可公開的安全metadata；不含任何raw JSON。"""

    呼叫識別碼: str
    端點識別碼: str
    端點版本識別碼: str
    請求識別碼: str
    狀態: str
    錯誤碼: str | None
    延遲毫秒: float | None
    建立時間: float
    完成時間: float | None
    是否有遮蔽: bool

    def __post_init__(self) -> None:
        """逐欄驗證safe metadata與時間一致性。"""
        if (not all(_是識別碼(值) for 值 in (
                self.呼叫識別碼, self.端點識別碼,
                self.端點版本識別碼, self.請求識別碼))
                or type(self.狀態) is not str or self.狀態 not in _狀態集合
                or not _是可空錯誤碼(self.錯誤碼)
                or (self.延遲毫秒 is not None and not _是有限時間(self.延遲毫秒))
                or not _是有限時間(self.建立時間)
                or not _是可空有限時間(self.完成時間)
                or (self.完成時間 is not None and self.完成時間 < self.建立時間)
                or type(self.是否有遮蔽) is not bool):
            raise ValueError("管理員呼叫列表項目無效") from None


@dataclass(frozen=True, slots=True)
class 管理員呼叫列表結果:
    """有界safe list項目與opaque signed cursor。"""

    項目: tuple[管理員呼叫列表項目, ...]
    下一頁游標: str | None

    def __post_init__(self) -> None:
        """只接受exact tuple／DTO與有界opaque cursor。"""
        if (type(self.項目) is not tuple or len(self.項目) > 100
                or any(type(項目) is not 管理員呼叫列表項目 for 項目 in self.項目)
                or (self.下一頁游標 is not None and (
                    type(self.下一頁游標) is not str
                    or not 1 <= len(self.下一頁游標) <= 4096
                    or re.fullmatch(r"[A-Za-z0-9_.-]+", self.下一頁游標) is None))):
            raise ValueError("管理員呼叫列表結果無效") from None


@dataclass(frozen=True, slots=True)
class 管理員呼叫投影頁:
    """SQLite安全投影回傳的項目與未簽章keyset位置。"""

    項目: tuple[管理員呼叫列表項目, ...]
    下一頁位置: 管理員呼叫游標位置 | None

    def __post_init__(self) -> None:
        """只接受exact tuple／DTO與exact位置，供adapter後續簽章。"""
        if (type(self.項目) is not tuple or len(self.項目) > 100
                or any(type(項目) is not 管理員呼叫列表項目 for 項目 in self.項目)
                or (self.下一頁位置 is not None
                    and type(self.下一頁位置) is not 管理員呼叫游標位置)):
            raise ValueError("管理員呼叫投影頁無效") from None


class 管理員呼叫游標編解碼器:
    """以HMAC綁定query scope與keyset position的opaque cursor。"""

    __slots__ = ("_金鑰",)

    def __init__(self, 簽章金鑰: bytes) -> None:
        """複製至少32 bytes的exact HMAC key。"""
        if type(簽章金鑰) is not bytes or len(簽章金鑰) < 32:
            raise 管理員呼叫游標錯誤("管理員呼叫游標無效") from None
        self._金鑰 = bytes(簽章金鑰)

    def 編碼(self, 條件: 管理員呼叫查詢條件, 位置: 管理員呼叫游標位置) -> str:
        """建立canonical payload並附加HMAC-SHA256簽章。"""
        try:
            payload = _建立游標payload(條件, 位置)
            內容 = json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("ascii")
            簽章 = hmac.new(self._金鑰, 內容, hashlib.sha256).digest()
            return _編碼base64url(內容) + "." + _編碼base64url(簽章)
        except _控制流程:
            raise
        except BaseException:
            raise 管理員呼叫游標錯誤("管理員呼叫游標無效") from None

    def 解碼(self, 游標: str, 條件: 管理員呼叫查詢條件) -> 管理員呼叫游標位置:
        """驗證canonical encoding、簽章及完整query scope後回傳位置。"""
        try:
            if type(游標) is not str or _游標格式.fullmatch(游標) is None:
                raise ValueError
            內容文字, 簽章文字 = 游標.split(".", 1)
            內容 = _解base64url(內容文字)
            簽章 = _解base64url(簽章文字)
            if len(簽章) != 32 or not hmac.compare_digest(
                    簽章, hmac.new(self._金鑰, 內容, hashlib.sha256).digest()):
                raise ValueError
            payload = json.loads(內容.decode("ascii"), object_pairs_hook=_拒絕重複鍵)
            if _編碼base64url(內容) != 內容文字 or type(payload) is not dict:
                raise ValueError
            預期scope = _建立scope(條件)
            if set(payload) != {"v", "scope", "position"} or payload["v"] != 1:
                raise ValueError
            if payload["scope"] != 預期scope or type(payload["position"]) is not list:
                raise ValueError
            if len(payload["position"]) != 2:
                raise ValueError
            return 管理員呼叫游標位置(payload["position"][0], payload["position"][1])
        except _控制流程:
            raise
        except BaseException:
            raise 管理員呼叫游標錯誤("管理員呼叫游標無效") from None


def _建立scope(條件: 管理員呼叫查詢條件) -> list[object]:
    """重建exact查詢scope，拒絕DTO子類或事後竄改。"""
    if type(條件) is not 管理員呼叫查詢條件:
        raise ValueError
    安全 = 管理員呼叫查詢條件(*(object.__getattribute__(條件, 名稱)
                              for 名稱 in 管理員呼叫查詢條件.__slots__))
    return [安全.端點識別碼, 安全.起始時間, 安全.結束時間,
            安全.狀態, 安全.錯誤碼, 安全.數量上限]


def _建立游標payload(
    條件: 管理員呼叫查詢條件, 位置: 管理員呼叫游標位置,
) -> dict[str, object]:
    """建立versioned、query-bound canonical payload。"""
    if type(位置) is not 管理員呼叫游標位置:
        raise ValueError
    安全位置 = 管理員呼叫游標位置(*(object.__getattribute__(位置, 名稱)
                                    for 名稱 in 管理員呼叫游標位置.__slots__))
    return {"v": 1, "scope": _建立scope(條件),
            "position": [安全位置.建立時間, 安全位置.呼叫識別碼]}


def _編碼base64url(內容: bytes) -> str:
    """建立canonical unpadded base64url。"""
    return base64.urlsafe_b64encode(內容).rstrip(b"=").decode("ascii")


def _解base64url(文字: str) -> bytes:
    """只解碼canonical unpadded base64url。"""
    if type(文字) is not str or not 文字 or re.fullmatch(r"[A-Za-z0-9_-]+", 文字) is None:
        raise ValueError
    內容 = base64.b64decode(文字 + "=" * (-len(文字) % 4), altchars=b"-_", validate=True)
    if _編碼base64url(內容) != 文字:
        raise ValueError
    return 內容


def _拒絕重複鍵(配對: list[tuple[str, object]]) -> dict[str, object]:
    """JSON object遇重複key即拒絕。"""
    結果: dict[str, object] = {}
    for 鍵, 值 in 配對:
        if 鍵 in 結果:
            raise ValueError
        結果[鍵] = 值
    return 結果
