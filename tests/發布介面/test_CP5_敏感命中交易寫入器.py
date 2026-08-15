"""A21-03 caller-owned transaction 內的敏感命中與稽核 writer 契約。"""

from __future__ import annotations

from dataclasses import FrozenInstanceError
import json
import sqlite3

import pytest

from 繁中代理.發布介面.呼叫.敏感稽核 import (
    SQLite敏感稽核儲存庫,
    敏感命中交易收據,
    敏感操作模式,
    敏感稽核錯誤,
    建立呼叫來源族,
    建立工具成功來源族,
    建立完成來源族,
)
from 繁中代理.發布介面.呼叫.擷取政策 import (
    呼叫擷取命令,
    擷取階段,
    敏感偵測擷取結果,
    目標敏感命中,
)
from 繁中代理.發布介面.資料庫 import 初始化發布介面資料庫


def _建立資料庫(tmp_path):
    路徑 = tmp_path / "writer.sqlite3"
    初始化發布介面資料庫(路徑)
    with sqlite3.connect(路徑) as 連線:
        連線.execute("PRAGMA foreign_keys=ON")
        連線.execute("INSERT INTO service_accounts VALUES('svc-main',1,NULL)")
        連線.execute(
            "INSERT INTO published_endpoints(id,owner_user_id,service_account_id,slug,status,"
            "current_version_id,created_at,updated_at) "
            "VALUES('ep-main','owner-main','svc-main','writer','active',NULL,0,0)"
        )
        連線.execute(
            "INSERT INTO published_endpoint_versions VALUES "
            "('ver-main','ep-main',1,'safe','safe','[]','[]','{}','rev-main',"
            "'{}','{}','{}',NULL,'{}',0,'owner-main',0)"
        )
        連線.execute(
            "UPDATE published_endpoints SET current_version_id='ver-main' WHERE id='ep-main'"
        )
        連線.execute(
            "INSERT INTO endpoint_invocations(id,endpoint_id,endpoint_version_id,request_id,status,"
            "input_json,created_at) VALUES('inv-main','ep-main','ver-main','req-main','running','{}',0)"
        )
        連線.execute(
            "INSERT INTO endpoint_tool_calls(id,invocation_id,sequence_number,tool_name,"
            "arguments_json,outcome,result_json,created_at) "
            "VALUES('tool-main','inv-main',1,'safe_tool','{}','success','{}',0)"
        )
    return 路徑


def _結果(*命中們):
    命令 = 呼叫擷取命令(擷取階段.AUTHENTICATED, "user", "{}", None, None, None)
    return 敏感偵測擷取結果(
        命令,
        tuple(命中們),
        ("sensitive_data_detected",) if 命中們 else (),
    )


def _三命中():
    return _結果(
        目標敏感命中("input", "email", "/a", 1, 3),
        目標敏感命中("metadata", "phone", "/b", 2, 5),
        目標敏感命中("tool_result", "tw_national_id_format", "/c", 3, 7),
    )


def _呼叫命中():
    return _結果(
        目標敏感命中("input", "email", "/a", 1, 3),
        目標敏感命中("metadata", "phone", "/b", 2, 5),
    )


def _工具命中():
    return _結果(目標敏感命中("tool_result", "tw_national_id_format", "/c", 3, 7))


def _開交易(路徑, *, 外鍵=True):
    連線 = sqlite3.connect(路徑, isolation_level=None)
    if 外鍵:
        連線.execute("PRAGMA foreign_keys=ON")
    連線.execute("BEGIN IMMEDIATE")
    return 連線


def _writer(路徑, *, 時鐘=lambda: 17, audits=None, hits=None):
    return SQLite敏感稽核儲存庫(
        路徑,
        時鐘=時鐘,
        識別碼工廠=(audits or iter(("audit-1", "audit-2", "audit-3")).__next__),
        命中識別碼工廠=(hits or iter(("hit-1", "hit-2", "hit-3")).__next__),
    )


def _寫(writer, 連線, 結果, *, family=None, mode=敏感操作模式.FIRST_WRITE):
    """以sealed operation seam寫入，同時讓既有node保留原驗證焦點。"""
    return writer.寫入呼叫交易(
        連線, mode, 建立呼叫來源族() if family is None else family,
        結果, "inv-main", "ep-main",
    )


def test_caller交易內逐筆原子寫audit_hit且回安全不可變收據(tmp_path):
    路徑 = _建立資料庫(tmp_path)
    連線 = _開交易(路徑)
    try:
        writer = _writer(路徑)
        呼叫收據 = _寫(writer, 連線, _呼叫命中())
        工具收據 = _寫(
            writer, 連線, _工具命中(), family=建立工具成功來源族("tool-main"),
        )
        assert 連線.in_transaction
        assert type(呼叫收據) is type(工具收據) is 敏感命中交易收據
        assert (
            呼叫收據.呼叫識別碼, 呼叫收據.命中數 + 工具收據.命中數,
            呼叫收據.稽核識別碼們 + 工具收據.稽核識別碼們,
            呼叫收據.命中識別碼們 + 工具收據.命中識別碼們,
        ) == (
            "inv-main", 3, ("audit-1", "audit-2", "audit-3"),
            ("hit-1", "hit-2", "hit-3"),
        )
        with pytest.raises(FrozenInstanceError):
            呼叫收據.命中數 = 4
        with pytest.raises(TypeError):
            敏感命中交易收據("inv-main", 3, (), ())
        assert not any(hasattr(呼叫收據, 名稱) for 名稱 in (
            "json_path", "開始", "結束", "payload", "request_id", "session_id", "message_id"
        ))

        稽核列 = 連線.execute(
            "SELECT id,occurred_at,request_id,endpoint_id,invocation_id,metadata_json,created_at "
            "FROM audit_events ORDER BY rowid"
        ).fetchall()
        命中列 = 連線.execute(
            "SELECT id,invocation_id,tool_call_id,target_type,detector_type,json_path,"
            "start_offset,end_offset,audit_event_id,detected_at "
            "FROM invocation_sensitive_hits ORDER BY rowid"
        ).fetchall()
        assert [列[:5] for 列 in 稽核列] == [
            (f"audit-{索引}", 17, None, "ep-main", "inv-main") for 索引 in range(1, 4)
        ]
        assert [列[:5] + 列[8:] for 列 in 命中列] == [
            ("hit-1", "inv-main", None, "input", "email", "audit-1", 17),
            ("hit-2", "inv-main", None, "metadata", "phone", "audit-2", 17),
            ("hit-3", "inv-main", "tool-main", "tool_result", "tw_national_id_format", "audit-3", 17),
        ]
        assert all(稽核[1] == 命中[9] == 稽核[6] for 稽核, 命中 in zip(稽核列, 命中列))
        assert all(set(json.loads(列[5])) == {
            "warning_code", "target", "detector_type", "json_path", "start", "end"
        } for 列 in 稽核列)
        assert all(not ({"raw", "value", "snippet", "hash"} & set(json.loads(列[5])))
                   for 列 in 稽核列)

        with sqlite3.connect(路徑) as 旁觀者:
            assert 旁觀者.execute("SELECT count(*) FROM audit_events").fetchone() == (0,)
            assert 旁觀者.execute(
                "SELECT count(*) FROM invocation_sensitive_hits"
            ).fetchone() == (0,)
    finally:
        連線.rollback()
        連線.close()


def test_caller_rollback同時移除audit與hit且writer不接管連線(tmp_path):
    路徑 = _建立資料庫(tmp_path)
    連線 = _開交易(路徑)
    writer = _writer(路徑)
    _寫(writer, 連線, _呼叫命中())
    _寫(writer, 連線, _工具命中(), family=建立工具成功來源族("tool-main"))
    連線.rollback()
    assert not 連線.in_transaction
    assert 連線.execute("SELECT count(*) FROM audit_events").fetchone() == (0,)
    assert 連線.execute("SELECT count(*) FROM invocation_sensitive_hits").fetchone() == (0,)
    連線.close()


@pytest.mark.parametrize("情境", ["not_transaction", "foreign_keys_off"])
def test_exact_connection必須已在caller交易且啟用外鍵(tmp_path, 情境):
    路徑 = _建立資料庫(tmp_path)
    次數 = [0]
    def 不可呼叫():
        次數[0] += 1
        raise AssertionError
    連線 = sqlite3.connect(路徑, isolation_level=None)
    if 情境 == "not_transaction":
        連線.execute("PRAGMA foreign_keys=ON")
    else:
        連線.execute("BEGIN IMMEDIATE")
    try:
        with pytest.raises(敏感稽核錯誤, match="敏感命中交易寫入失敗"):
            _寫(
                _writer(路徑, 時鐘=不可呼叫, audits=不可呼叫, hits=不可呼叫),
                連線, _呼叫命中(),
            )
        assert 次數 == [0]
    finally:
        if 連線.in_transaction:
            連線.rollback()
        連線.close()


def test_tool_target要求sealed_family且outcome必須吻合(tmp_path):
    路徑 = _建立資料庫(tmp_path)
    for family, 結果 in (
        (建立呼叫來源族(), _工具命中()),
        (建立工具成功來源族("tool-main"), _呼叫命中()),
        (建立工具成功來源族("tool-other"), _工具命中()),
    ):
        連線 = _開交易(路徑)
        try:
            with pytest.raises(敏感稽核錯誤):
                _寫(_writer(路徑), 連線, 結果, family=family)
            assert 連線.execute("SELECT count(*) FROM audit_events").fetchone() == (0,)
            assert 連線.execute(
                "SELECT count(*) FROM invocation_sensitive_hits"
            ).fetchone() == (0,)
        finally:
            連線.rollback()
            連線.close()


def test_zero_hits不碰factory或time且不寫入(tmp_path):
    路徑 = _建立資料庫(tmp_path)
    次數 = [0]
    def 不可呼叫():
        次數[0] += 1
        raise AssertionError
    連線 = _開交易(路徑)
    try:
        收據 = _寫(
            _writer(路徑, 時鐘=不可呼叫, audits=不可呼叫, hits=不可呼叫),
            連線, _結果(),
        )
        assert (收據.呼叫識別碼, 收據.命中數, 收據.稽核識別碼們,
                收據.命中識別碼們) == ("inv-main", 0, (), ())
        assert 次數 == [0]
        assert 連線.execute("SELECT count(*) FROM audit_events").fetchone() == (0,)
        assert 連線.execute("SELECT count(*) FROM invocation_sensitive_hits").fetchone() == (0,)
    finally:
        連線.rollback()
        連線.close()


def test_same_content_replay驗證exact_set後回同收據且不重複(tmp_path):
    路徑 = _建立資料庫(tmp_path)
    連線 = _開交易(路徑)
    try:
        writer = _writer(路徑)
        呼叫收據 = _寫(writer, 連線, _呼叫命中())
        工具收據 = _寫(
            writer, 連線, _工具命中(), family=建立工具成功來源族("tool-main"),
        )
        連線.commit()
    finally:
        連線.close()

    次數 = [0]
    def 不可呼叫():
        次數[0] += 1
        raise AssertionError
    連線 = _開交易(路徑)
    try:
        writer = _writer(路徑, 時鐘=不可呼叫, audits=不可呼叫, hits=不可呼叫)
        呼叫replay = _寫(
            writer, 連線, _呼叫命中(), mode=敏感操作模式.REPLAY,
        )
        工具replay = _寫(
            writer, 連線, _工具命中(), family=建立工具成功來源族("tool-main"),
            mode=敏感操作模式.REPLAY,
        )
        assert 呼叫replay == 呼叫收據 and 呼叫replay is not 呼叫收據
        assert 工具replay == 工具收據 and 工具replay is not 工具收據 and 次數 == [0]
        assert 連線.execute("SELECT count(*) FROM audit_events").fetchone() == (3,)
        assert 連線.execute("SELECT count(*) FROM invocation_sensitive_hits").fetchone() == (3,)
    finally:
        連線.rollback()
        連線.close()


def test_same_source_different_hit_set拒絕且不碰factory或time(tmp_path):
    路徑 = _建立資料庫(tmp_path)
    連線 = _開交易(路徑)
    _寫(
        _writer(路徑), 連線,
        _結果(目標敏感命中("input", "email", "/a", 1, 3)),
    )
    連線.commit()
    連線.execute("BEGIN IMMEDIATE")
    次數 = [0]
    def 不可呼叫():
        次數[0] += 1
        raise AssertionError
    try:
        with pytest.raises(敏感稽核錯誤):
            _寫(
                _writer(路徑, 時鐘=不可呼叫, audits=不可呼叫, hits=不可呼叫),
                連線, _結果(
                    目標敏感命中("input", "email", "/a", 1, 3),
                    目標敏感命中("input", "phone", "/z", 2, 5),
                ), mode=敏感操作模式.REPLAY,
            )
        assert 次數 == [0]
        assert 連線.execute("SELECT count(*) FROM audit_events").fetchone() == (1,)
        assert 連線.execute("SELECT count(*) FROM invocation_sensitive_hits").fetchone() == (1,)
    finally:
        連線.rollback()
        連線.close()


@pytest.mark.parametrize("失敗階段", ["audit", "hit"])
def test_audit或hit失敗固定拒絕且不替caller回滾(tmp_path, 失敗階段):
    路徑 = _建立資料庫(tmp_path)
    連線 = _開交易(路徑)
    _寫(
        _writer(路徑, audits=lambda: "audit-existing", hits=lambda: "hit-existing"),
        連線, _結果(目標敏感命中("metadata", "email", "/a", 1, 3)),
    )
    連線.commit()
    連線.execute("BEGIN IMMEDIATE")
    audits = (lambda: "audit-existing") if 失敗階段 == "audit" else (lambda: "audit-new")
    hits = (lambda: "hit-new") if 失敗階段 == "audit" else (lambda: "hit-existing")
    with pytest.raises(敏感稽核錯誤, match="敏感命中交易寫入失敗"):
        _寫(
            _writer(路徑, audits=audits, hits=hits), 連線,
            _結果(目標敏感命中("response_data", "phone", "/b", 2, 5)),
            family=建立完成來源族(),
        )
    assert 連線.in_transaction
    assert 連線.execute("SELECT count(*) FROM audit_events").fetchone() == (
        1 if 失敗階段 == "audit" else 2,
    )
    連線.rollback()
    assert 連線.execute("SELECT count(*) FROM audit_events").fetchone() == (1,)
    assert 連線.execute("SELECT count(*) FROM invocation_sensitive_hits").fetchone() == (1,)
    連線.close()


def test_schema_drift_fail_closed且不rollback_caller(tmp_path):
    路徑 = _建立資料庫(tmp_path)
    with sqlite3.connect(路徑) as 漂移:
        漂移.execute("CREATE TABLE unexpected_writer_drift(id INTEGER)")
    連線 = _開交易(路徑)
    try:
        with pytest.raises(敏感稽核錯誤):
            _寫(_writer(路徑), 連線, _呼叫命中())
        assert 連線.in_transaction
    finally:
        連線.rollback()
        連線.close()

def test_time_control_flow保留exact_identity且caller仍決定rollback(tmp_path):
    路徑 = _建立資料庫(tmp_path)
    控制 = KeyboardInterrupt("control", 9)
    def 時鐘():
        raise 控制
    連線 = _開交易(路徑)
    try:
        with pytest.raises(KeyboardInterrupt) as 資訊:
            _寫(_writer(路徑, 時鐘=時鐘), 連線, _呼叫命中())
        assert 資訊.value is 控制 and 資訊.value.args == ("control", 9)
        assert 連線.in_transaction
        assert 連線.execute("SELECT count(*) FROM audit_events").fetchone() == (0,)
    finally:
        連線.rollback()
        連線.close()
