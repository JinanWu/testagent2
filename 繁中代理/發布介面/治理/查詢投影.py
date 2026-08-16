"""SQLite 呼叫紀錄的擁有者安全與管理員原始查詢投影。"""

from __future__ import annotations

import json
import hashlib
import math
import os
import re
import sqlite3
import stat
import sys
from types import BuiltinFunctionType, FunctionType
from urllib.parse import quote
from typing import Any

from ..契約 import 附加稽核事件或失敗關閉
from ..協定 import AuditEventSink
from ..領域模型 import AuditActorRef, AuditEvent, AuditMetadata, AuditResourceRef
from ..領域模型 import InvocationRef, PublishedUsage
from .稽核結構 import _LEDGER
from .遮蔽 import _解析路徑, _驗證遮蔽schema
from .管理查詢契約 import (
    ADMIN_INVOCATION_AUDIT_ACTION,
    管理員呼叫列表項目,
    管理員呼叫投影頁,
    管理員呼叫查詢條件,
    管理員呼叫游標位置,
    管理員呼叫不存在錯誤,
    管理員呼叫查詢錯誤,
    管理員呼叫稽核錯誤,
    管理員拒絕稽核收據,
    管理員拒絕稽核收據權威,
    查詢投影錯誤,
    管理員呼叫完整詳情,
    擁有者安全詳情,
    建立管理員呼叫完整詳情,
    建立擁有者安全詳情,
    _驗證完整詳情墓碑一致性,
)

_控制流程 = (KeyboardInterrupt, SystemExit, GeneratorExit)
_固定錯誤 = "呼叫紀錄不可取得"
_建立連線 = sqlite3.connect
_最大JSON位元組 = 1_048_576
_最大JSON節點 = 4096
_最大JSON深度 = 128
_最大子列 = 4096
_最大敏感命中 = 1024
_最大安全整數 = 2**53 - 1
_最大安全時間 = 253_402_300_799
_敏感命中物件摘要 = (
    ("index", "idx_invocation_sensitive_hits_admin_sort", "e8acbd29e01fc9109b1318397e59dcd2124ff3ccdb538cf3759dee711d4c55bc"),
    ("index", "uq_invocation_sensitive_hits_with_tool", "63b908e94a920d183a97452d52e08b4c446a4aeeefbe1881115fcf9c516c437f"),
    ("index", "uq_invocation_sensitive_hits_without_tool", "90bcc4f208888aa3dc1aa0bddc8e45fa8490e79634decd0a99ddea07324ba851"),
    ("table", "invocation_sensitive_hits", "73de75e6c13657264a48fc1c7bbd64696fa8e4fe5af84f2a38d6761c8908771d"),
    ("trigger", "invocation_sensitive_hits_audit_scope_before_insert", "e6a7124820053a15099dd2478504a0953853d5adbd1488cfd38b8bc2ad570a05"),
    ("trigger", "invocation_sensitive_hits_no_delete", "6d0b62ae901c1e33a3f3e999f31f82650d2a21e2a9133f715143087681342425"),
    ("trigger", "invocation_sensitive_hits_no_update", "d9a19fb87d4bc6ca82632a2e1fa1a5b0aeab55b0650602670749f7d624a84dd5"),
)
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
    "endpoint_invocation_safe_errors": (
        ("invocation_id", "TEXT", 0, None, 1),
        ("error_code", "TEXT", 1, None, 0),
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
    "endpoint_invocation_safe_errors": (
        (0, 0, "endpoint_invocations", "invocation_id", "id", "NO ACTION", "CASCADE", "NONE"),
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
        "idx_endpoint_invocations_retention_candidates": (0, "c", 0, ((17, "created_at"), (0, "id"))),
        "idx_endpoint_invocations_credential_created": (0, "c", 0, ((3, "credential_id"), (17, "created_at"))),
        "idx_endpoint_invocations_status_created": (0, "c", 0, ((7, "status"), (17, "created_at"))),
        "idx_endpoint_invocations_endpoint_created": (0, "c", 0, ((1, "endpoint_id"), (17, "created_at"))),
        "sqlite_autoindex_endpoint_invocations_2": (1, "u", 0, ((4, "request_id"),)),
        "sqlite_autoindex_endpoint_invocations_1": (1, "pk", 0, ((0, "id"),)),
    },
    "endpoint_invocation_safe_errors": {
        "idx_endpoint_invocation_safe_errors_code": (0, "c", 0, ((1, "error_code"), (0, "invocation_id"))),
        "sqlite_autoindex_endpoint_invocation_safe_errors_1": (1, "pk", 0, ((0, "invocation_id"),)),
    },
    "run_events": {
        "idx_run_events_retention_invocation_id": (0, "c", 0, ((1, "invocation_id"), (0, "id"))),
        "sqlite_autoindex_run_events_3": (1, "u", 0, ((0, "id"), (1, "invocation_id"))),
        "sqlite_autoindex_run_events_2": (1, "u", 0, ((1, "invocation_id"), (2, "sequence_number"))),
        "sqlite_autoindex_run_events_1": (1, "pk", 0, ((0, "id"),)),
    },
    "endpoint_tool_calls": {
        "idx_endpoint_tool_calls_retention_invocation_id": (0, "c", 0, ((1, "invocation_id"), (0, "id"))),
        "idx_endpoint_tool_calls_invocation_created": (0, "c", 0, ((1, "invocation_id"), (11, "created_at"))),
        "sqlite_autoindex_endpoint_tool_calls_3": (1, "u", 0, ((0, "id"), (1, "invocation_id"))),
        "sqlite_autoindex_endpoint_tool_calls_2": (1, "u", 0, ((1, "invocation_id"), (3, "sequence_number"))),
        "sqlite_autoindex_endpoint_tool_calls_1": (1, "pk", 0, ((0, "id"),)),
    },
}


class 管理員原始資料稽核閘門:
    """在任何管理員 raw detail callback 前持久提交 canonical 安全稽核。"""

    __slots__ = ("_sink", "_detail", "_pairing_exists", "_receipt_authority")

    def __init__(
        self,
        稽核接收器: AuditEventSink,
        原始資料detail: FunctionType | BuiltinFunctionType,
        配對存在: FunctionType | BuiltinFunctionType,
        收據權威: 管理員拒絕稽核收據權威 | None = None,
    ) -> None:
        """注入audit sink、raw detail與不讀raw的endpoint/invocation配對判定。"""
        if 收據權威 is None:
            收據權威 = 管理員拒絕稽核收據權威(os.urandom(32))
        if (
            type(原始資料detail) not in (FunctionType, BuiltinFunctionType)
            or type(配對存在) not in (FunctionType, BuiltinFunctionType)
            or type(收據權威) is not 管理員拒絕稽核收據權威
        ):
            稽核接收器 = 原始資料detail = 配對存在 = None  # type: ignore[assignment]
            raise 查詢投影錯誤(_固定錯誤) from None
        self._sink = 稽核接收器
        self._detail = 原始資料detail
        self._pairing_exists = 配對存在
        self._receipt_authority = 收據權威

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
    ) -> 管理員呼叫完整詳情 | 管理員拒絕稽核收據:
        """先稽核 success/denied 嘗試；僅 exact True 且 receipt 已提交才取 raw。"""
        失敗 = 稽核已提交 = 配對已判定 = 原始讀取已開始 = 已授權 = 配對存在 = False
        結果類型 = None
        控制 = 結果 = 事件 = None
        接收器 = self._sink
        原始查詢 = self._detail
        配對查詢 = self._pairing_exists
        收據權威 = self._receipt_authority
        try:
            已授權 = type(管理員授權) is bool and 管理員授權 is True
            配對存在 = False
            if 已授權:
                配對存在 = 配對查詢(端點識別碼, 呼叫識別碼)
                if type(配對存在) is not bool:
                    raise ValueError
                配對已判定 = True
            事件 = AuditEvent(
                event_id=稽核事件識別碼,
                occurred_at=發生時間,
                action=ADMIN_INVOCATION_AUDIT_ACTION,
                outcome="success" if 已授權 else "denied",
                actor=AuditActorRef("user", 管理員識別碼),
                resource=AuditResourceRef("endpoint.invocation", 呼叫識別碼),
                request_id=請求識別碼,
                endpoint_id=端點識別碼 if 配對存在 else None,
                invocation_id=呼叫識別碼 if 配對存在 else None,
                metadata=AuditMetadata(),
            )
            附加稽核事件或失敗關閉(接收器, 事件)
            稽核已提交 = True
            事件 = 接收器 = None
            if not 已授權:
                結果 = 收據權威.簽發(
                    管理員識別碼, 請求識別碼, 稽核事件識別碼, 發生時間,
                    端點識別碼, 呼叫識別碼,
                )
            elif not 配對存在:
                結果類型 = 管理員呼叫不存在錯誤
                失敗 = True
            else:
                原始讀取已開始 = True
                結果 = 建立管理員呼叫完整詳情(
                    原始查詢(端點識別碼, 呼叫識別碼)
                )
        except _控制流程 as 捕捉控制:
            _清理控制鏈(捕捉控制)
            控制 = 捕捉控制
            捕捉控制 = None
        except BaseException as 捕捉:
            if 已授權 and not 配對已判定:
                結果類型 = 管理員呼叫查詢錯誤
            elif not 稽核已提交:
                結果類型 = 管理員呼叫稽核錯誤
            elif 原始讀取已開始:
                結果類型 = 管理員呼叫查詢錯誤
            elif type(捕捉) in (管理員呼叫不存在錯誤, 管理員呼叫查詢錯誤):
                結果類型 = type(捕捉)
            else:
                結果類型 = 管理員呼叫查詢錯誤
            捕捉 = None
            失敗 = True
        self = 管理員授權 = 管理員識別碼 = 請求識別碼 = 稽核事件識別碼 = None
        發生時間 = 端點識別碼 = 呼叫識別碼 = 事件 = 接收器 = 原始查詢 = 配對查詢 = None  # type: ignore[assignment]
        已授權 = 配對存在 = 稽核已提交 = 配對已判定 = 原始讀取已開始 = False
        if 控制 is not None:
            控制盒 = [控制]
            控制 = 結果 = None
            _重拋控制(控制盒.pop())
        if type(結果) is 管理員拒絕稽核收據:
            return 結果
        if 失敗 or type(結果) is not 管理員呼叫完整詳情:
            結果 = None
            if 結果類型 is 管理員呼叫稽核錯誤:
                raise 管理員呼叫稽核錯誤("呼叫紀錄暫時不可取得") from None
            if 結果類型 is 管理員呼叫不存在錯誤:
                raise 管理員呼叫不存在錯誤("找不到呼叫紀錄") from None
            raise 管理員呼叫查詢錯誤(_固定錯誤) from None
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

    def 查詢擁有者安全詳情(
        self, 擁有者識別碼: str, 端點識別碼: str, 呼叫識別碼: str, /
    ) -> 擁有者安全詳情:
        """以既有owner composite authority投影重建獨立typed safe DTO。"""
        return 建立擁有者安全詳情(
            self.查詢擁有者診斷(擁有者識別碼, 端點識別碼, 呼叫識別碼)
        )

    def 查詢管理員原始資料(
        self, 管理員授權: bool, 端點識別碼: str, 呼叫識別碼: str, /
    ) -> dict[str, Any]:
        """僅在 exact admin boundary 回傳 endpoint-scoped authoritative raw payload。"""
        失敗 = 不存在 = False
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
        except 管理員呼叫不存在錯誤:
            不存在 = True
        except BaseException:
            失敗 = True
        self = 管理員授權 = 端點識別碼 = 呼叫識別碼 = 路徑 = None
        if 控制 is not None:
            控制盒 = [控制]
            控制 = 結果 = None
            _重拋控制(控制盒.pop())
        if 不存在:
            結果 = None
            raise 管理員呼叫不存在錯誤("找不到呼叫紀錄") from None
        if 失敗 or type(結果) is not dict:
            結果 = None
            raise 管理員呼叫查詢錯誤(_固定錯誤) from None
        return 結果

    def 管理員呼叫配對存在(self, 端點識別碼: str, 呼叫識別碼: str, /) -> bool:
        """只查exact endpoint/invocation配對存在性，不選取任何raw欄位。"""
        路徑 = self._path
        連線 = 游標 = 列 = None
        結果 = 控制 = None
        已開始 = 失敗 = False
        try:
            if not _安全識別碼(端點識別碼) or not _安全識別碼(呼叫識別碼):
                raise ValueError
            連線 = _開啟唯讀快照(路徑)
            連線.execute("BEGIN")
            已開始 = True
            _驗證路徑與結構(連線, 路徑)
            游標 = 連線.execute(
                "SELECT 1 FROM endpoint_invocations WHERE endpoint_id=? AND id=? LIMIT 2",
                (端點識別碼, 呼叫識別碼),
            )
            列 = 游標.fetchall()
            if type(列) is not list or len(列) > 1 or any(項 != (1,) for 項 in 列):
                raise ValueError
            結果 = len(列) == 1
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
        self = 端點識別碼 = 呼叫識別碼 = 路徑 = 連線 = 游標 = 列 = 清理控制 = None
        if 控制 is not None:
            控制盒 = [控制]
            控制 = 結果 = None
            _重拋控制(控制盒.pop())
        if 失敗 or type(結果) is not bool:
            結果 = None
            raise 管理員呼叫查詢錯誤(_固定錯誤) from None
        return 結果

    def 查詢管理員完整詳情(
        self, 管理員授權: bool, 端點識別碼: str, 呼叫識別碼: str, /
    ) -> 管理員呼叫完整詳情:
        """以既有exact-admin raw投影重建獨立typed detail DTO。"""
        return 建立管理員呼叫完整詳情(
            self.查詢管理員原始資料(管理員授權, 端點識別碼, 呼叫識別碼)
        )

    def 列出管理員安全呼叫(
        self, 條件: 管理員呼叫查詢條件, 位置: 管理員呼叫游標位置 | None, /
    ) -> 管理員呼叫投影頁:
        """依endpoint與safe filters回傳created_at/id倒序的bounded metadata頁。

        描述：不選取raw JSON；error只在SQLite內投影 ``$.code``，redaction只查存在性。
        參數：``條件`` 是exact查詢scope；``位置`` 是已由adapter驗簽的keyset位置。
        返回值：最多limit筆safe DTO及下一頁未簽章位置。
        例外：資料、結構、型別或資源失敗固定為 ``查詢投影錯誤``；控制流程原樣。
        副作用：建立唯讀SQLite snapshot並在所有路徑關閉資源。
        """
        連線 = 游標 = 資料列 = 原始列 = 結果 = None
        安全條件: 管理員呼叫查詢條件 | None = None
        安全位置: 管理員呼叫游標位置 | None = None
        已開始 = 失敗 = False
        控制 = None
        路徑 = self._path
        try:
            if type(條件) is not 管理員呼叫查詢條件:
                raise ValueError
            安全條件 = 管理員呼叫查詢條件(*(
                object.__getattribute__(條件, 名稱) for 名稱 in 管理員呼叫查詢條件.__slots__
            ))
            if 位置 is None:
                安全位置 = None
            elif type(位置) is 管理員呼叫游標位置:
                安全位置 = 管理員呼叫游標位置(*(
                    object.__getattribute__(位置, 名稱) for 名稱 in 管理員呼叫游標位置.__slots__
                ))
            else:
                raise ValueError
            連線 = _開啟唯讀快照(路徑)
            連線.execute("BEGIN")
            已開始 = True
            _驗證路徑與結構(連線, 路徑)
            _驗證遮蔽schema(連線)
            位置時間 = None if 安全位置 is None else 安全位置.建立時間
            位置識別碼 = None if 安全位置 is None else 安全位置.呼叫識別碼
            游標 = 連線.execute(
                "SELECT i.id,i.endpoint_id,i.endpoint_version_id,i.request_id,i.status,"
                "se.error_code,i.latency_ms,i.created_at,i.completed_at,"
                "EXISTS(SELECT 1 FROM endpoint_redactions AS r WHERE r.invocation_id=i.id) "
                "FROM endpoint_invocations AS i LEFT JOIN endpoint_invocation_safe_errors AS se "
                "ON se.invocation_id=i.id WHERE i.endpoint_id=? "
                "AND (? IS NULL OR i.created_at>=?) AND (? IS NULL OR i.created_at<=?) "
                "AND (? IS NULL OR i.status=?) "
                "AND (? IS NULL OR se.error_code=?) "
                "AND (? IS NULL OR i.created_at<? OR (i.created_at=? AND i.id<?)) "
                "ORDER BY i.created_at DESC,i.id DESC LIMIT ?",
                (
                    安全條件.端點識別碼,
                    安全條件.起始時間, 安全條件.起始時間,
                    安全條件.結束時間, 安全條件.結束時間,
                    安全條件.狀態, 安全條件.狀態,
                    安全條件.錯誤碼, 安全條件.錯誤碼,
                    位置時間, 位置時間, 位置時間, 位置識別碼,
                    安全條件.數量上限 + 1,
                ),
            )
            原始列 = []
            資料列 = 游標.fetchone()
            while 資料列 is not None:
                if (len(原始列) > 安全條件.數量上限
                        or type(資料列) is not tuple or len(資料列) != 10):
                    raise ValueError
                原始列.append(資料列)
                資料列 = 游標.fetchone()
            游標.close()
            游標 = None
            有下一頁 = len(原始列) > 安全條件.數量上限
            顯示列 = 原始列[:安全條件.數量上限]
            項目 = tuple(_重建管理員列表項目(列) for 列 in 顯示列)
            下一頁位置 = None
            if 有下一頁 and 項目:
                下一頁位置 = 管理員呼叫游標位置(項目[-1].建立時間, 項目[-1].呼叫識別碼)
            結果 = 管理員呼叫投影頁(項目, 下一頁位置)
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
        self = 條件 = 位置 = 路徑 = 安全條件 = 安全位置 = None
        連線 = 游標 = 資料列 = 原始列 = 顯示列 = 項目 = 下一頁位置 = 清理控制 = None
        位置時間 = 位置識別碼 = None
        if 控制 is not None:
            控制盒 = [控制]
            控制 = 結果 = None
            _重拋控制(控制盒.pop())
        if 失敗 or type(結果) is not 管理員呼叫投影頁:
            結果 = None
            raise 管理員呼叫查詢錯誤(_固定錯誤) from None
        return 結果


def _重建管理員列表項目(列: tuple[Any, ...]) -> 管理員呼叫列表項目:
    """把exact十欄SQLite metadata列重建成安全DTO。

    描述：逐欄驗證，僅將SQLite EXISTS的exact 0/1轉成bool。
    參數：``列`` 是不含raw JSON的十欄tuple。
    返回值：全新 ``管理員呼叫列表項目``。
    例外：欄數、動態型別或值不符時拋出 ``ValueError``。
    """
    if type(列) is not tuple or len(列) != 10 or type(列[9]) is not int or 列[9] not in (0, 1):
        raise ValueError
    return 管理員呼叫列表項目(
        列[0], 列[1], 列[2], 列[3], 列[4], 列[5],
        列[6], 列[7], 列[8], bool(列[9]),
    )


def _讀取管理員原始資料(
    路徑: str, 端點識別碼: str, 呼叫識別碼: str
) -> dict[str, Any]:
    """同一 read transaction 先核算全部 JSON 長度，再按權威鍵取 payload。"""
    連線 = 游標 = 列 = 項 = 內容列 = 事件列 = 工具列 = 結果 = None
    遮蔽列 = 敏感命中列 = None
    輸入 = 中繼資料 = 輸出 = 錯誤 = 用量 = 事件 = 工具 = None
    已開始 = 失敗 = 不存在 = False
    控制 = None
    預算 = [0, 0]
    子列數 = 0
    try:
        連線 = _開啟唯讀快照(路徑)
        連線.execute("BEGIN")
        已開始 = True
        _驗證路徑與結構(連線, 路徑)
        _驗證遮蔽schema(連線)
        _驗證敏感命中schema(連線)
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
        if 列 is None:
            raise 管理員呼叫不存在錯誤("找不到呼叫紀錄") from None
        if 游標.fetchone() is not None or type(列) is not tuple or len(列) != 24:
            raise ValueError
        _驗證管理員呼叫列(列, 預算)
        游標.close()
        遮蔽中繼 = _預檢遮蔽中繼(連線, 呼叫識別碼, 預算)
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
        遮蔽列 = _讀取驗證遮蔽列(
            連線, 呼叫識別碼, 端點識別碼, 遮蔽中繼
        )
        子列數 += len(遮蔽列)
        敏感命中列 = _讀取驗證敏感命中列(
            連線, 呼叫識別碼, 端點識別碼
        )
        子列數 += len(敏感命中列)
        if 子列數 > _最大子列:
            raise ValueError
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
            "redactions": [{
                "id": 遮蔽[0], "target_type": 遮蔽[2], "target_row_id": 遮蔽[3],
                "json_path": 遮蔽[4], "reason": 遮蔽[6],
                "is_tombstone": True, "redacted_at": 遮蔽[11],
            } for 遮蔽 in 遮蔽列],
            "sensitive_hits": [{
                "id": 命中[0], "target": 命中[1], "tool_call_id": 命中[2],
                "detector_type": 命中[3], "json_path": 命中[4],
                "start": 命中[5], "end": 命中[6], "detected_at": 命中[7],
            } for 命中 in 敏感命中列],
        }
        _驗證完整詳情墓碑一致性(結果)
        連線.commit()
        已開始 = False
    except _控制流程 as 捕捉控制:
        _清理控制鏈(捕捉控制)
        控制 = 捕捉控制
        捕捉控制 = None
    except 管理員呼叫不存在錯誤:
        不存在 = True
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
    事件列 = 工具列 = 遮蔽中繼 = 遮蔽列 = 敏感命中列 = 事件 = 工具 = None
    輸入 = 中繼資料 = 輸出 = 錯誤 = 用量 = None
    預算 = 清理控制 = 工具JSON = None
    if 控制 is not None:
        控制盒 = [控制]
        控制 = 結果 = None
        _重拋控制(控制盒.pop())
    if 不存在:
        結果 = None
        raise 管理員呼叫不存在錯誤("找不到呼叫紀錄") from None
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


def _驗證敏感命中schema(連線: sqlite3.Connection) -> None:
    """只在 Admin detail callback 內驗證 hit table 的 exact schema/FK/index/trigger。"""
    欄位 = tuple(連線.execute("PRAGMA table_info(invocation_sensitive_hits)"))
    外鍵 = tuple(連線.execute("PRAGMA foreign_key_list(invocation_sensitive_hits)"))
    索引 = tuple(sorted((列[1], 列[2], 列[3], 列[4], tuple(
        項[2] for 項 in 連線.execute(f'PRAGMA index_info("{列[1]}")')
    )) for 列 in 連線.execute("PRAGMA index_list(invocation_sensitive_hits)")))
    物件 = tuple((類型, 名稱, hashlib.sha256(SQL.encode()).hexdigest())
               for 類型, 名稱, SQL in 連線.execute(
        "SELECT type,name,sql FROM sqlite_master WHERE tbl_name='invocation_sensitive_hits' "
        "AND sql IS NOT NULL AND type IN ('table','index','trigger') ORDER BY type,name"
    ))
    if (欄位 != (
            (0,"id","TEXT",0,None,1),(1,"invocation_id","TEXT",1,None,0),
            (2,"tool_call_id","TEXT",0,None,0),(3,"target_type","TEXT",1,None,0),
            (4,"detector_type","TEXT",1,None,0),(5,"json_path","TEXT",1,None,0),
            (6,"start_offset","INTEGER",1,None,0),(7,"end_offset","INTEGER",1,None,0),
            (8,"audit_event_id","TEXT",1,None,0),(9,"detected_at","REAL",1,None,0),
        ) or 外鍵 != (
            (0,0,"endpoint_tool_calls","tool_call_id","id","NO ACTION","RESTRICT","NONE"),
            (0,1,"endpoint_tool_calls","invocation_id","invocation_id","NO ACTION","RESTRICT","NONE"),
            (1,0,"audit_events","audit_event_id","id","NO ACTION","CASCADE","NONE"),
            (2,0,"endpoint_invocations","invocation_id","id","NO ACTION","RESTRICT","NONE"),
        ) or 索引 != (
            ("idx_invocation_sensitive_hits_admin_sort",0,"c",0,
             ("invocation_id","target_type","tool_call_id","json_path","start_offset",
              "end_offset","detector_type","id")),
            ("sqlite_autoindex_invocation_sensitive_hits_1",1,"pk",0,("id",)),
            ("sqlite_autoindex_invocation_sensitive_hits_2",1,"u",0,("audit_event_id",)),
            ("uq_invocation_sensitive_hits_with_tool",1,"c",1,
             ("invocation_id","tool_call_id","target_type","json_path","start_offset",
              "end_offset","detector_type")),
            ("uq_invocation_sensitive_hits_without_tool",1,"c",1,
             ("invocation_id","target_type","json_path","start_offset","end_offset","detector_type")),
        ) or 物件 != _敏感命中物件摘要):
        raise ValueError


def _讀取驗證敏感命中列(
    連線: sqlite3.Connection, 呼叫ID: str, 端點ID: str,
) -> tuple[tuple[Any, ...], ...]:
    """有界讀取 location-only rows，驗證 audit 一對一、ownership 與固定排序。"""
    孤立稽核 = 連線.execute(
        "SELECT count(*) FROM audit_events a LEFT JOIN invocation_sensitive_hits h "
        "ON h.audit_event_id=a.id WHERE a.invocation_id=? "
        "AND a.action='published_api.sensitive_data_detected' AND h.id IS NULL",
        (呼叫ID,),
    ).fetchone()
    if 孤立稽核 != (0,):
        raise ValueError
    游標 = 連線.execute(
        "SELECT h.id,h.target_type,h.tool_call_id,h.detector_type,h.json_path,"
        "h.start_offset,h.end_offset,h.detected_at,h.audit_event_id,t.sequence_number,"
        "a.id,a.event_id,a.occurred_at,a.action,a.outcome,a.actor_type,a.actor_id,"
        "a.resource_type,a.resource_id,a.request_id,a.endpoint_id,a.invocation_id,"
        "a.metadata_json,a.created_at FROM invocation_sensitive_hits h "
        "LEFT JOIN endpoint_tool_calls t ON t.id=h.tool_call_id AND t.invocation_id=h.invocation_id "
        "LEFT JOIN audit_events a ON a.id=h.audit_event_id WHERE h.invocation_id=? "
        "ORDER BY h.target_type,CASE WHEN h.tool_call_id IS NULL THEN 0 ELSE t.sequence_number END,"
        "h.json_path,h.start_offset,h.end_offset,h.detector_type,h.id LIMIT ?",
        (呼叫ID, _最大敏感命中 + 1),
    )
    try:
        列們 = tuple(游標.fetchall())
    finally:
        _關閉查詢游標並保留主要控制(游標)
    if len(列們) > _最大敏感命中:
        raise ValueError
    結果 = []
    前鍵 = None
    for 列 in 列們:
        if type(列) is not tuple or len(列) != 24:
            raise ValueError
        (命中ID, 目標, 工具ID, 偵測器, JSON路徑, 開始, 結束, 偵測時間,
         命中稽核ID, 工具序號, 稽核ID, 稽核事件ID, 稽核時間, 行動, 結果碼,
         操作者類型, 操作者ID, 資源類型, 資源ID, 請求ID, 稽核端點ID,
         稽核呼叫ID, 稽核中繼, 稽核建立時間) = 列
        是工具 = 目標 in ("tool_arguments", "tool_result")
        if (not _安全識別碼(命中ID) or 目標 not in
                ("input", "metadata", "response_data", "tool_arguments", "tool_result")
                or type(目標) is not str or not _安全識別碼(命中稽核ID)
                or (是工具 and (not _安全識別碼(工具ID) or not _安全正整數(工具序號)))
                or (not 是工具 and (工具ID is not None or 工具序號 is not None))
                or type(偵測器) is not str or not 1 <= len(偵測器.encode("utf-8")) <= 128
                or re.fullmatch(r"[a-z][a-z0-9_]{0,127}", 偵測器) is None
                or type(JSON路徑) is not str or len(JSON路徑.encode("utf-8")) > 8192
                or not (JSON路徑 == "" or (JSON路徑.startswith("/")
                        and "~" not in JSON路徑.replace("~0", "").replace("~1", "")))
                or type(開始) is not int or type(結束) is not int
                or not 0 <= 開始 < 結束 <= _最大安全整數
                or not _安全時間(偵測時間) or 偵測時間 > _最大安全時間):
            raise ValueError
        預期中繼 = json.dumps({
            "warning_code": "sensitive_data_detected", "target": 目標,
            "detector_type": 偵測器, "json_path": JSON路徑, "start": 開始, "end": 結束,
        }, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
        if (稽核ID != 命中稽核ID or 稽核事件ID != 命中稽核ID
                or 稽核時間 != 偵測時間 or 稽核建立時間 != 偵測時間
                or (行動, 結果碼, 操作者類型, 操作者ID, 資源類型, 資源ID, 請求ID,
                    稽核端點ID, 稽核呼叫ID, 稽核中繼) !=
                   ("published_api.sensitive_data_detected", "success", "system", None,
                    "invocation", 呼叫ID, None, 端點ID, 呼叫ID, 預期中繼)):
            raise ValueError
        鍵 = (目標, 0 if 工具ID is None else 工具序號,
             JSON路徑, 開始, 結束, 偵測器)
        if 前鍵 is not None and 鍵 <= 前鍵:
            raise ValueError
        前鍵 = 鍵
        結果.append(列[:8])
    return tuple(結果)


def _遮蔽中繼選取() -> tuple[str, tuple[str, ...], tuple[str, ...]]:
    """集中定義 redaction/audit metadata gate 與 payload 欄位順序。"""
    遮蔽文字欄 = ("id", "invocation_id", "target_type", "target_row_id", "json_path",
              "original_sha256", "reason", "actor_type", "actor_id", "audit_event_id")
    稽核文字欄 = ("id", "event_id", "action", "outcome", "actor_type", "actor_id",
              "resource_type", "resource_id", "request_id", "endpoint_id", "invocation_id",
              "metadata_json")
    選取 = ["r.rowid"]
    for 欄位 in 遮蔽文字欄:
        選取.extend((f"typeof(r.{欄位})", f"length(CAST(r.{欄位} AS BLOB))"))
    for 欄位 in 稽核文字欄:
        選取.extend((f"typeof(a.{欄位})", f"length(CAST(a.{欄位} AS BLOB))"))
    選取.extend(("typeof(r.is_tombstone)", "r.is_tombstone",
               "typeof(r.redacted_at)", "r.redacted_at", "typeof(a.occurred_at)",
               "a.occurred_at", "typeof(a.created_at)", "a.created_at"))
    return ",".join(選取), 遮蔽文字欄, 稽核文字欄


def _預檢遮蔽中繼(
    連線: sqlite3.Connection, 呼叫識別碼: str, 預算: list[int]
) -> tuple[tuple[Any, ...], ...]:
    """保留每筆 ledger，先驗 metadata，再核對 audit 身分鏈結。"""
    選取, _, _ = _遮蔽中繼選取()
    游標 = 連線.execute(
        f"SELECT {選取} FROM endpoint_redactions r LEFT JOIN audit_events a "
        "ON a.id=r.audit_event_id WHERE r.invocation_id=? ORDER BY r.rowid LIMIT ?",
        (呼叫識別碼, _最大子列 + 1),
    )
    try:
        中繼列 = _讀取有限列(游標, 53)
    finally:
        _關閉查詢游標並保留主要控制(游標)
    if len(中繼列) > _最大子列:
        raise ValueError
    結果 = []
    for 項 in 中繼列:
        if type(項[0]) is not int:
            raise ValueError
        for 索引 in range(1, 45, 2):
            _扣除JSON長度(項[索引], 項[索引 + 1], 預算, True)
        if (項[45:49] != ("integer", 1, "real", 項[48])
                or 項[49] not in ("real", "integer") or 項[51] not in ("real", "integer")
                or not all(_安全時間(項[索引]) for 索引 in (48, 50, 52))):
            raise ValueError
        身分 = _讀取遮蔽身分中繼(連線, 項[0], 呼叫識別碼)
        規格 = ((1, 2), (3, 4), (19, 20), (21, 22), (23, 24), (41, 42))
        if 身分[0] != 項[0]:
            raise ValueError
        for 值, (類型索引, 長度索引) in zip(身分[1:], 規格):
            if (type(值) is not str or 項[類型索引] != "text"
                    or len(值.encode("utf-8")) != 項[長度索引]):
                raise ValueError
        if (not all(_安全識別碼(值) for 值 in 身分[1:])
                or 身分[2] != 呼叫識別碼 or 身分[3] != 身分[4]
                or 身分[4] != 身分[5] or 身分[6] != 呼叫識別碼):
            raise ValueError
        結果.append((*項, *身分[1:]))
    return tuple(結果)


def _讀取遮蔽身分中繼(
    連線: sqlite3.Connection, 資料列識別碼: int, 呼叫識別碼: str
) -> tuple[Any, ...]:
    """長度 gate 通過後取得 bounded redaction/audit IDs，不讀 JSON payload。"""
    游標 = 連線.execute(
        "SELECT r.rowid,r.id,r.invocation_id,r.audit_event_id,a.id,a.event_id,a.invocation_id "
        "FROM endpoint_redactions r LEFT JOIN audit_events a ON a.id=r.audit_event_id "
        "WHERE r.rowid=? AND r.invocation_id=?", (資料列識別碼, 呼叫識別碼),
    )
    try:
        列 = 游標.fetchone()
        if 游標.fetchone() is not None or type(列) is not tuple or len(列) != 7:
            raise ValueError
        return 列
    finally:
        _關閉查詢游標並保留主要控制(游標)


def _讀取驗證遮蔽列(
    連線: sqlite3.Connection, 呼叫識別碼: str, 端點識別碼: str,
    中繼列: tuple[tuple[Any, ...], ...],
) -> tuple[tuple[Any, ...], ...]:
    """全投影 metadata gate 成功後，按 scoped rowid 取得、重驗 ledger/audit payload。"""
    選取, _, _ = _遮蔽中繼選取()
    payload欄 = (
        "r.id", "r.invocation_id", "r.target_type", "r.target_row_id", "r.json_path",
        "r.original_sha256", "r.reason", "r.actor_type", "r.actor_id", "r.audit_event_id",
        "r.is_tombstone", "r.redacted_at", "a.id", "a.event_id", "a.occurred_at",
        "a.action", "a.outcome", "a.actor_type", "a.actor_id", "a.resource_type",
        "a.resource_id", "a.request_id", "a.endpoint_id", "a.invocation_id",
        "a.metadata_json", "a.created_at",
    )
    結果 = []
    已見 = set()
    for 中繼 in 中繼列:
        游標 = 連線.execute(
            f"SELECT {選取},{','.join(payload欄)} FROM endpoint_redactions r "
            "JOIN audit_events a ON a.id=r.audit_event_id WHERE r.rowid=? AND r.invocation_id=?",
            (中繼[0], 呼叫識別碼),
        )
        try:
            列 = 游標.fetchone()
            if 游標.fetchone() is not None or type(列) is not tuple or len(列) != 79:
                raise ValueError
        finally:
            _關閉查詢游標並保留主要控制(游標)
        if 列[:53] != 中繼[:53]:
            raise ValueError
        項 = 列[53:]
        _驗證遮蔽語意(連線, 項, 呼叫識別碼, 端點識別碼, 已見)
        結果.append(項[:12])
    return tuple(結果)


def _驗證遮蔽語意(
    連線: sqlite3.Connection, 列: tuple[Any, ...], 呼叫ID: str, 端點ID: str, 已見: set[Any]
) -> None:
    """驗證完整 redaction/audit tuple 與子列權威歸屬。"""
    類型, 列ID, 路徑 = 列[2], 列[3], 列[4]
    if not all(_安全識別碼(列[索引]) for 索引 in (0, 1, 3, 8, 9)):
        raise ValueError
    if (列[1] != 呼叫ID or 類型 not in ("invocation_input", "metadata", "output", "error",
            "run_event", "tool_arguments", "tool_result", "tool_error")
            or type(類型) is not str or type(路徑) is not str or type(列[5]) is not str
            or len(列[5]) != 64 or any(字 not in "0123456789abcdef" for 字 in 列[5])
            or not _安全文字(列[6], 1000) or 列[7] != "admin" or 列[10] != 1
            or not _安全時間(列[11])):
        raise ValueError
    _解析路徑(路徑)
    鍵 = (類型, 列ID, 路徑)
    if 鍵 in 已見:
        raise ValueError
    已見.add(鍵)
    if 類型 in ("invocation_input", "metadata", "output", "error"):
        if 列ID != 呼叫ID:
            raise ValueError
    else:
        表格 = "run_events" if 類型 == "run_event" else "endpoint_tool_calls"
        if 連線.execute(f"SELECT count(*) FROM {表格} WHERE id=? AND invocation_id=?",
                     (列ID, 呼叫ID)).fetchone() != (1,):
            raise ValueError
    if (列[12:14] != (列[9], 列[9]) or 列[14:21] != (列[11], "audit.payload.redact",
            "success", "user", 列[8], "endpoint.redaction", 列[0])
            or not _安全識別碼(列[21]) or 列[22:26] !=
            (端點ID, 呼叫ID, '{"is_tombstone":true}', 列[11])):
        raise ValueError


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
    if 預算[1] > _最大JSON節點 or 深度 > _最大JSON深度:
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


def _關閉查詢游標並保留主要控制(游標: Any) -> None:
    """關閉查詢游標；active控制流程永遠優先於ordinary或cleanup控制。"""
    主要控制 = sys.exception()
    控制盒 = _清理資源操作(游標, "close")
    if 控制盒 and not isinstance(主要控制, _控制流程):
        _重拋控制(控制盒.pop())


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
