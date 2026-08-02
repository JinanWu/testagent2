"""PUB P06 啟用 capture、序列、完整性與 hostile rowcount。"""

import json
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

from 繁中代理.發布介面.資料庫 import 初始化發布介面資料庫
from 繁中代理.發布介面.規劃.版本服務 import (
    SQLite版本配置服務, 版本啟用錯誤,
)


def _資料庫(tmp_path, *, current="version-1"):
    path = tmp_path / "activation-safety.db"
    初始化發布介面資料庫(path)
    connection = sqlite3.connect(path)
    connection.execute("INSERT INTO service_accounts VALUES('account-1',1,NULL)")
    connection.execute("INSERT INTO service_accounts VALUES('account-2',1,NULL)")
    connection.execute(
        "INSERT INTO published_endpoints VALUES(?,?,?,?,?,?,?,?,?,?)",
        ("endpoint-1", "owner", "account-1", "demo", "active", current, 1, 1, 60, 60),
    )
    connection.execute(
        "INSERT INTO published_endpoints VALUES(?,?,?,?,?,?,?,?,?,?)",
        ("endpoint-2", "owner", "account-2", "other", "active", "other-1", 1, 1, 60, 60),
    )
    for number in (1, 2, 3):
        manifest = json.dumps({"reference": f"bundles/{number}", "sha256": str(number) * 64}, sort_keys=True, separators=(",", ":"))
        connection.execute(
            "INSERT INTO published_endpoint_versions VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (f"version-{number}", "endpoint-1", number, f"需求{number}", f"提示{number}", "[]", "[]", "{}", "runtime-1", "{}", "{}", manifest, None, "{}", int(number > 1), "owner", float(number)),
        )
    connection.execute(
        "INSERT INTO published_endpoint_versions VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        ("other-1", "endpoint-2", 1, "other", "other", "[]", "[]", "{}", "runtime-1", "{}", "{}", '{"sha256":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"}', None, "{}", 0, "owner", 1.0),
    )
    connection.commit(); connection.close()
    return path


def _服務(path, connection_factory=sqlite3.connect):
    return SQLite版本配置服務(path, lambda: "unused", lambda: 0.0, connection_factory)


def _啟用(service, version, verifier, audit_factory=lambda: "audit-1", clock=lambda: 20.0):
    return service.啟用("owner", "endpoint-1", version, request_id=None, bundle_verifier=verifier, audit_id_factory=audit_factory, clock=clock)


def _狀態(path):
    connection = sqlite3.connect(path)
    value = (connection.execute("SELECT current_version_id FROM published_endpoints WHERE id='endpoint-1'").fetchone()[0], connection.execute("SELECT id,metadata_json FROM audit_events ORDER BY id").fetchall())
    connection.close()
    return value


def test_verifier只能看副本且不能替換已capture的factory_clock與連線方法(tmp_path):
    path = _資料庫(tmp_path)
    service = _服務(path)
    calls = []
    def original_id(): calls.append("original-id"); return "audit-1"
    def original_clock(): calls.append("original-clock"); return 20.0
    def verify(manifest, *_args):
        manifest["sha256"] = "f" * 64
        service._連線工廠 = lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("swapped"))
        calls.append("verify")
        return True
    result = _啟用(service, "version-2", verify, original_id, original_clock)
    assert calls == ["verify", "original-id", "original-clock"]
    assert result.audit_id == "audit-1"
    assert json.loads(_狀態(path)[1][0][1])["bundle_sha256"] == "2" * 64


def test_三個callback皆先capture_bound_target再容許共享類別被修改(tmp_path):
    path = _資料庫(tmp_path); calls = []
    class Clock:
        def __call__(self): calls.append("clock-original"); return 23.0
    class Audit:
        def __call__(self):
            calls.append("audit-original")
            setattr(Clock, "__call__", lambda _self: (_ for _ in ()).throw(AssertionError("clock-swapped")))
            return "audit-bound"
    class Verifier:
        def __call__(self, *_args):
            calls.append("verify-original")
            setattr(Audit, "__call__", lambda _self: (_ for _ in ()).throw(AssertionError("audit-swapped")))
            setattr(Clock, "__call__", lambda _self: (_ for _ in ()).throw(AssertionError("clock-swapped")))
            return True
    result = _啟用(_服務(path), "version-2", Verifier(), Audit(), Clock())
    assert calls == ["verify-original", "audit-original", "clock-original"]
    assert (result.audit_id, result.activated_at) == ("audit-bound", 23.0)


def test_connection階段修改verifier類別仍呼叫已capture原方法(tmp_path):
    path = _資料庫(tmp_path); calls = []
    class Verifier:
        def __call__(self, *_args): calls.append("verify-original"); return True
    verifier = Verifier()
    def connect(*args, **kwargs):
        setattr(Verifier, "__call__", lambda _self, *_args: (_ for _ in ()).throw(AssertionError("verify-swapped")))
        return sqlite3.connect(*args, **kwargs)
    result = _啟用(_服務(path, connect), "version-2", verifier)
    assert calls == ["verify-original"] and result.new_version_id == "version-2"


class _屬性擷取控制(BaseException):
    pass


@pytest.mark.parametrize("failure", [RuntimeError("attribute-ordinary"), KeyboardInterrupt("attribute-K"),
                                      SystemExit("attribute-I"), GeneratorExit("attribute-G"),
                                      _屬性擷取控制("attribute-custom")])
def test_callback_call屬性擷取失敗在open前固定或exact且其他callback零次(tmp_path, failure):
    path = _資料庫(tmp_path); opens = []; calls = []
    class Hostile:
        def __getattribute__(self, name):
            if name == "__call__": raise failure
            return object.__getattribute__(self, name)
        def __call__(self, *_args): calls.append("hostile-call"); return True
    service = _服務(path, lambda *args, **kwargs: opens.append((args, kwargs)))
    expected = 版本啟用錯誤 if isinstance(failure, (Exception, _屬性擷取控制)) else type(failure)
    with pytest.raises(expected, match="^版本啟用失敗$" if expected is 版本啟用錯誤 else None) as caught:
        _啟用(service, "version-2", Hostile(), lambda: calls.append("audit"), lambda: calls.append("clock"))
    if expected is not 版本啟用錯誤:
        assert caught.value is failure and failure.__cause__ is None and failure.__context__ is None
    else:
        assert caught.value.__cause__ is None and caught.value.__context__ is None
    assert opens == calls == []


@pytest.mark.parametrize("proof", [1, 0, None, "true", object()])
def test_verifier只接受exact_bool_true(tmp_path, proof):
    path = _資料庫(tmp_path)
    with pytest.raises(版本啟用錯誤, match="^版本啟用失敗$"):
        _啟用(_服務(path), "version-2", lambda *_args: proof)
    assert _狀態(path) == ("version-1", [])


def test_cross_endpoint_candidate在所有callback前拒絕(tmp_path):
    path = _資料庫(tmp_path)
    calls = []
    with pytest.raises(版本啟用錯誤):
        _啟用(_服務(path), "other-1", lambda *_args: calls.append("verify"), lambda: calls.append("id"), lambda: calls.append("clock"))
    assert calls == [] and _狀態(path) == ("version-1", [])


class _惡意列數(int):
    pass


class _游標:
    rowcount = _惡意列數(1)


class _列數連線(sqlite3.Connection):
    def execute(self, sql, parameters=()):
        cursor = super().execute(sql, parameters)
        if sql.startswith("UPDATE published_endpoints"):
            return _游標()
        return cursor


def test_CAS_rowcount必須是exact_int_1(tmp_path):
    path = _資料庫(tmp_path)
    def connect(*args, **kwargs): return sqlite3.connect(*args, **kwargs, factory=_列數連線)
    with pytest.raises(版本啟用錯誤):
        _啟用(_服務(path, connect), "version-2", lambda *_args: True)
    assert _狀態(path) == ("version-1", [])


def test_v3競爭不能跳版而v2可成功且最後僅v2一筆audit(tmp_path):
    path = _資料庫(tmp_path)
    begun, release = threading.Event(), threading.Event()
    class FirstConnection(sqlite3.Connection):
        def execute(self, sql, parameters=()):
            cursor = super().execute(sql, parameters)
            if sql == "BEGIN IMMEDIATE":
                begun.set(); assert release.wait(5)
            return cursor
    def activate(version, audit, first=False):
        try:
            def connect(*args, **kwargs):
                return sqlite3.connect(*args, **kwargs, factory=FirstConnection)
            return _啟用(_服務(path, connect if first else sqlite3.connect), version, lambda *_args: True, lambda: audit)
        except 版本啟用錯誤:
            return None
    with ThreadPoolExecutor(max_workers=2) as executor:
        v3 = executor.submit(activate, "version-3", "audit-3", True)
        assert begun.wait(5)
        v2 = executor.submit(activate, "version-2", "audit-2")
        release.set()
    assert v3.result() is None and v2.result() is not None
    assert _狀態(path)[0] == "version-2" and [row[0] for row in _狀態(path)[1]] == ["audit-2"]


def test_audit_collision整筆rollback且版本列byte不變(tmp_path):
    path = _資料庫(tmp_path)
    before = sqlite3.connect(path).execute("SELECT * FROM published_endpoint_versions ORDER BY id").fetchall()
    connection = sqlite3.connect(path)
    connection.execute(
        "INSERT INTO audit_events VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        ("audit-1", "audit-1", 1, "x", "success", "user", "owner", "x", "x",
         None, "endpoint-1", None, "{}", 1),
    )
    connection.commit(); connection.close()
    with pytest.raises(版本啟用錯誤):
        _啟用(_服務(path), "version-2", lambda *_args: True)
    assert _狀態(path)[0] == "version-1"
    assert sqlite3.connect(path).execute("SELECT * FROM published_endpoint_versions ORDER BY id").fetchall() == before
