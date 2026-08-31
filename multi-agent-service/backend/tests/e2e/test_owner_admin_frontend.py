"""A22 Owner/Admin production-frontend closure contract wrapper。"""
from __future__ import annotations
import os
import subprocess
from pathlib import Path

import pytest

BACKEND_ROOT = Path(__file__).resolve().parents[2]
REPOSITORY_ROOT = BACKEND_ROOT.parents[1]
WEB = REPOSITORY_ROOT / "multi-agent-web" / "frontend"


def test_A22_spec鎖定Owner_Admin_restart與secret_hygiene():
    spec = (WEB / "browser" / "tests" / "a22-owner-admin.spec.ts").read_text(encoding="utf-8")
    required = [
        "browser-owner-a", "browser-owner-b", "browser-admin-a22", "憑證", "文件", "監控",
        "menuitemradio", "所有端點", "完整呼叫紀錄", "確認永久遮蔽", "A22_BROWSER_PHASE", "A22_BROWSER_RAW_MARKER",
        "localStorage", "sessionStorage", "caches.keys()", "adminRequests", "一次性 API key",
    ]
    assert all(token in spec for token in required)
    assert "page.route(" not in spec and "route.fulfill(" not in spec


def test_A22_runner鎖定distinct_process_DB_checker與誠實Planner_gate():
    runner = (WEB / "browser" / "run-a22-smoke.mjs").read_text(encoding="utf-8")
    for token in ["primary", "restart", "A22_SERVER_PIDS_DISTINCT=PASS", "檢查A22Browser資料庫.py", "PLANNER_LIVE=BLOCKED", "status = 2", "process.exitCode = status"]:
        assert token in runner
    assert "response.text()" not in runner


def test_A22_canonical_nonPlanner_live並誠實回報Planner_gate():
    python = os.environ.get("A22_BROWSER_PYTHON")
    if not python:
        pytest.skip("A22_BROWSER_PYTHON未提供，live browser由獨立Gate執行")
    result = subprocess.run(
        ["node", "browser/run-a22-smoke.mjs"], cwd=WEB,
        env={**os.environ, "A22_BROWSER_PYTHON": python},
        capture_output=True, text=True, timeout=300,
    )
    output = result.stdout + result.stderr
    assert "A22_NON_PLANNER=PASS" in output
    assert "A22_SERVER_PIDS_DISTINCT=PASS phases=2" in output
    assert result.returncode in {0, 2}
    if result.returncode == 2:
        assert (
            "PLANNER_LIVE=BLOCKED reason=ADC_REFRESH_UNAVAILABLE" in output
            or "PLANNER_LIVE=BLOCKED reason=LAB_ACCESS_UNAVAILABLE" in output
        )
    else:
        assert "PLANNER_LIVE=PASS model=gemini-2.5-flash-lite flow=DRAFT_PUBLISH" in output
