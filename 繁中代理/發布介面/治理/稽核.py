"""Append-only、無損且自行管理交易的 SQLite 稽核 sink。"""

from __future__ import annotations

import json
import math
import sqlite3
import time
from types import BuiltinFunctionType, FunctionType, MappingProxyType
from typing import Any

from ..契約 import AuditSinkError
from ..領域模型 import AuditActorRef, AuditAppendReceipt, AuditEvent
from ..領域模型 import AuditMetadata, AuditResourceRef
from .稽核資料庫 import _開啟既有資料庫, _驗證目前路徑, _驗證schema

_控制流程 = (KeyboardInterrupt, SystemExit, GeneratorExit)
_允許時鐘型別 = (FunctionType, BuiltinFunctionType)
_讀取metadata = AuditMetadata.to_json
_固定錯誤訊息 = "稽核事件無法確認提交"


class SQLite稽核服務:
    """以既有 SQLite v6 檔案實作 AuditEventSink。"""

    __slots__ = ("_path", "_clock")

    def __init__(self, 資料庫路徑: str, *, 時鐘: FunctionType | BuiltinFunctionType = time.time) -> None:
        """捕捉 exact str 路徑與 exact function/builtin-function 時鐘。"""
        if (
            type(資料庫路徑) is not str
            or not 資料庫路徑
            or 資料庫路徑.startswith("~")
            or type(時鐘) not in _允許時鐘型別
        ):
            資料庫路徑 = 時鐘 = None  # type: ignore[assignment]
            raise AuditSinkError(_固定錯誤訊息) from None
        self._path = 資料庫路徑
        self._clock = 時鐘

    def 附加稽核事件(self, 事件: AuditEvent, /) -> AuditAppendReceipt:
        """先重建完整 canonical event 與時鐘，再鎖定 v6 schema 並附加一列。

        提交後的 ordinary close failure 不會把已持久化事件誤報為失敗；close 的
        Python 控制流程例外仍依全域控制流程政策原樣傳遞。
        """
        連線 = 游標 = 資料列 = None
        序號 = 事件識別碼 = None
        已開始 = 已提交 = 一般失敗 = False
        主要控制 = None
        回滾控制盒: list[BaseException] = []
        關閉控制盒: list[BaseException] = []
        捕捉路徑 = self._path
        時鐘 = self._clock
        try:
            資料列 = _建立canonical列(事件)
            事件識別碼 = 資料列[0]
            事件 = None
            建立時間 = _讀取時鐘(時鐘)
            時鐘 = None
            資料列 = 資料列 + (建立時間,)
            建立時間 = None
            連線 = _開啟既有資料庫(捕捉路徑)
            連線.execute("BEGIN IMMEDIATE")
            已開始 = True
            _驗證目前路徑(連線, 捕捉路徑)
            捕捉路徑 = None
            _驗證schema(連線)
            游標 = 連線.execute(
                "INSERT INTO audit_events("
                "id,event_id,occurred_at,action,outcome,actor_type,actor_id,resource_type,"
                "resource_id,request_id,endpoint_id,invocation_id,metadata_json,created_at"
                ") VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                資料列,
            )
            序號 = 游標.lastrowid
            游標.close()
            游標 = None
            if type(序號) is not int or not 1 <= 序號 <= 2**63 - 1:
                raise sqlite3.DatabaseError("invalid audit sequence")
            連線.commit()
            已提交 = True
        except _控制流程 as 捕捉控制:
            _清理控制鏈(捕捉控制)
            主要控制 = 捕捉控制
            捕捉控制 = None
        except BaseException:
            一般失敗 = True

        if 連線 is not None and 已開始 and not 已提交:
            回滾控制盒 = _清理連線操作(連線, "rollback")
        if 連線 is not None:
            關閉控制盒 = _清理連線操作(連線, "close")

        self = 事件 = 連線 = 游標 = 資料列 = 捕捉路徑 = 時鐘 = None
        已開始 = False
        if 主要控制 is not None:
            回滾控制盒.clear()
            關閉控制盒.clear()
            控制盒 = [主要控制]
            主要控制 = 序號 = 事件識別碼 = None
            _重拋控制(控制盒.pop())
        if 回滾控制盒:
            關閉控制盒.clear()
            _重拋控制(回滾控制盒.pop())
        if 關閉控制盒:
            _重拋控制(關閉控制盒.pop())
        if 一般失敗 or not 已提交 or type(序號) is not int or type(事件識別碼) is not str:
            序號 = 事件識別碼 = None
            raise AuditSinkError(_固定錯誤訊息) from None
        return AuditAppendReceipt(事件識別碼, True, 序號)


setattr(SQLite稽核服務, "append_audit_event", SQLite稽核服務.附加稽核事件)


def _建立canonical列(事件: AuditEvent) -> tuple[Any, ...]:
    """只以 module-owned DTO constructors/serializer 重建所有 FND 欄位。"""
    失敗 = False
    控制 = 結果 = None
    純量值 = 行為者 = 資源 = 中繼資料 = 中繼資料字典 = None
    安全行為者 = 安全資源 = 安全中繼資料 = 安全事件 = 中繼資料JSON = None
    try:
        if type(事件) is not AuditEvent:
            raise ValueError
        純量值 = (
            事件.event_id, 事件.occurred_at, 事件.action, 事件.outcome,
            事件.request_id, 事件.endpoint_id, 事件.invocation_id,
        )
        行為者, 資源, 中繼資料 = 事件.actor, 事件.resource, 事件.metadata
        if type(行為者) is not AuditActorRef or type(資源) is not AuditResourceRef:
            raise ValueError
        if type(中繼資料) is not AuditMetadata or type(中繼資料._資料) is not MappingProxyType:
            raise ValueError
        安全行為者 = AuditActorRef(行為者.actor_type, 行為者.actor_id)
        安全資源 = AuditResourceRef(資源.resource_type, 資源.resource_id)
        中繼資料字典 = _讀取metadata(中繼資料)
        安全中繼資料 = AuditMetadata(中繼資料字典)
        安全事件 = AuditEvent(
            event_id=純量值[0], occurred_at=純量值[1], action=純量值[2], outcome=純量值[3],
            actor=安全行為者, resource=安全資源, request_id=純量值[4],
            endpoint_id=純量值[5], invocation_id=純量值[6], metadata=安全中繼資料,
        )
        事件 = 行為者 = 資源 = 中繼資料 = 純量值 = 中繼資料字典 = None
        中繼資料JSON = json.dumps(
            _讀取metadata(安全中繼資料), ensure_ascii=False, sort_keys=True,
            separators=(",", ":"), allow_nan=False,
        )
        結果 = (
            安全事件.event_id, 安全事件.event_id, 安全事件.occurred_at,
            安全事件.action, 安全事件.outcome, 安全行為者.actor_type, 安全行為者.actor_id,
            安全資源.resource_type, 安全資源.resource_id, 安全事件.request_id,
            安全事件.endpoint_id, 安全事件.invocation_id, 中繼資料JSON,
        )
    except _控制流程 as 捕捉控制:
        _清理控制鏈(捕捉控制)
        控制 = 捕捉控制
        捕捉控制 = None
    except BaseException:
        失敗 = True
    事件 = 純量值 = 行為者 = 資源 = 中繼資料 = 中繼資料字典 = None
    安全行為者 = 安全資源 = 安全中繼資料 = 安全事件 = 中繼資料JSON = None
    if 控制 is not None:
        控制盒 = [控制]
        控制 = 結果 = None
        _重拋控制(控制盒.pop())
    if 失敗 or type(結果) is not tuple:
        結果 = None
        raise ValueError("invalid audit event") from None
    return 結果


def _讀取時鐘(時鐘: FunctionType | BuiltinFunctionType) -> float:
    """exact-once 呼叫可信時鐘並正規化到 SQLite 可保存範圍。"""
    失敗 = False
    控制 = 值 = 結果 = None
    try:
        值 = 時鐘()
        if type(值) not in (int, float) or 值 < 0 or 值 > 253402300799:
            raise ValueError
        if type(值) is float and not math.isfinite(值):
            raise ValueError
        結果 = float(值)
    except _控制流程 as 捕捉控制:
        _清理控制鏈(捕捉控制)
        控制 = 捕捉控制
        捕捉控制 = None
    except BaseException:
        失敗 = True
    時鐘 = 值 = None
    if 控制 is not None:
        控制盒 = [控制]
        控制 = None
        _重拋控制(控制盒.pop())
    if 失敗 or type(結果) is not float:
        結果 = None
        raise ValueError("invalid audit clock") from None
    return 結果


def _清理連線操作(連線: sqlite3.Connection, 操作: str) -> list[BaseException]:
    """best-effort cleanup；只回傳已去鏈控制流程的一元素盒。"""
    控制盒: list[BaseException] = []
    try:
        getattr(連線, 操作)()
    except _控制流程 as 捕捉控制:
        _清理控制鏈(捕捉控制)
        BaseException.__setattr__(捕捉控制, "__traceback__", None)
        控制盒.append(捕捉控制)
        捕捉控制 = None
    except BaseException:
        pass
    連線 = 操作 = None  # type: ignore[assignment]
    return 控制盒


def _清理控制鏈(控制: BaseException) -> None:
    """移除控制流程例外的既有 cause/context 鏈。"""
    BaseException.__setattr__(控制, "__cause__", None)
    BaseException.__setattr__(控制, "__context__", None)
    BaseException.__setattr__(控制, "__suppress_context__", True)


def _重拋控制(控制: BaseException) -> None:
    """以乾淨 traceback 保留 Python 控制例外 exact identity 與 args。"""
    try:
        _清理控制鏈(控制)
        BaseException.__setattr__(控制, "__traceback__", None)
        raise 控制
    except _控制流程:
        控制 = None  # type: ignore[assignment]
        raise
