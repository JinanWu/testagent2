"""GOV G01 append-only、無損、去敏 SQLite 稽核服務測試。"""

import sqlite3
from concurrent.futures import ThreadPoolExecutor
from dataclasses import fields
from pathlib import Path

import pytest

from 繁中代理.發布介面 import AuditActorRef, AuditEvent, AuditMetadata, AuditResourceRef
from 繁中代理.發布介面.契約 import AuditSinkError
from 繁中代理.發布介面.治理.稽核 import SQLite稽核服務
from 繁中代理.發布介面.資料庫 import 初始化發布介面資料庫


def _事件(action="audit.detail.view", *, event_id="evt_1", metadata=None, invocation_id=None):
    return AuditEvent(
        event_id=event_id,
        occurred_at=123.5,
        action=action,
        outcome="success",
        actor=AuditActorRef("user", "user-1"),
        resource=AuditResourceRef("audit.event", "target-1"),
        request_id="req-1",
        endpoint_id=None,
        invocation_id=invocation_id,
        metadata=AuditMetadata(metadata),
    )


@pytest.fixture
def 資料庫(tmp_path):
    path = tmp_path / "published.sqlite"
    初始化發布介面資料庫(path)
    return path


@pytest.mark.parametrize("action", ["audit.detail.view", "credential.sensitive.reveal", "custom.audit"])
def test_管理動作無損附加完整欄位且clock不同於occurred_at(資料庫, action):
    calls = []

    def 時鐘():
        calls.append(1)
        return 456.25

    receipt = SQLite稽核服務(str(資料庫), 時鐘=時鐘).append_audit_event(
        _事件(action, metadata={"allowed": True, "count": 2})
    )
    with sqlite3.connect(資料庫) as connection:
        row = connection.execute(
            "SELECT id,event_id,occurred_at,action,outcome,actor_type,actor_id,"
            "resource_type,resource_id,request_id,endpoint_id,invocation_id,"
            "metadata_json,created_at FROM audit_events"
        ).fetchone()
    assert row == (
        "evt_1", "evt_1", 123.5, action, "success", "user", "user-1",
        "audit.event", "target-1", "req-1", None, None,
        '{"allowed":true,"count":2}', 456.25,
    )
    assert calls == [1]
    assert (receipt.event_id, receipt.committed, receipt.sequence) == ("evt_1", True, 1)


def test_有效去敏事件不把來源raw_marker寫入資料庫bytes(資料庫):
    marker = "DO_NOT_STORE_RAW_VALUE"
    raw_source = {"secret": marker, "allowed": True}
    event = _事件(metadata={"allowed": raw_source["allowed"]})
    SQLite稽核服務(str(資料庫), 時鐘=lambda: 456.0).append_audit_event(event)
    assert marker.encode() not in 資料庫.read_bytes()


@pytest.mark.parametrize("metadata", [
    {"secret": 1}, {"path": 1}, {"raw": 1}, {"nested": {"x": 1}},
    {"many": float("inf")}, {"value": "raw text"},
])
def test_metadata秘密巢狀非有限與文字由DTO拒絕(資料庫, metadata):
    with pytest.raises(Exception):
        _事件(metadata=metadata)
    assert sqlite3.connect(資料庫).execute("SELECT count(*) FROM audit_events").fetchone() == (0,)


def test_偽造event欄位或metadata固定公開失敗且不寫入(資料庫):
    cases = []
    invalid = _事件()
    object.__setattr__(invalid, "outcome", "forged")
    cases.append(invalid)
    invalid = _事件()
    forged = object.__new__(AuditMetadata)
    object.__setattr__(forged, "_資料", {"nested": {"marker": "SECRET"}})
    object.__setattr__(invalid, "metadata", forged)
    cases.append(invalid)
    for event in cases:
        with pytest.raises(AuditSinkError) as error:
            SQLite稽核服務(str(資料庫)).append_audit_event(event)
        assert type(error.value) is AuditSinkError
        assert error.value.args == ("稽核事件無法確認提交",)
        assert error.value.__cause__ is error.value.__context__ is None
    assert sqlite3.connect(資料庫).execute("SELECT count(*) FROM audit_events").fetchone() == (0,)


def test_duplicate_id固定失敗且原列不變(資料庫):
    sink = SQLite稽核服務(str(資料庫))
    sink.append_audit_event(_事件())
    with pytest.raises(AuditSinkError):
        sink.append_audit_event(_事件(action="custom.audit"))
    assert sqlite3.connect(資料庫).execute("SELECT action FROM audit_events").fetchall() == [("audit.detail.view",)]


@pytest.mark.parametrize("sql", [
    "UPDATE published_api_schema_migrations SET name='drift' WHERE version=2",
    "DROP TRIGGER audit_events_no_update",
    "DROP INDEX idx_audit_events_resource_time",
    "ALTER TABLE audit_events ADD COLUMN extra TEXT",
])
def test_v6_schema_ledger_table_index_trigger漂移全部失敗關閉(資料庫, sql):
    with sqlite3.connect(資料庫) as connection:
        connection.execute(sql)
    with pytest.raises(AuditSinkError):
        SQLite稽核服務(str(資料庫)).append_audit_event(_事件())
    assert sqlite3.connect(資料庫).execute("SELECT count(*) FROM audit_events").fetchone() == (0,)


def test_append_only_triggers拒絕直接更新與刪除(資料庫):
    SQLite稽核服務(str(資料庫)).append_audit_event(_事件())
    for sql in ("UPDATE audit_events SET action='other'", "DELETE FROM audit_events"):
        with sqlite3.connect(資料庫) as connection:
            with pytest.raises(sqlite3.IntegrityError, match="append only"):
                connection.execute(sql)


def test_constructor只接受exact_str與exact_function時鐘(資料庫):
    class StrSubclass(str):
        pass

    class Callable:
        def __call__(self):
            return 1.0

    for path in (資料庫, StrSubclass(str(資料庫)), "~/ambiguous.sqlite"):
        with pytest.raises(AuditSinkError):
            SQLite稽核服務(path)  # type: ignore[arg-type]
    sink = SQLite稽核服務(str(資料庫))
    for callback in (Callable(), sink.append_audit_event):
        with pytest.raises(AuditSinkError):
            SQLite稽核服務(str(資料庫), 時鐘=callback)  # type: ignore[arg-type]


def test_只接受既有非空一般sqlite且拒絕symlink(tmp_path, 資料庫):
    empty = tmp_path / "empty.sqlite"
    empty.touch()
    link = tmp_path / "link.sqlite"
    link.symlink_to(資料庫)
    for path in (tmp_path / "missing.sqlite", empty, link):
        with pytest.raises(AuditSinkError):
            SQLite稽核服務(str(path)).append_audit_event(_事件())


def test_event_subclass拒絕且clock零呼叫(資料庫):
    subclass = type("EventSubclass", (AuditEvent,), {})
    forged = object.__new__(subclass)
    event = _事件()
    for field in fields(AuditEvent):
        object.__setattr__(forged, field.name, getattr(event, field.name))
    calls = []
    with pytest.raises(AuditSinkError):
        SQLite稽核服務(str(資料庫), 時鐘=lambda: calls.append(1) or 1).append_audit_event(forged)
    assert calls == []


def test_並行append產生唯一sequence與完整列(資料庫):
    def append(index):
        return SQLite稽核服務(str(資料庫)).append_audit_event(_事件(event_id=f"evt-{index}")).sequence

    with ThreadPoolExecutor(max_workers=6) as pool:
        sequences = list(pool.map(append, range(12)))
    assert sorted(sequences) == list(range(1, 13))
