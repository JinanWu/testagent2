"""A22 canonical frontend management harness的Python integration wrapper。"""
from __future__ import annotations
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
WEB = ROOT / "apps" / "web-app"


def test_A22_browser_glue完整且不啟用artifact_capture():
    config = (WEB / "playwright.a22.config.ts").read_text(encoding="utf-8")
    runner = (WEB / "browser" / "run-a22-smoke.mjs").read_text(encoding="utf-8")
    server = (WEB / "browser" / "啟動A22Browser伺服器.py").read_text(encoding="utf-8")
    assert "trace: 'off'" in config and "screenshot: 'off'" in config and "video: 'off'" in config
    assert "reuseExistingServer: false" in config and "root_asgi.建立應用程式()" in server
    assert "PLANNER_LIVE=BLOCKED" in runner and "gemini-2.5-flash-lite" in runner
    assert "AIAGENT_MODEL_MODE: 'fake'" not in runner


def test_A22_server與checker可由隔離Python編譯():
    files = [WEB / "browser" / "啟動A22Browser伺服器.py", WEB / "browser" / "檢查A22Browser資料庫.py"]
    code = "import pathlib,sys;[compile(pathlib.Path(p).read_text(encoding='utf-8'),p,'exec') for p in sys.argv[1:]]"
    result = subprocess.run([sys.executable, "-c", code, *map(str, files)], cwd=ROOT, env={"PATH": ""}, capture_output=True, text=True)
    assert result.returncode == 0, "A22 Python glue syntax invalid"
