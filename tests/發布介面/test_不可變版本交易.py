"""PUB P05 transaction failpoint、callback 與路徑整合回歸。"""

import os
import sqlite3
from dataclasses import FrozenInstanceError

import pytest

from 繁中代理.發布介面.資料庫 import 初始化發布介面資料庫
from 繁中代理.發布介面.規劃.端點發布 import 發布版本快照
from 繁中代理.發布介面.規劃.版本服務 import SQLite版本配置服務, 版本配置錯誤, 版本配置結果


def _快照(**覆寫):
    值 = dict(
        original_requirement_text="需求", system_prompt="提示",
        allowed_skills=["skill.one"], allowed_tools=["tool.one"],
        tool_schema_snapshot={"tool.one": {"revision": "r1"}},
        tool_runtime_revision="runtime-1", model_config_snapshot={"model": "m1"},
        retry_policy={"max_attempts": 1}, skill_bundle_manifest={"sha256": "a" * 64},
        input_schema={"nested": ["before"]}, response_schema={"type": "string"},
        created_by_user_id="owner",
    )
    值.update(覆寫)
    return 發布版本快照(**值)


def _資料庫(tmp_path, name="versions.db"):
    path = tmp_path / name
    初始化發布介面資料庫(path)
    connection = sqlite3.connect(path)
    connection.execute("INSERT INTO service_accounts VALUES('account-1',1,NULL)")
    connection.execute("INSERT INTO published_endpoints VALUES('endpoint-1','owner','account-1','demo','active','version-1',1,1,60,60)")
    connection.execute(
        "INSERT INTO published_endpoint_versions VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        ("version-1", "endpoint-1", 1, "舊需求", "舊提示", "[]", "[]", "{}", "runtime-1",
         "{}", "{}", "{}", '{"old":true}', '{"type":"string"}', 0, "owner", 1),
    )
    connection.commit()
    connection.close()
    return path


def _列(path):
    with sqlite3.connect(path) as connection:
        return connection.execute(
            "SELECT id,version_number,input_schema_json FROM published_endpoint_versions ORDER BY version_number"
        ).fetchall()


class _失敗連線(sqlite3.Connection):
    stage = failure = None
    begin_calls = rollback_calls = commit_calls = close_calls = insert_calls = 0

    def execute(self, sql, parameters=()):
        kind = None
        if sql == "BEGIN IMMEDIATE":
            kind = "begin"
            type(self).begin_calls += 1
        elif sql == "ROLLBACK":
            kind = "rollback"
            type(self).rollback_calls += 1
        elif sql == "COMMIT":
            kind = "commit"
            type(self).commit_calls += 1
        elif sql.startswith("INSERT INTO published_endpoint_versions"):
            kind = "insert"
            type(self).insert_calls += 1
        if type(self).stage is not None and kind == type(self).stage:
            raise type(self).failure
        return super().execute(sql, parameters)

    def close(self):
        type(self).close_calls += 1
        if type(self).stage == "close":
            raise type(self).failure
        return super().close()


def _服務(path, stage=None, failure=None, callbacks=None, version="version-2", connect=None):
    callbacks = [] if callbacks is None else callbacks
    _失敗連線.stage, _失敗連線.failure = stage, failure
    for name in ("begin_calls", "rollback_calls", "commit_calls", "close_calls", "insert_calls"):
        setattr(_失敗連線, name, 0)
    def factory(*args, **kwargs):
        return sqlite3.connect(*args, **kwargs, factory=_失敗連線)
    def identifier():
        callbacks.append(("id", _失敗連線.begin_calls, _失敗連線.in_transaction if False else True))
        return version
    def clock():
        callbacks.append(("clock", _失敗連線.begin_calls))
        return 20.0
    return SQLite版本配置服務(path, identifier, clock, connect or factory)


@pytest.mark.parametrize("stage", ["insert", "commit"])
def test_INSERT與COMMIT普通失敗rollback_close一次且下一次仍配置v2(tmp_path, stage):
    path = _資料庫(tmp_path)
    with pytest.raises(版本配置錯誤, match="^版本配置失敗$") as caught:
        _服務(path, stage, sqlite3.OperationalError("PRIMARY")).配置("owner", "endpoint-1", _快照())
    assert caught.value.__cause__ is None and caught.value.__context__ is None
    assert (_失敗連線.rollback_calls, _失敗連線.close_calls) == (1, 1)
    assert [row[:2] for row in _列(path)] == [("version-1", 1)]
    result = _服務(path).配置("owner", "endpoint-1", _快照())
    assert result.version_number == 2 and [row[1] for row in _列(path)] == [1, 2]


def test_BEGIN普通失敗不rollback_close一次且callback零次(tmp_path):
    path = _資料庫(tmp_path)
    calls = []
    with pytest.raises(版本配置錯誤):
        _服務(path, "begin", sqlite3.OperationalError("BEGIN"), calls).配置("owner", "endpoint-1", _快照())
    assert calls == []
    assert (_失敗連線.rollback_calls, _失敗連線.close_calls) == (0, 1)
    assert len(_列(path)) == 1


def test_commit成功後ordinary_close失敗仍成功且durable(tmp_path):
    path = _資料庫(tmp_path)
    result = _服務(path, "close", RuntimeError("CLOSE")).配置("owner", "endpoint-1", _快照())
    assert result == 版本配置結果("version-2", "endpoint-1", 2, True, 20.0)
    assert _失敗連線.close_calls == 1 and len(_列(path)) == 2


def test_結果只含frozen_scalar且無serializer或schema_secret():
    result = 版本配置結果("version-2", "endpoint-1", 2, True, 20.0)
    assert result.__slots__ == ("version_id", "endpoint_id", "version_number", "schema_changed", "created_at")
    assert all(type(getattr(result, name)) in (str, int, bool, float) for name in result.__slots__)
    assert not any(hasattr(result, name) for name in ("model_dump", "dict", "to_dict", "json"))
    assert "TRACEP05" not in repr(result)


@pytest.mark.parametrize("control", [KeyboardInterrupt("K"), SystemExit("I"), GeneratorExit("G")])
def test_commit成功後close控制精確傳播且row_durable(tmp_path, control):
    path = _資料庫(tmp_path)
    with pytest.raises(type(control)) as caught:
        _服務(path, "close", control).配置("owner", "endpoint-1", _快照())
    assert caught.value is control and caught.value.args == control.args
    assert control.__cause__ is None and control.__context__ is None
    assert _失敗連線.close_calls == 1 and len(_列(path)) == 2


def test_callback在access後且BEGIN鎖內呼叫並可配置detached快照(tmp_path):
    path = _資料庫(tmp_path)
    _失敗連線.stage = _失敗連線.failure = None
    original = _快照()
    observations = []
    def connect(*args, **kwargs):
        connection = sqlite3.connect(*args, **kwargs, factory=_失敗連線)
        def identifier():
            observations.append(("id", connection.in_transaction))
            object.__setattr__(original, "input_schema", {"nested": ["after"]})
            object.__setattr__(original, "system_prompt", "after-secret")
            return "version-2"
        service._版本識別工廠 = identifier
        return connection
    service = SQLite版本配置服務(path, lambda: "unused", lambda: observations.append(("clock", True)) or 20.0, connect)
    result = service.配置("owner", "endpoint-1", original)
    assert observations == [("id", True), ("clock", True)]
    assert result.version_number == 2 and _列(path)[1][2] == '{"nested":["before"]}'
    assert sqlite3.connect(path).execute("SELECT system_prompt,current_version_id FROM published_endpoint_versions JOIN published_endpoints ON published_endpoints.id=endpoint_id WHERE version_number=2").fetchone() == ("提示", "version-1")
    with pytest.raises(FrozenInstanceError):
        result.version_number = 9
    assert "after-secret" not in repr(result)


def test_symlink在open與callback前拒絕(tmp_path):
    real = _資料庫(tmp_path, "real.db")
    link = tmp_path / "link.db"
    link.symlink_to(real)
    opens, calls = [], []
    service = SQLite版本配置服務(link, lambda: calls.append("id"), lambda: calls.append("clock"), lambda *a, **k: opens.append(1))
    with pytest.raises(版本配置錯誤):
        service.配置("owner", "endpoint-1", _快照())
    assert opens == calls == [] and len(_列(real)) == 1


@pytest.mark.parametrize("drift", ["schema", "ledger"])
def test_wrong_schema或fingerprint拒絕_callback零_close一次(tmp_path, drift):
    path = _資料庫(tmp_path)
    connection = sqlite3.connect(path)
    connection.execute("DROP TRIGGER published_endpoint_versions_no_update")
    if drift == "ledger":
        connection.execute("DELETE FROM published_api_schema_migrations WHERE version=5")
    connection.commit(); connection.close()
    calls = []
    with pytest.raises(版本配置錯誤):
        _服務(path, callbacks=calls).配置("owner", "endpoint-1", _快照())
    assert calls == [] and _失敗連線.close_calls == 1 and len(_列(path)) == 1


def test_connect後path替換拒絕且callback零替代檔無寫入(tmp_path):
    path = _資料庫(tmp_path, "target.db")
    replacement = _資料庫(tmp_path, "replacement.db")
    calls = []
    def connect(*args, **kwargs):
        connection = sqlite3.connect(*args, **kwargs, factory=_失敗連線)
        os.replace(replacement, path)
        return connection
    with pytest.raises(版本配置錯誤):
        _服務(path, callbacks=calls, connect=connect).配置("owner", "endpoint-1", _快照())
    assert calls == [] and _失敗連線.close_calls == 1 and len(_列(path)) == 1
