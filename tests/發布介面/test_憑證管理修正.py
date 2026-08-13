import sqlite3
from types import MethodType

import pytest

import 繁中代理.發布介面.憑證.管理 as 管理模組
import 繁中代理.發布介面.憑證.管理操作 as 管理操作模組
import 繁中代理.發布介面.憑證.服務 as 服務模組
from 繁中代理.發布介面.憑證.加密 import AESGCM憑證封套
from 繁中代理.發布介面.憑證.儲存庫 import SQLite憑證儲存庫, 憑證儲存錯誤
from 繁中代理.發布介面.憑證.管理 import SQLite憑證管理服務
from 繁中代理.發布介面.憑證.服務 import SQLite憑證撤銷服務, 憑證撤銷找不到錯誤
from 繁中代理.發布介面.憑證管理契約 import (
    找不到端點憑證錯誤, 憑證建立命令, 憑證管理操作錯誤, 端點生命週期衝突錯誤,
)
from 繁中代理.發布介面.資料庫 import 初始化發布介面資料庫
from 繁中代理.發布介面.領域模型 import WebOwnerPrincipal


class 自訂Base(BaseException):
    pass


class 自訂鍵盤中斷(KeyboardInterrupt):
    pass


class 自訂系統離開(SystemExit):
    pass


class 自訂產生器離開(GeneratorExit):
    pass


def _資料庫(path):
    初始化發布介面資料庫(path)
    with sqlite3.connect(path) as connection:
        connection.execute("INSERT INTO service_accounts(id,created_at) VALUES ('sa1',1)")
        connection.execute(
            "INSERT INTO published_endpoints(id,owner_user_id,service_account_id,slug,status,created_at,updated_at) "
            "VALUES ('ep1','OWNER-TRACE','sa1','one','active',1,1)"
        )


def _新增(path):
    return SQLite憑證儲存庫(
        path, AESGCM憑證封套({1: b"k" * 32}, 1), clock=lambda: 100.0,
        id_factory=lambda: "cred-1",
    ).建立(
        "ep1", WebOwnerPrincipal("OWNER-TRACE"), name="name", purpose="purpose",
        expires_at=1000.0,
    )


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
    if type(value) is dict:
        return any(_可達(item, marker, seen) for pair in value.items() for item in pair)
    if isinstance(value, BaseException):
        return _可達((value.args, value.__cause__, value.__context__), marker, seen)
    if type(value) is MethodType:
        return _可達(value.__self__, marker, seen)
    if type(value) is AESGCM憑證封套:
        return False
    if type(value) is SQLite憑證管理服務:
        return _可達(value.__dict__, marker, seen)
    return False


def _trace乾淨(error, *markers):
    trace = error.__traceback__
    while trace is not None:
        if "/繁中代理/發布介面/憑證/" in trace.tb_frame.f_code.co_filename:
            for local in tuple(trace.tb_frame.f_locals.values()):
                for marker in markers:
                    assert not _可達(local, marker, set()), trace.tb_frame.f_code.co_name
        trace = trace.tb_next


@pytest.mark.parametrize("無效欄位", ["envelope", "clock"])
def test_legacy建構子無效封套或時鐘維持exact儲存錯誤且零callback隱私(無效欄位):
    callbacks = []
    marker = f"CONSTRUCTOR-PRIVATE-{無效欄位}"

    class Hostile:
        __slots__ = ("marker",)

        def __init__(self):
            object.__setattr__(self, "marker", marker)

        def __getattribute__(self, name):
            callbacks.append(name)
            raise AssertionError("不得讀取不可信建構子參數")

        def __repr__(self):
            callbacks.append("repr")
            raise AssertionError("不得顯示不可信建構子參數")

    hostile = Hostile()
    envelope = hostile if 無效欄位 == "envelope" else AESGCM憑證封套({1: b"m" * 32}, 1)
    clock = hostile if 無效欄位 == "clock" else (lambda: 100.0)
    with pytest.raises(憑證儲存錯誤) as caught:
        SQLite憑證儲存庫("unused.sqlite3", envelope, clock=clock)
    assert type(caught.value) is 憑證儲存錯誤
    assert isinstance(caught.value, ValueError) and not isinstance(caught.value, RuntimeError)
    assert str(caught.value) == "憑證建立失敗"
    assert caught.value.__cause__ is None and caught.value.__context__ is None
    assert callbacks == []
    trace = caught.value.__traceback__
    while trace is not None:
        if trace.tb_frame.f_code.co_name == "__init__" and "/憑證/儲存庫.py" in trace.tb_frame.f_code.co_filename:
            assert all(value is not hostile for value in tuple(trace.tb_frame.f_locals.values()))
        trace = trace.tb_next
    assert marker not in str(caught.value)


def test_legacy錯誤恢復exact_ValueError且管理建立保留typed結果(tmp_path):
    assert 憑證儲存錯誤.__name__ == "憑證儲存錯誤"
    assert 憑證儲存錯誤.__module__ == "繁中代理.發布介面.憑證.儲存庫"
    assert issubclass(憑證儲存錯誤, ValueError) and not issubclass(憑證儲存錯誤, RuntimeError)
    db = tmp_path / "legacy.sqlite3"
    _資料庫(db)
    repository = SQLite憑證儲存庫(db, AESGCM憑證封套({1: b"m" * 32}, 1), clock=lambda: 100.0)
    with pytest.raises(憑證儲存錯誤) as invalid:
        repository.建立("ep1", WebOwnerPrincipal("OWNER-TRACE"), name=" ", purpose="purpose", expires_at=200.0)
    assert type(invalid.value) is 憑證儲存錯誤
    with pytest.raises(憑證儲存錯誤) as missing:
        repository.建立("missing", WebOwnerPrincipal("OWNER-TRACE"), name="name", purpose="purpose", expires_at=200.0)
    assert type(missing.value) is 憑證儲存錯誤
    with pytest.raises(找不到端點憑證錯誤):
        repository.建立管理憑證(
            "missing", WebOwnerPrincipal("OWNER-TRACE"), name="name", purpose="purpose",
            expires_at=200.0,
        )
    with sqlite3.connect(db) as connection:
        connection.execute("UPDATE published_endpoints SET status='disabled' WHERE id='ep1'")
    with pytest.raises(端點生命週期衝突錯誤):
        repository.建立管理憑證(
            "ep1", WebOwnerPrincipal("OWNER-TRACE"), name="name", purpose="purpose",
            expires_at=200.0,
        )


def test_malformed_owner與missing_foreign同為exact_not_found且admin為operational(tmp_path):
    """未授權 caller 不得由畸形 owner slot 判斷 existing credential。"""
    db = tmp_path / "malformed-owner-oracle.sqlite3"
    _資料庫(db)
    _新增(db)
    with sqlite3.connect(db) as connection:
        connection.execute(
            "UPDATE published_endpoints SET owner_user_id=? WHERE id='ep1'",
            (sqlite3.Binary(b"MALFORMED-OWNER"),),
        )
    service = SQLite憑證管理服務(db, 時鐘=lambda: 150.0)
    for credential_id in ("missing", "cred-1"):
        with pytest.raises(找不到端點憑證錯誤) as denied:
            service.撤銷憑證(
                端點識別碼="ep1", 憑證識別碼=credential_id,
                擁有者使用者識別碼="FOREIGN", 是否管理者=False,
                請求識別碼=f"request-{credential_id}",
            )
        assert type(denied.value) is 找不到端點憑證錯誤
    with pytest.raises(憑證管理操作錯誤) as admin:
        service.撤銷憑證(
            端點識別碼="ep1", 憑證識別碼="cred-1",
            擁有者使用者識別碼="ADMIN", 是否管理者=True,
            請求識別碼="request-admin",
        )
    assert type(admin.value) is 憑證管理操作錯誤


def test_foreign畸形生命週期仍exact_not_found但admin為operational(tmp_path):
    db = tmp_path / "foreign-corrupt.sqlite3"
    _資料庫(db)
    _新增(db)
    with sqlite3.connect(db) as connection:
        connection.execute("PRAGMA ignore_check_constraints=ON")
        for (name,) in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='trigger' AND tbl_name='endpoint_credentials'"
        ).fetchall():
            connection.execute(f'DROP TRIGGER "{name}"')
        connection.execute("UPDATE endpoint_credentials SET created_at='BROKEN' WHERE id='cred-1'")
    service = SQLite憑證管理服務(db, 時鐘=lambda: 150.0)
    with pytest.raises(找不到端點憑證錯誤) as denied:
        service.撤銷憑證(
            端點識別碼="ep1", 憑證識別碼="cred-1", 擁有者使用者識別碼="FOREIGN",
            是否管理者=False, 請求識別碼="request-foreign",
        )
    assert type(denied.value) is 找不到端點憑證錯誤
    with pytest.raises(憑證管理操作錯誤) as admin:
        service.撤銷憑證(
            端點識別碼="ep1", 憑證識別碼="cred-1", 擁有者使用者識別碼="ADMIN",
            是否管理者=True, 請求識別碼="request-admin",
        )
    assert type(admin.value) is 憑證管理操作錯誤


class _連線代理:
    def __init__(self, connection, rollback_error):
        self.connection, self.rollback_error = connection, rollback_error

    @property
    def in_transaction(self):
        return self.connection.in_transaction

    def execute(self, sql, parameters=()):
        if sql.strip().upper() == "ROLLBACK":
            raise self.rollback_error
        return self.connection.execute(sql, parameters)

    def close(self):
        self.connection.close()


def test_foreign_saved_not_found被rollback普通失敗覆寫為operational(tmp_path, monkeypatch):
    db = tmp_path / "rollback-taxonomy.sqlite3"
    _資料庫(db)
    _新增(db)
    real_connect = sqlite3.connect
    monkeypatch.setattr(
        sqlite3, "connect",
        lambda *args, **kwargs: _連線代理(real_connect(*args, **kwargs), RuntimeError("ROLLBACK-PRIVATE")),
    )
    with pytest.raises(憑證撤銷錯誤 := 服務模組.憑證撤銷錯誤) as error:
        SQLite憑證撤銷服務(db, clock=lambda: 150.0).撤銷(
            "ep1", "cred-1", WebOwnerPrincipal("FOREIGN"), "REQUEST-PRIVATE",
        )
    assert type(error.value) is 憑證撤銷錯誤 and error.value.__context__ is None


@pytest.mark.parametrize("error_type", [
    KeyboardInterrupt, SystemExit, GeneratorExit,
    自訂鍵盤中斷, 自訂系統離開, 自訂產生器離開,
])
@pytest.mark.parametrize("boundary", ["clock", "database", "reconstruct", "revoke"])
def test_list_revoke所有邊界exact控制且master_owner_request不留trace(tmp_path, monkeypatch, error_type, boundary):
    db = tmp_path / f"trace-{boundary}-{error_type.__name__}.sqlite3"
    _資料庫(db)
    _新增(db)
    master_marker = "M" * 32
    winner = error_type("CONTROL-ARGS")
    vault = AESGCM憑證封套({1: master_marker.encode()}, 1)
    if boundary == "clock":
        service = SQLite憑證管理服務(db, vault, 時鐘=lambda: (_ for _ in ()).throw(winner))
    else:
        service = SQLite憑證管理服務(db, vault, 時鐘=lambda: 150.0)
    if boundary == "database":
        monkeypatch.setattr(sqlite3, "connect", lambda *args, **kwargs: (_ for _ in ()).throw(winner))
    elif boundary == "reconstruct":
        monkeypatch.setattr(管理操作模組, "_重建摘要", lambda *args: (_ for _ in ()).throw(winner))
    elif boundary == "revoke":
        class FakeRevoke:
            def __init__(self, *args, **kwargs): pass
            def 撤銷(self, *args, **kwargs): raise winner
        monkeypatch.setattr(管理操作模組, "SQLite憑證撤銷服務", FakeRevoke)
    with pytest.raises(error_type) as caught:
        if boundary == "revoke":
            service.撤銷憑證(
                端點識別碼="ep1", 憑證識別碼="cred-1", 擁有者使用者識別碼="OWNER-TRACE",
                是否管理者=False, 請求識別碼="REQUEST-TRACE",
            )
        else:
            service.列出憑證(端點識別碼="ep1", 擁有者使用者識別碼="OWNER-TRACE")
    assert caught.value is winner and caught.value.args == ("CONTROL-ARGS",)
    _trace乾淨(winner, master_marker, "OWNER-TRACE", "REQUEST-TRACE")


def test_list普通Base固定無鏈且master不留trace(tmp_path):
    db = tmp_path / "ordinary.sqlite3"
    _資料庫(db)
    marker = "Z" * 32
    service = SQLite憑證管理服務(
        db, AESGCM憑證封套({1: marker.encode()}, 1), 時鐘=lambda: (_ for _ in ()).throw(自訂Base("PRIVATE")),
    )
    with pytest.raises(憑證管理操作錯誤) as error:
        service.列出憑證(端點識別碼="ep1", 擁有者使用者識別碼="OWNER-TRACE")
    assert error.value.__cause__ is None and error.value.__context__ is None
    _trace乾淨(error.value, marker, "OWNER-TRACE")
