"""A22 canonical non-Planner Owner/Admin browser＋restart server。"""
from __future__ import annotations
import json
import os
import sqlite3
import sys
import time
from pathlib import Path
import uvicorn

_BACKEND_ROOT = Path(__file__).resolve().parents[3] / "multi-agent-service" / "backend"
sys.path.insert(0, str(_BACKEND_ROOT))
sys.path.insert(0, str(_BACKEND_ROOT / "tests" / "發布介面"))
from a08_3_formal_publish import 建立正式v1
from 繁中代理.使用者 import 使用者庫
from 繁中代理.發布介面.asgi import 建立CP4SPAASGI應用程式, 解析Production環境設定
from 繁中代理.發布介面.資料庫 import 初始化發布介面資料庫


def required_path(name: str) -> Path:
    raw = os.environ.get(name); value = Path(raw) if raw else Path()
    if not raw or not value.is_absolute() or ".." in value.parts:
        raise RuntimeError("A22 browser fixture設定無效") from None
    return value


def required_text(name: str, minimum: int, maximum: int) -> str:
    value = os.environ.get(name)
    if type(value) is not str or not minimum <= len(value) <= maximum or not value.isascii():
        raise RuntimeError("A22 browser fixture設定無效") from None
    return value


def seed(root: Path, password: str, marker: str) -> None:
    root.mkdir(parents=True, exist_ok=False)
    identity = 建立正式v1(web=root / "web.sqlite3", db=root / "published.sqlite3",
                         bundles=root / "bundles", skill_root=root / "skills")
    users = 使用者庫(root / "web.sqlite3")
    try:
        owner_a = users.建立使用者("browser-owner-a", password, roles=["user"])["id"]
        owner_b = users.建立使用者("browser-owner-b", password, roles=["user"])["id"]
        users.建立使用者("browser-admin-a22", password, roles=["admin"])
    finally:
        users.連線.close()
    now = time.time()
    db = root / "published.sqlite3"
    with sqlite3.connect(db) as connection:
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("UPDATE published_endpoints SET owner_user_id=? WHERE id=?", (owner_a, identity["endpoint"]))
        connection.execute("INSERT INTO service_accounts VALUES(?,?,NULL)", ("service-browser-a22-b", now))
        connection.execute(
            "INSERT INTO published_endpoints(id,owner_user_id,service_account_id,slug,status,current_version_id,created_at,updated_at,rate_limit_requests,rate_limit_window_seconds) VALUES(?,?,?,?,?,NULL,?,?,?,?)",
            ("endpoint-browser-a22-b", owner_b, "service-browser-a22-b", "browser-a22-b", "active", now, now, 60, 60),
        )
        connection.execute(
            "INSERT INTO published_endpoint_versions VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            ("version-browser-a22-b", "endpoint-browser-a22-b", 1, "REQ-B", "SYSTEM-B", "[]", "[]", "{}", "revision-b",
             '{"type":"object"}', '{"type":"object","properties":{"answer":{"type":"string"}},"required":["answer"],"additionalProperties":false}',
             '{"provider":"gemini-adc","model":"gemini-2.5-flash-lite","temperature":0.0,"max_tokens":100,"timeout_seconds":3.0,"structured_output":true,"schema_retry_count":1}',
             None, "{}", 0, owner_b, now),
        )
        connection.execute("UPDATE published_endpoints SET current_version_id='version-browser-a22-b' WHERE id='endpoint-browser-a22-b'")
        payload = json.dumps({"secret": marker, "safe": "owner-a"}, separators=(",", ":"))
        connection.execute(
            "INSERT INTO endpoint_invocations(id,endpoint_id,endpoint_version_id,credential_id,request_id,session_id,message_id,status,input_json,metadata_json,output_json,error_json,usage_json,metadata_size_bytes,metadata_sha256,latency_ms,pricing_version,created_at,completed_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            ("invocation-browser-a22-a", identity["endpoint"], identity["v1"], None, "request-browser-a22-a", None, None,
             "succeeded", payload, None, '{"answer":"safe"}', None,
             '{"input_tokens":2,"output_tokens":3,"total_tokens":5,"estimated_cost_usd":"0.001"}',
             None, None, 8.0, "price-v1", now - 10, now - 9),
        )


def seed_planner(root: Path, password: str) -> None:
    """只為ADC live gate建立真user/skill與空Published DB，不預造Draft/Publish。"""
    root.mkdir(parents=True, exist_ok=False)
    skill = root / "skills" / "stable"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text(
        "---\nname: stable\ndescription: Stable browser live skill\n---\n\nReturn a safe structured answer.\n",
        encoding="utf-8",
    )
    users = 使用者庫(root / "web.sqlite3")
    try:
        users.建立使用者(
            "browser-planner-a22", password, roles=["user"],
            enabled_tools=["skills_list", "skill_view"], enabled_skills=["stable"],
            skill_roots=[str(root / "skills")], allowed_workdirs=[str(root)],
        )
    finally:
        users.連線.close()
    初始化發布介面資料庫(root / "published.sqlite3")
    (root / "bundles").mkdir()


def configure(root: Path, dist: Path, port: str) -> None:
    settings = {
        "TESTAGENT2_DB_PATH": str(root / "web.sqlite3"),
        "TESTAGENT2_PUBLISHED_DB_PATH": str(root / "published.sqlite3"),
        "TESTAGENT2_PUBLISHED_BUNDLE_ROOT": str(root / "bundles"),
        "TESTAGENT2_WEB_DIST_ROOT": str(dist),
        "TESTAGENT2_WEB_ORIGINS": json.dumps([f"http://127.0.0.1:{port}"], separators=(",", ":")),
        "TESTAGENT2_COOKIE_SECURE": "false", "TESTAGENT2_SESSION_TTL_SECONDS": "300",
        "TESTAGENT2_MODEL_PROVIDER": "gemini-adc", "TESTAGENT2_MODEL_NAME": "gemini-2.5-flash-lite",
        "AIAGENT_GCP_PROJECT": os.environ.get("A22_BROWSER_GCP_PROJECT", "browser-project"), "AIAGENT_GCP_LOCATION": "global",
        "TESTAGENT2_PUBLISHED_CREDENTIAL_ACTIVE_KEY_VERSION": "1",
        "TESTAGENT2_PUBLISHED_CREDENTIAL_KEYS_JSON": json.dumps({"1": required_text("A22_BROWSER_CREDENTIAL_KEY", 43, 43)}, separators=(",", ":")),
        "TESTAGENT2_OWNER_OBSERVABILITY_CURSOR_KEY": required_text("A22_BROWSER_OWNER_CURSOR_KEY", 43, 43),
    }
    for name in tuple(os.environ):
        if name.startswith(("TESTAGENT2_", "AIAGENT_")): del os.environ[name]
    os.environ.update(settings)


def main() -> int:
    root = required_path("A22_BROWSER_ROOT"); dist = required_path("A22_BROWSER_DIST_ROOT")
    pid_file = required_path("A22_BROWSER_PID_FILE")
    port = required_text("A22_BROWSER_PORT", 4, 5); phase = required_text("A22_BROWSER_PHASE", 7, 7)
    password = required_text("A22_BROWSER_PASSWORD", 32, 128)
    marker = required_text("A22_BROWSER_RAW_MARKER", 32, 96)
    if not port.isdecimal() or not 1024 <= int(port) <= 65535 or phase not in {"primary", "restart", "planner"}:
        raise RuntimeError("A22 browser fixture設定無效") from None
    if phase == "primary": seed(root, password, marker)
    elif phase == "planner": seed_planner(root, password)
    elif not (root / "web.sqlite3").is_file() or not (root / "published.sqlite3").is_file():
        raise RuntimeError("A22 browser restart state無效") from None
    configure(root, dist, port)
    with pid_file.open("x", encoding="ascii") as handle: handle.write(str(os.getpid()))
    password = marker = ""
    for name in ("A22_BROWSER_PASSWORD", "A22_BROWSER_RAW_MARKER", "A22_BROWSER_CREDENTIAL_KEY", "A22_BROWSER_OWNER_CURSOR_KEY"):
        os.environ.pop(name, None)
    Web設定, Published設定, SPA設定 = 解析Production環境設定(os.environ)
    應用程式 = 建立CP4SPAASGI應用程式(Web設定, Published設定, SPA設定)
    uvicorn.run(應用程式, host="127.0.0.1", port=int(port), log_level="warning", access_log=False)
    return 0


if __name__ == "__main__": raise SystemExit(main())
