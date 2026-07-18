"""SQLite 呼叫紀錄的擁有者安全與管理員原始查詢投影。"""

from __future__ import annotations

import json
import math
import os
import sqlite3
import stat
from types import BuiltinFunctionType, FunctionType
from urllib.parse import quote
from typing import Any

from ..契約 import 附加稽核事件或失敗關閉
from ..協定 import AuditEventSink
from ..領域模型 import AuditActorRef, AuditEvent, AuditMetadata, AuditResourceRef
from ..領域模型 import InvocationRef, PublishedUsage
from .稽核結構 import _LEDGER

_控制流程 = (KeyboardInterrupt, SystemExit, GeneratorExit)
_固定錯誤 = "呼叫紀錄不可取得"
_建立連線 = sqlite3.connect
_最大JSON位元組 = 1_048_576
_最大JSON節點 = 4096
_最大子列 = 4096
_欄位指紋 = {
    "published_endpoints": (
        ("id", "TEXT", 0, None, 1), ("owner_user_id", "TEXT", 1, None, 0),
        ("service_account_id", "TEXT", 1, None, 0), ("slug", "TEXT", 1, None, 0),
        ("status", "TEXT", 1, None, 0), ("current_version_id", "TEXT", 0, None, 0),
        ("created_at", "REAL", 1, None, 0), ("updated_at", "REAL", 1, None, 0),
        ("rate_limit_requests", "INTEGER", 1, "60", 0),
        ("rate_limit_window_seconds", "INTEGER", 1, "60", 0),
    ),
    "endpoint_invocations": (
        ("id", "TEXT", 0, None, 1), ("endpoint_id", "TEXT", 1, None, 0),
        ("endpoint_version_id", "TEXT", 1, None, 0), ("credential_id", "TEXT", 0, None, 0),
        ("request_id", "TEXT", 1, None, 0), ("session_id", "TEXT", 0, None, 0),
        ("message_id", "TEXT", 0, None, 0), ("status", "TEXT", 1, None, 0),
        ("input_json", "TEXT", 1, None, 0), ("metadata_json", "TEXT", 0, None, 0),
        ("output_json", "TEXT", 0, None, 0), ("error_json", "TEXT", 0, None, 0),
        ("usage_json", "TEXT", 0, None, 0), ("metadata_size_bytes", "INTEGER", 0, None, 0),
        ("metadata_sha256", "TEXT", 0, None, 0), ("latency_ms", "REAL", 0, None, 0),
        ("pricing_version", "TEXT", 0, None, 0), ("created_at", "REAL", 1, None, 0),
        ("completed_at", "REAL", 0, None, 0),
    ),
    "run_events": (
        ("id", "TEXT", 0, None, 1), ("invocation_id", "TEXT", 1, None, 0),
        ("sequence_number", "INTEGER", 1, None, 0), ("event_type", "TEXT", 1, None, 0),
        ("payload_json", "TEXT", 1, None, 0), ("created_at", "REAL", 1, None, 0),
    ),
    "endpoint_tool_calls": (
        ("id", "TEXT", 0, None, 1), ("invocation_id", "TEXT", 1, None, 0),
        ("run_event_id", "TEXT", 0, None, 0), ("sequence_number", "INTEGER", 1, None, 0),
        ("tool_name", "TEXT", 1, None, 0), ("arguments_json", "TEXT", 1, None, 0),
        ("outcome", "TEXT", 1, None, 0), ("result_json", "TEXT", 0, None, 0),
        ("error_json", "TEXT", 0, None, 0), ("latency_ms", "REAL", 0, None, 0),
        ("retry_of_tool_call_id", "TEXT", 0, None, 0), ("created_at", "REAL", 1, None, 0),
    ),
}
_外鍵指紋 = {
    "published_endpoints": (
        (0, 0, "published_endpoint_versions", "current_version_id", "id", "NO ACTION", "NO ACTION", "NONE"),
        (0, 1, "published_endpoint_versions", "id", "endpoint_id", "NO ACTION", "NO ACTION", "NONE"),
        (1, 0, "service_accounts", "service_account_id", "id", "NO ACTION", "NO ACTION", "NONE"),
    ),
    "endpoint_invocations": (
        (0, 0, "endpoint_credentials", "credential_id", "id", "NO ACTION", "NO ACTION", "NONE"),
        (0, 1, "endpoint_credentials", "endpoint_id", "endpoint_id", "NO ACTION", "NO ACTION", "NONE"),
        (1, 0, "published_endpoint_versions", "endpoint_version_id", "id", "NO ACTION", "NO ACTION", "NONE"),
        (1, 1, "published_endpoint_versions", "endpoint_id", "endpoint_id", "NO ACTION", "NO ACTION", "NONE"),
        (2, 0, "published_endpoints", "endpoint_id", "id", "NO ACTION", "RESTRICT", "NONE"),
    ),
    "run_events": (
        (0, 0, "endpoint_invocations", "invocation_id", "id", "NO ACTION", "RESTRICT", "NONE"),
    ),
    "endpoint_tool_calls": (
        (0, 0, "endpoint_tool_calls", "retry_of_tool_call_id", "id", "NO ACTION", "NO ACTION", "NONE"),
        (0, 1, "endpoint_tool_calls", "invocation_id", "invocation_id", "NO ACTION", "NO ACTION", "NONE"),
        (1, 0, "run_events", "run_event_id", "id", "NO ACTION", "NO ACTION", "NONE"),
        (1, 1, "run_events", "invocation_id", "invocation_id", "NO ACTION", "NO ACTION", "NONE"),
        (2, 0, "endpoint_invocations", "invocation_id", "id", "NO ACTION", "RESTRICT", "NONE"),
    ),
}
_索引指紋 = {
    "published_endpoints": {
        "idx_published_endpoints_owner_status": (0, "c", 0, ((1, "owner_user_id"), (4, "status"))),
        "sqlite_autoindex_published_endpoints_3": (1, "u", 0, ((3, "slug"),)),
        "sqlite_autoindex_published_endpoints_2": (1, "u", 0, ((2, "service_account_id"),)),
        "sqlite_autoindex_published_endpoints_1": (1, "pk", 0, ((0, "id"),)),
    },
    "endpoint_invocations": {
        "idx_endpoint_invocations_credential_created": (0, "c", 0, ((3, "credential_id"), (17, "created_at"))),
        "idx_endpoint_invocations_status_created": (0, "c", 0, ((7, "status"), (17, "created_at"))),
        "idx_endpoint_invocations_endpoint_created": (0, "c", 0, ((1, "endpoint_id"), (17, "created_at"))),
        "sqlite_autoindex_endpoint_invocations_2": (1, "u", 0, ((4, "request_id"),)),
        "sqlite_autoindex_endpoint_invocations_1": (1, "pk", 0, ((0, "id"),)),
    },
    "run_events": {
        "sqlite_autoindex_run_events_3": (1, "u", 0, ((0, "id"), (1, "invocation_id"))),
        "sqlite_autoindex_run_events_2": (1, "u", 0, ((1, "invocation_id"), (2, "sequence_number"))),
        "sqlite_autoindex_run_events_1": (1, "pk", 0, ((0, "id"),)),
    },
    "endpoint_tool_calls": {
        "idx_endpoint_tool_calls_invocation_created": (0, "c", 0, ((1, "invocation_id"), (11, "created_at"))),
        "sqlite_autoindex_endpoint_tool_calls_3": (1, "u", 0, ((0, "id"), (1, "invocation_id"))),
        "sqlite_autoindex_endpoint_tool_calls_2": (1, "u", 0, ((1, "invocation_id"), (3, "sequence_number"))),
        "sqlite_autoindex_endpoint_tool_calls_1": (1, "pk", 0, ((0, "id"),)),
    },
}


class 查詢投影錯誤(RuntimeError):
    """查詢投影無法安全授權或驗證資料庫時的固定錯誤。"""


class 管理員原始資料稽核閘門:
    """在任何管理員 raw detail callback 前持久提交 canonical 安全稽核。"""

    __slots__ = ("_sink", "_detail")

    def __init__(self, 稽核接收器: AuditEventSink, 原始資料detail: FunctionType | BuiltinFunctionType) -> None:
        """注入 AuditEventSink 與只接受 endpoint/invocation 識別碼的 exact function。"""
        if type(原始資料detail) not in (FunctionType, BuiltinFunctionType):
            稽核接收器 = 原始資料detail = None  # type: ignore[assignment]
            raise 查詢投影錯誤(_固定錯誤) from None
        self._sink = 稽核接收器
        self._detail = 原始資料detail

    def 查詢管理員原始資料(
        self,
        管理員授權: bool,
        管理員識別碼: str,
        請求識別碼: str,
        稽核事件識別碼: str,
        發生時間: int | float,
        端點識別碼: str,
        呼叫識別碼: str,
        /,
    ) -> dict[str, Any]:
        """先稽核 success/denied 嘗試；僅 exact True 且 receipt 已提交才取 raw。"""
        失敗 = False
        控制 = 結果 = 事件 = None
        接收器 = self._sink
        原始查詢 = self._detail
        try:
            已授權 = type(管理員授權) is bool and 管理員授權 is True
            事件 = AuditEvent(
                event_id=稽核事件識別碼,
                occurred_at=發生時間,
                action="audit.detail.view",
                outcome="success" if 已授權 else "denied",
                actor=AuditActorRef("user", 管理員識別碼),
                resource=AuditResourceRef("endpoint.invocation", 呼叫識別碼),
                request_id=請求識別碼,
                endpoint_id=端點識別碼,
                invocation_id=呼叫識別碼,
                metadata=AuditMetadata(),
            )
            附加稽核事件或失敗關閉(接收器, 事件)
            事件 = 接收器 = None
            if not 已授權:
                失敗 = True
            else:
                結果 = 原始查詢(端點識別碼, 呼叫識別碼)
                if type(結果) is not dict:
                    失敗 = True
        except _控制流程 as 捕捉控制:
            _清理控制鏈(捕捉控制)
            控制 = 捕捉控制
            捕捉控制 = None
        except BaseException:
            失敗 = True
        self = 管理員授權 = 管理員識別碼 = 請求識別碼 = 稽核事件識別碼 = None
        發生時間 = 端點識別碼 = 呼叫識別碼 = 事件 = 接收器 = 原始查詢 = None
        已授權 = False
        if 控制 is not None:
            控制盒 = [控制]
            控制 = 結果 = None
            _重拋控制(控制盒.pop())
        if 失敗 or type(結果) is not dict:
            結果 = None
            raise 查詢投影錯誤(_固定錯誤) from None
        return 結果


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
    """同一 read transaction 先核算全部 JSON 長度，再按權威鍵取 payload。"""
    連線 = 游標 = 列 = 項 = 內容列 = 事件列 = 工具列 = 結果 = None
    輸入 = 中繼資料 = 輸出 = 錯誤 = 用量 = 事件 = 工具 = None
    已開始 = 失敗 = False
    控制 = None
    預算 = [0, 0]
    子列數 = 0
    try:
        連線 = _開啟唯讀快照(路徑)
        連線.execute("BEGIN")
        已開始 = True
        _驗證路徑與結構(連線, 路徑)
        游標 = 連線.execute(
            "SELECT id,endpoint_id,endpoint_version_id,credential_id,request_id,session_id,"
            "message_id,status,metadata_size_bytes,metadata_sha256,latency_ms,pricing_version,"
            "created_at,completed_at,typeof(input_json),length(CAST(input_json AS BLOB)),"
            "typeof(metadata_json),length(CAST(metadata_json AS BLOB)),"
            "typeof(output_json),length(CAST(output_json AS BLOB)),"
            "typeof(error_json),length(CAST(error_json AS BLOB)),"
            "typeof(usage_json),length(CAST(usage_json AS BLOB)) "
            "FROM endpoint_invocations WHERE endpoint_id=? AND id=?",
            (端點識別碼, 呼叫識別碼),
        )
        列 = 游標.fetchone()
        if 游標.fetchone() is not None or type(列) is not tuple or len(列) != 24:
            raise ValueError
        _驗證管理員呼叫列(列, 預算)
        游標.close()
        事件列 = []
        游標 = 連線.execute(
            "SELECT id,sequence_number,event_type,created_at,typeof(payload_json),"
            "length(CAST(payload_json AS BLOB)) FROM run_events "
            "WHERE invocation_id=? ORDER BY sequence_number", (呼叫識別碼,),
        )
        項 = 游標.fetchone()
        while 項 is not None:
            子列數 += 1
            if 子列數 > _最大子列 or type(項) is not tuple or len(項) != 6:
                raise ValueError
            if not (_安全識別碼(項[0]) and _安全正整數(項[1])
                    and _安全文字(項[2], 256) and _安全時間(項[3])):
                raise ValueError
            _扣除JSON長度(項[4], 項[5], 預算, True)
            事件列.append(項)
            項 = 游標.fetchone()
        游標.close()
        工具列 = []
        游標 = 連線.execute(
            "SELECT id,run_event_id,sequence_number,tool_name,outcome,latency_ms,"
            "retry_of_tool_call_id,created_at,typeof(arguments_json),"
            "length(CAST(arguments_json AS BLOB)),typeof(result_json),"
            "length(CAST(result_json AS BLOB)),typeof(error_json),"
            "length(CAST(error_json AS BLOB)) FROM endpoint_tool_calls "
            "WHERE invocation_id=? ORDER BY sequence_number", (呼叫識別碼,),
        )
        項 = 游標.fetchone()
        while 項 is not None:
            子列數 += 1
            if 子列數 > _最大子列 or type(項) is not tuple or len(項) != 14:
                raise ValueError
            _驗證工具長度列(項, 預算)
            工具列.append(項)
            項 = 游標.fetchone()
        游標.close()
        游標 = 連線.execute(
            "SELECT input_json,metadata_json,output_json,error_json,usage_json "
            "FROM endpoint_invocations WHERE endpoint_id=? AND id=?",
            (端點識別碼, 呼叫識別碼),
        )
        內容列 = 游標.fetchone()
        if 游標.fetchone() is not None or type(內容列) is not tuple or len(內容列) != 5:
            raise ValueError
        輸入, 中繼資料, 輸出, 錯誤, 用量 = (
            _解析可空JSON(值, 預算, False) for 值 in 內容列
        )
        游標.close()
        事件 = []
        for 項 in 事件列:
            游標 = 連線.execute(
                "SELECT payload_json FROM run_events "
                "WHERE invocation_id=? AND id=? AND sequence_number=?",
                (呼叫識別碼, 項[0], 項[1]),
            )
            內容列 = 游標.fetchone()
            if 游標.fetchone() is not None or type(內容列) is not tuple or len(內容列) != 1:
                raise ValueError
            事件.append({"id": 項[0], "sequence_number": 項[1], "event_type": 項[2],
                         "payload": _解析可空JSON(內容列[0], 預算, False), "created_at": 項[3]})
            游標.close()
        工具 = []
        for 項 in 工具列:
            游標 = 連線.execute(
                "SELECT arguments_json,result_json,error_json FROM endpoint_tool_calls "
                "WHERE invocation_id=? AND id=? AND sequence_number=?",
                (呼叫識別碼, 項[0], 項[2]),
            )
            內容列 = 游標.fetchone()
            if 游標.fetchone() is not None or type(內容列) is not tuple or len(內容列) != 3:
                raise ValueError
            工具JSON = tuple(_解析可空JSON(值, 預算, False) for 值 in 內容列)
            工具.append({"id": 項[0], "run_event_id": 項[1], "sequence_number": 項[2],
                          "tool_name": 項[3], "arguments": 工具JSON[0], "outcome": 項[4],
                          "result": 工具JSON[1], "error": 工具JSON[2], "latency_ms": 項[5],
                          "retry_of_tool_call_id": 項[6], "created_at": 項[7]})
            游標.close()
        結果 = {
            "invocation": InvocationRef(列[0], 列[4], 列[5]).to_json(),
            "endpoint_id": 列[1], "endpoint_version_id": 列[2], "credential_id": 列[3],
            "message_id": 列[6], "status": 列[7], "input": 輸入,
            "metadata": 中繼資料, "output": 輸出, "error": 錯誤, "usage": 用量,
            "metadata_size_bytes": 列[8], "metadata_sha256": 列[9],
            "latency_ms": 列[10], "pricing_version": 列[11], "created_at": 列[12],
            "completed_at": 列[13], "run_events": 事件, "tool_calls": 工具,
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
    路徑 = 端點識別碼 = 呼叫識別碼 = 連線 = 游標 = 列 = 項 = 內容列 = None
    事件列 = 工具列 = 事件 = 工具 = 輸入 = 中繼資料 = 輸出 = 錯誤 = 用量 = None
    預算 = 清理控制 = 工具JSON = None
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
    for 資料表, 規格 in _欄位指紋.items():
        預期欄位 = tuple((序號, *欄位) for 序號, 欄位 in enumerate(規格))
        欄位列 = tuple(連線.execute(f'PRAGMA table_info("{資料表}")'))
        if 欄位列 != 預期欄位:
            raise ValueError
        外鍵列 = tuple(連線.execute(f'PRAGMA foreign_key_list("{資料表}")'))
        if 外鍵列 != _外鍵指紋[資料表]:
            raise ValueError
        索引列 = tuple(連線.execute(f'PRAGMA index_list("{資料表}")'))
        if any(type(列) is not tuple or len(列) != 5 or type(列[1]) is not str for 列 in 索引列):
            raise ValueError
        實際索引 = {}
        for 索引 in 索引列:
            索引欄位 = tuple(連線.execute(f'PRAGMA index_info("{索引[1]}")'))
            if any(type(項) is not tuple or len(項) != 3 for 項 in 索引欄位):
                raise ValueError
            實際索引[索引[1]] = (
                索引[2], 索引[3], 索引[4], tuple((項[1], 項[2]) for 項 in 索引欄位)
            )
        if 實際索引 != _索引指紋[資料表]:
            raise ValueError


def _解析可空物件(文字: Any) -> dict[str, Any]:
    """只把 SQLite exact str/NULL 解析為 exact built-in JSON object。"""
    值 = _解析可空JSON(文字)
    if 值 is None:
        return {}
    if type(值) is not dict:
        raise ValueError
    return 值


def _解析可空JSON(
    文字: Any, 預算: list[int] | None = None, 計算位元組: bool = True
) -> Any:
    """解析 SQLite exact str/NULL，共用 raw UTF-8 與 parsed-node 聚合預算。"""
    if 文字 is None:
        return None
    if type(文字) is not str:
        raise ValueError
    if 預算 is None:
        預算 = [0, 0]
    if 計算位元組:
        _累計原始JSON位元組(文字, 預算)
    值 = json.loads(
        文字,
        parse_constant=_拒絕JSON常數,
        object_pairs_hook=_建立無重複物件,
    )
    if not _JSON樹為精確內建型別(值, 0, 預算):
        raise ValueError
    return 值


def _扣除JSON長度(儲存類型: Any, 長度: Any, 預算: list[int], 必填: bool = False) -> None:
    """只接受 SQLite text/NULL 長度 metadata，先扣共用原始位元組預算。"""
    if type(儲存類型) is not str:
        raise ValueError
    if 儲存類型 == "null" and 長度 is None and not 必填:
        return
    if 儲存類型 != "text":
        raise ValueError
    if type(長度) is not int or 長度 < 0 or 預算[0] + 長度 > _最大JSON位元組:
        raise ValueError
    預算[0] += 長度


def _累計原始JSON位元組(文字: str, 預算: list[int]) -> None:
    """不建立完整 bytes 副本即精確累計 UTF-8 大小並拒絕 surrogate。"""
    總數 = 預算[0]
    for 字元 in 文字:
        碼點 = ord(字元)
        if 碼點 <= 0x7F:
            總數 += 1
        elif 碼點 <= 0x7FF:
            總數 += 2
        elif 0xD800 <= 碼點 <= 0xDFFF:
            raise ValueError
        elif 碼點 <= 0xFFFF:
            總數 += 3
        else:
            總數 += 4
        if 總數 > _最大JSON位元組:
            raise ValueError
    預算[0] = 總數


def _JSON樹為精確內建型別(值: Any, 深度: int, 預算: list[int]) -> bool:
    """遞迴拒絕超深、聚合過多或非 exact/finite built-in JSON 值。"""
    預算[1] += 1
    if 預算[1] > _最大JSON節點 or 深度 > 16:
        return False
    if 值 is None or type(值) in (bool, int):
        return True
    if type(值) is float:
        return math.isfinite(值)
    if type(值) is str:
        return len(值.encode("utf-8")) <= _最大JSON位元組
    if type(值) is list:
        return all(_JSON樹為精確內建型別(項目, 深度 + 1, 預算) for 項目 in 值)
    if type(值) is dict:
        return all(
            type(鍵) is str and len(鍵.encode("utf-8")) <= 4096
            and _JSON樹為精確內建型別(項目, 深度 + 1, 預算)
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
    """逐列讀取 bounded child rows，避免一次配置最多 4097 個敵對值。"""
    結果 = []
    項 = 游標.fetchone()
    while 項 is not None:
        if len(結果) >= _最大子列 or type(項) is not tuple or len(項) != 欄寬:
            raise ValueError
        結果.append(項)
        項 = 游標.fetchone()
    return tuple(結果)


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


def _驗證管理員呼叫列(列: tuple[Any, ...], 預算: list[int]) -> None:
    """驗證 authoritative invocation 安全純量與 JSON 長度 metadata。"""
    for 索引 in (0, 1, 2, 4, 7):
        if not _安全文字(列[索引]):
            raise ValueError
    for 索引 in (3, 5, 6, 9, 11):
        if not _安全文字(列[索引], 256, True):
            raise ValueError
    if 列[8] is not None and (type(列[8]) is not int or 列[8] < 0):
        raise ValueError
    for 索引 in (10, 12, 13):
        if not _安全時間(列[索引], 索引 in (10, 13)):
            raise ValueError
    for 索引 in range(14, 24, 2):
        _扣除JSON長度(列[索引], 列[索引 + 1], 預算, 索引 == 14)


def _驗證工具長度列(列: tuple[Any, ...], 預算: list[int]) -> None:
    """驗證 tool 安全純量與三個 JSON 的 length-first metadata。"""
    if not (_安全識別碼(列[0]) and _安全文字(列[1], 可空=True)
            and _安全正整數(列[2]) and _安全文字(列[3], 256)
            and 列[4] in ("success", "error") and type(列[4]) is str
            and _安全時間(列[5], True) and _安全文字(列[6], 可空=True)
            and _安全時間(列[7])):
        raise ValueError
    if (列[4] == "success") != (列[10] == "text" and 列[12] == "null"):
        raise ValueError
    _扣除JSON長度(列[8], 列[9], 預算, True)
    _扣除JSON長度(列[10], 列[11], 預算)
    _扣除JSON長度(列[12], 列[13], 預算)


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
