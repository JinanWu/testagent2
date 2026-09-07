"""PostgreSQL users/CLI auth repository 的無資料庫測試。"""
from __future__ import annotations

import inspect
import ast
import re
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

import pytest
from psycopg import Connection, Cursor
from psycopg.types.json import Jsonb

import 繁中代理.PostgreSQL使用者庫 as 使用者模組
from 繁中代理.PostgreSQL使用者庫 import PostgreSQL使用者庫
from 繁中代理.交易儲存設定 import 交易儲存設定
from 繁中代理.使用者 import 權限更新錯誤, 雜湊Token

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
        value = self.fetch.get(self.current)
        if isinstance(value, list):
            return value.pop(0) if value else None
        return value

    def fetchall(self):
        return list(self.fetch.get(self.current, []))

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        return None


class 假連線:
    def __init__(self, cursor):
        self._cursor = cursor

    def cursor(self, name='', *, binary=False, row_factory=None, scrollable=None, withhold=False):
        return self._cursor


def 安裝(monkeypatch, 模組, 游標):
    @contextmanager
    def transaction(設定):
        assert 設定 is 模組.設定
        yield 假連線(游標)
    monkeypatch.setattr("繁中代理.PostgreSQL連線.交易連線", transaction)


def test_fake_psycopg介面參數名稱與3_3_4一致():
    for 真實, 假造 in ((Connection.cursor, 假連線.cursor), (Cursor.execute, 假游標.execute),
                     (Cursor.fetchone, 假游標.fetchone), (Cursor.fetchall, 假游標.fetchall),
                     (Cursor.__enter__, 假游標.__enter__), (Cursor.__exit__, 假游標.__exit__)):
        a, b = inspect.signature(真實).parameters, inspect.signature(假造).parameters
        assert list(a) == list(b)
        assert [x.kind for x in a.values()] == [x.kind for x in b.values()]


def test_建立使用者同交易寫兩表且明文密碼不進SQL參數(monkeypatch):
    module = PostgreSQL使用者庫(postgres設定())
    row = {"id": "user-a", "username": "alice", "password_hash": "stored"}
    cursor = 假游標(fetch={2: row})
    安裝(monkeypatch, module, cursor)
    created = module.建立使用者(" alice ", "correct horse", roles=["admin"])
    assert created == row
    assert ["INSERT INTO users" in c[0] for c in cursor.calls] == [True, False, False]
    params = repr([c[1] for c in cursor.calls])
    assert "correct horse" not in params
    assert "pbkdf2_sha256$" in params
    assert all(isinstance(x, datetime) and x.tzinfo is not None for x in cursor.calls[0][1][-2:])
    assert isinstance(cursor.calls[0][1][4], Jsonb)
    assert all(isinstance(x, Jsonb) for x in cursor.calls[1][1][1:5])


def test_權限更新使用row_lock與舊值CAS(monkeypatch):
    module = PostgreSQL使用者庫(postgres設定())
    cursor = 假游標(fetch={0: {"id": "u1", "enabled_tools_json": '["read"]'}}, rowcounts={1: 1})
    安裝(monkeypatch, module, cursor)
    module.設定權限欄位("alice", "enabled_tools_json", ["write", "read"])
    assert "FOR UPDATE OF s" in cursor.calls[0][0]
    assert "IS NOT DISTINCT FROM" in cursor.calls[1][0]
    assert cursor.calls[1][1][0].obj == ["read", "write"]
    assert cursor.calls[1][1][3].obj == ["read"]
    assert cursor.calls[1][1][1].tzinfo is not None
    cursor = 假游標(fetch={0: {"id": "u1", "enabled_tools_json": '["read"]'}}, rowcounts={1: 0})
    安裝(monkeypatch, module, cursor)
    with pytest.raises(權限更新錯誤, match="^權限更新失敗$"):
        module.設定權限欄位("alice", "enabled_tools_json", ["write"])

    cursor = 假游標(fetch={0: {"id": "u1", "enabled_tools_json": '{malformed'}}, rowcounts={1: 1})
    安裝(monkeypatch, module, cursor)
    with pytest.raises(權限更新錯誤, match="^權限更新失敗$"):
        module.設定權限欄位("alice", "enabled_tools_json", ["write"])
    cursor = 假游標(fetch={0: None})
    安裝(monkeypatch, module, cursor)
    with pytest.raises(ValueError, match="^找不到使用者$"):
        module.設定權限欄位("missing", "enabled_tools_json", ["write"])


def test_CLI_token只保存雜湊且驗證更新使用時間(monkeypatch):
    module = PostgreSQL使用者庫(postgres設定())
    cursor = 假游標()
    安裝(monkeypatch, module, cursor)
    token = module.建立登入Token("u1", expires_at=0)
    params = cursor.calls[0][1]
    assert params[0] == 雜湊Token(token) and token not in repr(params)
    assert params[3] is None
    assert all(params[i].tzinfo is not None for i in (2, 4))

    cursor = 假游標(fetch={0: {"user_id": "u1", "expires_at": None}})
    安裝(monkeypatch, module, cursor)
    monkeypatch.setattr(module, "建立使用者上下文", lambda **kwargs: kwargs["user_id"])
    assert module.驗證登入Token(token) == "u1"
    assert cursor.calls[0][1] == (雜湊Token(token),)
    assert "last_used_at" in cursor.calls[1][0]
    assert cursor.calls[1][1][0].tzinfo is not None


def test_SQL欄位直接對齊0001_inventory且runtime無DDL():
    assert {"roles", "created_at", "updated_at"} <= _欄位("users")
    assert set(使用者模組._權限欄位.values()) <= _欄位("user_settings")
    assert {"token_hash", "expires_at", "revoked_at"} <= _欄位("auth_sessions")
    原始碼 = inspect.getsource(使用者模組)
    assert "CREATE TABLE" not in 原始碼 and "ALTER TABLE" not in 原始碼
    assert not re.search(r"(?:u|s)\.(?:roles_json|enabled_tools_json|enabled_skills_json|skill_roots_json|allowed_workdirs_json|settings_json)(?!\s+AS)", 原始碼)
    assert "INSERT INTO users(id,username,display_name,password_hash,auth_provider,roles_json" not in 原始碼
    assert "INSERT INTO user_settings(user_id,enabled_tools_json" not in 原始碼
