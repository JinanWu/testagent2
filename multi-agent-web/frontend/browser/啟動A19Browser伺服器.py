"""啟動A19 two-owner真SQLite與canonical production SPA ASGI。"""

from __future__ import annotations

import json
import os
import sqlite3
import sys
import time
from pathlib import Path

import uvicorn

_後端根 = Path(__file__).resolve().parents[3] / "multi-agent-service" / "backend"
sys.path.insert(0, str(_後端根))

from 繁中代理.使用者 import 使用者庫
from 繁中代理.發布介面.asgi import 建立CP4SPAASGI應用程式, 解析Production環境設定
from 繁中代理.發布介面.資料庫 import 初始化發布介面資料庫

_密碼環境 = "A19_BROWSER_PASSWORD"
_標記 = "A19_BROWSER_RAW_MARKER"


def _必要路徑(名稱: str) -> Path:
    """讀取runner提供的absolute system-temp authority。"""
    文字 = os.environ.get(名稱)
    路徑 = Path(文字) if type(文字) is str else Path()
    if not 文字 or not 路徑.is_absolute() or ".." in 路徑.parts:
        raise RuntimeError("A19 browser fixture設定無效") from None
    return 路徑


def _必要文字(名稱: str, 最小: int, 最大: int) -> str:
    """讀取bounded ASCII secret或port authority且不輸出值。"""
    值 = os.environ.get(名稱)
    if type(值) is not str or not 最小 <= len(值) <= 最大 or not 值.isascii():
        raise RuntimeError("A19 browser fixture設定無效") from None
    return 值


def _建立使用者(根: Path, 密碼: str) -> tuple[str, str]:
    """透過真使用者庫建立兩個browser owner並回傳durable IDs。"""
    儲存庫 = 使用者庫(根 / "web.sqlite3")
    try:
        A = 儲存庫.建立使用者("browser-owner-a", 密碼, roles=["user"])["id"]
        B = 儲存庫.建立使用者("browser-owner-b", 密碼, roles=["user"])["id"]
        return A, B
    finally:
        儲存庫.連線.close()


def _建立Published資料(根: Path, OwnerA: str, OwnerB: str) -> None:
    """以真owner IDs建立兩端點及含raw marker的durable invocation。"""
    路徑 = 根 / "published.sqlite3"
    初始化發布介面資料庫(路徑)
    現在 = time.time()
    with sqlite3.connect(路徑) as 連線:
        連線.execute("PRAGMA foreign_keys=ON")
        for 後綴, Owner in (("a", OwnerA), ("b", OwnerB)):
            連線.execute("INSERT INTO service_accounts VALUES(?,?,NULL)", (f"service-browser-a19-{後綴}", 現在))
            連線.execute(
                "INSERT INTO published_endpoints VALUES(?,?,?,?,?,?,?,?,?,?)",
                (f"endpoint-browser-a19-{後綴}", Owner, f"service-browser-a19-{後綴}",
                 f"browser-a19-{後綴}", "active", None, 現在, 現在, 60, 60),
            )
            連線.execute(
                "INSERT INTO published_endpoint_versions VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (f"version-browser-a19-{後綴}", f"endpoint-browser-a19-{後綴}", 1,
                 "requirement", "system", "[]", "[]", "{}", "revision", "{}", "{}", "{}",
                 None, "{}", 0, Owner, 現在),
            )
            連線.execute("UPDATE published_endpoints SET current_version_id=? WHERE id=?",
                         (f"version-browser-a19-{後綴}", f"endpoint-browser-a19-{後綴}"))
            連線.execute(
                "INSERT INTO endpoint_invocations VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (f"invocation-browser-a19-{後綴}", f"endpoint-browser-a19-{後綴}",
                 f"version-browser-a19-{後綴}", None, f"request-browser-a19-{後綴}", None, None,
                 "failed", json.dumps({"raw": _標記}), json.dumps({"raw": _標記}),
                 json.dumps({"raw": _標記}), json.dumps({"code": f"safe_{後綴}", "raw": _標記}),
                 json.dumps({"input_tokens": 2, "output_tokens": 3, "total_tokens": 5,
                             "estimated_cost_usd": "0.001"}),
                 1, "a" * 64, 12.0, "price-v1", 現在 - 10, 現在 - 9),
            )


def _設定Production環境(根: Path, Dist根: Path, Port: str) -> None:
    """建立root factory唯一允許的production environment。"""
    Credential金鑰 = _必要文字("A19_BROWSER_CREDENTIAL_KEY", 43, 43)
    Owner金鑰 = _必要文字("A19_BROWSER_OWNER_CURSOR_KEY", 43, 43)
    設定 = {
        "TESTAGENT2_DB_PATH": str(根 / "web.sqlite3"),
        "TESTAGENT2_PUBLISHED_DB_PATH": str(根 / "published.sqlite3"),
        "TESTAGENT2_PUBLISHED_BUNDLE_ROOT": str(根 / "bundles"),
        "TESTAGENT2_WEB_DIST_ROOT": str(Dist根),
        "TESTAGENT2_WEB_ORIGINS": json.dumps([f"http://127.0.0.1:{Port}"], separators=(",", ":")),
        "TESTAGENT2_COOKIE_SECURE": "false",
        "TESTAGENT2_SESSION_TTL_SECONDS": "300",
        "TESTAGENT2_MODEL_PROVIDER": "gemini-adc",
        "TESTAGENT2_MODEL_NAME": "gemini-browser-a19",
        "AIAGENT_GCP_PROJECT": "browser-project",
        "AIAGENT_GCP_LOCATION": "global",
        "TESTAGENT2_PUBLISHED_CREDENTIAL_ACTIVE_KEY_VERSION": "1",
        "TESTAGENT2_PUBLISHED_CREDENTIAL_KEYS_JSON": json.dumps({"1": Credential金鑰}, separators=(",", ":")),
        "TESTAGENT2_OWNER_OBSERVABILITY_CURSOR_KEY": Owner金鑰,
    }
    for 名稱 in tuple(os.environ):
        if 名稱.startswith(("TESTAGENT2_", "AIAGENT_")):
            del os.environ[名稱]
    os.environ.update(設定)


def main() -> int:
    """建立fixture後以前景uvicorn執行canonical root app。"""
    根 = _必要路徑("A19_BROWSER_ROOT")
    Dist根 = _必要路徑("A19_BROWSER_DIST_ROOT")
    Port = _必要文字("A19_BROWSER_PORT", 4, 5)
    if not Port.isdecimal() or not 1024 <= int(Port) <= 65535:
        raise RuntimeError("A19 browser fixture設定無效") from None
    密碼 = _必要文字(_密碼環境, 32, 128)
    根.mkdir(parents=True, exist_ok=False)
    (根 / "bundles").mkdir()
    OwnerA, OwnerB = _建立使用者(根, 密碼)
    _建立Published資料(根, OwnerA, OwnerB)
    _設定Production環境(根, Dist根, Port)
    密碼 = ""
    for 名稱 in (_密碼環境, "A19_BROWSER_CREDENTIAL_KEY", "A19_BROWSER_OWNER_CURSOR_KEY"):
        os.environ.pop(名稱, None)
    Web設定, Published設定, SPA設定 = 解析Production環境設定(os.environ)
    應用程式 = 建立CP4SPAASGI應用程式(Web設定, Published設定, SPA設定)
    uvicorn.run(應用程式, host="127.0.0.1", port=int(Port), log_level="warning", access_log=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
