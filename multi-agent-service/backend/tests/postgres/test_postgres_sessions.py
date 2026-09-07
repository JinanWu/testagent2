from __future__ import annotations

import ast
import inspect
import re
from contextlib import contextmanager
from datetime import datetime
from typing import Any

import pytest
from psycopg.types.json import Jsonb

from 繁中代理.交易儲存設定 import 交易儲存設定
import 繁中代理.PostgreSQL工作階段庫 as 模組
from 繁中代理.BigQuery工作階段庫 import BigQuery工作階段庫
from 繁中代理.工作階段庫 import 工作階段庫

設定 = 交易儲存設定("postgres", "postgresql:///db?host=/cloudsql/proj:region:inst", "proj:region:inst")

# Canonical 0001 inventory for every table this repository is allowed to use.
SCHEMA = {
    "sessions": {
        "id", "source", "user_id", "model", "model_config", "system_prompt",
        "parent_session_id", "title", "end_reason", "compressed_from_session_id",
        "prompt_tokens", "input_tokens", "output_tokens", "cache_read_tokens",
        "cache_write_tokens", "reasoning_tokens", "message_count", "tool_call_count",
        "api_call_count", "compression_count", "cwd", "billing_provider",
        "billing_base_url", "billing_mode", "estimated_cost_usd", "actual_cost_usd",
        "cost_status", "cost_source", "pricing_version", "handoff_state",
        "handoff_platform", "handoff_error", "rewind_count", "archived", "created_at",
        "updated_at", "started_at", "ended_at",
    },
    "messages": {
        "id", "session_id", "message_index", "role", "content", "content_json",
        "tool_call_id", "tool_calls", "tool_name", "token_count", "finish_reason",
        "reasoning", "reasoning_content", "reasoning_details", "codex_reasoning_items",
        "codex_message_items", "platform_message_id", "observed", "active",
        "created_at", "timestamp",
    },
    "session_usage_events": {
        "id", "session_id", "user_id", "model", "prompt_tokens", "input_tokens",
        "output_tokens", "cache_read_tokens", "cache_write_tokens", "reasoning_tokens",
        "estimated_cost_usd", "actual_cost_usd", "metadata", "created_at",
    },
    "compression_leases": {"session_id", "holder", "acquired_at", "expires_at"},
}


class Cursor:
    def __init__(self, rows=()):
        self.rows = list(rows)

    def fetchone(self):
        return self.rows.pop(0) if self.rows else None

    def fetchall(self):
        rows = self.rows
        self.rows = []
        return rows


class Connection:
    def __init__(self, replies):
        self.replies = list(replies)
        self.calls = []

    def execute(self, query: str, params: Any = None, *, prepare=None, binary=None):
        self.calls.append((query, params))
        return Cursor(self.replies.pop(0) if self.replies else ())


def install(monkeypatch, replies):
    conn = Connection(replies)

    @contextmanager
    def tx(config):
        assert config is 設定
        yield conn

    monkeypatch.setattr(模組, "交易連線", tx)
    return conn


def test_runtime不執行DDL且建立與讀取session(monkeypatch):
    conn = install(monkeypatch, [[], [{"id": "s", "user_id": "u"}], [{"id": "s", "user_id": "u"}]])
    repo = 模組.PostgreSQL工作階段庫(設定)
    assert repo.建立或讀取工作階段("s", user_id="u", model_config={"temperature": 0}) == "s"
    assert repo.讀取工作階段("s")["id"] == "s"
    sql = " ".join(q for q, _ in conn.calls).upper()
    assert "CREATE TABLE" not in sql and "ALTER TABLE" not in sql
    assert "FOR UPDATE" in sql
    insert_params = next(p for q, p in conn.calls if "INSERT INTO sessions" in q)
    assert isinstance(insert_params[6], Jsonb)
    assert all(not isinstance(value, float) for value in insert_params[-3:])
    assert all(isinstance(value, datetime) and value.tzinfo is not None for value in insert_params[-3:])


def test拒絕錯誤owner接管(monkeypatch):
    install(monkeypatch, [[{"user_id": "owner"}]])
    with pytest.raises(PermissionError):
        模組.PostgreSQL工作階段庫(設定).建立或讀取工作階段("s", user_id="other")


def test_公開runtime_api與兩個既有repository同簽名():
    methods = (
        "搜尋訊息", "取得錨點視圖", "搜尋工作階段", "讀取工作階段全文",
        "捲動工作階段訊息", "匯出工作階段JSONL",
    )
    for name in methods:
        postgres = getattr(模組.PostgreSQL工作階段庫, name)
        assert inspect.signature(postgres) == inspect.signature(getattr(工作階段庫, name))
        assert inspect.signature(postgres) == inspect.signature(getattr(BigQuery工作階段庫, name))
    for intentionally_forbidden in ("建立資料表", "建立FTS", "重建FTS", "補齊欄位", "套用WAL模式", "確保資料集與資料表"):
        assert not hasattr(模組.PostgreSQL工作階段庫, intentionally_forbidden)


def test_repository_sql表與寫入欄位均存在於0001_inventory():
    source = inspect.getsource(模組.PostgreSQL工作階段庫)
    assert "compression_locks" not in source
    assert "CREATE TABLE" not in source and "ALTER TABLE" not in source

    tree = ast.parse(source)
    sql_fragments = [
        node.value for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
        and re.search(r"\b(SELECT|INSERT|UPDATE|DELETE|FROM|JOIN)\b", node.value, re.I)
    ]
    sql = "\n".join(sql_fragments)
    tables: set[str] = set()
    for pattern in (
        r"\bFROM\s+([a-z_]+)",
        r"\bJOIN\s+([a-z_]+)",
        r"\bINTO\s+([a-z_]+)",
        r"\bUPDATE\s+([a-z_]+)",
        r"\bDELETE\s+FROM\s+([a-z_]+)",
    ):
        tables.update(re.findall(pattern, sql, re.I))
    tables -= {"SELECT", "INSERT", "UPDATE", "DELETE", "SET"}
    assert tables - {"lineage", "tips"} <= set(SCHEMA)
    assert {"sessions", "messages", "session_usage_events", "compression_leases"} <= tables

    for match in re.finditer(r"INSERT\s+INTO\s+([a-z_]+)\s*\(([^)]+)\)", sql, re.I | re.S):
        table, raw_columns = match.groups()
        columns = {column.strip() for column in raw_columns.split(",")}
        assert columns <= SCHEMA[table], (table, columns - SCHEMA[table])

    # 0001 messages.id is bigint identity: runtime must let PostgreSQL allocate it.
    message_insert = re.search(r"INSERT\s+INTO\s+messages\s*\(([^)]+)\)(.*?)RETURNING\s+id", sql, re.I | re.S)
    assert message_insert and "id" not in {c.strip() for c in message_insert.group(1).split(",")}

    usage_insert = re.search(r"INSERT\s+INTO\s+session_usage_events\s*\(([^)]+)\)", sql, re.I | re.S)
    assert usage_insert
    usage_columns = {c.strip() for c in usage_insert.group(1).split(",")}
    assert "metadata" in usage_columns
    assert not ({"billing_provider", "pricing_version"} & usage_columns)
