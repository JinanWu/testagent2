"""GOV SQLite paginated owner-safe diagnostics implementation。"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import math
from typing import Any, Callable

from .查詢投影 import (
    _控制流程, _安全可空字串, _安全時間, _安全文字, _安全識別碼,
    _扣除JSON長度, _核對投影墓碑, _清理控制鏈, _清理資源操作, _解析可空JSON,
    _讀取有限列, _讀取驗證遮蔽列, _重拋控制, _開啟唯讀快照,
    _驗證路徑與結構,
)
from .遮蔽 import _驗證遮蔽schema
from .觀測契約 import (
    診斷查詢成功, 診斷查詢結果, 診斷用量, 診斷項目, 診斷頁,
    端點不可見結果,
)

_固定錯誤 = "端點觀測不可取得"
_用量欄位 = frozenset(("input_tokens", "output_tokens", "total_tokens", "estimated_cost_usd"))
_最大子列 = 4096


class 診斷查詢錯誤(RuntimeError):
    """Cursor、schema、ledger 或 persisted row 無法安全驗證。"""


def 列出安全診斷(
    路徑: str, 時鐘: Callable[[], float], 金鑰: bytes, 擁有者: str, 管理者: bool,
    端點: str, 視窗秒數: int, 上限: int, 游標文字: str | None,
) -> 診斷查詢結果:
    """在單一 authority-scoped read transaction 建立 deterministic diagnostics page。"""
    連線 = 查詢游標 = 列 = 資料列 = 結果 = None
    參數 = 條件 = 項目們 = 最後 = 下一頁 = 清理控制 = None
    開始 = 結束 = 位置時間 = 位置識別碼 = None
    已開始 = 失敗 = False
    控制 = None
    try:
        if (not _安全識別碼(擁有者) or type(管理者) is not bool or not _安全識別碼(端點)
                or type(視窗秒數) is not int or not 1 <= 視窗秒數 <= 2_592_000
                or type(上限) is not int or not 1 <= 上限 <= 100
                or (游標文字 is not None and (type(游標文字) is not str or not 1 <= len(游標文字) <= 1024))):
            raise ValueError
        if 游標文字 is None:
            結束 = 時鐘()
            if type(結束) not in (int, float) or not math.isfinite(結束) or 結束 < 視窗秒數:
                raise ValueError
            結束 = float(結束)
            開始 = 結束 - 視窗秒數
        else:
            開始, 結束, 位置時間, 位置識別碼 = _解碼游標(金鑰, 游標文字, 端點, 視窗秒數)
        連線 = _開啟唯讀快照(路徑)
        連線.execute("BEGIN")
        已開始 = True
        _驗證路徑與結構(連線, 路徑)
        _驗證遮蔽schema(連線)
        查詢游標 = 連線.execute(
            "SELECT id FROM published_endpoints WHERE id=? AND (?=1 OR owner_user_id=?)",
            (端點, int(管理者), 擁有者),
        )
        列 = 查詢游標.fetchone()
        if 查詢游標.fetchone() is not None:
            raise ValueError
        查詢游標.close(); 查詢游標 = None
        if 列 is None:
            結果 = 端點不可見結果()
        elif type(列) is not tuple or 列 != (端點,):
            raise ValueError
        else:
            條件 = "" if 位置時間 is None else " AND (created_at<? OR (created_at=? AND id<?))"
            參數 = (端點, 開始, 結束) if 位置時間 is None else (
                端點, 開始, 結束, 位置時間, 位置時間, 位置識別碼,
            )
            查詢游標 = 連線.execute(
                "SELECT id,request_id,endpoint_version_id,status,latency_ms,created_at,completed_at,"
                "typeof(error_json),length(CAST(error_json AS BLOB)),"
                "typeof(usage_json),length(CAST(usage_json AS BLOB)),"
                "typeof(input_json),length(CAST(input_json AS BLOB)),"
                "typeof(metadata_json),length(CAST(metadata_json AS BLOB)),"
                "typeof(output_json),length(CAST(output_json AS BLOB)) FROM endpoint_invocations "
                "WHERE endpoint_id=? AND created_at>=? AND created_at<?" + 條件
                + " ORDER BY created_at DESC,id DESC LIMIT ?", (*參數, 上限 + 1),
            )
            資料列 = _讀取有限列(查詢游標, 17)
            查詢游標.close(); 查詢游標 = None
            預算 = [0, 0]
            子中繼 = {}
            子總數 = 0
            for 列 in 資料列:
                _驗證呼叫中繼(列, 預算)
                事件列 = _讀取子中繼(連線, 列[0], True, 預算)
                工具列 = _讀取子中繼(連線, 列[0], False, 預算)
                子總數 += len(事件列) + len(工具列)
                if 子總數 > _最大子列:
                    raise ValueError
                子中繼[列[0]] = (事件列, 工具列)
            項目們 = []
            for 列 in 資料列[:上限]:
                項目們.append(_建立項目(連線, 端點, 列, 子中繼[列[0]], 預算))
            下一頁 = None
            if len(資料列) > 上限:
                最後 = 資料列[上限 - 1]
                下一頁 = _編碼游標(金鑰, 端點, 視窗秒數, 開始, 結束, 最後[5], 最後[0])
            結果 = 診斷查詢成功(診斷頁(tuple(項目們), 下一頁))
        連線.commit(); 已開始 = False
    except _控制流程 as 捕捉控制:
        _清理控制鏈(捕捉控制); 控制 = 捕捉控制; 捕捉控制 = None
    except BaseException:
        失敗 = True
    if 查詢游標 is not None:
        清理控制 = _清理資源操作(查詢游標, "close")
        if 控制 is None and 清理控制: 控制 = 清理控制.pop()
    if 連線 is not None and 已開始:
        清理控制 = _清理資源操作(連線, "rollback")
        if 控制 is None and 清理控制: 控制 = 清理控制.pop()
    if 連線 is not None:
        清理控制 = _清理資源操作(連線, "close")
        if 控制 is None and 清理控制: 控制 = 清理控制.pop()
    路徑 = 時鐘 = 金鑰 = 擁有者 = 管理者 = 端點 = 視窗秒數 = 上限 = 游標文字 = None
    連線 = 查詢游標 = 列 = 資料列 = 參數 = 條件 = 項目們 = 最後 = 下一頁 = 清理控制 = None
    預算 = 子中繼 = 事件列 = 工具列 = 子總數 = None
    開始 = 結束 = 位置時間 = 位置識別碼 = None
    if 控制 is not None:
        控制盒 = [控制]; 控制 = 結果 = None; _重拋控制(控制盒.pop())
    if 失敗 or type(結果) not in (診斷查詢成功, 端點不可見結果):
        結果 = None
        raise 診斷查詢錯誤(_固定錯誤) from None
    return 結果


def _驗證呼叫中繼(列: tuple[Any, ...], 預算: list[int]) -> None:
    """驗證 invocation safe scalars 與五個 JSON storage metadata。"""
    if (not all(_安全識別碼(列[i]) for i in (0, 1, 2)) or type(列[3]) is not str
            or 列[3] not in ("pending", "running", "succeeded", "failed", "rate_limited", "invalid_api_key")
            or not _安全時間(列[4], True) or not _安全時間(列[5])
            or not _安全時間(列[6], True)):
        raise ValueError
    for 索引 in range(7, 17, 2):
        _扣除JSON長度(列[索引], 列[索引 + 1], 預算, 索引 == 11)


def _讀取子中繼(連線: Any, 呼叫: str, 是事件: bool,
           預算: list[int]) -> tuple[tuple[Any, ...], ...]:
    """只讀 bounded child safe scalars/type/length，不選取 payload。"""
    if 是事件:
        SQL = ("SELECT id,invocation_id,sequence_number,event_type,created_at,typeof(payload_json),"
               "length(CAST(payload_json AS BLOB)) FROM run_events "
               "WHERE invocation_id=? ORDER BY sequence_number")
        欄寬 = 7
    else:
        SQL = ("SELECT id,invocation_id,run_event_id,sequence_number,tool_name,outcome,latency_ms,"
               "retry_of_tool_call_id,created_at,typeof(arguments_json),"
               "length(CAST(arguments_json AS BLOB)),typeof(result_json),"
               "length(CAST(result_json AS BLOB)),typeof(error_json),"
               "length(CAST(error_json AS BLOB)) FROM endpoint_tool_calls "
               "WHERE invocation_id=? ORDER BY sequence_number")
        欄寬 = 15
    游標 = 連線.execute(SQL, (呼叫,))
    try:
        資料列 = _讀取有限列(游標, 欄寬)
    finally:
        游標.close()
    for 列 in 資料列:
        if 是事件:
            if (not _安全識別碼(列[0]) or 列[1] != 呼叫 or type(列[1]) is not str
                    or type(列[2]) is not int or 列[2] <= 0 or not _安全文字(列[3], 256)
                    or not _安全時間(列[4])):
                raise ValueError
            _扣除JSON長度(列[5], 列[6], 預算, True)
        else:
            if (not _安全識別碼(列[0]) or 列[1] != 呼叫 or type(列[1]) is not str
                    or not _安全文字(列[2], 可空=True) or type(列[3]) is not int or 列[3] <= 0
                    or not _安全文字(列[4], 256) or type(列[5]) is not str
                    or 列[5] not in ("success", "error") or not _安全時間(列[6], True)
                    or not _安全文字(列[7], 可空=True) or not _安全時間(列[8])):
                raise ValueError
            _扣除JSON長度(列[9], 列[10], 預算, True)
            _扣除JSON長度(列[11], 列[12], 預算)
            _扣除JSON長度(列[13], 列[14], 預算)
            if (列[5] == "success") != (列[11] == "text" and 列[13] == "null"):
                raise ValueError
    return 資料列


def _建立項目(連線: Any, 端點: str, 列: tuple[Any, ...],
          子中繼: tuple[tuple[tuple[Any, ...], ...], tuple[tuple[Any, ...], ...]],
          預算: list[int]) -> 診斷項目:
    """metadata gate 全頁通過後，按 exact authoritative keys 取得並核對 payload。"""
    主列 = _讀取唯一(連線,
        "SELECT id,request_id,endpoint_version_id,status,latency_ms,created_at,completed_at,"
        "typeof(error_json),length(CAST(error_json AS BLOB)),typeof(usage_json),"
        "length(CAST(usage_json AS BLOB)),typeof(input_json),length(CAST(input_json AS BLOB)),"
        "typeof(metadata_json),length(CAST(metadata_json AS BLOB)),typeof(output_json),"
        "length(CAST(output_json AS BLOB)),error_json,usage_json,input_json,metadata_json,output_json "
        "FROM endpoint_invocations WHERE endpoint_id=? AND id=?", (端點, 列[0]), 22)
    if 主列[:17] != 列:
        raise ValueError
    錯誤, 用量, 輸入, 中繼, 輸出 = (_解析可空JSON(值, 預算, False) for 值 in 主列[17:])
    事件列, 工具列 = 子中繼
    事件 = []
    for 中繼項 in 事件列:
        事件項 = _讀取唯一(連線,
            "SELECT id,invocation_id,sequence_number,event_type,created_at,typeof(payload_json),"
            "length(CAST(payload_json AS BLOB)),payload_json FROM run_events "
            "WHERE invocation_id=? AND id=? AND sequence_number=?", (列[0], 中繼項[0], 中繼項[2]), 8)
        if 事件項[:7] != 中繼項:
            raise ValueError
        事件.append({"id": 事件項[0], "payload": _解析可空JSON(事件項[7], 預算, False)})
    工具 = []
    for 中繼項 in 工具列:
        工具項 = _讀取唯一(連線,
            "SELECT id,invocation_id,run_event_id,sequence_number,tool_name,outcome,latency_ms,"
            "retry_of_tool_call_id,created_at,typeof(arguments_json),length(CAST(arguments_json AS BLOB)),"
            "typeof(result_json),length(CAST(result_json AS BLOB)),typeof(error_json),"
            "length(CAST(error_json AS BLOB)),arguments_json,result_json,error_json "
            "FROM endpoint_tool_calls WHERE invocation_id=? AND id=? AND sequence_number=?",
            (列[0], 中繼項[0], 中繼項[3]), 18)
        if 工具項[:15] != 中繼項:
            raise ValueError
        工具.append({"id": 工具項[0], "arguments": _解析可空JSON(工具項[15], 預算, False),
                   "result": _解析可空JSON(工具項[16], 預算, False),
                   "error": _解析可空JSON(工具項[17], 預算, False)})
    遮蔽列 = _讀取驗證遮蔽列(連線, 列[0], 端點, 預算)
    _核對投影墓碑(遮蔽列, 輸入, 中繼, 輸出, 錯誤, 事件, 工具)
    已遮蔽錯誤 = any(項[2] == "error" for 項 in 遮蔽列)
    if 錯誤 is not None and type(錯誤) is not dict: raise ValueError
    if 用量 is not None:
        if type(用量) is not dict or set(用量) != _用量欄位: raise ValueError
        輸入數, 輸出數, 總數 = (用量[鍵] for 鍵 in ("input_tokens", "output_tokens", "total_tokens"))
        if any(type(值) is not int or 值 < 0 for 值 in (輸入數, 輸出數, 總數)) or 輸入數 + 輸出數 != 總數:
            raise ValueError
        診斷用量值 = 診斷用量(總數)
    else:
        診斷用量值 = None
    return 診斷項目(
        列[0], 列[1], 列[2], 列[3], None if 已遮蔽錯誤 else _安全可空字串((錯誤 or {}).get("code")),
        None if 已遮蔽錯誤 else _安全可空字串((錯誤 or {}).get("schema_path")),
        None if 列[4] is None else float(列[4]), 診斷用量值,
        tuple(sorted({項[4] for 項 in 工具列})), float(列[5]),
        None if 列[6] is None else float(列[6]),
        ("error_code", "schema_path") if 已遮蔽錯誤 else (),
    )


def _讀取唯一(連線: Any, SQL: str, 參數: tuple[Any, ...], 欄寬: int) -> tuple[Any, ...]:
    """按 exact authority scope/key 讀取唯一 payload row 並關閉 cursor。"""
    游標 = 連線.execute(SQL, 參數)
    try:
        列 = 游標.fetchone()
        if 游標.fetchone() is not None or type(列) is not tuple or len(列) != 欄寬:
            raise ValueError
        return 列
    finally:
        游標.close()


def _編碼游標內容(端點: str, 秒數: int, 開始: float, 結束: float,
            位置: float, 識別碼: str) -> bytes:
    """依 production 唯一 JSON 規則建立 canonical cursor payload。"""
    return json.dumps(
        [1, 端點, 秒數, float(開始), float(結束), float(位置), 識別碼],
        ensure_ascii=True, separators=(",", ":"), sort_keys=True, allow_nan=False,
    ).encode("utf-8")


def _編碼Base64URL(原始: bytes) -> str:
    """依 production 唯一規則建立無 padding 的 canonical Base64URL。"""
    return base64.urlsafe_b64encode(原始).rstrip(b"=").decode("ascii")


def _編碼游標(金鑰: bytes, 端點: str, 秒數: int, 開始: float, 結束: float,
          位置: float, 識別碼: str) -> str:
    """以 canonical payload 與 HMAC-SHA256 產生 tamper-resistant opaque cursor。"""
    內容 = _編碼游標內容(端點, 秒數, 開始, 結束, 位置, 識別碼)
    簽章 = hmac.new(金鑰, 內容, hashlib.sha256).digest()
    return _編碼Base64URL(內容 + 簽章)


def _解碼游標(金鑰: bytes, 游標: str, 端點: str, 秒數: int) -> tuple[float, float, float, str]:
    """驗證 canonical encoding、signature、scope、window 與 keyset position。"""
    if (type(游標) is not str or not 1 <= len(游標) <= 1024 or not 游標.isascii()
            or any(字 not in "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_" for 字 in 游標)):
        raise ValueError
    原始 = base64.b64decode(游標 + "=" * (-len(游標) % 4), altchars=b"-_", validate=True)
    if not hmac.compare_digest(_編碼Base64URL(原始), 游標):
        raise ValueError
    if len(原始) <= 32 or not hmac.compare_digest(原始[-32:], hmac.new(金鑰, 原始[:-32], hashlib.sha256).digest()):
        raise ValueError
    值 = json.loads(原始[:-32], parse_constant=lambda _值: (_ for _ in ()).throw(ValueError()))
    if (type(值) is not list or len(值) != 7
            or type(值[0]) is not int or int.__eq__(值[0], 1) is not True
            or type(值[1]) is not str or not _安全識別碼(值[1])
            or str.__eq__(值[1], 端點) is not True
            or type(值[2]) is not int or not 1 <= 值[2] <= 2_592_000
            or int.__eq__(值[2], 秒數) is not True
            or not all(type(項) is float and math.isfinite(項) and 項 >= 0
                       and not (項 == 0.0 and math.copysign(1.0, 項) < 0) for 項 in 值[3:6])
            or not 值[3] < 值[4] or 值[3] + 秒數 != 值[4]
            or not 值[3] <= 值[5] < 值[4]
            or type(值[6]) is not str or not _安全識別碼(值[6])):
        raise ValueError
    if not hmac.compare_digest(
        _編碼游標內容(端點, 秒數, 值[3], 值[4], 值[5], 值[6]), 原始[:-32]
    ):
        raise ValueError
    return 值[3], 值[4], 值[5], 值[6]
