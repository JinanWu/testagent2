"""GOV SQLite 端點觀測查詢 provider；不提供 HTTP route。"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation, localcontext
from datetime import date, timedelta
from types import BuiltinFunctionType, FunctionType
import math
import re
import sys
import time
from typing import Any, Callable

from .查詢投影 import (
    _控制流程, _安全識別碼, _清理控制鏈, _清理資源操作, _解析可空物件,
    _讀取驗證遮蔽列, _重拋控制, _開啟唯讀快照, _預檢遮蔽中繼, _驗證路徑與結構,
)
from .觀測契約 import (
    安全錯誤排行, 定價版本成本, 延遲摘要, 指標查詢成功, 指標查詢結果, 每日端點指標, 用量摘要,
    端點不可見結果, 端點指標, 診斷查詢成功, 診斷查詢結果, 觀測視窗,
)
from .觀測診斷 import 列出安全診斷
from ..資料庫結構契約 import 驗證資料庫結構

_固定錯誤 = "端點觀測不可取得"
_最大計數 = 2**63 - 1
_終態 = frozenset(("succeeded", "failed", "rate_limited", "invalid_api_key"))
_錯誤態 = frozenset(("failed", "rate_limited", "invalid_api_key"))
_用量欄位 = frozenset(("input_tokens", "output_tokens", "total_tokens", "estimated_cost_usd"))
_持久成本格式 = re.compile(r"(?:0|[1-9][0-9]{0,17})(?:\.[0-9]{0,27}[1-9])?\Z")
_安全錯誤碼格式 = re.compile(r"[a-z][a-z0-9_.-]{0,127}\Z")
_最大頁位元組 = 1_048_576
_最大頁列 = 4096
_最大遮蔽列 = 4096


class 端點觀測查詢錯誤(RuntimeError):
    """資料庫、schema、cursor 或 persisted row 無法安全驗證的固定錯誤。"""


class SQLite端點觀測查詢服務:
    """在一個 owner/admin scoped read transaction 產生 GOV 觀測結果。"""

    __slots__ = ("_path", "_clock", "_cursor_key")

    def __init__(self, 資料庫路徑: str, *, 時鐘: Callable[[], float] = time.time,
                 游標簽章金鑰: bytes) -> None:
        """捕捉資料庫路徑、可信 clock 與至少 256-bit 的 DB-external cursor key。"""
        if (type(資料庫路徑) is not str or not 資料庫路徑
                or type(時鐘) not in (FunctionType, BuiltinFunctionType)
                or type(游標簽章金鑰) is not bytes or len(游標簽章金鑰) < 32):
            raise 端點觀測查詢錯誤(_固定錯誤) from None
        self._path = 資料庫路徑
        self._clock = 時鐘
        self._cursor_key = bytes(游標簽章金鑰)

    def 讀取端點指標(self, *, 擁有者使用者識別碼: str, 是否管理者: bool,
                 端點識別碼: str, 視窗秒數: int) -> 指標查詢結果:
        """同一 read transaction 內完成 authority gate 與 exact aggregate。"""
        連線 = 游標 = 列 = 結果 = None
        路徑 = self._path
        時鐘 = self._clock
        已開始 = 失敗 = False
        控制 = None
        try:
            if (not _安全識別碼(擁有者使用者識別碼) or type(是否管理者) is not bool
                    or not _安全識別碼(端點識別碼) or type(視窗秒數) is not int
                    or not 1 <= 視窗秒數 <= 2_592_000):
                raise ValueError
            結束 = 時鐘()
            if type(結束) not in (int, float) or not math.isfinite(結束) or 結束 < 視窗秒數:
                raise ValueError
            結束 = float(結束)
            開始 = 結束 - 視窗秒數
            連線 = _開啟唯讀快照(路徑)
            連線.execute("BEGIN")
            已開始 = True
            _驗證路徑與結構(連線, 路徑)
            驗證資料庫結構(連線)
            游標 = 連線.execute(
                "SELECT id FROM published_endpoints WHERE id=? AND (?=1 OR owner_user_id=?)",
                (端點識別碼, int(是否管理者), 擁有者使用者識別碼),
            )
            列 = 游標.fetchone()
            if 游標.fetchone() is not None:
                raise ValueError
            游標.close()
            游標 = None
            if 列 is None:
                結果 = 端點不可見結果()
            elif type(列) is not tuple or 列 != (端點識別碼,):
                raise ValueError
            else:
                資料列 = _讀取有界用量列(連線, 端點識別碼, 開始, 結束)
                結果 = 指標查詢成功(_建立指標(端點識別碼, 開始, 結束, 資料列))
            連線.commit()
            已開始 = False
        except _控制流程 as 捕捉控制:
            _清理控制鏈(捕捉控制)
            控制 = 捕捉控制
            捕捉控制 = None
        except BaseException:
            失敗 = True
        if 游標 is not None:
            清理控制 = _清理資源操作(游標, "close")
            if 控制 is None and 清理控制:
                控制 = 清理控制.pop()
        if 連線 is not None and 已開始:
            清理控制 = _清理資源操作(連線, "rollback")
            if 控制 is None and 清理控制:
                控制 = 清理控制.pop()
        if 連線 is not None:
            清理控制 = _清理資源操作(連線, "close")
            if 控制 is None and 清理控制:
                控制 = 清理控制.pop()
        self = 擁有者使用者識別碼 = 是否管理者 = 端點識別碼 = 視窗秒數 = None
        連線 = 游標 = 列 = 路徑 = 時鐘 = 資料列 = 清理控制 = None
        開始 = 結束 = None
        if 控制 is not None:
            控制盒 = [控制]
            控制 = 結果 = None
            _重拋控制(控制盒.pop())
        if 失敗 or type(結果) not in (指標查詢成功, 端點不可見結果):
            結果 = None
            raise 端點觀測查詢錯誤(_固定錯誤) from None
        return 結果

    def 列出端點診斷(self, *, 擁有者使用者識別碼: str, 是否管理者: bool,
                 端點識別碼: str, 視窗秒數: int, 數量上限: int,
                 游標: str | None) -> 診斷查詢結果:
        """委派單一 authoritative paginated safe-diagnostics operation。"""
        結果 = 控制 = None
        失敗 = False
        路徑, 時鐘, 金鑰 = self._path, self._clock, self._cursor_key
        try:
            結果 = 列出安全診斷(
                路徑, 時鐘, 金鑰, 擁有者使用者識別碼, 是否管理者,
                端點識別碼, 視窗秒數, 數量上限, 游標,
            )
        except _控制流程 as 捕捉控制:
            _清理控制鏈(捕捉控制)
            控制 = 捕捉控制
            捕捉控制 = None
        except BaseException:
            失敗 = True
        self = 路徑 = 時鐘 = 金鑰 = 擁有者使用者識別碼 = 是否管理者 = None
        端點識別碼 = 視窗秒數 = 數量上限 = 游標 = None
        if 控制 is not None:
            控制盒 = [控制]
            控制 = 結果 = None
            _重拋控制(控制盒.pop())
        if 失敗 or type(結果) not in (診斷查詢成功, 端點不可見結果):
            結果 = None
            raise 端點觀測查詢錯誤(_固定錯誤) from None
        return 結果


def _建立指標(端點識別碼: str, 開始: float, 結束: float,
          資料列: tuple[tuple[Any, ...], ...]) -> 端點指標:
    """驗證 SQLite dynamic rows 並凍結 nearest-rank aggregate。"""
    呼叫數 = 終態數 = 錯誤數 = 用量數 = 輸入數 = 輸出數 = 0
    延遲們: list[float] = []
    成本們: dict[str, Decimal] = {}
    每日們: dict[str, list[Any]] = {}
    錯誤碼們: dict[str, int] = {}
    with localcontext() as 小數環境:
        小數環境.prec = 64
        總成本 = Decimal(0)
        for 列 in 資料列:
            if type(列) is not tuple or len(列) != 7:
                raise ValueError
            建立時間, 狀態, 延遲, 用量文字, 定價版本, 安全錯誤碼, 錯誤已遮蔽 = 列
            if type(建立時間) not in (int, float) or not math.isfinite(建立時間) or 建立時間 < 0:
                raise ValueError
            try:
                日期 = (date(1970, 1, 1) + timedelta(days=math.floor(建立時間 / 86400))).isoformat()
            except (OverflowError, ValueError):
                raise ValueError from None
            if type(狀態) is not str or 狀態 not in _終態 | {"pending", "running"}:
                raise ValueError
            if (安全錯誤碼 is not None and (type(安全錯誤碼) is not str
                    or not _安全錯誤碼格式.fullmatch(安全錯誤碼))):
                raise ValueError
            if type(錯誤已遮蔽) is not int or 錯誤已遮蔽 not in (0, 1):
                raise ValueError
            呼叫數 += 1
            終態數 += int(狀態 in _終態)
            錯誤數 += int(狀態 in _錯誤態)
            每日 = 每日們.setdefault(日期, [0, 0, 0, 0, Decimal(0)])
            每日[0] += 1
            每日[1] += int(狀態 in _終態)
            每日[2] += int(狀態 in _錯誤態)
            if 狀態 in _錯誤態 and 安全錯誤碼 is not None and not 錯誤已遮蔽:
                錯誤碼們[安全錯誤碼] = 錯誤碼們.get(安全錯誤碼, 0) + 1
            if 延遲 is not None:
                if type(延遲) not in (int, float) or not math.isfinite(延遲) or 延遲 < 0:
                    raise ValueError
                延遲們.append(float(延遲))
            if 用量文字 is None:
                if 定價版本 is not None:
                    raise ValueError
                continue
            用量 = _解析可空物件(用量文字)
            if set(用量) != _用量欄位 or type(定價版本) is not str:
                raise ValueError
            輸入, 輸出, 總數 = (用量[鍵] for 鍵 in ("input_tokens", "output_tokens", "total_tokens"))
            if any(type(值) is not int or not 0 <= 值 <= _最大計數 for 值 in (輸入, 輸出, 總數)):
                raise ValueError
            if 輸入 + 輸出 != 總數:
                raise ValueError
            try:
                成本文字 = 用量["estimated_cost_usd"]
                if type(成本文字) is not str or not _持久成本格式.fullmatch(成本文字):
                    raise ValueError
                小數 = Decimal(成本文字)
            except (InvalidOperation, TypeError, ValueError):
                raise ValueError from None
            用量數 += 1
            輸入數 += 輸入
            輸出數 += 輸出
            總成本 += 小數
            每日[3] += 總數
            每日[4] += 小數
            成本們[定價版本] = 成本們.get(定價版本, Decimal(0)) + 小數
            if max(呼叫數, 終態數, 錯誤數, 用量數, 輸入數, 輸出數, 輸入數 + 輸出數) > _最大計數:
                raise ValueError
    延遲們.sort()
    樣本數 = len(延遲們)
    if 樣本數:
        平均 = math.fsum(延遲們) / 樣本數
        中位 = 延遲們[math.ceil(樣本數 * 0.50) - 1]
        百分之九十五 = 延遲們[math.ceil(樣本數 * 0.95) - 1]
        最大 = 延遲們[-1]
    else:
        平均 = 中位 = 百分之九十五 = 最大 = None
    成本分項 = tuple(定價版本成本(版本, _正規小數(成本們[版本])) for 版本 in sorted(成本們))
    每日分項 = tuple(
        每日端點指標(日期, 值[0], 值[1], 值[2], 值[3], _正規小數(值[4]))
        for 日期, 值 in sorted(每日們.items())
    )
    錯誤排行 = tuple(
        安全錯誤排行(錯誤碼, 數量)
        for 錯誤碼, 數量 in sorted(錯誤碼們.items(), key=lambda 項: (-項[1], 項[0]))[:10]
    )
    return 端點指標(
        端點識別碼, 觀測視窗(開始, 結束), 呼叫數, 終態數, 錯誤數,
        0.0 if 終態數 == 0 else 錯誤數 / 終態數,
        延遲摘要(樣本數, 平均, 中位, 百分之九十五, 最大),
        用量摘要(用量數, 輸入數, 輸出數, 輸入數 + 輸出數), _正規小數(總成本), 成本分項,
        每日分項, 錯誤排行,
    )


def _正規小數(值: Decimal) -> str:
    """輸出 non-exponent canonical nonnegative decimal。"""
    文字 = format(值, "f")
    if "." in 文字:
        文字 = 文字.rstrip("0").rstrip(".")
    return 文字 or "0"


def _讀取有界用量列(連線: Any, 端點: str, 開始: float,
             結束: float) -> tuple[tuple[Any, ...], ...]:
    """先驗 usage與redaction ledger metadata，再按exact key取得payload。"""
    游標 = None
    try:
        游標 = 連線.execute(
            "SELECT COUNT(*),COUNT(usage_json),COALESCE(SUM(length(CAST(usage_json AS BLOB))),0),"
            "COALESCE(SUM(CASE WHEN typeof(usage_json) IN ('text','null') THEN 0 ELSE 1 END),0) "
            "FROM endpoint_invocations WHERE endpoint_id=? AND created_at>=? AND created_at<?",
            (端點, 開始, 結束),
        )
        聚合 = 游標.fetchone()
        if 游標.fetchone() is not None:
            raise ValueError
        _關閉游標(游標); 游標 = None
        if (type(聚合) is not tuple or len(聚合) != 4
                or any(type(值) is not int or 值 < 0 for 值 in 聚合)
                or 聚合[0] > _最大頁列 or 聚合[1] > 聚合[0]
                or 聚合[2] > _最大頁位元組 or 聚合[3] != 0):
            raise ValueError
        游標 = 連線.execute(
            "SELECT i.id,i.created_at,i.status,i.latency_ms,i.pricing_version,typeof(i.usage_json),"
            "length(CAST(i.usage_json AS BLOB)),s.error_code "
            "FROM endpoint_invocations i LEFT JOIN endpoint_invocation_safe_errors s ON s.invocation_id=i.id "
            "WHERE i.endpoint_id=? AND i.created_at>=? AND i.created_at<? ORDER BY i.id",
            (端點, 開始, 結束),
        )
        中繼列 = []
        while True:
            列 = 游標.fetchone()
            if 列 is None:
                break
            if type(列) is not tuple or len(列) != 8 or len(中繼列) >= _最大頁列:
                raise ValueError
            識別碼, 建立時間, 狀態, 延遲, 版本, 類型, 長度, 安全錯誤碼 = 列
            if (not _安全識別碼(識別碼)
                    or type(建立時間) not in (int, float) or not math.isfinite(建立時間) or 建立時間 < 0
                    or type(狀態) is not str
                    or 狀態 not in _終態 | {"pending", "running"}
                    or (延遲 is not None and (type(延遲) not in (int, float)
                        or not math.isfinite(延遲) or 延遲 < 0))
                    or type(類型) is not str
                    or (安全錯誤碼 is not None and (type(安全錯誤碼) is not str
                        or not _安全錯誤碼格式.fullmatch(安全錯誤碼)))):
                raise ValueError
            if 類型 == "null":
                if 長度 is not None or 版本 is not None:
                    raise ValueError
            elif (類型 != "text" or type(長度) is not int or 長度 < 0
                  or type(版本) is not str):
                raise ValueError
            中繼列.append(列)
        _關閉游標(游標); 游標 = None
        if len(中繼列) != 聚合[0] or sum(列[6] or 0 for 列 in 中繼列) != 聚合[2]:
            raise ValueError
        驗證列 = []
        遮蔽預算 = [0, 0]
        遮蔽總數 = 0
        for 中繼 in 中繼列:
            遮蔽中繼 = _預檢遮蔽中繼(連線, 中繼[0], 遮蔽預算)
            遮蔽總數 += len(遮蔽中繼)
            if 遮蔽總數 > _最大遮蔽列:
                raise ValueError
            遮蔽列 = _讀取驗證遮蔽列(連線, 中繼[0], 端點, 遮蔽中繼)
            驗證列.append((*中繼, int(any(項[2] == "error" for 項 in 遮蔽列))))
        結果 = []
        for 中繼 in 驗證列:
            游標 = 連線.execute(
                "SELECT i.id,i.created_at,i.status,i.latency_ms,i.pricing_version,typeof(i.usage_json),"
                "length(CAST(i.usage_json AS BLOB)),s.error_code,i.usage_json FROM endpoint_invocations i "
                "LEFT JOIN endpoint_invocation_safe_errors s ON s.invocation_id=i.id "
                "WHERE i.endpoint_id=? AND i.id=?", (端點, 中繼[0]),
            )
            列 = 游標.fetchone()
            if 游標.fetchone() is not None or type(列) is not tuple or len(列) != 9 or 列[:8] != 中繼[:8]:
                raise ValueError
            結果.append((列[1], 列[2], 列[3], 列[8], 列[4], 列[7], 中繼[8]))
            _關閉游標(游標); 游標 = None
        return tuple(結果)
    finally:
        if 游標 is not None:
            _關閉游標(游標, 保留主要控制=isinstance(sys.exception(), _控制流程))


def _關閉游標(游標: Any, *, 保留主要控制: bool = False) -> None:
    """Best-effort close且只重拋不可被ordinary cleanup壓掉的控制流程。"""
    控制盒 = _清理資源操作(游標, "close")
    if 控制盒 and not 保留主要控制:
        _重拋控制(控制盒.pop())
