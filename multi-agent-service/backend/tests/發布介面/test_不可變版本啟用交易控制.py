"""PUB P06 啟用 transaction failpoint、控制 precedence 與路徑回歸。"""
import json
import os
import sqlite3

import pytest

from 繁中代理.發布介面.資料庫 import 初始化發布介面資料庫
from 繁中代理.發布介面.規劃.版本服務 import (
    SQLite版本配置服務, 版本啟用輸入錯誤, 版本啟用錯誤,
)


def _資料庫(tmp_path, name="activation-control.db"):
    path = tmp_path / name
    初始化發布介面資料庫(path)
    connection = sqlite3.connect(path)
    connection.execute("INSERT INTO service_accounts VALUES('account-1',1,NULL)")
    connection.execute("INSERT INTO published_endpoints VALUES('endpoint-marker','owner-marker','account-1','demo','active','version-1-marker',1,1,60,60)")
    for number in (1, 2):
        manifest = json.dumps(
            {"prompt_marker": "PROMPT-MARKER", "sha256": str(number) * 64},
            sort_keys=True, separators=(",", ":"),
        )
        connection.execute(
            "INSERT INTO published_endpoint_versions VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (f"version-{number}-marker", "endpoint-marker", number, "需求", "提示", "[]", "[]", "{}",
             "runtime-1", "{}", "{}", manifest, None, "{}", 0, "owner-marker", float(number)),
        )
    connection.commit(); connection.close()
    return path


def _狀態(path):
    connection = sqlite3.connect(path)
    result = (
        connection.execute("SELECT current_version_id,updated_at FROM published_endpoints").fetchone(),
        connection.execute("SELECT id,request_id FROM audit_events ORDER BY id").fetchall(),
    )
    connection.close()
    return result


class _注入連線(sqlite3.Connection):
    stage = primary = rollback_failure = close_failure = None
    begin_calls = rollback_calls = commit_calls = close_calls = 0

    def execute(self, sql, parameters=()):
        kind = None
        if sql == "BEGIN IMMEDIATE":
            kind = "begin"; type(self).begin_calls += 1
        elif sql == "ROLLBACK":
            kind = "rollback"; type(self).rollback_calls += 1
        elif sql == "COMMIT":
            kind = "commit"; type(self).commit_calls += 1
        elif sql.startswith("SELECT version_number,skill_bundle"):
            kind = "candidate"
        elif sql.startswith("INSERT INTO audit_events"):
            kind = "audit"
        elif sql.startswith("UPDATE published_endpoints"):
            kind = "update"
        failure = type(self).rollback_failure if kind == "rollback" else type(self).primary
        if (kind == "rollback" and failure is not None) or (kind == type(self).stage and failure is not None):
            raise failure
        return super().execute(sql, parameters)

    def close(self):
        type(self).close_calls += 1
        if type(self).close_failure is not None:
            raise type(self).close_failure
        return super().close()


def _服務(path, *, stage=None, primary=None, rollback=None, close=None):
    cls = _注入連線
    cls.stage, cls.primary = stage, primary
    cls.rollback_failure, cls.close_failure = rollback, close
    cls.begin_calls = cls.rollback_calls = cls.commit_calls = cls.close_calls = 0
    def connect(*args, **kwargs):
        return sqlite3.connect(*args, **kwargs, factory=cls)
    return SQLite版本配置服務(path, lambda: "unused", lambda: 0.0, connect)


def _啟用(service, *, verifier=lambda *_args: True, audit=lambda: "audit-marker", clock=lambda: 20.0, request="request-marker"):
    return service.啟用(
        "owner-marker", "endpoint-marker", "version-2-marker", request_id=request,
        bundle_verifier=verifier, audit_id_factory=audit, clock=clock,
    )


def _含標記(value, marker, visited):
    if value is None or id(value) in visited:
        return False
    visited.add(id(value))
    if type(value) is str:
        return marker in value
    if type(value) is bytes:
        return marker.encode() in value
    if type(value) is dict:
        return any(_含標記(item, marker, visited) for pair in value.items() for item in pair)
    if type(value) in (list, tuple, set, frozenset):
        return any(_含標記(item, marker, visited) for item in value)
    if isinstance(value, BaseException):
        return (_含標記(value.args, marker, visited) or _含標記(value.__cause__, marker, visited)
                or _含標記(value.__context__, marker, visited) or _含標記(value.__dict__, marker, visited))
    try:
        attributes = object.__getattribute__(value, "__dict__")
    except (AttributeError, TypeError):
        attributes = None
    return type(attributes) is dict and _含標記(attributes, marker, visited)


def _確認乾淨(error, markers):
    names = set(); traceback = error.__traceback__
    while traceback is not None:
        frame = traceback.tb_frame
        if frame.f_code.co_filename.endswith("版本服務.py"):
            names.add(frame.f_code.co_name)
            for value in tuple(frame.f_locals.values()):
                for marker in markers:
                    assert not _含標記(value, marker, set()), (frame.f_code.co_name, marker)
        traceback = traceback.tb_next
    assert {"啟用", "_啟用交易"} <= names
    for marker in markers:
        assert not _含標記(error.__cause__, marker, set())
        assert not _含標記(error.__context__, marker, set())


@pytest.mark.parametrize("stage", ["candidate", "audit", "commit"])
def test_audit_INSERT與COMMIT普通失敗rollback_close一次且下次可成功(tmp_path, stage):
    path = _資料庫(tmp_path)
    with pytest.raises(版本啟用錯誤) as caught:
        _啟用(_服務(path, stage=stage, primary=sqlite3.OperationalError("PRIMARY")))
    assert caught.value.__cause__ is None and caught.value.__context__ is None
    assert (_注入連線.rollback_calls, _注入連線.close_calls) == (1, 1)
    assert _狀態(path) == (("version-1-marker", 1.0), [])
    assert _啟用(_服務(path)).new_version_id == "version-2-marker"


def test_BEGIN普通失敗不rollback_close一次且callbacks零次(tmp_path):
    path = _資料庫(tmp_path); calls = []
    with pytest.raises(版本啟用錯誤):
        _啟用(_服務(path, stage="begin", primary=RuntimeError("BEGIN")),
            verifier=lambda *_: calls.append("v"), audit=lambda: calls.append("a"), clock=lambda: calls.append("c"))
    assert calls == [] and (_注入連線.rollback_calls, _注入連線.close_calls) == (0, 1)
    assert _狀態(path) == (("version-1-marker", 1.0), [])


@pytest.mark.parametrize("control", [KeyboardInterrupt("K"), SystemExit("I"), GeneratorExit("G")])
def test_commit成功後close控制精確且pointer_audit耐久(tmp_path, control):
    path = _資料庫(tmp_path)
    with pytest.raises(type(control)) as caught:
        _啟用(_服務(path, close=control))
    assert caught.value is control and control.__cause__ is None and control.__context__ is None
    assert (_注入連線.rollback_calls, _注入連線.close_calls) == (0, 1)
    assert _狀態(path) == (("version-2-marker", 20.0), [("audit-marker", "request-marker")])


class _子鍵盤(KeyboardInterrupt):
    pass


@pytest.mark.parametrize("source,control", [("verifier", KeyboardInterrupt("PRIMARY-K")), ("verifier", SystemExit("PRIMARY-I")), ("verifier", GeneratorExit("PRIMARY-G")), ("verifier", _子鍵盤("PRIMARY-SUB")), ("begin", SystemExit("BEGIN-PRIMARY"))])
def test_verifier與BEGIN_primary控制含subclass勝過cleanup且frames乾淨(tmp_path, source, control):
    path = _資料庫(tmp_path)
    rollback, close = SystemExit("ROLLBACK-LOSER"), GeneratorExit("CLOSE-LOSER")
    def verifier(manifest, *_args):
        assert manifest["prompt_marker"] == "PROMPT-MARKER"
        raise control
    with pytest.raises(type(control)) as caught:
        _啟用(_服務(path, stage="begin" if source == "begin" else None,
                    primary=control if source == "begin" else None, rollback=rollback, close=close), verifier=verifier)
    assert caught.value is control and control.__cause__ is None and control.__context__ is None
    assert (_注入連線.rollback_calls, _注入連線.close_calls) == (source != "begin", 1)
    _確認乾淨(control, ("owner-marker", "endpoint-marker", "version-2-marker", "request-marker", "audit-marker", "PROMPT-MARKER", "22222222", "ROLLBACK-LOSER", "CLOSE-LOSER"))
    assert _狀態(path) == (("version-1-marker", 1.0), [])


@pytest.mark.parametrize("rollback,close,winner", [
    (KeyboardInterrupt("ROLLBACK-WIN"), GeneratorExit("CLOSE-LOSE"), "rollback"),
    (RuntimeError("ROLLBACK-ORD"), SystemExit("CLOSE-WIN"), "close"),
])
def test_ordinary_primary清理控制precedence精確(tmp_path, rollback, close, winner):
    path = _資料庫(tmp_path)
    expected = rollback if winner == "rollback" else close
    with pytest.raises(type(expected)) as caught:
        _啟用(_服務(path, stage="audit", primary=RuntimeError("PRIMARY-LOSE"), rollback=rollback, close=close))
    assert caught.value is expected and expected.__cause__ is None and expected.__context__ is None
    _確認乾淨(expected, ("PRIMARY-LOSE", "CLOSE-LOSE" if winner == "rollback" else "ROLLBACK-ORD"))
    assert (_注入連線.rollback_calls, _注入連線.close_calls) == (1, 1)


@pytest.mark.parametrize("which,control", [("audit", KeyboardInterrupt("AUDIT-K")), ("clock", SystemExit("CLOCK-I"))])
def test_audit_factory與clock控制精確rollback(tmp_path, which, control):
    path = _資料庫(tmp_path)
    def audit():
        if which == "audit": raise control
        return "audit-marker"
    def clock(): raise control
    with pytest.raises(type(control)) as caught:
        _啟用(_服務(path), audit=audit, clock=clock)
    assert caught.value is control and (_注入連線.rollback_calls, _注入連線.close_calls) == (1, 1)
    assert _狀態(path) == (("version-1-marker", 1.0), [])


@pytest.mark.parametrize("audit,now", [("bad id!", 20.0), ("audit-marker", float("nan")), ("audit-marker", True)])
def test_malformed_audit_id與clock固定錯誤rollback(tmp_path, audit, now):
    path = _資料庫(tmp_path)
    with pytest.raises(版本啟用錯誤, match="^版本啟用失敗$") as caught:
        _啟用(_服務(path), audit=lambda: audit, clock=lambda: now)
    assert caught.value.__cause__ is None and caught.value.__context__ is None
    assert (_注入連線.rollback_calls, _注入連線.close_calls) == (1, 1)


def test_request_id_None寫入NULL且malformed_subclass在open_callback前拒絕(tmp_path):
    path = _資料庫(tmp_path)
    _啟用(_服務(path), request=None)
    assert _狀態(path)[1] == [("audit-marker", None)]
    class Text(str): pass
    for request in ("bad id!", "x" * 129, Text("request-2")):
        opens = []; calls = []
        service = SQLite版本配置服務(path, lambda: "unused", lambda: 0.0, lambda *a, **k: opens.append(1))
        with pytest.raises(版本啟用輸入錯誤):
            _啟用(service, request=request, verifier=lambda *_: calls.append("v"), audit=lambda: calls.append("a"), clock=lambda: calls.append("c"))
        assert opens == calls == []


@pytest.mark.parametrize("mode", ["symlink", "schema", "replacement"])
def test_path整合在callbacks前拒絕且helper_close精確(tmp_path, mode):
    path = _資料庫(tmp_path, "target.db"); calls = []
    if mode == "symlink":
        link = tmp_path / "link.db"; link.symlink_to(path); path = link
        service = SQLite版本配置服務(path, lambda: "u", lambda: 0.0, lambda *a, **k: calls.append("open"))
    else:
        if mode == "schema":
            connection = sqlite3.connect(path); connection.execute("DROP TRIGGER published_endpoint_versions_no_update"); connection.commit(); connection.close()
            service = _服務(path)
        else:
            replacement = _資料庫(tmp_path, "replacement.db")
            _注入連線.close_calls = 0
            def connect(*args, **kwargs):
                connection = sqlite3.connect(*args, **kwargs, factory=_注入連線); os.replace(replacement, path); return connection
            service = SQLite版本配置服務(path, lambda: "u", lambda: 0.0, connect)
    with pytest.raises(版本啟用錯誤):
        _啟用(service, verifier=lambda *_: calls.append("v"), audit=lambda: calls.append("a"), clock=lambda: calls.append("c"))
    assert calls == ([] if mode != "symlink" else [])
    if mode != "symlink": assert _注入連線.close_calls == 1
