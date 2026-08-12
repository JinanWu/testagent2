"""CP3 Quality R1：fresh factory、partial failure 與 async lifespan 證據。"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
import subprocess
import sys
import time

import pytest

import 繁中代理.發布介面.生產Web代理 as 組裝
from 繁中代理.發布介面.Web代理服務 import Web代理服務
from 繁中代理.發布介面.生產Web代理 import (
    延遲Web代理服務,
    生產Web代理建構器,
    生產Web代理資源,
)
from 繁中代理.發布介面.設定 import 生產設定


class _連線:
    def __init__(self, 名稱: str, 紀錄: list[str], 錯誤: BaseException | None = None, 延遲: float = 0):
        self.名稱, self.紀錄, self.錯誤, self.延遲 = 名稱, 紀錄, 錯誤, 延遲

    def close(self):
        time.sleep(self.延遲)
        self.紀錄.append(self.名稱)
        if self.錯誤 is not None:
            raise self.錯誤


class _庫:
    def __init__(self, 連線):
        self.連線 = 連線


def _設定(path: Path) -> 生產設定:
    return 生產設定(path, ("http://localhost:5173",), "fake", "fake", Cookie安全=False)


def test_root_asgi真正fresh_process_import與factory皆不建DB(tmp_path):
    """新直譯器只import root入口，再呼叫其建立應用程式，兩步皆無DB I/O。"""
    web_db = tmp_path / "web.sqlite3"
    published_db = tmp_path / "published.sqlite3"
    bundle_root = tmp_path / "bundles"
    code = """
from pathlib import Path
import asgi
env = __import__('os').environ
paths = tuple(Path(env[name]) for name in (
    'TESTAGENT2_DB_PATH', 'TESTAGENT2_PUBLISHED_DB_PATH',
    'TESTAGENT2_PUBLISHED_BUNDLE_ROOT',
))
assert all(not path.exists() for path in paths)
app = asgi.建立應用程式()
assert all(not path.exists() for path in paths)
assert app.title == '繁中代理發布介面'
print('FRESH_ROOT_FACTORY_OK')
"""
    env = {
        名稱: 值 for 名稱, 值 in os.environ.items()
        if not 名稱.startswith(("TESTAGENT2_", "AIAGENT_"))
    } | {
        "TESTAGENT2_DB_PATH": str(web_db),
        "TESTAGENT2_PUBLISHED_DB_PATH": str(published_db),
        "TESTAGENT2_PUBLISHED_BUNDLE_ROOT": str(bundle_root),
        "TESTAGENT2_WEB_ORIGINS": '["http://localhost:5173"]',
        "TESTAGENT2_MODEL_NAME": "gemini-test",
        "TESTAGENT2_COOKIE_SECURE": "false",
        "AIAGENT_GCP_PROJECT": "test-project",
        "AIAGENT_GCP_LOCATION": "global",
    }
    result = subprocess.run(
        [sys.executable, "-c", code], cwd=Path(__file__).parents[2], env=env,
        text=True, capture_output=True, check=False,
    )
    assert (result.returncode, result.stdout.strip(), result.stderr) == (0, "FRESH_ROOT_FACTORY_OK", "")
    assert all(not path.exists() for path in (web_db, published_db, bundle_root))


def test_async_startup慢同步工廠不阻塞同一event_loop(tmp_path, monkeypatch):
    """resource factory將慢同步startup送至thread，期間ticker必須進展。"""
    sentinel = object()

    def 慢建立(_設定值, _延遲服務):
        time.sleep(0.08)
        return sentinel

    monkeypatch.setattr(組裝, "_建立生產Web代理資源", 慢建立)
    相依 = 生產Web代理建構器().建立附加相依項(_設定(tmp_path / "db.sqlite3"), lambda: None, lambda: None)
    factory = 相依.資源工廠清單[0]

    async def 情境():
        ticks = 0
        running = True

        async def ticker():
            nonlocal ticks
            while running:
                ticks += 1
                await asyncio.sleep(0)

        task = asyncio.create_task(ticker())
        result = await factory()
        running = False
        await task
        return result, ticks

    result, ticks = asyncio.run(情境())
    assert result is sentinel and ticks > 1


def test_async_close慢同步close不阻塞同一event_loop():
    """drain與兩個慢close皆offload；close進行中ticker仍可進展。"""
    紀錄: list[str] = []
    服務 = Web代理服務(object(), object(), lambda **_kwargs: None)
    延遲 = 延遲Web代理服務()
    延遲.安裝(服務)
    資源 = 生產Web代理資源(
        延遲, 服務,
        _庫(_連線("session", 紀錄, 延遲=0.04)),
        _庫(_連線("user", 紀錄, 延遲=0.04)),
    )

    async def 情境():
        ticks = 0
        running = True

        async def ticker():
            nonlocal ticks
            while running:
                ticks += 1
                await asyncio.sleep(0)

        task = asyncio.create_task(ticker())
        await 資源.關閉()
        running = False
        await task
        return ticks

    assert asyncio.run(情境()) > 1
    assert 紀錄 == ["user", "session"]


@pytest.mark.parametrize("失敗階段", ["使用者供應器工廠", "服務工廠", "服務安裝"])
def test_startup各工廠失敗清理已取得資源且保留ordinary_identity(tmp_path, monkeypatch, 失敗階段):
    """provider/service/install任一ordinary失敗均清理已取得連線且原樣傳出。"""
    紀錄: list[str] = []
    session = _庫(_連線("session", 紀錄))
    user = _庫(_連線("user", 紀錄))
    original = LookupError(f"original-{失敗階段}")
    monkeypatch.setattr(組裝, "工作階段庫", lambda _path: session)
    monkeypatch.setattr(組裝, "使用者庫", lambda _path: (_ for _ in ()).throw(original) if 失敗階段 == "使用者供應器工廠" else user)
    monkeypatch.setattr(組裝, "初始化發布介面資料庫", lambda _path: ())
    if 失敗階段 == "服務工廠":
        monkeypatch.setattr(組裝, "Web代理服務", lambda *_args: (_ for _ in ()).throw(original))
    延遲 = 延遲Web代理服務()
    if 失敗階段 == "服務安裝":
        monkeypatch.setattr(延遲, "安裝", lambda _service: (_ for _ in ()).throw(original))

    with pytest.raises(LookupError) as captured:
        組裝._建立生產Web代理資源(_設定(tmp_path / "db.sqlite3"), 延遲)
    assert captured.value is original
    assert 紀錄 == (["session"] if 失敗階段 == "使用者供應器工廠" else ["user", "session"])


@pytest.mark.parametrize("第一錯誤,第二錯誤", [
    (RuntimeError("first"), None),
    (None, RuntimeError("second")),
    (RuntimeError("first"), RuntimeError("second")),
])
def test_shutdown第一第二或兩個close失敗皆attempt且第一ordinary保留identity(第一錯誤, 第二錯誤):
    """shutdown ordinary precedence為使用者後工作階段；所有close仍exact-once attempt。"""
    紀錄: list[str] = []
    服務 = Web代理服務(object(), object(), lambda **_kwargs: None)
    延遲 = 延遲Web代理服務(); 延遲.安裝(服務)
    資源 = 生產Web代理資源(
        延遲, 服務,
        _庫(_連線("session", 紀錄, 第二錯誤)),
        _庫(_連線("user", 紀錄, 第一錯誤)),
    )
    expected = 第一錯誤 or 第二錯誤
    with pytest.raises(RuntimeError) as captured:
        asyncio.run(資源.關閉())
    assert captured.value is expected
    asyncio.run(資源.關閉())
    assert 紀錄 == ["user", "session"]


def test_shutdown_control_flow明確優先ordinary且仍attempt全部close():
    """兩個close皆失敗時control-flow優先ordinary，並保留control identity。"""
    紀錄: list[str] = []
    ordinary = RuntimeError("ordinary")
    control = GeneratorExit("control")
    服務 = Web代理服務(object(), object(), lambda **_kwargs: None)
    延遲 = 延遲Web代理服務(); 延遲.安裝(服務)
    資源 = 生產Web代理資源(
        延遲, 服務,
        _庫(_連線("session", 紀錄, control)),
        _庫(_連線("user", 紀錄, ordinary)),
    )
    with pytest.raises(GeneratorExit) as captured:
        asyncio.run(資源.關閉())
    assert captured.value is control
    assert 紀錄 == ["user", "session"]
