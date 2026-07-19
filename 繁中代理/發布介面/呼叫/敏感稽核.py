"""把 L06 位置命中逐筆附加至既有 generic audit_events。

參數／欄位：不適用；本模組定義敏感命中的稽核附加操作與固定結構契約。
回傳：不適用；各附加操作的回傳契約由其文件字串分別說明。
例外：匯入相依模組失敗時原樣傳出匯入例外。
副作用：匯入時只定義常數與函式，不開啟資料庫或附加稽核事件。
"""

from __future__ import annotations

import json
import math
import os
from pathlib import Path
import secrets
import sqlite3
import stat
import time
from typing import Callable

from ..資料庫結構契約 import 遷移帳本 as _必要遷移
from .擷取政策 import 敏感偵測擷取結果, 目標敏感命中

_控制流程例外 = (KeyboardInterrupt, SystemExit, GeneratorExit)
_Path具體型別 = type(Path())
_稽核結構 = (
    ("index", "idx_audit_events_endpoint_time", "audit_events",
     "CREATE INDEX idx_audit_events_endpoint_time\n  ON audit_events(endpoint_id, occurred_at)"),
    ("index", "idx_audit_events_invocation_time", "audit_events",
     "CREATE INDEX idx_audit_events_invocation_time\n  ON audit_events(invocation_id, occurred_at)"),
    ("index", "idx_audit_events_resource_time", "audit_events",
     "CREATE INDEX idx_audit_events_resource_time\n  ON audit_events(resource_type, resource_id, occurred_at)"),
    ("index", "idx_audit_events_retention_invocation_id", "audit_events",
     "CREATE INDEX idx_audit_events_retention_invocation_id\n  ON audit_events(invocation_id, id)"),
    ("index", "sqlite_autoindex_audit_events_1", "audit_events", None),
    ("index", "sqlite_autoindex_audit_events_2", "audit_events", None),
    ("table", "audit_events", "audit_events", """CREATE TABLE "audit_events" (
  id TEXT PRIMARY KEY,
  event_id TEXT NOT NULL UNIQUE,
  occurred_at REAL NOT NULL CHECK(typeof(occurred_at) IN ('real','integer') AND occurred_at >= 0),
  action TEXT NOT NULL CHECK(trim(action) <> ''),
  outcome TEXT NOT NULL CHECK(outcome IN ('success','denied','failed','legacy_unknown')),
  actor_type TEXT NOT NULL CHECK(actor_type IN ('user','admin','service_account','system')),
  actor_id TEXT CHECK(actor_type = 'system' OR (actor_id IS NOT NULL AND trim(actor_id) <> '')),
  resource_type TEXT NOT NULL CHECK(trim(resource_type) <> ''),
  resource_id TEXT NOT NULL CHECK(trim(resource_id) <> ''),
  request_id TEXT,
  endpoint_id TEXT REFERENCES published_endpoints(id) ON DELETE RESTRICT,
  invocation_id TEXT REFERENCES endpoint_invocations(id) ON DELETE RESTRICT,
  metadata_json TEXT NOT NULL CHECK(json_valid(metadata_json) AND json_type(metadata_json) = 'object'),
  created_at REAL NOT NULL CHECK(typeof(created_at) IN ('real','integer') AND created_at >= 0)
)"""),
    ("trigger", "audit_events_no_delete", "audit_events", """CREATE TRIGGER audit_events_no_delete BEFORE DELETE ON audit_events BEGIN
  SELECT RAISE(ABORT, 'audit events are append only');
END"""),
    ("trigger", "audit_events_no_update", "audit_events", """CREATE TRIGGER audit_events_no_update BEFORE UPDATE ON audit_events BEGIN
  SELECT RAISE(ABORT, 'audit events are append only');
END"""),
)


class 敏感稽核錯誤(RuntimeError):
    """代表 sanitized 敏感稽核批次被固定拒絕。"""


class SQLite敏感稽核儲存庫:
    """只向既有 generic audit_events 原子附加位置事件。"""

    def __init__(self, 資料庫: str | Path, *, 時鐘: Callable[[], float] = time.time,
                 識別碼工廠: Callable[[], str] | None = None,
                 連線工廠: Callable[..., sqlite3.Connection] = sqlite3.connect) -> None:
        """保存可測依賴；初始化不開資料庫。"""
        try:
            if (type(資料庫) not in (str, _Path具體型別)
                    or type(資料庫) is str and not 資料庫
                    or not callable(時鐘) or not callable(連線工廠)
                    or 識別碼工廠 is not None and not callable(識別碼工廠)):
                raise ValueError
            self._資料庫 = Path(資料庫)
            self._時鐘 = 時鐘
            self._識別碼工廠 = 識別碼工廠 or (lambda: f"audit-{secrets.token_hex(16)}")
            self._連線工廠 = 連線工廠
        except BaseException as 錯誤:
            是控制流程 = type(錯誤) in _控制流程例外
            self = 資料庫 = 時鐘 = 識別碼工廠 = 連線工廠 = 錯誤 = None
            if 是控制流程:
                raise
            raise 敏感稽核錯誤("敏感稽核儲存庫初始化失敗") from None

    def 附加偵測事件(self, 結果: 敏感偵測擷取結果, 呼叫識別碼: str,
                 端點識別碼: str, 請求識別碼: str) -> tuple[str, ...]:
        """完整重驗 L06 結果後，以一個交易依命中順序逐筆附加。"""
        命中們 = 稽核識別碼們 = 稽核識別碼 = 時間 = 稽核中繼資料 = 中繼資料JSON = None
        資料列們 = 連線 = 命中 = None
        交易開始 = 已提交 = False
        try:
            _驗證識別碼(呼叫識別碼, 端點識別碼, 請求識別碼)
            命中們 = _重建命中們(結果)
            結果 = None
            if not 命中們:
                呼叫識別碼 = 端點識別碼 = 請求識別碼 = 命中們 = self = None
                return ()
            稽核識別碼們 = []
            for 命中 in 命中們:
                稽核識別碼 = self._識別碼工廠()
                if (type(稽核識別碼) is not str or not 稽核識別碼.strip()
                        or len(稽核識別碼) > 256 or 稽核識別碼 in 稽核識別碼們):
                    raise ValueError
                稽核識別碼們.append(稽核識別碼)
                稽核識別碼 = None
            時間 = self._時鐘()
            if not _是非負有限時間(時間):
                raise ValueError
            資料列們 = []
            for 索引 in range(len(命中們)):
                命中 = 命中們[索引]
                稽核中繼資料 = {
                    "warning_code": "sensitive_data_detected",
                    "target": object.__getattribute__(命中, "目標代碼"),
                    "detector_type": object.__getattribute__(命中, "類型代碼"),
                    "json_path": object.__getattribute__(命中, "JSON路徑"),
                    "start": object.__getattribute__(命中, "開始"),
                    "end": object.__getattribute__(命中, "結束"),
                }
                中繼資料JSON = json.dumps(稽核中繼資料, ensure_ascii=False, sort_keys=True,
                                           separators=(",", ":"), allow_nan=False)
                資料列們.append((稽核識別碼們[索引], 稽核識別碼們[索引], 時間,
                    "published_api.sensitive_data_detected", "success", "system", None,
                    "invocation", 呼叫識別碼, 請求識別碼, 端點識別碼, 呼叫識別碼,
                    中繼資料JSON, 時間))
                命中 = 稽核中繼資料 = 中繼資料JSON = None
            命中們 = None
            連線 = self._開啟連線()
            連線.execute("BEGIN IMMEDIATE")
            交易開始 = True
            _驗證稽核結構(連線)
            for 資料列 in 資料列們:
                連線.execute(
                    "INSERT INTO audit_events(id,event_id,occurred_at,action,outcome,actor_type,actor_id,"
                    "resource_type,resource_id,request_id,endpoint_id,invocation_id,metadata_json,created_at) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)", 資料列,
                )
                資料列 = None
            連線.commit()
            已提交 = True
            try:
                連線.close()
            except BaseException as 關閉錯誤:
                關閉是控制流程 = type(關閉錯誤) in _控制流程例外
                if 關閉是控制流程:
                    關閉錯誤.__cause__ = 關閉錯誤.__context__ = None
                    關閉錯誤.__suppress_context__ = True
                關閉錯誤 = None
                if 關閉是控制流程:
                    raise
            回傳值 = tuple(稽核識別碼們)
            self = 呼叫識別碼 = 端點識別碼 = 請求識別碼 = 稽核識別碼們 = 資料列們 = 連線 = None
            return 回傳值
        except BaseException as 錯誤:
            是控制流程 = type(錯誤) in _控制流程例外
            if 是控制流程:
                錯誤.__cause__ = 錯誤.__context__ = None
                錯誤.__suppress_context__ = True
            清理控制 = None
            if 連線 is not None and 交易開始 and not 已提交:
                try:
                    連線.rollback()
                except BaseException as 回滾錯誤:
                    if not 是控制流程 and type(回滾錯誤) in _控制流程例外:
                        清理控制 = 回滾錯誤
                    回滾錯誤 = None
            if 連線 is not None and not 已提交:
                try:
                    連線.close()
                except BaseException as 關閉錯誤:
                    if (not 是控制流程 and 清理控制 is None
                            and type(關閉錯誤) in _控制流程例外):
                        清理控制 = 關閉錯誤
                    關閉錯誤 = None
            self = 結果 = 呼叫識別碼 = 端點識別碼 = 請求識別碼 = 命中們 = None
            稽核識別碼們 = 稽核識別碼 = 時間 = 稽核中繼資料 = 中繼資料JSON = 資料列們 = None
            連線 = 命中 = 資料列 = 索引 = None
            if 是控制流程:
                錯誤.__cause__ = 錯誤.__context__ = None
                錯誤.__suppress_context__ = True
                錯誤 = 清理控制 = None
                raise
            錯誤 = None
            if 清理控制 is not None:
                try:
                    raise 清理控制
                except BaseException as 選定:
                    選定.__cause__ = 選定.__context__ = None
                    選定.__suppress_context__ = True
                    清理控制 = 選定 = None
                    raise
        raise 敏感稽核錯誤("敏感稽核附加失敗") from None

    def _開啟連線(self) -> sqlite3.Connection:
        """只以 rw 開啟釘住的既有非空一般檔並啟用外鍵。"""
        連線 = 路徑 = 連線URI = 開啟前 = 開啟後 = None
        try:
            路徑 = self._資料庫.absolute()
            開啟前 = os.lstat(路徑)
            if not stat.S_ISREG(開啟前.st_mode) or 開啟前.st_size <= 0:
                raise ValueError
            連線URI = 路徑.as_uri() + "?mode=rw"
            連線 = self._連線工廠(連線URI, uri=True, isolation_level=None)
            開啟後 = os.lstat(路徑)
            if (not stat.S_ISREG(開啟後.st_mode)
                    or (開啟前.st_dev, 開啟前.st_ino) != (開啟後.st_dev, 開啟後.st_ino)):
                raise ValueError
            連線.execute("PRAGMA foreign_keys=ON")
            if 連線.execute("PRAGMA foreign_keys").fetchone() != (1,):
                raise ValueError
            return 連線
        except BaseException as 錯誤:
            是控制流程 = type(錯誤) in _控制流程例外
            if 是控制流程:
                錯誤.__cause__ = 錯誤.__context__ = None
                錯誤.__suppress_context__ = True
            if 連線 is not None:
                try:
                    連線.close()
                except BaseException as 關閉錯誤:
                    關閉是控制流程 = type(關閉錯誤) in _控制流程例外
                    if 關閉是控制流程 and not 是控制流程:
                        關閉錯誤.__cause__ = 關閉錯誤.__context__ = None
                        關閉錯誤.__suppress_context__ = True
                    self = 連線 = 路徑 = 連線URI = 開啟前 = 開啟後 = 錯誤 = None
                    if 關閉是控制流程 and not 是控制流程:
                        關閉錯誤 = None
                        raise
            self = 連線 = 路徑 = 連線URI = 開啟前 = 開啟後 = 錯誤 = None
            if 是控制流程:
                raise
        raise 敏感稽核錯誤("敏感稽核資料庫開啟失敗") from None


def _驗證稽核結構(連線) -> None:
    """在持有 BEGIN IMMEDIATE 寫交易時驗證精確結構與完整遷移帳本。"""
    結構 = 遷移紀錄 = None
    try:
        結構 = tuple(連線.execute(
            "SELECT type,name,tbl_name,sql FROM sqlite_master "
            "WHERE tbl_name='audit_events' ORDER BY type,name"))
        遷移紀錄 = tuple(連線.execute(
            "SELECT version,name FROM published_api_schema_migrations ORDER BY version"))
        if 結構 != _稽核結構 or 遷移紀錄 != _必要遷移:
            raise ValueError
        連線 = 結構 = 遷移紀錄 = None
    except BaseException:
        連線 = 結構 = 遷移紀錄 = None
        raise


def _驗證識別碼(呼叫識別碼, 端點識別碼, 請求識別碼) -> None:
    """在任何 DTO、工廠、時鐘或資料庫操作前驗證直接識別碼。"""
    try:
        for 識別碼 in (呼叫識別碼, 端點識別碼, 請求識別碼):
            if type(識別碼) is not str or not 識別碼.strip() or len(識別碼) > 512:
                raise ValueError
    except BaseException:
        呼叫識別碼 = 端點識別碼 = 請求識別碼 = 識別碼 = None
        raise


def _重建命中們(結果) -> tuple[目標敏感命中, ...]:
    """重建完整 L06 結果並拒絕非嚴格 deterministic 順序。"""
    重建 = 命中們 = 命中 = 前鍵 = 鍵 = None
    try:
        if type(結果) is not 敏感偵測擷取結果:
            raise ValueError
        重建 = 敏感偵測擷取結果(
            object.__getattribute__(結果, "命令"), object.__getattribute__(結果, "命中們"),
            object.__getattribute__(結果, "警告代碼們"))
        命中們 = object.__getattribute__(重建, "命中們")
        for 命中 in 命中們:
            鍵 = (object.__getattribute__(命中, "目標代碼"),
                 object.__getattribute__(命中, "JSON路徑"), object.__getattribute__(命中, "開始"),
                 object.__getattribute__(命中, "結束"), object.__getattribute__(命中, "類型代碼"))
            if 前鍵 is not None and 鍵 <= 前鍵:
                raise ValueError
            前鍵 = 鍵
        回傳值 = tuple(命中們)
        結果 = 重建 = 命中們 = 命中 = 前鍵 = 鍵 = None
        return 回傳值
    except BaseException:
        結果 = 重建 = 命中們 = 命中 = 前鍵 = 鍵 = None
        raise


def _是非負有限時間(值) -> bool:
    """只接受精確 int/float 且總化 overflow。"""
    try:
        return type(值) in (int, float) and math.isfinite(值) and 值 >= 0
    except (OverflowError, ValueError):
        return False
