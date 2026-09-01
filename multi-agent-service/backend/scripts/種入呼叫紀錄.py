"""把模擬的發布端點呼叫紀錄寫進地端 Published SQLite，供前端觀測頁測試用。

用途：
    地端資料庫只有端點定義、沒有任何 endpoint_invocations，導致「端點觀測」與
    「日誌篩選」兩頁在本機開發時永遠是空的。這支腳本補上一批涵蓋各種狀態的紀錄。

設計：
    一律走 SQLite呼叫儲存庫 的正規寫入路徑（建立 → 標記執行中 → 附加工具呼叫 → 完成），
    不手刻 INSERT。這樣 CHECK 條件、狀態轉換矩陣與 safe error 的 trigger 都會照常生效，
    寫出來的資料和真的被呼叫過一模一樣。

用法：
    python scripts/種入呼叫紀錄.py [--資料庫 var/dev/published.sqlite3] [--清除既有]
"""

from __future__ import annotations

import argparse
import secrets
import sqlite3
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from 繁中代理.發布介面.呼叫.儲存庫 import SQLite呼叫儲存庫, 呼叫計量  # noqa: E402

預設資料庫 = "var/dev/published.sqlite3"
一天秒數 = 86400.0


class _可控時鐘:
    """讓每筆紀錄落在指定時間點，才做得出跨日的每日趨勢。"""

    def __init__(self) -> None:
        self.現在 = time.time()

    def __call__(self) -> float:
        return self.現在

    def 前進(self, 秒: float) -> None:
        self.現在 += 秒


def 讀取端點資料(連線: sqlite3.Connection) -> tuple[str, str, str | None]:
    """取出要掛紀錄的端點、它的目前版本與任一把憑證。"""
    列 = 連線.execute(
        "SELECT id, current_version_id FROM published_endpoints ORDER BY created_at LIMIT 1",
    ).fetchone()
    if 列 is None:
        raise SystemExit("published_endpoints 是空的，請先在本機建立一個發布端點。")
    端點識別碼, 版本識別碼 = 列
    憑證列 = 連線.execute(
        "SELECT id FROM endpoint_credentials WHERE endpoint_id=? LIMIT 1", (端點識別碼,),
    ).fetchone()
    return 端點識別碼, 版本識別碼, (None if 憑證列 is None else 憑證列[0])


# 每筆：(距今幾天, 問題, 結果狀態, 錯誤碼, 工具結果, 延遲毫秒, 輸入token, 輸出token, 成本)
劇本: tuple[tuple[int, str, str, str | None, str | None, float | None, int, int, str], ...] = (
    (2, "請問公司的請假規定是什麼？", "succeeded", None, "success", 1840.0, 1120, 260, "0.00042"),
    (2, "特休假怎麼計算？", "succeeded", None, "success", 2310.0, 980, 340, "0.00039"),
    (2, "出差旅費可以報多少？", "failed", "tool_execution_failed", "error", 3120.0, 0, 0, "0"),
    (1, "育嬰留停要怎麼申請？", "succeeded", None, "success", 1520.0, 1040, 210, "0.00037"),
    (1, "眷屬健保加保需要哪些文件？", "succeeded", None, "success", 2740.0, 1310, 420, "0.00051"),
    (1, "加班費的計算基準是什麼？", "failed", "model_timeout", None, 60120.0, 0, 0, "0"),
    (1, "年終獎金發放時間", "rate_limited", "rate_limit_exceeded", None, None, 0, 0, "0"),
    (0, "公司的差勤系統怎麼補打卡？", "succeeded", None, "success", 1980.0, 1180, 300, "0.00044"),
    (0, "留職停薪期間勞保怎麼辦？", "succeeded", None, "success", 2150.0, 1090, 280, "0.00041"),
    (0, "報銷流程要跑幾關？", "failed", "tool_execution_failed", "error", 3480.0, 0, 0, "0"),
    (0, "婚假有幾天？", "failed", "tool_execution_failed", "error", 2960.0, 0, 0, "0"),
    (0, "外派津貼怎麼算？", "invalid_api_key", "invalid_api_key", None, None, 0, 0, "0"),
)


def 種入(資料庫: str, 清除既有: bool) -> None:
    """依劇本寫入呼叫紀錄；最後刻意留一筆 pending 重現終態數少一筆的情況。"""
    路徑 = Path(資料庫)
    if not 路徑.exists():
        raise SystemExit(f"找不到資料庫：{路徑}")

    連線 = sqlite3.connect(路徑)
    try:
        連線.execute("PRAGMA foreign_keys=ON")
        端點識別碼, 版本識別碼, 憑證識別碼 = 讀取端點資料(連線)
        if 清除既有:
            連線.execute("DELETE FROM endpoint_tool_calls")
            連線.execute("DELETE FROM endpoint_invocation_safe_errors")
            連線.execute("DELETE FROM endpoint_invocations")
            連線.commit()
    finally:
        連線.close()

    時鐘 = _可控時鐘()
    儲存庫 = SQLite呼叫儲存庫(路徑, 時鐘=時鐘)
    基準 = time.time()
    已寫入 = 0

    for 天前, 問題, 結果狀態, 錯誤碼, 工具結果, 延遲, 輸入token, 輸出token, 成本 in 劇本:
        # 一律往過去排：時間若超過查詢當下，紀錄會落在觀測視窗之外而算不進指標。
        時鐘.現在 = 基準 - 天前 * 一天秒數 - (len(劇本) - 已寫入) * 137.0
        呼叫識別碼 = 儲存庫.建立已解析呼叫(
            端點識別碼, 版本識別碼, f"req_{secrets.token_hex(16)}", {"question": 問題},
            credential_id=憑證識別碼, metadata={"source": "種入呼叫紀錄"},
        )
        if 結果狀態 in ("succeeded", "failed") and 錯誤碼 != "model_timeout":
            儲存庫.標記執行中(呼叫識別碼)
        elif 結果狀態 == "failed":
            儲存庫.標記執行中(呼叫識別碼)

        if 工具結果 is not None:
            時鐘.前進(0.4)
            共用 = {
                "invocation_id": 呼叫識別碼,
                "tool_call_id": f"tool-{secrets.token_hex(12)}",
                "tool_name": "administrative_search",
                "arguments": {"query": 問題, "search_mode": "hybrid"},
                "latency_ms": 640.0,
            }
            if 工具結果 == "success":
                儲存庫.附加工具呼叫(
                    **共用, outcome="success",
                    result={"success": True, "result": {"total_count": 3}},
                )
            else:
                儲存庫.附加工具呼叫(
                    **共用, outcome="error",
                    error={"code": "tool_execution_failed", "message": "工具執行失敗。"},
                )

        時鐘.前進(0.0 if 延遲 is None else 延遲 / 1000.0)
        if 結果狀態 == "succeeded":
            儲存庫.完成呼叫(
                呼叫識別碼, "succeeded",
                output={"answer": f"（測試資料）針對「{問題}」的回答。"},
                usage=呼叫計量(輸入token, 輸出token, 成本, "v1"),
                latency_ms=延遲,
            )
        else:
            儲存庫.完成呼叫(
                呼叫識別碼, 結果狀態,
                error={"code": 錯誤碼, "message": f"（測試資料）{錯誤碼}"},
                latency_ms=延遲,
            )
        已寫入 += 1

    # 刻意留一筆不結案：重現「呼叫數 比 終態數 多一筆」的畫面。
    時鐘.現在 = 基準 - 60.0
    儲存庫.建立已解析呼叫(
        端點識別碼, 版本識別碼, f"req_{secrets.token_hex(16)}", {"question": "尚未結案的呼叫"},
        credential_id=憑證識別碼, metadata={"source": "種入呼叫紀錄"},
    )
    已寫入 += 1
    print(f"已寫入 {已寫入} 筆呼叫紀錄到 {路徑}（端點 {端點識別碼}）")


def 主程式() -> None:
    """解析參數後執行種入。"""
    解析器 = argparse.ArgumentParser(description="種入發布端點呼叫紀錄")
    解析器.add_argument("--資料庫", default=預設資料庫)
    解析器.add_argument("--清除既有", action="store_true")
    參數 = 解析器.parse_args()
    種入(參數.資料庫, 參數.清除既有)


if __name__ == "__main__":
    主程式()
