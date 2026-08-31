"""为A18 Playwright smoke启动真SQLite与canonical production SPA ASGI。"""

from __future__ import annotations

import json
import os
import sqlite3
import sys
from pathlib import Path

import uvicorn

_專案根 = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(_專案根))

from 繁中代理.使用者 import 使用者庫
from 繁中代理.發布介面.asgi import 建立CP4SPAASGI應用程式
from 繁中代理.發布介面.生產Published執行 import Published生產設定
from 繁中代理.發布介面.生產SPA import ProductionSPA設定
from 繁中代理.發布介面.設定 import 生產設定
from 繁中代理.發布介面.資料庫 import 初始化發布介面資料庫
from 繁中代理.發布介面.治理.遮蔽 import SQLite不可逆遮蔽服務


_管理員帳號 = "browser-admin"
_一般帳號 = "browser-member"
_端點識別碼 = "endpoint-browser-a18"
_版本識別碼 = "version-browser-a18"
_呼叫識別碼 = "invocation-browser-a18"


def _必要絕對路徑(名稱: str) -> Path:
    """读取runner传入的绝对路径并拒绝relative与父层穿越。"""
    文字 = os.environ.get(名稱)
    路徑 = Path(文字) if type(文字) is str else Path()
    if not 文字 or not 路徑.is_absolute() or ".." in 路徑.parts:
        raise RuntimeError("A18 browser fixture設定無效") from None
    return 路徑


def _必要密碼() -> str:
    """讀取runner單次隨機credential，不輸出、不保存至repository。"""
    密碼 = os.environ.get("A18_BROWSER_PASSWORD")
    if type(密碼) is not str or not 32 <= len(密碼) <= 128 or not 密碼.isascii():
        raise RuntimeError("A18 browser fixture設定無效") from None
    return 密碼


def _建立使用者(根: Path, 密碼: str) -> None:
    """建立真Admin与member登录authority。"""
    儲存庫 = 使用者庫(根 / "web.sqlite3")
    try:
        儲存庫.建立使用者(_管理員帳號, 密碼, roles=["admin"])
        儲存庫.建立使用者(_一般帳號, 密碼, roles=["user"])
    finally:
        儲存庫.連線.close()


def _建立Published資料(根: Path) -> None:
    """经正式migration建立单一可列表/查看的呼叫纪录。"""
    路徑 = 根 / "published.sqlite3"
    初始化發布介面資料庫(路徑)
    with sqlite3.connect(路徑) as 連線:
        連線.execute("PRAGMA foreign_keys=ON")
        連線.execute("INSERT INTO service_accounts VALUES(?,?,NULL)", ("service-browser-a18", 1.0))
        連線.execute(
            "INSERT INTO published_endpoints(id,owner_user_id,service_account_id,slug,status,"
            "current_version_id,created_at,updated_at) VALUES(?,?,?,?,?,NULL,?,?)",
            (_端點識別碼, "browser-owner", "service-browser-a18", "browser-a18", "active", 1.0, 1.0),
        )
        連線.execute(
            "INSERT INTO published_endpoint_versions VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (_版本識別碼, _端點識別碼, 1, "requirement", "system", "[]", "[]", "{}",
             "revision", "{}", "{}", "{}", None, "{}", 0, "browser-owner", 1.0),
        )
        連線.execute(
            "UPDATE published_endpoints SET current_version_id=? WHERE id=?",
            (_版本識別碼, _端點識別碼),
        )
        連線.execute(
            "INSERT INTO endpoint_invocations(id,endpoint_id,endpoint_version_id,credential_id,"
            "request_id,session_id,message_id,status,input_json,metadata_json,output_json,error_json,"
            "usage_json,metadata_size_bytes,metadata_sha256,latency_ms,pricing_version,created_at,completed_at) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (_呼叫識別碼, _端點識別碼, _版本識別碼, None, "request-browser-a18",
             "session-browser-a18", "message-browser-a18", "succeeded",
             json.dumps({"prompt": "BROWSER_RAW_INPUT_MARKER"}),
             json.dumps({"trace": "BROWSER_RAW_METADATA_MARKER"}),
             json.dumps({"answer": "BROWSER_RAW_OUTPUT_MARKER"}), None,
             json.dumps({"total_tokens": 2}), 30, "a" * 64, 2.0, "pricing-v1", 10.0, 12.0),
        )
        連線.execute(
            "INSERT INTO run_events(id,invocation_id,sequence_number,event_type,payload_json,created_at) "
            "VALUES(?,?,?,?,?,?)",
            ("event-browser-a18", _呼叫識別碼, 1, "model.completed",
             json.dumps({"state": "BROWSER_RAW_EVENT_MARKER"}), 11.0),
        )
    遮蔽服務 = SQLite不可逆遮蔽服務(str(路徑))
    遮蔽服務.遮蔽(
        True, "redaction-browser-metadata", "audit-browser-redact-metadata",
        _管理員帳號, "request-browser-redact-metadata", _呼叫識別碼,
        "metadata", _呼叫識別碼, "/trace", "privacy", 13.0,
    )
    遮蔽服務.遮蔽(
        True, "redaction-browser-event", "audit-browser-redact-event",
        _管理員帳號, "request-browser-redact-event", _呼叫識別碼,
        "run_event", "event-browser-a18", "/state", "privacy", 14.0,
    )


def _建立應用程式(根: Path, Dist根: Path):
    """使用与production root相同的CP4＋SPA composition，仅model adapter为fake。"""
    Bundle根 = 根 / "bundles"
    Bundle根.mkdir()
    Web設定 = 生產設定(
        (根 / "web.sqlite3").resolve(), ("http://127.0.0.1:4173",), "fake", "fake",
        Cookie安全=False, 工作階段有效秒數=300,
    )
    Published設定 = Published生產設定(
        (根 / "published.sqlite3").resolve(), Bundle根.resolve(),
        lambda _工具庫: None, lambda: {"fake": object()},
    )
    return 建立CP4SPAASGI應用程式(Web設定, Published設定, ProductionSPA設定(Dist根))


def main() -> int:
    """建立fixture并前景运行bounded uvicorn process。"""
    根 = _必要絕對路徑("A18_BROWSER_ROOT")
    Dist根 = _必要絕對路徑("A18_BROWSER_DIST_ROOT")
    Port文字 = os.environ.get("A18_BROWSER_PORT", "4173")
    if not Port文字.isascii() or not Port文字.isdecimal() or not 1024 <= int(Port文字) <= 65535:
        raise RuntimeError("A18 browser fixture設定無效") from None
    根.mkdir(parents=True, exist_ok=False)
    _建立使用者(根, _必要密碼())
    _建立Published資料(根)
    應用程式 = _建立應用程式(根, Dist根)
    uvicorn.run(應用程式, host="127.0.0.1", port=int(Port文字), log_level="warning", access_log=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
