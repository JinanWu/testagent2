"""PUB P05 cleanup precedence 與控制流 traceback hygiene。"""

import sqlite3

import pytest

from 繁中代理.發布介面.資料庫 import 初始化發布介面資料庫
from 繁中代理.發布介面.規劃.端點發布 import 發布版本快照
from 繁中代理.發布介面.規劃.版本服務 import SQLite版本配置服務, 版本存取錯誤, 版本配置錯誤


def _快照(marker="safe", owner="owner"):
    return 發布版本快照(
        original_requirement_text=f"需求-{marker}", system_prompt=f"提示-{marker}",
        allowed_skills=[f"skill.{marker}"], allowed_tools=["tool.one"],
        tool_schema_snapshot={"tool.one": {"marker": marker}}, tool_runtime_revision="runtime-1",
        model_config_snapshot={"model": marker}, retry_policy={"marker": marker},
        skill_bundle_manifest={"sha256": "a" * 64, "marker": marker},
        input_schema={"marker": marker}, response_schema={"marker": marker},
        created_by_user_id=owner,
    )


def _資料庫(tmp_path):
    path = tmp_path / "cleanup.db"
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
    count = connection.execute("SELECT count(*) FROM published_endpoint_versions").fetchone()[0]
    connection.close()
    return count


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
        return (_含標記(value.args, marker, visited) or _含標記(value.__cause__, marker, visited)
                or _含標記(value.__context__, marker, visited) or _含標記(value.__dict__, marker, visited))
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


def _確認乾淨(error, markers, required=()):
    names = set()
    traceback = error.__traceback__
    while traceback is not None:
        frame = traceback.tb_frame
        if frame.f_code.co_filename.endswith("版本服務.py"):
            names.add(frame.f_code.co_name)
            for value in tuple(frame.f_locals.values()):
                for marker in markers:
                    assert not _含標記(value, marker, set()), (frame.f_code.co_name, marker)
        traceback = traceback.tb_next
    assert set(required) <= names
    for marker in markers:
        assert not _含標記(error.__cause__, marker, set())
        assert not _含標記(error.__context__, marker, set())


class _清理連線(sqlite3.Connection):
    primary_stage = primary = rollback_failure = close_failure = None
    rollback_calls = close_calls = 0

    def execute(self, sql, parameters=()):
        if sql == "ROLLBACK":
            type(self).rollback_calls += 1
            if type(self).rollback_failure is not None:
                raise type(self).rollback_failure
        if type(self).primary_stage == "begin" and sql == "BEGIN IMMEDIATE":
            raise type(self).primary
        if type(self).primary_stage == "insert" and sql.startswith("INSERT INTO published_endpoint_versions"):
            raise type(self).primary
        return super().execute(sql, parameters)

    def close(self):
        type(self).close_calls += 1
        if type(self).close_failure is not None:
            raise type(self).close_failure
        return super().close()


def _服務(path, *, stage=None, primary=None, rollback=None, close=None, identifier=None, clock=None):
    _清理連線.primary_stage, _清理連線.primary = stage, primary
    _清理連線.rollback_failure, _清理連線.close_failure = rollback, close
    _清理連線.rollback_calls = _清理連線.close_calls = 0
    def connect(*args, **kwargs):
        return sqlite3.connect(*args, **kwargs, factory=_清理連線)
    return SQLite版本配置服務(path, identifier or (lambda: "version-2"), clock or (lambda: 2.0), connect)


@pytest.mark.parametrize("rollback", [KeyboardInterrupt("ROLLBACK-K"), SystemExit("ROLLBACK-S"), GeneratorExit("ROLLBACK-G")])
def test_INSERT普通primary後rollback控制勝過close控制且圖乾淨(tmp_path, rollback):
    path = _資料庫(tmp_path)
    close = GeneratorExit("CLOSE-LOSER")
    with pytest.raises(type(rollback)) as caught:
        _服務(path, stage="insert", primary=RuntimeError("PRIMARY-SECRET"), rollback=rollback, close=close).配置("owner", "endpoint-1", _快照("SNAPSHOT-SECRET"))
    assert caught.value is rollback and rollback.__cause__ is None and rollback.__context__ is None
    _確認乾淨(rollback, ("PRIMARY-SECRET", "CLOSE-LOSER", "SNAPSHOT-SECRET"), ("_配置交易",))
    assert (_清理連線.rollback_calls, _清理連線.close_calls, _列數(path)) == (1, 1, 1)


@pytest.mark.parametrize("close", [KeyboardInterrupt("CLOSE-K"), SystemExit("CLOSE-S"), GeneratorExit("CLOSE-G")])
def test_rollback普通失敗後close控制精確勝出(tmp_path, close):
    path = _資料庫(tmp_path)
    with pytest.raises(type(close)) as caught:
        _服務(path, stage="insert", primary=RuntimeError("PRIMARY"), rollback=RuntimeError("ROLLBACK-ORDINARY"), close=close).配置("owner", "endpoint-1", _快照())
    assert caught.value is close and close.__cause__ is None and close.__context__ is None
    _確認乾淨(close, ("PRIMARY", "ROLLBACK-ORDINARY"), ("_配置交易",))
    assert (_清理連線.rollback_calls, _清理連線.close_calls, _列數(path)) == (1, 1, 1)


@pytest.mark.parametrize("control", [KeyboardInterrupt("PRIMARY-K"), SystemExit("PRIMARY-S"), GeneratorExit("PRIMARY-G")])
def test_id_factory_primary控制勝過rollback_close控制(tmp_path, control):
    path = _資料庫(tmp_path)
    def identifier():
        raise control
    with pytest.raises(type(control)) as caught:
        _服務(path, rollback=SystemExit("ROLLBACK-LOSER"), close=GeneratorExit("CLOSE-LOSER"), identifier=identifier).配置("owner", "endpoint-1", _快照("SNAPSHOT-SECRET"))
    assert caught.value is control and control.__cause__ is None and control.__context__ is None
    _確認乾淨(control, ("ROLLBACK-LOSER", "CLOSE-LOSER", "SNAPSHOT-SECRET"), ("_配置交易", "配置"))
    assert (_清理連線.rollback_calls, _清理連線.close_calls, _列數(path)) == (1, 1, 1)


def test_access普通失敗加rollback控制時cleanup勝出(tmp_path):
    path = _資料庫(tmp_path)
    rollback = KeyboardInterrupt("ACCESS-CLEANUP")
    with pytest.raises(KeyboardInterrupt) as caught:
        _服務(path, rollback=rollback).配置("foreign", "endpoint-1", _快照(owner="foreign"))
    assert caught.value is rollback and (_清理連線.rollback_calls, _清理連線.close_calls) == (1, 1)
    _確認乾淨(rollback, ("foreign",), ("_配置交易",))


def test_access普通失敗加普通cleanup仍固定存取錯誤且只rollback一次(tmp_path):
    path = _資料庫(tmp_path)
    with pytest.raises(版本存取錯誤, match="^版本配置存取遭拒$") as caught:
        _服務(path, rollback=RuntimeError("ROLLBACK"), close=RuntimeError("CLOSE")).配置("foreign", "endpoint-1", _快照(owner="foreign"))
    assert caught.value.__cause__ is None and caught.value.__context__ is None
    assert (_清理連線.rollback_calls, _清理連線.close_calls, _列數(path)) == (1, 1, 1)


@pytest.mark.parametrize("control", [KeyboardInterrupt("BEGIN-K"), SystemExit("BEGIN-S"), GeneratorExit("BEGIN-G")])
def test_BEGIN_primary控制勝過close且不rollback(tmp_path, control):
    path = _資料庫(tmp_path)
    with pytest.raises(type(control)) as caught:
        _服務(path, stage="begin", primary=control, close=GeneratorExit("CLOSE-LOSER")).配置("owner", "endpoint-1", _快照("SNAPSHOT-SECRET"))
    assert caught.value is control and (_清理連線.rollback_calls, _清理連線.close_calls) == (0, 1)
    _確認乾淨(control, ("CLOSE-LOSER", "SNAPSHOT-SECRET"), ("_配置交易", "配置"))
