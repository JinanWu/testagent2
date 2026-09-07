"""PostgreSQL canonical consumers 的因果測試；禁止用 adapter 存在性冒充接線。"""
from __future__ import annotations

import asyncio
import hashlib
import json
from contextlib import contextmanager

import pytest

from 繁中代理.交易儲存設定 import 交易儲存設定
from 繁中代理.發布介面 import PostgreSQL資源 as 資源模組
from 繁中代理.發布介面.呼叫 import PostgreSQLPublished工作階段 as 歷史模組
from 繁中代理.發布介面.執行期 import PostgreSQL快照儲存庫 as 快照模組
from 繁中代理.發布介面.執行期.執行器 import 發布執行快照
from 繁中代理.發布介面.執行期.服務帳戶 import ServiceAccountContext
from 繁中代理.發布介面.技能套件.載入器 import 技能套件定位


設定 = 交易儲存設定(
    "postgres",
    "postgresql://runtime:pw@/app?host=/cloudsql/p:r:i",
    "p:r:i",
    1,
    2,
    5,
)


class _ConnectionContext:
    def __init__(self, connection):
        self.connection_value = connection
    def __enter__(self):
        return self.connection_value
    def __exit__(self, *_):
        return False


class _Pool:
    def __init__(self, connection):
        self.connection_value = connection
    def connection(self):
        return _ConnectionContext(self.connection_value)


def test_pool_owner先readiness後發布且失敗必關閉(monkeypatch):
    事件 = []
    pool = _Pool(object())
    monkeypatch.setattr(資源模組.PostgreSQL連線, "啟動共用連線池", lambda value: (事件.append("open"), pool)[1])
    monkeypatch.setattr(資源模組, "檢查PostgreSQL就緒", lambda connection: 事件.append("ready"))
    monkeypatch.setattr(資源模組.PostgreSQL連線, "關閉共用連線池", lambda: 事件.append("close"))
    resource = asyncio.run(資源模組.建立PostgreSQL資源(設定))
    assert 事件 == ["open", "ready"]
    asyncio.run(resource.關閉())
    asyncio.run(resource.關閉())
    assert 事件 == ["open", "ready", "close"]

    事件.clear()
    monkeypatch.setattr(資源模組, "檢查PostgreSQL就緒", lambda _: (_ for _ in ()).throw(RuntimeError("drift")))
    with pytest.raises(RuntimeError, match="drift"):
        asyncio.run(資源模組.建立PostgreSQL資源(設定))
    assert 事件 == ["open", "close"]


class _Result:
    def __init__(self, *, rows=(), row=None):
        self.rows = list(rows)
        self.row = row
    def fetchall(self):
        return list(self.rows)
    def fetchone(self):
        return self.row


class _SQLConnection:
    def __init__(self, results):
        self.results = list(results)
        self.sql = []
    def execute(self, sql, params=()):
        self.sql.append((sql, params))
        return self.results.pop(0)


def _transaction(monkeypatch, module, connection):
    @contextmanager
    def transaction(value):
        assert value is 設定
        yield connection
    monkeypatch.setattr(module, "交易連線", transaction)


def test_published_history_consumer重建exact_DTO且只查PostgreSQL(monkeypatch):
    user = json.dumps({"role": "user", "content": "hello"}, separators=(",", ":"))
    assistant = json.dumps({"role": "assistant", "content": "world"}, separators=(",", ":"))
    size = len(user.encode()) + len(assistant.encode())
    connection = _SQLConnection([_Result(rows=[{
        "sequence_number": 1, "endpoint_version_id": "ver-1",
        "user_message": user, "assistant_message": assistant,
        "pair_size_bytes": size, "token_count": 4,
    }])])
    _transaction(monkeypatch, 歷史模組, connection)
    history = 歷史模組.PostgreSQLPublished工作階段儲存庫(設定).讀取成功歷史("ep", "sa", "session")
    assert len(history) == 1
    assert (history[0].sequence_number, history[0].endpoint_version_id) == (1, "ver-1")
    assert history[0].user_message == {"role": "user", "content": "hello"}
    assert "published_session_turn_pairs" in connection.sql[0][0]


def _snapshot_row():
    model = {"provider": "fake", "model": "m", "temperature": 0.0,
             "max_tokens": 10, "timeout_seconds": 3.0,
             "structured_output": False, "schema_retry_count": 1}
    tools = {"lookup": {"revision": "rev-1", "description": "desc", "parameters": {"type": "object"}}}
    return {
        "id": "ver-1", "endpoint_id": "ep-1", "service_account_id": "sa-1",
        "status": "active", "disabled_at": None, "system_prompt": "system",
        "allowed_tools": ["lookup"], "tool_schema_snapshot": tools,
        "tool_runtime_revision": "release-1", "model_config_snapshot": model,
        "response_schema": None,
        "manifest_reference": "bundles/v1/bundle-1/manifest.json#generation=7",
        "manifest_digest": "a" * 64, "bundle_hash": "b" * 64,
        "state": "published", "bundle_id": "bundle-1", "total_bytes": 12,
    }


def _tool_digest(name, revision, description, parameters_json):
    payload = {"name": name, "revision": revision, "description": description,
               "parameters": json.loads(parameters_json)}
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode()).hexdigest()


def test_snapshot_consumer三介面共用authority並保留工具(monkeypatch):
    connection = _SQLConnection([_Result(rows=[_snapshot_row()]), _Result(rows=[_snapshot_row()]), _Result(rows=[_snapshot_row()])])
    _transaction(monkeypatch, 快照模組, connection)
    repo = 快照模組.PostgreSQL發布快照儲存庫(設定, _tool_digest)
    snapshot = repo.取得發布執行快照("ver-1")
    context = repo.載入服務帳戶上下文("sa-1", "ver-1", "endpoint_version_snapshot")
    locator = repo.取得技能套件定位("ver-1")
    assert type(snapshot) is 發布執行快照
    assert tuple(tool.name for tool in snapshot.tool_snapshot) == ("lookup",)
    assert type(context) is ServiceAccountContext and context.allowed_tools == ("lookup",)
    assert context.permission_snapshot_digest == snapshot.permission_snapshot_digest
    assert type(locator) is 技能套件定位 and locator.bundle_id == "bundle-1"
