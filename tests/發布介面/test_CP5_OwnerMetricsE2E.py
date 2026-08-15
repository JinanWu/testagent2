"""A19-04 canonical two-owner、restart與zero-raw產品驗收。"""

from __future__ import annotations

import base64
import json
import os
import sqlite3
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

import asgi as root_asgi
from production_spa_support import 建立ProductionDist
from 繁中代理.使用者 import 使用者庫
from 繁中代理.發布介面 import asgi as production_asgi
from 繁中代理.發布介面.資料庫 import 初始化發布介面資料庫

_密碼 = "correct horse battery staple"
_標記 = (
    "A19_RAW_INPUT_MARKER", "A19_RAW_METADATA_MARKER", "A19_RAW_OUTPUT_MARKER",
    "A19_RAW_ERROR_MARKER", "A19_RAW_EVENT_MARKER", "A19_RAW_TOOL_MARKER",
)


def _設定環境(tmp_path, monkeypatch, 游標金鑰: bytes = b"O" * 32):
    """建立canonical factory所需的唯一外部authority集合。"""
    Web資料庫 = tmp_path / "web.sqlite3"
    Published資料庫 = tmp_path / "published.sqlite3"
    Bundle根 = tmp_path / "bundles"
    Bundle根.mkdir(exist_ok=True)
    Dist根 = 建立ProductionDist(tmp_path)
    Credential金鑰 = base64.urlsafe_b64encode(b"C" * 32).rstrip(b"=").decode("ascii")
    Owner金鑰 = base64.urlsafe_b64encode(游標金鑰).rstrip(b"=").decode("ascii")
    設定 = {
        "TESTAGENT2_DB_PATH": str(Web資料庫),
        "TESTAGENT2_PUBLISHED_DB_PATH": str(Published資料庫),
        "TESTAGENT2_PUBLISHED_BUNDLE_ROOT": str(Bundle根),
        "TESTAGENT2_WEB_DIST_ROOT": str(Dist根),
        "TESTAGENT2_WEB_ORIGINS": '["http://127.0.0.1:4173"]',
        "TESTAGENT2_COOKIE_SECURE": "false",
        "TESTAGENT2_SESSION_TTL_SECONDS": "300",
        "TESTAGENT2_MODEL_PROVIDER": "gemini-adc",
        "TESTAGENT2_MODEL_NAME": "gemini-test",
        "AIAGENT_GCP_PROJECT": "test-project",
        "AIAGENT_GCP_LOCATION": "global",
        "TESTAGENT2_PUBLISHED_CREDENTIAL_ACTIVE_KEY_VERSION": "1",
        "TESTAGENT2_PUBLISHED_CREDENTIAL_KEYS_JSON": json.dumps({"1": Credential金鑰}, separators=(",", ":")),
        "TESTAGENT2_OWNER_OBSERVABILITY_CURSOR_KEY": Owner金鑰,
    }
    for 名稱 in tuple(os.environ):
        if 名稱.startswith(("TESTAGENT2_", "AIAGENT_")):
            monkeypatch.delenv(名稱, raising=False)
    for 名稱, 值 in 設定.items():
        monkeypatch.setenv(名稱, 值)
    return Web資料庫, Published資料庫, Owner金鑰


def _建立使用者與資料(Web資料庫, Published資料庫):
    """以真使用者庫ID建立兩位owner與含raw marker的durable rows。"""
    使用者儲存庫 = 使用者庫(Web資料庫)
    try:
        OwnerA = 使用者儲存庫.建立使用者("owner-a", _密碼, roles=["user"])["id"]
        OwnerB = 使用者儲存庫.建立使用者("owner-b", _密碼, roles=["user"])["id"]
    finally:
        使用者儲存庫.連線.close()
    初始化發布介面資料庫(Published資料庫)
    現在 = time.time()
    UTC日初 = float(int(現在 // 86_400) * 86_400)
    with sqlite3.connect(Published資料庫) as 連線:
        連線.execute("PRAGMA foreign_keys=ON")
        for 後綴, Owner in (("a", OwnerA), ("b", OwnerB)):
            連線.execute("INSERT INTO service_accounts VALUES(?,?,NULL)", (f"service-{後綴}", 現在))
            連線.execute(
                "INSERT INTO published_endpoints VALUES(?,?,?,?,?,?,?,?,?,?)",
                (f"endpoint-{後綴}", Owner, f"service-{後綴}", f"slug-{後綴}", "active", None,
                 現在, 現在, 60, 60),
            )
            連線.execute(
                "INSERT INTO published_endpoint_versions VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (f"version-{後綴}", f"endpoint-{後綴}", 1, "requirement", "system", "[]", "[]", "{}",
                 "revision", "{}", "{}", "{}", None, "{}", 0, Owner, 現在),
            )
            連線.execute("UPDATE published_endpoints SET current_version_id=? WHERE id=?",
                         (f"version-{後綴}", f"endpoint-{後綴}"))
        資料列 = (
            ("invocation-a-1", "endpoint-a", "version-a", "failed", 30.0, "timeout", "0.001", "price-v1", UTC日初 - 1),
            ("invocation-a-2", "endpoint-a", "version-a", "succeeded", 10.0, None, "0.002", "price-v1", UTC日初 + 1),
            ("invocation-a-3", "endpoint-a", "version-a", "rate_limited", None, "quota", "0.003", "price-v2", UTC日初 + 2),
            ("invocation-a-4", "endpoint-a", "version-a", "pending", None, None, None, None, UTC日初 - 2),
            ("invocation-a-5", "endpoint-a", "version-a", "running", None, None, None, None, UTC日初 + 3),
            ("invocation-b-1", "endpoint-b", "version-b", "invalid_api_key", 99.0, "foreign", "0.004", "price-v3", UTC日初 - 1),
            ("invocation-b-2", "endpoint-b", "version-b", "succeeded", 88.0, None, "0.005", "price-v4", UTC日初 + 3),
        )
        for 識別碼, 端點, 版本, 狀態, 延遲, 錯誤碼, 成本, 價格版本, 建立時間 in 資料列:
            錯誤 = None if 錯誤碼 is None else json.dumps({"code": 錯誤碼, "internal": _標記[3]})
            用量 = None if 成本 is None else json.dumps({
                "input_tokens": 2, "output_tokens": 3, "total_tokens": 5,
                "estimated_cost_usd": 成本,
            })
            完成時間 = None if 狀態 in ("pending", "running") else 建立時間 + 1
            連線.execute(
                "INSERT INTO endpoint_invocations VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (識別碼, 端點, 版本, None, f"request-{識別碼}", None, None, 狀態,
                 json.dumps({"input": _標記[0]}), json.dumps({"metadata": _標記[1]}),
                 json.dumps({"output": _標記[2]}), 錯誤, 用量,
                 1, "a" * 64, 延遲, 價格版本, 建立時間, 完成時間),
            )
        連線.execute("INSERT INTO run_events VALUES(?,?,?,?,?,?)",
                     ("event-a", "invocation-a-1", 1, "completed", json.dumps({"raw": _標記[4]}), 現在 - 29))
        連線.execute("INSERT INTO endpoint_tool_calls VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                     ("tool-a", "invocation-a-1", "event-a", 1, "skills_list",
                      json.dumps({"raw": _標記[5]}), "success", json.dumps({"raw": _標記[5]}),
                      None, 1.0, None, 現在 - 28))
    return OwnerA, OwnerB


def _登入(客戶端: TestClient, 帳號: str) -> None:
    """經公開login route建立獨立真session。"""
    回應 = 客戶端.post("/api/auth/login", json={"username": 帳號, "password": _密碼})
    assert 回應.status_code == 200


def _無Raw(*候選) -> None:
    """對sanitized HTTP artifacts執行零marker掃描。"""
    文字 = "\n".join(值 if type(值) is str else json.dumps(值, ensure_ascii=False, sort_keys=True) for 值 in 候選)
    assert all(標記 not in 文字 for 標記 in _標記)


def _執行RestartWorker(*, cursor: str | None, 環境覆寫: dict[str, str] | None = None) -> dict[str, Any]:
    """以全新Python程序執行canonical app，且不回顯worker stderr或環境。"""
    環境 = dict(os.environ)
    for 名稱 in ("PYTHONPATH", "PYTHONHOME", "VIRTUAL_ENV", "PYTHONUSERBASE"):
        環境.pop(名稱, None)
    環境["PYTHONNOUSERSITE"] = "1"
    環境["A19_RESTART_PASSWORD"] = _密碼
    if 環境覆寫:
        環境.update(環境覆寫)
    結果 = subprocess.run(
        [sys.executable, str(Path(__file__).with_name("a19_restart_worker.py"))],
        input=json.dumps({"mode": "snapshot" if cursor is None else "continue", "cursor": cursor}),
        text=True, capture_output=True, timeout=30, env=環境,
        cwd=Path(__file__).resolve().parents[2], check=False,
    )
    assert 結果.returncode == 0
    assert len(結果.stdout.encode("utf-8")) <= 262_144
    _無Raw(結果.stdout, 結果.stderr)
    輸出 = json.loads(結果.stdout)
    assert type(輸出) is dict
    return 輸出


def test_A19_canonical_two_owner_restart_cursor與zero_raw(tmp_path, monkeypatch):
    """以fresh canonical app證明tenant isolation、stable restart與zero raw。"""
    Web資料庫, Published資料庫, Owner金鑰文字 = _設定環境(tmp_path, monkeypatch)
    OwnerA, OwnerB = _建立使用者與資料(Web資料庫, Published資料庫)
    assert OwnerA != OwnerB
    路徑A = "/api/published-endpoints/endpoint-a"
    路徑B = "/api/published-endpoints/endpoint-b"
    指標Query = "metrics?window_seconds=172800"
    診斷Query = "diagnostics?window_seconds=172800&limit=1"

    App1 = root_asgi.建立應用程式()
    with TestClient(App1, raise_server_exceptions=False) as A:
        B = TestClient(App1, raise_server_exceptions=False)
        _登入(A, "owner-a")
        _登入(B, "owner-b")
        A指標 = A.get(f"{路徑A}/{指標Query}")
        B指標 = B.get(f"{路徑B}/{指標Query}")
        assert A指標.status_code == B指標.status_code == 200
        assert (A指標.json()["invocation_count"], A指標.json()["terminal_count"],
                A指標.json()["error_count"], A指標.json()["estimated_cost_usd"]) == (5, 3, 2, "0.006")
        assert (B指標.json()["invocation_count"], B指標.json()["terminal_count"],
                B指標.json()["error_count"], B指標.json()["estimated_cost_usd"]) == (2, 2, 1, "0.009")
        assert [項["invocation_count"] for 項 in A指標.json()["daily"]] == [2, 3]
        assert sum(項["error_count"] for 項 in A指標.json()["daily"]) == 2
        assert [項["pricing_version"] for 項 in A指標.json()["cost_by_pricing_version"]] == ["price-v1", "price-v2"]
        assert {項["error_code"] for 項 in A指標.json()["top_errors"]} == {"quota", "timeout"}
        A外來 = A.get(f"{路徑B}/{指標Query}")
        A不存在 = A.get(f"/api/published-endpoints/endpoint-missing/{指標Query}")
        B外來 = B.get(f"{路徑A}/{指標Query}")
        assert (A外來.status_code, A外來.content) == (A不存在.status_code, A不存在.content)
        assert (B外來.status_code, B外來.content) == (A不存在.status_code, A不存在.content)
        assert A外來.status_code == 404
        A診斷外來 = A.get(f"{路徑B}/{診斷Query}")
        A診斷不存在 = A.get(f"/api/published-endpoints/endpoint-missing/{診斷Query}")
        assert (A診斷外來.status_code, A診斷外來.content) == (
            A診斷不存在.status_code, A診斷不存在.content,
        ) == (404, A診斷外來.content)
        for Claim in ("owner_id=spoof", "scope=all", "admin=true"):
            assert A.get(f"{路徑A}/{指標Query}&{Claim}").status_code == 422
        for Header in ({"X-Owner-ID": OwnerB}, {"X-Scope": "all"}, {"X-Admin": "true"}):
            assert A.get(f"{路徑A}/{指標Query}", headers=Header).json()["invocation_count"] == 5
        assert A.request(
            "GET", f"{路徑A}/{指標Query}",
            content=b'{"owner_id":"spoof","scope":"all","admin":true}',
        ).status_code == 422

        第一頁 = A.get(f"{路徑A}/{診斷Query}")
        assert 第一頁.status_code == 200 and len(第一頁.json()["items"]) == 1
        游標 = 第一頁.json()["next_cursor"]
        assert type(游標) is str and 游標
        第二頁 = A.get(f"{路徑A}/{診斷Query}&cursor={游標}")
        assert 第二頁.status_code == 200
        assert 第一頁.json()["items"][0]["invocation_id"] == "invocation-a-5"
        assert 第二頁.json()["items"][0]["invocation_id"] == "invocation-a-3"
        全部識別碼: list[str] = []
        全部游標 = None
        for _ in range(5):
            參數 = f"{診斷Query}&cursor={全部游標}" if 全部游標 is not None else 診斷Query
            頁 = A.get(f"{路徑A}/{參數}")
            assert 頁.status_code == 200 and len(頁.json()["items"]) == 1
            全部識別碼.append(頁.json()["items"][0]["invocation_id"])
            全部游標 = 頁.json()["next_cursor"]
        assert 全部識別碼 == ["invocation-a-5", "invocation-a-3", "invocation-a-2", "invocation-a-1", "invocation-a-4"]
        assert 全部游標 is None
        跨端點Replay = A.get(f"{路徑B}/{診斷Query}&cursor={游標}")
        MissingReplay = A.get(f"/api/published-endpoints/endpoint-missing/{診斷Query}&cursor={游標}")
        assert 跨端點Replay.status_code == 422
        assert (跨端點Replay.status_code, 跨端點Replay.content) == (MissingReplay.status_code, MissingReplay.content)
        assert A.get(f"{路徑A}/diagnostics?window_seconds=3600&limit=1&cursor={游標}").status_code == 422
        篡改 =游標[:-1] + ("A" if 游標[-1] != "A" else "B")
        assert A.get(f"{路徑A}/{診斷Query}&cursor={篡改}").status_code == 422
        OpenAPI = A.get("/openapi.json")
        assert OpenAPI.status_code == 200
        _無Raw(A指標.text, B指標.text, 第一頁.text, 第二頁.text, 游標, A外來.text, OpenAPI.text)
        App1指標 = A指標.json()
        B.close()

    程序1 = _執行RestartWorker(cursor=None)
    程序游標 = 程序1["first"]["next_cursor"]
    assert type(程序游標) is str
    程序2 = _執行RestartWorker(cursor=程序游標)
    assert 程序2["status"] == 200
    assert 程序2["continuation"] == 程序1["continuation"]
    for 欄位 in ("invocation_count", "terminal_count", "error_count", "usage", "estimated_cost_usd",
               "cost_by_pricing_version", "daily", "top_errors"):
        assert 程序2["metrics"][欄位] == 程序1["metrics"][欄位]
    _無Raw(程序1, 程序2, 程序游標)

    App2 = root_asgi.建立應用程式()
    assert App2 is not App1
    with TestClient(App2, raise_server_exceptions=False) as A2:
        _登入(A2, "owner-a")
        重啟指標 = A2.get(f"{路徑A}/{指標Query}")
        assert 重啟指標.status_code == 200
        for 欄位 in ("invocation_count", "terminal_count", "error_count", "usage", "estimated_cost_usd",
                   "cost_by_pricing_version", "daily", "top_errors"):
            assert 重啟指標.json()[欄位] == App1指標[欄位]
        重啟續頁 = A2.get(f"{路徑A}/{診斷Query}&cursor={游標}")
        assert 重啟續頁.status_code == 200
        _無Raw(重啟指標.text, 重啟續頁.text)

    輪替金鑰文字 = base64.urlsafe_b64encode(b"W" * 32).rstrip(b"=").decode("ascii")
    程序3 = _執行RestartWorker(
        cursor=程序游標,
        環境覆寫={"TESTAGENT2_OWNER_OBSERVABILITY_CURSOR_KEY": 輪替金鑰文字},
    )
    assert 程序3["status"] == 422
    _無Raw(程序3)
    monkeypatch.setenv("TESTAGENT2_OWNER_OBSERVABILITY_CURSOR_KEY", 輪替金鑰文字)
    App3 = root_asgi.建立應用程式()
    with TestClient(App3, raise_server_exceptions=False) as A3:
        _登入(A3, "owner-a")
        assert A3.get(f"{路徑A}/{診斷Query}&cursor={游標}").status_code == 422

    資料庫位元組 = Published資料庫.read_bytes()
    assert Owner金鑰文字.encode("ascii") not in 資料庫位元組
    assert b"O" * 32 not in 資料庫位元組
    assert 輪替金鑰文字.encode("ascii") not in 資料庫位元組
    assert b"W" * 32 not in 資料庫位元組
    設定Repr = repr(production_asgi.解析Production環境設定(dict(os.environ)))
    assert Owner金鑰文字 not in 設定Repr
    assert 輪替金鑰文字 not in 設定Repr
    assert repr(b"O" * 32) not in 設定Repr
    assert repr(b"W" * 32) not in 設定Repr
