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
        可信冪等衝突 = 可信不存在 = None
        try:
            if (not isinstance(連線, sqlite3.Connection) or not 連線.in_transaction
                    or 連線.execute("PRAGMA foreign_keys").fetchone() != (1,)):
                raise ValueError
            _驗證識別碼(管理員識別碼)
            _驗證識別碼(冪等鍵)
            _驗證識別碼(端點識別碼)
            _驗證識別碼(呼叫識別碼)
            _驗證識別碼(目標列識別碼)
            驗證遮蔽公開欄位(目標類型, JSON路徑, 原因)
            正規原因 = 原因.strip()
            if not 正規原因 or len(正規原因.encode("utf-8")) > 256:
                raise ValueError
            驗證遮蔽公開欄位(目標類型, JSON路徑, 正規原因)
            正規請求 = {
                "endpoint_id": 端點識別碼,
                "invocation_id": 呼叫識別碼,
                "json_path": JSON路徑,
                "reason": 正規原因,
                "target_row_id": 目標列識別碼,
                "target_type": 目標類型,
            }
            請求指紋 = 計算正規JSON雜湊(正規請求)
            連線.execute(
                "UPDATE redaction_idempotency_commands "
                "SET principal_id=principal_id WHERE 0"
            )
            驗證資料庫結構(連線)
            既有 = 連線.execute(
                f"SELECT {_命令欄位} FROM redaction_idempotency_commands "
                "WHERE principal_id=? AND idempotency_key=?",
                (管理員識別碼, 冪等鍵),
            ).fetchall()
            if 既有:
                if len(既有) != 1:
                    raise ValueError
                命令 = _重建命令(既有[0])
                if not _是相同請求(命令, 請求指紋, 正規請求):
                    可信冪等衝突 = 遮蔽命令冪等衝突(_固定衝突)
                    raise 可信冪等衝突 from None
                return 命令

            try:
                _驗證目標(連線, 端點識別碼, 呼叫識別碼, 目標類型, 目標列識別碼)
            except 遮蔽命令目標不存在 as 捕捉不存在:
                可信不存在 = 捕捉不存在
                捕捉不存在 = None
                raise 可信不存在 from None
            遮蔽識別碼 = self._遮蔽識別碼工廠()
            稽核事件識別碼 = self._稽核事件識別碼工廠()
            請求識別碼 = self._請求識別碼工廠()
            首次建立時間 = self._時鐘()
            for 識別碼 in (遮蔽識別碼, 稽核事件識別碼, 請求識別碼):
                _驗證識別碼(識別碼)
            if (type(首次建立時間) not in (int, float)
                    or not math.isfinite(首次建立時間)
                    or not 0 <= 首次建立時間 <= 253_402_300_799):
                raise ValueError
            值 = (
                管理員識別碼,
                冪等鍵,
                請求指紋,
                遮蔽識別碼,
                稽核事件識別碼,
                請求識別碼,
                端點識別碼,
                呼叫識別碼,
                目標類型,
                目標列識別碼,
                JSON路徑,
                正規原因,
                float(首次建立時間),
            )
            游標 = 連線.execute(
                f"INSERT INTO redaction_idempotency_commands({_命令欄位}) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                值,
            )
            if 游標.rowcount != 1:
                raise sqlite3.DatabaseError
            已寫入 = 連線.execute(
                f"SELECT {_命令欄位} FROM redaction_idempotency_commands "
                "WHERE principal_id=? AND idempotency_key=?",
                (管理員識別碼, 冪等鍵),
            ).fetchall()
            if 已寫入 != [值]:
                raise sqlite3.DatabaseError
            return _重建命令(值)
        except (遮蔽命令冪等衝突, 遮蔽命令目標不存在) as 捕捉語意:
            if 捕捉語意 is 可信冪等衝突 or 捕捉語意 is 可信不存在:
                raise
            raise 遮蔽命令錯誤(_固定錯誤) from None
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
        命令.請求指紋 == 指紋
        and 命令.端點識別碼 == 請求["endpoint_id"]
        and 命令.呼叫識別碼 == 請求["invocation_id"]
        and 命令.JSON路徑 == 請求["json_path"]
        and 命令.原因 == 請求["reason"]
        and 命令.目標列識別碼 == 請求["target_row_id"]
        and 命令.目標類型 == 請求["target_type"]
    )
