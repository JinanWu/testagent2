import base64
import sqlite3
from pathlib import Path
from types import MappingProxyType, MethodType

import pytest

import 繁中代理.發布介面.憑證.儲存庫 as 儲存庫模組
from 繁中代理.發布介面.憑證.加密 import AESGCM憑證封套, 新APIKey
from 繁中代理.發布介面.憑證.儲存庫 import SQLite憑證儲存庫, 建立憑證結果, 憑證儲存錯誤
from 繁中代理.發布介面.憑證.管理 import SQLite憑證管理服務
from 繁中代理.發布介面.憑證管理契約 import (
    一次性憑證建立收據, 憑證建立命令, 憑證管理操作錯誤,
)
from 繁中代理.發布介面.資料庫 import 初始化發布介面資料庫
from 繁中代理.發布介面.領域模型 import WebOwnerPrincipal


class 自訂普通Base(BaseException):
    pass


class 自訂鍵盤中斷(KeyboardInterrupt):
    pass


class 自訂系統離開(SystemExit):
    pass


class 自訂產生器離開(GeneratorExit):
    pass


控制類型 = (
    KeyboardInterrupt, SystemExit, GeneratorExit,
    自訂鍵盤中斷, 自訂系統離開, 自訂產生器離開,
)


def _準備(path):
    初始化發布介面資料庫(path)
    with sqlite3.connect(path) as connection:
        connection.execute("INSERT INTO service_accounts(id,created_at) VALUES ('sa1',1)")
        connection.execute(
            "INSERT INTO published_endpoints(id,owner_user_id,service_account_id,slug,status,created_at,updated_at) "
            "VALUES ('ep1','OWNER-TRACE','sa1','one','active',1,1)"
        )


def _可達(value, marker, seen):
    if id(value) in seen:
        return False
    seen.add(id(value))
    if type(value) is str:
        return marker in value
    if type(value) is bytes:
        return marker.encode() in value
    if isinstance(value, Path):
        return marker in str(value)
    if type(value) in (tuple, list, set, frozenset):
        return any(_可達(item, marker, seen) for item in value)
    if type(value) in (dict, MappingProxyType):
        return any(_可達(item, marker, seen) for pair in value.items() for item in pair)
    if isinstance(value, BaseException):
        return _可達((value.args, value.__cause__, value.__context__), marker, seen)
    if type(value) is MethodType:
        return _可達(value.__self__, marker, seen)
    if type(value) is AESGCM憑證封套:
        return _可達(value._keys, marker, seen)
    if type(value) in (SQLite憑證儲存庫, SQLite憑證管理服務):
        return _可達(value.__dict__, marker, seen)
    if type(value) in (憑證建立命令, 一次性憑證建立收據, 建立憑證結果, 新APIKey):
        slots = ()
        for cls in type(value).__mro__:
            current = getattr(cls, "__slots__", ())
            slots += (current,) if type(current) is str else tuple(current)
        return any(
            _可達(object.__getattribute__(value, name), marker, seen)
            for name in slots if name not in ("__weakref__",) and hasattr(value, name)
        )
    if type(value) is _連線代理:
        return _可達(value.__dict__, marker, seen)
    return False


def _trace乾淨(error, *markers):
    frames = []
    trace = error.__traceback__
    while trace is not None:
        filename = trace.tb_frame.f_code.co_filename
        if "/繁中代理/發布介面/憑證/" in filename:
            frames.append(trace.tb_frame.f_code.co_name)
            for local in tuple(trace.tb_frame.f_locals.values()):
                for marker in markers:
                    assert not _可達(local, marker, set()), trace.tb_frame.f_code.co_name
        trace = trace.tb_next
    assert "_建立交易" in frames


def test_scanner_positive_oracle涵蓋exact服務封套請求與明文結果():
    master = "M" * 32
    plaintext = "pk_" + base64.urlsafe_b64encode(b"P" * 32).rstrip(b"=").decode()
    envelope = AESGCM憑證封套({1: master.encode()}, 1, 隨機位元組=lambda size: b"P" * size)
    service = SQLite憑證管理服務("unused", envelope)
    request = 憑證建立命令("NAME-TRACE", "PURPOSE-TRACE", 200.0, (), 60)
    issued = envelope.產生並加密("ep1", "cred-1")
    assert _可達(service, master, set()) and _可達(envelope, master, set())
    assert _可達(request, "NAME-TRACE", set()) and _可達(issued, plaintext, set())


def test_管理建構子與畸形exact請求清除master與partial_self():
    master = "C" * 32
    envelope = AESGCM憑證封套({1: master.encode()}, 1)
    with pytest.raises(憑證管理操作錯誤) as invalid_clock:
        SQLite憑證管理服務("PATH-TRACE", envelope, 時鐘=object())
    forged = object.__new__(憑證建立命令)
    object.__setattr__(forged, "名稱", " ")
    object.__setattr__(forged, "用途", "PURPOSE-TRACE")
    object.__setattr__(forged, "到期時間", 200.0)
    object.__setattr__(forged, "IP允許清單", ())
    object.__setattr__(forged, "速率限制請求數", 60)
    service = SQLite憑證管理服務("PATH-TRACE", envelope)
    with pytest.raises(憑證管理操作錯誤) as malformed:
        service.建立憑證(端點識別碼="ep1", 擁有者使用者識別碼="OWNER-TRACE", 請求=forged)
    for caught in (invalid_clock.value, malformed.value):
        assert caught.__cause__ is None and caught.__context__ is None
        trace = caught.__traceback__
        while trace is not None:
            if "/繁中代理/發布介面/憑證/" in trace.tb_frame.f_code.co_filename:
                for local in tuple(trace.tb_frame.f_locals.values()):
                    assert not _可達(local, master, set())
            trace = trace.tb_next


class _連線代理:
    def __init__(self, connection, boundary, winner):
        self.connection, self.boundary, self.winner = connection, boundary, winner

    @property
    def in_transaction(self):
        return self.connection.in_transaction

    def execute(self, sql, parameters=()):
        upper = sql.strip().upper()
        if self.boundary == "insert" and upper.startswith("INSERT INTO ENDPOINT_CREDENTIALS"):
            raise self.winner
        if self.boundary == "commit" and upper == "COMMIT":
            raise self.winner
        if self.boundary == "rollback" and upper.startswith("INSERT INTO ENDPOINT_CREDENTIALS"):
            raise RuntimeError("PRIMARY-TRACE")
        if self.boundary == "rollback" and upper == "ROLLBACK":
            raise self.winner
        return self.connection.execute(sql, parameters)

    def close(self):
        self.connection.close()
        if self.boundary == "close":
            raise self.winner


@pytest.mark.parametrize("error_type", (自訂普通Base,) + 控制類型)
@pytest.mark.parametrize("boundary", ["clock", "id", "encrypt", "connect", "insert", "commit", "rollback", "close"])
def test_real_repository所有建立failpoint清除master明文identity_path_rows與錯誤鏈(
    tmp_path, monkeypatch, error_type, boundary,
):
    path = tmp_path / "PATH-TRACE.sqlite3"
    _準備(path)
    master = "M" * 32
    plaintext = "pk_" + base64.urlsafe_b64encode(b"P" * 32).rstrip(b"=").decode()
    winner = error_type("WINNER-TRACE")
    clock = (lambda: (_ for _ in ()).throw(winner)) if boundary == "clock" else (lambda: 100.0)
    id_factory = (lambda: (_ for _ in ()).throw(winner)) if boundary == "id" else (lambda: "cred-TRACE")
    random = (lambda size: (_ for _ in ()).throw(winner)) if boundary == "encrypt" else (lambda size: b"P" * size)
    envelope = AESGCM憑證封套({1: master.encode()}, 1, 隨機位元組=random)
    real_connect = sqlite3.connect
    if boundary == "connect":
        monkeypatch.setattr(sqlite3, "connect", lambda *args, **kwargs: (_ for _ in ()).throw(winner))
    elif boundary in ("insert", "commit", "rollback", "close"):
        selected = boundary
        real_register = 儲存庫模組.註冊憑證SQLite函式
        monkeypatch.setattr(
            儲存庫模組, "註冊憑證SQLite函式", lambda connection: real_register(connection.connection),
        )
        monkeypatch.setattr(
            sqlite3, "connect",
            lambda *args, **kwargs: _連線代理(real_connect(*args, **kwargs), selected, winner),
        )
    repository = SQLite憑證儲存庫(path, envelope, clock=clock, id_factory=id_factory)
    if boundary == "close" and error_type is 自訂普通Base:
        result = repository.建立管理憑證(
            "ep1", WebOwnerPrincipal("OWNER-TRACE"), name="NAME-TRACE", purpose="PURPOSE-TRACE",
            expires_at=500.0,
        )
        assert type(result) is 建立憑證結果
        return
    expected = error_type if error_type in 控制類型 else 憑證管理操作錯誤
    with pytest.raises(expected) as caught:
        repository.建立管理憑證(
            "ep1", WebOwnerPrincipal("OWNER-TRACE"), name="NAME-TRACE", purpose="PURPOSE-TRACE",
            expires_at=500.0,
        )
    if error_type in 控制類型:
        assert caught.value is winner and caught.value.args == ("WINNER-TRACE",)
    else:
        assert type(caught.value) is 憑證管理操作錯誤
    assert caught.value.__cause__ is None and caught.value.__context__ is None
    _trace乾淨(
        caught.value, master, plaintext, "OWNER-TRACE", "NAME-TRACE", "PURPOSE-TRACE",
        "PATH-TRACE", "PRIMARY-TRACE", "WINNER-TRACE",
    )


def test_legacy建立管理錯誤轉換在except外且無內鏈(tmp_path):
    path = tmp_path / "legacy.sqlite3"
    _準備(path)
    repository = SQLite憑證儲存庫(
        path, AESGCM憑證封套({1: b"L" * 32}, 1), clock=lambda: 100.0,
    )
    with pytest.raises(憑證儲存錯誤) as caught:
        repository.建立(
            "missing", WebOwnerPrincipal("OWNER-TRACE"), name="NAME-TRACE",
            purpose="PURPOSE-TRACE", expires_at=500.0,
        )
    assert type(caught.value) is 憑證儲存錯誤
    assert caught.value.__cause__ is None and caught.value.__context__ is None
