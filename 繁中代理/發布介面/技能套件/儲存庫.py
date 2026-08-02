"""保存技能套件收據並查詢檔案系統協調狀態。

參數：不適用；模組公開接受呼叫端 SQLite 連線的收據儲存庫。回傳：不適用。
例外：匯入時若基礎模組不可用，傳出標準匯入例外；操作錯誤由公開類別固定映射。
副作用：匯入只建立類別、正規表示式與型別，不開啟資料庫或啟動交易。
"""

from __future__ import annotations

from dataclasses import dataclass
import math
from pathlib import PurePosixPath
import re
import sqlite3
from typing import cast, Iterable, NoReturn

from .發布器 import 套件發布收據


class 套件收據錯誤(RuntimeError):
    """表示套件收據無效或資料庫操作失敗。

    參數：沿用 ``RuntimeError`` 的訊息參數。
    回傳：不適用；本類別是失敗訊號。
    例外：建構本身只可能傳出基底例外的標準錯誤。
    副作用：建立錯誤物件不會操作資料庫。
    """


@dataclass(frozen=True, slots=True)
class 套件資料庫收據:
    """保存 ``published_skill_bundles`` 的不可變列投影。

    欄位：套件與版本識別碼建立關聯；清單參照、清單摘要及套件雜湊識別成果；
    總位元組數記錄內容大小；狀態、發布時間與協調時間記錄生命週期。
    回傳：建構後得到不可變列投影。
    例外：dataclass 建構不額外驗證；型別或值契約由儲存庫邊界負責。
    副作用：建構只保存不可變資料，不查詢或修改資料庫。
    """

    套件識別碼: str
    版本識別碼: str
    清單參照: str
    清單摘要: str
    套件雜湊: str
    總位元組數: int
    狀態: str
    發布時間: float
    協調時間: float | None


_識別格式 = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}\Z")
_雜湊格式 = re.compile(r"[0-9a-f]{64}\Z")
_最大總位元組數 = 4 * 1024 * 1024
_最大清單參照數 = 32 * 256
_最大清單參照位元組數 = 1024
_固定錯誤訊息 = "套件收據錯誤"
_控制流程例外 = (KeyboardInterrupt, SystemExit, GeneratorExit)
_收據欄位數 = 9


def _拒絕收據() -> NoReturn:
    """拋出固定套件收據錯誤。

    參數：無。
    回傳：不會正常回傳。
    例外：一律拋出 ``套件收據錯誤``。
    副作用：只建立並拋出錯誤，不執行 SQL 或改變交易。
    """
    raise 套件收據錯誤(_固定錯誤訊息) from None


def _合法識別(值: object) -> bool:
    """判斷值是否為長度受限的 exact 識別字串。

    參數：``值`` 是待驗證物件。
    回傳：僅 exact ``str`` 且符合固定識別格式時為真。
    例外：沒有預期例外；無法驗證的值回傳假。
    副作用：只執行正規表示式比對，不修改輸入或資料庫。
    """
    return type(值) is str and _識別格式.fullmatch(值) is not None


def _合法雜湊(值: object) -> bool:
    """判斷值是否為小寫 SHA-256 十六進位摘要。

    參數：``值`` 是待驗證物件。
    回傳：僅 exact ``str`` 且含六十四個小寫十六進位字元時為真。
    例外：沒有預期例外；無法驗證的值回傳假。
    副作用：只執行正規表示式比對，不修改輸入或資料庫。
    """
    return type(值) is str and _雜湊格式.fullmatch(值) is not None


def _合法清單參照(值: object) -> bool:
    """判斷清單參照是否為正規且不逃逸的相對 POSIX 路徑。

    參數：``值`` 是待驗證物件。
    回傳：exact 字串、UTF-8 長度受限、非絕對且不含空白元件或 ``..`` 時為真。
    例外：編碼或路徑解析異常會關閉為假，不向外傳出。
    副作用：只配置短暫路徑物件，不讀寫檔案系統或資料庫。
    """
    try:
        if type(值) is not str or not 值 or "\\" in 值:
            return False
        if len(值.encode("utf-8")) > _最大清單參照位元組數:
            return False
        路徑 = PurePosixPath(值)
        return (
            not 路徑.is_absolute()
            and str(路徑) == 值
            and all(元件 not in {"", ".", ".."} for 元件 in 路徑.parts)
        )
    except (UnicodeError, ValueError):
        return False


def _合法時間(值: object) -> bool:
    """判斷值是否為非布林、有限且非負的時間數字。

    參數：``值`` 是待驗證物件。
    回傳：僅 exact ``int`` 或 ``float``、有限且不小於零時為真。
    例外：沒有預期例外；不合法值回傳假。
    副作用：只執行數值檢查，不修改輸入或資料庫。
    """
    return (
        type(值) in (int, float)
        and math.isfinite(cast(float, 值))
        and cast(float, 值) >= 0
    )


def _合法收據欄位(
    套件識別碼: object,
    版本識別碼: object,
    清單參照: object,
    清單摘要: object,
    套件雜湊: object,
    總位元組數: object,
    狀態: object,
    發布時間: object,
    協調時間: object,
) -> bool:
    """一次驗證資料庫收據九欄的 exact type、界限與欄間關係。

    參數：九個物件依資料表欄位順序提供待驗證值。
    回傳：所有識別、路徑、摘要、大小、狀態與時間契約符合時為真。
    例外：沒有預期例外；任何不合法欄位回傳假。
    副作用：只讀取參數，不執行 SQL 或修改外部狀態。
    """
    return (
        _合法識別(套件識別碼)
        and _合法識別(版本識別碼)
        and _合法清單參照(清單參照)
        and _合法雜湊(清單摘要)
        and _合法雜湊(套件雜湊)
        and type(總位元組數) is int
        and 0 <= 總位元組數 <= _最大總位元組數
        and type(狀態) is str
        and 狀態 in {"published", "reconciled"}
        and _合法時間(發布時間)
        and (
            (狀態 == "published" and 協調時間 is None)
            or (
                狀態 == "reconciled"
                and _合法時間(協調時間)
                and cast(float, 協調時間) >= cast(float, 發布時間)
            )
        )
    )


def _重建資料列(資料列: object) -> 套件資料庫收據:
    """從 hostile SQLite 回傳值重建並驗證不可變收據投影。

    參數：``資料列`` 是 SQLite 查詢回傳的單列物件。
    回傳：九欄 exact tuple 通過完整驗證後的 ``套件資料庫收據``。
    例外：列型別、欄數或欄值異常時拋出固定 ``套件收據錯誤``。
    副作用：只配置不可變投影，不執行額外 SQL 或改變交易。
    """
    if (
        type(資料列) is not tuple
        or len(資料列) != _收據欄位數
        or not _合法收據欄位(*資料列)
    ):
        _拒絕收據()
    return 套件資料庫收據(*資料列)


class 套件收據儲存庫:
    """以呼叫端擁有的 exact SQLite 連線組合有界收據交易。

    參數：建構時接受且只接受 exact ``sqlite3.Connection``。
    回傳：方法回傳已重驗收據、可空收據或有界不可變序列。
    例外：所有輸入、SQLite 與 hostile row 失敗皆映射為固定 ``套件收據錯誤``。
    副作用：可在目前交易讀寫收據資料，但不提交、回滾或關閉連線。
    """

    def __init__(self, 連線: sqlite3.Connection) -> None:
        """保存呼叫端連線且不接管其生命週期。

        參數：``連線`` 必須是 exact ``sqlite3.Connection``，不接受子類或 duck type。
        回傳：無。
        例外：連線型別不符時拋出固定 ``套件收據錯誤``。
        副作用：只保存通過驗證的參照，不執行 SQL 或變更交易狀態。
        """
        if type(連線) is not sqlite3.Connection:
            _拒絕收據()
        self.連線 = 連線

    def 新增(
        self,
        *,
        版本識別碼: str,
        收據: 套件發布收據,
        發布時間: float,
        狀態: str = "published",
        協調時間: float | None = None,
    ) -> 套件資料庫收據:
        """在目前交易新增一筆已完整預檢的不可變技能套件收據。

        參數：版本識別碼連結端點版本；收據描述成果；時間與狀態描述生命週期。
        回傳：與成功寫入列相同且重新驗證的 ``套件資料庫收據``。
        例外：任何輸入或寫入異常皆拋出固定 ``套件收據錯誤``。
        副作用：完整預檢成功後在目前交易執行一次插入；不提交、回滾或關閉連線。
        """
        if type(收據) is not 套件發布收據:
            _拒絕收據()
        欄位 = (
            收據.套件識別碼,
            版本識別碼,
            收據.清單參照,
            收據.清單摘要,
            收據.套件雜湊,
            收據.總位元組數,
            狀態,
            發布時間,
            協調時間,
        )
        if not _合法收據欄位(*欄位):
            _拒絕收據()
        try:
            游標 = self.連線.execute(
                "INSERT INTO published_skill_bundles(bundle_id,version_id,manifest_reference,"
                "manifest_digest,bundle_hash,total_bytes,state,published_at,reconciled_at) "
                "VALUES(?,?,?,?,?,?,?,?,?)",
                欄位,
            )
            if type(游標.rowcount) is not int or 游標.rowcount != 1:
                _拒絕收據()
            return 套件資料庫收據(*欄位)
        except _控制流程例外:
            raise
        except 套件收據錯誤:
            raise
        except BaseException:
            _拒絕收據()

    def 依版本查詢(self, 版本識別碼: str) -> 套件資料庫收據 | None:
        """依 exact 端點版本識別碼查詢並重驗單一套件收據。

        參數：``版本識別碼`` 是符合固定格式的線上版本主鍵。
        回傳：查無資料時為 ``None``，否則為完整重驗的不可變收據。
        例外：輸入、SQLite 或資料列異常皆拋出固定 ``套件收據錯誤``。
        副作用：讀取目前交易可見資料，不修改、提交或關閉連線。
        """
        if not _合法識別(版本識別碼):
            _拒絕收據()
        try:
            資料列 = self.連線.execute(
                "SELECT bundle_id,version_id,manifest_reference,manifest_digest,bundle_hash,total_bytes,"
                "state,published_at,reconciled_at FROM published_skill_bundles WHERE version_id=?",
                (版本識別碼,),
            ).fetchone()
            return None if 資料列 is None else _重建資料列(資料列)
        except _控制流程例外:
            raise
        except 套件收據錯誤:
            raise
        except BaseException:
            _拒絕收據()

    def 查詢未參照清單(self, 清單參照列: Iterable[str]) -> tuple[str, ...]:
        """找出檔案系統已發現但資料庫沒有收據的清單。

        參數：``清單參照列`` 是檔案系統探索所得 iterable。
        回傳：去重並按字串排序的孤兒清單參照 tuple。
        例外：輸入、超限、迭代或 SQLite 異常皆拋出固定 ``套件收據錯誤``。
        副作用：最多耗用上限加一項，並對去重後參照逐一查詢目前交易；不修改資料庫。
        """
        try:
            唯一參照: set[str] = set()
            for 索引, 參照 in enumerate(清單參照列):
                if 索引 >= _最大清單參照數 or not _合法清單參照(參照):
                    _拒絕收據()
                唯一參照.add(參照)
            結果: list[str] = []
            for 參照 in sorted(唯一參照):
                資料列 = self.連線.execute(
                    "SELECT 1 FROM published_skill_bundles WHERE manifest_reference=?", (參照,)
                ).fetchone()
                if 資料列 is None:
                    結果.append(參照)
            return tuple(結果)
        except _控制流程例外:
            raise
        except 套件收據錯誤:
            raise
        except BaseException:
            _拒絕收據()

    def 查詢待協調收據(self) -> tuple[套件資料庫收據, ...]:
        """依發布順序列出尚未完成檔案系統協調的收據。

        參數：無。回傳：狀態仍為 ``published`` 且無協調時間的有界收據 tuple。
        例外：SQLite、資料列或結果超限異常皆拋出固定 ``套件收據錯誤``。
        副作用：讀取目前交易可見資料，不修改、提交或關閉連線。
        """
        try:
            資料列 = self.連線.execute(
                "SELECT bundle_id,version_id,manifest_reference,manifest_digest,bundle_hash,total_bytes,"
                "state,published_at,reconciled_at FROM published_skill_bundles "
                "WHERE state='published' AND reconciled_at IS NULL ORDER BY published_at,bundle_id "
                "LIMIT ?",
                (_最大清單參照數 + 1,),
            )
            結果: list[套件資料庫收據] = []
            for 資料 in 資料列:
                if len(結果) >= _最大清單參照數:
                    _拒絕收據()
                結果.append(_重建資料列(資料))
            return tuple(結果)
        except _控制流程例外:
            raise
        except 套件收據錯誤:
            raise
        except BaseException:
            _拒絕收據()
