"""發布介面的唯一權威資料庫結構帳本、指紋與有界驗證契約。

參數／欄位：不適用；本模組提供遷移帳本、結構指紋、錯誤與驗證操作。
回傳：不適用；各指紋及驗證操作的回傳契約由其文件字串分別說明。
例外：匯入標準相依模組失敗時原樣傳出匯入例外。
副作用：匯入時只建立固定常數與函式，不連線、查詢或修改資料庫。
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from typing import Any, NoReturn

遷移帳本 = (
    (1, "0001_建立發布端點核心.sql"),
    (2, "0002_建立憑證與稽核.sql"),
    (3, "0003_建立呼叫事件與工具紀錄.sql"),
    (4, "0004_建立限流與遮蔽資料.sql"),
    (5, "0005_建立網頁工作階段.sql"),
    (6, "0006_擴充稽核事件契約.sql"),
    (7, "0007_建立不可逆遮蔽墓碑.sql"),
    (8, "0008_建立五年保存候選索引.sql"),
    (9, "0009_建立保存相依識別索引.sql"),
    (10, "0010_建立來源驗證失敗節流.sql"),
    (11, "0011_重建空憑證為CRED結構.sql"),
    (12, "0012_建立技能套件收據.sql"),
    (13, "0013_建立Published工作階段歷史.sql"),
    (14, "0014_建立呼叫安全錯誤碼.sql"),
)
資料庫結構指紋 = "5ef3da7e002fc46145bad8172bef9442391e7fad2de5c1494a1372bba3c9037a"
_預期結構列數 = 70
_單筆結構文字上限 = 64 * 1024
_結構文字總上限 = 1024 * 1024
_結構名稱位元組上限 = 256
_遷移名稱位元組上限 = 256
_固定錯誤訊息 = "資料庫結構契約錯誤"
_控制流程例外 = (KeyboardInterrupt, SystemExit, GeneratorExit)


class 資料庫結構契約錯誤(sqlite3.DatabaseError):
    """表示資料庫結構、帳本或中繼資料不符合固定契約。

    參數：沿用 ``sqlite3.DatabaseError`` 的訊息參數。
    回傳：不適用；本類別是固定的關閉失敗訊號。
    例外：建構本身只可能傳出基底例外的標準錯誤。
    副作用：建立錯誤物件不讀寫資料庫，也不改變交易狀態。
    """


def _拒絕資料庫結構() -> NoReturn:
    """拋出固定資料庫結構契約錯誤。

    參數：無。
    回傳：不會正常回傳。
    例外：一律拋出 ``資料庫結構契約錯誤``。
    副作用：只建立並拋出錯誤，不操作連線或交易。
    """
    raise 資料庫結構契約錯誤(_固定錯誤訊息) from None


def _有界擷取(游標: Any, 預期筆數: int) -> list[tuple[Any, ...]]:
    """以預期筆數加一為硬上限逐列擷取 SQLite 結果。

    參數：``游標`` 提供 ``fetchone``；``預期筆數`` 是固定契約列數。
    回傳：至多為預期筆數的 exact tuple 列串列。
    例外：列數、列型別或游標行為異常時拋出 ``資料庫結構契約錯誤``。
    副作用：推進游標至結果結尾或第一筆超限列，不提交或修改資料庫。
    """
    結果: list[tuple[Any, ...]] = []
    try:
        for _索引 in range(預期筆數 + 1):
            資料列 = 游標.fetchone()
            if 資料列 is None:
                break
            if type(資料列) is not tuple:
                _拒絕資料庫結構()
            結果.append(資料列)
        if len(結果) != 預期筆數:
            _拒絕資料庫結構()
        return 結果
    except _控制流程例外:
        結果.clear()
        raise
    except 資料庫結構契約錯誤:
        結果.clear()
        raise
    except BaseException:
        結果.clear()
        _拒絕資料庫結構()


def _驗證遷移帳本(連線: sqlite3.Connection) -> None:
    """有界驗證遷移帳本的筆數、列形狀、exact type 與內容。

    參數：``連線`` 是呼叫端擁有且已開啟的 SQLite 連線。
    回傳：帳本完全符合時回傳 ``None``。
    例外：任何查詢、型別、預算或內容異常皆拋出 ``資料庫結構契約錯誤``。
    副作用：只讀取遷移帳本，不提交、回滾或關閉連線。
    """
    列數 = len(遷移帳本)
    中繼 = _有界擷取(
        連線.execute(
            "SELECT version,typeof(version),name,typeof(name),length(CAST(name AS BLOB)) "
            "FROM published_api_schema_migrations ORDER BY version LIMIT ?",
            (列數 + 1,),
        ),
        列數,
    )
    try:
        for 索引, 資料列 in enumerate(中繼):
            預期版本, 預期名稱 = 遷移帳本[索引]
            if (
                len(資料列) != 5
                or type(資料列[0]) is not int
                or 資料列[0] != 預期版本
                or 資料列[1] != "integer"
                or type(資料列[2]) is not str
                or 資料列[2] != 預期名稱
                or 資料列[3] != "text"
                or type(資料列[4]) is not int
                or not 0 < 資料列[4] <= _遷移名稱位元組上限
                or len(資料列[2].encode("utf-8")) != 資料列[4]
            ):
                _拒絕資料庫結構()
    finally:
        中繼.clear()


def _讀取有界結構列(連線: sqlite3.Connection) -> list[tuple[str, str, str, str]]:
    """先驗證 SQLite 結構中繼預算，再有界讀取完整結構列。

    參數：``連線`` 是呼叫端擁有且已開啟的 SQLite 連線。
    回傳：固定筆數、固定四欄且全部為 UTF-8 字串的結構列。
    例外：任何查詢、型別、列數、單筆或總預算異常皆拋出固定結構錯誤。
    副作用：兩次只讀掃描 ``sqlite_master``，不修改或關閉資料庫。
    """
    條件 = "name NOT LIKE 'sqlite_%'"
    中繼 = _有界擷取(
        連線.execute(
            "SELECT typeof(type),length(CAST(type AS BLOB)),"
            "typeof(name),length(CAST(name AS BLOB)),"
            "typeof(tbl_name),length(CAST(tbl_name AS BLOB)),"
            "typeof(sql),length(CAST(sql AS BLOB)) FROM sqlite_master WHERE "
            f"{條件} ORDER BY type,name LIMIT ?",
            (_預期結構列數 + 1,),
        ),
        _預期結構列數,
    )
    try:
        總預算 = 0
        for 資料列 in 中繼:
            if type(資料列) is not tuple or len(資料列) != 8:
                _拒絕資料庫結構()
            for 型別索引, 長度索引 in ((0, 1), (2, 3), (4, 5), (6, 7)):
                if 資料列[型別索引] != "text" or type(資料列[長度索引]) is not int:
                    _拒絕資料庫結構()
            if (
                not 0 < 資料列[1] <= 16
                or not 0 < 資料列[3] <= _結構名稱位元組上限
                or not 0 < 資料列[5] <= _結構名稱位元組上限
                or not 0 < 資料列[7] <= _單筆結構文字上限
            ):
                _拒絕資料庫結構()
            總預算 += 資料列[1] + 資料列[3] + 資料列[5] + 資料列[7]
            if 總預算 > _結構文字總上限:
                _拒絕資料庫結構()
    finally:
        中繼.clear()
    原始列 = _有界擷取(
        連線.execute(
            "SELECT type,name,tbl_name,sql FROM sqlite_master WHERE "
            f"{條件} ORDER BY type,name LIMIT ?",
            (_預期結構列數 + 1,),
        ),
        _預期結構列數,
    )
    結果: list[tuple[str, str, str, str]] = []
    try:
        總預算 = 0
        for 資料列 in 原始列:
            if len(資料列) != 4 or any(type(值) is not str for 值 in 資料列):
                _拒絕資料庫結構()
            位元組列 = tuple(值.encode("utf-8") for 值 in 資料列)
            if (
                not 0 < len(位元組列[0]) <= 16
                or not 0 < len(位元組列[1]) <= _結構名稱位元組上限
                or not 0 < len(位元組列[2]) <= _結構名稱位元組上限
                or not 0 < len(位元組列[3]) <= _單筆結構文字上限
            ):
                _拒絕資料庫結構()
            總預算 += sum(len(值) for 值 in 位元組列)
            if 總預算 > _結構文字總上限:
                _拒絕資料庫結構()
            結果.append(資料列)
        return 結果
    except _控制流程例外:
        結果.clear()
        raise
    except BaseException:
        結果.clear()
        _拒絕資料庫結構()
    finally:
        原始列.clear()


def 計算資料庫結構指紋(連線: sqlite3.Connection) -> str:
    """有界計算發布介面資料庫的完整結構指紋。

    參數：``連線`` 是已開啟且由呼叫端管理生命週期的 SQLite 連線。
    回傳：依固定物件順序與正規 JSON 計算的 SHA-256 十六進位字串。
    例外：連線、查詢、中繼型別或預算異常時拋出 ``資料庫結構契約錯誤``。
    副作用：只讀取結構中繼資料，不提交、回滾或關閉連線。
    """
    結構列: list[tuple[str, str, str, str]] = []
    try:
        結構列 = _讀取有界結構列(連線)
        正規內容 = json.dumps(結構列, ensure_ascii=False, separators=(",", ":"))
        return hashlib.sha256(正規內容.encode("utf-8")).hexdigest()
    except _控制流程例外:
        結構列.clear()
        raise
    except 資料庫結構契約錯誤:
        結構列.clear()
        raise
    except BaseException:
        結構列.clear()
        _拒絕資料庫結構()


def 驗證資料庫結構(連線: sqlite3.Connection) -> None:
    """有界驗證完整遷移帳本與資料庫結構指紋。

    參數：``連線`` 是已開啟且由呼叫端管理交易與生命週期的 SQLite 連線。
    回傳：帳本、列形狀、文字預算及指紋全數符合時回傳 ``None``。
    例外：任何輸入、查詢或契約異常皆拋出固定 ``資料庫結構契約錯誤``。
    副作用：只讀取遷移與結構中繼資料，不提交、回滾或關閉連線。
    """
    try:
        _驗證遷移帳本(連線)
        if 計算資料庫結構指紋(連線) != 資料庫結構指紋:
            _拒絕資料庫結構()
    except _控制流程例外:
        raise
    except 資料庫結構契約錯誤:
        raise
    except BaseException:
        _拒絕資料庫結構()
