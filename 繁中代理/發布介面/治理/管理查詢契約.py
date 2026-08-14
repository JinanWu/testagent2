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
import time
from typing import cast

from ..嚴格JSON import 建立正規JSON
from .遮蔽 import 驗證遮蔽公開欄位


ADMIN_INVOCATION_LIST_PATH = "/api/admin/endpoints/{endpoint_id}/invocations"
ADMIN_INVOCATION_METHOD = "GET"
ADMIN_INVOCATION_DETAIL_PATH = (
    "/api/admin/endpoints/{endpoint_id}/invocations/{invocation_id}"
)
ADMIN_INVOCATION_QUERY_KEYS = frozenset(
    {"from_at", "to_at", "status", "error_code", "limit", "cursor"}
)
ADMIN_INVOCATION_FORBIDDEN_QUERY_KEYS = frozenset(
    {"owner_id", "raw_search", "export", "sort"}
)
ADMIN_INVOCATION_REJECT_DUPLICATE_QUERY_KEYS = True
ADMIN_INVOCATION_AUDIT_ACTION = "audit.detail.view"
ADMIN_INVOCATION_ERROR_CONTRACT = {
    401: "需要登入",
    403: "只有管理者可查看完整呼叫紀錄",
    404: "找不到呼叫紀錄",
    422: None,
    503: "呼叫紀錄暫時不可取得",
    500: "呼叫紀錄不可取得",
}
_最大安全JSON整數 = 2**53 - 1


@dataclass(frozen=True, slots=True)
class 管理員拒絕稽核收據:
    """綁定單次denied audit欄位且由per-app authority驗證的opaque receipt。"""

    載荷: bytes
    簽章: bytes


class 管理員拒絕稽核收據權威:
    """以行程內HMAC key簽發並驗證不可由detail provider自行偽造的receipt。"""

    __slots__ = ("_key",)

    def __init__(self, 金鑰: bytes) -> None:
        """複製至少32 bytes key；不執行I/O。"""
        if type(金鑰) is not bytes or len(金鑰) < 32:
            raise ValueError
        self._key = bytes(金鑰)

    @staticmethod
    def _載荷(管理員識別碼, 請求識別碼, 稽核事件識別碼, 發生時間, 端點識別碼, 呼叫識別碼) -> bytes:
        """建立exact、canonical、不可混淆的request-bound payload。"""
        return json.dumps(
            [管理員識別碼, 請求識別碼, 稽核事件識別碼, 發生時間, 端點識別碼, 呼叫識別碼],
            ensure_ascii=True, separators=(",", ":"), allow_nan=False,
        ).encode("utf-8")

    def 簽發(self, *欄位) -> 管理員拒絕稽核收據:
        """僅供已成功提交audit的canonical gate簽發request-bound receipt。"""
        載荷 = self._載荷(*欄位)
        return 管理員拒絕稽核收據(載荷, hmac.digest(self._key, 載荷, "sha256"))

    def 驗證(self, 收據: object, *欄位) -> bool:
        """固定時間驗證exact receipt type、payload binding與HMAC。"""
        try:
            預期載荷 = self._載荷(*欄位)
            return (
                type(收據) is 管理員拒絕稽核收據
                and 收據.載荷 == 預期載荷
                and hmac.compare_digest(收據.簽章, hmac.digest(self._key, 預期載荷, "sha256"))
            )
        except (TypeError, ValueError, OverflowError):
            return False
ADMIN_INVOCATION_DETAIL_FIELDS = frozenset({
    "invocation", "endpoint_id", "endpoint_version_id", "credential_id", "message_id",
    "status", "input", "metadata", "output", "error", "usage", "metadata_size_bytes",
    "metadata_sha256", "latency_ms", "pricing_version", "created_at", "completed_at",
    "run_events", "tool_calls", "redactions",
})
OWNER_SAFE_DETAIL_FIELDS = frozenset({
    "invocation", "endpoint_version_id", "status", "error_code", "schema_path",
    "latency_ms", "usage", "tool_names",
})

_識別格式 = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}\Z")
_錯誤碼格式 = re.compile(r"[a-z][a-z0-9_.-]{0,127}\Z")
_游標格式 = re.compile(r"[A-Za-z0-9_-]{1,2048}\.[A-Za-z0-9_-]{43}\Z")
_狀態集合 = frozenset(
    {"pending", "running", "succeeded", "failed", "rate_limited", "invalid_api_key"}
)
_控制流程 = (KeyboardInterrupt, SystemExit, GeneratorExit)
_最大詳情JSON位元組 = 1_048_576
_最大詳情JSON節點 = 4096
_最大詳情JSON深度 = 128
_事件欄位 = frozenset({"id", "sequence_number", "event_type", "payload", "created_at"})
_工具欄位 = frozenset({
    "id", "run_event_id", "sequence_number", "tool_name", "arguments", "outcome",
    "result", "error", "latency_ms", "retry_of_tool_call_id", "created_at",
})
_遮蔽欄位 = frozenset({
    "id", "target_type", "target_row_id", "json_path", "reason", "is_tombstone", "redacted_at",
})

_禁止敏感鍵 = frozenset({
    "authorization", "proxyauthorization", "cookie", "setcookie", "apikey",
    "credentialsecret", "credentialciphertext", "credentialhash", "masterkey",
    "providersecret", "clientsecret", "secretkey", "privatekey", "password",
    "accesstoken", "refreshtoken",
})
_禁止敏感值標記 = frozenset({
    "authorization", "proxyauthorization", "cookie", "setcookie", "apikey",
    "credentialsecret", "credentialciphertext", "credentialhash", "masterkey",
    "providersecret", "clientsecret", "secretkey", "privatekey", "password",
    "accesstoken", "refreshtoken",
})
_檔案路徑鍵 = frozenset({"path", "filepath", "filesystempath", "absolutepath"})
_平台APIKey格式 = re.compile(r"(?<![A-Za-z0-9_-])pk_[A-Za-z0-9_-]{43}(?![A-Za-z0-9_-])")
_憑證token形狀 = re.compile(
    r"(?<![A-Za-z0-9_-])(?:"
    r"[A-Za-z0-9]{2,16}[_-][A-Za-z0-9_-]{32,}|"
    r"[A-Za-z0-9]{2,4}[_-][A-Za-z0-9]{2,16}[_-][A-Za-z0-9_-]{16,}"
    r")(?![A-Za-z0-9_-])"
)
_絕對檔案路徑格式 = re.compile(
    r"(?:^|[\s:=\"'])(?:~[/\\]|/(?:Users|home|etc|var|tmp|private|opt|usr|root|proc|sys|dev|srv)/"
    r"|[A-Za-z]:[\\/]|\\\\)", re.IGNORECASE,
)



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
    """Admin-only bounded raw detail；內部只保存canonical immutable bytes。"""

    __slots__ = ("_內容",)

    def __init__(self, 值: dict[str, object], /) -> None:
        """逐欄驗證exact detail schema、JSON bounds並保存canonical bytes。"""
        _驗證管理員完整詳情(值)
        內容 = _編碼bounded_JSON(值)
        object.__setattr__(self, "_內容", 內容)

    def __setattr__(self, _名稱: str, _值: object) -> None:
        """建構後拒絕所有一般attribute mutation。"""
        raise AttributeError("管理員呼叫完整詳情不可變")

    def __repr__(self) -> str:
        """避免raw payload因log/debug repr外洩。"""
        return "管理員呼叫完整詳情([REDACTED])"

    def 建立JSON(self) -> dict[str, object]:
        """重驗內部canonical bytes後建立供trusted Admin adapter使用的新dict。"""
        try:
            內容 = object.__getattribute__(self, "_內容")
            if type(內容) is not bytes or not 內容 or len(內容) > _最大詳情JSON位元組:
                raise ValueError
            值 = json.loads(內容.decode("ascii"), object_pairs_hook=_拒絕重複鍵)
            _驗證管理員完整詳情(值)
            if not hmac.compare_digest(_編碼bounded_JSON(值), 內容):
                raise ValueError
            return cast(dict[str, object], 值)
        except _控制流程:
            raise
        except BaseException:
            raise 管理員呼叫查詢錯誤("呼叫紀錄不可取得") from None


def 建立管理員呼叫完整詳情(原始投影: dict[str, object], /) -> 管理員呼叫完整詳情:
    """把既有已稽核raw provider dict重建為module-owned exact DTO。"""
    try:
        return 管理員呼叫完整詳情(原始投影)
    except _控制流程:
        raise
    except BaseException:
        raise 管理員呼叫查詢錯誤("呼叫紀錄不可取得") from None


def _驗證管理員完整詳情(值: object) -> None:
    """逐欄驗證Admin detail及run/tool child schemas。"""
    if type(值) is not dict or set(值) != ADMIN_INVOCATION_DETAIL_FIELDS:
        raise ValueError
    詳情 = cast(dict[str, object], 值)
    呼叫 = 詳情["invocation"]
    if (type(呼叫) is not dict or set(呼叫) != {"id", "request_id", "session_id"}
            or not _是識別碼(呼叫["id"]) or not _是識別碼(呼叫["request_id"])
            or (呼叫["session_id"] is not None and not _是識別碼(呼叫["session_id"]))
            or not _是識別碼(詳情["endpoint_id"])
            or not _是識別碼(詳情["endpoint_version_id"])
            or (詳情["credential_id"] is not None and not _是識別碼(詳情["credential_id"]))
            or (詳情["message_id"] is not None and not _是識別碼(詳情["message_id"]))
            or type(詳情["status"]) is not str or 詳情["status"] not in _狀態集合
            or (詳情["metadata"] is not None and type(詳情["metadata"]) is not dict)
            or (詳情["metadata_size_bytes"] is not None and (
                type(詳情["metadata_size_bytes"]) is not int or 詳情["metadata_size_bytes"] < 0))
            or not _是可空SHA256(詳情["metadata_sha256"])
            or (詳情["latency_ms"] is not None and not _是有限時間(詳情["latency_ms"]))
            or (詳情["pricing_version"] is not None and (
                type(詳情["pricing_version"]) is not str or len(詳情["pricing_version"]) > 256))
            or not _是有限時間(詳情["created_at"])
            or not _是可空有限時間(詳情["completed_at"])
            or (詳情["completed_at"] is not None
                and cast(int | float, 詳情["completed_at"])
                < cast(int | float, 詳情["created_at"]))
            or type(詳情["run_events"]) is not list or type(詳情["tool_calls"]) is not list
            or type(詳情["redactions"]) is not list
            or len(詳情["run_events"]) + len(詳情["tool_calls"]) + len(詳情["redactions"]) > 4096):
        raise ValueError
    for raw值 in (詳情["input"], 詳情["metadata"], 詳情["output"], 詳情["error"], 詳情["usage"]):
        _驗證raw無禁止secret(raw值)
    事件識別碼: set[str] = set()
    事件序號: set[int] = set()
    for 事件 in cast(list[object], 詳情["run_events"]):
        if (type(事件) is not dict or set(事件) != _事件欄位
                or not _是識別碼(事件["id"])
                or 事件["id"] in 事件識別碼
                or type(事件["sequence_number"]) is not int or 事件["sequence_number"] <= 0
                or 事件["sequence_number"] in 事件序號
                or type(事件["event_type"]) is not str or not 1 <= len(事件["event_type"]) <= 256
                or type(事件["payload"]) is not dict
                or not _是有限時間(事件["created_at"])):
            raise ValueError
        事件識別碼.add(cast(str, 事件["id"]))
        事件序號.add(cast(int, 事件["sequence_number"]))
        _驗證raw無禁止secret(事件["payload"])
    工具識別碼: set[str] = set()
    工具序號: set[int] = set()
    for 工具 in cast(list[object], 詳情["tool_calls"]):
        if (type(工具) is not dict or set(工具) != _工具欄位
                or not _是識別碼(工具["id"])
                or 工具["id"] in 工具識別碼
                or (工具["run_event_id"] is not None and 工具["run_event_id"] not in 事件識別碼)
                or type(工具["sequence_number"]) is not int or 工具["sequence_number"] <= 0
                or 工具["sequence_number"] in 工具序號
                or type(工具["tool_name"]) is not str or not 1 <= len(工具["tool_name"]) <= 256
                or type(工具["arguments"]) is not dict
                or 工具["outcome"] not in ("success", "error")
                or (工具["outcome"] == "success"
                    and (工具["result"] is None or 工具["error"] is not None))
                or (工具["outcome"] == "error"
                    and (工具["result"] is not None or 工具["error"] is None))
                or (工具["latency_ms"] is not None and not _是有限時間(工具["latency_ms"]))
                or (工具["retry_of_tool_call_id"] is not None
                    and 工具["retry_of_tool_call_id"] not in 工具識別碼)
                or not _是有限時間(工具["created_at"])):
            raise ValueError
        工具識別碼.add(cast(str, 工具["id"]))
        工具序號.add(cast(int, 工具["sequence_number"]))
        for raw值 in (工具["arguments"], 工具["result"], 工具["error"]):
            _驗證raw無禁止secret(raw值)
    for 遮蔽 in cast(list[object], 詳情["redactions"]):
        if (type(遮蔽) is not dict or set(遮蔽) != _遮蔽欄位
                or not _是識別碼(遮蔽["id"])
                or not _是識別碼(遮蔽["target_row_id"])
                or 遮蔽["is_tombstone"] is not True
                or not _是有限時間(遮蔽["redacted_at"])):
            raise ValueError
        驗證遮蔽公開欄位(遮蔽["target_type"], 遮蔽["json_path"], 遮蔽["reason"])
    _驗證完整詳情墓碑一致性(詳情)
    _驗證metadata關聯(詳情)


def _驗證metadata關聯(詳情: dict[str, object]) -> None:
    """驗證可空metrics成對，存在時綁定writer canonical UTF-8 bytes。"""
    中繼 = 詳情["metadata"]
    大小 = 詳情["metadata_size_bytes"]
    摘要 = 詳情["metadata_sha256"]
    if (大小 is None) != (摘要 is None):
        raise ValueError
    if 大小 is None:
        return
    if type(中繼) is not dict:
        raise ValueError
    if any(
        cast(dict[str, object], 遮蔽)["target_type"] == "metadata"
        for 遮蔽 in cast(list[object], 詳情["redactions"])
    ):
        return
    正規位元組 = 建立正規JSON(中繼).encode("utf-8")
    if len(正規位元組) != 大小 or hashlib.sha256(正規位元組).hexdigest() != 摘要:
        raise ValueError


def _驗證完整詳情墓碑一致性(詳情: dict[str, object]) -> None:
    """雙向核對所有canonical tombstone與sanitized redaction ledger。"""
    呼叫 = cast(dict[str, object], 詳情["invocation"])
    呼叫ID = cast(str, 呼叫["id"])
    目標: dict[tuple[str, str], object] = {
        ("invocation_input", 呼叫ID): 詳情["input"],
        ("metadata", 呼叫ID): 詳情["metadata"],
        ("output", 呼叫ID): 詳情["output"],
        ("error", 呼叫ID): 詳情["error"],
        ("__usage_forbidden__", 呼叫ID): 詳情["usage"],
    }
    for 事件 in cast(list[dict[str, object]], 詳情["run_events"]):
        目標[("run_event", cast(str, 事件["id"]))] = 事件["payload"]
    for 工具 in cast(list[dict[str, object]], 詳情["tool_calls"]):
        列ID = cast(str, 工具["id"])
        目標[("tool_arguments", 列ID)] = 工具["arguments"]
        目標[("tool_result", 列ID)] = 工具["result"]
        目標[("tool_error", 列ID)] = 工具["error"]

    _驗證墓碑目標一致性(目標, cast(list[object], 詳情["redactions"]))


def _驗證墓碑目標一致性(
    目標: dict[tuple[str, str], object], 原遮蔽列: list[object], /
) -> None:
    """以唯一底層authority雙向核對payload targets與sanitized ledger。"""

    預期: set[tuple[str, str, str, str, int | float]] = set()
    for 原遮蔽 in 原遮蔽列:
        遮蔽 = cast(dict[str, object], 原遮蔽)
        鍵 = (cast(str, 遮蔽["target_type"]), cast(str, 遮蔽["target_row_id"]))
        項 = (
            鍵[0], 鍵[1], cast(str, 遮蔽["json_path"]), cast(str, 遮蔽["id"]),
            cast(int | float, 遮蔽["redacted_at"]),
        )
        if 鍵 not in 目標 or 項 in 預期:
            raise ValueError
        預期.add(項)
    if len(預期) != len(原遮蔽列):
        raise ValueError

    發現: set[tuple[str, str, str, str, int | float]] = set()
    for (類型, 列ID), payload in 目標.items():
        _收集完整詳情墓碑(payload, 類型, 列ID, 發現)
    if 發現 != 預期:
        raise ValueError


def _收集完整詳情墓碑(
    值: object, 類型: str, 列ID: str,
    結果: set[tuple[str, str, str, str, int | float]],
) -> None:
    """Iterative收集exact tombstone及RFC 6901 path，拒絕相似但非canonical形狀。"""
    待驗證: list[tuple[object, str]] = [(值, "")]
    while 待驗證:
        項, 路徑 = 待驗證.pop()
        if type(項) is dict and "$tombstone" in 項:
            墓碑外層 = cast(dict[str, object], 項)
            墓碑 = 墓碑外層.get("$tombstone")
            if (set(墓碑外層) != {"$tombstone"} or type(墓碑) is not dict
                    or set(墓碑) != {"redaction_id", "redacted_at"}
                    or not _是識別碼(墓碑.get("redaction_id"))
                    or not _是有限時間(墓碑.get("redacted_at"))):
                raise ValueError
            結果.add((
                類型, 列ID, 路徑, cast(str, 墓碑["redaction_id"]),
                cast(int | float, 墓碑["redacted_at"]),
            ))
            continue
        if type(項) is dict:
            for 鍵, 子項 in cast(dict[str, object], 項).items():
                片段 = 鍵.replace("~", "~0").replace("/", "~1")
                待驗證.append((子項, 路徑 + "/" + 片段))
        elif type(項) is list:
            for 索引, 子項 in enumerate(cast(list[object], 項)):
                待驗證.append((子項, 路徑 + "/" + str(索引)))


def _驗證raw無禁止secret(值: object) -> None:
    """Iterative拒絕raw tree中的治理secret key、平台API key與絕對filesystem path。"""
    待驗證: list[tuple[object, int]] = [(值, 1)]
    已見容器: set[int] = set()
    節點 = 0
    while 待驗證:
        項, 深度 = 待驗證.pop()
        節點 += 1
        if 節點 > _最大詳情JSON節點 or 深度 > _最大詳情JSON深度:
            raise ValueError
        if type(項) is str:
            文字 = cast(str, 項)
            正規文字 = re.sub(r"[^a-z0-9]", "", 文字.casefold())
            if (_平台APIKey格式.search(文字) is not None
                    or _憑證token形狀.search(文字) is not None
                    or _絕對檔案路徑格式.search(文字) is not None
                    or any(標記 in 正規文字 for 標記 in _禁止敏感值標記)):
                raise ValueError
            continue
        if type(項) is int:
            if not -_最大安全JSON整數 <= cast(int, 項) <= _最大安全JSON整數:
                raise ValueError
            continue
        if type(項) is list:
            if id(項) in 已見容器:
                raise ValueError
            已見容器.add(id(項))
            待驗證.extend((子項, 深度 + 1) for 子項 in cast(list[object], 項))
            continue
        if type(項) is not dict:
            continue
        if id(項) in 已見容器:
            raise ValueError
        已見容器.add(id(項))
        for 鍵, 子項 in cast(dict[object, object], 項).items():
            if type(鍵) is not str:
                raise ValueError
            正規鍵 = re.sub(r"[^a-z0-9]", "", cast(str, 鍵).casefold())
            if 正規鍵 in _禁止敏感鍵 or any(
                    正規鍵.endswith(尾碼) for 尾碼 in
                    ("authorization", "cookie", "apikey", "credentialsecret",
                     "credentialciphertext", "credentialhash", "providersecret",
                     "privatekey", "secretkey", "masterkey", "clientsecret",
                     "accesstoken", "refreshtoken")):
                raise ValueError
            if (正規鍵 in _檔案路徑鍵 and type(子項) is str
                    and _絕對檔案路徑格式.match(cast(str, 子項)) is not None):
                raise ValueError
            待驗證.append((子項, 深度 + 1))


def _編碼bounded_JSON(值: object) -> bytes:
    """Iterative驗證JSON tree bounds後建立canonical bytes。"""
    待驗證: list[tuple[object, int]] = [(值, 1)]
    節點 = 0
    while 待驗證:
        項, 深度 = 待驗證.pop()
        節點 += 1
        if 節點 > _最大詳情JSON節點 or 深度 > _最大詳情JSON深度:
            raise ValueError
        if 項 is None or type(項) in (str, bool):
            continue
        if type(項) is int:
            if not -_最大安全JSON整數 <= cast(int, 項) <= _最大安全JSON整數:
                raise ValueError
            continue
        if type(項) is float:
            if not math.isfinite(cast(float, 項)):
                raise ValueError
            continue
        if type(項) is list:
            待驗證.extend((子項, 深度 + 1) for 子項 in cast(list[object], 項))
            continue
        if type(項) is dict:
            字典 = cast(dict[object, object], 項)
            if any(type(鍵) is not str for 鍵 in 字典):
                raise ValueError
            待驗證.extend((子項, 深度 + 1) for 子項 in 字典.values())
            continue
        raise ValueError
    內容 = _編碼canonical_JSON(值)
    if not 內容 or len(內容) > _最大詳情JSON位元組:
        raise ValueError
    return 內容


class 擁有者安全詳情:
    """Owner-only安全摘要；以canonical bytes與Admin raw DTO完全分離。"""

    __slots__ = ("_內容",)

    def __init__(self, 值: dict[str, object], /) -> None:
        """逐欄驗證既有Owner-safe projection並保存canonical bytes。"""
        _驗證擁有者安全詳情(值)
        object.__setattr__(self, "_內容", _編碼bounded_JSON(值))

    def __setattr__(self, _名稱: str, _值: object) -> None:
        """建構後拒絕所有一般attribute mutation。"""
        raise AttributeError("擁有者安全詳情不可變")

    def __repr__(self) -> str:
        """Owner DTO也不把識別碼或錯誤摘要帶入log。"""
        return "擁有者安全詳情([REDACTED])"

    def 建立JSON(self) -> dict[str, object]:
        """重驗內部canonical bytes後建立全新Owner-safe dict。"""
        try:
            內容 = object.__getattribute__(self, "_內容")
            if type(內容) is not bytes or not 內容 or len(內容) > _最大詳情JSON位元組:
                raise ValueError
            值 = json.loads(內容.decode("ascii"), object_pairs_hook=_拒絕重複鍵)
            _驗證擁有者安全詳情(值)
            if not hmac.compare_digest(_編碼bounded_JSON(值), 內容):
                raise ValueError
            return cast(dict[str, object], 值)
        except _控制流程:
            raise
        except BaseException:
            raise 查詢投影錯誤("呼叫紀錄不可取得") from None


def _驗證擁有者安全詳情(值: object) -> None:
    """逐欄驗證Owner-safe detail schema。"""
    if type(值) is not dict or set(值) != OWNER_SAFE_DETAIL_FIELDS:
        raise ValueError
    詳情 = cast(dict[str, object], 值)
    呼叫 = 詳情["invocation"]
    用量 = 詳情["usage"]
    工具名稱 = 詳情["tool_names"]
    if (type(呼叫) is not dict or set(呼叫) != {"id", "request_id", "session_id"}
            or not _是識別碼(呼叫["id"]) or not _是識別碼(呼叫["request_id"])
            or (呼叫["session_id"] is not None and not _是識別碼(呼叫["session_id"]))
            or not _是識別碼(詳情["endpoint_version_id"])
            or type(詳情["status"]) is not str or 詳情["status"] not in _狀態集合
            or not _是可空錯誤碼(詳情["error_code"])
            or (詳情["schema_path"] is not None and (
                type(詳情["schema_path"]) is not str or len(詳情["schema_path"]) > 512))
            or (詳情["latency_ms"] is not None and not _是有限時間(詳情["latency_ms"]))
            or type(用量) is not dict or set(用量) != {"total_tokens"}
            or (用量["total_tokens"] is not None and (
                type(用量["total_tokens"]) is not int or 用量["total_tokens"] < 0))
            or type(工具名稱) is not list or len(工具名稱) > 4096
            or any(type(名稱) is not str or not 1 <= len(名稱) <= 256 for 名稱 in 工具名稱)):
        raise ValueError


def 建立擁有者安全詳情(原始投影: dict[str, object], /) -> 擁有者安全詳情:
    """把既有Owner projection重建為module-owned exact DTO。"""
    try:
        return 擁有者安全詳情(原始投影)
    except _控制流程:
        raise
    except BaseException:
        raise 查詢投影錯誤("呼叫紀錄不可取得") from None


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


def _是可空SHA256(值: object) -> bool:
    """驗證可空lowercase SHA-256文字。"""
    return 值 is None or (
        type(值) is str and len(值) == 64 and all(字 in "0123456789abcdef" for 字 in 值)
    )


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


@dataclass(frozen=True, slots=True, repr=False)
class 管理員呼叫游標位置:
    """固定created_at DESC、id DESC排序的下一頁位置。"""

    建立時間: float
    呼叫識別碼: str

    def __post_init__(self) -> None:
        """驗證有限時間及呼叫識別碼。"""
        if not _是有限時間(self.建立時間) or not _是識別碼(self.呼叫識別碼):
            raise ValueError("管理員呼叫游標位置無效") from None

    def __repr__(self) -> str:
        """Cursor position不把識別碼帶入repr。"""
        return "管理員呼叫游標位置([REDACTED])"


@dataclass(frozen=True, slots=True, repr=False)
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

    def __repr__(self) -> str:
        """List metadata不進log/debug repr。"""
        return "管理員呼叫列表項目([REDACTED])"


@dataclass(frozen=True, slots=True, repr=False)
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
        for 項目 in self.項目:
            項目.__post_init__()
        object.__setattr__(self, "項目", tuple(_重建列表項目(項目) for 項目 in self.項目))

    def __repr__(self) -> str:
        """組合結果不把child metadata帶入repr。"""
        return "管理員呼叫列表結果([REDACTED])"


@dataclass(frozen=True, slots=True, repr=False)
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
        for 項目 in self.項目:
            項目.__post_init__()
        object.__setattr__(self, "項目", tuple(_重建列表項目(項目) for 項目 in self.項目))
        if self.下一頁位置 is not None:
            self.下一頁位置.__post_init__()
            object.__setattr__(self, "下一頁位置", 管理員呼叫游標位置(
                self.下一頁位置.建立時間, self.下一頁位置.呼叫識別碼,
            ))

    def __repr__(self) -> str:
        """SQLite projection page不顯示metadata。"""
        return "管理員呼叫投影頁([REDACTED])"


def _重建列表項目(項目: 管理員呼叫列表項目) -> 管理員呼叫列表項目:
    """驗證後建立fresh safe-list snapshot，切斷caller alias。"""
    return 管理員呼叫列表項目(
        項目.呼叫識別碼, 項目.端點識別碼, 項目.端點版本識別碼, 項目.請求識別碼,
        項目.狀態, 項目.錯誤碼, 項目.延遲毫秒, 項目.建立時間, 項目.完成時間, 項目.是否有遮蔽,
    )


class 管理員呼叫游標編解碼器:
    """以HMAC綁定query scope與keyset position的opaque cursor。"""

    __slots__ = ("_金鑰", "_時鐘")

    def __init__(self, 簽章金鑰: bytes, *, 時鐘=time.time) -> None:
        """複製至少32 bytes的exact HMAC key並捕捉server clock authority。"""
        if type(簽章金鑰) is not bytes or len(簽章金鑰) < 32 or not callable(時鐘):
            raise 管理員呼叫游標錯誤("管理員呼叫游標無效") from None
        self._金鑰 = bytes(簽章金鑰)
        self._時鐘 = 時鐘

    def 編碼(self, 條件: 管理員呼叫查詢條件, 位置: 管理員呼叫游標位置) -> str:
        """建立canonical payload並附加HMAC-SHA256簽章。"""
        try:
            簽發時間 = _讀取游標時鐘(self._時鐘)
            payload = _建立游標payload(條件, 位置, 簽發時間)
            內容 = _編碼canonical_JSON(payload)
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
            if not hmac.compare_digest(_編碼canonical_JSON(payload), 內容):
                raise ValueError
            預期scope = _建立scope(條件)
            if set(payload) != {"v", "scope", "position", "iat", "exp"} or payload["v"] != 2:
                raise ValueError
            if payload["scope"] != 預期scope or type(payload["position"]) is not list:
                raise ValueError
            if len(payload["position"]) != 2:
                raise ValueError
            簽發時間, 到期時間 = payload["iat"], payload["exp"]
            if (type(簽發時間) not in (int, float) or type(到期時間) not in (int, float)
                    or not math.isfinite(簽發時間) or not math.isfinite(到期時間)
                    or 簽發時間 < 0 or 到期時間 != 簽發時間 + 300):
                raise ValueError
            現在 = _讀取游標時鐘(self._時鐘)
            if 簽發時間 > 現在 or 現在 >= 到期時間:
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
    條件: 管理員呼叫查詢條件, 位置: 管理員呼叫游標位置, 簽發時間: float,
) -> dict[str, object]:
    """建立versioned、query-bound且固定五分鐘TTL的canonical payload。"""
    if type(位置) is not 管理員呼叫游標位置 or type(簽發時間) is not float:
        raise ValueError
    安全位置 = 管理員呼叫游標位置(*(object.__getattribute__(位置, 名稱)
                                    for 名稱 in 管理員呼叫游標位置.__slots__))
    return {"v": 2, "scope": _建立scope(條件),
            "position": [安全位置.建立時間, 安全位置.呼叫識別碼],
            "iat": 簽發時間, "exp": 簽發時間 + 300}


def _讀取游標時鐘(時鐘) -> float:
    """只接受server clock回傳的非負finite numeric timestamp。"""
    現在 = 時鐘()
    if type(現在) not in (int, float) or not math.isfinite(現在) or 現在 < 0:
        raise ValueError
    return float(現在)


def _編碼canonical_JSON(值: object) -> bytes:
    """使用游標唯一JSON表示，拒絕NaN/Infinity並固定排序與空白。"""
    return json.dumps(
        值, ensure_ascii=True, sort_keys=True, separators=(",", ":"), allow_nan=False,
    ).encode("ascii")


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
