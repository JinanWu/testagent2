"""SQLite稽核資料庫的路徑釘選與schema fail-closed驗證。"""

from __future__ import annotations

import os
import sqlite3
import stat
from urllib.parse import quote

from .稽核結構 import _FOREIGN_KEYS, _INDEXES, _LEDGER, _OBJECT_SQL, _TABLE_INFO

_控制流程 = (KeyboardInterrupt, SystemExit, GeneratorExit)


def _開啟既有資料庫(路徑: str) -> sqlite3.Connection:
    """釘住 regular non-symlink inode；成功前發生任何錯誤都 exact-once close。"""
    連線 = 游標 = None
    開啟前 = 解析路徑 = 資料庫URI = 資料庫列 = 開啟路徑 = None
    開啟後 = 實際檔案 = 釘選識別 = 外鍵狀態 = None
    控制 = None
    關閉控制盒: list[BaseException] = []
    失敗 = False
    try:
        開啟前 = os.lstat(路徑)
        if not stat.S_ISREG(開啟前.st_mode) or 開啟前.st_size <= 0:
            raise ValueError
        解析路徑 = os.path.realpath(路徑)
        if 解析路徑 != os.path.abspath(路徑):
            raise ValueError
        資料庫URI = "file:" + quote(解析路徑, safe="/") + "?mode=rw"
        連線 = sqlite3.connect(資料庫URI, uri=True, isolation_level=None, timeout=30.0)
        游標 = 連線.execute("PRAGMA database_list")
        資料庫列 = 游標.fetchone()
        游標.close()
        游標 = None
        if type(資料庫列) is not tuple or len(資料庫列) != 3 or type(資料庫列[2]) is not str:
            raise ValueError
        開啟路徑 = os.path.realpath(資料庫列[2])
        開啟後 = os.stat(解析路徑)
        實際檔案 = os.stat(開啟路徑)
        釘選識別 = (開啟前.st_dev, 開啟前.st_ino)
        if 釘選識別 != (開啟後.st_dev, 開啟後.st_ino) or 釘選識別 != (實際檔案.st_dev, 實際檔案.st_ino):
            raise ValueError
        連線.execute("PRAGMA foreign_keys=ON")
        游標 = 連線.execute("PRAGMA foreign_keys")
        外鍵狀態 = 游標.fetchone()
        游標.close()
        游標 = None
        if 外鍵狀態 != (1,):
            raise ValueError
    except _控制流程 as 捕捉控制:
        _清理控制鏈(捕捉控制)
        控制 = 捕捉控制
        捕捉控制 = None
    except BaseException:
        失敗 = True
    路徑 = 游標 = None
    開啟前 = 解析路徑 = 資料庫URI = 資料庫列 = 開啟路徑 = 開啟後 = 實際檔案 = 釘選識別 = 外鍵狀態 = None
    if (失敗 or 控制 is not None) and 連線 is not None:
        關閉控制盒 = _關閉並取得控制(連線)
        連線 = None
    if 控制 is not None:
        關閉控制盒.clear()
        控制盒 = [控制]
        控制 = None
        _重拋控制(控制盒.pop())
    if 關閉控制盒:
        _重拋控制(關閉控制盒.pop())
    if 失敗 or 連線 is None:
        raise ValueError("invalid audit database") from None
    return 連線


def _驗證目前路徑(連線: sqlite3.Connection, 路徑: str) -> None:
    """交易取得後再次確認公開路徑仍指向已開啟 inode。"""
    控制 = 游標 = 可見檔案 = 資料庫列 = 實際檔案 = None
    失敗 = False
    try:
        可見檔案 = os.lstat(路徑)
        游標 = 連線.execute("PRAGMA database_list")
        資料庫列 = 游標.fetchone()
        游標.close()
        游標 = None
        if type(資料庫列) is not tuple or len(資料庫列) != 3 or type(資料庫列[2]) is not str:
            raise ValueError
        實際檔案 = os.stat(os.path.realpath(資料庫列[2]))
        if not stat.S_ISREG(可見檔案.st_mode) or (可見檔案.st_dev, 可見檔案.st_ino) != (實際檔案.st_dev, 實際檔案.st_ino):
            raise ValueError
    except _控制流程 as 捕捉控制:
        _清理控制鏈(捕捉控制)
        控制 = 捕捉控制
        捕捉控制 = None
    except BaseException:
        失敗 = True
    連線 = 路徑 = 游標 = 可見檔案 = 資料庫列 = 實際檔案 = None
    if 控制 is not None:
        控制盒 = [控制]
        控制 = None
        _重拋控制(控制盒.pop())
    if 失敗:
        raise ValueError("invalid audit database") from None


def _驗證schema(連線: sqlite3.Connection) -> None:
    """在 BEGIN IMMEDIATE 持有期間驗證完整 v6 ledger/table/FK/index/trigger。"""
    控制 = 遷移紀錄 = 資料表資訊 = 外鍵狀態 = 物件SQL = 索引資訊 = None
    資料列 = 名稱 = 欄位 = 項目 = None
    失敗 = False
    try:
        遷移紀錄 = tuple(連線.execute(
            "SELECT version,name FROM published_api_schema_migrations ORDER BY version"
        ))
        資料表資訊 = tuple(連線.execute("PRAGMA table_info(audit_events)"))
        外鍵狀態 = tuple(連線.execute("PRAGMA foreign_key_list(audit_events)"))
        物件SQL = tuple(連線.execute(
            "SELECT type,name,sql FROM sqlite_master "
            "WHERE tbl_name='audit_events' AND type IN ('table','trigger') ORDER BY type,name"
        ))
        索引資訊 = []
        for 資料列 in 連線.execute("PRAGMA index_list(audit_events)"):
            名稱 = 資料列[1]
            if type(名稱) is not str or 名稱 not in {項[0] for 項 in _INDEXES}:
                raise sqlite3.DatabaseError("invalid audit index")
            欄位 = tuple(項目[2] for 項目 in 連線.execute(f'PRAGMA index_info("{名稱}")'))
            索引資訊.append((名稱, 資料列[2], 資料列[3], 欄位))
        if (
            遷移紀錄 != _LEDGER or 資料表資訊 != _TABLE_INFO or 外鍵狀態 != _FOREIGN_KEYS
            or 物件SQL != _OBJECT_SQL or tuple(sorted(索引資訊)) != _INDEXES
        ):
            raise sqlite3.DatabaseError("invalid audit schema")
    except _控制流程 as 捕捉控制:
        _清理控制鏈(捕捉控制)
        控制 = 捕捉控制
        捕捉控制 = None
    except BaseException:
        失敗 = True
    連線 = 遷移紀錄 = 資料表資訊 = 外鍵狀態 = 物件SQL = 索引資訊 = None
    資料列 = 名稱 = 欄位 = 項目 = None
    if 控制 is not None:
        控制盒 = [控制]
        控制 = None
        _重拋控制(控制盒.pop())
    if 失敗:
        raise ValueError("invalid audit schema") from None


def _關閉並取得控制(連線: sqlite3.Connection) -> list[BaseException]:
    """Acquirer失敗時exact-once close，僅回傳去鏈控制流程。"""
    控制盒: list[BaseException] = []
    try:
        連線.close()
    except _控制流程 as 捕捉控制:
        _清理控制鏈(捕捉控制)
        BaseException.__setattr__(捕捉控制, "__traceback__", None)
        控制盒.append(捕捉控制)
        捕捉控制 = None
    except BaseException:
        pass
    連線 = None  # type: ignore[assignment]
    return 控制盒


def _清理控制鏈(控制: BaseException) -> None:
    """以BaseException原生setter移除敵對subclass的例外鏈。"""
    BaseException.__setattr__(控制, "__cause__", None)
    BaseException.__setattr__(控制, "__context__", None)
    BaseException.__setattr__(控制, "__suppress_context__", True)


def _重拋控制(控制: BaseException) -> None:
    """以乾淨traceback保留控制流程exact identity與args。"""
    try:
        _清理控制鏈(控制)
        BaseException.__setattr__(控制, "__traceback__", None)
        raise 控制
    except _控制流程:
        控制 = None  # type: ignore[assignment]
        raise
