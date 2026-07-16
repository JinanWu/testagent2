"""遷移 SQL 切分與 parser/authorizer 驗證 primitive。"""

from __future__ import annotations

import sqlite3


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
