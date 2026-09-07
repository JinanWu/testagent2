"""W1 PostgreSQL Web runtime authority 與 raw duplicate-cookie 因果測試。"""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from fastapi import HTTPException, Request, Response

from 繁中代理.交易儲存設定 import 交易儲存設定
from 繁中代理.使用者 import 使用者上下文
from 繁中代理.發布介面.設定 import (
    網頁安全設定,
    網頁工作階段Cookie名稱,
)
from 繁中代理.發布介面.網頁工作階段 import 網頁工作階段服務
import 繁中代理.發布介面.生產Web代理 as Web組裝
import 繁中代理.發布介面.路由.網頁認證 as 認證
import 繁中代理.儲存 as 儲存模組
from 繁中代理.PostgreSQL工作階段庫 import PostgreSQL工作階段庫


_TOKEN_A = "a" * 32
_TOKEN_B = "b" * 32


def _postgres設定() -> 交易儲存設定:
    return 交易儲存設定(
        "postgres",
        "postgresql://runtime:***@/app?host=/cloudsql/p:r:i",
        "p:r:i",
        1,
        2,
        5,
    )


def _請求(cookie_headers: list[bytes]) -> Request:
    headers = [(b"cookie", value) for value in cookie_headers]
    return Request({"type": "http", "method": "GET", "path": "/api/auth/me", "headers": headers})


def _使用者上下文(runtime_root: Path) -> 使用者上下文:
    return 使用者上下文(
        user_id="user-1",
        username="alice",
        display_name="Alice",
        roles=["user"],
        enabled_tools=set(),
        enabled_skills=set(),
        skill_roots=[],
        allowed_workdirs=[runtime_root],
        memory_home=runtime_root / ".memory",
        is_admin=False,
    )


def test_canonical_postgres_web_resource建立request_local_runtime且零SQLite_path讀取(tmp_path, monkeypatch):
    """PG Web resource 只用PG設定與明確runtime root，不碰SQLite factory/path。"""
    def 禁止(*_args, **_kwargs):
        raise AssertionError("不得呼叫SQLite path constructor/fallback")

    monkeypatch.setattr(儲存模組, "建立工作階段庫", 禁止)
    monkeypatch.setattr(儲存模組, "建立使用者庫", 禁止)
    monkeypatch.setattr(Web組裝, "工作階段庫", 禁止)
    monkeypatch.setattr(Web組裝, "使用者庫", 禁止)
    monkeypatch.setattr(Web組裝, "初始化發布介面資料庫", 禁止)

    def 禁止資料庫路徑(_self):
        raise AssertionError("不得讀取repository .資料庫路徑")

    monkeypatch.setattr(PostgreSQL工作階段庫, "資料庫路徑", property(禁止資料庫路徑), raising=False)
    runtime_root = (tmp_path / "runtime-authority").resolve()
    設定 = Web組裝.生產設定(
        None,
        ("https://web.example",),
        "gemini-adc",
        "gemini-2.5-flash",
        "project-1",
        "asia-east1",
        交易儲存=_postgres設定(),
    )
    延遲 = Web組裝.延遲Web代理服務()
    資源 = Web組裝._建立生產Web代理資源(設定, 延遲)
    try:
        服務 = 延遲._服務
        assert 服務 is not None
        第一個 = 服務._執行階段工廠(使用者上下文物件=_使用者上下文(runtime_root), source="web")
        第二個 = 服務._執行階段工廠(使用者上下文物件=_使用者上下文(runtime_root), source="web")
        assert 第一個 is not 第二個
        assert 第一個.工作階段庫物件 is 服務._工作階段庫
        assert type(第一個.工作階段庫物件) is PostgreSQL工作階段庫
        assert 第一個.工作目錄 == str(runtime_root)
    finally:
        asyncio.run(資源.關閉())


def test_postgres_web_runtime拒絕缺少明確non_db工作目錄(tmp_path, monkeypatch):
    """PG runtime workdir 不得由cwd、DB path或其他fallback暗中導出。"""
    設定 = Web組裝.生產設定(
        None,
        ("https://web.example",),
        "gemini-adc",
        "gemini-2.5-flash",
        "project-1",
        "asia-east1",
        交易儲存=_postgres設定(),
    )
    延遲 = Web組裝.延遲Web代理服務()
    資源 = Web組裝._建立生產Web代理資源(設定, 延遲)
    try:
        上下文 = _使用者上下文(tmp_path.resolve())
        上下文.allowed_workdirs = None
        with pytest.raises(ValueError, match="^Web執行工作目錄無效$"):
            延遲._服務._執行階段工廠(使用者上下文物件=上下文, source="web")
    finally:
        asyncio.run(資源.關閉())


@pytest.mark.parametrize(
    ("headers", "expected"),
    (
        ([f"{網頁工作階段Cookie名稱}={_TOKEN_A}".encode()], _TOKEN_A),
        ([f"theme=dark; {網頁工作階段Cookie名稱}={_TOKEN_A}; locale=zh-TW".encode()], _TOKEN_A),
        ([f"{網頁工作階段Cookie名稱}={_TOKEN_A}; {網頁工作階段Cookie名稱}={_TOKEN_B}".encode()], None),
        ([f"{網頁工作階段Cookie名稱}={_TOKEN_A}".encode(), f"other=x; {網頁工作階段Cookie名稱}={_TOKEN_B}".encode()], None),
        ([f"{網頁工作階段Cookie名稱}={_TOKEN_A}".encode(), f"{網頁工作階段Cookie名稱}={_TOKEN_A}".encode()], None),
        ([f"{網頁工作階段Cookie名稱}={_TOKEN_A},hostile".encode()], None),
        ([f"{網頁工作階段Cookie名稱}=bad%00value".encode()], None),
        ([b"other=published_web_session=" + _TOKEN_A.encode()], None),
    ),
)
def test_cookie只由raw_ASGI_headers解析並拒絕同名多值(headers, expected):
    """合法單cookie/其他cookie通過；同header、跨header與hostile值皆fail closed。"""
    assert 認證._讀取cookie(_請求(headers), 網頁工作階段Cookie名稱) == expected


def test_duplicate_session_cookie在authoritative_restore前拒絕(tmp_path):
    """Starlette mapping 即使會折疊，current-session 仍在restore前拒絕duplicate。"""
    服務 = 網頁工作階段服務(tmp_path / "unused.sqlite3", 有效秒數=60)
    呼叫 = []

    def 不得恢復(*args):
        呼叫.append(args)
        raise AssertionError("duplicate cookie不得到達authority")

    服務.恢復 = 不得恢復
    相依 = 認證.建立目前工作階段相依項(
        服務,
        網頁安全設定(("http://localhost:5173",), Cookie安全=False, 工作階段有效秒數=60),
    )
    請求 = _請求([
        f"{網頁工作階段Cookie名稱}={_TOKEN_A}".encode(),
        f"{網頁工作階段Cookie名稱}={_TOKEN_B}".encode(),
    ])
    with pytest.raises(HTTPException) as 捕捉:
        相依(請求, Response())
    assert 捕捉.value.status_code == 401
    assert 呼叫 == []
