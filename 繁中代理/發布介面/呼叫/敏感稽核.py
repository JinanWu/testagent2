"""把 L06 位置命中逐筆寫入 caller transaction 的 hit/audit authority。

參數／欄位：不適用；本模組定義敏感命中的稽核附加操作與固定結構契約。
回傳：不適用；各附加操作的回傳契約由其文件字串分別說明。
例外：匯入相依模組失敗時原樣傳出匯入例外。
副作用：匯入時只定義常數與函式，不開啟資料庫或附加稽核事件。
"""

from __future__ import annotations

import json
import math
import os
import re
from pathlib import Path
import secrets
import sqlite3
import stat
import time
from asyncio import CancelledError
from dataclasses import dataclass
from typing import Callable

from ..資料庫結構契約 import 遷移帳本 as _必要遷移, 驗證資料庫結構
from .擷取政策 import 敏感偵測擷取結果, 目標敏感命中

_控制流程例外 = (KeyboardInterrupt, SystemExit, GeneratorExit, CancelledError)
_Path具體型別 = type(Path())
_安全識別格式 = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}\Z")
_最大呼叫命中數 = 1024
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


@dataclass(frozen=True, slots=True, init=False)
class 敏感命中交易收據:
    """只由 writer 建立、只含安全 identity 與數量的目前交易收據。"""

    呼叫識別碼: str
    命中數: int
    稽核識別碼們: tuple[str, ...]
    命中識別碼們: tuple[str, ...]

    def __init__(self, *_參數, **_關鍵字) -> None:
        """拒絕外部直接建構；收據只能從已驗證資料庫結果建立。"""
        raise TypeError("敏感命中交易收據不可直接建立")


def _建立交易收據(
    呼叫識別碼: str, 稽核識別碼們: tuple[str, ...], 命中識別碼們: tuple[str, ...],
) -> 敏感命中交易收據:
    """從已完整驗證的安全 identity 建立不可變收據。"""
    收據 = object.__new__(敏感命中交易收據)
    object.__setattr__(收據, "呼叫識別碼", 呼叫識別碼)
    object.__setattr__(收據, "命中數", len(命中識別碼們))
    object.__setattr__(收據, "稽核識別碼們", 稽核識別碼們)
    object.__setattr__(收據, "命中識別碼們", 命中識別碼們)
    return 收據


class SQLite敏感稽核儲存庫:
    """提供 caller-owned pair writer 與舊式 self-owned audit convenience。"""

    def __init__(self, 資料庫: str | Path, *, 時鐘: Callable[[], float] = time.time,
                 識別碼工廠: Callable[[], str] | None = None,
                 連線工廠: Callable[..., sqlite3.Connection] = sqlite3.connect,
                 命中識別碼工廠: Callable[[], str] | None = None) -> None:
        """保存可測依賴；初始化不開資料庫。"""
        try:
            if (type(資料庫) not in (str, _Path具體型別)
                    or type(資料庫) is str and not 資料庫
                    or not callable(時鐘) or not callable(連線工廠)
                    or 識別碼工廠 is not None and not callable(識別碼工廠)
                    or 命中識別碼工廠 is not None and not callable(命中識別碼工廠)):
                raise ValueError
            self._資料庫 = Path(資料庫)
            self._時鐘 = 時鐘
            self._識別碼工廠 = 識別碼工廠 or (lambda: f"audit-{secrets.token_hex(16)}")
            self._命中識別碼工廠 = 命中識別碼工廠 or (lambda: f"hit-{secrets.token_hex(16)}")
            self._連線工廠 = 連線工廠
        except BaseException as 錯誤:
            是控制流程 = type(錯誤) in _控制流程例外
            self = 資料庫 = 時鐘 = 識別碼工廠 = 連線工廠 = 命中識別碼工廠 = 錯誤 = None
            if 是控制流程:
                raise
            raise 敏感稽核錯誤("敏感稽核儲存庫初始化失敗") from None

    def 驗證啟動結構(self) -> None:
        """startup 以短生命週期交易驗證 writer 所需完整 schema。

        成功或失敗都回滾並關閉連線；不寫入 hit/audit，不保留 connection authority。
        """
        連線 = 主要錯誤 = 清理控制 = None
        已回滾 = 已嘗試關閉 = False
        try:
            連線 = self._開啟連線()
            連線.execute("BEGIN IMMEDIATE")
            驗證資料庫結構(連線)
            _驗證稽核結構(連線)
            已回滾 = True
            連線.rollback()
            已嘗試關閉 = True
            連線.close()
            連線 = None
            return
        except BaseException as 錯誤:
            主要錯誤 = 錯誤
            主要是控制流程 = type(錯誤) in _控制流程例外
            if 連線 is not None and not 已回滾:
                try:
                    連線.rollback()
                except BaseException as 回滾錯誤:
                    if (not 主要是控制流程 and 清理控制 is None
                            and type(回滾錯誤) in _控制流程例外):
                        清理控制 = 回滾錯誤
            if 連線 is not None and not 已嘗試關閉:
                try:
                    連線.close()
                except BaseException as 關閉錯誤:
                    if (not 主要是控制流程 and 清理控制 is None
                            and type(關閉錯誤) in _控制流程例外):
                        清理控制 = 關閉錯誤
        if 主要是控制流程:
            raise 主要錯誤
        if 清理控制 is not None:
            主要錯誤 = None
            清理控制.__cause__ = 清理控制.__context__ = None
            清理控制.__suppress_context__ = True
            raise 清理控制
        raise 敏感稽核錯誤("敏感稽核啟動結構無效") from None

    def 寫入呼叫交易(
        self,
        連線: sqlite3.Connection,
        結果: 敏感偵測擷取結果,
        呼叫識別碼: str,
        端點識別碼: str,
        *,
        工具呼叫識別碼們: tuple[str | None, ...] | None = None,
    ) -> 敏感命中交易收據:
        """在 caller 已持有的交易內寫入一對一 audit/hit，不控制交易或連線。

        相同 invocation/target/tool source 的完整 hit set 可安全 replay；任何 set 或
        已存 audit/hit 漂移都固定拒絕。回傳只證明目前 caller transaction 中可見的
        exact 寫入，不宣稱 caller 尚未執行的 commit。
        """
        命中們 = 對照 = 期望們 = 來源們 = 已存列們 = None
        稽核識別碼們 = 命中識別碼們 = 時間 = 資料列們 = None
        try:
            if type(連線) is not sqlite3.Connection:
                raise ValueError
            _驗證交易識別碼(呼叫識別碼, 端點識別碼)
            命中們 = _重建命中們(結果)
            結果 = None
            對照 = _重建工具對照(命中們, 工具呼叫識別碼們)
            工具呼叫識別碼們 = None
            if not 連線.in_transaction:
                raise ValueError
            外鍵列 = 連線.execute("PRAGMA foreign_keys").fetchone()
            if type(外鍵列) is not tuple or 外鍵列 != (1,):
                raise ValueError
            驗證資料庫結構(連線)
            呼叫列 = 連線.execute(
                "SELECT id,endpoint_id FROM endpoint_invocations WHERE id=?", (呼叫識別碼,),
            ).fetchone()
            if type(呼叫列) is not tuple or 呼叫列 != (呼叫識別碼, 端點識別碼):
                raise ValueError
            for 工具識別碼 in dict.fromkeys(值 for 值 in 對照 if 值 is not None):
                工具列 = 連線.execute(
                    "SELECT id,invocation_id FROM endpoint_tool_calls WHERE id=?",
                    (工具識別碼,),
                ).fetchone()
                if type(工具列) is not tuple or 工具列 != (工具識別碼, 呼叫識別碼):
                    raise ValueError

            期望們 = tuple(
                (object.__getattribute__(命中, "目標代碼"), 對照[索引],
                 object.__getattribute__(命中, "類型代碼"),
                 object.__getattribute__(命中, "JSON路徑"),
                 object.__getattribute__(命中, "開始"), object.__getattribute__(命中, "結束"))
                for 索引, 命中 in enumerate(命中們)
            )
            來源們 = tuple(dict.fromkeys((列[0], 列[1]) for 列 in 期望們))
            已存列們 = _讀取來源命中(連線, 呼叫識別碼, 來源們)
            總命中數列 = 連線.execute(
                "SELECT count(*) FROM invocation_sensitive_hits WHERE invocation_id=?",
                (呼叫識別碼,),
            ).fetchone()
            if (type(總命中數列) is not tuple or len(總命中數列) != 1
                    or type(總命中數列[0]) is not int
                    or not 0 <= 總命中數列[0] <= _最大呼叫命中數):
                raise ValueError
            if 已存列們:
                _驗證回放完整集合(連線, 呼叫識別碼, 端點識別碼, 期望們, 已存列們)
                稽核識別碼們 = tuple(列[6] for 列 in 已存列們)
                命中識別碼們 = tuple(列[7] for 列 in 已存列們)
                return _建立交易收據(呼叫識別碼, 稽核識別碼們, 命中識別碼們)
            if 總命中數列[0] + len(命中們) > _最大呼叫命中數:
                raise ValueError
            if not 命中們:
                return _建立交易收據(呼叫識別碼, (), ())
            稽核識別碼們 = _配置安全識別碼們(self._識別碼工廠, len(命中們), ())
            命中識別碼們 = _配置安全識別碼們(
                self._命中識別碼工廠, len(命中們), 稽核識別碼們,
            )
            時間 = self._時鐘()
            if not _是非負有限時間(時間):
                raise ValueError
            時間 = float(時間)
            資料列們 = []
            for 索引, 命中 in enumerate(命中們):
                中繼資料 = {
                    "warning_code": "sensitive_data_detected",
                    "target": object.__getattribute__(命中, "目標代碼"),
                    "detector_type": object.__getattribute__(命中, "類型代碼"),
                    "json_path": object.__getattribute__(命中, "JSON路徑"),
                    "start": object.__getattribute__(命中, "開始"),
                    "end": object.__getattribute__(命中, "結束"),
                }
                中繼資料JSON = json.dumps(
                    中繼資料, ensure_ascii=False, sort_keys=True,
                    separators=(",", ":"), allow_nan=False,
                )
                資料列們.append((命中, 對照[索引], 稽核識別碼們[索引],
                               命中識別碼們[索引], 中繼資料JSON))
            for 命中, 工具識別碼, 稽核識別碼, 命中識別碼, 中繼資料JSON in 資料列們:
                稽核游標 = 連線.execute(
                    "INSERT INTO audit_events(id,event_id,occurred_at,action,outcome,actor_type,"
                    "actor_id,resource_type,resource_id,request_id,endpoint_id,invocation_id,"
                    "metadata_json,created_at) VALUES(?,?,?,'published_api.sensitive_data_detected',"
                    "'success','system',NULL,'invocation',?,NULL,?,?,?,?)",
                    (稽核識別碼, 稽核識別碼, 時間, 呼叫識別碼, 端點識別碼,
                     呼叫識別碼, 中繼資料JSON, 時間),
                )
                if type(稽核游標.rowcount) is not int or 稽核游標.rowcount != 1:
                    raise ValueError
                命中游標 = 連線.execute(
                    "INSERT INTO invocation_sensitive_hits(id,invocation_id,tool_call_id,target_type,"
                    "detector_type,json_path,start_offset,end_offset,audit_event_id,detected_at) "
                    "VALUES(?,?,?,?,?,?,?,?,?,?)",
                    (命中識別碼, 呼叫識別碼, 工具識別碼,
                     object.__getattribute__(命中, "目標代碼"),
                     object.__getattribute__(命中, "類型代碼"),
                     object.__getattribute__(命中, "JSON路徑"),
                     object.__getattribute__(命中, "開始"), object.__getattribute__(命中, "結束"),
                     稽核識別碼, 時間),
                )
                if type(命中游標.rowcount) is not int or 命中游標.rowcount != 1:
                    raise ValueError
            已存列們 = _讀取來源命中(連線, 呼叫識別碼, 來源們)
            _驗證回放完整集合(連線, 呼叫識別碼, 端點識別碼, 期望們, 已存列們)
            if (tuple(列[6] for 列 in 已存列們) != 稽核識別碼們
                    or tuple(列[7] for 列 in 已存列們) != 命中識別碼們):
                raise ValueError
            return _建立交易收據(呼叫識別碼, 稽核識別碼們, 命中識別碼們)
        except _控制流程例外:
            raise
        except BaseException:
            raise 敏感稽核錯誤("敏感命中交易寫入失敗") from None

    def 附加偵測事件(self, 結果: 敏感偵測擷取結果, 呼叫識別碼: str,
                 端點識別碼: str, 請求識別碼: str) -> tuple[str, ...]:
        """保留 import/class compatibility，但永久拒絕 audit-only authority。"""
        raise 敏感稽核錯誤("舊式敏感稽核附加已停用") from None

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


def _驗證交易識別碼(呼叫識別碼: object, 端點識別碼: object) -> None:
    """在 callback 或 SQL 前拒絕非 canonical safe identity。"""
    if (type(呼叫識別碼) is not str or _安全識別格式.fullmatch(呼叫識別碼) is None
            or type(端點識別碼) is not str or _安全識別格式.fullmatch(端點識別碼) is None):
        raise ValueError


def _重建工具對照(
    命中們: tuple[目標敏感命中, ...],
    工具呼叫識別碼們: tuple[str | None, ...] | None,
) -> tuple[str | None, ...]:
    """建立與 deterministic hit order 等長的 exact nullable tool mapping。"""
    if 工具呼叫識別碼們 is None:
        if any(object.__getattribute__(命中, "目標代碼").startswith("tool_") for 命中 in 命中們):
            raise ValueError
        return (None,) * len(命中們)
    if type(工具呼叫識別碼們) is not tuple or len(工具呼叫識別碼們) != len(命中們):
        raise ValueError
    結果: list[str | None] = []
    for 命中, 工具識別碼 in zip(命中們, 工具呼叫識別碼們):
        是工具 = object.__getattribute__(命中, "目標代碼").startswith("tool_")
        if 是工具:
            if type(工具識別碼) is not str or _安全識別格式.fullmatch(工具識別碼) is None:
                raise ValueError
        elif 工具識別碼 is not None:
            raise ValueError
        結果.append(工具識別碼)
    return tuple(結果)


def _配置安全識別碼們(
    工廠: Callable[[], str], 數量: int, 禁止: tuple[str, ...],
) -> tuple[str, ...]:
    """依 hit order 配置 exact、canonical 且批次唯一的安全 identity。"""
    結果: list[str] = []
    已見 = set(禁止)
    for _ in range(數量):
        識別碼 = 工廠()
        if (type(識別碼) is not str or _安全識別格式.fullmatch(識別碼) is None
                or 識別碼 in 已見):
            raise ValueError
        已見.add(識別碼)
        結果.append(識別碼)
    return tuple(結果)


def _讀取單一來源命中(
    連線: sqlite3.Connection, 呼叫識別碼: str, 來源: tuple[str, str | None],
) -> tuple[tuple[object, ...], ...]:
    """有界讀取單一 target/tool source 的 joined hit/audit authority。"""
    目標, 工具識別碼 = 來源
    工具條件 = "h.tool_call_id IS NULL" if 工具識別碼 is None else "h.tool_call_id=?"
    參數 = (呼叫識別碼, 目標) if 工具識別碼 is None else (呼叫識別碼, 目標, 工具識別碼)
    游標 = 連線.execute(
        "SELECT h.target_type,h.tool_call_id,h.detector_type,h.json_path,h.start_offset,h.end_offset,"
        "h.audit_event_id,h.id,h.detected_at,a.id,a.event_id,a.occurred_at,a.action,a.outcome,"
        "a.actor_type,a.actor_id,a.resource_type,a.resource_id,a.request_id,a.endpoint_id,"
        "a.invocation_id,a.metadata_json,a.created_at FROM invocation_sensitive_hits AS h "
        "LEFT JOIN audit_events AS a ON a.id=h.audit_event_id "
        f"WHERE h.invocation_id=? AND h.target_type=? AND {工具條件} "
        "ORDER BY h.target_type,h.json_path,h.start_offset,h.end_offset,h.detector_type,"
        "h.tool_call_id,h.id LIMIT 1025",
        參數,
    )
    結果 = tuple(游標.fetchall())
    if len(結果) > 1024 or any(type(列) is not tuple or len(列) != 23 for 列 in 結果):
        raise ValueError
    return 結果


def _讀取來源命中(
    連線: sqlite3.Connection,
    呼叫識別碼: str,
    來源們: tuple[tuple[str, str | None], ...],
) -> tuple[tuple[object, ...], ...]:
    """按 L06 identity order 組合所有本次 source 的 joined authority rows。"""
    結果: list[tuple[object, ...]] = []
    for 來源 in 來源們:
        結果.extend(_讀取單一來源命中(連線, 呼叫識別碼, 來源))
    結果.sort(key=lambda 列: (列[0], 列[3], 列[4], 列[5], 列[2], 列[1] or "", 列[7]))
    if len(結果) > 1024:
        raise ValueError
    return tuple(結果)


def _驗證回放完整集合(
    連線: sqlite3.Connection,
    呼叫識別碼: str,
    端點識別碼: str,
    期望們: tuple[tuple[object, ...], ...],
    已存列們: tuple[tuple[object, ...], ...],
) -> None:
    """重驗 exact hit set、pairing、timestamp、audit shape 與 canonical metadata。"""
    if len(已存列們) != len(期望們):
        raise ValueError
    for 期望, 列 in zip(期望們, 已存列們):
        if 列[:6] != 期望:
            raise ValueError
        稽核識別碼, 命中識別碼, 偵測時間 = 列[6:9]
        if (type(稽核識別碼) is not str or _安全識別格式.fullmatch(稽核識別碼) is None
                or type(命中識別碼) is not str or _安全識別格式.fullmatch(命中識別碼) is None
                or not _是非負有限時間(偵測時間)):
            raise ValueError
        if 列[9:21] != (
            稽核識別碼, 稽核識別碼, 偵測時間,
            "published_api.sensitive_data_detected", "success", "system", None,
            "invocation", 呼叫識別碼, None, 端點識別碼, 呼叫識別碼,
        ) or 列[22] != 偵測時間:
            raise ValueError
        目標, _, 偵測器, 路徑, 開始, 結束 = 期望
        中繼資料 = {
            "warning_code": "sensitive_data_detected", "target": 目標,
            "detector_type": 偵測器, "json_path": 路徑, "start": 開始, "end": 結束,
        }
        預期JSON = json.dumps(
            中繼資料, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False,
        )
        if type(列[21]) is not str or 列[21] != 預期JSON:
            raise ValueError
    孤立列 = 連線.execute(
        "SELECT count(*) FROM audit_events AS a LEFT JOIN invocation_sensitive_hits AS h "
        "ON h.audit_event_id=a.id WHERE a.invocation_id=? "
        "AND a.action='published_api.sensitive_data_detected' AND h.id IS NULL",
        (呼叫識別碼,),
    ).fetchone()
    if type(孤立列) is not tuple or len(孤立列) != 1 or type(孤立列[0]) is not int or 孤立列 != (0,):
        raise ValueError
