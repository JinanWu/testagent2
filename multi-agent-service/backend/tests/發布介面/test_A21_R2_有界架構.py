"""A21-R2 封閉來源族、建立重播與失敗工具觀察的架構契約。"""

from __future__ import annotations

import inspect
import json
import sqlite3
import threading

import pytest

from 繁中代理.發布介面.呼叫.儲存庫 import (
    SQLite呼叫儲存庫,
    呼叫儲存錯誤,
    呼叫敏感交易協調器,
)
from 繁中代理.發布介面.呼叫.敏感稽核 import (
    SQLite敏感稽核儲存庫,
    敏感稽核錯誤,
    敏感操作模式,
    建立呼叫來源族,
)
from 繁中代理.發布介面.呼叫.擷取政策 import (
    擷取階段,
    敏感偵測擷取結果,
    準備含敏感偵測的呼叫擷取,
)
from 繁中代理.發布介面.資料庫 import 初始化發布介面資料庫


def _建立資料庫(tmp_path):
    路徑 = tmp_path / "a21-r2.sqlite3"
    初始化發布介面資料庫(路徑)
    with sqlite3.connect(路徑) as 連線:
        連線.execute("INSERT INTO service_accounts VALUES('svc',1,NULL)")
        連線.execute(
            "INSERT INTO published_endpoints(id,owner_user_id,service_account_id,slug,status,"
            "current_version_id,created_at,updated_at) "
            "VALUES('ep','owner','svc','r2','active',NULL,0,0)"
        )
        連線.execute(
            "INSERT INTO published_endpoint_versions VALUES "
            "('ver','ep',1,'safe','safe','[]','[]','{}','rev','{}','{}','{}',NULL,'{}',0,'owner',0)"
        )
        連線.execute("UPDATE published_endpoints SET current_version_id='ver' WHERE id='ep'")
    return 路徑


def test_writer_public_seam只接受mode與sealed_source_family():
    參數 = inspect.signature(SQLite敏感稽核儲存庫.寫入呼叫交易).parameters
    assert tuple(參數) == ("self", "連線", "模式", "來源族", "結果", "呼叫識別碼", "端點識別碼")
    assert "工具呼叫識別碼們" not in 參數 and "回放來源" not in 參數


def test_invocation_replay依request_id回既有ID且不呼叫fresh_factory_clock(tmp_path):
    路徑 = _建立資料庫(tmp_path)
    首次 = SQLite呼叫儲存庫(路徑, 時鐘=lambda: 7, 識別碼工廠=lambda: "inv-first")
    assert 首次.建立已解析呼叫("ep", "ver", "req", {"q": 1}, metadata={"m": 2}) == "inv-first"

    次數 = []
    def 不可呼叫():
        次數.append(1)
        raise AssertionError

    重播 = SQLite呼叫儲存庫(路徑, 時鐘=不可呼叫, 識別碼工廠=不可呼叫)
    assert 重播.建立已解析呼叫("ep", "ver", "req", {"q": 1}, metadata={"m": 2}) == "inv-first"
    assert 次數 == []


def test_replay空expected仍讀完整invocation_family並拒絕污染(tmp_path):
    路徑 = _建立資料庫(tmp_path)
    with sqlite3.connect(路徑) as 連線:
        連線.execute(
            "INSERT INTO endpoint_invocations(id,endpoint_id,endpoint_version_id,request_id,status,"
            "input_json,created_at) VALUES('inv','ep','ver','req','pending','{}',0)"
        )
    writer = SQLite敏感稽核儲存庫(路徑)
    連線 = sqlite3.connect(路徑, isolation_level=None)
    連線.execute("PRAGMA foreign_keys=ON")
    連線.execute("BEGIN IMMEDIATE")
    try:
        空結果 = 準備含敏感偵測的呼叫擷取(擷取階段.AUTHENTICATED, {}, None)
        收據 = writer.寫入呼叫交易(
            連線, 敏感操作模式.REPLAY, 建立呼叫來源族(), 空結果, "inv", "ep",
        )
        assert 收據.命中數 == 0
    finally:
        連線.rollback()
        連線.close()


def test_first_write空expected仍拒絕orphan_sensitive_audit(tmp_path):
    路徑 = _建立資料庫(tmp_path)
    with sqlite3.connect(路徑) as 連線:
        連線.execute(
            "INSERT INTO endpoint_invocations(id,endpoint_id,endpoint_version_id,request_id,status,"
            "input_json,created_at) VALUES('inv-orphan','ep','ver','req-orphan','pending','{}',0)"
        )
        連線.execute(
            "INSERT INTO audit_events(id,event_id,occurred_at,action,outcome,actor_type,resource_type,"
            "resource_id,endpoint_id,invocation_id,metadata_json,created_at) VALUES("
            "'audit-orphan','audit-orphan',0,'published_api.sensitive_data_detected','success',"
            "'system','invocation','inv-orphan','ep','inv-orphan','{}',0)"
        )
    writer = SQLite敏感稽核儲存庫(路徑)
    連線 = sqlite3.connect(路徑, isolation_level=None)
    連線.execute("PRAGMA foreign_keys=ON")
    連線.execute("BEGIN IMMEDIATE")
    try:
        空結果 = 準備含敏感偵測的呼叫擷取(擷取階段.AUTHENTICATED, {}, None)
        assert type(空結果) is 敏感偵測擷取結果
        with pytest.raises(敏感稽核錯誤, match="敏感命中交易寫入失敗"):
            writer.寫入呼叫交易(
                連線, 敏感操作模式.FIRST_WRITE, 建立呼叫來源族(),
                空結果, "inv-orphan", "ep",
            )
    finally:
        連線.rollback()
        連線.close()


class _提交後遺失回應連線(sqlite3.Connection):
    """真實提交後模擬driver遺失ordinary acknowledgement。"""

    def commit(self):
        super().commit()
        raise sqlite3.OperationalError("acknowledgement lost")


def _遺失提交連線(*args, **kwargs):
    return sqlite3.connect(*args, factory=_提交後遺失回應連線, **kwargs)


def _敏感協調器(路徑):
    writer = SQLite敏感稽核儲存庫(
        路徑, 時鐘=lambda: 11,
        識別碼工廠=iter(f"audit-r2-{n}" for n in range(20)).__next__,
        命中識別碼工廠=iter(f"hit-r2-{n}" for n in range(20)).__next__,
    )
    return 呼叫敏感交易協調器(writer)


def test_creation_commit已成功但ack遺失以request_identity重建結果(tmp_path):
    路徑 = _建立資料庫(tmp_path)
    repo = SQLite呼叫儲存庫(
        路徑, 時鐘=lambda: 5, 識別碼工廠=lambda: "inv-ack",
        連線工廠=_遺失提交連線, 敏感交易協調器=_敏感協調器(路徑),
    )

    assert repo.建立已解析呼叫("ep", "ver", "req-ack", {"q": 1}) == "inv-ack"
    with sqlite3.connect(路徑) as 連線:
        assert 連線.execute(
            "SELECT id,status FROM endpoint_invocations WHERE request_id='req-ack'"
        ).fetchone() == ("inv-ack", "pending")


def test_tool_commit已成功但ack遺失以tool_identity重建序號(tmp_path):
    路徑 = _建立資料庫(tmp_path)
    base = SQLite呼叫儲存庫(路徑, 時鐘=lambda: 1, 識別碼工廠=lambda: "inv-tool")
    base.建立已解析呼叫("ep", "ver", "req-tool", {})
    base.標記執行中("inv-tool")
    repo = SQLite呼叫儲存庫(
        路徑, 時鐘=lambda: 7, 連線工廠=_遺失提交連線,
        敏感交易協調器=_敏感協調器(路徑),
    )

    assert repo.附加工具呼叫(
        "inv-tool", "tool-ack", "lookup", {"mail": "observer@example.test"},
        "error", error={"code": "tool_execution_failed"},
    ) == 1
    with sqlite3.connect(路徑) as 連線:
        assert 連線.execute(
            "SELECT outcome,result_json,error_json FROM endpoint_tool_calls WHERE id='tool-ack'"
        ).fetchone() == ("error", None, '{"code":"tool_execution_failed"}')
        assert 連線.execute(
            "SELECT target_type FROM invocation_sensitive_hits WHERE tool_call_id='tool-ack'"
        ).fetchall() == [("tool_arguments",)]


def test_existing_tool_exact_replay在terminal_parent仍回原序號(tmp_path):
    路徑 = _建立資料庫(tmp_path)
    repo = SQLite呼叫儲存庫(
        路徑, 時鐘=lambda: 7, 識別碼工廠=lambda: "inv-terminal-tool",
        敏感交易協調器=_敏感協調器(路徑),
    )
    repo.建立已解析呼叫("ep", "ver", "req-terminal-tool", {})
    repo.標記執行中("inv-terminal-tool")
    assert repo.附加工具呼叫(
        "inv-terminal-tool", "tool-terminal", "lookup", {"value": 1},
        "error", error={"code": "tool_execution_failed"},
    ) == 1
    repo.完成呼叫("inv-terminal-tool", "failed", error={"code": "internal_error"})
    assert repo.附加工具呼叫(
        "inv-terminal-tool", "tool-terminal", "lookup", {"value": 1},
        "error", error={"code": "tool_execution_failed"},
    ) == 1
    with pytest.raises(呼叫儲存錯誤, match="工具呼叫附加失敗"):
        repo.附加工具呼叫(
            "inv-terminal-tool", "tool-new", "lookup", {"value": 1},
            "error", error={"code": "tool_execution_failed"},
        )
    with pytest.raises(呼叫儲存錯誤, match="工具呼叫附加失敗"):
        repo.附加工具呼叫(
            "inv-terminal-tool", "tool-terminal", "lookup", {"value": 2},
            "error", error={"code": "tool_execution_failed"},
        )


def test_completion_commit已成功但ack遺失以event_identity重建結果(tmp_path):
    路徑 = _建立資料庫(tmp_path)
    base = SQLite呼叫儲存庫(路徑, 時鐘=lambda: 1, 識別碼工廠=lambda: "inv-done")
    base.建立已解析呼叫("ep", "ver", "req-done", {})
    base.標記執行中("inv-done")
    repo = SQLite呼叫儲存庫(
        路徑, 時鐘=lambda: 9, 連線工廠=_遺失提交連線,
        敏感交易協調器=_敏感協調器(路徑),
    )

    結果 = repo.原子記錄執行事件並結案(
        "inv-done", "event-done", "run.completed", {"status": "ok"}, 1,
        status="succeeded", output={"mail": "observer@example.test"},
        warnings=(),
    )
    assert type(結果) is tuple and 結果[0] == 1
    with sqlite3.connect(路徑) as 連線:
        assert 連線.execute(
            "SELECT status FROM endpoint_invocations WHERE id='inv-done'"
        ).fetchone() == ("succeeded",)
        assert 連線.execute(
            "SELECT target_type FROM invocation_sensitive_hits WHERE invocation_id='inv-done'"
        ).fetchall() == [("response_data",)]


class _提交前失敗連線(sqlite3.Connection):
    """模擬commit尚未生效即ordinary failure。"""

    def commit(self):
        raise sqlite3.OperationalError("commit rejected")


class _關閉失敗連線(sqlite3.Connection):
    """模擬commit成功後ordinary close failure。"""

    def close(self):
        super().close()
        raise sqlite3.OperationalError("close failed")


def _指定連線(類型):
    return lambda *args, **kwargs: sqlite3.connect(*args, factory=類型, **kwargs)


def test_creation_commit前失敗不留下partial_graph(tmp_path):
    路徑 = _建立資料庫(tmp_path)
    repo = SQLite呼叫儲存庫(
        路徑, 時鐘=lambda: 5, 識別碼工廠=lambda: "inv-none",
        連線工廠=_指定連線(_提交前失敗連線), 敏感交易協調器=_敏感協調器(路徑),
    )
    with pytest.raises(呼叫儲存錯誤, match="呼叫建立失敗"):
        repo.建立已解析呼叫("ep", "ver", "req-none", {"mail": "observer@example.test"})
    with sqlite3.connect(路徑) as 連線:
        assert tuple(連線.execute(f"SELECT count(*) FROM {table}").fetchone()[0] for table in (
            "endpoint_invocations", "invocation_sensitive_hits", "audit_events",
        )) == (0, 0, 0)


def test_creation_commit成功後ordinary_close失敗仍回durable_receipt(tmp_path):
    路徑 = _建立資料庫(tmp_path)
    repo = SQLite呼叫儲存庫(
        路徑, 時鐘=lambda: 5, 識別碼工廠=lambda: "inv-close",
        連線工廠=_指定連線(_關閉失敗連線), 敏感交易協調器=_敏感協調器(路徑),
    )
    assert repo.建立已解析呼叫("ep", "ver", "req-close", {}) == "inv-close"


def test_request_identity衝突_running_terminal皆fail_closed(tmp_path):
    路徑 = _建立資料庫(tmp_path)
    repo = SQLite呼叫儲存庫(路徑, 時鐘=lambda: 1, 識別碼工廠=lambda: "inv-state")
    assert repo.建立已解析呼叫("ep", "ver", "req-state", {"q": 1}) == "inv-state"
    with pytest.raises(呼叫儲存錯誤, match="呼叫建立失敗"):
        repo.建立已解析呼叫("ep", "ver", "req-state", {"q": 2})
    repo.標記執行中("inv-state")
    with pytest.raises(呼叫儲存錯誤, match="呼叫建立失敗"):
        repo.建立已解析呼叫("ep", "ver", "req-state", {"q": 1})
    repo.完成呼叫("inv-state", "failed", error={"code": "internal_error"})
    with pytest.raises(呼叫儲存錯誤, match="呼叫建立失敗"):
        repo.建立已解析呼叫("ep", "ver", "req-state", {"q": 1})


def test_concurrent_same_request只有一個parent且皆回同一identity(tmp_path):
    路徑 = _建立資料庫(tmp_path)
    barrier = threading.Barrier(3)
    結果們, 錯誤們 = [], []

    def run(index):
        repo = SQLite呼叫儲存庫(
            路徑, 時鐘=lambda: 1, 識別碼工廠=lambda: f"inv-{index}",
            敏感交易協調器=_敏感協調器(路徑),
        )
        barrier.wait()
        try:
            結果們.append(repo.建立已解析呼叫("ep", "ver", "req-concurrent", {"q": 1}))
        except BaseException as error:
            錯誤們.append(type(error))

    threads = [threading.Thread(target=run, args=(index,)) for index in range(2)]
    for thread in threads:
        thread.start()
    barrier.wait()
    for thread in threads:
        thread.join()
    assert 錯誤們 == [] and len(set(結果們)) == 1
    with sqlite3.connect(路徑) as 連線:
        assert 連線.execute(
            "SELECT count(*) FROM endpoint_invocations WHERE request_id='req-concurrent'"
        ).fetchone() == (1,)
