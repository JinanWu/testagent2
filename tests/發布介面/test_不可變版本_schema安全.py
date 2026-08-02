"""PUB P05 schema canonical identity、strict loader 與資源界限。"""

import json
import sqlite3

import pytest

from 繁中代理.發布介面.資料庫 import 初始化發布介面資料庫
from 繁中代理.發布介面.規劃.端點發布 import 發布版本快照
from 繁中代理.發布介面.規劃.版本服務 import (
    SQLite版本配置服務,
    版本配置錯誤,
    _schema等價,
)


def _snapshot(input_schema=None, response_schema=None):
    return 發布版本快照(
        original_requirement_text="需求", system_prompt="提示",
        allowed_skills=["skill.one"], allowed_tools=["tool.one"],
        tool_schema_snapshot={"tool.one": {"revision": "r1"}},
        tool_runtime_revision="runtime-1", model_config_snapshot={"model": "m1"},
        retry_policy={"max_attempts": 1}, skill_bundle_manifest={"sha256": "a" * 64},
        input_schema=input_schema, response_schema=response_schema or {"type": "string"},
        created_by_user_id="owner",
    )


def _database(tmp_path, input_text: str | None = '{"n":-0.0,"a":1}', response_text='{"type":"string"}'):
    tmp_path.mkdir(parents=True, exist_ok=True)
    path = tmp_path / "schema.db"
    初始化發布介面資料庫(path)
    connection = sqlite3.connect(path)
    connection.execute("INSERT INTO service_accounts VALUES('account-1',1,NULL)")
    connection.execute(
        "INSERT INTO published_endpoints VALUES('endpoint-1','owner','account-1','demo','active','version-1',1,1,60,60)"
    )
    values = (
        "version-1", "endpoint-1", 1, "舊需求", "舊提示", "[]", "[]", "{}", "runtime-1",
        "{}", "{}", "{}", input_text, response_text, 0, "owner", 1,
    )
    connection.execute("INSERT INTO published_endpoint_versions VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", values)
    connection.commit()
    connection.close()
    return path


def _configure(path, snapshot, version="version-2"):
    return SQLite版本配置服務(path, lambda: version, lambda: 2.0).配置(
        "owner", "endpoint-1", snapshot,
    )


@pytest.mark.parametrize("left,right", [
    (' { "b" : [1,2], "a": 1 } ', '{"a":1.0,"b":[1.0,2]}'),
    ('{"n":-0.0}', '{"n":0}'),
    ('{"n":1}', '{"n":1.0}'),
    ('{"a":null}', '{"a":null}'),
])
def test_schema_key順序空白signed_zero與數值表示法語意等價(left, right):
    assert _schema等價(left, right) is True


@pytest.mark.parametrize("left,right", [
    ('{"n":10000000000000000000000000000000000000001}',
     '{"n":10000000000000000000000000000000000000000}'),
    ('{"n":true}', '{"n":1}'),
    ('{"a":[1,2]}', '{"a":[2,1]}'),
    ('null', '{}'),
])
def test_schema_huge_int_bool_array與型別差異不等價(left, right):
    assert _schema等價(left, right) is False


@pytest.mark.parametrize("hostile", [
    '{"a":1,"a":2}', '{"n":NaN}', '{"n":Infinity}', '{"n":-Infinity}',
    "[" * 65 + "0" + "]" * 65,
    "{" + ",".join(f'\"k{i}\":0' for i in range(10001)) + "}",
], ids=("duplicate", "nan", "infinity", "negative-infinity", "deep", "wide"))
def test_strict_loader拒絕duplicate非有限過深與過寬(hostile):
    with pytest.raises(sqlite3.DatabaseError) as error:
        _schema等價(hostile, "{}")
    assert error.value.__cause__ is None and error.value.__context__ is None


@pytest.mark.parametrize("column,hostile", [
    ("input_schema_json", '{"a":1,"a":2}'),
    ("response_schema_json", '{"n":NaN}'),
])
def test_持久化前版schema遭duplicate或非有限竄改會fail_closed且不追加(tmp_path, column, hostile):
    path = _database(tmp_path)
    connection = sqlite3.connect(path)
    connection.execute("DROP TRIGGER published_endpoint_versions_no_update")
    connection.execute(f"UPDATE published_endpoint_versions SET {column}=?", (hostile,))
    connection.commit()
    connection.close()
    # 初始化另一份取得 canonical trigger SQL，再原樣重建。
    template = tmp_path / "template.db"
    初始化發布介面資料庫(template)
    sql = sqlite3.connect(template).execute(
        "SELECT sql FROM sqlite_master WHERE type='trigger' AND name='published_endpoint_versions_no_update'"
    ).fetchone()[0]
    connection = sqlite3.connect(path)
    connection.execute(sql)
    connection.commit()
    connection.close()

    before = sqlite3.connect(path).execute("SELECT count(*),max(version_number) FROM published_endpoint_versions").fetchone()
    with pytest.raises(版本配置錯誤, match="^版本配置失敗$") as error:
        _configure(path, _snapshot(input_schema={"n": 0}))
    assert error.value.__cause__ is None and error.value.__context__ is None
    connection = sqlite3.connect(path)
    assert connection.execute("SELECT count(*),max(version_number) FROM published_endpoint_versions").fetchone() == before
    assert connection.execute("SELECT current_version_id FROM published_endpoints").fetchone() == ("version-1",)


def test_None_input轉換與response差異各自標記schema_changed(tmp_path):
    none_path = _database(tmp_path, input_text=None)
    assert _configure(none_path, _snapshot(input_schema=None)).schema_changed is False

    changed_path = _database(tmp_path / "changed", input_text=None)
    assert _configure(changed_path, _snapshot(input_schema={"type": "object"})).schema_changed is True

    response_path = _database(tmp_path / "response", input_text=None)
    assert _configure(response_path, _snapshot(input_schema=None, response_schema={"type": "number"})).schema_changed is True
