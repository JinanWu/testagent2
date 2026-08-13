import math

import pytest

import 繁中代理.發布介面.憑證.管理 as 管理模組
from 繁中代理.發布介面.憑證.加密 import AESGCM憑證封套
from 繁中代理.發布介面.憑證.管理 import SQLite憑證管理服務
from 繁中代理.發布介面.憑證管理契約 import (
    一次性憑證建立收據, 憑證列表結果, 憑證建立命令, 憑證摘要,
    憑證撤銷收據, 憑證管理操作錯誤, 憑證管理狀態,
)


契約路徑 = "/發布介面/憑證管理契約.py"
管理路徑 = "/發布介面/憑證/管理.py"


def _摘要(**changes):
    values = dict(
        憑證識別碼="cred-1", 名稱="name", 用途="purpose", 金鑰前綴="pk_public",
        金鑰末四碼="last", 狀態=憑證管理狀態.有效, 到期時間=200.0,
        最後使用時間=None, 建立時間=100.0, 撤銷時間=None, IP允許清單=(),
        速率限制請求數=60,
    )
    return 憑證摘要(**(values | changes))


def _含標記(value, marker, seen=None):
    seen = set() if seen is None else seen
    if id(value) in seen:
        return False
    seen.add(id(value))
    if type(value) is str:
        return marker in value
    if type(value) is bytes:
        return marker.encode() in value
    if type(value) in (tuple, list):
        return any(_含標記(item, marker, seen) for item in value)
    if type(value) is dict:
        return any(_含標記(項目, marker, seen) for 配對 in value.items() for 項目 in 配對)
    if isinstance(value, BaseException):
        return _含標記((value.args, value.__cause__, value.__context__), marker, seen)
    if type(value) in (
        憑證摘要, 一次性憑證建立收據, 憑證列表結果, 憑證撤銷收據,
        憑證建立命令,
    ):
        names = tuple(type(value).__slots__)
        if type(value) is 一次性憑證建立收據:
            names += tuple(憑證摘要.__slots__)
        return any(_含標記(object.__getattribute__(value, 名稱), marker, seen) for 名稱 in names)
    if type(value) is AESGCM憑證封套:
        return False
    if type(value) is SQLite憑證管理服務:
        return _含標記(value.__dict__, marker, seen)
    if type(value).__name__ == "mappingproxy":
        return _含標記(dict(value), marker, seen)
    if type(value) is _FakeCreateResult:
        return any(_含標記(getattr(value, 名稱), marker, seen) for 名稱 in value.__slots__)
    return False


def _assert_production_trace_clean(error, marker, paths=(契約路徑, 管理路徑)):
    trace = error.__traceback__
    while trace is not None:
        if any(path in trace.tb_frame.f_code.co_filename for path in paths):
            for value in tuple(trace.tb_frame.f_locals.values()):
                assert not _含標記(value, marker, set()), trace.tb_frame.f_code.co_name
        trace = trace.tb_next


def test_traceback_scanner_known_positive_direct與DTO控制():
    marker = "KNOWN-LEAK-CONTROL"

    def direct(value):
        raise RuntimeError("fixed")

    with pytest.raises(RuntimeError) as direct_error:
        direct(marker)
    trace = direct_error.value.__traceback__
    while trace.tb_next is not None:
        trace = trace.tb_next
    assert _含標記(trace.tb_frame.f_locals["value"], marker)
    forged = _摘要()
    object.__setattr__(forged, "用途", marker)
    assert _含標記(forged, marker)
    master = b"M" * 32
    service = SQLite憑證管理服務("unused", AESGCM憑證封套({1: master}, 1))
    assert not _含標記(service, "M" * 16)


@pytest.mark.parametrize("factory", [
    lambda marker: _摘要(用途=marker + "\n"),
    lambda marker: 憑證建立命令(marker + "\n", "purpose", 200.0, (), 60),
    lambda marker: 憑證撤銷收據(marker + "\n", 150.0, False),
])
def test_惡意DTO驗證錯誤與production_trace不留輸入(factory):
    marker = "HOSTILE-DTO-MARKER"
    with pytest.raises(ValueError) as error:
        factory(marker)
    assert marker not in repr(error.value)
    _assert_production_trace_clean(error.value, marker, (契約路徑,))


def test_列表拒絕hostile_tuple_subclass且零callback():
    calls = []

    class HostileTuple(tuple):
        def __iter__(self):
            calls.append("iter")
            return super().__iter__()

    with pytest.raises(ValueError):
        憑證列表結果(HostileTuple((_摘要(),)))
    with pytest.raises(ValueError):
        憑證建立命令("name", "purpose", 200.0, HostileTuple(()), 60)
    assert calls == []


class _FakeCreateResult:
    __slots__ = (
        "credential_id", "name", "purpose", "key_prefix", "key_last4", "expires_at",
        "created_at", "ip_allowlist", "rate_limit_requests", "_api", "api_reads",
    )

    def __init__(self, *, 是否畸形=False):
        self.credential_id = "cred-1"
        self.name = " bad" if 是否畸形 else "name"
        self.purpose = "purpose"
        self.key_prefix = "pk_public"
        self.key_last4 = "last"
        self.expires_at = 200.0
        self.created_at = 100.0
        self.ip_allowlist = ()
        self.rate_limit_requests = 60
        self._api = "pk_ONCE-ONLY-SECRET"
        self.api_reads = 0

    @property
    def api_key(self):
        self.api_reads += 1
        return self._api


def test_建立重建先驗所有安全欄位再只讀一次明文(monkeypatch):
    calls = []
    sources = [_FakeCreateResult(是否畸形=True), _FakeCreateResult()]

    class FakeRepository:
        def __init__(self, *args, **kwargs):
            pass

        def 建立管理憑證(self, *args, **kwargs):
            calls.append("create")
            return sources[len(calls) - 1]

    monkeypatch.setattr(管理模組, "建立憑證結果", _FakeCreateResult)
    monkeypatch.setattr(管理模組, "SQLite憑證儲存庫", FakeRepository)
    service = SQLite憑證管理服務(
        "unused.sqlite3", AESGCM憑證封套({1: b"m" * 32}, 1), 時鐘=lambda: 100.0,
    )
    command = 憑證建立命令("name", "purpose", 200.0, (), 60)
    with pytest.raises(憑證管理操作錯誤) as error:
        service.建立憑證(端點識別碼="ep1", 擁有者使用者識別碼="owner-1", 請求=command)
    assert sources[0].api_reads == 0 and sources[0]._api not in repr(error.value)
    _assert_production_trace_clean(error.value, sources[0]._api, (管理路徑,))
    receipt = service.建立憑證(
        端點識別碼="ep1", 擁有者使用者識別碼="owner-1", 請求=command,
    )
    assert calls == ["create", "create"] and sources[1].api_reads == 1
    assert receipt.初始金鑰 == sources[1]._api and receipt.初始金鑰 not in repr(receipt)


class _CustomBase(BaseException):
    pass


@pytest.mark.parametrize("error_type", [KeyboardInterrupt, SystemExit, GeneratorExit])
def test_建立exact_KISG且trace不留request_master(monkeypatch, error_type):
    marker = "CONTROL-PRIVATE-MARKER"
    winner = error_type(marker)

    class FakeRepository:
        def __init__(self, *args, **kwargs): pass
        def 建立管理憑證(self, *args, **kwargs): raise winner

    monkeypatch.setattr(管理模組, "SQLite憑證儲存庫", FakeRepository)
    service = SQLite憑證管理服務("unused", AESGCM憑證封套({1: b"M" * 32}, 1))
    command = 憑證建立命令("name", marker, 200.0, (), 60)
    with pytest.raises(error_type) as caught:
        service.建立憑證(端點識別碼="ep1", 擁有者使用者識別碼="owner-1", 請求=command)
    assert caught.value is winner and caught.value.args == (marker,)
    _assert_production_trace_clean(winner, marker, (管理路徑,))
    _assert_production_trace_clean(winner, "M" * 16, (管理路徑,))


def test_建立ordinary_BaseException固定且不留request_master(monkeypatch):
    marker = "ORDINARY-PRIVATE-MARKER"

    class FakeRepository:
        def __init__(self, *args, **kwargs): pass
        def 建立管理憑證(self, *args, **kwargs): raise _CustomBase(marker)

    monkeypatch.setattr(管理模組, "SQLite憑證儲存庫", FakeRepository)
    service = SQLite憑證管理服務("unused", AESGCM憑證封套({1: b"M" * 32}, 1))
    command = 憑證建立命令("name", marker, 200.0, (), 60)
    with pytest.raises(憑證管理操作錯誤) as caught:
        service.建立憑證(端點識別碼="ep1", 擁有者使用者識別碼="owner-1", 請求=command)
    assert str(caught.value) == "憑證管理失敗"
    assert caught.value.__cause__ is None and caught.value.__context__ is None
    _assert_production_trace_clean(caught.value, marker, (管理路徑,))
    _assert_production_trace_clean(caught.value, "M" * 16, (管理路徑,))


def test_所有公開管理DTO不含crypto與internal欄位():
    objects = [
        _摘要(), 憑證列表結果((_摘要(),)), 憑證撤銷收據("cred-1", 150.0, False),
    ]
    forbidden = (
        "api_key", "key_hash", "nonce", "cipher", "master", "refresh", "proof",
        "revision", "key_version", "internal",
    )
    for value in objects:
        text = repr(value).lower()
        slots = " ".join(getattr(type(value), "__slots__", ())).lower()
        assert all(word not in text and word not in slots for word in forbidden)


@pytest.mark.parametrize("value", [float("nan"), float("inf"), -math.inf, 10**400])
def test_撤銷收據拒絕nonfinite與oversized時間(value):
    with pytest.raises(ValueError):
        憑證撤銷收據("cred-1", value, False)
