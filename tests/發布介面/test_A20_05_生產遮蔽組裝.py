"""A20-05 canonical production redaction composition contracts。"""
from __future__ import annotations

import asyncio
from pathlib import Path

from 繁中代理.使用者 import 使用者庫
from 繁中代理.發布介面.治理.管理遮蔽治理 import (
    管理遮蔽內部失敗,
    管理遮蔽請求,
    管理遮蔽治理權限,
)
from 繁中代理.發布介面.治理.遮蔽 import SQLite不可逆遮蔽服務
from 繁中代理.發布介面.生產管理稽核 import 安裝管理遮蔽資源
from 繁中代理.發布介面.網頁工作階段 import 網頁工作階段服務
from 繁中代理.發布介面.設定 import 網頁安全設定
from 繁中代理.發布介面.資料庫 import 初始化發布介面資料庫


class _主資源:
    def __init__(self) -> None:
        self.關閉次數 = 0

    async def 關閉(self) -> None:
        self.關閉次數 += 1


def _建立權限(tmp_path: Path) -> 管理遮蔽治理權限:
    web = tmp_path / "web.sqlite3"
    users = 使用者庫(web)
    users.連線.close()
    setting = 網頁安全設定(("http://localhost:5173",), Cookie安全=False, 工作階段有效秒數=60)
    return 管理遮蔽治理權限(網頁工作階段服務(web, 有效秒數=60), setting)


def _請求() -> 管理遮蔽請求:
    return 管理遮蔽請求(
        "admin-1", "idem-1", "endpoint-1", "invocation-1",
        "tool_result", "tool-1", "/secret", "privacy request",
    )


def test_A20_05_startup發布authority且shutdown先撤銷再關閉inner(monkeypatch, tmp_path):
    async def scenario() -> None:
        published = tmp_path / "published.sqlite3"
        初始化發布介面資料庫(published)
        authority = _建立權限(tmp_path)
        inner = _主資源()
        calls: list[str] = []

        def execute(self, command, **kwargs):
            del self, command, kwargs
            calls.append("mutation")
            return object()

        monkeypatch.setattr(SQLite不可逆遮蔽服務, "執行命令", execute)
        resource = await 安裝管理遮蔽資源(inner, authority, published)
        assert type(authority.執行(_請求())) is 管理遮蔽內部失敗
        assert calls == ["mutation"]

        await resource.關閉()
        assert inner.關閉次數 == 1
        assert type(authority.執行(_請求())) is 管理遮蔽內部失敗
        assert calls == ["mutation"]
        await resource.關閉()
        assert inner.關閉次數 == 1

    asyncio.run(scenario())
