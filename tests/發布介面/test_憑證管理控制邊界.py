import sqlite3
from types import MappingProxyType, MethodType

import pytest

import 繁中代理.發布介面.憑證.服務 as 服務模組
from 繁中代理.發布介面.憑證.加密 import AESGCM憑證封套
from 繁中代理.發布介面.憑證.儲存庫 import SQLite憑證儲存庫, 憑證儲存錯誤
from 繁中代理.發布介面.憑證.管理 import SQLite憑證管理服務
from 繁中代理.發布介面.憑證.服務 import SQLite憑證撤銷服務
from 繁中代理.發布介面.憑證管理契約 import 憑證管理操作錯誤
from 繁中代理.發布介面.資料庫 import 初始化發布介面資料庫
from 繁中代理.發布介面.領域模型 import WebOwnerPrincipal


class 自訂鍵盤中斷(KeyboardInterrupt):
    pass


class 自訂系統離開(SystemExit):
    pass


class 自訂產生器離開(GeneratorExit):
    pass


class 自訂普通Base(BaseException):
    pass


def _準備(path):
    初始化發布介面資料庫(path)
    with sqlite3.connect(path) as connection:
        connection.execute("INSERT INTO service_accounts(id,created_at) VALUES ('sa1',1)")
        connection.execute(
            "INSERT INTO published_endpoints(id,owner_user_id,service_account_id,slug,status,created_at,updated_at) "
            "VALUES ('ep1','OWNER-MARKER','sa1','one','active',1,1)"
        )
    SQLite憑證儲存庫(
        path, AESGCM憑證封套({1: b"k" * 32}, 1), clock=lambda: 100.0,
        id_factory=lambda: "cred-1",
    ).建立("ep1", WebOwnerPrincipal("OWNER-MARKER"), name="name", purpose="purpose", expires_at=1000.0)


def _可達(value, marker, seen):
    if id(value) in seen:
        return False
    seen.add(id(value))
    if type(value) is str:
        return marker in value
    if type(value) is bytes:
        return marker.encode() in value
    if type(value) in (tuple, list, set, frozenset):
        return any(_可達(item, marker, seen) for item in value)
    if type(value) is dict or type(value) is MappingProxyType:
        return any(_可達(item, marker, seen) for pair in value.items() for item in pair)
    if isinstance(value, BaseException):
        return _可達((value.args, value.__cause__, value.__context__), marker, seen)
    if type(value) is MethodType:
        return _可達(value.__self__, marker, seen)
    if type(value) is AESGCM憑證封套:
        return False
    if type(value) is SQLite憑證管理服務:
        return _可達(value.__dict__, marker, seen)
    slots = getattr(type(value), "__slots__", ())
    if type(slots) is str:
        slots = (slots,)
    for name in slots if type(slots) is tuple else ():
        try:
            if _可達(object.__getattribute__(value, name), marker, seen):
                return True
        except (AttributeError, TypeError):
            pass
    return False


def _trace乾淨(error, *markers):
    trace = error.__traceback__
    found = []
    while trace is not None:
        if "/繁中代理/發布介面/憑證/" in trace.tb_frame.f_code.co_filename:
            found.append(trace.tb_frame.f_code.co_name)
            for local in tuple(trace.tb_frame.f_locals.values()):
                for marker in markers:
                    assert not _可達(local, marker, set()), trace.tb_frame.f_code.co_name
        trace = trace.tb_next
    assert found


def test_scanner_positive_oracle涵蓋service_attr與bound_method():
    marker = "M" * 32
    service = SQLite憑證管理服務("unused", AESGCM憑證封套({1: marker.encode()}, 1))
    assert not _可達(service, marker, set())
    assert not _可達(service.列出憑證, marker, set())


class _RollbackProxy:
    def __init__(self, connection, winner):
        self.connection, self.winner = connection, winner

    @property
    def in_transaction(self):
        return self.connection.in_transaction

    def execute(self, sql, parameters=()):
        if sql.strip().upper() == "ROLLBACK":
            raise self.winner
        return self.connection.execute(sql, parameters)

    def close(self):
        self.connection.close()


@pytest.mark.parametrize("error_type", [
    KeyboardInterrupt, SystemExit, GeneratorExit,
    自訂鍵盤中斷, 自訂系統離開, 自訂產生器離開,
])
@pytest.mark.parametrize("boundary", ["audit", "rollback"])
def test_revoke_audit與rollback控制優先且所有production_frame無敏感可達(
    tmp_path, monkeypatch, error_type, boundary,
):
    db = tmp_path / f"revoke-{boundary}-{error_type.__name__}.sqlite3"
    _準備(db)
    master = "M" * 32
    winner = error_type("CONTROL-ARGS")
    service = SQLite憑證管理服務(db, AESGCM憑證封套({1: master.encode()}, 1), 時鐘=lambda: 150.0)
    if boundary == "audit":
        monkeypatch.setattr(
            SQLite憑證撤銷服務, "_insert_audit", staticmethod(lambda *args: (_ for _ in ()).throw(winner)),
        )
    else:
        real_connect = sqlite3.connect
        monkeypatch.setattr(服務模組, "註冊憑證SQLite函式", lambda connection: None)
        monkeypatch.setattr(
            sqlite3, "connect",
            lambda *args, **kwargs: _RollbackProxy(real_connect(*args, **kwargs), winner),
        )
        monkeypatch.setattr(
            SQLite憑證撤銷服務, "_insert_audit",
            staticmethod(lambda *args: (_ for _ in ()).throw(自訂普通Base("AUDIT-ORDINARY"))),
        )
    with pytest.raises(error_type) as caught:
        service.撤銷憑證(
            端點識別碼="ep1", 憑證識別碼="cred-1", 擁有者使用者識別碼="OWNER-MARKER",
            是否管理者=False, 請求識別碼="REQUEST-MARKER",
        )
    assert caught.value is winner and caught.value.args == ("CONTROL-ARGS",)
    _trace乾淨(winner, master, "OWNER-MARKER", "REQUEST-MARKER")


def test_revoke_audit普通Base固定無鏈且回滾資料庫(tmp_path, monkeypatch):
    db = tmp_path / "audit-ordinary.sqlite3"
    _準備(db)
    monkeypatch.setattr(
        SQLite憑證撤銷服務, "_insert_audit",
        staticmethod(lambda *args: (_ for _ in ()).throw(自訂普通Base("AUDIT-PRIVATE"))),
    )
    service = SQLite憑證管理服務(
        db, AESGCM憑證封套({1: b"M" * 32}, 1), 時鐘=lambda: 150.0,
    )
    with pytest.raises(憑證管理操作錯誤) as error:
        service.撤銷憑證(
            端點識別碼="ep1", 憑證識別碼="cred-1", 擁有者使用者識別碼="OWNER-MARKER",
            是否管理者=False, 請求識別碼="REQUEST-MARKER",
        )
    assert error.value.__cause__ is None and error.value.__context__ is None
    _trace乾淨(error.value, "M" * 32, "OWNER-MARKER", "REQUEST-MARKER", "AUDIT-PRIVATE")
    with sqlite3.connect(db) as connection:
        assert connection.execute("SELECT revoked_at FROM endpoint_credentials WHERE id='cred-1'").fetchone() == (None,)
        assert connection.execute("SELECT count(*) FROM audit_events").fetchone() == (0,)


def test_legacy建立exact_ValueError涵蓋生命週期與operational(tmp_path, monkeypatch):
    db = tmp_path / "legacy.sqlite3"
    _準備(db)
    repository = SQLite憑證儲存庫(db, AESGCM憑證封套({1: b"m" * 32}, 1), clock=lambda: 150.0)
    with sqlite3.connect(db) as connection:
        connection.execute("UPDATE published_endpoints SET status='disabled' WHERE id='ep1'")
    with pytest.raises(憑證儲存錯誤) as conflict:
        repository.建立("ep1", WebOwnerPrincipal("OWNER-MARKER"), name="other", purpose="purpose", expires_at=500.0)
    assert type(conflict.value) is 憑證儲存錯誤
    monkeypatch.setattr(sqlite3, "connect", lambda *args, **kwargs: (_ for _ in ()).throw(sqlite3.OperationalError("PRIVATE")))
    with pytest.raises(憑證儲存錯誤) as operational:
        repository.建立("ep1", WebOwnerPrincipal("OWNER-MARKER"), name="other", purpose="purpose", expires_at=500.0)
    assert type(operational.value) is 憑證儲存錯誤
    assert 憑證儲存錯誤.__name__ == "憑證儲存錯誤"
    assert 憑證儲存錯誤.__module__ == "繁中代理.發布介面.憑證.儲存庫"
    assert 憑證儲存錯誤.__bases__ == (ValueError,)
