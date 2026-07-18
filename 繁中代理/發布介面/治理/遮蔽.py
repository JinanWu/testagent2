"""GOV G04 SQLite 不可逆 payload 遮蔽與墓碑帳本。"""

from __future__ import annotations

import json
import math
import re
import sqlite3
from typing import Any

from .稽核結構 import _LEDGER

_固定錯誤 = "呼叫資料無法遮蔽"
_最大JSON位元組 = 1_048_576
_目標 = {
    "invocation_input": ("endpoint_invocations", "input_json", False),
    "metadata": ("endpoint_invocations", "metadata_json", False),
    "output": ("endpoint_invocations", "output_json", False),
    "error": ("endpoint_invocations", "error_json", False),
    "run_event": ("run_events", "payload_json", True),
    "tool_arguments": ("endpoint_tool_calls", "arguments_json", True),
    "tool_result": ("endpoint_tool_calls", "result_json", True),
    "tool_error": ("endpoint_tool_calls", "error_json", True),
}
_秘密格式 = re.compile(r"(?i)(?:bearer|(?:sk|pk)[_-])|\b[0-9a-f]{64}\b")
_必要觸發器 = frozenset({
    "endpoint_redactions_require_tombstone", "endpoint_redactions_target_before_insert",
    "endpoint_redactions_no_update", "endpoint_redactions_no_delete",
    "redacted_invocation_payload_no_update", "redacted_run_event_no_update",
    "redacted_tool_call_no_update",
})


class 不可逆遮蔽錯誤(RuntimeError):
    """遮蔽請求無法安全且原子提交時的固定公開錯誤。"""


def _驗證請求(*值: Any) -> None:
    授權, *文字, 時間 = 值
    if type(授權) is not bool or 授權 is not True or type(時間) not in (int, float):
        raise ValueError
    if not math.isfinite(時間) or 時間 < 0 or 時間 > 253402300799:
        raise ValueError
    if not all(type(項) is str for 項 in 文字):
        raise ValueError
    遮蔽ID, 事件ID, 操作者, 請求ID, 呼叫ID, 類型, 列ID, 路徑, 原因 = 文字
    if 類型 not in _目標 or not all(_安全識別碼(項) for 項 in
                                    (遮蔽ID, 事件ID, 操作者, 請求ID, 呼叫ID, 列ID)):
        raise ValueError
    if len(原因) > 256 or not 原因.strip() or _秘密格式.search(原因):
        raise ValueError
    _解析路徑(路徑)


def _安全識別碼(值: Any) -> bool:
    return type(值) is str and 0 < len(值) <= 128 and not any(字元.isspace() for 字元 in 值)


def _解析路徑(路徑: str) -> tuple[str, ...]:
    if 路徑 == "":
        return ()
    if type(路徑) is not str or not 路徑.startswith("/") or len(路徑) > 4096:
        raise ValueError
    結果 = []
    for 片段 in 路徑[1:].split("/"):
        if len(結果) >= 16 or len(片段) > 256 or re.search(r"~(?![01])", 片段):
            raise ValueError
        結果.append(片段.replace("~1", "/").replace("~0", "~"))
    return tuple(結果)


def _尋找JSON位置(payload: Any, 路徑: str) -> tuple[Any, Any]:
    片段列 = _解析路徑(路徑)
    現在 = payload
    for 片段 in 片段列[:-1]:
        現在 = _下一層(現在, 片段)
    if not 片段列:
        raise ValueError
    鍵 = _索引(現在, 片段列[-1])
    _ = 現在[鍵]
    return 現在, 鍵


def _下一層(現在: Any, 片段: str) -> Any:
    return 現在[_索引(現在, 片段)]


def _索引(現在: Any, 片段: str) -> Any:
    if type(現在) is dict:
        return 片段
    if type(現在) is list and 片段.isascii() and 片段.isdigit() and (片段 == "0" or not 片段.startswith("0")):
        索引 = int(片段)
        if 索引 < len(現在):
            return 索引
    raise ValueError


def _解析JSON(文字: str) -> Any:
    值 = json.loads(文字, parse_constant=_拒絕JSON常數, object_pairs_hook=_無重複物件)
    預算 = [0]
    if not _合法JSON(值, 0, 預算):
        raise ValueError
    return 值


def _拒絕JSON常數(_值: str) -> None:
    raise ValueError


def _無重複物件(項目: list[tuple[str, Any]]) -> dict[str, Any]:
    結果: dict[str, Any] = {}
    for 鍵, 值 in 項目:
        if 鍵 in 結果:
            raise ValueError
        結果[鍵] = 值
    return 結果


def _合法JSON(值: Any, 深度: int, 預算: list[int]) -> bool:
    預算[0] += 1
    if 預算[0] > 4096 or 深度 > 16:
        return False
    if 值 is None or type(值) in (bool, int, str):
        return True
    if type(值) is float:
        return math.isfinite(值)
    if type(值) is list:
        return all(_合法JSON(項, 深度 + 1, 預算) for 項 in 值)
    if type(值) is dict:
        return all(type(鍵) is str and _合法JSON(項, 深度 + 1, 預算) for 鍵, 項 in 值.items())
    return False


def _canonical(值: Any) -> str:
    return json.dumps(值, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _相同重試(列: tuple[Any, ...], 路徑: str, 原因: str, 操作者: str) -> bool:
    return (type(列) is tuple and len(列) == 8 and 列[1] == 路徑 and 列[3] == 原因.strip()
            and 列[4] == 操作者 and 列[6] == 1 and type(列[2]) is str
            and len(列[2]) == 64 and type(列[7]) in (int, float))


def _結果(列: tuple[Any, ...], 類型: str, 列ID: str) -> dict[str, Any]:
    return {"redaction_id": 列[0], "target_type": 類型, "target_row_id": 列ID,
            "json_path": 列[1], "original_sha256": 列[2], "reason": 列[3],
            "actor_id": 列[4], "audit_event_id": 列[5], "is_tombstone": True,
            "redacted_at": 列[7]}


def _驗證遮蔽schema(連線: sqlite3.Connection) -> None:
    if tuple(連線.execute("SELECT version,name FROM published_api_schema_migrations ORDER BY version")) != _LEDGER:
        raise ValueError
    欄位 = tuple(列[1] for 列 in 連線.execute("PRAGMA table_info(endpoint_redactions)"))
    if 欄位 != ("id", "invocation_id", "target_type", "target_row_id", "json_path",
                 "original_sha256", "reason", "actor_type", "actor_id", "audit_event_id",
                 "is_tombstone", "redacted_at"):
        raise ValueError
    觸發器 = frozenset(列[0] for 列 in 連線.execute(
        "SELECT name FROM sqlite_master WHERE type='trigger' AND name LIKE '%redact%'"
    ))
    if not _必要觸發器 <= 觸發器:
        raise ValueError
