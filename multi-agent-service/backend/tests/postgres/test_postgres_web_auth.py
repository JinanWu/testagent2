"""PostgreSQL Web auth session repository 的無資料庫測試。"""
from __future__ import annotations

import hashlib
import ast
import inspect
import re
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

import pytest

from 繁中代理.交易儲存設定 import 交易儲存設定
from 繁中代理.發布介面.PostgreSQL網頁工作階段 import PostgreSQL網頁工作階段服務
import 繁中代理.發布介面.PostgreSQL網頁工作階段 as 網頁模組
from 繁中代理.發布介面.網頁工作階段 import (
    網頁CSRF無效, 網頁使用者, 網頁未授權, 網頁管理權限不足,
)

連線名稱 = "lab-cola-rd:asia-east1:testagent2-postgres-lab"


def _schema_ddl():
    本庫 = Path(__file__).parents[2] / "繁中代理/postgres_migrations/versions/0001_full_product_schema.py"
    樹 = ast.parse(本庫.read_text(encoding="utf-8"))
    for 節點 in 樹.body:
        if isinstance(節點, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "DDL" for t in 節點.targets):
            return ast.literal_eval(節點.value)
    raise AssertionError("0001 DDL inventory 不存在")


def _欄位(table):
    ddl = next(x for x in _schema_ddl() if x.startswith(f"CREATE TABLE {table} ("))
    return set(re.findall(r"(?:\(\s*|,\s*)([a-z_][a-z0-9_]*)\s+(?:text|jsonb|boolean|timestamptz|bytea)", ddl))


def postgres設定():
    return 交易儲存設定(
        "postgres", f"postgresql://alice:secret@/app?host=/cloudsql/{連線名稱}", 連線名稱, 1, 2, 5,
    )


class 假游標:
    def __init__(self, fetch=None, rowcounts=None):
        self.fetch = dict(fetch or {})
        self.rowcounts = dict(rowcounts or {})
        self.calls = []
        self.current = -1
        self.rowcount = -1

    def execute(self, query, params=None, *, prepare=None, binary=None):
        self.current += 1
        self.calls.append((str(query), params, prepare, binary))
        self.rowcount = self.rowcounts.get(self.current, 1)
        return self

    def fetchone(self):
        return self.fetch.get(self.current)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        return None


class 假連線:
    def __init__(self, cursor): self._cursor = cursor
    def cursor(self, name='', *, binary=False, row_factory=None, scrollable=None, withhold=False):
        return self._cursor


def 安裝(monkeypatch, service, cursor):
    @contextmanager
    def transaction(設定):
        assert 設定 is service.設定
        yield 假連線(cursor)
    monkeypatch.setattr("繁中代理.PostgreSQL連線.交易連線", transaction)


def row(*, role="member", disabled=False, expires=1060.0, csrf="c" * 32):
    return {
        "id": "web-1", "user_id": "u1", "csrf_token_hash": hashlib.sha256(csrf.encode()).digest(),
        "expires_at": datetime.fromtimestamp(expires, timezone.utc),
        "last_seen_at": datetime.fromtimestamp(1000.0, timezone.utc), "revoked_at": None,
        "username": "alice", "roles": ["admin"] if role == "admin" else ["user"],
        "disabled": disabled,
    }


def test_發行只保存hash並在同交易撤銷presented_cookie(monkeypatch):
    values = iter(("s" * 32, "c" * 32))
    service = PostgreSQL網頁工作階段服務(
        postgres設定(), 時鐘=lambda: 1000.0, 密鑰工廠=values.__next__, 有效秒數=60,
    )
    cursor = 假游標(fetch={1: {"id": "web-1"}})
    安裝(monkeypatch, service, cursor)
    result = service.發行(網頁使用者("u1", "alice", "member"), "o" * 32, "browser")
    assert result.工作階段權杖 == "s" * 32 and result.CSRF權杖 == "c" * 32
    assert "UPDATE web_sessions" in cursor.calls[0][0]
    assert "ON CONFLICT (session_token_hash) DO NOTHING RETURNING id" in cursor.calls[1][0]
    all_params = repr([call[1] for call in cursor.calls])
    assert "s" * 32 not in all_params and "c" * 32 not in all_params and "o" * 32 not in all_params
    assert len(cursor.calls[1][1][2]) == len(cursor.calls[1][1][3]) == 32
    assert all(cursor.calls[0][1][i].tzinfo is not None for i in (0, 2))
    assert all(cursor.calls[1][1][i].tzinfo is not None for i in (4, 5, 6))


def test_恢復缺csrf以row_lock與CAS輪替且replay失敗(monkeypatch):
    service = PostgreSQL網頁工作階段服務(
        postgres設定(), 時鐘=lambda: 1001.0, 密鑰工廠=lambda: "n" * 32, 有效秒數=60,
    )
    cursor = 假游標(fetch={0: row()}, rowcounts={1: 1})
    安裝(monkeypatch, service, cursor)
    result = service.恢復("s" * 32, None)
    assert result.CSRF權杖 == "n" * 32 and result.csrf已輪替
    assert "FOR UPDATE OF s" in cursor.calls[0][0]
    assert "csrf_token_hash=%s" in cursor.calls[1][0]
    assert "n" * 32 not in repr(cursor.calls[1][1])

    cursor = 假游標(fetch={0: row()}, rowcounts={1: 0})
    安裝(monkeypatch, service, cursor)
    with pytest.raises(網頁CSRF無效, match="^csrf_invalid$"):
        service.輪替("s" * 32, "c" * 32)


def test_expiry與disabled_owner拒絕且mutating路徑撤銷(monkeypatch):
    service = PostgreSQL網頁工作階段服務(postgres設定(), 時鐘=lambda: 1060.0, 有效秒數=60)
    cursor = 假游標(fetch={0: row(expires=1060.0)})
    安裝(monkeypatch, service, cursor)
    with pytest.raises(網頁未授權, match="^unauthorized$"):
        service.驗證身份("s" * 32)
    assert len(cursor.calls) == 1

    service = PostgreSQL網頁工作階段服務(postgres設定(), 時鐘=lambda: 1001.0, 有效秒數=60)
    cursor = 假游標(fetch={0: row(disabled=True)})
    安裝(monkeypatch, service, cursor)
    with pytest.raises(網頁未授權):
        service.恢復("s" * 32, "c" * 32)
    assert "revoked_at" in cursor.calls[1][0]


def test_管理授權role_first_member不消耗csrf_admin才CAS輪替(monkeypatch):
    service = PostgreSQL網頁工作階段服務(
        postgres設定(), 時鐘=lambda: 1001.0, 密鑰工廠=lambda: "n" * 32, 有效秒數=60,
    )
    cursor = 假游標(fetch={0: row(role="member")})
    安裝(monkeypatch, service, cursor)
    with pytest.raises(網頁管理權限不足, match="^admin_required$"):
        service.授權管理操作("s" * 32, "wrong")
    assert len(cursor.calls) == 1

    cursor = 假游標(fetch={0: row(role="admin")}, rowcounts={1: 1})
    安裝(monkeypatch, service, cursor)
    result = service.授權管理操作("s" * 32, "c" * 32)
    assert result.CSRF權杖 == "n" * 32 and result.csrf已輪替
    assert "FOR UPDATE OF s" in cursor.calls[0][0]
    assert cursor.calls[1][1][-1] == row(role="admin")["csrf_token_hash"]
    assert cursor.calls[1][1][1].tzinfo is not None


def test_SQL欄位與型別直接對齊0001_inventory且runtime無DDL():
    assert {"id", "user_id", "session_token_hash", "csrf_token_hash", "created_at", "expires_at", "last_seen_at", "revoked_at", "user_agent_hash"} <= _欄位("web_sessions")
    assert "roles" in _欄位("users")
    原始碼 = inspect.getsource(網頁模組)
    assert "CREATE TABLE" not in 原始碼 and "ALTER TABLE" not in 原始碼
    assert "u.roles_json" not in 原始碼
