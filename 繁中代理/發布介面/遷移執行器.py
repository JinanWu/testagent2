"""遷移 SQL 切分與 parser/authorizer 驗證 primitive。"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import sqlite3
import time
from collections.abc import Sequence


@dataclass(frozen=True)
class 遷移項目:
    """單一發布介面資料庫遷移項目。"""

    版本: int
    名稱: str
    SQL: str


class 遷移執行錯誤(RuntimeError):
    """代表遷移 runner 拒絕執行或偵測到 ledger 衝突。"""


class 遷移SQL錯誤(ValueError):
    """代表遷移 SQL 不符合發布介面允許的安全契約。"""


class 遷移授權狀態:
    """SQLite authorizer callable，記錄 runner 應拒絕的 opcode 類型。"""

    _拒絕動作 = {
        sqlite3.SQLITE_TRANSACTION: "TRANSACTION",
        sqlite3.SQLITE_SAVEPOINT: "SAVEPOINT",
        sqlite3.SQLITE_ATTACH: "ATTACH",
        sqlite3.SQLITE_DETACH: "DETACH",
        sqlite3.SQLITE_PRAGMA: "PRAGMA",
    }

    def __init__(self) -> None:
        self.拒絕類型: str | None = None

    def __call__(self, 動作碼: int, *_: object) -> int:
        拒絕類型 = self._拒絕動作.get(動作碼)
        if 拒絕類型 is not None:
            self.拒絕類型 = 拒絕類型
            return sqlite3.SQLITE_DENY
        return sqlite3.SQLITE_OK


def 拆分遷移SQL(腳本: str) -> tuple[str, ...]:
    """用 SQLite lexical completeness 將遷移腳本切為 semicolon-terminated statements。"""
    if not isinstance(腳本, str):
        腳本 = None
        raise 遷移SQL錯誤("遷移 SQL 不符合契約")

    陳述清單: list[str] = []
    目前片段: list[str] = []
    for 字元 in 腳本:
        目前片段.append(字元)
        if 字元 == ";" and sqlite3.complete_statement("".join(目前片段)):
            陳述 = "".join(目前片段).strip()
            if 陳述 and not _只含空白或註解(陳述)[0]:
                陳述清單.append(陳述)
            目前片段.clear()

    尾端 = "".join(目前片段)
    只有註解, 未關閉註解 = _只含空白或註解(尾端)
    if 未關閉註解 or not 只有註解:
        腳本 = None
        尾端 = None
        目前片段 = []
        陳述 = None
        陳述清單 = []
        字元 = None
        raise 遷移SQL錯誤("遷移 SQL 不符合契約")
    return tuple(陳述清單)


def 驗證遷移SQL(
    陳述清單: tuple[str, ...] | list[str],
    *,
    授權狀態: 遷移授權狀態 | None = None,
) -> None:
    """以獨立 in-memory SQLite connection 做 parser-only prepare 驗證。"""
    if not isinstance(陳述清單, (tuple, list)):
        陳述清單 = ()
        raise 遷移SQL錯誤("遷移 SQL 不符合契約")

    連線 = sqlite3.connect(":memory:")
    狀態 = 授權狀態 if 授權狀態 is not None else 遷移授權狀態()
    try:
        連線.set_authorizer(狀態)
        for 陳述 in 陳述清單:
            if not isinstance(陳述, str):
                陳述 = None
                陳述清單 = ()
                raise 遷移SQL錯誤("遷移 SQL 不符合契約")
            錯誤訊息: str | None = None
            狀態.拒絕類型 = None
            try:
                游標 = 連線.execute("EXPLAIN " + 陳述)
                游標.close()
            except sqlite3.Error as 錯誤:
                if 狀態.拒絕類型 is not None:
                    錯誤訊息 = "遷移 SQL 包含禁止操作"
                elif not _是可延後語意錯誤(錯誤):
                    錯誤訊息 = "遷移 SQL 不符合契約"
            if 錯誤訊息 is not None:
                陳述 = None
                陳述清單 = ()
                游標 = None
                錯誤 = None
                raise 遷移SQL錯誤(錯誤訊息)
    finally:
        連線.close()


def 執行遷移(資料庫路徑: str | Path, 遷移清單: Sequence[遷移項目]) -> tuple[int, ...]:
    """以獨立 ledger 與每筆交易原子套用發布介面 schema 遷移。"""
    已驗證清單 = _驗證遷移清單(遷移清單)
    if not 已驗證清單:
        return ()

    連線 = sqlite3.connect(str(資料庫路徑), timeout=30.0, isolation_level=None)
    已套用: list[int] = []
    try:
        _啟用並確認外鍵(連線)
        for 項目 in 已驗證清單:
            if _套用單一遷移(連線, 項目):
                已套用.append(項目.版本)
    finally:
        連線.set_authorizer(None)
        連線.close()
    return tuple(已套用)


def _驗證遷移清單(遷移清單: Sequence[遷移項目]) -> tuple[遷移項目, ...]:
    版本集合: set[int] = set()
    已驗證: list[遷移項目] = []
    for 項目 in 遷移清單:
        版本 = 項目.版本
        名稱 = 項目.名稱
        SQL = 項目.SQL
        if not isinstance(版本, int) or isinstance(版本, bool) or 版本 <= 0:
            raise 遷移執行錯誤("遷移項目不符合契約")
        if 版本 in 版本集合:
            raise 遷移執行錯誤("遷移項目不符合契約")
        if not isinstance(名稱, str) or 名稱.strip() == "":
            raise 遷移執行錯誤("遷移項目不符合契約")
        if not isinstance(SQL, str):
            SQL = None
            raise 遷移執行錯誤("遷移項目不符合契約")
        版本集合.add(版本)
        已驗證.append(遷移項目(版本, 名稱.strip(), SQL))
    return tuple(sorted(已驗證, key=lambda item: item.版本))


def _啟用並確認外鍵(連線: sqlite3.Connection) -> None:
    連線.execute("PRAGMA foreign_keys = ON")
    if 連線.execute("PRAGMA foreign_keys").fetchone() != (1,):
        raise 遷移執行錯誤("資料庫連線不符合遷移契約")


def _套用單一遷移(連線: sqlite3.Connection, 項目: 遷移項目) -> bool:
    授權狀態: 遷移授權狀態 | None = None
    禁止操作 = False
    try:
        連線.execute("BEGIN IMMEDIATE")
        _建立ledger(連線)
        既有 = 連線.execute(
            "SELECT name FROM published_api_schema_migrations WHERE version = ?",
            (項目.版本,),
        ).fetchone()
        if 既有 is not None:
            連線.execute("ROLLBACK")
            if 既有[0] == 項目.名稱:
                return False
            raise 遷移執行錯誤(f"遷移版本 {項目.版本} 名稱衝突: {既有[0]} != {項目.名稱}")

        陳述清單 = 拆分遷移SQL(項目.SQL)
        授權狀態 = 遷移授權狀態()
        驗證遷移SQL(陳述清單, 授權狀態=授權狀態)
        授權狀態 = 遷移授權狀態()
        連線.set_authorizer(授權狀態)
        for 陳述 in 陳述清單:
            授權狀態.拒絕類型 = None
            連線.execute(陳述)
        連線.set_authorizer(None)
        連線.execute(
            "INSERT INTO published_api_schema_migrations(version, name, applied_at) VALUES (?, ?, ?)",
            (項目.版本, 項目.名稱, time.time()),
        )
        連線.execute("COMMIT")
        return True
    except sqlite3.DatabaseError:
        _rollback並清理authorizer(連線)
        if 授權狀態 is not None and 授權狀態.拒絕類型 is not None:
            禁止操作 = True
        else:
            raise
    except 遷移SQL錯誤 as 錯誤:
        _rollback並清理authorizer(連線)
        if str(錯誤) == "遷移 SQL 包含禁止操作":
            禁止操作 = True
        else:
            raise
    except Exception:
        _rollback並清理authorizer(連線)
        raise
    if 禁止操作:
        raise 遷移執行錯誤("遷移 SQL 包含禁止操作")
    return False


def _建立ledger(連線: sqlite3.Connection) -> None:
    連線.execute(
        "CREATE TABLE IF NOT EXISTS published_api_schema_migrations("
        "version INTEGER PRIMARY KEY CHECK(typeof(version)='integer' AND version>0),"
        "name TEXT NOT NULL CHECK(trim(name)<>''),"
        "applied_at REAL NOT NULL)"
    )


def _rollback並清理authorizer(連線: sqlite3.Connection) -> None:
    連線.set_authorizer(None)
    try:
        連線.execute("ROLLBACK")
    except sqlite3.DatabaseError:
        pass


def _只含空白或註解(文字: str) -> tuple[bool, bool]:
    索引 = 0
    長度 = len(文字)
    while 索引 < 長度:
        字元 = 文字[索引]
        if 字元.isspace():
            索引 += 1
            continue
        if 文字.startswith("--", 索引):
            換行 = 文字.find("\n", 索引 + 2)
            if 換行 == -1:
                return True, False
            索引 = 換行 + 1
            continue
        if 文字.startswith("/*", 索引):
            結尾 = 文字.find("*/", 索引 + 2)
            if 結尾 == -1:
                return False, True
            索引 = 結尾 + 2
            continue
        return False, False
    return True, False


def _是可延後語意錯誤(錯誤: sqlite3.Error) -> bool:
    訊息 = str(錯誤).lower()
    return any(
        片段 in 訊息
        for 片段 in (
            "no such table",
            "no such column",
            "no such index",
            "no such trigger",
            "no such function",
            "incorrect number of bindings supplied",
            "uses 1, and there are 0 supplied",
        )
    )
