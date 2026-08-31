"""以獨立 SQLite 領域計數無效憑證的權威來源 IP 與端點 slug。

參數／欄位：不適用；本模組定義來源節流資料型別、界限與 SQLite 操作。
回傳：不適用；各節流操作的回傳契約由其文件字串分別說明。
例外：匯入相依模組失敗時原樣傳出匯入例外。
副作用：匯入時只定義型別、常數與函式，不開啟或修改資料庫。
"""

from dataclasses import dataclass
import hashlib
import ipaddress
import math
import os
from pathlib import Path
import re
import sqlite3
import stat
from typing import cast

from ..資料庫結構契約 import 遷移帳本 as _預期帳本

最大來源失敗上限 = 10_000
最大來源視窗秒數 = 86_400
最大安全時間戳記 = 253_402_300_799
最大SQLite整數 = 9_223_372_036_854_775_807
_控制流程例外 = (KeyboardInterrupt, SystemExit, GeneratorExit)
_安全端點slug = re.compile(r"[A-Za-z0-9][A-Za-z0-9._~-]{0,127}").fullmatch
_連接SQLite = sqlite3.connect
_預期欄位 = [
    (0, "client_ip", "TEXT", 1, None, 1),
    (1, "endpoint_slug", "TEXT", 1, None, 2),
    (2, "window_start", "INTEGER", 1, None, 3),
    (3, "failure_count", "INTEGER", 1, None, 0),
    (4, "updated_at", "REAL", 1, None, 0),
]
_預期索引欄位 = [(0, 2, "window_start")]
_預期結構雜湊 = [
    ("index", "idx_auth_failure_rate_counters_window_start", "auth_failure_rate_counters",
     "09045a482812152a0e76fc24d42f8efde0f5ac4b0538d03049ebaf58b69705f2"),
    ("table", "auth_failure_rate_counters", "auth_failure_rate_counters",
     "9563e50d8fbbad4f3423ba2f31f83e2399b62abd18c965164554160a87c940e3"),
]
_增加語句 = """INSERT INTO auth_failure_rate_counters(
client_ip,endpoint_slug,window_start,failure_count,updated_at) VALUES(?,?,?,1,?)
ON CONFLICT(client_ip,endpoint_slug,window_start) DO UPDATE SET
failure_count=failure_count+1,updated_at=excluded.updated_at
WHERE typeof(failure_count)='integer' AND failure_count>=0
AND failure_count<9223372036854775807 RETURNING failure_count"""


class 來源節流錯誤(RuntimeError):
    """表示來源驗證失敗計數無法安全且完整地提交。"""


@dataclass(frozen=True, slots=True)
class 來源驗證失敗節流決策:
    """每次無效金鑰嘗試增加後的 transport-neutral 固定視窗決策。"""

    計數: int
    上限: int
    視窗開始秒: int
    視窗結束秒: int
    已超限: bool
    重試秒數: int | None

    def __post_init__(self) -> None:
        """驗證決策欄位與超限、重試語意完全一致。"""
        if (
            type(self) is not 來源驗證失敗節流決策
            or type(self.計數) is not int or not 1 <= self.計數 <= 最大SQLite整數
            or type(self.上限) is not int or not 1 <= self.上限 <= 最大來源失敗上限
            or type(self.視窗開始秒) is not int or self.視窗開始秒 < 0
            or type(self.視窗結束秒) is not int or self.視窗結束秒 <= self.視窗開始秒
            or type(self.已超限) is not bool or self.已超限 != (self.計數 > self.上限)
            or (self.已超限 and (type(self.重試秒數) is not int
                or not 1 <= cast(int, self.重試秒數) <= self.視窗結束秒 - self.視窗開始秒))
            or (not self.已超限 and self.重試秒數 is not None)
        ):
            raise 來源節流錯誤("來源節流失敗") from None

    def __init_subclass__(cls, **kwargs: object) -> None:
        """禁止以子類偽造受信任的節流決策。"""
        del cls, kwargs
        raise TypeError("來源驗證失敗節流決策不可被繼承")


class 來源驗證失敗節流器:
    """只在呼叫者已將憑證分類為無效後，原子記錄一次失敗。"""

    __slots__ = ("_資料庫路徑",)

    def __init__(self, 資料庫路徑: str | Path) -> None:
        """保存資料庫路徑；每次計數仍重新釘選檔案與結構。"""
        if type(資料庫路徑) is str:
            路徑 = Path(資料庫路徑)
        elif type(資料庫路徑) is type(Path()):
            路徑 = 資料庫路徑
        else:
            raise 來源節流錯誤("來源節流失敗") from None
        object.__setattr__(self, "_資料庫路徑", 路徑)

    def 記錄失敗(
        self, 用戶端IP: object, 端點slug: object, 時間戳記: object,
        上限: object, 視窗秒數: object,
    ) -> 來源驗證失敗節流決策:
        """每個 invalid-key attempt 都增加；count > limit 才超限，等於上限仍由 caller 回 401。"""
        try:
            return 記錄來源驗證失敗(self._資料庫路徑, 用戶端IP, 端點slug, 時間戳記, 上限, 視窗秒數)
        except BaseException:
            del self, 用戶端IP, 端點slug, 時間戳記, 上限, 視窗秒數
            raise


def _正規輸入(用戶端IP: object, 端點slug: object, 時間戳記: object,
          上限: object, 視窗秒數: object) -> tuple[str, str, int | float, int, int, int, int]:
    """正規化exact來源、slug、時間及固定視窗設定。"""
    位址 = 正規IP = 正規slug = 正規時間 = 正規上限 = 正規視窗 = None
    開始 = 結束 = None
    try:
        if type(用戶端IP) is not str or not 2 <= len(用戶端IP) <= 45 or "%" in 用戶端IP:
            raise ValueError
        位址 = ipaddress.ip_address(用戶端IP)
        if type(位址) is ipaddress.IPv6Address and 位址.ipv4_mapped is not None:
            raise ValueError
        正規IP = 位址.compressed
        if 正規IP != 用戶端IP:
            raise ValueError
        if type(端點slug) is not str or _安全端點slug(端點slug) is None:
            raise ValueError
        正規slug = str(端點slug)
        if type(上限) is not int or not 1 <= 上限 <= 最大來源失敗上限:
            raise ValueError
        正規上限 = int(上限)
        if type(視窗秒數) is not int or not 1 <= 視窗秒數 <= 最大來源視窗秒數:
            raise ValueError
        正規視窗 = int(視窗秒數)
        if type(時間戳記) is int:
            if not 0 <= 時間戳記 <= 最大安全時間戳記:
                raise ValueError
            正規時間 = int(時間戳記)
        elif type(時間戳記) is float and math.isfinite(時間戳記) and 0 <= 時間戳記 <= 最大安全時間戳記:
            正規時間 = float(時間戳記)
        else:
            raise ValueError
        開始 = math.floor(正規時間 / 正規視窗) * 正規視窗
        結束 = 開始 + 正規視窗
        return 正規IP, 正規slug, 正規時間, 正規上限, 正規視窗, 開始, 結束
    finally:
        用戶端IP = 端點slug = 時間戳記 = 上限 = 視窗秒數 = None
        位址 = 正規IP = 正規slug = 正規時間 = 正規上限 = 正規視窗 = None
        開始 = 結束 = None


def _開啟既有資料庫(路徑: Path):
    """以讀寫模式開啟既有regular SQLite並重新核對inode與外鍵。"""
    連線 = None
    狀態 = 真實路徑 = 實際檔 = 連線狀態 = None
    主要失敗 = False
    控制盒: list[BaseException] = []
    try:
        if not 路徑.name:
            raise ValueError
        狀態 = os.lstat(路徑)
        if stat.S_ISLNK(狀態.st_mode) or not stat.S_ISREG(狀態.st_mode) or 狀態.st_size <= 0:
            raise ValueError
        真實路徑 = 路徑.resolve(strict=True)
        連線 = _連接SQLite(真實路徑.as_uri() + "?mode=rw", uri=True, timeout=5.0, isolation_level=None)
        實際檔 = 連線.execute("PRAGMA database_list").fetchone()
        連線狀態 = os.stat(實際檔[2]) if type(實際檔) is tuple and len(實際檔) == 3 else None
        if 連線狀態 is None or (連線狀態.st_dev, 連線狀態.st_ino) != (狀態.st_dev, 狀態.st_ino):
            raise ValueError
        連線.execute("PRAGMA foreign_keys=ON")
        if 連線.execute("PRAGMA foreign_keys").fetchone() != (1,):
            raise ValueError
    except _控制流程例外 as 錯誤:
        _清除控制流程(錯誤)
        控制盒.append(錯誤)
        錯誤 = None
    except BaseException:
        主要失敗 = True
    if not 主要失敗 and not 控制盒:
        return 連線
    if 連線 is not None:
        try:
            連線.close()
        except _控制流程例外 as 錯誤:
            _清除控制流程(錯誤)
            if not 控制盒:
                控制盒.append(錯誤)
            錯誤 = None
        except BaseException:
            pass
    路徑 = 狀態 = 真實路徑 = 實際檔 = 連線狀態 = 連線 = None
    if 控制盒:
        raise 控制盒.pop()
    raise ValueError


def _驗證有界結構盤點(連線) -> None:
    """先驗證文字型別與位元組預算，再物化 expected+1 筆結構內容。"""
    欄位中繼 = 連線.execute(
        "SELECT cid,typeof(name),length(CAST(name AS BLOB)),typeof(type),"
        "length(CAST(type AS BLOB)),\"notnull\",typeof(dflt_value),"
        "length(CAST(dflt_value AS BLOB)),pk FROM pragma_table_info('auth_failure_rate_counters') "
        "ORDER BY cid LIMIT 6"
    ).fetchall()
    if (type(欄位中繼) is not list or len(欄位中繼) != 5
            or any(type(列) is not tuple or len(列) != 9 or 列[1] != "text" or 列[3] != "text"
                   or type(列[2]) is not int or not 1 <= 列[2] <= 64
                   or type(列[4]) is not int or not 1 <= 列[4] <= 16
                   or 列[6:] not in (("null", None, 列[8]),) for 列 in 欄位中繼)
            or sum(列[2] + 列[4] for 列 in 欄位中繼) > 256):
        raise ValueError
    欄位 = 連線.execute(
        "SELECT cid,name,type,\"notnull\",dflt_value,pk FROM pragma_table_info('auth_failure_rate_counters') "
        "ORDER BY cid LIMIT 6"
    ).fetchall()
    if 欄位 != _預期欄位:
        raise ValueError
    索引中繼 = 連線.execute(
        "SELECT seq,typeof(name),length(CAST(name AS BLOB)),\"unique\",typeof(origin),"
        "length(CAST(origin AS BLOB)),partial FROM pragma_index_list('auth_failure_rate_counters') "
        "ORDER BY seq LIMIT 3"
    ).fetchall()
    if (type(索引中繼) is not list or len(索引中繼) != 2
            or any(type(列) is not tuple or len(列) != 7 or 列[1] != "text" or 列[4] != "text"
                   or type(列[2]) is not int or not 1 <= 列[2] <= 128
                   or type(列[5]) is not int or not 1 <= 列[5] <= 8 for 列 in 索引中繼)
            or sum(列[2] + 列[5] for 列 in 索引中繼) > 256):
        raise ValueError
    索引 = 連線.execute(
        "SELECT seq,name,\"unique\",origin,partial FROM pragma_index_list('auth_failure_rate_counters') "
        "ORDER BY seq LIMIT 3"
    ).fetchall()
    if 索引 != [(0, "idx_auth_failure_rate_counters_window_start", 0, "c", 0),
              (1, "sqlite_autoindex_auth_failure_rate_counters_1", 1, "pk", 0)]:
        raise ValueError
    索引欄位中繼 = 連線.execute(
        "SELECT seqno,cid,typeof(name),length(CAST(name AS BLOB)) "
        "FROM pragma_index_info('idx_auth_failure_rate_counters_window_start') ORDER BY seqno LIMIT 2"
    ).fetchall()
    if 索引欄位中繼 != [(0, 2, "text", len("window_start"))]:
        raise ValueError
    if 連線.execute(
        "SELECT seqno,cid,name FROM pragma_index_info('idx_auth_failure_rate_counters_window_start') "
        "ORDER BY seqno LIMIT 2"
    ).fetchall() != _預期索引欄位:
        raise ValueError


def _驗證結構(連線) -> None:
    """在計數交易內有界驗證遷移帳本與來源節流結構。

    參數：``連線`` 是已開始計數交易且由呼叫端管理的 SQLite 連線。
    回傳：帳本、資料表、索引與結構雜湊皆符合時回傳 ``None``。
    例外：任何中繼資料、列形狀、型別、界限或內容不符時拋出 ``ValueError``；
    SQLite 查詢失敗時原樣傳出其例外。
    副作用：只在既有交易讀取有界結構中繼資料，不提交、回滾或關閉連線。
    """
    遷移紀錄 = 連線.execute(
        "SELECT version,typeof(version),typeof(name),length(CAST(name AS BLOB)),"
        f"typeof(applied_at),applied_at FROM published_api_schema_migrations ORDER BY version LIMIT {len(_預期帳本) + 1}"
    ).fetchall()
    if type(遷移紀錄) is not list or len(遷移紀錄) != len(_預期帳本):
        raise ValueError
    for 索引, 列 in enumerate(遷移紀錄):
        if (type(列) is not tuple or len(列) != 6 or 列[:5] != (索引 + 1, "integer", "text", len(_預期帳本[索引][1].encode()), 列[4])
                or 列[4] not in ("real", "integer") or type(列[5]) not in (int, float)
                or not math.isfinite(列[5]) or 列[5] < 0):
            raise ValueError
    名稱 = 連線.execute(f"SELECT version,name FROM published_api_schema_migrations ORDER BY version LIMIT {len(_預期帳本) + 1}").fetchall()
    if 名稱 != list(_預期帳本):
        raise ValueError
    _驗證有界結構盤點(連線)
    中繼 = 連線.execute(
        "SELECT typeof(type),length(CAST(type AS BLOB)),typeof(name),length(CAST(name AS BLOB)),"
        "typeof(tbl_name),length(CAST(tbl_name AS BLOB)),typeof(sql),length(CAST(sql AS BLOB)) "
        "FROM sqlite_master WHERE name IN ('auth_failure_rate_counters',"
        "'idx_auth_failure_rate_counters_window_start') ORDER BY type,name LIMIT 3"
    ).fetchall()
    if (中繼 != [("text", 5, "text", 43, "text", 26, "text", 102),
               ("text", 5, "text", 26, "text", 26, "text", 815)]
            or sum(列[7] for 列 in 中繼) > 1024):
        raise ValueError
    定義 = 連線.execute(
        "SELECT type,name,tbl_name,sql FROM sqlite_master WHERE name IN "
        "('auth_failure_rate_counters','idx_auth_failure_rate_counters_window_start') "
        "ORDER BY type,name LIMIT 3"
    ).fetchall()
    if (type(定義) is not list or len(定義) != 2
            or [(列[0], 列[1], 列[2], hashlib.sha256(列[3].encode()).hexdigest())
                for 列 in 定義 if type(列) is tuple and len(列) == 4 and type(列[3]) is str]
            != _預期結構雜湊):
        raise ValueError
    觸發器 = 連線.execute(
        "SELECT 1 FROM sqlite_master WHERE type='trigger' AND "
        "(tbl_name='auth_failure_rate_counters' OR name='auth_failure_rate_counters') "
        "ORDER BY name LIMIT 1"
    ).fetchall()
    if 觸發器 != []:
        raise ValueError


def _清除控制流程(錯誤: BaseException) -> None:
    """以BaseException原生descriptor清除控制流程例外鏈。"""
    BaseException.__setattr__(錯誤, "__cause__", None)
    BaseException.__setattr__(錯誤, "__context__", None)
    BaseException.__setattr__(錯誤, "__suppress_context__", True)


def 記錄來源驗證失敗(
    資料庫路徑: object, 用戶端IP: object, 端點slug: object, 時間戳記: object,
    上限: object, 視窗秒數: object,
) -> 來源驗證失敗節流決策:
    """在一個 BEGIN IMMEDIATE／UPSERT／COMMIT 中獨立增加 IP+slug 失敗計數。"""
    連線 = None
    正規 = None
    路徑 = 列 = 超限 = 重試 = None
    決策 = None
    已開始 = False
    已提交 = False
    失敗 = False
    控制盒: list[BaseException] = []
    try:
        正規 = _正規輸入(用戶端IP, 端點slug, 時間戳記, 上限, 視窗秒數)
        路徑 = Path(cast(str | Path, 資料庫路徑)) if type(資料庫路徑) in (str, type(Path())) else None
        if 路徑 is None:
            raise ValueError
        連線 = _開啟既有資料庫(路徑)
        連線.execute("BEGIN IMMEDIATE")
        已開始 = True
        _驗證結構(連線)
        列 = 連線.execute(_增加語句, (正規[0], 正規[1], 正規[5], 正規[2])).fetchone()
        if type(列) is not tuple or len(列) != 1 or type(列[0]) is not int or not 1 <= 列[0] <= 最大SQLite整數:
            raise ValueError
        超限 = 列[0] > 正規[3]
        重試 = max(1, min(正規[4], math.ceil(正規[6] - 正規[2]))) if 超限 else None
        決策 = 來源驗證失敗節流決策(列[0], 正規[3], 正規[5], 正規[6], 超限, 重試)
        連線.commit()
        已提交 = True
    except _控制流程例外 as 錯誤:
        _清除控制流程(錯誤)
        控制盒.append(錯誤)
        錯誤 = None
    except BaseException:
        失敗 = True
    if 連線 is not None:
        if 已開始 and not 已提交:
            try:
                連線.rollback()
            except _控制流程例外 as 錯誤:
                _清除控制流程(錯誤)
                if not 控制盒:
                    控制盒.append(錯誤)
                錯誤 = None
            except BaseException:
                失敗 = True
        try:
            連線.close()
        except _控制流程例外 as 錯誤:
            _清除控制流程(錯誤)
            if not 控制盒:
                控制盒.append(錯誤)
            錯誤 = None
        except BaseException:
            if not 已提交:
                失敗 = True
    資料庫路徑 = 用戶端IP = 端點slug = 時間戳記 = 上限 = 視窗秒數 = None
    連線 = 正規 = 路徑 = 列 = 超限 = 重試 = None
    if 控制盒:
        決策 = None
        raise 控制盒.pop()
    if 失敗 or type(決策) is not 來源驗證失敗節流決策:
        決策 = None
        raise 來源節流錯誤("來源節流失敗") from None
    return 決策
