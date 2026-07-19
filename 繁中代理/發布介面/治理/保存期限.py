"""GOV G05 exact Gregorian 五年保存期限與唯讀候選計畫。"""
from __future__ import annotations

from calendar import isleap
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import math
import os
import sqlite3
import stat
from typing import Any
from urllib.parse import quote

from .稽核結構 import _LEDGER
from .稽核資料庫 import _清理控制鏈, _重拋控制, _開啟既有資料庫, _驗證目前路徑

_固定錯誤 = "五年保存候選無法規劃"
_建立連線 = sqlite3.connect
_最大候選 = 1000
_最大相依 = 10000
_清除固定錯誤 = "五年保存資料無法清除"
_完整結構數 = 45
_完整結構雜湊 = "fc8aa9a0e4b01b6b96eb67763d368f21615d3692ae091965aea873786848799f"
_建立寫入連線 = _開啟既有資料庫
_保存刪除guard名稱 = (
    "audit_events_no_delete", "endpoint_redactions_no_delete",
    "redacted_run_event_no_delete", "redacted_tool_call_no_delete",
)
_保存刪除guardDROP = (
    "DROP TRIGGER audit_events_no_delete",
    "DROP TRIGGER endpoint_redactions_no_delete",
    "DROP TRIGGER redacted_run_event_no_delete",
    "DROP TRIGGER redacted_tool_call_no_delete",
)
_保存刪除guardSQL = (
    "CREATE TRIGGER audit_events_no_delete BEFORE DELETE ON audit_events BEGIN\n  SELECT RAISE(ABORT, 'audit events are append only');\nEND",
    "CREATE TRIGGER endpoint_redactions_no_delete\nBEFORE DELETE ON endpoint_redactions\nBEGIN\n  SELECT RAISE(ABORT, 'redaction tombstones are append only');\nEND",
    "CREATE TRIGGER redacted_run_event_no_delete\nBEFORE DELETE ON run_events\nWHEN EXISTS (\n  SELECT 1 FROM endpoint_redactions\n  WHERE target_type='run_event' AND target_row_id=OLD.id\n)\nBEGIN\n  SELECT RAISE(ABORT, 'redacted run event identity is retained');\nEND",
    "CREATE TRIGGER redacted_tool_call_no_delete\nBEFORE DELETE ON endpoint_tool_calls\nWHEN EXISTS (\n  SELECT 1 FROM endpoint_redactions\n  WHERE target_row_id=OLD.id\n    AND target_type IN ('tool_arguments','tool_result','tool_error')\n)\nBEGIN\n  SELECT RAISE(ABORT, 'redacted tool identity is retained');\nEND",
)
_物件摘要 = (
    ("index", "idx_audit_events_retention_invocation_id", "12574b4c27e9eb623e63dcc8369717fd0e48325b78eac9d4e3f58b506e464449"),
    ("index", "idx_endpoint_invocations_retention_candidates", "2def7fde0fe646c07680c43625af0d54b6bae57f3f38e43ad9e6d33bdef64ba2"),
    ("index", "idx_endpoint_redactions_retention_invocation_id", "6840aa298208852fec984ec8bcf7f7115fbc12beaa274e24689375f234bd64e6"),
    ("index", "idx_endpoint_tool_calls_retention_invocation_id", "56afdb9fed064f0398d4205927f4ffd7017ce6f5662b99ceaabcfa34c3dc6173"),
    ("index", "idx_run_events_retention_invocation_id", "839208a6f80e818ae675641f1ab6c1b20ba463c5c1e2856eb1a50d879e082c4e"),
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

@dataclass(frozen=True, slots=True)
class 保存清除結果:
    """只揭露本批各資料類型的刪除列數。"""

    呼叫數: int
    執行事件數: int
    工具呼叫數: int
    遮蔽數: int
    稽核事件數: int

class 保存清除錯誤(RuntimeError):
    """保存資料未能確認為原子且guard完整地清除。"""

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
        """依實際期限/id 合併至多兩個索引範圍；不讀取 payload、雜湊或原因。"""
        連線 = 路徑 = 根列 = 候選列 = 結果列 = 列 = 期限 = 結果 = None
        現在 = 現在秒 = 條件 = 參數 = 已見 = 呼叫ID = None
        已開始 = 已提交 = 失敗 = False
        主要控制盒: list[BaseException] = []
        回滾控制盒: list[BaseException] = []
        關閉控制盒: list[BaseException] = []
        try:
            現在 = _UTC時間(現在epoch秒, False)
            現在秒 = 現在.timestamp()
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
            候選列 = []
            已見 = set()
            for 條件, 參數 in _候選建立時間範圍(現在):
                根列 = 連線.execute(
                    "SELECT typeof(id),id,typeof(created_at),created_at FROM endpoint_invocations "
                    f"WHERE {條件} ORDER BY created_at,id LIMIT ?",
                    (*參數, 候選上限 + 1),
                ).fetchall()
                for 列 in 根列:
                    if (type(列) is not tuple or len(列) != 4 or 列[0] != "text"
                            or 列[2] not in ("integer", "real") or not _安全識別碼(列[1])):
                        raise ValueError
                    期限 = 五年保存期限(列[3])
                    if 期限 > 現在秒:
                        raise ValueError
                    if 列[1] not in 已見:
                        已見.add(列[1])
                        候選列.append((期限, 列[1]))
            候選列.sort()
            結果列 = []
            for 期限, 呼叫ID in 候選列[:候選上限]:
                結果列.append(_建立計畫(連線, 呼叫ID, 期限, 相依上限))
            結果 = tuple(結果列)
            連線.execute("COMMIT")
            已開始 = False
            已提交 = True
        except (KeyboardInterrupt, SystemExit, GeneratorExit) as 捕捉控制:
            _清理控制鏈(捕捉控制)
            主要控制盒.append(捕捉控制)
            捕捉控制 = None
        except BaseException:
            失敗 = True
        if 連線 is not None and 已開始:
            回滾控制盒 = _回滾並取得控制(連線)
            已開始 = False
        if 連線 is not None:
            關閉控制盒, 關閉失敗 = _關閉並取得控制(連線)
            失敗 = 失敗 or 關閉失敗
            連線 = None
        self = 現在epoch秒 = 候選上限 = 相依上限 = 路徑 = None
        根列 = 候選列 = 結果列 = 列 = 期限 = 現在 = 現在秒 = None
        條件 = 參數 = 已見 = 呼叫ID = None
        if 主要控制盒 or 回滾控制盒 or 關閉控制盒:
            結果 = None
        if 主要控制盒:
            回滾控制盒.clear()
            關閉控制盒.clear()
            _重拋控制(主要控制盒.pop())
        if 回滾控制盒:
            關閉控制盒.clear()
            _重拋控制(回滾控制盒.pop())
        if 關閉控制盒:
            _重拋控制(關閉控制盒.pop())
        if 失敗 or not 已提交 or type(結果) is not tuple:
            結果 = None
            raise 保存候選規劃錯誤(_固定錯誤) from None
        return 結果


class SQLite保存清除服務:
    """自行選取到期根，並在單一 IMMEDIATE 交易內實體清除與復原guards。"""

    __slots__ = ("_path",)

    def __init__(self, 資料庫路徑: str) -> None:
        if type(資料庫路徑) is not str or not 資料庫路徑 or os.path.abspath(資料庫路徑) != 資料庫路徑:
            資料庫路徑 = None  # type: ignore[assignment]
            raise 保存清除錯誤(_清除固定錯誤) from None
        self._path = 資料庫路徑

    def 清除(self, 現在epoch秒: int | float, /, *, 批次上限: int = 100) -> 保存清除結果:
        """清除至多批次上限個根及全部相依；不接受caller候選、期限或識別碼。"""
        連線 = 路徑 = 現在 = 候選 = 呼叫ID = 參數 = 游標 = 結果 = None
        遮蔽數 = 稽核數 = 工具數 = 執行數 = 呼叫數 = 0
        已開始 = 已提交 = 失敗 = False
        主要控制盒: list[BaseException] = []
        回滾控制盒: list[BaseException] = []
        關閉控制盒: list[BaseException] = []
        try:
            現在 = _UTC時間(現在epoch秒, False)
            if type(批次上限) is not int or not 1 <= 批次上限 <= _最大候選:
                raise ValueError
            路徑 = self._path
            連線 = _建立寫入連線(路徑)
            連線.execute("BEGIN IMMEDIATE")
            已開始 = True
            _驗證目前路徑(連線, 路徑)
            _驗證完整結構(連線)
            候選 = _選取清除根(連線, 現在, 批次上限)
            if 候選:
                呼叫ID = tuple(項[1] for 項 in 候選)
                參數 = ",".join("?" for _ in 呼叫ID)
                for SQL in _保存刪除guardDROP:
                    連線.execute(SQL)
                遮蔽數 = _刪除列數(連線.execute(
                    f"DELETE FROM endpoint_redactions WHERE invocation_id IN ({參數})", 呼叫ID))
                稽核數 = _刪除列數(連線.execute(
                    f"DELETE FROM audit_events WHERE invocation_id IN ({參數})", 呼叫ID))
                工具數 = _刪除列數(連線.execute(
                    f"DELETE FROM endpoint_tool_calls WHERE invocation_id IN ({參數})", 呼叫ID))
                執行數 = _刪除列數(連線.execute(
                    f"DELETE FROM run_events WHERE invocation_id IN ({參數})", 呼叫ID))
                游標 = 連線.execute(f"DELETE FROM endpoint_invocations WHERE id IN ({參數})", 呼叫ID)
                呼叫數 = _刪除列數(游標)
                if 呼叫數 != len(呼叫ID):
                    raise ValueError
                for SQL in _保存刪除guardSQL:
                    連線.execute(SQL)
            _驗證完整結構(連線)
            結果 = 保存清除結果(呼叫數, 執行數, 工具數, 遮蔽數, 稽核數)
            連線.commit()
            已開始 = False
            已提交 = True
        except (KeyboardInterrupt, SystemExit, GeneratorExit) as 捕捉控制:
            _清理控制鏈(捕捉控制)
            主要控制盒.append(捕捉控制)
            捕捉控制 = None
        except BaseException:
            失敗 = True
        if 連線 is not None and 已開始:
            回滾控制盒 = _回滾並取得控制(連線)
            已開始 = False
        if 連線 is not None:
            關閉控制盒, 關閉失敗 = _關閉並取得控制(連線)
            if not 已提交:
                失敗 = 失敗 or 關閉失敗
            連線 = None
        self = 現在epoch秒 = 批次上限 = 路徑 = 現在 = 候選 = 呼叫ID = 參數 = 游標 = None
        遮蔽數 = 稽核數 = 工具數 = 執行數 = 呼叫數 = None
        if 主要控制盒 or 回滾控制盒 or 關閉控制盒:
            結果 = None
        if 主要控制盒:
            回滾控制盒.clear(); 關閉控制盒.clear()
            _重拋控制(主要控制盒.pop())
        if 回滾控制盒:
            關閉控制盒.clear()
            _重拋控制(回滾控制盒.pop())
        if 關閉控制盒:
            _重拋控制(關閉控制盒.pop())
        if 失敗 or not 已提交 or type(結果) is not 保存清除結果:
            結果 = None
            raise 保存清除錯誤(_清除固定錯誤) from None
        return 結果


def _選取清除根(連線: sqlite3.Connection, 現在: datetime, 上限: int) -> list[tuple[float, str]]:
    """使用G05兩個有索引範圍，自行驗證並合併實際期限/id。"""
    候選: list[tuple[float, str]] = []
    已見: set[str] = set()
    條件 = 參數 = 列組 = 列 = 期限 = None
    控制盒: list[BaseException] = []
    try:
        現在秒 = 現在.timestamp()
        for 條件, 參數 in _候選建立時間範圍(現在):
            列組 = 連線.execute(
                "SELECT typeof(id),id,typeof(created_at),created_at FROM endpoint_invocations "
                f"WHERE {條件} ORDER BY created_at,id LIMIT ?", (*參數, 上限 + 1),
            ).fetchall()
            for 列 in 列組:
                if (type(列) is not tuple or len(列) != 4 or 列[0] != "text" or 列[2] not in
                        ("integer", "real") or not _安全識別碼(列[1])):
                    raise ValueError
                期限 = 五年保存期限(列[3])
                if 期限 > 現在秒:
                    raise ValueError
                if 列[1] not in 已見:
                    已見.add(列[1]); 候選.append((期限, 列[1]))
        候選.sort()
        結果 = 候選[:上限]
    except (KeyboardInterrupt, SystemExit, GeneratorExit) as 捕捉控制:
        _清理控制鏈(捕捉控制)
        控制盒.append(捕捉控制)
        捕捉控制 = None
        結果 = []
    連線 = 現在 = 上限 = 候選 = 已見 = 條件 = 參數 = 列組 = 列 = 期限 = 現在秒 = None
    if 控制盒:
        結果 = []
        _重拋控制(控制盒.pop())
    return 結果


def _刪除列數(游標: sqlite3.Cursor) -> int:
    """取得單一DELETE的exact非負rowcount，並清除控制流程frame。"""
    控制盒: list[BaseException] = []
    結果 = None
    try:
        結果 = 游標.rowcount
        游標.close()
    except (KeyboardInterrupt, SystemExit, GeneratorExit) as 捕捉控制:
        _清理控制鏈(捕捉控制)
        控制盒.append(捕捉控制)
        捕捉控制 = None
    游標 = None
    if 控制盒:
        結果 = None
        _重拋控制(控制盒.pop())
    if type(結果) is not int or 結果 < 0:
        raise ValueError
    return 結果


def _驗證完整結構(連線: sqlite3.Connection) -> None:
    """驗證完整ledger及所有非SQLite內部table/index/trigger的exact SQL fingerprint。"""
    if tuple(連線.execute(
        "SELECT version,name FROM published_api_schema_migrations ORDER BY version LIMIT ?",
        (len(_LEDGER) + 1,),
    )) != _LEDGER:
        raise ValueError
    列組 = tuple(連線.execute(
        "SELECT type,name,tbl_name,sql FROM sqlite_master WHERE name NOT LIKE 'sqlite_%' ORDER BY type,name"
    ))
    if len(列組) != _完整結構數:
        raise ValueError
    原文 = "\n".join("\0".join("" if 值 is None else str(值) for 值 in 列) for 列 in 列組)
    if hashlib.sha256(原文.encode()).hexdigest() != _完整結構雜湊:
        raise ValueError


def _候選建立時間範圍(現在: datetime) -> tuple[tuple[str, tuple[float, ...]], ...]:
    """回傳一或兩個不重疊 UTC 範圍，完整反推已到期的建立時間。"""
    原年 = 現在.year - 5
    if 現在.month == 2 and 現在.day == 29:
        截止 = datetime(原年, 3, 1, tzinfo=timezone.utc) - timedelta(microseconds=1)
    else:
        截止 = 現在.replace(year=原年)
    範圍: list[tuple[str, tuple[float, ...]]] = [("created_at<=?", (截止.timestamp(),))]
    if 現在.month == 2 and 現在.day == 28 and isleap(原年):
        閏日起 = datetime(原年, 2, 29, tzinfo=timezone.utc)
        閏日止 = 現在.replace(year=原年, day=29)
        範圍.append(("created_at>=? AND created_at<=?", (閏日起.timestamp(), 閏日止.timestamp())))
    return tuple(範圍)

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
    連線 = 游標 = 檔案 = 實路徑 = 查詢唯讀 = None
    主要控制盒: list[BaseException] = []
    關閉控制盒: list[BaseException] = []
    失敗 = False
    try:
        檔案 = os.lstat(路徑)
        實路徑 = os.path.realpath(路徑)
        if not stat.S_ISREG(檔案.st_mode) or 檔案.st_size <= 0 or 實路徑 != 路徑:
            raise ValueError
        連線 = _建立連線("file:" + quote(實路徑, safe="/") + "?mode=ro", uri=True,
                     isolation_level=None, timeout=30.0)
        連線.execute("PRAGMA query_only=ON")
        游標 = 連線.execute("PRAGMA query_only")
        查詢唯讀 = 游標.fetchone()
        if 查詢唯讀 != (1,) or 游標.fetchone() is not None:
            raise ValueError
        游標.close()
        游標 = None
    except (KeyboardInterrupt, SystemExit, GeneratorExit) as 捕捉控制:
        _清理控制鏈(捕捉控制)
        主要控制盒.append(捕捉控制)
        捕捉控制 = None
    except BaseException:
        失敗 = True
    路徑 = 游標 = 檔案 = 實路徑 = 查詢唯讀 = None
    if (失敗 or 主要控制盒) and 連線 is not None:
        關閉控制盒, 關閉失敗 = _關閉並取得控制(連線)
        失敗 = 失敗 or 關閉失敗
        連線 = None
    if 主要控制盒:
        關閉控制盒.clear()
        _重拋控制(主要控制盒.pop())
    if 關閉控制盒:
        _重拋控制(關閉控制盒.pop())
    if 失敗 or 連線 is None:
        raise ValueError from None
    return 連線


def _回滾並取得控制(連線: sqlite3.Connection) -> list[BaseException]:
    """exact-once rollback；ordinary失敗固定化，控制流程留待precedence選擇。"""
    控制盒: list[BaseException] = []
    try:
        連線.rollback()
    except (KeyboardInterrupt, SystemExit, GeneratorExit) as 捕捉控制:
        _清理控制鏈(捕捉控制)
        BaseException.__setattr__(捕捉控制, "__traceback__", None)
        控制盒.append(捕捉控制)
        捕捉控制 = None
    except BaseException:
        pass
    連線 = None  # type: ignore[assignment]
    return 控制盒


def _關閉並取得控制(連線: sqlite3.Connection) -> tuple[list[BaseException], bool]:
    """exact-once close，分離控制流程與ordinary失敗。"""
    控制盒: list[BaseException] = []
    失敗 = False
    try:
        連線.close()
    except (KeyboardInterrupt, SystemExit, GeneratorExit) as 捕捉控制:
        _清理控制鏈(捕捉控制)
        BaseException.__setattr__(捕捉控制, "__traceback__", None)
        控制盒.append(捕捉控制)
        捕捉控制 = None
    except BaseException:
        失敗 = True
    連線 = None  # type: ignore[assignment]
    return 控制盒, 失敗

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
    if tuple(連線.execute(
        "SELECT version,name FROM published_api_schema_migrations ORDER BY version LIMIT ?",
        (len(_LEDGER) + 1,),
    )) != _LEDGER:
        raise ValueError
    參數 = ",".join("?" for _ in _物件名稱)
    物件 = tuple((類型, 名稱, hashlib.sha256(SQL.encode()).hexdigest())
               for 類型, 名稱, SQL in 連線.execute(
        f"SELECT type,name,sql FROM sqlite_master WHERE name IN ({參數}) ORDER BY type,name",
        _物件名稱,
    ))
    if 物件 != _物件摘要:
        raise ValueError
    游標 = 連線.execute("PRAGMA index_info(sqlite_autoindex_run_events_2)")
    執行索引 = tuple(游標.fetchmany(3))
    if 執行索引 != ((0, 1, "invocation_id"), (1, 2, "sequence_number")):
        raise ValueError

def _安全識別碼(值: Any) -> bool:
    """識別碼必須是 bounded exact 非空字串。"""
    return type(值) is str and 0 < len(值) <= 128 and not any(字.isspace() for 字 in 值)
