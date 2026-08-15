"""啟動 A21 真 SQLite 與 canonical production SPA ASGI。"""

from __future__ import annotations

import json
import os
import sqlite3
import sys
from pathlib import Path

import uvicorn

_專案根 = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(_專案根))

import asgi as root_asgi
from 繁中代理.使用者 import 使用者庫
from 繁中代理.發布介面.治理.遮蔽 import SQLite不可逆遮蔽服務
from 繁中代理.發布介面.資料庫 import 初始化發布介面資料庫

_管理員帳號 = "browser-admin-a21"
_成員帳號 = "browser-owner-a21"
_端點 = "endpoint-browser-a21"
_版本 = "version-browser-a21"
_呼叫 = "invocation-browser-a21"


def _必要路徑(名稱: str) -> Path:
    文字 = os.environ.get(名稱)
    路徑 = Path(文字) if type(文字) is str else Path()
    if not 文字 or not 路徑.is_absolute() or ".." in 路徑.parts:
        raise RuntimeError("A21 browser fixture設定無效") from None
    return 路徑


def _必要文字(名稱: str, 最小: int, 最大: int) -> str:
    值 = os.environ.get(名稱)
    if type(值) is not str or not 最小 <= len(值) <= 最大 or not 值.isascii():
        raise RuntimeError("A21 browser fixture設定無效") from None
    return 值


def _合成標記們() -> tuple[str, str, str, str, str]:
    """只在 fixture 內合成安全值，runner 不輸出內容。"""
    郵件 = lambda 前綴: 前綴 + chr(64) + "safe.invalid"
    return (郵件("input"), "0912-345-678", "4" + "1" * 15,
            郵件("arguments"), 郵件("result"))


def _建立首次資料(根: Path, 密碼: str) -> None:
    """以真 user store/migration/DB 建立五 target durable fixture。"""
    使用者 = 使用者庫(根 / "web.sqlite3")
    try:
        使用者.建立使用者(_管理員帳號, 密碼, roles=["admin"])
        owner = 使用者.建立使用者(_成員帳號, 密碼, roles=["user"])["id"]
    finally:
        使用者.連線.close()
    路徑 = 根 / "published.sqlite3"
    初始化發布介面資料庫(路徑)
    輸入, 中繼, 回應, 參數, 結果 = _合成標記們()
    with sqlite3.connect(路徑) as 連線:
        連線.execute("PRAGMA foreign_keys=ON")
        連線.execute("INSERT INTO service_accounts VALUES(?,?,NULL)", ("service-browser-a21", 1.0))
        連線.execute(
            "INSERT INTO published_endpoints VALUES(?,?,?,?,?,?,?,?,?,?)",
            (_端點, owner, "service-browser-a21", "browser-a21", "active", None, 1.0, 1.0, 60, 60),
        )
        連線.execute(
            "INSERT INTO published_endpoint_versions VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (_版本, _端點, 1, "safe", "safe", "[]", "[]", "{}", "revision", "{}",
             "{}", "{}", None, "{}", 0, owner, 1.0),
        )
        連線.execute("UPDATE published_endpoints SET current_version_id=? WHERE id=?", (_版本, _端點))
        連線.execute(
            "INSERT INTO endpoint_invocations VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (_呼叫, _端點, _版本, None, "request-browser-a21", None, None, "succeeded",
             json.dumps({"contact": 輸入}), json.dumps({"phone": 中繼}),
             json.dumps({"answer": 回應}), None, json.dumps({"total_tokens": 3}),
             None, None, 4.0, "price-a21", 10.0, 14.0),
        )
        警告 = {"warnings": [{
            "code": "sensitive_data_detected", "message": "回應包含可能的敏感資料。",
        }]}
        連線.execute(
            "INSERT INTO run_events VALUES(?,?,?,?,?,?)",
            ("event-browser-a21", _呼叫, 1, "completed", json.dumps(警告), 14.0),
        )
        連線.execute(
            "INSERT INTO endpoint_tool_calls VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            ("tool-browser-a21-1", _呼叫, None, 1, "skills_list", json.dumps({"category": 參數}),
             "success", json.dumps({"success": True, "result": {"skills": []}}), None, None, None, 12.0),
        )
        連線.execute(
            "INSERT INTO endpoint_tool_calls VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            ("tool-browser-a21-2", _呼叫, None, 2, "skill_view", json.dumps({"name": "stable"}),
             "success", json.dumps({"success": True, "result": {"content": 結果}}), None, None, None, 13.0),
        )
    遮蔽 = SQLite不可逆遮蔽服務(str(路徑))
    for 後綴, 目標, 列, JSON路徑 in (
        ("input", "invocation_input", _呼叫, "/contact"),
        ("metadata", "metadata", _呼叫, "/phone"),
        ("response", "output", _呼叫, "/answer"),
        ("arguments", "tool_arguments", "tool-browser-a21-1", "/category"),
        ("result", "tool_result", "tool-browser-a21-2", "/result/content"),
    ):
        遮蔽.遮蔽(True, f"redaction-a21-{後綴}", f"audit-redaction-a21-{後綴}",
                 _管理員帳號, f"request-redaction-a21-{後綴}", _呼叫,
                 目標, 列, JSON路徑, "privacy", 15.0)
    命中們 = (
        ("input", None, "email_detector", "/contact", 1, 4),
        ("metadata", None, "phone_detector", "/phone", 0, 3),
        ("response_data", None, "card_detector", "/answer", 2, 8),
        ("tool_arguments", "tool-browser-a21-1", "email_detector", "/category", 0, 2),
        ("tool_result", "tool-browser-a21-2", "email_detector", "/result/content", 3, 9),
    )
    with sqlite3.connect(路徑) as 連線:
        連線.execute("PRAGMA foreign_keys=ON")
        for 序號, (目標, 工具, 類型, JSON路徑, 開始, 結束) in enumerate(命中們, 1):
            命中ID, 稽核ID, 時間 = f"hit-browser-a21-{序號}", f"audit-hit-browser-a21-{序號}", 20.0 + 序號
            中繼資料 = json.dumps({
                "warning_code": "sensitive_data_detected", "target": 目標,
                "detector_type": 類型, "json_path": JSON路徑, "start": 開始, "end": 結束,
            }, sort_keys=True, separators=(",", ":"))
            連線.execute(
                "INSERT INTO audit_events VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (稽核ID, 稽核ID, 時間, "published_api.sensitive_data_detected", "success",
                 "system", None, "invocation", _呼叫, None, _端點, _呼叫, 中繼資料, 時間),
            )
            連線.execute(
                "INSERT INTO invocation_sensitive_hits VALUES(?,?,?,?,?,?,?,?,?,?)",
                (命中ID, _呼叫, 工具, 目標, 類型, JSON路徑, 開始, 結束, 稽核ID, 時間),
            )


def _設定生產環境(根: Path, Dist根: Path, Port: str) -> None:
    設定 = {
        "TESTAGENT2_DB_PATH": str(根 / "web.sqlite3"),
        "TESTAGENT2_PUBLISHED_DB_PATH": str(根 / "published.sqlite3"),
        "TESTAGENT2_PUBLISHED_BUNDLE_ROOT": str(根 / "bundles"),
        "TESTAGENT2_WEB_DIST_ROOT": str(Dist根),
        "TESTAGENT2_WEB_ORIGINS": json.dumps([f"http://127.0.0.1:{Port}"], separators=(",", ":")),
        "TESTAGENT2_COOKIE_SECURE": "false", "TESTAGENT2_SESSION_TTL_SECONDS": "300",
        "TESTAGENT2_MODEL_PROVIDER": "gemini-adc", "TESTAGENT2_MODEL_NAME": "gemini-browser-a21",
        "AIAGENT_GCP_PROJECT": "browser-project", "AIAGENT_GCP_LOCATION": "global",
        "TESTAGENT2_PUBLISHED_CREDENTIAL_ACTIVE_KEY_VERSION": "1",
        "TESTAGENT2_PUBLISHED_CREDENTIAL_KEYS_JSON": json.dumps({
            "1": _必要文字("A21_BROWSER_CREDENTIAL_KEY", 43, 43),
        }, separators=(",", ":")),
        "TESTAGENT2_OWNER_OBSERVABILITY_CURSOR_KEY": _必要文字("A21_BROWSER_OWNER_CURSOR_KEY", 43, 43),
    }
    for 名稱 in tuple(os.environ):
        if 名稱.startswith(("TESTAGENT2_", "AIAGENT_")):
            del os.environ[名稱]
    os.environ.update(設定)


def main() -> int:
    根 = _必要路徑("A21_BROWSER_ROOT")
    Dist根 = _必要路徑("A21_BROWSER_DIST_ROOT")
    Port = _必要文字("A21_BROWSER_PORT", 4, 5)
    if not Port.isdecimal() or not 1024 <= int(Port) <= 65535:
        raise RuntimeError("A21 browser fixture設定無效") from None
    密碼 = _必要文字("A21_BROWSER_PASSWORD", 32, 128)
    if not 根.exists():
        根.mkdir(parents=True, exist_ok=False)
        (根 / "bundles").mkdir()
        _建立首次資料(根, 密碼)
    _設定生產環境(根, Dist根, Port)
    密碼 = ""
    for 名稱 in ("A21_BROWSER_PASSWORD", "A21_BROWSER_CREDENTIAL_KEY", "A21_BROWSER_OWNER_CURSOR_KEY"):
        os.environ.pop(名稱, None)
    uvicorn.run(root_asgi.建立應用程式(), host="127.0.0.1", port=int(Port), log_level="warning", access_log=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
