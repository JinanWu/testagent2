"""PUB P06 current pointer、audit 原子啟用與 invocation pin。"""

import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor

import pytest

from 繁中代理.發布介面.資料庫 import 初始化發布介面資料庫
from 繁中代理.發布介面.規劃.版本服務 import (
    SQLite版本配置服務,
    SQLite目前版本解析器,
    版本啟用結果,
    版本啟用存取錯誤,
    版本啟用錯誤,
    目前版本不存在錯誤,
    目前版本解析錯誤,
)


def _資料庫(tmp_path, *, status="active", current="version-1"):
    path = tmp_path / "activation.db"
    初始化發布介面資料庫(path)
    connection = sqlite3.connect(path)
    connection.execute("INSERT INTO service_accounts VALUES('account-1',1,NULL)")
    connection.execute(
        "INSERT INTO published_endpoints VALUES(?,?,?,?,?,?,?,?,?,?)",
        ("endpoint-1", "owner", "account-1", "demo", status, current, 1, 1, 60, 60),
    )
    for number in (1, 2, 3):
        manifest = json.dumps(
            {"reference": f"bundles/{number}", "sha256": str(number) * 64},
            sort_keys=True, separators=(",", ":"),
        )
        connection.execute(
            "INSERT INTO published_endpoint_versions VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (f"version-{number}", "endpoint-1", number, f"需求{number}", f"提示{number}",
             f'["skill.{number}"]', f'["tool.{number}"]',
             json.dumps({f"tool.{number}": {"revision": f"r{number}"}}, separators=(",", ":")),
             f"runtime-{number}", json.dumps({"model": f"m{number}"}, separators=(",", ":")),
             json.dumps({"max_attempts": number}, separators=(",", ":")), manifest,
             None, json.dumps({"const": number}, separators=(",", ":")),
             int(number > 1), "owner", float(number)),
        )
    connection.commit(); connection.close()
    return path


def _啟用(path, version="version-2", *, verifier=None, audit="audit-1", now=20.0, request="request-1", connection_factory=sqlite3.connect):
    verifier = verifier or (lambda manifest, version_id, endpoint_id: True)
    service = SQLite版本配置服務(path, lambda: "unused", lambda: 0.0, connection_factory)
    return service.啟用(
        "owner", "endpoint-1", version, request_id=request,
        bundle_verifier=verifier, audit_id_factory=lambda: audit, clock=lambda: now,
    )


def _狀態(path):
    connection = sqlite3.connect(path)
    result = (
        connection.execute("SELECT current_version_id,updated_at FROM published_endpoints").fetchone(),
        connection.execute("SELECT id,event_id,occurred_at,action,outcome,actor_type,actor_id,resource_type,resource_id,endpoint_id,request_id,metadata_json,created_at FROM audit_events").fetchall(),
    )
    connection.close()
    return result


def test_v2啟用原子更新pointer與exact安全audit且版本列不變(tmp_path):
    path = _資料庫(tmp_path)
    before = sqlite3.connect(path).execute("SELECT * FROM published_endpoint_versions ORDER BY version_number").fetchall()
    seen = []
    def verify(manifest, version_id, endpoint_id):
        seen.append((manifest, version_id, endpoint_id))
        manifest["sha256"] = "f" * 64
        return True

    result = _啟用(path, verifier=verify)

    assert result == 版本啟用結果("endpoint-1", "version-1", "version-2", 2, "audit-1", 20.0)
    assert seen == [({"reference": "bundles/2", "sha256": "f" * 64}, "version-2", "endpoint-1")]
    endpoint, audits = _狀態(path)
    assert endpoint == ("version-2", 20.0)
    assert audits == [("audit-1", "audit-1", 20.0, "endpoint_version_activated", "success", "user", "owner", "published_endpoint_version", "version-2", "endpoint-1", "request-1", '{"bundle_sha256":"' + "2" * 64 + '","new_version_id":"version-2","old_version_id":"version-1","version_number":2}', 20.0)]
    assert sqlite3.connect(path).execute("SELECT * FROM published_endpoint_versions ORDER BY version_number").fetchall() == before
    assert "提示" not in audits[0][11] and "schema" not in audits[0][11]


@pytest.mark.parametrize("owner,status,version", [
    ("foreign", "active", "version-2"), ("owner", "disabled", "version-2"),
    ("owner", "archived", "version-2"),
])
def test_authority拒絕且所有callback零次(tmp_path, owner, status, version):
    path = _資料庫(tmp_path, status=status)
    calls = []
    service = SQLite版本配置服務(path, lambda: "unused", lambda: 0.0)
    with pytest.raises(版本啟用存取錯誤, match="^版本啟用存取遭拒$"):
        service.啟用(owner, "endpoint-1", version, bundle_verifier=lambda *a: calls.append("verify"), audit_id_factory=lambda: calls.append("id"), clock=lambda: calls.append("clock"))
    assert calls == [] and _狀態(path)[1] == []


@pytest.mark.parametrize("current,version", [
    (None, "version-2"), ("version-1", "version-1"),
    ("version-2", "version-1"), ("version-1", "version-3"),
])
def test_null_same_older_skip皆固定拒絕且不稽核(tmp_path, current, version):
    path = _資料庫(tmp_path, current=current)
    with pytest.raises(版本啟用錯誤, match="^版本啟用失敗$"):
        _啟用(path, version)
    assert _狀態(path) == ((current, 1.0), [])


@pytest.mark.parametrize("mode", ["false", "error"])
def test_verifier_false或ordinary_error整筆rollback(tmp_path, mode):
    path = _資料庫(tmp_path)
    def verify(*_args):
        if mode == "error":
            raise RuntimeError("secret")
        return False
    with pytest.raises(版本啟用錯誤) as caught:
        _啟用(path, verifier=verify)
    assert caught.value.__cause__ is None and caught.value.__context__ is None
    assert _狀態(path) == (("version-1", 1.0), [])


class _失敗連線(sqlite3.Connection):
    stage = None
    def execute(self, sql, parameters=()):
        if type(self).stage == "audit" and sql.startswith("INSERT INTO audit_events"):
            raise sqlite3.OperationalError("audit")
        if type(self).stage == "update" and sql.startswith("UPDATE published_endpoints"):
            return type("Cursor", (), {"rowcount": 0})()
        return super().execute(sql, parameters)


@pytest.mark.parametrize("stage", ["audit", "update"])
def test_audit失敗或CAS失敗pointer與audit一起rollback(tmp_path, stage):
    path = _資料庫(tmp_path)
    _失敗連線.stage = stage
    def connect(*args, **kwargs):
        return sqlite3.connect(*args, **kwargs, factory=_失敗連線)
    with pytest.raises(版本啟用錯誤):
        _啟用(path, connection_factory=connect)
    assert _狀態(path) == (("version-1", 1.0), [])


def test_same_candidate兩個writer僅一成功且一筆audit(tmp_path):
    path = _資料庫(tmp_path)
    def activate(index):
        try:
            return _啟用(path, audit=f"audit-{index}")
        except 版本啟用錯誤:
            return None
    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(activate, range(2)))
    assert sum(result is not None for result in results) == 1
    endpoint, audits = _狀態(path)
    assert endpoint[0] == "version-2" and len(audits) == 1


def test_resolver單一JOIN釘選完整v1且後續啟用不改已回傳bytes(tmp_path):
    path = _資料庫(tmp_path)
    resolver = SQLite目前版本解析器(path)
    pinned = resolver.依slug解析("demo")
    before = pinned.取得版本快照()
    _啟用(path)
    after = pinned.取得版本快照()
    next_pinned = resolver.依slug解析("demo")

    assert (pinned.version_id, pinned.version_number) == ("version-1", 1)
    assert before.original_requirement_text == after.original_requirement_text == "需求1"
    before.allowed_skills.append("mutated")
    assert pinned.取得版本快照().allowed_skills == ["skill.1"]
    assert (next_pinned.version_id, next_pinned.version_number) == ("version-2", 2)
    assert next_pinned.取得版本快照().system_prompt == "提示2"


@pytest.mark.parametrize("slug,status,current", [
    ("missing", "active", "version-1"), ("demo", "disabled", "version-1"),
    ("demo", "archived", "version-1"), ("demo", "active", None),
])
def test_resolver_missing非active與null不fallback(tmp_path, slug, status, current):
    path = _資料庫(tmp_path, status=status, current=current)
    with pytest.raises(目前版本不存在錯誤, match="^目前版本不存在$") as caught:
        SQLite目前版本解析器(path).依slug解析(slug)
    assert type(caught.value) is 目前版本不存在錯誤
    assert caught.value.__cause__ is None and caught.value.__context__ is None


class _固定列游標:
    def __init__(self, row): self._row = row
    def fetchone(self): return self._row


class _異常目前列連線(sqlite3.Connection):
    current_row = ("malformed-non-none",)
    rollback_failure = close_failure = None
    rollback_calls = close_calls = 0

    def execute(self, sql, parameters=()):
        if "published_endpoints e JOIN published_endpoint_versions v" in sql:
            return _固定列游標(type(self).current_row)
        if sql == "ROLLBACK":
            type(self).rollback_calls += 1
            if type(self).rollback_failure is not None:
                raise type(self).rollback_failure
        return super().execute(sql, parameters)

    def close(self):
        type(self).close_calls += 1
        if type(self).close_failure is not None:
            raise type(self).close_failure
        return super().close()


@pytest.mark.parametrize("mode", ["row", "schema", "database"])
def test_resolver_malformed列schema與DB失敗仍為exact通用解析錯誤(tmp_path, mode):
    path = _資料庫(tmp_path)
    connection_factory = sqlite3.connect
    if mode == "row":
        _異常目前列連線.current_row = ("malformed-non-none",)
        _異常目前列連線.rollback_failure = _異常目前列連線.close_failure = None
        def connection_factory(*args, **kwargs):
            return sqlite3.connect(*args, **kwargs, factory=_異常目前列連線)
    elif mode == "schema":
        connection = sqlite3.connect(path)
        connection.execute("DROP TRIGGER published_endpoint_versions_no_update")
        connection.commit(); connection.close()
    else:
        def connection_factory(*_args, **_kwargs):
            raise sqlite3.OperationalError("private operational detail")
    with pytest.raises(目前版本解析錯誤, match="^目前版本解析失敗$") as caught:
        SQLite目前版本解析器(path, connection_factory).依slug解析("demo")
    assert type(caught.value) is 目前版本解析錯誤
    assert caught.value.__cause__ is None and caught.value.__context__ is None


@pytest.mark.parametrize("rollback,close,winner,loser_marker", [
    (KeyboardInterrupt("rollback-wins"), SystemExit("close-loses"), "rollback", "close-loses"),
    (RuntimeError("rollback-ordinary"), GeneratorExit("close-wins"), "close", "rollback-ordinary"),
])
def test_resolver_not_found清理控制仍依既有precedence精確勝出且frame乾淨(
    tmp_path, rollback, close, winner, loser_marker,
):
    cls = _異常目前列連線
    cls.current_row, cls.rollback_failure, cls.close_failure = None, rollback, close
    cls.rollback_calls = cls.close_calls = 0
    def connect(*args, **kwargs):
        return sqlite3.connect(*args, **kwargs, factory=cls)
    expected = rollback if winner == "rollback" else close
    with pytest.raises(type(expected)) as caught:
        SQLite目前版本解析器(_資料庫(tmp_path), connect).依slug解析("missing-private")
    assert caught.value is expected
    assert caught.value.__cause__ is None and caught.value.__context__ is None
    assert (cls.rollback_calls, cls.close_calls) == (1, 1)
    traceback = caught.value.__traceback__
    while traceback is not None:
        frame = traceback.tb_frame
        if frame.f_code.co_filename.endswith("版本服務.py"):
            for value in tuple(frame.f_locals.values()):
                assert "missing-private" not in repr(value)
                assert loser_marker not in repr(value)
        traceback = traceback.tb_next
