"""GOV G01 SQLite交易各階段的控制流程與cleanup precedence測試。"""

import sqlite3
import traceback

import pytest

from 繁中代理.發布介面 import AuditActorRef, AuditEvent, AuditMetadata, AuditResourceRef
from 繁中代理.發布介面.契約 import AuditSinkError
from 繁中代理.發布介面.治理.稽核 import SQLite稽核服務
from 繁中代理.發布介面.資料庫 import 初始化發布介面資料庫


def _事件(識別碼="evt_phase"):
    return AuditEvent(
        event_id=識別碼, occurred_at=1.0, action="audit.phase", outcome="success",
        actor=AuditActorRef("system", None), resource=AuditResourceRef("audit.event", 識別碼),
        metadata=AuditMetadata(),
    )


@pytest.fixture
def 資料庫(tmp_path):
    路徑 = tmp_path / "phase.sqlite"
    初始化發布介面資料庫(路徑)
    return 路徑


class 游標代理:
    def __init__(self, 游標, 階段, 主要錯誤):
        self._游標 = 游標
        self._階段 = 階段
        self._主要錯誤 = 主要錯誤

    @property
    def lastrowid(self):
        if self._階段 == "lastrowid":
            raise self._主要錯誤
        return self._游標.lastrowid

    def close(self):
        if self._階段 == "cursor_close":
            raise self._主要錯誤
        return self._游標.close()


class 連線代理:
    def __init__(
        self, 連線, 階段, 主要錯誤, *, 回滾錯誤=None, 關閉錯誤=None,
    ):
        self._連線 = 連線
        self._階段 = 階段
        self._主要錯誤 = 主要錯誤
        self._回滾錯誤 = 回滾錯誤
        self._關閉錯誤 = 關閉錯誤
        self.database_list次數 = 0
        self.cleanup順序 = []

    def execute(self, SQL, *參數):
        if SQL == "PRAGMA database_list":
            self.database_list次數 += 1
            if self._階段 == "acquire" and self.database_list次數 == 1:
                raise self._主要錯誤
            if self._階段 == "path" and self.database_list次數 == 2:
                raise self._主要錯誤
        if self._階段 == "schema" and SQL.startswith("SELECT version,name FROM"):
            raise self._主要錯誤
        if self._階段 == "insert" and SQL.startswith("INSERT INTO audit_events"):
            raise self._主要錯誤
        游標 = self._連線.execute(SQL, *參數)
        if SQL.startswith("INSERT INTO audit_events"):
            return 游標代理(游標, self._階段, self._主要錯誤)
        return 游標

    def commit(self):
        if self._階段 == "commit":
            raise self._主要錯誤
        return self._連線.commit()

    def rollback(self):
        self.cleanup順序.append("rollback")
        if self._回滾錯誤 is not None:
            raise self._回滾錯誤
        return self._連線.rollback()

    def close(self):
        self.cleanup順序.append("close")
        self._連線.close()
        if self._關閉錯誤 is not None:
            raise self._關閉錯誤


def _注入連線(monkeypatch, 資料庫, 階段, 主要錯誤, **清理錯誤):
    原始connect = sqlite3.connect
    真實連線 = 原始connect(資料庫, timeout=30.0, isolation_level=None)
    代理 = 連線代理(真實連線, 階段, 主要錯誤, **清理錯誤)
    monkeypatch.setattr(sqlite3, "connect", lambda *_args, **_kwargs: 代理)
    return 代理


def _assert控制乾淨(捕捉, 原始, marker):
    assert 捕捉.value is 原始
    assert 捕捉.value.args == (marker,)
    assert 捕捉.value.__cause__ is 捕捉.value.__context__ is None
    for 框架, _ in traceback.walk_tb(捕捉.value.__traceback__):
        if 框架.f_globals.get("__name__", "").startswith("繁中代理.發布介面.治理.稽核"):
            assert marker not in repr(框架.f_locals)


def test_connect控制流程保持identity且不產生cleanup_owner(資料庫, monkeypatch):
    marker = "PRIVATE_CONNECT_MARKER"
    原始 = KeyboardInterrupt(marker)

    def 失敗connect(*_args, **_kwargs):
        raise 原始

    monkeypatch.setattr(sqlite3, "connect", 失敗connect)
    with pytest.raises(KeyboardInterrupt) as 捕捉:
        SQLite稽核服務(str(資料庫)).append_audit_event(_事件())
    _assert控制乾淨(捕捉, 原始, marker)


@pytest.mark.parametrize(
    "階段", ["acquire", "path", "schema", "insert", "lastrowid", "cursor_close", "commit"],
)
def test_各DB階段控制流程保持identity並依序cleanup(資料庫, monkeypatch, 階段):
    marker = f"PRIVATE_{階段}_MARKER"
    原始 = KeyboardInterrupt(marker)
    代理 = _注入連線(monkeypatch, 資料庫, 階段, 原始)

    with pytest.raises(KeyboardInterrupt) as 捕捉:
        SQLite稽核服務(str(資料庫), 時鐘=lambda: 2.0).append_audit_event(_事件())

    _assert控制乾淨(捕捉, 原始, marker)
    if 階段 == "acquire":
        assert 代理.cleanup順序 == ["close"]
    else:
        assert 代理.cleanup順序 == ["rollback", "close"]


def test_primary控制優先於rollback與close控制(資料庫, monkeypatch):
    主要 = KeyboardInterrupt("PRIMARY_MARKER")
    回滾 = SystemExit("ROLLBACK_MARKER")
    關閉 = GeneratorExit("CLOSE_MARKER")
    代理 = _注入連線(
        monkeypatch, 資料庫, "insert", 主要, 回滾錯誤=回滾, 關閉錯誤=關閉,
    )

    with pytest.raises(KeyboardInterrupt) as 捕捉:
        SQLite稽核服務(str(資料庫)).append_audit_event(_事件())

    assert 捕捉.value is 主要
    assert 代理.cleanup順序 == ["rollback", "close"]


@pytest.mark.parametrize("rollback_control", [True, False])
def test_ordinary_primary時cleanup控制winner與rollback_before_close(
    資料庫, monkeypatch, rollback_control,
):
    回滾 = SystemExit("ROLLBACK_WINNER") if rollback_control else RuntimeError("ordinary rollback")
    關閉 = GeneratorExit("CLOSE_WINNER")
    代理 = _注入連線(
        monkeypatch, 資料庫, "insert", RuntimeError("ordinary primary"),
        回滾錯誤=回滾, 關閉錯誤=關閉,
    )
    預期 = SystemExit if rollback_control else GeneratorExit

    with pytest.raises(預期) as 捕捉:
        SQLite稽核服務(str(資料庫)).append_audit_event(_事件())

    assert 捕捉.value is (回滾 if rollback_control else 關閉)
    assert 代理.cleanup順序 == ["rollback", "close"]


@pytest.mark.parametrize("控制關閉", [False, True])
def test_postcommit_close政策不誤報ordinary但傳遞控制(資料庫, monkeypatch, 控制關閉):
    關閉錯誤 = KeyboardInterrupt("POST_COMMIT_CLOSE") if 控制關閉 else RuntimeError("ordinary close")
    代理 = _注入連線(
        monkeypatch, 資料庫, "none", RuntimeError("unused"), 關閉錯誤=關閉錯誤,
    )
    if 控制關閉:
        with pytest.raises(KeyboardInterrupt) as 捕捉:
            SQLite稽核服務(str(資料庫)).append_audit_event(_事件())
        assert 捕捉.value is 關閉錯誤
    else:
        receipt = SQLite稽核服務(str(資料庫)).append_audit_event(_事件())
        assert receipt.committed is True

    assert 代理.cleanup順序 == ["close"]
    monkeypatch.undo()
    with sqlite3.connect(資料庫) as 連線:
        assert 連線.execute("SELECT event_id FROM audit_events").fetchall() == [("evt_phase",)]
