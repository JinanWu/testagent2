"""SQLite 呼叫紀錄的擁有者安全與管理員原始查詢投影。"""

from __future__ import annotations

import json
import math
import os
import sqlite3
import stat
from urllib.parse import quote
from typing import Any

from ..領域模型 import InvocationRef, PublishedUsage
from .稽核結構 import _LEDGER

_控制流程 = (KeyboardInterrupt, SystemExit, GeneratorExit)
_固定錯誤 = "呼叫紀錄不可取得"
_建立連線 = sqlite3.connect
_最大JSON位元組 = 1_048_576
_最大JSON節點 = 4096
_最大子列 = 4096
_必要索引 = {
    "published_endpoints": frozenset(("idx_published_endpoints_owner_status",)),
    "endpoint_invocations": frozenset((
        "idx_endpoint_invocations_endpoint_created",
        "idx_endpoint_invocations_status_created",
        "idx_endpoint_invocations_credential_created",
    )),
    "endpoint_tool_calls": frozenset(("idx_endpoint_tool_calls_invocation_created",)),
}
_資料表欄位 = {
    "published_endpoints": (
        "id", "owner_user_id", "service_account_id", "slug", "status",
        "current_version_id", "created_at", "updated_at", "rate_limit_requests",
        "rate_limit_window_seconds",
    ),
    "endpoint_invocations": (
        "id", "endpoint_id", "endpoint_version_id", "credential_id", "request_id",
        "session_id", "message_id", "status", "input_json", "metadata_json",
        "output_json", "error_json", "usage_json", "metadata_size_bytes",
        "metadata_sha256", "latency_ms", "pricing_version", "created_at", "completed_at",
    ),
    "endpoint_tool_calls": (
        "id", "invocation_id", "run_event_id", "sequence_number", "tool_name",
        "arguments_json", "outcome", "result_json", "error_json", "latency_ms",
        "retry_of_tool_call_id", "created_at",
    ),
    "run_events": (
        "id", "invocation_id", "sequence_number", "event_type", "payload_json", "created_at",
    ),
}


class 查詢投影錯誤(RuntimeError):
    """查詢投影無法安全授權或驗證資料庫時的固定錯誤。"""


class SQLite呼叫查詢投影:
    """從既有 SQLite 快照產生角色分離、transport-neutral 的呼叫投影。"""

    __slots__ = ("_path",)

    def __init__(self, 資料庫路徑: str) -> None:
        """捕捉 exact absolute-like database path，不在建構時開啟檔案。"""
        if type(資料庫路徑) is not str or not 資料庫路徑 or 資料庫路徑.startswith("~"):
            資料庫路徑 = None  # type: ignore[assignment]
            raise 查詢投影錯誤(_固定錯誤) from None
        self._path = 資料庫路徑

    def 查詢擁有者診斷(
        self, 擁有者識別碼: str, 端點識別碼: str, 呼叫識別碼: str, /
    ) -> dict[str, Any]:
        """以 owner+endpoint+invocation 同一 SQL gate 回傳 R48 固定安全欄位。"""
        連線 = 游標 = 資料列 = 工具列 = None
        錯誤資料 = 用量資料 = 結果 = None
        已開始 = False
        失敗 = False
        控制 = None
        路徑 = self._path
        try:
            if not all(_安全識別碼(值) for 值 in (擁有者識別碼, 端點識別碼, 呼叫識別碼)):
                raise ValueError
            連線 = _開啟唯讀快照(路徑)
            連線.execute("BEGIN")
            已開始 = True
            _驗證路徑與結構(連線, 路徑)
            游標 = 連線.execute(
                "SELECT i.id,i.request_id,i.session_id,i.endpoint_version_id,i.status,"
                "i.error_json,i.latency_ms,i.usage_json "
                "FROM endpoint_invocations AS i JOIN published_endpoints AS e "
                "ON e.id=i.endpoint_id WHERE e.owner_user_id=? AND e.id=? AND i.id=?",
                (擁有者識別碼, 端點識別碼, 呼叫識別碼),
            )
            資料列 = 游標.fetchone()
            if 游標.fetchone() is not None or type(資料列) is not tuple or len(資料列) != 8:
                raise ValueError
            _驗證擁有者列(資料列)
            游標.close()
            游標 = None
            游標 = 連線.execute(
                "SELECT tool_name FROM endpoint_tool_calls WHERE invocation_id=? "
                "ORDER BY sequence_number",
                (呼叫識別碼,),
            )
            工具列 = _讀取有限列(游標, 1)
            for 列 in 工具列:
                if not _安全文字(列[0], 256):
                    raise ValueError
            錯誤資料 = _解析可空物件(資料列[5])
            用量資料 = _解析可空物件(資料列[7])
            錯誤碼 = _安全可空字串(錯誤資料.get("code"))
            綱要路徑 = _安全可空字串(錯誤資料.get("schema_path"))
            總權杖數 = 用量資料.get("total_tokens")
            if 總權杖數 is not None and (type(總權杖數) is not int or 總權杖數 < 0):
                raise ValueError
            參照 = InvocationRef(資料列[0], 資料列[1], 資料列[2]).to_json()
            用量 = PublishedUsage(總權杖數).to_json()
            結果 = {
                "invocation": 參照,
                "endpoint_version_id": 資料列[3],
                "status": 資料列[4],
                "error_code": 錯誤碼,
                "schema_path": 綱要路徑,
                "latency_ms": 資料列[6],
                "usage": 用量,
                "tool_names": [列[0] for 列 in 工具列],
            }
            連線.commit()
            已開始 = False
        except _控制流程 as 捕捉控制:
            _清理控制鏈(捕捉控制)
            控制 = 捕捉控制
            捕捉控制 = None
        except BaseException:
            失敗 = True
        if 游標 is not None:
            清理控制 = _清理資源操作(游標, "close")
            if 控制 is None and 清理控制:
                控制 = 清理控制.pop()
        if 連線 is not None and 已開始:
            清理控制 = _清理資源操作(連線, "rollback")
            if 控制 is None and 清理控制:
                控制 = 清理控制.pop()
        if 連線 is not None:
            清理控制 = _清理資源操作(連線, "close")
            if 控制 is None and 清理控制:
                控制 = 清理控制.pop()
        self = 擁有者識別碼 = 端點識別碼 = 呼叫識別碼 = 路徑 = None
        連線 = 游標 = 資料列 = 工具列 = 錯誤資料 = 用量資料 = 清理控制 = None
        錯誤碼 = 綱要路徑 = 總權杖數 = 參照 = 用量 = 列 = None
        if 控制 is not None:
            控制盒 = [控制]
            控制 = 結果 = None
            _重拋控制(控制盒.pop())
        if 失敗 or type(結果) is not dict:
            結果 = None
            raise 查詢投影錯誤(_固定錯誤) from None
        return 結果

    def 查詢管理員原始資料(
        self, 管理員授權: bool, 端點識別碼: str, 呼叫識別碼: str, /
    ) -> dict[str, Any]:
        """僅在 exact admin boundary 回傳 endpoint-scoped authoritative raw payload。"""
        失敗 = False
        控制 = 結果 = None
        路徑 = self._path
        try:
            if 管理員授權 is not True or type(管理員授權) is not bool:
                raise ValueError
            if not _安全識別碼(端點識別碼) or not _安全識別碼(呼叫識別碼):
                raise ValueError
            結果 = _讀取管理員原始資料(路徑, 端點識別碼, 呼叫識別碼)
        except _控制流程 as 捕捉控制:
            _清理控制鏈(捕捉控制)
            控制 = 捕捉控制
            捕捉控制 = None
        except BaseException:
            失敗 = True
        self = 管理員授權 = 端點識別碼 = 呼叫識別碼 = 路徑 = None
        if 控制 is not None:
            控制盒 = [控制]
            控制 = 結果 = None
            _重拋控制(控制盒.pop())
        if 失敗 or type(結果) is not dict:
            結果 = None
            raise 查詢投影錯誤(_固定錯誤) from None
        return 結果


def _讀取管理員原始資料(
    路徑: str, 端點識別碼: str, 呼叫識別碼: str
) -> dict[str, Any]:
    """在單一 read transaction 以明確欄位取得 invocation 與 child payload。"""
    連線 = 游標 = 列 = 執行事件列 = 工具列 = 事件 = 工具 = 結果 = None
    已開始 = 失敗 = False
    控制 = None
    try:
        連線 = _開啟唯讀快照(路徑)
        連線.execute("BEGIN")
        已開始 = True
        _驗證路徑與結構(連線, 路徑)
        游標 = 連線.execute(
            "SELECT id,endpoint_id,endpoint_version_id,credential_id,request_id,session_id,"
            "message_id,status,input_json,metadata_json,output_json,error_json,usage_json,"
            "metadata_size_bytes,metadata_sha256,latency_ms,pricing_version,created_at,completed_at "
            "FROM endpoint_invocations WHERE endpoint_id=? AND id=?",
            (端點識別碼, 呼叫識別碼),
        )
        列 = 游標.fetchone()
        if 游標.fetchone() is not None or type(列) is not tuple or len(列) != 19:
            raise ValueError
        _驗證管理員呼叫列(列)
        游標.close()
        游標 = None
        游標 = 連線.execute(
            "SELECT id,sequence_number,event_type,payload_json,created_at FROM run_events "
            "WHERE invocation_id=? ORDER BY sequence_number", (呼叫識別碼,),
        )
        執行事件列 = _讀取有限列(游標, 5)
        for 項 in 執行事件列:
            if not (_安全識別碼(項[0]) and _安全正整數(項[1])
                    and _安全文字(項[2], 256) and _安全文字(項[3], _最大JSON位元組)
                    and _安全時間(項[4])):
                raise ValueError
            _解析可空JSON(項[3])
        游標.close()
        游標 = None
        游標 = 連線.execute(
            "SELECT id,run_event_id,sequence_number,tool_name,arguments_json,outcome,result_json,"
            "error_json,latency_ms,retry_of_tool_call_id,created_at FROM endpoint_tool_calls "
            "WHERE invocation_id=? ORDER BY sequence_number", (呼叫識別碼,),
        )
        工具列 = _讀取有限列(游標, 11)
        for 項 in 工具列:
            _驗證工具列(項)
        事件 = [
            {"id": 項[0], "sequence_number": 項[1], "event_type": 項[2],
             "payload": _解析可空JSON(項[3]), "created_at": 項[4]}
            for 項 in 執行事件列
        ]
        工具 = [
            {"id": 項[0], "run_event_id": 項[1], "sequence_number": 項[2],
             "tool_name": 項[3], "arguments": _解析可空JSON(項[4]), "outcome": 項[5],
             "result": _解析可空JSON(項[6]), "error": _解析可空JSON(項[7]),
             "latency_ms": 項[8], "retry_of_tool_call_id": 項[9], "created_at": 項[10]}
            for 項 in 工具列
        ]
        結果 = {
            "invocation": InvocationRef(列[0], 列[4], 列[5]).to_json(),
            "endpoint_id": 列[1], "endpoint_version_id": 列[2], "credential_id": 列[3],
            "message_id": 列[6], "status": 列[7], "input": _解析可空JSON(列[8]),
            "metadata": _解析可空JSON(列[9]), "output": _解析可空JSON(列[10]),
            "error": _解析可空JSON(列[11]), "usage": _解析可空JSON(列[12]),
            "metadata_size_bytes": 列[13], "metadata_sha256": 列[14],
            "latency_ms": 列[15], "pricing_version": 列[16], "created_at": 列[17],
            "completed_at": 列[18], "run_events": 事件, "tool_calls": 工具,
        }
        連線.commit()
        已開始 = False
    except _控制流程 as 捕捉控制:
        _清理控制鏈(捕捉控制)
        控制 = 捕捉控制
        捕捉控制 = None
    except BaseException:
        失敗 = True
    if 游標 is not None:
        清理控制 = _清理資源操作(游標, "close")
        if 控制 is None and 清理控制:
            控制 = 清理控制.pop()
    if 連線 is not None and 已開始:
        清理控制 = _清理資源操作(連線, "rollback")
        if 控制 is None and 清理控制:
            控制 = 清理控制.pop()
    if 連線 is not None:
        清理控制 = _清理資源操作(連線, "close")
        if 控制 is None and 清理控制:
            控制 = 清理控制.pop()
    路徑 = 端點識別碼 = 呼叫識別碼 = 連線 = 游標 = 列 = None
    執行事件列 = 工具列 = 事件 = 工具 = 項 = 清理控制 = None
    if 控制 is not None:
        控制盒 = [控制]
        控制 = 結果 = None
        _重拋控制(控制盒.pop())
    if 失敗 or type(結果) is not dict:
        結果 = None
        raise ValueError("invalid raw projection") from None
    return 結果


def _開啟唯讀快照(路徑: str) -> sqlite3.Connection:
    """只開啟既有、非空、regular、non-symlink SQLite 檔案。"""
    if type(路徑) is not str or os.path.abspath(路徑) != 路徑:
        raise ValueError
    檔案 = os.lstat(路徑)
    if not stat.S_ISREG(檔案.st_mode) or 檔案.st_size <= 0:
        raise ValueError
    實路徑 = os.path.realpath(路徑)
    if 實路徑 != os.path.abspath(路徑):
        raise ValueError
    連線 = _建立連線(
        "file:" + quote(實路徑, safe="/") + "?mode=ro",
        uri=True, isolation_level=None, timeout=30.0,
    )
    return 連線


def _驗證路徑與結構(連線: sqlite3.Connection, 路徑: str) -> None:
    """在 read transaction 內重驗 inode、完整 遷移帳本 與投影資料表欄位。"""
    可見 = os.lstat(路徑)
    資料庫列 = 連線.execute("PRAGMA database_list").fetchone()
    if type(資料庫列) is not tuple or len(資料庫列) != 3 or type(資料庫列[2]) is not str:
        raise ValueError
    實際 = os.stat(os.path.realpath(資料庫列[2]))
    if (可見.st_dev, 可見.st_ino) != (實際.st_dev, 實際.st_ino):
        raise ValueError
    遷移帳本 = tuple(連線.execute(
        "SELECT version,name FROM published_api_schema_migrations ORDER BY version"
    ))
    if 遷移帳本 != _LEDGER:
        raise ValueError
    for 資料表, 欄位 in _資料表欄位.items():
        欄位列 = tuple(連線.execute(f'PRAGMA table_info("{資料表}")'))
        if any(type(列) is not tuple or len(列) != 6 for 列 in 欄位列):
            raise ValueError
        實際欄位 = tuple(列[1] for 列 in 欄位列)
        if 實際欄位 != 欄位:
            raise ValueError
    for 資料表, 必要 in _必要索引.items():
        索引列 = tuple(連線.execute(f'PRAGMA index_list("{資料表}")'))
        if any(type(列) is not tuple or len(列) < 2 or type(列[1]) is not str for 列 in 索引列):
            raise ValueError
        if not 必要.issubset({列[1] for 列 in 索引列}):
            raise ValueError


def _解析可空物件(文字: Any) -> dict[str, Any]:
    """只把 SQLite exact str/NULL 解析為 exact built-in JSON object。"""
    值 = _解析可空JSON(文字)
    if 值 is None:
        return {}
    if type(值) is not dict:
        raise ValueError
    return 值


def _解析可空JSON(文字: Any) -> Any:
    """解析 bounded SQLite exact str/NULL，拒絕重複鍵與非有限 JSON tree。"""
    if 文字 is None:
        return None
    if type(文字) is not str or len(文字.encode("utf-8")) > _最大JSON位元組:
        raise ValueError
    值 = json.loads(
        文字,
        parse_constant=_拒絕JSON常數,
        object_pairs_hook=_建立無重複物件,
    )
    if not _JSON樹為精確內建型別(值, 0, [0]):
        raise ValueError
    return 值


def _JSON樹為精確內建型別(值: Any, 深度: int, 計數: list[int]) -> bool:
    """遞迴拒絕超深、過多或非 exact/finite built-in JSON 值。"""
    計數[0] += 1
    if 計數[0] > _最大JSON節點 or 深度 > 16:
        return False
    if 值 is None or type(值) in (bool, int):
        return True
    if type(值) is float:
        return math.isfinite(值)
    if type(值) is str:
        return len(值.encode("utf-8")) <= _最大JSON位元組
    if type(值) is list:
        return all(_JSON樹為精確內建型別(項目, 深度 + 1, 計數) for 項目 in 值)
    if type(值) is dict:
        return all(
            type(鍵) is str and len(鍵.encode("utf-8")) <= 4096
            and _JSON樹為精確內建型別(項目, 深度 + 1, 計數)
            for 鍵, 項目 in 值.items()
        )
    return False


def _建立無重複物件(項目列: list[tuple[str, Any]]) -> dict[str, Any]:
    """建立 exact dict 並拒絕重複 JSON object key。"""
    結果: dict[str, Any] = {}
    for 鍵, 值 in 項目列:
        if 鍵 in 結果:
            raise ValueError
        結果[鍵] = 值
    return 結果


def _拒絕JSON常數(_值: str) -> None:
    """拒絕 NaN 與 Infinity 等非標準、非有限 JSON 常數。"""
    raise ValueError


def _讀取有限列(游標: sqlite3.Cursor, 欄寬: int) -> tuple[tuple[Any, ...], ...]:
    """以固定上限讀取 child rows，避免查詢投影無界配置記憶體。"""
    列 = tuple(游標.fetchmany(_最大子列 + 1))
    if len(列) > _最大子列 or any(type(項) is not tuple or len(項) != 欄寬 for 項 in 列):
        raise ValueError
    return 列


def _安全文字(值: Any, 上限: int = 128, 可空: bool = False) -> bool:
    """驗證 SQLite dynamic value 為 bounded exact str/允許的 NULL。"""
    return (可空 and 值 is None) or (type(值) is str and 0 < len(值) <= 上限)


def _安全時間(值: Any, 可空: bool = False) -> bool:
    """驗證 SQLite dynamic numeric storage class 為 finite nonnegative exact number。"""
    return (可空 and 值 is None) or (
        type(值) in (int, float) and math.isfinite(值) and 值 >= 0
    )


def _安全正整數(值: Any) -> bool:
    """判斷SQLite投影純量是否為正exact int。"""
    return type(值) is int and 值 > 0


def _驗證擁有者列(列: tuple[Any, ...]) -> None:
    """拒絕 owner projection 任一 SQLite dynamic storage class 漂移。"""
    if not all(_安全文字(列[索引]) for 索引 in (0, 1, 3, 4)):
        raise ValueError
    if not _安全文字(列[2], 可空=True) or not _安全時間(列[6], 可空=True):
        raise ValueError
    if any(列[索引] is not None and type(列[索引]) is not str for 索引 in (5, 7)):
        raise ValueError


def _驗證管理員呼叫列(列: tuple[Any, ...]) -> None:
    """驗證 authoritative invocation 的全部 dynamic SQLite storage classes。"""
    for 索引 in (0, 1, 2, 4, 7):
        if not _安全文字(列[索引]):
            raise ValueError
    for 索引 in (3, 5, 6, 14, 16):
        if not _安全文字(列[索引], 256, True):
            raise ValueError
    if type(列[8]) is not str:
        raise ValueError
    for 索引 in (9, 10, 11, 12):
        if 列[索引] is not None and type(列[索引]) is not str:
            raise ValueError
    if 列[13] is not None and (type(列[13]) is not int or 列[13] < 0):
        raise ValueError
    for 索引 in (15, 17, 18):
        if not _安全時間(列[索引], 索引 in (15, 18)):
            raise ValueError


def _驗證工具列(列: tuple[Any, ...]) -> None:
    """驗證 authoritative tool row 的 shape、scalar 與 JSON storage classes。"""
    if not (_安全識別碼(列[0]) and _安全文字(列[1], 可空=True)
            and _安全正整數(列[2]) and _安全文字(列[3], 256)
            and _安全文字(列[4], _最大JSON位元組) and 列[5] in ("success", "error")
            and _安全文字(列[9], 可空=True)
            and _安全時間(列[8], True) and _安全時間(列[10])):
        raise ValueError
    if (列[5] == "success") != (列[6] is not None and 列[7] is None):
        raise ValueError
    for 索引 in (4, 6, 7):
        if 列[索引] is not None and type(列[索引]) is not str:
            raise ValueError
        _解析可空JSON(列[索引])


def _安全識別碼(值: Any) -> bool:
    """驗證查詢身分輸入為 bounded exact str。"""
    return type(值) is str and 0 < len(值) <= 128 and not any(字元.isspace() for 字元 in 值)


def _安全可空字串(值: Any) -> str | None:
    """只允許 bounded exact diagnostic string 或 None。"""
    if 值 is None:
        return None
    if type(值) is not str or len(值) > 512:
        raise ValueError
    return 值


def _清理資源操作(資源: Any, 操作: str) -> list[BaseException]:
    """best-effort close/rollback；ordinary與自訂Base不阻止後續cleanup。"""
    控制盒: list[BaseException] = []
    try:
        getattr(資源, 操作)()
    except _控制流程 as 捕捉控制:
        _清理控制鏈(捕捉控制)
        BaseException.__setattr__(捕捉控制, "__traceback__", None)
        控制盒.append(捕捉控制)
        捕捉控制 = None
    except BaseException:
        pass
    資源 = 操作 = None
    return 控制盒


def _清理控制鏈(控制: BaseException) -> None:
    """移除控制流程既有 cause/context。"""
    BaseException.__setattr__(控制, "__cause__", None)
    BaseException.__setattr__(控制, "__context__", None)
    BaseException.__setattr__(控制, "__suppress_context__", True)


def _重拋控制(控制: BaseException) -> None:
    """保留 Python 控制流程 exact identity 與 args。"""
    try:
        BaseException.__setattr__(控制, "__traceback__", None)
        raise 控制
    except _控制流程:
        控制 = None  # type: ignore[assignment]
        raise
