"""A20 server-owned 不可逆遮蔽命令與 caller-owned 冪等 mapping。"""

from __future__ import annotations

import asyncio
import math
import re
import sqlite3
from dataclasses import dataclass
from typing import Callable

from ..嚴格JSON import 計算正規JSON雜湊
from ..資料庫結構契約 import 驗證資料庫結構
from .遮蔽 import 驗證遮蔽公開欄位

_控制流程例外 = (asyncio.CancelledError, KeyboardInterrupt, SystemExit, GeneratorExit)
_安全識別碼 = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
_固定錯誤 = "遮蔽命令無法建立"
_固定衝突 = "遮蔽命令冪等衝突"
_命令欄位 = (
    "principal_id,idempotency_key,request_fingerprint,redaction_id,audit_event_id,request_id,"
    "endpoint_id,invocation_id,target_type,target_row_id,json_path,reason,first_seen_at"
)


class 遮蔽命令錯誤(RuntimeError):
    """命令輸入、schema、transaction 或 server factory 無法安全建立 mapping。"""


class 遮蔽命令冪等衝突(RuntimeError):
    """同一 admin principal 與 idempotency key 已綁定不同 canonical request。"""


class 遮蔽命令目標不存在(RuntimeError):
    """endpoint/invocation/target ownership lookup不成立的固定provenance。"""


@dataclass(frozen=True, slots=True)
class 伺服器遮蔽命令:
    """只含 server identity 與治理 request metadata 的不可變命令。"""

    管理員識別碼: str
    冪等鍵: str
    請求指紋: str
    遮蔽識別碼: str
    稽核事件識別碼: str
    請求識別碼: str
    端點識別碼: str
    呼叫識別碼: str
    目標類型: str
    目標列識別碼: str
    JSON路徑: str
    原因: str
    首次建立時間: float


@dataclass(frozen=True, slots=True)
class 待配置遮蔽命令:
    """prepare階段sealed request；不含payload、hash、server IDs或time。"""
    管理員識別碼: str
    冪等鍵: str
    請求指紋: str
    端點識別碼: str
    呼叫識別碼: str
    目標類型: str
    目標列識別碼: str
    JSON路徑: str
    原因: str
    既有命令: 伺服器遮蔽命令 | None = None


class SQLite遮蔽命令服務:
    """在 caller transaction 取得或建立 stable server-owned 遮蔽命令。"""

    __slots__ = ("_遮蔽識別碼工廠", "_稽核事件識別碼工廠", "_請求識別碼工廠", "_時鐘")

    def __init__(
        self,
        *,
        遮蔽識別碼工廠: Callable[[], str],
        稽核事件識別碼工廠: Callable[[], str],
        請求識別碼工廠: Callable[[], str],
        時鐘: Callable[[], int | float],
    ) -> None:
        """保存四個 server-owned factories，建構期間不執行 I/O。

        參數：三個識別碼工廠與權威時鐘；全部只可由 server composition 注入。
        返回值：無。
        例外：任一 factory 不可呼叫時拋出固定 ``遮蔽命令錯誤``。
        副作用：只保存 callable reference，不開啟資料庫、不配置 identity。
        """
        if not all(callable(工廠) for 工廠 in (
            遮蔽識別碼工廠,
            稽核事件識別碼工廠,
            請求識別碼工廠,
            時鐘,
        )):
            raise 遮蔽命令錯誤(_固定錯誤) from None
        self._遮蔽識別碼工廠 = 遮蔽識別碼工廠
        self._稽核事件識別碼工廠 = 稽核事件識別碼工廠
        self._請求識別碼工廠 = 請求識別碼工廠
        self._時鐘 = 時鐘

    def 取得或建立(
        self,
        連線: sqlite3.Connection,
        *,
        管理員識別碼: str,
        冪等鍵: str,
        端點識別碼: str,
        呼叫識別碼: str,
        目標類型: str,
        目標列識別碼: str,
        JSON路徑: str,
        原因: str,
    ) -> 伺服器遮蔽命令:
        """在既有 caller transaction 建立或回放 principal+key 命令。

        參數：FK-enabled caller transaction、server-authenticated admin principal、bounded key，
            以及不含 authority/internal identity/time/original value 的 canonical request 欄位。
        返回值：首次建立或 exact replay 的同一 ``伺服器遮蔽命令`` identity。
        例外：同 key 不同 request 拋固定冪等衝突；其他普通失敗固定關閉失敗。
        副作用：首次 request 只加入一筆 mapping；不 begin、commit、rollback 或讀取 payload。
        """
        待配置 = SQLite遮蔽命令服務.準備(
            self,
            連線,
            管理員識別碼=管理員識別碼,
            冪等鍵=冪等鍵,
            端點識別碼=端點識別碼,
            呼叫識別碼=呼叫識別碼,
            目標類型=目標類型,
            目標列識別碼=目標列識別碼,
            JSON路徑=JSON路徑,
            原因=原因,
        )
        if 待配置.既有命令 is not None:
            return 待配置.既有命令
        return SQLite遮蔽命令服務.建立(self, 連線, 待配置)

    def 準備(
        self, 連線: sqlite3.Connection, *, 管理員識別碼: str, 冪等鍵: str,
        端點識別碼: str, 呼叫識別碼: str, 目標類型: str,
        目標列識別碼: str, JSON路徑: str, 原因: str,
    ) -> 待配置遮蔽命令:
        """固定request並先判same-key衝突與ownership；fresh不配置或寫入。"""
        可信衝突 = 可信不存在 = None
        try:
            if (not isinstance(連線, sqlite3.Connection) or not 連線.in_transaction
                    or 連線.execute("PRAGMA foreign_keys").fetchone() != (1,)):
                raise ValueError
            for 值 in (管理員識別碼, 冪等鍵, 端點識別碼, 呼叫識別碼, 目標列識別碼):
                _驗證識別碼(值)
            正規原因 = 原因.strip()
            驗證遮蔽公開欄位(目標類型, JSON路徑, 正規原因)
            if not 正規原因 or len(正規原因.encode("utf-8")) > 256:
                raise ValueError
            正規請求 = {"endpoint_id": 端點識別碼, "invocation_id": 呼叫識別碼,
                    "json_path": JSON路徑, "reason": 正規原因,
                    "target_row_id": 目標列識別碼, "target_type": 目標類型}
            指紋 = 計算正規JSON雜湊(正規請求)
            連線.execute("UPDATE redaction_idempotency_commands SET principal_id=principal_id WHERE 0")
            驗證資料庫結構(連線)
            rows = 連線.execute(
                f"SELECT {_命令欄位} FROM redaction_idempotency_commands WHERE principal_id=? AND idempotency_key=?",
                (管理員識別碼, 冪等鍵),
            ).fetchall()
            if rows:
                if len(rows) != 1:
                    raise ValueError
                命令 = _重建命令(rows[0])
                if not _是相同請求(命令, 指紋, 正規請求):
                    可信衝突 = 遮蔽命令冪等衝突(_固定衝突)
                    raise 可信衝突 from None
                return 待配置遮蔽命令(管理員識別碼, 冪等鍵, 指紋, 端點識別碼,
                    呼叫識別碼, 目標類型, 目標列識別碼, JSON路徑, 正規原因, 命令)
            try:
                _驗證目標(連線, 端點識別碼, 呼叫識別碼, 目標類型, 目標列識別碼)
            except 遮蔽命令目標不存在 as 錯誤:
                可信不存在 = 錯誤; 錯誤 = None
                raise 可信不存在 from None
            return 待配置遮蔽命令(管理員識別碼, 冪等鍵, 指紋, 端點識別碼,
                呼叫識別碼, 目標類型, 目標列識別碼, JSON路徑, 正規原因)
        except (遮蔽命令冪等衝突, 遮蔽命令目標不存在) as 錯誤:
            if 錯誤 is 可信衝突 or 錯誤 is 可信不存在:
                raise
            raise 遮蔽命令錯誤(_固定錯誤) from None
        except _控制流程例外:
            raise
        except BaseException:
            raise 遮蔽命令錯誤(_固定錯誤) from None

    def 建立(self, 連線: sqlite3.Connection, pending: 待配置遮蔽命令) -> 伺服器遮蔽命令:
        """payload preflight完成後才配置IDs/time並寫入exact mapping。"""
        try:
            if type(pending) is not 待配置遮蔽命令 or pending.既有命令 is not None or not 連線.in_transaction:
                raise ValueError
            ids = (self._遮蔽識別碼工廠(), self._稽核事件識別碼工廠(), self._請求識別碼工廠())
            時間 = self._時鐘()
            for 值 in ids:
                _驗證識別碼(值)
            if type(時間) not in (int, float) or not math.isfinite(時間) or not 0 <= 時間 <= 253_402_300_799:
                raise ValueError
            值 = (pending.管理員識別碼, pending.冪等鍵, pending.請求指紋,
                ids[0], ids[1], ids[2], pending.端點識別碼, pending.呼叫識別碼,
                pending.目標類型, pending.目標列識別碼, pending.JSON路徑, pending.原因, float(時間))
            if 連線.execute(
                f"INSERT INTO redaction_idempotency_commands({_命令欄位}) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)", 值,
            ).rowcount != 1:
                raise sqlite3.DatabaseError
            if 連線.execute(
                f"SELECT {_命令欄位} FROM redaction_idempotency_commands WHERE principal_id=? AND idempotency_key=?",
                (pending.管理員識別碼, pending.冪等鍵),
            ).fetchall() != [值]:
                raise sqlite3.DatabaseError
            return _重建命令(值)
        except _控制流程例外:
            raise
        except BaseException:
            raise 遮蔽命令錯誤(_固定錯誤) from None


def _驗證識別碼(值: object) -> str:
    """驗證 server/internal 與 idempotency identity 共用的 bounded ASCII grammar。"""
    if type(值) is not str or _安全識別碼.fullmatch(值) is None:
        raise ValueError
    return 值


def _驗證目標(
    連線: sqlite3.Connection,
    端點識別碼: str,
    呼叫識別碼: str,
    目標類型: str,
    目標列識別碼: str,
) -> None:
    """不讀 payload，只驗證 endpoint/invocation/child target ownership。"""
    if 連線.execute(
        "SELECT id,endpoint_id FROM endpoint_invocations WHERE id=?",
        (呼叫識別碼,),
    ).fetchall() != [(呼叫識別碼, 端點識別碼)]:
        raise 遮蔽命令目標不存在("遮蔽目標不存在") from None
    if 目標類型 in ("invocation_input", "metadata", "output", "error"):
        if 目標列識別碼 != 呼叫識別碼:
            raise 遮蔽命令目標不存在("遮蔽目標不存在") from None
        return
    表格 = "run_events" if 目標類型 == "run_event" else "endpoint_tool_calls"
    if 連線.execute(
        f"SELECT id,invocation_id FROM {表格} WHERE id=?",
        (目標列識別碼,),
    ).fetchall() != [(目標列識別碼, 呼叫識別碼)]:
        raise 遮蔽命令目標不存在("遮蔽目標不存在") from None


def _重建命令(列: object) -> 伺服器遮蔽命令:
    """由 untrusted SQLite row 重建 exact typed command 並逐欄重驗。"""
    if type(列) is not tuple or len(列) != 13:
        raise ValueError
    管理員, 冪等鍵, 指紋, 遮蔽ID, 稽核ID, 請求ID, 端點ID, 呼叫ID, 類型, 列ID, 路徑, 原因, 時間 = 列
    for 識別碼 in (管理員, 冪等鍵, 遮蔽ID, 稽核ID, 請求ID, 端點ID, 呼叫ID, 列ID):
        _驗證識別碼(識別碼)
    if (type(指紋) is not str or re.fullmatch(r"[0-9a-f]{64}", 指紋) is None
            or type(時間) not in (int, float) or not math.isfinite(時間)
            or not 0 <= 時間 <= 253_402_300_799):
        raise ValueError
    驗證遮蔽公開欄位(類型, 路徑, 原因)
    if len(原因.encode("utf-8")) > 256 or 原因 != 原因.strip():
        raise ValueError
    return 伺服器遮蔽命令(
        管理員,
        冪等鍵,
        指紋,
        遮蔽ID,
        稽核ID,
        請求ID,
        端點ID,
        呼叫ID,
        類型,
        列ID,
        路徑,
        原因,
        float(時間),
    )


def _是相同請求(命令: 伺服器遮蔽命令, 指紋: str, 請求: dict[str, str]) -> bool:
    """同時比較 digest 與 durable canonical fields，避免只信任 hash equality。"""
    return (
        _命令正規指紋相符(命令)
        and 命令.請求指紋 == 指紋
        and 命令.端點識別碼 == 請求["endpoint_id"]
        and 命令.呼叫識別碼 == 請求["invocation_id"]
        and 命令.JSON路徑 == 請求["json_path"]
        and 命令.原因 == 請求["reason"]
        and 命令.目標列識別碼 == 請求["target_row_id"]
        and 命令.目標類型 == 請求["target_type"]
    )


def _命令正規指紋相符(命令: 伺服器遮蔽命令) -> bool:
    """由durable canonical request fields重算fingerprint，不信任stored digest。"""
    if type(命令) is not 伺服器遮蔽命令:
        return False
    正規請求 = {
        "endpoint_id": 命令.端點識別碼,
        "invocation_id": 命令.呼叫識別碼,
        "json_path": 命令.JSON路徑,
        "reason": 命令.原因,
        "target_row_id": 命令.目標列識別碼,
        "target_type": 命令.目標類型,
    }
    return 命令.請求指紋 == 計算正規JSON雜湊(正規請求)
