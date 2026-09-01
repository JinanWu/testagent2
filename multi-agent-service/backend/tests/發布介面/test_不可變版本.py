"""PUB P05 不可變版本配置與 canonical schema_changed。"""

import itertools
import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor

import pytest

from 繁中代理.發布介面.資料庫 import 初始化發布介面資料庫
from 繁中代理.發布介面.規劃.端點發布 import 發布版本快照
from 繁中代理.發布介面.規劃.版本服務 import (
    SQLite版本配置服務,
    版本存取錯誤,
    版本配置結果,
)


def _快照(*, requirement="需求二", input_schema=None, response_schema=None, owner="owner"):
    return 發布版本快照(
        original_requirement_text=requirement,
        system_prompt="系統提示二",
        allowed_skills=["skill.one"],
        allowed_tools=["tool.one"],
        tool_schema_snapshot={"tool.one": {"revision": "r1"}},
        tool_runtime_revision="runtime-1",
        model_config_snapshot={"model": "m1", "temperature": 0},
        retry_policy={"max_attempts": 2},
        skill_bundle_manifest={"sha256": "a" * 64},
        input_schema=input_schema,
        response_schema=response_schema or {"type": "string"},
        created_by_user_id=owner,
    )


def _資料庫(tmp_path, *, status="active"):
    path = tmp_path / "versions.db"
    初始化發布介面資料庫(path)
    connection = sqlite3.connect(path)
    connection.execute("PRAGMA foreign_keys=ON")
    connection.execute("INSERT INTO service_accounts VALUES('account-1',1,NULL)")
    connection.execute(
        "INSERT INTO published_endpoints(id,owner_user_id,service_account_id,slug,status,current_version_id,created_at,updated_at,rate_limit_requests,rate_limit_window_seconds) VALUES('endpoint-1','owner','account-1','demo',?,NULL,1,1,60,60)",
        (status,),
    )
    fields = (
        "version-1", "endpoint-1", 1, "原始需求一", "系統提示一", '["skill.one"]',
        '["tool.one"]', '{"tool.one":{"revision":"r1"}}', "runtime-1",
        '{"model":"m1","temperature":0}', '{"max_attempts":1}',
        '{"sha256":"' + "a" * 64 + '"}', '{"a":1,"n":-0.0}',
        '{"properties":{"x":{"type":"string"}},"type":"object"}', 0, "owner", 1,
    )
    connection.execute(
        "INSERT INTO published_endpoint_versions VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", fields,
    )
    connection.execute("UPDATE published_endpoints SET current_version_id='version-1' WHERE id='endpoint-1'")
    connection.commit()
    connection.close()
    return path


def _服務(path, version_id="version-2", calls=None):
    calls = [] if calls is None else calls
    def identifier():
        calls.append("id")
        return version_id
    def clock():
        calls.append("clock")
        return 20.0
    return SQLite版本配置服務(path, identifier, clock)


def _列(path):
    return sqlite3.connect(path).execute(
        "SELECT id,version_number,original_requirement_text,input_schema_json,response_schema_json,schema_changed FROM published_endpoint_versions ORDER BY version_number"
    ).fetchall()


def test_v2正規等價schema為false且不改current與舊列(tmp_path):
    path = _資料庫(tmp_path)
    before = sqlite3.connect(path).execute("SELECT * FROM published_endpoint_versions WHERE version_number=1").fetchone()
    snapshot = _快照(
        input_schema={"n": 0, "a": 1.0},
        response_schema={"type": "object", "properties": {"x": {"type": "string"}}},
    )

    result = _服務(path).配置("owner", "endpoint-1", snapshot)

    assert result == 版本配置結果("version-2", "endpoint-1", 2, False, 20.0)
    connection = sqlite3.connect(path)
    assert connection.execute("SELECT current_version_id FROM published_endpoints").fetchone() == ("version-1",)
    assert connection.execute("SELECT * FROM published_endpoint_versions WHERE version_number=1").fetchone() == before
    assert _列(path)[1][2:] == ("需求二", '{"a":1.0,"n":0}', '{"properties":{"x":{"type":"string"}},"type":"object"}', 0)


@pytest.mark.parametrize("input_schema,response_schema", [
    ({"a": 2}, {"type": "object", "properties": {"x": {"type": "string"}}}),
    ({"a": 1, "n": 0}, {"type": "number"}),
    ({"a": 2}, {"type": "number"}),
    ({"a": 1, "n": 0}, {"enum": [2, 1]}),
    ({"a": 10**200}, {"type": "object", "properties": {"x": {"type": "string"}}}),
])
def test_input_response_both_array與huge_int語意差異為true(tmp_path, input_schema, response_schema):
    path = _資料庫(tmp_path)
    assert _服務(path).配置("owner", "endpoint-1", _快照(input_schema=input_schema, response_schema=response_schema)).schema_changed is True


@pytest.mark.parametrize("owner,endpoint,status", [
    ("foreign", "endpoint-1", "active"), ("owner", "missing", "active"),
    ("owner", "endpoint-1", "disabled"), ("owner", "endpoint-1", "archived"),
])
def test_missing_foreign與非active同一固定存取錯誤且零callback(tmp_path, owner, endpoint, status):
    path = _資料庫(tmp_path, status=status)
    calls = []
    with pytest.raises(版本存取錯誤, match="^版本配置存取遭拒$") as error:
        _服務(path, calls=calls).配置(owner, endpoint, _快照(owner=owner))
    assert error.value.__cause__ is None and error.value.__context__ is None
    assert calls == [] and len(_列(path)) == 1


def test_forged_frozen_snapshot會被重建且caller不能指定號碼或schema_changed(tmp_path):
    path = _資料庫(tmp_path)
    snapshot = _快照()
    object.__setattr__(snapshot, "input_schema", {"a": object()})
    with pytest.raises(Exception):
        _服務(path).配置("owner", "endpoint-1", snapshot)
    assert len(_列(path)) == 1
    assert "version_number" not in SQLite版本配置服務.配置.__annotations__


def test_四個獨立連線writer配置連續2到5且快照不遺失(tmp_path):
    path = _資料庫(tmp_path)
    ids = itertools.count(2)
    def write(index):
        number = next(ids)
        service = _服務(path, f"version-{number}")
        return service.配置("owner", "endpoint-1", _快照(requirement=f"需求-{index}"))
    with ThreadPoolExecutor(max_workers=4) as executor:
        results = list(executor.map(write, range(4)))
    assert sorted(result.version_number for result in results) == [2, 3, 4, 5]
    rows = _列(path)
    assert [row[1] for row in rows] == [1, 2, 3, 4, 5]
    assert {row[2] for row in rows[1:]} == {f"需求-{index}" for index in range(4)}
    assert sqlite3.connect(path).execute("SELECT current_version_id FROM published_endpoints").fetchone() == ("version-1",)


def test_版本列禁止直接update與delete(tmp_path):
    path = _資料庫(tmp_path)
    connection = sqlite3.connect(path)
    for sql in ("UPDATE published_endpoint_versions SET system_prompt='x'", "DELETE FROM published_endpoint_versions"):
        with pytest.raises(sqlite3.DatabaseError, match="immutable"):
            connection.execute(sql)
