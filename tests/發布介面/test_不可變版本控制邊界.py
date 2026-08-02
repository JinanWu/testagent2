"""PUB P05 callback、SQL 與 helper 控制流回歸。"""

import sqlite3

import pytest

from 繁中代理.發布介面.資料庫 import 初始化發布介面資料庫
from 繁中代理.發布介面.規劃.端點發布 import 發布版本快照
from 繁中代理.發布介面.規劃 import 版本服務 as 版本模組
from 繁中代理.發布介面.規劃.版本服務 import SQLite版本配置服務, 版本配置輸入錯誤


def _快照(marker="TRACEP05"):
    return 發布版本快照(
        original_requirement_text=f"需求-{marker}", system_prompt=f"提示-{marker}",
        allowed_skills=[f"skill.{marker}"], allowed_tools=["tool.one"],
        tool_schema_snapshot={"tool.one": {"marker": marker}}, tool_runtime_revision="runtime-1",
        model_config_snapshot={"model": marker}, retry_policy={"marker": marker},
        skill_bundle_manifest={"sha256": "a" * 64, "marker": marker},
        input_schema={"marker": marker}, response_schema={"marker": marker},
        created_by_user_id="owner",
    )


def _資料庫(tmp_path):
    path = tmp_path / "controls.db"
    初始化發布介面資料庫(path)
    connection = sqlite3.connect(path)
    connection.execute("INSERT INTO service_accounts VALUES('account-1',1,NULL)")
    connection.execute("INSERT INTO published_endpoints VALUES('endpoint-1','owner','account-1','demo','active','version-1',1,1,60,60)")
    connection.execute(
        "INSERT INTO published_endpoint_versions VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        ("version-1", "endpoint-1", 1, "old", "old", "[]", "[]", "{}", "runtime-1",
         "{}", "{}", "{}", "{}", "{}", 0, "owner", 1),
    )
    connection.commit(); connection.close()
    return path


def _列數(path):
    connection = sqlite3.connect(path)
    result = connection.execute("SELECT count(*) FROM published_endpoint_versions").fetchone()[0]
    connection.close()
    return result


def _含標記(value, marker, visited):
    if value is None or id(value) in visited:
        return False
    visited.add(id(value))
    if type(value) is str:
        return marker in value
    if type(value) is bytes:
        return marker.encode() in value
    if type(value) is dict:
        return any(_含標記(item, marker, visited) for pair in dict.items(value) for item in pair)
    if type(value) in (list, tuple, set, frozenset):
        return any(_含標記(item, marker, visited) for item in value)
    if isinstance(value, BaseException):
        return _含標記(value.args, marker, visited) or _含標記(value.__cause__, marker, visited) or _含標記(value.__context__, marker, visited)
    slots = getattr(type(value), "__slots__", ())
    if type(slots) is str:
        slots = (slots,)
    if type(slots) in (tuple, list):
        for name in slots:
            try:
                if _含標記(object.__getattribute__(value, name), marker, visited):
                    return True
            except (AttributeError, TypeError):
                pass
    try:
        attributes = object.__getattribute__(value, "__dict__")
    except (AttributeError, TypeError):
        attributes = None
    return type(attributes) is dict and _含標記(attributes, marker, visited)


def _確認frames乾淨(error, marker="TRACEP05"):
    found = False
    traceback = error.__traceback__
    while traceback is not None:
        frame = traceback.tb_frame
        if frame.f_code.co_filename.endswith("版本服務.py"):
            found = True
            for value in tuple(frame.f_locals.values()):
                assert not _含標記(value, marker, set()), frame.f_code.co_name
        traceback = traceback.tb_next
    assert found


class _CursorProxy:
    def __init__(self, cursor, failure):
        self.cursor, self.failure = cursor, failure
    def fetchone(self):
        raise self.failure


class _控制連線(sqlite3.Connection):
    stage = control = None
    rollback_calls = close_calls = 0
    access_seen = False

    def execute(self, sql, parameters=()):
        if sql == "ROLLBACK":
            type(self).rollback_calls += 1
        if sql.startswith("SELECT owner_user_id"):
            type(self).access_seen = True
        if type(self).stage == "insert" and sql.startswith("INSERT INTO published_endpoint_versions"):
            raise type(self).control
        if type(self).stage == "commit" and sql == "COMMIT":
            raise type(self).control
        cursor = super().execute(sql, parameters)
        if type(self).stage == "aggregate" and sql.startswith("SELECT count(*)"):
            return _CursorProxy(cursor, type(self).control)
        if type(self).stage == "previous" and sql.startswith("SELECT input_schema_json"):
            return _CursorProxy(cursor, type(self).control)
        return cursor

    def close(self):
        type(self).close_calls += 1
        return super().close()


def _服務(path, identifier, clock, stage=None, control=None):
    _控制連線.stage, _控制連線.control = stage, control
    _控制連線.rollback_calls = _控制連線.close_calls = 0
    _控制連線.access_seen = False
    def connect(*args, **kwargs):
        return sqlite3.connect(*args, **kwargs, factory=_控制連線)
    return SQLite版本配置服務(path, identifier, clock, connect)


@pytest.mark.parametrize("which", ["id", "clock"])
@pytest.mark.parametrize("control", [KeyboardInterrupt("CALLBACK-K"), SystemExit("CALLBACK-S"), GeneratorExit("CALLBACK-G")])
def test_id_factory與clock控制在access後鎖內exact傳播rollback(tmp_path, which, control):
    path = _資料庫(tmp_path)
    calls = []
    def identifier():
        calls.append(("id", _控制連線.access_seen))
        if which == "id":
            raise control
        return "version-2"
    def clock():
        calls.append(("clock", _控制連線.access_seen))
        raise control
    with pytest.raises(type(control)) as caught:
        _服務(path, identifier, clock).配置("owner", "endpoint-1", _快照())
    assert caught.value is control and control.__cause__ is None and control.__context__ is None
    assert calls == ([('id', True)] if which == "id" else [('id', True), ('clock', True)])
    assert (_控制連線.rollback_calls, _控制連線.close_calls, _列數(path)) == (1, 1, 1)
    _確認frames乾淨(control)


@pytest.mark.parametrize("stage,control", [
    ("aggregate", KeyboardInterrupt("SQL-K")), ("previous", SystemExit("SQL-S")),
    ("insert", GeneratorExit("SQL-G")), ("commit", KeyboardInterrupt("COMMIT-K")),
])
def test_aggregate_previous_INSERT_COMMIT控制精確且snapshot不洩漏(tmp_path, stage, control):
    path = _資料庫(tmp_path)
    with pytest.raises(type(control)) as caught:
        _服務(path, lambda: "version-2", lambda: 2.0, stage, control).配置("owner", "endpoint-1", _快照())
    assert caught.value is control and control.__cause__ is None and control.__context__ is None
    assert (_控制連線.rollback_calls, _控制連線.close_calls, _列數(path)) == (1, 1, 1)
    _確認frames乾淨(control)


@pytest.mark.parametrize("helper,control", [
    ("_正規JSON", SystemExit("JSON-S")), ("_schema等價", GeneratorExit("SCHEMA-G")),
])
def test_正規JSON與schema等價控制精確且清理(tmp_path, monkeypatch, helper, control):
    path = _資料庫(tmp_path)
    original = getattr(版本模組, helper)
    calls = [0]
    def fail(*args, **kwargs):
        calls[0] += 1
        if (helper == "_正規JSON" and calls[0] == 1) or helper == "_schema等價":
            raise control
        return original(*args, **kwargs)
    monkeypatch.setattr(版本模組, helper, fail)
    with pytest.raises(type(control)) as caught:
        _服務(path, lambda: "version-2", lambda: 2.0).配置("owner", "endpoint-1", _快照())
    assert caught.value is control and (_控制連線.rollback_calls, _控制連線.close_calls, _列數(path)) == (1, 1, 1)
    _確認frames乾淨(control)


@pytest.mark.parametrize("hostile", ["missing", "cycle", "nonfinite"])
def test_hostile_forged_snapshot在open前固定拒絕(hostile, tmp_path):
    snapshot = _快照("safe")
    if hostile == "missing":
        object.__delattr__(snapshot, "system_prompt")
    elif hostile == "cycle":
        cycle = {}; cycle["self"] = cycle
        object.__setattr__(snapshot, "input_schema", cycle)
    else:
        object.__setattr__(snapshot, "response_schema", {"n": float("nan")})
    opens = []
    service = SQLite版本配置服務(tmp_path / "absent.db", lambda: "v", lambda: 1.0, lambda *a, **k: opens.append(1))
    with pytest.raises(版本配置輸入錯誤, match="^版本配置輸入無效$") as caught:
        service.配置("owner", "endpoint-1", snapshot)
    assert caught.value.__cause__ is None and caught.value.__context__ is None and opens == []
