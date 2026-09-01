"""Acceptance #21 Gate A review correction：authority、cleanup 與 readable cap。"""

from __future__ import annotations

import json
import sqlite3
import threading
from asyncio import CancelledError

import pytest

from 繁中代理.發布介面.呼叫.儲存庫 import (
    SQLite呼叫儲存庫,
    呼叫儲存錯誤,
    呼叫敏感交易協調器,
)
from 繁中代理.發布介面.呼叫.敏感稽核 import SQLite敏感稽核儲存庫, 敏感稽核錯誤
from 繁中代理.發布介面.呼叫.擷取政策 import (
    敏感偵測擷取結果,
    準備含敏感偵測的呼叫擷取,
    目標敏感命中,
    擷取階段,
)
from 繁中代理.發布介面.呼叫.生產橋接 import InvocationLedger橋接
from 繁中代理.發布介面.呼叫.編排器 import 執行嘗試結果, 執行嘗試請求
from 繁中代理.發布介面.治理.查詢投影 import SQLite呼叫查詢投影
from 繁中代理.發布介面.資料庫 import 初始化發布介面資料庫
from 繁中代理.發布介面.領域模型 import InvocationRef


def _標記() -> str:
    return "".join(("gate-a", "@", "example.test"))


def _建立資料庫(tmp_path, name="gate-a.sqlite3"):
    路徑 = tmp_path / name
    初始化發布介面資料庫(路徑)
    with sqlite3.connect(路徑) as 連線:
        連線.execute("PRAGMA foreign_keys=ON")
        連線.execute("INSERT INTO service_accounts VALUES('svc',1,NULL)")
        連線.execute(
            "INSERT INTO published_endpoints(id,owner_user_id,service_account_id,slug,status,"
            "current_version_id,created_at,updated_at) "
            "VALUES('ep','owner','svc','gate-a','active',NULL,0,0)"
        )
        連線.execute(
            "INSERT INTO published_endpoint_versions VALUES "
            "('ver','ep',1,'safe','safe','[]','[]','{}','rev','{}','{}','{}',NULL,'{}',0,'owner',0)"
        )
        連線.execute("UPDATE published_endpoints SET current_version_id='ver' WHERE id='ep'")
    return 路徑


def _協調器(路徑, detector=準備含敏感偵測的呼叫擷取):
    return 呼叫敏感交易協調器(
        SQLite敏感稽核儲存庫(
            路徑, 時鐘=lambda: 11,
            識別碼工廠=iter(f"audit-{n}" for n in range(4096)).__next__,
            命中識別碼工廠=iter(f"hit-{n}" for n in range(4096)).__next__,
        ),
        偵測器=detector,
    )


def _建立呼叫(路徑, *, input_value=None, metadata=None, coordinator=True):
    庫 = SQLite呼叫儲存庫(
        路徑, 時鐘=lambda: 7, 識別碼工廠=lambda: "inv",
        敏感交易協調器=_協調器(路徑) if coordinator else None,
    )
    庫.建立已解析呼叫(
        "ep", "ver", "req-inv", {} if input_value is None else input_value,
        metadata=metadata,
    )
    庫.標記執行中("inv")
    return 庫


def _完成(路徑, output):
    庫 = SQLite呼叫儲存庫(
        路徑, 時鐘=lambda: 13, 敏感交易協調器=_協調器(路徑),
    )
    return InvocationLedger橋接(庫).記錄執行嘗試(
        InvocationRef("inv", "req-inv"), 執行嘗試請求(object(), {}, None, 1),
        執行嘗試結果("success", output), True,
    )


@pytest.mark.parametrize("既有目標", ["input", "metadata", "tool_arguments", "tool_result"])
def test_completion_warning取invocation既有durable_hits且只揭露固定警告(tmp_path, 既有目標):
    路徑 = _建立資料庫(tmp_path)
    if 既有目標 == "input":
        庫 = _建立呼叫(路徑, input_value={"value": _標記()})
    elif 既有目標 == "metadata":
        庫 = _建立呼叫(路徑, metadata={"value": _標記()})
    else:
        庫 = _建立呼叫(路徑)
        庫.附加工具呼叫(
            "inv", "tool", "lookup",
            {"value": _標記()} if 既有目標 == "tool_arguments" else {},
            "success",
            result={"value": _標記()} if 既有目標 == "tool_result" else {},
        )

    收據 = _完成(路徑, {"answer": 1})
    assert [(warning.code, warning.message) for warning in 收據.warnings] == [
        ("sensitive_data_detected", "回應包含可能的敏感資料。"),
    ]
    with sqlite3.connect(路徑) as 連線:
        payload = json.loads(連線.execute(
            "SELECT payload_json FROM run_events WHERE invocation_id='inv'"
        ).fetchone()[0])
    assert payload["warnings"] == [
        {"code": "sensitive_data_detected", "message": "回應包含可能的敏感資料。"},
    ]
    assert 既有目標 not in json.dumps(payload["warnings"], ensure_ascii=False)


def test_legacy附加入口在result_clock_id_DB前固定hard_fail(tmp_path):
    次數 = []

    def 不可呼叫(*_args, **_kwargs):
        次數.append("called")
        raise AssertionError

    class 毒結果:
        def __getattribute__(self, _name):
            不可呼叫()

    庫 = SQLite敏感稽核儲存庫(
        tmp_path / "missing.sqlite3", 時鐘=不可呼叫, 識別碼工廠=不可呼叫,
        連線工廠=不可呼叫, 命中識別碼工廠=不可呼叫,
    )
    with pytest.raises(敏感稽核錯誤, match="^舊式敏感稽核附加已停用$") as info:
        庫.附加偵測事件(毒結果(), "inv", "ep", "req")
    assert info.value.__cause__ is None and 次數 == []
    assert not (tmp_path / "missing.sqlite3").exists()


class _清理連線(sqlite3.Connection):
    rollback錯誤 = None
    close錯誤 = None

    def rollback(self):
        super().rollback()
        if type(self).rollback錯誤 is not None:
            raise type(self).rollback錯誤

    def close(self):
        super().close()
        if type(self).close錯誤 is not None:
            raise type(self).close錯誤


class _注入協調器:
    def __init__(self, *, writer失敗):
        self.writer失敗 = writer失敗

    def 偵測呼叫(self, input_value, metadata):
        return 準備含敏感偵測的呼叫擷取(
            擷取階段.AUTHENTICATED, input_value, metadata,
        )

    def 偵測工具(self, arguments, result):
        return 準備含敏感偵測的呼叫擷取(
            擷取階段.AUTHENTICATED, {}, None,
            tool_arguments=arguments, tool_result=result,
        )

    def 偵測回應(self, response):
        return 準備含敏感偵測的呼叫擷取(
            擷取階段.AUTHENTICATED, {}, None, response_data=response,
        )

    def 寫入呼叫交易(self, *_args, **_kwargs):
        if self.writer失敗:
            raise RuntimeError("ordinary-primary")


def _清理factory(*args, **kwargs):
    return sqlite3.connect(*args, **kwargs, factory=_清理連線)


@pytest.mark.parametrize("operation", ["create", "tool"])
@pytest.mark.parametrize("cleanup", ["rollback", "close"])
def test_A21_create_tool_cleanup控制流程保留exact_identity(tmp_path, operation, cleanup):
    路徑 = _建立資料庫(tmp_path)
    if operation == "tool":
        _建立呼叫(路徑, coordinator=False)
    control = KeyboardInterrupt(f"{operation}-{cleanup}", 21)
    _清理連線.rollback錯誤 = control if cleanup == "rollback" else None
    _清理連線.close錯誤 = control if cleanup == "close" else None
    庫 = SQLite呼叫儲存庫(
        路徑, 時鐘=lambda: 7, 識別碼工廠=lambda: "inv",
        連線工廠=_清理factory,
        敏感交易協調器=_注入協調器(writer失敗=cleanup == "rollback"),
    )
    with pytest.raises(KeyboardInterrupt) as info:
        if operation == "create":
            庫.建立已解析呼叫("ep", "ver", "req-inv", {})
        else:
            庫.附加工具呼叫("inv", "tool", "lookup", {}, "success", result={})
    assert info.value is control and info.value.args == (f"{operation}-{cleanup}", 21)
    assert info.value.__cause__ is None and info.value.__context__ is None
    _清理連線.rollback錯誤 = _清理連線.close錯誤 = None


def _種入命中(路徑, 數量):
    with sqlite3.connect(路徑) as 連線:
        連線.execute("PRAGMA foreign_keys=ON")
        for index in range(數量):
            target = "input"
            path = f"/{index:04d}"
            metadata = json.dumps({
                "warning_code": "sensitive_data_detected", "target": target,
                "detector_type": "email", "json_path": path, "start": 0, "end": 1,
            }, sort_keys=True, separators=(",", ":"))
            連線.execute(
                "INSERT INTO audit_events(id,event_id,occurred_at,action,outcome,actor_type,actor_id,"
                "resource_type,resource_id,request_id,endpoint_id,invocation_id,metadata_json,created_at) "
                "VALUES(?,?,1,'published_api.sensitive_data_detected','success','system',NULL,"
                "'invocation','inv',NULL,'ep','inv',?,1)",
                (f"seed-audit-{index}", f"seed-audit-{index}", metadata),
            )
            連線.execute(
                "INSERT INTO invocation_sensitive_hits(id,invocation_id,tool_call_id,target_type,"
                "detector_type,json_path,start_offset,end_offset,audit_event_id,detected_at) "
                "VALUES(?,'inv',NULL,'input','email',?,0,1,?,1)",
                (f"seed-hit-{index}", path, f"seed-audit-{index}"),
            )


def test_invocation_hit_cap_exact_1024可由Admin讀且第1025_payload全回滾(tmp_path):
    路徑 = _建立資料庫(tmp_path)
    庫 = _建立呼叫(路徑, coordinator=False)
    _種入命中(路徑, 1023)
    庫 = SQLite呼叫儲存庫(路徑, 敏感交易協調器=_協調器(路徑))
    assert 庫.附加工具呼叫(
        "inv", "tool-1024", "lookup", {"value": _標記()}, "success", result={},
    ) == 1
    detail = SQLite呼叫查詢投影(str(路徑)).查詢管理員原始資料(True, "ep", "inv")
    assert len(detail["sensitive_hits"]) == 1024

    with pytest.raises(呼叫儲存錯誤, match="^工具呼叫附加失敗$"):
        庫.附加工具呼叫(
            "inv", "tool-1025", "lookup", {"value": _標記()}, "success", result={},
        )
    with sqlite3.connect(路徑) as 連線:
        assert 連線.execute(
            "SELECT count(*) FROM invocation_sensitive_hits WHERE invocation_id='inv'"
        ).fetchone() == (1024,)
        assert 連線.execute(
            "SELECT count(*) FROM endpoint_tool_calls WHERE id='tool-1025'"
        ).fetchone() == (0,)
        assert 連線.execute(
            "SELECT count(*) FROM audit_events WHERE invocation_id='inv'"
        ).fetchone() == (1024,)


def test_hit_cap_boundary並發由_BEGIN_IMMEDIATE_只接受一個payload(tmp_path):
    路徑 = _建立資料庫(tmp_path)
    _建立呼叫(路徑, coordinator=False)
    _種入命中(路徑, 1023)
    barrier = threading.Barrier(3)
    成功, 失敗 = [], []

    def append(index):
        庫 = SQLite呼叫儲存庫(路徑, 敏感交易協調器=_協調器(路徑))
        barrier.wait()
        try:
            成功.append(庫.附加工具呼叫(
                "inv", f"tool-{index}", "lookup", {"value": _標記()},
                "success", result={},
            ))
        except 呼叫儲存錯誤 as error:
            失敗.append(error.args)

    threads = [threading.Thread(target=append, args=(index,)) for index in range(2)]
    for thread in threads:
        thread.start()
    barrier.wait()
    for thread in threads:
        thread.join(timeout=10)
        assert not thread.is_alive()
    assert 成功 == [1] and 失敗 == [("工具呼叫附加失敗",)]
    with sqlite3.connect(路徑) as 連線:
        assert 連線.execute(
            "SELECT count(*) FROM invocation_sensitive_hits WHERE invocation_id='inv'"
        ).fetchone() == (1024,)
        assert 連線.execute("SELECT count(*) FROM endpoint_tool_calls").fetchone() == (1,)


def test_completion第1025_hit拒絕且event_output_hit_audit全回滾(tmp_path):
    路徑 = _建立資料庫(tmp_path)
    _建立呼叫(路徑, coordinator=False)
    _種入命中(路徑, 1024)
    with pytest.raises(呼叫儲存錯誤, match="^執行事件原子提交失敗$"):
        _完成(路徑, {"value": _標記()})
    with sqlite3.connect(路徑) as 連線:
        assert 連線.execute(
            "SELECT status,output_json FROM endpoint_invocations WHERE id='inv'"
        ).fetchone() == ("running", None)
        assert 連線.execute("SELECT count(*) FROM run_events").fetchone() == (0,)
        assert 連線.execute("SELECT count(*) FROM invocation_sensitive_hits").fetchone() == (1024,)
        assert 連線.execute("SELECT count(*) FROM audit_events").fetchone() == (1024,)


def test_completion第1024_hit成功且warning與Admin_projection皆可讀(tmp_path):
    路徑 = _建立資料庫(tmp_path)
    _建立呼叫(路徑, coordinator=False)
    _種入命中(路徑, 1023)
    收據 = _完成(路徑, {"value": _標記()})
    assert [(warning.code, warning.message) for warning in 收據.warnings] == [
        ("sensitive_data_detected", "回應包含可能的敏感資料。"),
    ]
    detail = SQLite呼叫查詢投影(str(路徑)).查詢管理員原始資料(True, "ep", "inv")
    assert detail["status"] == "succeeded" and len(detail["sensitive_hits"]) == 1024


def test_invocation_create_exact_1024_hits仍可讀(tmp_path):
    路徑 = _建立資料庫(tmp_path)
    空結果 = 準備含敏感偵測的呼叫擷取(擷取階段.AUTHENTICATED, {}, None)
    命中們 = tuple(
        目標敏感命中(目標, "email", f"/{index:04d}", 0, 1)
        for 目標 in ("input", "metadata") for index in range(512)
    )

    def detector(*_args, **_kwargs):
        return 敏感偵測擷取結果(
            空結果.命令, 命中們, ("sensitive_data_detected",),
        )

    庫 = SQLite呼叫儲存庫(
        路徑, 時鐘=lambda: 7, 識別碼工廠=lambda: "inv",
        敏感交易協調器=_協調器(路徑, detector),
    )
    assert 庫.建立已解析呼叫("ep", "ver", "req-inv", {}) == "inv"
    detail = SQLite呼叫查詢投影(str(路徑)).查詢管理員原始資料(True, "ep", "inv")
    assert len(detail["sensitive_hits"]) == 1024


class _命中查詢失敗連線(sqlite3.Connection):
    rollback數 = 0

    def execute(self, sql, parameters=(), /):
        if sql == "SELECT count(*) FROM invocation_sensitive_hits WHERE invocation_id=?":
            raise sqlite3.OperationalError("count-query-failed")
        return super().execute(sql, parameters)

    def rollback(self):
        type(self).rollback數 += 1
        return super().rollback()


def test_completion_existing_hit_query失敗rollback_fail_closed(tmp_path):
    路徑 = _建立資料庫(tmp_path)
    _建立呼叫(路徑, coordinator=False)

    def factory(*args, **kwargs):
        return sqlite3.connect(*args, **kwargs, factory=_命中查詢失敗連線)

    _命中查詢失敗連線.rollback數 = 0
    庫 = SQLite呼叫儲存庫(
        路徑, 時鐘=lambda: 13, 連線工廠=factory,
        敏感交易協調器=_注入協調器(writer失敗=False),
    )
    with pytest.raises(呼叫儲存錯誤, match="^執行事件原子提交失敗$"):
        庫.原子記錄執行事件並結案(
            "inv", "event", "completed", {"kind": "completed"}, 1,
            status="succeeded", output={"answer": 1}, warnings=(),
        )
    assert _命中查詢失敗連線.rollback數 == 1
    with sqlite3.connect(路徑) as 連線:
        assert 連線.execute("SELECT status FROM endpoint_invocations WHERE id='inv'").fetchone() == ("running",)
        assert 連線.execute("SELECT count(*) FROM run_events").fetchone() == (0,)


@pytest.mark.parametrize("cleanup", ["rollback", "close"])
def test_completion_cleanup控制流程保留exact_identity(tmp_path, cleanup):
    路徑 = _建立資料庫(tmp_path)
    _建立呼叫(路徑, coordinator=False)
    control = (
        KeyboardInterrupt("completion-rollback", 31)
        if cleanup == "rollback" else CancelledError("completion-close", 32)
    )
    _清理連線.rollback錯誤 = control if cleanup == "rollback" else None
    _清理連線.close錯誤 = control if cleanup == "close" else None
    庫 = SQLite呼叫儲存庫(
        路徑, 時鐘=lambda: 13, 連線工廠=_清理factory,
        敏感交易協調器=_注入協調器(writer失敗=cleanup == "rollback"),
    )
    with pytest.raises(type(control)) as info:
        庫.原子記錄執行事件並結案(
            "inv", "event", "completed", {"kind": "completed"}, 1,
            status="succeeded", output={"answer": 1}, warnings=(),
        )
    assert info.value is control and info.value.args == control.args
    assert info.value.__cause__ is None and info.value.__context__ is None
    _清理連線.rollback錯誤 = _清理連線.close錯誤 = None
    with sqlite3.connect(路徑) as 連線:
        assert 連線.execute("SELECT status FROM endpoint_invocations WHERE id='inv'").fetchone() == (
            "running" if cleanup == "rollback" else "succeeded",
        )


@pytest.mark.parametrize("operation", ["create", "tool", "completion"])
def test_A21_primary控制流程不被ordinary_cleanup覆蓋(tmp_path, operation):
    路徑 = _建立資料庫(tmp_path)
    if operation != "create":
        _建立呼叫(路徑, coordinator=False)
    primary = GeneratorExit(f"primary-{operation}", 41)

    class 協調器(_注入協調器):
        def 偵測呼叫(self, *_args):
            raise primary

        def 偵測工具(self, *_args):
            raise primary

        def 偵測回應(self, *_args):
            raise primary

    庫 = SQLite呼叫儲存庫(
        路徑, 時鐘=lambda: 13, 連線工廠=_清理factory,
        識別碼工廠=lambda: "inv", 敏感交易協調器=協調器(writer失敗=False),
    )
    with pytest.raises(GeneratorExit) as info:
        if operation == "create":
            庫.建立已解析呼叫("ep", "ver", "req-inv", {})
        elif operation == "tool":
            庫.附加工具呼叫("inv", "tool", "lookup", {}, "success", result={})
        else:
            庫.原子記錄執行事件並結案(
                "inv", "event", "completed", {"kind": "completed"}, 1,
                status="succeeded", output={"answer": 1}, warnings=(),
            )
    assert info.value is primary and info.value.args == (f"primary-{operation}", 41)
