"""A20真實不可逆遮蔽與restart browser server。"""
from __future__ import annotations

import json
import os
import secrets
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

_管理員 = "browser-admin-a20"
_端點 = "endpoint-browser-a20"
_版本 = "version-browser-a20"
_呼叫 = "invocation-browser-a20"
_事件 = "event-browser-a20"
_工具 = "tool-browser-a20"


def _必要路徑(名稱: str) -> Path:
    文字 = os.environ.get(名稱)
    路徑 = Path(文字) if type(文字) is str else Path()
    if not 文字 or not 路徑.is_absolute() or ".." in 路徑.parts:
        raise RuntimeError("A20 browser fixture設定無效") from None
    return 路徑


def _必要文字(名稱: str, 最小: int, 最大: int) -> str:
    值 = os.environ.get(名稱)
    if type(值) is not str or not 最小 <= len(值) <= 最大 or not 值.isascii():
        raise RuntimeError("A20 browser fixture設定無效") from None
    return 值


def _建立Primary資料(根: Path, 密碼: str) -> None:
    根.mkdir(parents=True, exist_ok=False)
    使用者 = 使用者庫(根 / "web.sqlite3")
    try:
        使用者.建立使用者(_管理員, 密碼, roles=["admin"])
    finally:
        使用者.連線.close()
    資料庫 = 根 / "published.sqlite3"
    初始化發布介面資料庫(資料庫)
    標記們 = tuple(
        [secrets.randbelow(90_000_000) + 10_000_000 for _ in range(4)]
        for _ in range(3)
    )
    with sqlite3.connect(資料庫) as 連線:
        連線.execute("PRAGMA foreign_keys=ON")
        連線.execute("INSERT INTO service_accounts VALUES(?,?,NULL)", ("service-browser-a20", 1.0))
        連線.execute(
            "INSERT INTO published_endpoints(id,owner_user_id,service_account_id,slug,status,"
            "current_version_id,created_at,updated_at) VALUES(?,?,?,?,?,NULL,?,?)",
            (_端點, "browser-owner", "service-browser-a20", "browser-a20", "active", 1.0, 1.0),
        )
        連線.execute(
            "INSERT INTO published_endpoint_versions VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (_版本, _端點, 1, "requirement", "system", "[]", "[]", "{}", "revision",
             "{}", "{}", "{}", None, "{}", 0, "browser-owner", 1.0),
        )
        連線.execute("UPDATE published_endpoints SET current_version_id=? WHERE id=?", (_版本, _端點))
        連線.execute(
            "INSERT INTO endpoint_invocations(id,endpoint_id,endpoint_version_id,credential_id,"
            "request_id,session_id,message_id,status,input_json,metadata_json,output_json,error_json,"
            "usage_json,metadata_size_bytes,metadata_sha256,latency_ms,pricing_version,created_at,completed_at) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (_呼叫, _端點, _版本, None, "request-browser-a20", None, None, "succeeded",
             json.dumps({"payload": {"value": 標記們[0]}}), None, json.dumps({"ok": True}),
             None, json.dumps({"total_tokens": 1}), None, None, 2.0, "pricing-v1", 10.0, 12.0),
        )
        連線.execute(
            "INSERT INTO run_events(id,invocation_id,sequence_number,event_type,payload_json,created_at) "
            "VALUES(?,?,?,?,?,?)",
            (_事件, _呼叫, 1, "model.completed", json.dumps({"payload": {"value": 標記們[1]}}), 11.0),
        )
        連線.execute(
            "INSERT INTO endpoint_tool_calls(id,invocation_id,run_event_id,sequence_number,tool_name,"
            "arguments_json,outcome,result_json,error_json,latency_ms,retry_of_tool_call_id,created_at) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            (_工具, _呼叫, _事件, 1, "safe_tool", "{}", "success",
             json.dumps({"payload": {"value": 標記們[2]}}), None, 1.0, None, 11.5),
        )


def _建立應用程式(根: Path, Dist根: Path, Port: str):
    Bundle根 = 根 / "bundles"
    Bundle根.mkdir(exist_ok=True)
    Web設定 = 生產設定(
        (根 / "web.sqlite3").resolve(), (f"http://127.0.0.1:{Port}",), "fake", "fake",
        Cookie安全=False, 工作階段有效秒數=300,
    )
    Published設定 = Published生產設定(
        (根 / "published.sqlite3").resolve(), Bundle根.resolve(),
        lambda _工具庫: None, lambda: {"fake": object()},
    )
    return 建立CP4SPAASGI應用程式(Web設定, Published設定, ProductionSPA設定(Dist根))


def main() -> int:
    根 = _必要路徑("A20_BROWSER_ROOT")
    Dist根 = _必要路徑("A20_BROWSER_DIST_ROOT")
    Port = _必要文字("A20_BROWSER_PORT", 4, 5)
    Phase = _必要文字("A20_BROWSER_PHASE", 7, 7)
    PID檔 = _必要路徑("A20_BROWSER_PID_FILE")
    密碼 = _必要文字("A20_BROWSER_PASSWORD", 32, 128)
    if not Port.isdecimal() or not 1024 <= int(Port) <= 65535 or Phase not in {"primary", "restart"}:
        raise RuntimeError("A20 browser fixture設定無效") from None
    if Phase == "primary":
        _建立Primary資料(根, 密碼)
    elif not (根 / "web.sqlite3").is_file() or not (根 / "published.sqlite3").is_file():
        raise RuntimeError("A20 browser restart state無效") from None
    with PID檔.open("x", encoding="ascii") as 檔案:
        檔案.write(str(os.getpid()))
    應用程式 = _建立應用程式(根, Dist根, Port)
    密碼 = ""
    os.environ.pop("A20_BROWSER_PASSWORD", None)
    uvicorn.run(應用程式, host="127.0.0.1", port=int(Port), log_level="warning", access_log=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
