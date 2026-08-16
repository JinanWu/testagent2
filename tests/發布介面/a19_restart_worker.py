"""A19 process-restart worker：每次程序只啟動一個canonical app lifecycle。"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

_專案根 = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_專案根))

from fastapi.testclient import TestClient

import asgi as root_asgi


def _讀取命令() -> dict[str, object]:
    """只接受bounded restart probe command，不讀取credential內容。"""
    原文 = sys.stdin.read(4097)
    if len(原文.encode("utf-8")) > 4096:
        raise RuntimeError("A19 restart worker輸入無效") from None
    值 = json.loads(原文)
    if type(值) is not dict or set(值) != {"mode", "cursor"}:
        raise RuntimeError("A19 restart worker輸入無效") from None
    if 值["mode"] not in ("snapshot", "continue"):
        raise RuntimeError("A19 restart worker輸入無效") from None
    if not (值["cursor"] is None or type(值["cursor"]) is str and 1 <= len(值["cursor"]) <= 1024):
        raise RuntimeError("A19 restart worker輸入無效") from None
    return 值


def _登入(客戶端: TestClient) -> None:
    """使用runner-only password建立真canonical session。"""
    密碼 = os.environ.get("A19_RESTART_PASSWORD")
    if type(密碼) is not str or not 20 <= len(密碼) <= 128:
        raise RuntimeError("A19 restart worker設定無效") from None
    回應 = 客戶端.post("/api/auth/login", json={"username": "owner-a", "password": 密碼})
    if 回應.status_code != 200:
        raise RuntimeError("A19 restart worker登入失敗") from None


def main() -> int:
    """執行snapshot或continuation probe，stdout只寫safe DTO。"""
    命令 = _讀取命令()
    with TestClient(root_asgi.建立應用程式(), raise_server_exceptions=False) as 客戶端:
        _登入(客戶端)
        基礎 = "/api/published-endpoints/endpoint-a"
        if 命令["mode"] == "snapshot":
            指標 = 客戶端.get(f"{基礎}/metrics", params={"window_seconds": 172800})
            第一頁 = 客戶端.get(f"{基礎}/diagnostics", params={"window_seconds": 172800, "limit": 1})
            if 指標.status_code != 200 or 第一頁.status_code != 200:
                raise RuntimeError("A19 restart worker查詢失敗") from None
            游標 = 第一頁.json().get("next_cursor")
            if type(游標) is not str:
                raise RuntimeError("A19 restart worker游標無效") from None
            第二頁 = 客戶端.get(
                f"{基礎}/diagnostics",
                params={"window_seconds": 172800, "limit": 1, "cursor": 游標},
            )
            if 第二頁.status_code != 200:
                raise RuntimeError("A19 restart worker續頁失敗") from None
            輸出 = {"metrics": 指標.json(), "first": 第一頁.json(), "continuation": 第二頁.json()}
        else:
            指標 = 客戶端.get(f"{基礎}/metrics", params={"window_seconds": 172800})
            回應 = 客戶端.get(
                f"{基礎}/diagnostics",
                params={"window_seconds": 172800, "limit": 1, "cursor": 命令["cursor"]},
            )
            if 指標.status_code != 200:
                raise RuntimeError("A19 restart worker查詢失敗") from None
            輸出 = {"metrics": 指標.json(), "status": 回應.status_code, "continuation": 回應.json()}
    sys.stdout.write(json.dumps(輸出, ensure_ascii=False, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
