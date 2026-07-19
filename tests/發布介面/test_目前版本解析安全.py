"""PUB P06 resolver single-JOIN、snapshot、path 與控制流安全。"""

import json
import os
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

from 繁中代理.發布介面.資料庫 import 初始化發布介面資料庫
from 繁中代理.發布介面.規劃 import 版本服務 as 版本模組
from 繁中代理.發布介面.規劃.版本服務 import SQLite目前版本解析器, 已釘選版本, 目前版本解析錯誤


def _資料庫(tmp_path):
    path = tmp_path / "resolver.db"
    初始化發布介面資料庫(path)
    connection = sqlite3.connect(path)
    connection.execute("INSERT INTO service_accounts VALUES('account-1',1,NULL)")
    connection.execute("INSERT INTO published_endpoints VALUES('endpoint-1','owner','account-1','demo','active','version-1',1,1,60,60)")
    connection.execute(
        "INSERT INTO published_endpoint_versions VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        ("version-1", "endpoint-1", 1, "需求", "提示", '["skill.one"]', '["tool.one"]',
         '{"tool.one":{"revision":"r1"}}', "runtime-1", '{"model":"m1"}', '{"max_attempts":1}',
         '{"reference":"bundles/1","sha256":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"}',
         '{"type":"object"}', '{"type":"string"}', 0, "owner", 1.0),
    )
    connection.commit(); connection.close()
    return path


class _追蹤游標:
    def __init__(self, cursor, owner): self._cursor, self._owner = cursor, owner
    def fetchone(self): self._owner.fetches += 1; return self._cursor.fetchone()
    def __iter__(self): return iter(self._cursor)
    def fetchall(self): return self._cursor.fetchall()


class _追蹤連線:
    def __init__(self, connection): self._connection, self.sql, self.fetches, self.close_calls = connection, [], 0, 0
    def execute(self, sql, parameters=()):
        self.sql.append((sql, parameters))
        return _追蹤游標(self._connection.execute(sql, parameters), self)
    def close(self): self.close_calls += 1; return self._connection.close()


def _加入版本二(path):
    connection = sqlite3.connect(path)
    row = connection.execute(
        "SELECT * FROM published_endpoint_versions WHERE id='version-1'"
    ).fetchone()
    values = list(row); values[0] = "version-2"; values[2] = 2
    values[4] = "提示二"; values[-1] = 2.0
    connection.execute(
        "INSERT INTO published_endpoint_versions VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", values,
    )
    connection.commit(); connection.close()


def test_exactly_one_authoritative_JOIN一列fetch且無latest_MAX_fallback(tmp_path):
    path = _資料庫(tmp_path)
    boxes = []
    def connect(*args, **kwargs):
        proxy = _追蹤連線(sqlite3.connect(*args, **kwargs)); boxes.append(proxy); return proxy
    pinned = SQLite目前版本解析器(path, connect).依slug解析("demo")
    joins = [sql for sql, _ in boxes[0].sql if "published_endpoints e JOIN published_endpoint_versions v" in sql]
    assert len(joins) == 1 and boxes[0].fetches == 1 and boxes[0].close_calls == 1
    normalized = " ".join(joins[0].split())
    assert "e.slug=? AND e.status='active'" in normalized
    assert "v.id=e.current_version_id" in normalized and "v.endpoint_id=e.id" in normalized
    assert "MAX(" not in " ".join(sql for sql, _ in boxes[0].sql).upper() and "LATEST" not in " ".join(sql for sql, _ in boxes[0].sql).upper()
    assert pinned.version_id == "version-1"


def test_schema首讀與current_JOIN固定同一WAL_snapshot且SQL順序正確(tmp_path):
    path = _資料庫(tmp_path); _加入版本二(path)
    setup = sqlite3.connect(path)
    assert setup.execute("PRAGMA journal_mode=WAL").fetchone() == ("wal",)
    setup.close()
    reached_join, release_join, boxes = threading.Event(), threading.Event(), []
    class Connection(_追蹤連線):
        def execute(self, sql, parameters=()):
            if "published_endpoints e JOIN" in sql:
                reached_join.set(); assert release_join.wait(5)
            return super().execute(sql, parameters)
    def connect(*args, **kwargs):
        proxy = Connection(sqlite3.connect(*args, **kwargs)); boxes.append(proxy); return proxy
    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(SQLite目前版本解析器(path, connect).依slug解析, "demo")
        assert reached_join.wait(5)
        writer = sqlite3.connect(path)
        writer.execute("UPDATE published_endpoints SET current_version_id='version-2'")
        writer.commit(); writer.close(); release_join.set()
        pinned = future.result(timeout=5)
    assert pinned.version_id == "version-1" and pinned.取得版本快照().system_prompt == "提示"
    current = SQLite目前版本解析器(path).依slug解析("demo")
    assert current.version_id == "version-2" and current.取得版本快照().system_prompt == "提示二"
    sql = [statement for statement, _ in boxes[0].sql]
    begin, join, commit = sql.index("BEGIN"), next(i for i, item in enumerate(sql) if "published_endpoints e JOIN" in item), sql.index("COMMIT")
    schema = next(i for i, item in enumerate(sql) if "published_api_schema_migrations" in item)
    assert begin < schema < join < commit


class _故障連線(_追蹤連線):
    stage = failure = rollback_failure = close_failure = None
    def execute(self, sql, parameters=()):
        kind = ("begin" if sql == "BEGIN" else "rollback" if sql == "ROLLBACK" else
                "commit" if sql == "COMMIT" else "join" if "published_endpoints e JOIN" in sql else None)
        failure = type(self).rollback_failure if kind == "rollback" else type(self).failure
        self.sql.append((sql, parameters))
        if failure is not None and (kind == type(self).stage or kind == "rollback"):
            raise failure
        return _追蹤游標(self._connection.execute(sql, parameters), self)
    def close(self):
        self.close_calls += 1
        if type(self).close_failure is not None: raise type(self).close_failure
        return self._connection.close()


def _故障解析器(path, stage=None, failure=None, rollback=None, close=None):
    _故障連線.stage, _故障連線.failure = stage, failure
    _故障連線.rollback_failure, _故障連線.close_failure = rollback, close
    boxes = []
    def connect(*args, **kwargs):
        proxy = _故障連線(sqlite3.connect(*args, **kwargs)); boxes.append(proxy); return proxy
    return SQLite目前版本解析器(path, connect), boxes


@pytest.mark.parametrize("stage,rollbacks", [("begin", 0), ("join", 1), ("commit", 1)])
def test_交易普通失敗rollback與close精確一次(tmp_path, stage, rollbacks):
    resolver, boxes = _故障解析器(_資料庫(tmp_path), stage, sqlite3.OperationalError(stage))
    with pytest.raises(目前版本解析錯誤, match="^目前版本解析失敗$") as caught:
        resolver.依slug解析("demo")
    sql = [item for item, _ in boxes[0].sql]
    assert sql.count("ROLLBACK") == rollbacks and boxes[0].close_calls == 1
    assert caught.value.__cause__ is None and caught.value.__context__ is None


def test_COMMIT後close普通成功而close控制精確傳播(tmp_path):
    path = _資料庫(tmp_path)
    resolver, boxes = _故障解析器(path, close=RuntimeError("ordinary-close"))
    assert resolver.依slug解析("demo").version_id == "version-1"
    assert boxes[0].close_calls == 1
    control = SystemExit("close-control")
    resolver, boxes = _故障解析器(path, close=control)
    with pytest.raises(SystemExit) as caught: resolver.依slug解析("demo")
    assert caught.value is control and control.__cause__ is None and control.__context__ is None
    assert boxes[0].close_calls == 1


class _子鍵盤(KeyboardInterrupt):
    pass


@pytest.mark.parametrize("stage,control,rollbacks", [
    ("begin", SystemExit("begin-primary"), 0),
    ("commit", _子鍵盤("commit-primary"), 1),
])
def test_BEGIN與COMMIT_primary控制勝cleanup且exact傳播(tmp_path, stage, control, rollbacks):
    resolver, boxes = _故障解析器(
        _資料庫(tmp_path), stage, control,
        GeneratorExit("rollback-loses"), KeyboardInterrupt("close-loses"),
    )
    with pytest.raises(type(control)) as caught: resolver.依slug解析("demo")
    assert caught.value is control and control.__cause__ is None and control.__context__ is None
    sql = [item for item, _ in boxes[0].sql]
    assert sql.count("ROLLBACK") == rollbacks and boxes[0].close_calls == 1


@pytest.mark.parametrize("rollback,close,winner", [
    (KeyboardInterrupt("rollback-wins"), SystemExit("close-loses"), "rollback"),
    (RuntimeError("rollback-ordinary"), GeneratorExit("close-wins"), "close"),
])
def test_ordinary_primary清理控制precedence且皆只一次(tmp_path, rollback, close, winner):
    resolver, boxes = _故障解析器(
        _資料庫(tmp_path), "join", RuntimeError("primary"), rollback, close,
    )
    expected = rollback if winner == "rollback" else close
    with pytest.raises(type(expected)) as caught: resolver.依slug解析("demo")
    assert caught.value is expected and expected.__cause__ is None and expected.__context__ is None
    sql = [item for item, _ in boxes[0].sql]
    assert sql.count("ROLLBACK") == 1 and boxes[0].close_calls == 1


def test_full_snapshot_exact且重複取得全新detached物件(tmp_path):
    pinned = SQLite目前版本解析器(_資料庫(tmp_path)).依slug解析("demo")
    first, second = pinned.取得版本快照(), pinned.取得版本快照()
    assert first is not second and first.allowed_skills is not second.allowed_skills
    assert first.original_requirement_text == "需求" and first.system_prompt == "提示"
    assert first.allowed_skills == ["skill.one"] and first.allowed_tools == ["tool.one"]
    assert first.tool_schema_snapshot == {"tool.one": {"revision": "r1"}}
    assert first.model_config_snapshot == {"model": "m1"} and first.retry_policy == {"max_attempts": 1}
    assert first.skill_bundle_manifest["sha256"] == "a" * 64
    assert first.input_schema == {"type": "object"} and first.response_schema == {"type": "string"}
    assert first.created_by_user_id == "owner"


@pytest.mark.parametrize("slot,value", [
    ("endpoint_id", ""), ("version_number", True), ("schema_changed", 1),
    ("_版本JSON", '{"system_prompt":"forged"}'),
])
def test_forged_pinned任何slot皆fail_closed且不回傳內部identity(tmp_path, slot, value):
    pinned = SQLite目前版本解析器(_資料庫(tmp_path)).依slug解析("demo")
    object.__setattr__(pinned, slot, value)
    with pytest.raises(目前版本解析錯誤, match="^目前版本解析失敗$") as caught:
        pinned.取得版本快照()
    assert caught.value.__cause__ is None and caught.value.__context__ is None


def _版本frames不含(error, marker):
    def contains(value, seen):
        if value is None or id(value) in seen: return False
        seen.add(id(value))
        if type(value) is str: return marker in value
        if type(value) in (tuple, list): return any(contains(item, seen) for item in value)
        if type(value) is dict: return any(contains(item, seen) for pair in value.items() for item in pair)
        if isinstance(value, BaseException): return contains(value.args, seen) or contains(value.__context__, seen)
        slots = getattr(type(value), "__slots__", ())
        if type(slots) is str: slots = (slots,)
        for name in slots:
            try:
                if contains(object.__getattribute__(value, name), seen): return True
            except (AttributeError, TypeError):
                pass
        return False
    names = set(); traceback = error.__traceback__
    while traceback is not None:
        frame = traceback.tb_frame
        if frame.f_code.co_filename.endswith("版本服務.py"):
            names.add(frame.f_code.co_name)
            for value in tuple(frame.f_locals.values()):
                assert not contains(value, set()), frame.f_code.co_name
        traceback = traceback.tb_next
    assert "__post_init__" in names
    return names


class _解析子鍵盤(KeyboardInterrupt):
    pass


@pytest.mark.parametrize("control", [KeyboardInterrupt("DTO-K-PRIVATE"), SystemExit("DTO-I-PRIVATE"),
                                      GeneratorExit("DTO-G-PRIVATE"), _解析子鍵盤("DTO-SUB-PRIVATE")])
def test_直接建構已釘選版本_nested_parser控制exact且所有版本frame乾淨(monkeypatch, control):
    monkeypatch.setattr(版本模組, "_解析正規值", lambda _text: (_ for _ in ()).throw(control))
    with pytest.raises(type(control)) as caught:
        已釘選版本("endpoint-1", "account-1", "version-1", 1, False, 1.0, '{"private":"DTO-PRIVATE"}')
    assert caught.value is control and caught.value.args == control.args
    assert control.__cause__ is None and control.__context__ is None
    assert "_解析正規物件" in _版本frames不含(control, "PRIVATE")


class _解析普通基底(BaseException):
    pass


@pytest.mark.parametrize("failure", [RuntimeError("DTO-ORD-PRIVATE"), _解析普通基底("DTO-BASE-PRIVATE")])
def test_直接建構已釘選版本_nested_parser普通失敗固定無鏈且不洩漏(monkeypatch, failure):
    monkeypatch.setattr(版本模組, "_解析正規值", lambda _text: (_ for _ in ()).throw(failure))
    with pytest.raises(目前版本解析錯誤, match="^目前版本解析失敗$") as caught:
        已釘選版本("endpoint-1", "account-1", "version-1", 1, False, 1.0, '{"private":"DTO-PRIVATE"}')
    assert caught.value.__cause__ is None and caught.value.__context__ is None
    assert "PRIVATE" not in repr(caught.value)
    _版本frames不含(caught.value, "PRIVATE")


def test_symlink在open前拒絕且connection零次(tmp_path):
    path = _資料庫(tmp_path)
    link = tmp_path / "link.db"; link.symlink_to(path)
    opens = []
    with pytest.raises(目前版本解析錯誤):
        SQLite目前版本解析器(link, lambda *_a, **_k: opens.append(1)).依slug解析("demo")
    assert opens == []


def test_wrong_valid_SQLite_schema拒絕且只close一次(tmp_path):
    path = tmp_path / "wrong.db"
    connection = sqlite3.connect(path); connection.execute("CREATE TABLE harmless(x)"); connection.close()
    boxes = []
    def connect(*args, **kwargs):
        proxy = _追蹤連線(sqlite3.connect(*args, **kwargs)); boxes.append(proxy); return proxy
    with pytest.raises(目前版本解析錯誤):
        SQLite目前版本解析器(path, connect).依slug解析("demo")
    assert boxes[0].close_calls == 1


@pytest.mark.parametrize("control", [KeyboardInterrupt("JOIN-K"), SystemExit("JOIN-S"), GeneratorExit("JOIN-G")])
def test_JOIN_execute控制exact傳播且close一次(tmp_path, control):
    path = _資料庫(tmp_path)
    boxes = []
    class Connection(_追蹤連線):
        def execute(self, sql, parameters=()):
            if "published_endpoints e JOIN" in sql: raise control
            return super().execute(sql, parameters)
    def connect(*args, **kwargs):
        proxy = Connection(sqlite3.connect(*args, **kwargs)); boxes.append(proxy); return proxy
    with pytest.raises(type(control)) as caught:
        SQLite目前版本解析器(path, connect).依slug解析("demo")
    assert caught.value is control and control.__cause__ is None and control.__context__ is None
    assert boxes[0].close_calls == 1


def test_open後路徑替換拒絕且helper擁有close不會雙關閉(tmp_path):
    path = _資料庫(tmp_path)
    replacement = tmp_path / "replacement.db"
    (tmp_path / "other").mkdir(); _資料庫(tmp_path / "other")
    os.rename(tmp_path / "other" / "resolver.db", replacement)
    boxes = []
    def connect(*args, **kwargs):
        connection = sqlite3.connect(*args, **kwargs)
        proxy = _追蹤連線(connection); boxes.append(proxy)
        os.replace(replacement, path)
        return proxy
    with pytest.raises(目前版本解析錯誤):
        SQLite目前版本解析器(path, connect).依slug解析("demo")
    assert boxes[0].close_calls == 1
