"""GOV G01 callback、cleanup與traceback安全邊界測試。"""

import math
import sqlite3
import traceback

import pytest

from 繁中代理.發布介面 import AuditActorRef, AuditEvent, AuditMetadata, AuditResourceRef
from 繁中代理.發布介面.契約 import AuditSinkError
from 繁中代理.發布介面.治理.稽核 import SQLite稽核服務
from 繁中代理.發布介面.資料庫 import 初始化發布介面資料庫


def _事件():
    return AuditEvent(
        event_id="evt_1", occurred_at=123.5, action="audit.detail.view", outcome="success",
        actor=AuditActorRef("user", "user-1"),
        resource=AuditResourceRef("audit.event", "target-1"),
        request_id="req-1", endpoint_id=None, invocation_id=None,
        metadata=AuditMetadata(),
    )


@pytest.fixture
def 資料庫(tmp_path):
    路徑 = tmp_path / "published.sqlite"
    初始化發布介面資料庫(路徑)
    return 路徑


class _HostileBaseException(BaseException):
    pass


@pytest.mark.parametrize("error_type", [_HostileBaseException, KeyboardInterrupt, SystemExit, GeneratorExit])
def test_clock_callback_BaseException政策與exact_once(資料庫, error_type):
    marker = "CALLBACK_SECRET_MARKER"
    original = error_type(marker)
    calls = []

    def fail():
        calls.append(1)
        raise original

    if error_type is _HostileBaseException:
        with pytest.raises(AuditSinkError) as captured:
            SQLite稽核服務(str(資料庫), 時鐘=fail).append_audit_event(_事件())
        assert type(captured.value) is AuditSinkError
        assert captured.value.args == ("稽核事件無法確認提交",)
        assert captured.value.__cause__ is captured.value.__context__ is None
    else:
        with pytest.raises(error_type) as captured:
            SQLite稽核服務(str(資料庫), 時鐘=fail).append_audit_event(_事件())
        assert captured.value is original
        assert captured.value.args == (marker,)
        assert captured.value.__cause__ is captured.value.__context__ is None
    assert calls == [1]


@pytest.mark.parametrize("value", [True, -1, math.inf, math.nan, 253402300800])
def test_clock非法回傳固定失敗且資料庫未開啟(資料庫, monkeypatch, value):
    def 時鐘():
        return value

    def forbidden_connect(*_args, **_kwargs):
        raise AssertionError("clock 驗證前不可開啟資料庫")

    monkeypatch.setattr(sqlite3, "connect", forbidden_connect)
    with pytest.raises(AuditSinkError):
        SQLite稽核服務(str(資料庫), 時鐘=時鐘).append_audit_event(_事件())


def test_post_connect驗證失敗由acquirer_exact_once_close且清除marker(資料庫, monkeypatch):
    """Connection成功建立但尚未移交時，helper必須自行關閉且不洩漏locals。"""
    marker = "POST_CONNECT_PRIVATE_MARKER"

    class 失敗連線:
        def __init__(self):
            self.closed = 0

        def execute(self, _sql, *_args):
            raise RuntimeError(marker)

        def close(self):
            self.closed += 1

    連線 = 失敗連線()
    monkeypatch.setattr(sqlite3, "connect", lambda *_args, **_kwargs: 連線)

    with pytest.raises(AuditSinkError) as captured:
        SQLite稽核服務(str(資料庫), 時鐘=lambda: 1.0).append_audit_event(_事件())

    assert 連線.closed == 1
    for frame, _ in traceback.walk_tb(captured.value.__traceback__):
        if frame.f_globals.get("__name__") == "繁中代理.發布介面.治理.稽核":
            assert marker not in repr(frame.f_locals)


def test_hostile控制流程subclass覆寫setattr仍保持identity(資料庫):
    """清理control chain不得呼叫敵對subclass覆寫的__setattr__。"""
    class 敵對中斷(KeyboardInterrupt):
        def __setattr__(self, _name, _value):
            raise RuntimeError("不得呼叫覆寫方法")

    original = 敵對中斷("control-marker")

    def 時鐘():
        raise original

    with pytest.raises(敵對中斷) as captured:
        SQLite稽核服務(str(資料庫), 時鐘=時鐘).append_audit_event(_事件())
    assert captured.value is original
    assert captured.value.args == ("control-marker",)
    assert captured.value.__cause__ is captured.value.__context__ is None
