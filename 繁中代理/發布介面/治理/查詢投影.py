"""SQLite 呼叫紀錄的擁有者安全與管理員原始查詢投影。"""

from __future__ import annotations

import json
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


class ProjectionAccessError(RuntimeError):
    """查詢投影無法安全授權或驗證資料庫時的固定錯誤。"""


class SQLite呼叫查詢投影:
    """從既有 SQLite 快照產生角色分離、transport-neutral 的呼叫投影。"""

    __slots__ = ("_path",)

    def __init__(self, 資料庫路徑: str) -> None:
        """捕捉 exact absolute-like database path，不在建構時開啟檔案。"""
        if type(資料庫路徑) is not str or not 資料庫路徑 or 資料庫路徑.startswith("~"):
            資料庫路徑 = None  # type: ignore[assignment]
            raise ProjectionAccessError(_固定錯誤) from None
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
            游標.close()
            游標 = 連線.execute(
                "SELECT tool_name FROM endpoint_tool_calls WHERE invocation_id=? "
                "ORDER BY sequence_number",
                (呼叫識別碼,),
            )
            工具列 = tuple(游標.fetchall())
            if any(type(列) is not tuple or len(列) != 1 or type(列[0]) is not str for 列 in 工具列):
                raise ValueError
            錯誤資料 = _解析可空物件(資料列[5])
            用量資料 = _解析可空物件(資料列[7])
            error_code = _安全可空字串(錯誤資料.get("code"))
            schema_path = _安全可空字串(錯誤資料.get("schema_path"))
            total_tokens = 用量資料.get("total_tokens")
            if total_tokens is not None and (type(total_tokens) is not int or total_tokens < 0):
                raise ValueError
            參照 = InvocationRef(資料列[0], 資料列[1], 資料列[2]).to_json()
            用量 = PublishedUsage(total_tokens).to_json()
            結果 = {
                "invocation": 參照,
                "endpoint_version_id": 資料列[3],
                "status": 資料列[4],
                "error_code": error_code,
                "schema_path": schema_path,
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
            try:
                游標.close()
            except _控制流程 as 捕捉控制:
                if 控制 is None:
                    _清理控制鏈(捕捉控制)
                    控制 = 捕捉控制
                捕捉控制 = None
            except BaseException:
                失敗 = True
        if 連線 is not None and 已開始:
            try:
                連線.rollback()
            except _控制流程 as 捕捉控制:
                if 控制 is None:
                    _清理控制鏈(捕捉控制)
                    控制 = 捕捉控制
                捕捉控制 = None
            except BaseException:
                失敗 = True
        if 連線 is not None:
            try:
                連線.close()
            except _控制流程 as 捕捉控制:
                if 控制 is None:
                    _清理控制鏈(捕捉控制)
                    控制 = 捕捉控制
                捕捉控制 = None
            except BaseException:
                失敗 = True
        self = 擁有者識別碼 = 端點識別碼 = 呼叫識別碼 = 路徑 = None
        連線 = 游標 = 資料列 = 工具列 = 錯誤資料 = 用量資料 = None
        if 控制 is not None:
            控制盒 = [控制]
            控制 = 結果 = None
            _重拋控制(控制盒.pop())
        if 失敗 or type(結果) is not dict:
            結果 = None
            raise ProjectionAccessError(_固定錯誤) from None
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
            raise ProjectionAccessError(_固定錯誤) from None
        return 結果


def _讀取管理員原始資料(
    路徑: str, 端點識別碼: str, 呼叫識別碼: str
) -> dict[str, Any]:
    """在單一 read transaction 以明確欄位取得 invocation 與 child payload。"""
    連線 = 游標 = None
    已開始 = False
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
        游標.close()
        游標 = 連線.execute(
            "SELECT id,sequence_number,event_type,payload_json,created_at FROM run_events "
            "WHERE invocation_id=? ORDER BY sequence_number", (呼叫識別碼,),
        )
        執行事件列 = tuple(游標.fetchall())
        游標.close()
        游標 = 連線.execute(
            "SELECT id,run_event_id,sequence_number,tool_name,arguments_json,outcome,result_json,"
            "error_json,latency_ms,retry_of_tool_call_id,created_at FROM endpoint_tool_calls "
            "WHERE invocation_id=? ORDER BY sequence_number", (呼叫識別碼,),
        )
        工具列 = tuple(游標.fetchall())
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
        return 結果
    finally:
        if 游標 is not None:
            游標.close()
        if 連線 is not None and 已開始:
            連線.rollback()
        if 連線 is not None:
            連線.close()


def _開啟唯讀快照(路徑: str) -> sqlite3.Connection:
    """只開啟既有、非空、regular、non-symlink SQLite 檔案。"""
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
    """在 read transaction 內重驗 inode、完整 ledger 與投影資料表欄位。"""
    可見 = os.lstat(路徑)
    資料庫列 = 連線.execute("PRAGMA database_list").fetchone()
    if type(資料庫列) is not tuple or len(資料庫列) != 3 or type(資料庫列[2]) is not str:
        raise ValueError
    實際 = os.stat(os.path.realpath(資料庫列[2]))
    if (可見.st_dev, 可見.st_ino) != (實際.st_dev, 實際.st_ino):
        raise ValueError
    ledger = tuple(連線.execute(
        "SELECT version,name FROM published_api_schema_migrations ORDER BY version"
    ))
    if ledger != _LEDGER:
        raise ValueError
    for 資料表, 欄位 in _資料表欄位.items():
        實際欄位 = tuple(列[1] for 列 in 連線.execute(f'PRAGMA table_info("{資料表}")'))
        if 實際欄位 != 欄位:
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
    """解析 SQLite exact str/NULL，並拒絕非 exact built-in JSON tree。"""
    if 文字 is None:
        return None
    if type(文字) is not str:
        raise ValueError
    值 = json.loads(文字)
    if not _exact_json(值):
        raise ValueError
    return 值


def _exact_json(值: Any) -> bool:
    """遞迴拒絕非 exact built-in JSON 值。"""
    if 值 is None or type(值) in (str, bool, int, float):
        return True
    if type(值) is list:
        return all(_exact_json(項目) for 項目 in 值)
    if type(值) is dict:
        return all(type(鍵) is str and _exact_json(項目) for 鍵, 項目 in 值.items())
    return False


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
