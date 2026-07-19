"""GOV G05 exact Gregorian 五年保存期限與唯讀候選計畫。"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import math
import os
import sqlite3
import stat
from typing import Any
from urllib.parse import quote

from .稽核結構 import _LEDGER

_固定錯誤 = "五年保存候選無法規劃"
_建立連線 = sqlite3.connect
_最大候選 = 1000
_最大相依 = 10000
_物件摘要 = (
    ("index", "idx_endpoint_invocations_retention_candidates", "2def7fde0fe646c07680c43625af0d54b6bae57f3f38e43ad9e6d33bdef64ba2"),
    ("table", "audit_events", "f70689d15850c48388d80779c6f0749669a376df51c46010ca6dab9b3c63c35f"),
    ("table", "endpoint_invocations", "22062cd8cb9a0b17d70cd20c46f3fd2bb9b72f157101f3465eee43453ea7ebc2"),
    ("table", "endpoint_redactions", "ead15701fb385c3c2652214c8bab6a3214ad056a10ede486b76a376092b91eec"),
    ("table", "endpoint_tool_calls", "29c45a363a31a31835379582b26b6fa38356c8eef658ddc9cba67d6a611927b0"),
    ("table", "run_events", "60af3b50baf08e18b86d235538ffc15460520279fe159c27e61c59adac4e863e"),
    ("trigger", "audit_events_no_delete", "636d6a219b02c18af1810494b69b5cd4e635f42abd07d224eec22848ba081138"),
    ("trigger", "endpoint_redactions_no_delete", "510b4591ca13baf4c281784ad75e275ba89de10ba0e2f10d4db597a6a72145d5"),
    ("trigger", "redacted_run_event_no_delete", "ea8c587b4c4873f84e7649ba3ab418649ef65d92d739683f257fb8ace909992d"),
    ("trigger", "redacted_tool_call_no_delete", "532779cd5095eb3ea00ee7812b6fb5dc6a8f2fdcdbb1b9012c91f2950803c440"),
)
_物件名稱 = tuple(項[1] for 項 in _物件摘要)

保存刪除順序 = (
    "endpoint_redactions", "audit_events", "endpoint_tool_calls",
    "run_events", "endpoint_invocations",
)

class 保存候選規劃錯誤(RuntimeError):
    """保存期限或唯讀候選無法安全、確定地規劃。"""
@dataclass(frozen=True, slots=True)
class 保存候選計畫:
    """只含識別碼、根期限、計數、刪除順序與現行阻擋器的計畫。"""

    呼叫識別碼: str
    保存期限: float
    執行事件識別碼: tuple[str, ...]
    工具呼叫識別碼: tuple[str, ...]
    遮蔽識別碼: tuple[str, ...]
    稽核事件識別碼: tuple[str, ...]
    執行事件數: int
    工具呼叫數: int
    遮蔽數: int
    稽核事件數: int
    刪除順序: tuple[str, ...]
    刪除阻擋器: tuple[str, ...]

def _UTC時間(epoch秒: Any, 可加五年: bool) -> datetime:
    """把 exact、finite、非負 epoch 秒映射為 UTC datetime。"""
    if type(epoch秒) not in (int, float):
        raise ValueError
    try:
        if not math.isfinite(epoch秒) or epoch秒 < 0:
            raise ValueError
        結果 = datetime.fromtimestamp(epoch秒, timezone.utc)
        if 可加五年 and 結果.year > 9994:
            raise ValueError
        return 結果
    except (OverflowError, OSError, ValueError):
        raise ValueError from None

def 五年保存期限(建立epoch秒: int | float, /) -> float:
    """回傳建立時間後五個 Gregorian 曆年的 UTC epoch 秒；閏日落二月二十八日。"""
    try:
        建立 = _UTC時間(建立epoch秒, True)
        try:
            到期 = 建立.replace(year=建立.year + 5)
        except ValueError:
            到期 = 建立.replace(year=建立.year + 5, month=2, day=28)
        結果 = 到期.timestamp()
    except (OverflowError, OSError, ValueError):
        建立epoch秒 = 建立 = 到期 = 結果 = None
    if 建立 is None:
        raise 保存候選規劃錯誤(_固定錯誤) from None
    建立epoch秒 = 建立 = 到期 = None
    return 結果

def 已達五年保存期限(建立epoch秒: int | float, 現在epoch秒: int | float, /) -> bool:
    """以 UTC 微秒精度判斷；now 等於期限即到期，前一微秒仍保留。"""
    try:
        現在 = _UTC時間(現在epoch秒, False)
        期限 = 五年保存期限(建立epoch秒)
        結果 = 現在.timestamp() >= 期限
    except (保存候選規劃錯誤, OverflowError, OSError, ValueError):
        建立epoch秒 = 現在epoch秒 = 現在 = 期限 = None
    if 現在 is None:
        raise 保存候選規劃錯誤(_固定錯誤) from None
    建立epoch秒 = 現在epoch秒 = 現在 = 期限 = None
    return 結果

class SQLite保存候選規劃器:
    """在同一 mode=ro read transaction 驗證結構並建立有界刪除候選。"""

    __slots__ = ("_path",)

    def __init__(self, 資料庫路徑: str) -> None:
        """保存 exact 絕對路徑；檔案存在性於每次規劃重新釘選。"""
        if type(資料庫路徑) is not str or not 資料庫路徑 or os.path.abspath(資料庫路徑) != 資料庫路徑:
            資料庫路徑 = None  # type: ignore[assignment]
            raise 保存候選規劃錯誤(_固定錯誤) from None
        self._path = 資料庫路徑

    def 規劃(
        self, 現在epoch秒: int | float, /, *, 候選上限: int = 100, 相依上限: int = 4096,
    ) -> tuple[保存候選計畫, ...]:
        """依根 created_at/id 排序回傳有界候選；不讀取任何 payload、雜湊或原因。"""
        連線 = 路徑 = 根列 = 結果 = None
        已開始 = False
        try:
            _UTC時間(現在epoch秒, False)
            if type(候選上限) is not int or not 1 <= 候選上限 <= _最大候選:
                raise ValueError
            if type(相依上限) is not int or not 1 <= 相依上限 <= _最大相依:
                raise ValueError
            路徑 = self._path
            連線 = _開啟唯讀資料庫(路徑)
            連線.execute("BEGIN")
            已開始 = True
            _驗證路徑(連線, 路徑)
            _驗證結構(連線)
            根列 = 連線.execute(
                "SELECT typeof(id),id,typeof(created_at),created_at FROM endpoint_invocations "
                "ORDER BY created_at,id LIMIT ?", (候選上限 + 1,),
            ).fetchall()
            結果列 = []
            for 列 in 根列:
                if type(列) is not tuple or len(列) != 4 or 列[0] != "text" or 列[2] not in ("integer", "real"):
                    raise ValueError
                if not _安全識別碼(列[1]):
                    raise ValueError
                期限 = 五年保存期限(列[3])
                if not 已達五年保存期限(列[3], 現在epoch秒):
                    break
                if len(結果列) >= 候選上限:
                    break
                結果列.append(_建立計畫(連線, 列[1], 期限, 相依上限))
            結果 = tuple(結果列)
            連線.execute("COMMIT")
            已開始 = False
        except (KeyboardInterrupt, SystemExit, GeneratorExit):
            raise
        except BaseException:
            結果 = None
        if 連線 is not None and 已開始:
            try:
                連線.execute("ROLLBACK")
            except BaseException:
                結果 = None
        if 連線 is not None:
            try:
                連線.close()
            except BaseException:
                結果 = None
        self = 現在epoch秒 = 候選上限 = 相依上限 = 連線 = 路徑 = 根列 = None
        if type(結果) is not tuple:
            結果 = None
            raise 保存候選規劃錯誤(_固定錯誤) from None
        return 結果

def _建立計畫(連線: sqlite3.Connection, 呼叫ID: str, 期限: float, 上限: int) -> 保存候選計畫:
    """以一個共享相依列預算讀取單一根的識別資料。"""
    剩餘 = [上限]
    執行 = _讀識別碼(連線, "run_events", 呼叫ID, 剩餘)
    工具 = _讀識別碼(連線, "endpoint_tool_calls", 呼叫ID, 剩餘)
    遮蔽, 類型 = _讀遮蔽(連線, 呼叫ID, 剩餘)
    稽核 = _讀識別碼(連線, "audit_events", 呼叫ID, 剩餘)
    阻擋 = []
    if 遮蔽:
        阻擋.append("endpoint_redactions_no_delete")
    if 稽核:
        阻擋.append("audit_events_no_delete")
    if "run_event" in 類型:
        阻擋.append("redacted_run_event_no_delete")
    if any(項.startswith("tool_") for 項 in 類型):
        阻擋.append("redacted_tool_call_no_delete")
    return 保存候選計畫(呼叫ID, 期限, 執行, 工具, 遮蔽, 稽核, len(執行), len(工具),
                    len(遮蔽), len(稽核), 保存刪除順序, tuple(阻擋))

def _讀識別碼(連線: sqlite3.Connection, 表格: str, 呼叫ID: str, 剩餘: list[int]) -> tuple[str, ...]:
    """只讀 FK 範圍內的 exact ID，並扣除共享列預算。"""
    列 = 連線.execute(
        f"SELECT typeof(id),id FROM {表格} WHERE invocation_id=? ORDER BY id LIMIT ?",
        (呼叫ID, 剩餘[0] + 1),
    ).fetchall()
    if len(列) > 剩餘[0] or any(項[0] != "text" or not _安全識別碼(項[1]) for 項 in 列):
        raise ValueError
    剩餘[0] -= len(列)
    return tuple(項[1] for 項 in 列)

def _讀遮蔽(連線: sqlite3.Connection, 呼叫ID: str, 剩餘: list[int]) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """只讀遮蔽 ID/受控 target_type，以宣告目前刪除 guard。"""
    列 = 連線.execute(
        "SELECT typeof(id),id,typeof(target_type),target_type FROM endpoint_redactions "
        "WHERE invocation_id=? ORDER BY id LIMIT ?", (呼叫ID, 剩餘[0] + 1),
    ).fetchall()
    合法類型 = {"invocation_input", "metadata", "output", "error", "run_event",
            "tool_arguments", "tool_result", "tool_error"}
    if len(列) > 剩餘[0] or any(項[0] != "text" or not _安全識別碼(項[1])
                         or 項[2] != "text" or 項[3] not in 合法類型 for 項 in 列):
        raise ValueError
    剩餘[0] -= len(列)
    return tuple(項[1] for 項 in 列), tuple(項[3] for 項 in 列)

def _開啟唯讀資料庫(路徑: str) -> sqlite3.Connection:
    """以 mode=ro 開啟既有、非空、regular、non-symlink SQLite 檔案。"""
    檔案 = os.lstat(路徑)
    實路徑 = os.path.realpath(路徑)
    if not stat.S_ISREG(檔案.st_mode) or 檔案.st_size <= 0 or 實路徑 != 路徑:
        raise ValueError
    連線 = _建立連線("file:" + quote(實路徑, safe="/") + "?mode=ro", uri=True,
                 isolation_level=None, timeout=30.0)
    連線.execute("PRAGMA query_only=ON")
    return 連線

def _驗證路徑(連線: sqlite3.Connection, 路徑: str) -> None:
    """在 read transaction 內確認可見路徑與連線仍為同一 inode。"""
    可見 = os.lstat(路徑)
    資料庫列 = 連線.execute("PRAGMA database_list").fetchone()
    if type(資料庫列) is not tuple or len(資料庫列) != 3 or type(資料庫列[2]) is not str:
        raise ValueError
    實際 = os.stat(os.path.realpath(資料庫列[2]))
    if (可見.st_dev, 可見.st_ino) != (實際.st_dev, 實際.st_ino):
        raise ValueError

def _驗證結構(連線: sqlite3.Connection) -> None:
    """在候選相同 read transaction 驗證完整 ledger 與相關 table/index/guard SQL。"""
    if tuple(連線.execute("SELECT version,name FROM published_api_schema_migrations ORDER BY version")) != _LEDGER:
        raise ValueError
    參數 = ",".join("?" for _ in _物件名稱)
    物件 = tuple((類型, 名稱, hashlib.sha256(SQL.encode()).hexdigest())
               for 類型, 名稱, SQL in 連線.execute(
        f"SELECT type,name,sql FROM sqlite_master WHERE name IN ({參數}) ORDER BY type,name",
        _物件名稱,
    ))
    if 物件 != _物件摘要:
        raise ValueError

def _安全識別碼(值: Any) -> bool:
    """識別碼必須是 bounded exact 非空字串。"""
    return type(值) is str and 0 < len(值) <= 128 and not any(字.isspace() for 字 in 值)
