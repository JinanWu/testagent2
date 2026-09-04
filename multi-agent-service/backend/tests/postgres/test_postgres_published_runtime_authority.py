"""W2 PostgreSQL Published history 與 runtime authority 因果測試。

無 live DSN 時，使用會模擬 parent-row transaction lock 的 strict server-semantic fake，
並以 pglast 驗證實際送出的 PostgreSQL SQL。Controller 必須另以
TESTAGENT2_POSTGRES_TEST_DSN 對 exact Alembic head 執行 live gate。
"""
from __future__ import annotations

import hashlib
import json
import os
import threading
import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

import pytest

from 繁中代理.交易儲存設定 import 交易儲存設定
from 繁中代理.發布介面.呼叫 import PostgreSQLPublished工作階段 as 歷史模組
from 繁中代理.發布介面.呼叫.Published工作階段 import Published工作階段錯誤
from 繁中代理.發布介面.執行期 import PostgreSQL快照儲存庫 as 快照模組
from 繁中代理.發布介面.執行期.快照儲存庫 import 發布快照儲存庫錯誤


設定 = 交易儲存設定(
    "postgres", "postgresql://runtime:***@/app?host=/cloudsql/p:r:i", "p:r:i", 1, 4, 5,
)


class _Result:
    def __init__(self, *, row=None, rows=()):
        self._row = row
        self._rows = list(rows)

    def fetchone(self):
        return self._row

    def fetchall(self):
        return list(self._rows)


class _HistoryAuthority:
    """只接受合法 parent row lock，並模擬 READ COMMITTED commit visibility。"""

    def __init__(self):
        self.lock = threading.Lock()
        self.rows: list[dict[str, object]] = []
        self.sql: list[str] = []

    @contextmanager
    def transaction(self, _settings) -> Iterator["_HistoryConnection"]:
        connection = _HistoryConnection(self)
        try:
            yield connection
            if connection.pending is not None:
                self.rows.append(connection.pending)
        finally:
            if connection.locked:
                self.lock.release()


class _HistoryConnection:
    def __init__(self, authority: _HistoryAuthority):
        self.authority = authority
        self.locked = False
        self.pending: dict[str, object] | None = None

    def execute(self, sql, params=()):
        from pglast import parse_sql

        parse_sql(sql.replace("%s", "NULL"))
        normalized = " ".join(sql.lower().split())
        self.authority.sql.append(normalized)
        if normalized.startswith("select id from published_endpoints"):
            assert normalized.endswith("for update")
            self.authority.lock.acquire()
            self.locked = True
            return _Result(row={"id": params[0]})
        if "coalesce(max(sequence_number),0)" in normalized:
            assert "for update" not in normalized
            matching = [
                row for row in self.authority.rows
                if tuple(row[key] for key in ("endpoint_id", "service_account_id", "session_id"))
                == tuple(params)
            ]
            current = max((int(row["sequence_number"]) for row in matching), default=0)
            return _Result(row={
                "count": len(matching),
                "minimum": min((int(row["sequence_number"]) for row in matching), default=None),
                "n": current,
            })
        if normalized.startswith("insert into published_session_turn_pairs"):
            self.pending = dict(zip(
                ("endpoint_id", "service_account_id", "session_id", "sequence_number",
                 "endpoint_version_id", "user_message", "assistant_message",
                 "pair_size_bytes", "token_count"),
                params,
            ))
            return _Result()
        raise AssertionError(sql)


def _message(role: str, content: str = "x") -> dict[str, object]:
    return {"role": role, "content": content}


def test_history_SQL可由PostgreSQL解析且同expected_sequence並行只有一個winner(monkeypatch):
    pytest.importorskip("pglast")
    authority = _HistoryAuthority()
    monkeypatch.setattr(歷史模組, "交易連線", authority.transaction)
    repository = 歷史模組.PostgreSQLPublished工作階段儲存庫(設定)
    barrier = threading.Barrier(3)
    outcomes: list[str] = []

    def append() -> None:
        barrier.wait()
        try:
            repository.附加成功對話組(
                "ep-1", "sa-1", "session-1", "ver-1",
                _message("user"), _message("assistant"), 2, expected_sequence=1,
            )
            outcomes.append("ok")
        except Published工作階段錯誤:
            outcomes.append("conflict")

    threads = [threading.Thread(target=append) for _ in range(2)]
    for thread in threads:
        thread.start()
    barrier.wait()
    for thread in threads:
        thread.join(timeout=5)
        assert not thread.is_alive()

    assert sorted(outcomes) == ["conflict", "ok"]
    assert [row["sequence_number"] for row in authority.rows] == [1]
    with pytest.raises(Published工作階段錯誤):
        repository.附加成功對話組(
            "ep-1", "sa-1", "session-1", "ver-1",
            _message("user", "gap"), _message("assistant", "gap"), 2,
            expected_sequence=3,
        )
    assert [row["sequence_number"] for row in authority.rows] == [1]
    assert repository.附加成功對話組(
        "ep-1", "sa-1", "session-1", "ver-1",
        _message("user", "next"), _message("assistant", "next"), 2,
        expected_sequence=2,
    ) == 2
    assert [row["sequence_number"] for row in authority.rows] == [1, 2]
    assert any(sql.endswith("for update") for sql in authority.sql)
    assert all(
        "for update" not in sql
        for sql in authority.sql if "max(sequence_number)" in sql
    )


def test_history_dict_row刻意亂序仍按欄名重建(monkeypatch):
    user = _message("user", "hello")
    assistant = _message("assistant", "world")
    user_json = json.dumps(user, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    assistant_json = json.dumps(assistant, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    row = dict([
        ("token_count", 4), ("assistant_message", assistant_json),
        ("sequence_number", 1), ("pair_size_bytes", len(user_json.encode()) + len(assistant_json.encode())),
        ("user_message", user_json), ("endpoint_version_id", "ver-1"),
    ])

    class Connection:
        def execute(self, *_args, **_kwargs):
            return _Result(rows=[row])

    @contextmanager
    def transaction(_settings):
        yield Connection()

    monkeypatch.setattr(歷史模組, "交易連線", transaction)
    history = 歷史模組.PostgreSQLPublished工作階段儲存庫(設定).讀取成功歷史(
        "ep-1", "sa-1", "session-1",
    )
    assert history[0].sequence_number == 1
    assert history[0].endpoint_version_id == "ver-1"
    assert history[0].user_message == user
    assert history[0].assistant_message == assistant


def _canonical(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _tool_digest(name, revision, description, parameters_json):
    projection = {
        "name": name, "revision": revision, "description": description,
        "parameters": json.loads(parameters_json),
    }
    return hashlib.sha256(_canonical(projection).encode()).hexdigest()


def _snapshot_row(**changes):
    now = datetime.now(timezone.utc)
    tools = {
        "alpha": {"revision": "alpha@v1", "description": "甲", "parameters": {"type": "string"}},
        "zeta": {"revision": "zeta@v1", "description": "乙", "parameters": {"type": "object"}},
    }
    model = {
        "provider": "fake", "model": "m", "temperature": 0.0, "max_tokens": 10,
        "timeout_seconds": 3.0, "structured_output": False, "schema_retry_count": 1,
    }
    values = {
        "id": "ver-1", "endpoint_id": "ep-1", "service_account_id": "sa-1",
        "status": "active", "disabled_at": None, "system_prompt": "system",
        "allowed_tools": ["zeta", "alpha"], "tool_schema_snapshot": tools,
        "tool_runtime_revision": "release-1", "model_config_snapshot": model,
        "response_schema": None, "skill_bundle_manifest": {},
        "manifest_reference": "bundles/v1/bundle-1/manifest.json#generation=7", "manifest_digest": "a" * 64,
        "bundle_hash": "b" * 64, "state": "published", "bundle_id": "bundle-1",
        "total_bytes": 12, "published_at": now, "reconciled_at": None,
    }
    values.update(changes)
    # psycopg dict_row insertion order is not a DTO contract.
    return dict(reversed(list(values.items())))


def _install_snapshot_rows(monkeypatch, rows):
    queue = list(rows)

    class Connection:
        def execute(self, sql, _params=()):
            from pglast import parse_sql
            parse_sql(sql.replace("%s", "NULL"))
            return _Result(rows=[queue.pop(0)])

    @contextmanager
    def transaction(_settings):
        yield Connection()

    monkeypatch.setattr(快照模組, "交易連線", transaction)


def test_snapshot亂序dict_row三consumer維持identity_hash_permission與完整工具(monkeypatch):
    pytest.importorskip("pglast")
    rows = [_snapshot_row(), _snapshot_row(), _snapshot_row()]
    _install_snapshot_rows(monkeypatch, rows)
    repository = 快照模組.PostgreSQL發布快照儲存庫(設定, _tool_digest)

    snapshot = repository.取得發布執行快照("ver-1")
    context = repository.載入服務帳戶上下文("sa-1", "ver-1", "endpoint_version_snapshot")
    locator = repository.取得技能套件定位("ver-1")
    expected_permission = hashlib.sha256(_canonical({
        "allowed_tools": ["zeta", "alpha"], "skill_bundle_hash": "b" * 64,
        "tool_handler_release": "release-1",
    }).encode()).hexdigest()

    assert [tool.name for tool in snapshot.tool_snapshot] == ["zeta", "alpha"]
    assert [tool.revision for tool in snapshot.tool_snapshot] == ["zeta@v1", "alpha@v1"]
    assert snapshot.permission_snapshot_digest == context.permission_snapshot_digest == expected_permission
    assert (snapshot.version_id, context.endpoint_version_id, locator.version_id) == ("ver-1",) * 3
    assert snapshot.skill_bundle_hash == context.skill_bundle_hash == locator.bundle_hash == "b" * 64
    assert snapshot.service_account_id == context.service_account_id == "sa-1"


@pytest.mark.parametrize("change", [
    {"status": "disabled"},
    {"disabled_at": datetime.now(timezone.utc)},
    {"state": "staging"},
    {"state": "published", "reconciled_at": datetime.now(timezone.utc)},
    {"state": "reconciled", "reconciled_at": None},
    {"state": "reconciled", "published_at": datetime.now(timezone.utc),
     "reconciled_at": datetime.now(timezone.utc) - timedelta(days=1)},
    {"id": "ver-other"},
    {"manifest_digest": "A" * 64},
    {"bundle_hash": "short"},
    {"manifest_reference": "wrong/manifest.json"},
    {"allowed_tools": ["zeta", "zeta"]},
    {"tool_schema_snapshot": {"zeta": {"revision": "zeta@v1", "description": "乙", "parameters": {}}}},
])
def test_snapshot_revocation與schema_invariants三consumer皆fail_closed(monkeypatch, change):
    pytest.importorskip("pglast")
    _install_snapshot_rows(monkeypatch, [_snapshot_row(**change) for _ in range(3)])
    repository = 快照模組.PostgreSQL發布快照儲存庫(設定, _tool_digest)
    calls: tuple[Callable[[], object], ...] = (
        lambda: repository.取得發布執行快照("ver-1"),
        lambda: repository.載入服務帳戶上下文("sa-1", "ver-1", "endpoint_version_snapshot"),
        lambda: repository.取得技能套件定位("ver-1"),
    )
    for call in calls:
        with pytest.raises(發布快照儲存庫錯誤, match="^發布快照不可用$"):
            call()


@pytest.mark.skipif(not os.getenv("TESTAGENT2_POSTGRES_TEST_DSN"), reason="controller live-PG gate: TESTAGENT2_POSTGRES_TEST_DSN 未設定")
def test_live_PostgreSQL_exact_head_SQL與concurrent_one_winner(monkeypatch):
    """只對 disposable exact-head PostgreSQL 執行；fixture 全部使用唯一 identity 並清理。"""
    import psycopg
    from psycopg.rows import dict_row

    dsn = os.environ["TESTAGENT2_POSTGRES_TEST_DSN"]
    suffix = uuid.uuid4().hex
    user_id, account_id, endpoint_id = f"u-{suffix}", f"sa-{suffix}", f"ep-{suffix}"
    version_id, session_id = f"ver-{suffix}", f"session-{suffix}"
    with psycopg.connect(dsn, row_factory=dict_row) as connection:
        revision = connection.execute("SELECT version_num FROM alembic_version").fetchone()
        assert revision == {"version_num": "0001_full_product_schema"}
        connection.execute("INSERT INTO users(id,username) VALUES(%s,%s)", (user_id, f"user-{suffix}"))
        connection.execute("INSERT INTO service_accounts(id,owner_user_id) VALUES(%s,%s)", (account_id, user_id))
        connection.execute(
            "INSERT INTO published_endpoints(id,owner_user_id,service_account_id,slug,status) VALUES(%s,%s,%s,%s,'active')",
            (endpoint_id, user_id, account_id, f"slug-{suffix}"),
        )
        connection.execute(
            "INSERT INTO published_endpoint_versions(id,endpoint_id,version_number,original_requirement_text,system_prompt,allowed_skills,allowed_tools,tool_schema_snapshot,tool_runtime_revision,model_config_snapshot,retry_policy,skill_bundle_manifest,input_schema,response_schema,schema_changed,created_by_user_id) "
            "VALUES(%s,%s,1,'req','prompt','[]'::jsonb,'[]'::jsonb,'{}'::jsonb,'release-1','{}'::jsonb,'{}'::jsonb,'{}'::jsonb,NULL,'null'::jsonb,false,%s)",
            (version_id, endpoint_id, user_id),
        )

    @contextmanager
    def transaction(_settings):
        with psycopg.connect(dsn, row_factory=dict_row) as connection:
            yield connection

    monkeypatch.setattr(歷史模組, "交易連線", transaction)
    repository = 歷史模組.PostgreSQLPublished工作階段儲存庫(設定)
    barrier = threading.Barrier(3)
    outcomes: list[str] = []

    def append():
        barrier.wait()
        try:
            repository.附加成功對話組(
                endpoint_id, account_id, session_id, version_id,
                _message("user"), _message("assistant"), 2, expected_sequence=1,
            )
            outcomes.append("ok")
        except Published工作階段錯誤:
            outcomes.append("conflict")

    threads = [threading.Thread(target=append) for _ in range(2)]
    try:
        for thread in threads:
            thread.start()
        barrier.wait()
        for thread in threads:
            thread.join(timeout=10)
            assert not thread.is_alive()
        assert sorted(outcomes) == ["conflict", "ok"]
        assert [pair.sequence_number for pair in repository.讀取成功歷史(endpoint_id, account_id, session_id)] == [1]
    finally:
        with psycopg.connect(dsn) as connection:
            connection.execute("DELETE FROM published_endpoint_versions WHERE id=%s", (version_id,))
            connection.execute("DELETE FROM published_endpoints WHERE id=%s", (endpoint_id,))
            connection.execute("DELETE FROM service_accounts WHERE id=%s", (account_id,))
            connection.execute("DELETE FROM users WHERE id=%s", (user_id,))
