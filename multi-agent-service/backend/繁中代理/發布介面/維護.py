"""GOV G07 明確 dry-run／execute 五年保存維護命令列。"""
from __future__ import annotations

import argparse
import asyncio
import json
import math
import os
import re
import sys
from collections.abc import Awaitable, Callable, Mapping, Sequence
from typing import Any

from .治理.保存期限 import SQLite保存候選規劃器, SQLite保存清除服務
from .治理.PostgreSQL保存期限 import PostgreSQL保存候選規劃器, PostgreSQL保存清除服務
from .PostgreSQL資源 import 建立PostgreSQL資源
from ..環境設定 import 讀取交易儲存設定
from ..交易儲存設定 import 交易儲存設定

_固定錯誤 = "retention maintenance failed"
_非負十進位 = re.compile(r"[0-9]+(?:\.[0-9]+)?\Z")


def _絕對資料庫路徑(文字: str) -> str:
    """驗證命令列資料庫值為未經正規化的 exact 絕對路徑。"""
    if not 文字 or os.path.abspath(文字) != 文字:
        raise argparse.ArgumentTypeError("must be an exact absolute path")
    return 文字


def _非負有限epoch(文字: str) -> float:
    """把不含指數、符號或特殊值的非負十進位轉為有限 float。"""
    if _非負十進位.fullmatch(文字) is None:
        raise argparse.ArgumentTypeError("must be a finite nonnegative decimal")
    try:
        結果 = float(文字)
    except (OverflowError, ValueError):
        raise argparse.ArgumentTypeError("must be a finite nonnegative decimal") from None
    if not math.isfinite(結果):
        raise argparse.ArgumentTypeError("must be a finite nonnegative decimal")
    return 結果


def _批次上限(文字: str) -> int:
    """驗證批次上限為一至一千的十進位整數。"""
    if not 文字.isascii() or not 文字.isdecimal():
        raise argparse.ArgumentTypeError("must be an integer from 1 to 1000")
    結果 = int(文字)
    if not 1 <= 結果 <= 1000:
        raise argparse.ArgumentTypeError("must be an integer from 1 to 1000")
    return 結果


def _建立剖析器() -> argparse.ArgumentParser:
    """建立只提供明確 retention 操作的命令列剖析器。"""
    剖析器 = argparse.ArgumentParser(
        prog="python -m 繁中代理.發布介面.維護",
        description="五年保存資料的安全維護工具。",
    )
    子命令 = 剖析器.add_subparsers(dest="命令", required=True)
    保存 = 子命令.add_parser(
        "retention", help="規劃或執行五年保存資料清除",
        description="以注入的參考時間規劃或執行一批五年保存資料清除。",
    )
    保存.add_argument("--backend", choices=("sqlite", "postgres"), default="sqlite", dest="後端")
    保存.add_argument("--database", required=False, type=_絕對資料庫路徑, dest="資料庫")
    保存.add_argument("--now-epoch", required=True, type=_非負有限epoch, dest="現在")
    保存.add_argument("--batch-limit", type=_批次上限, default=100, dest="批次上限")
    模式 = 保存.add_mutually_exclusive_group(required=True)
    模式.add_argument("--dry-run", action="store_true", dest="僅規劃", help="唯讀列出聚合候選統計")
    模式.add_argument("--execute", action="store_true", dest="執行", help="不提示並原子清除一批到期資料")
    return 剖析器


def _規劃摘要(計畫組: tuple[Any, ...]) -> dict[str, object]:
    """只從 G05 計畫產生不含識別碼的固定欄位聚合摘要。"""
    期限 = [計畫.保存期限 for 計畫 in 計畫組]
    return {
        "mode": "dry-run",
        "candidate_count": len(計畫組),
        "run_event_count": sum(計畫.執行事件數 for 計畫 in 計畫組),
        "tool_call_count": sum(計畫.工具呼叫數 for 計畫 in 計畫組),
        "redaction_count": sum(計畫.遮蔽數 for 計畫 in 計畫組),
        "audit_event_count": sum(計畫.稽核事件數 for 計畫 in 計畫組),
        "earliest_deadline": min(期限, default=None),
        "latest_deadline": max(期限, default=None),
    }


def _清除摘要(結果: Any) -> dict[str, object]:
    """只從 G06 結果產生固定欄位刪除計數。"""
    return {
        "mode": "execute",
        "invocation_count": 結果.呼叫數,
        "run_event_count": 結果.執行事件數,
        "tool_call_count": 結果.工具呼叫數,
        "redaction_count": 結果.遮蔽數,
        "audit_event_count": 結果.稽核事件數,
    }


async def _執行PostgreSQL一次(
    參數: argparse.Namespace,
    設定: 交易儲存設定,
    *,
    資源工廠: Callable[[交易儲存設定], Awaitable[Any]],
    規劃器工廠: Callable[[交易儲存設定], Any],
    清除服務工廠: Callable[[交易儲存設定], Any],
) -> dict[str, object]:
    """建立 canonical pool/readiness 資源，執行一批，並確實反向關閉。"""
    資源 = await 資源工廠(設定)
    try:
        if 參數.僅規劃:
            服務 = 規劃器工廠(設定)
            return _規劃摘要(服務.規劃(參數.現在, 候選上限=參數.批次上限))
        服務 = 清除服務工廠(設定)
        return _清除摘要(服務.清除(參數.現在, 批次上限=參數.批次上限))
    finally:
        await 資源.關閉()


def 執行主程式(
    參數列: Sequence[str] | None = None,
    *,
    規劃器工廠: Callable[[str], Any] = SQLite保存候選規劃器,
    清除服務工廠: Callable[[str], Any] = SQLite保存清除服務,
    PostgreSQL規劃器工廠: Callable[[交易儲存設定], Any] = PostgreSQL保存候選規劃器,
    PostgreSQL清除服務工廠: Callable[[交易儲存設定], Any] = PostgreSQL保存清除服務,
    PostgreSQL資源工廠: Callable[[交易儲存設定], Awaitable[Any]] = 建立PostgreSQL資源,
    交易設定工廠: Callable[[Mapping[str, str]], 交易儲存設定] = 讀取交易儲存設定,
) -> int:
    """剖析命令並執行一次；argparse 自行處理 help 與使用錯誤。"""
    剖析器 = _建立剖析器()
    參數 = 剖析器.parse_args(參數列)
    if 參數.後端 == "sqlite" and 參數.資料庫 is None:
        剖析器.error("--database is required with --backend sqlite")
    if 參數.後端 == "postgres" and 參數.資料庫 is not None:
        剖析器.error("--database is not allowed with --backend postgres")
    try:
        if 參數.後端 == "postgres":
            設定 = 交易設定工廠(os.environ)
            if 設定.後端 != "postgres":
                raise ValueError
            摘要 = asyncio.run(_執行PostgreSQL一次(
                參數,
                設定,
                資源工廠=PostgreSQL資源工廠,
                規劃器工廠=PostgreSQL規劃器工廠,
                清除服務工廠=PostgreSQL清除服務工廠,
            ))
        else:
            服務 = 規劃器工廠(參數.資料庫) if 參數.僅規劃 else 清除服務工廠(參數.資料庫)
            if 參數.僅規劃:
                摘要 = _規劃摘要(服務.規劃(參數.現在, 候選上限=參數.批次上限))
            else:
                摘要 = _清除摘要(服務.清除(參數.現在, 批次上限=參數.批次上限))
        輸出 = json.dumps(摘要, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
    except (KeyboardInterrupt, SystemExit, GeneratorExit):
        raise
    except BaseException:
        參數 = 摘要 = 輸出 = None
        print(_固定錯誤, file=sys.stderr)
        return 1
    print(輸出)
    return 0


if __name__ == "__main__":
    raise SystemExit(執行主程式())
