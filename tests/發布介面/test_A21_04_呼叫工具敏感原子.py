"""A21-04 invocation create 與 tool append 敏感命中原子整合。"""

from __future__ import annotations

import json
import sqlite3
import threading

import pytest

from 繁中代理.發布介面.呼叫.儲存庫 import (
    SQLite呼叫儲存庫,
    呼叫儲存錯誤,
    呼叫敏感交易協調器,
)
from 繁中代理.發布介面.呼叫.敏感稽核 import SQLite敏感稽核儲存庫
from 繁中代理.發布介面.呼叫.擷取政策 import (
    敏感偵測擷取結果,
    準備含敏感偵測的呼叫擷取,
    目標敏感命中,
    擷取階段,
)
from 繁中代理.發布介面.資料庫 import 初始化發布介面資料庫


def _安全標記():
    return "".join(("observer", "@", "example.test"))


def _建立資料庫(tmp_path):
    路徑 = tmp_path / "a21-04.sqlite3"
    初始化發布介面資料庫(路徑)
    with sqlite3.connect(路徑) as 連線:
        連線.execute("PRAGMA foreign_keys=ON")
        連線.execute("INSERT INTO service_accounts VALUES('svc',1,NULL)")
        連線.execute(
            "INSERT INTO published_endpoints(id,owner_user_id,service_account_id,slug,status,"
            "current_version_id,created_at,updated_at) "
            "VALUES('ep','owner','svc','atomic','active',NULL,0,0)"
        )
        連線.execute(
            "INSERT INTO published_endpoint_versions VALUES "
            "('ver','ep',1,'safe','safe','[]','[]','{}','rev','{}','{}','{}',NULL,'{}',0,'owner',0)"
        )
        連線.execute("UPDATE published_endpoints SET current_version_id='ver' WHERE id='ep'")
    return 路徑


def _數量(路徑):
    with sqlite3.connect(路徑) as 連線:
        return tuple(連線.execute(f"SELECT count(*) FROM {table}").fetchone()[0] for table in (
            "endpoint_invocations", "endpoint_tool_calls", "invocation_sensitive_hits",
            "audit_events", "endpoint_redactions",
        ))


def _協調器(路徑, *, detector=準備含敏感偵測的呼叫擷取,
         audits=None, hits=None):
    writer = SQLite敏感稽核儲存庫(
        路徑, 時鐘=lambda: 19,
        識別碼工廠=(audits or iter(f"audit-{n}" for n in range(20)).__next__),
        命中識別碼工廠=(hits or iter(f"hit-{n}" for n in range(20)).__next__),
    )
    return 呼叫敏感交易協調器(writer, 偵測器=detector)


def test_invocation與tool成功路徑同交易寫row_hit_audit且不遮蔽(tmp_path):
    路徑 = _建立資料庫(tmp_path)
    marker = _安全標記()
    phone_marker = "".join(("09", "12-345-678"))
    credential_marker = "".join(("password", "=", "abcdefgh"))
    庫 = SQLite呼叫儲存庫(
        路徑, 時鐘=lambda: 7, 識別碼工廠=lambda: "inv",
        敏感交易協調器=_協調器(路徑),
    )
    assert 庫.建立已解析呼叫(
        "ep", "ver", "req", {"mail": marker},
        metadata={"phone": phone_marker},
    ) == "inv"
    庫.標記執行中("inv")
    assert 庫.附加工具呼叫(
        "inv", "tool", "lookup", {"auth": credential_marker},
        "success", result={"mail": marker},
    ) == 1
    assert 庫.附加工具呼叫(
        "inv", "tool-error", "lookup", {"mail": marker}, "error",
        error={"mail": marker},
    ) == 2

    with sqlite3.connect(路徑) as 連線:
        呼叫JSON = 連線.execute(
            "SELECT input_json,metadata_json FROM endpoint_invocations WHERE id='inv'"
        ).fetchone()
        工具JSON = 連線.execute(
            "SELECT arguments_json,result_json FROM endpoint_tool_calls WHERE id='tool'"
        ).fetchone()
        targets = 連線.execute(
            "SELECT target_type,tool_call_id FROM invocation_sensitive_hits ORDER BY rowid"
        ).fetchall()
        assert 呼叫JSON == (json.dumps({"mail": marker}, sort_keys=True, separators=(",", ":")),
                           json.dumps({"phone": phone_marker}, sort_keys=True, separators=(",", ":")))
        assert 工具JSON == (json.dumps({"auth": credential_marker}, sort_keys=True, separators=(",", ":")),
                           json.dumps({"mail": marker}, sort_keys=True, separators=(",", ":")))
        assert targets == [
            ("input", None), ("metadata", None),
            ("tool_arguments", "tool"), ("tool_result", "tool"),
            ("tool_arguments", "tool-error"),
        ]
    assert _數量(路徑) == (1, 2, 5, 5, 0)


def test_detector前後canonical_bytes不變且只使用同一連線(tmp_path):
    路徑 = _建立資料庫(tmp_path)
    觀測 = []

    def detector(*args, **kwargs):
        before = tuple(json.dumps(x, sort_keys=True, separators=(",", ":")).encode() for x in args[1:3])
        result = 準備含敏感偵測的呼叫擷取(*args, **kwargs)
        after = tuple(json.dumps(x, sort_keys=True, separators=(",", ":")).encode() for x in args[1:3])
        觀測.append((before, after))
        return result

    庫 = SQLite呼叫儲存庫(
        路徑, 時鐘=lambda: 1, 識別碼工廠=lambda: "inv",
        敏感交易協調器=_協調器(路徑, detector=detector),
    )
    庫.建立已解析呼叫("ep", "ver", "req", {"z": 1, "a": _安全標記()}, metadata={"b": 2})
    assert 觀測 and 觀測[0][0] == 觀測[0][1] and _數量(路徑)[-1] == 0


@pytest.mark.parametrize("stage", ["detector", "writer"])
def test_invocation_dependency普通失敗固定錯誤且全回滾(tmp_path, stage):
    路徑 = _建立資料庫(tmp_path)

    class 失敗協調器:
        def 偵測呼叫(self, *_args):
            if stage == "detector":
                raise RuntimeError("fixed-marker")
            return 準備含敏感偵測的呼叫擷取(擷取階段.AUTHENTICATED, *_args)
        def 偵測工具(self, *_args):
            raise AssertionError
        def 寫入呼叫交易(self, *_args, **_kwargs):
            raise RuntimeError("fixed-marker")

    庫 = SQLite呼叫儲存庫(
        路徑, 時鐘=lambda: 1, 識別碼工廠=lambda: "inv",
        敏感交易協調器=失敗協調器(),
    )
    with pytest.raises(呼叫儲存錯誤, match="^呼叫建立失敗$") as info:
        庫.建立已解析呼叫("ep", "ver", "req", {"mail": _安全標記()})
    assert info.value.__cause__ is None and _數量(路徑) == (0, 0, 0, 0, 0)
    repository_frames = [
        item.frame.f_locals for item in info.traceback if item.path.name == "儲存庫.py"
    ]
    assert _安全標記() not in repr(repository_frames)


def test_same_tool_replay不重複_different_hit_set拒絕(tmp_path):
    路徑 = _建立資料庫(tmp_path)
    庫 = SQLite呼叫儲存庫(
        路徑, 時鐘=lambda: 1, 識別碼工廠=lambda: "inv",
        敏感交易協調器=_協調器(路徑),
    )
    庫.建立已解析呼叫("ep", "ver", "req", {})
    庫.標記執行中("inv")
    payload = {"mail": _安全標記()}
    assert 庫.附加工具呼叫("inv", "tool", "lookup", payload, "success", result={}) == 1
    assert 庫.附加工具呼叫("inv", "tool", "lookup", payload, "success", result={}) == 1
    assert _數量(路徑) == (1, 1, 1, 1, 0)

    原結果 = 準備含敏感偵測的呼叫擷取(
        擷取階段.AUTHENTICATED, {}, None, tool_arguments=payload,
    )
    def 改變偵測(*_args, **_kwargs):
        return 敏感偵測擷取結果(
            原結果.命令, 原結果.命中們 + (目標敏感命中("tool_arguments", "phone", "/extra", 0, 1),),
            ("sensitive_data_detected",),
        )
    拒絕庫 = SQLite呼叫儲存庫(路徑, 敏感交易協調器=_協調器(路徑, detector=改變偵測))
    with pytest.raises(呼叫儲存錯誤, match="^工具呼叫附加失敗$"):
        拒絕庫.附加工具呼叫("inv", "tool", "lookup", payload, "success", result={})
    assert _數量(路徑) == (1, 1, 1, 1, 0)


def test_zero_hit_replay改為非zero_hit_set也拒絕(tmp_path):
    路徑 = _建立資料庫(tmp_path)
    marker = _安全標記()
    def zero_detector(*_args, **_kwargs):
        return 準備含敏感偵測的呼叫擷取(擷取階段.AUTHENTICATED, {}, None)
    庫 = SQLite呼叫儲存庫(路徑, 識別碼工廠=lambda: "inv")
    庫.建立已解析呼叫("ep", "ver", "req", {})
    庫.標記執行中("inv")
    zero_repo = SQLite呼叫儲存庫(
        路徑, 敏感交易協調器=_協調器(路徑, detector=zero_detector),
    )
    assert zero_repo.附加工具呼叫(
        "inv", "tool", "lookup", {"mail": marker}, "success", result={},
    ) == 1
    real_repo = SQLite呼叫儲存庫(路徑, 敏感交易協調器=_協調器(路徑))
    with pytest.raises(呼叫儲存錯誤, match="^工具呼叫附加失敗$"):
        real_repo.附加工具呼叫(
            "inv", "tool", "lookup", {"mail": marker}, "success", result={},
        )
    assert _數量(路徑) == (1, 1, 0, 0, 0)


def test_concurrency_same_tool只有single_authority(tmp_path):
    路徑 = _建立資料庫(tmp_path)
    base = SQLite呼叫儲存庫(路徑, 識別碼工廠=lambda: "inv")
    base.建立已解析呼叫("ep", "ver", "req", {})
    base.標記執行中("inv")
    barrier = threading.Barrier(3)
    results, errors = [], []

    def run(n):
        庫 = SQLite呼叫儲存庫(
            路徑, 敏感交易協調器=_協調器(
                路徑, audits=lambda: f"audit-{n}", hits=lambda: f"hit-{n}",
            ),
        )
        barrier.wait()
        try:
            results.append(庫.附加工具呼叫(
                "inv", "tool", "lookup", {"mail": _安全標記()}, "success", result={},
            ))
        except BaseException as exc:
            errors.append(type(exc))

    threads = [threading.Thread(target=run, args=(n,)) for n in range(2)]
    for thread in threads:
        thread.start()
    barrier.wait()
    for thread in threads:
        thread.join()
    assert errors == [] and results == [1, 1]
    assert _數量(路徑) == (1, 1, 1, 1, 0)


def test_control_flow_identity保留且事件路徑不受注入影響(tmp_path):
    路徑 = _建立資料庫(tmp_path)
    control = KeyboardInterrupt("control", 21)
    class 協調器:
        def 偵測呼叫(self, *_args):
            raise control
        def 偵測工具(self, *_args):
            raise control
        def 寫入呼叫交易(self, *_args, **_kwargs):
            raise AssertionError
    庫 = SQLite呼叫儲存庫(
        路徑, 識別碼工廠=lambda: "inv", 敏感交易協調器=協調器(),
    )
    with pytest.raises(KeyboardInterrupt) as info:
        庫.建立已解析呼叫("ep", "ver", "req", {})
    assert info.value is control and info.value.args == ("control", 21)
    assert _數量(路徑) == (0, 0, 0, 0, 0)


@pytest.mark.parametrize("stage", ["audit_insert", "hit_insert"])
def test_A21_03_audit或hit_insert失敗會連invocation全回滾(tmp_path, stage):
    路徑 = _建立資料庫(tmp_path)
    with sqlite3.connect(路徑) as 連線:
        連線.execute(
            "INSERT INTO endpoint_invocations(id,endpoint_id,endpoint_version_id,request_id,status,"
            "input_json,created_at) VALUES('base','ep','ver','base-request','running','{}',0)"
        )
        連線.execute(
            "INSERT INTO audit_events(id,event_id,occurred_at,action,outcome,actor_type,actor_id,"
            "resource_type,resource_id,request_id,endpoint_id,invocation_id,metadata_json,created_at) "
            "VALUES('duplicate-audit','duplicate-audit',0,'safe','success','system',NULL,"
            "'invocation','base',NULL,'ep','base','{}',0)"
        )
        連線.execute(
            "INSERT INTO invocation_sensitive_hits(id,invocation_id,tool_call_id,target_type,"
            "detector_type,json_path,start_offset,end_offset,audit_event_id,detected_at) "
            "VALUES('duplicate-hit','base',NULL,'input','email','/safe',0,1,'duplicate-audit',0)"
        )
    before = _數量(路徑)
    audits = (lambda: "duplicate-audit") if stage == "audit_insert" else (lambda: "new-audit")
    hits = (lambda: "new-hit") if stage == "audit_insert" else (lambda: "duplicate-hit")
    庫 = SQLite呼叫儲存庫(
        路徑, 時鐘=lambda: 2, 識別碼工廠=lambda: "inv",
        敏感交易協調器=_協調器(路徑, audits=audits, hits=hits),
    )
    with pytest.raises(呼叫儲存錯誤, match="^呼叫建立失敗$"):
        庫.建立已解析呼叫("ep", "ver", "req", {"mail": _安全標記()})
    assert _數量(路徑) == before


class _失敗連線(sqlite3.Connection):
    失敗階段 = ""
    回滾數 = 0

    def execute(self, sql, parameters=(), /):
        if type(self).失敗階段 == "tool_row" and sql.startswith("INSERT INTO endpoint_tool_calls("):
            raise sqlite3.OperationalError("fixed")
        return super().execute(sql, parameters)

    def commit(self):
        if type(self).失敗階段 == "commit":
            raise sqlite3.OperationalError("fixed")
        return super().commit()

    def rollback(self):
        type(self).回滾數 += 1
        return super().rollback()


class _無寫入協調器:
    def 偵測呼叫(self, input_value, metadata):
        return 準備含敏感偵測的呼叫擷取(擷取階段.AUTHENTICATED, input_value, metadata)
    def 偵測工具(self, arguments, result):
        return 準備含敏感偵測的呼叫擷取(
            擷取階段.AUTHENTICATED, {}, None,
            tool_arguments=arguments, tool_result=result,
        )
    def 寫入呼叫交易(self, *_args, **_kwargs):
        return None


@pytest.mark.parametrize("stage", ["tool_row", "commit"])
def test_tool_row與commit失敗都不回成功receipt且回滾(tmp_path, stage):
    路徑 = _建立資料庫(tmp_path)
    base = SQLite呼叫儲存庫(路徑, 識別碼工廠=lambda: "inv")
    base.建立已解析呼叫("ep", "ver", "req", {})
    base.標記執行中("inv")
    _失敗連線.失敗階段 = stage
    _失敗連線.回滾數 = 0
    def factory(*args, **kwargs):
        return sqlite3.connect(*args, **kwargs, factory=_失敗連線)
    庫 = SQLite呼叫儲存庫(
        路徑, 連線工廠=factory, 敏感交易協調器=_無寫入協調器(),
    )
    with pytest.raises(呼叫儲存錯誤, match="^工具呼叫附加失敗$"):
        庫.附加工具呼叫("inv", "tool", "lookup", {}, "success", result={})
    assert _失敗連線.回滾數 == 1
    assert _數量(路徑) == (1, 0, 0, 0, 0)
