"""GOV G02 擁有者安全與管理員原始呼叫投影測試。"""

import inspect
import sqlite3
from contextlib import closing
import traceback

import pytest

from 繁中代理.發布介面.治理 import 查詢投影
from 繁中代理.發布介面.治理.查詢投影 import (
    管理員原始資料稽核閘門,
    查詢投影錯誤,
    SQLite呼叫查詢投影,
)
from 繁中代理.發布介面.治理.管理查詢契約 import (
    管理員呼叫查詢條件,
    管理員呼叫游標位置,
    管理員呼叫不存在錯誤,
    管理員呼叫查詢錯誤,
    管理員呼叫稽核錯誤,
    管理員呼叫完整詳情,
    管理員拒絕稽核已提交,
    擁有者安全詳情,
)
from 繁中代理.發布介面.治理.稽核 import SQLite稽核服務
from 繁中代理.發布介面.治理.遮蔽 import SQLite不可逆遮蔽服務
from 繁中代理.發布介面.治理.管理查詢契約 import ADMIN_INVOCATION_DETAIL_FIELDS
from 繁中代理.發布介面.資料庫 import 初始化發布介面資料庫


@pytest.fixture
def 呼叫資料庫(tmp_path):
    路徑 = tmp_path / "published.sqlite"
    初始化發布介面資料庫(路徑)
    with closing(sqlite3.connect(路徑)) as 連線, 連線:
        連線.execute("INSERT INTO service_accounts VALUES('sa-1',1,NULL)")
        連線.execute(
            "INSERT INTO published_endpoints VALUES(?,?,?,?,?,?,?,?,?,?)",
            ("ep-1", "owner-1", "sa-1", "safe-endpoint", "active", None, 1, 1, 60, 60),
        )
        連線.execute(
            "INSERT INTO published_endpoint_versions VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            ("ver-1", "ep-1", 1, "需求", "系統", "[]", "[]", "{}", "rev-1", "{}", "{}", "{}", None, "{}", 0, "owner-1", 1),
        )
        連線.execute("UPDATE published_endpoints SET current_version_id='ver-1' WHERE id='ep-1'")
        連線.execute(
            "INSERT INTO endpoint_invocations VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                "inv-1", "ep-1", "ver-1", None, "req-1", "session-1", "message-1",
                "failed", '{"raw_input":"INPUT_SECRET"}',
                '{"raw_metadata":"METADATA_SECRET"}', '{"raw_output":"OUTPUT_SECRET"}',
                '{"code":"schema_invalid","schema_path":"$.answer","internal":"ERROR_SECRET"}',
                '{"total_tokens":17,"provider_detail":"USAGE_SECRET"}', 99,
                "a" * 64, 12.5, "price-v1", 10, 11,
            ),
        )
        連線.execute(
            "INSERT INTO run_events VALUES(?,?,?,?,?,?)",
            ("run-1", "inv-1", 1, "model.output", '{"raw":"RUN_SECRET"}', 10.25),
        )
        連線.execute(
            "INSERT INTO endpoint_tool_calls VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                "tool-1", "inv-1", "run-1", 1, "safe_tool", '{"token":"ARG_SECRET"}',
                "success", '{"secret":"RESULT_SECRET"}', None, 1.5, None, 10.5,
            ),
        )
    return 路徑

def test_擁有者診斷只回固定安全欄位與摘要(呼叫資料庫):
    結果 = SQLite呼叫查詢投影(str(呼叫資料庫)).查詢擁有者診斷(
        "owner-1", "ep-1", "inv-1"
    )

    assert 結果 == {
        "invocation": {"id": "inv-1", "request_id": "req-1", "session_id": "session-1"},
        "endpoint_version_id": "ver-1",
        "status": "failed",
        "error_code": "schema_invalid",
        "schema_path": "$.answer",
        "latency_ms": 12.5,
        "usage": {"total_tokens": 17},
        "tool_names": ["safe_tool"],
    }
    serialized = repr(結果)
    for marker in ("INPUT_SECRET", "METADATA_SECRET", "OUTPUT_SECRET", "ERROR_SECRET", "USAGE_SECRET", "ARG_SECRET", "RESULT_SECRET", "a" * 64):
        assert marker not in serialized

@pytest.mark.parametrize(
    ("owner_id", "endpoint_id", "invocation_id"),
    [
        ("owner-2", "ep-1", "inv-1"),
        ("owner-1", "ep-missing", "inv-1"),
        ("owner-1", "ep-1", "inv-missing"),
    ],
)
def test_擁有者外來與缺少直接識別碼統一失敗關閉(
    呼叫資料庫, owner_id, endpoint_id, invocation_id
):
    with pytest.raises(查詢投影錯誤) as error:
        SQLite呼叫查詢投影(str(呼叫資料庫)).查詢擁有者診斷(
            owner_id, endpoint_id, invocation_id
        )
    assert type(error.value) is 查詢投影錯誤
    assert error.value.args == ("呼叫紀錄不可取得",)
    assert error.value.__cause__ is error.value.__context__ is None

def test_管理員原始投影保留權威payload且transport_neutral(呼叫資料庫):
    結果 = SQLite呼叫查詢投影(str(呼叫資料庫)).查詢管理員原始資料(
        True, "ep-1", "inv-1"
    )

    assert 結果["invocation"] == {
        "id": "inv-1", "request_id": "req-1", "session_id": "session-1"
    }
    assert 結果["endpoint_id"] == "ep-1"
    assert 結果["endpoint_version_id"] == "ver-1"
    assert 結果["message_id"] == "message-1"
    assert 結果["input"] == {"raw_input": "INPUT_SECRET"}
    assert 結果["metadata"] == {"raw_metadata": "METADATA_SECRET"}
    assert 結果["output"] == {"raw_output": "OUTPUT_SECRET"}
    assert 結果["error"]["internal"] == "ERROR_SECRET"
    assert 結果["usage"]["provider_detail"] == "USAGE_SECRET"
    assert 結果["run_events"][0]["payload"] == {"raw": "RUN_SECRET"}
    assert 結果["tool_calls"][0]["arguments"] == {"token": "ARG_SECRET"}
    assert 結果["tool_calls"][0]["result"] == {"secret": "RESULT_SECRET"}
    assert not ({"http_status", "headers", "body"} & 結果.keys())


def test_A18管理員與Owner使用不同typed_DTO且無role_switch(呼叫資料庫):
    """A18 adapter只能依角色呼叫不同method，不得用role flag切換同一schema。"""
    服務 = SQLite呼叫查詢投影(str(呼叫資料庫))
    Owner結果 = 服務.查詢擁有者安全詳情("owner-1", "ep-1", "inv-1")
    Admin結果 = 服務.查詢管理員完整詳情(True, "ep-1", "inv-1")
    assert type(Owner結果) is 擁有者安全詳情
    assert type(Admin結果) is 管理員呼叫完整詳情
    assert type(Owner結果) is not type(Admin結果)
    assert set(Owner結果.建立JSON()) == {
        "invocation", "endpoint_version_id", "status", "error_code", "schema_path",
        "latency_ms", "usage", "tool_names",
    }
    assert Admin結果.建立JSON()["input"] == {"raw_input": "INPUT_SECRET"}
    for 標記 in ("INPUT_SECRET", "ERROR_SECRET", "ARG_SECRET", "RESULT_SECRET"):
        assert 標記 not in repr(Owner結果) and 標記 not in repr(Admin結果)
        assert 標記 not in repr(Owner結果.建立JSON())
    assert "role" not in inspect.signature(服務.查詢擁有者安全詳情).parameters
    assert "role" not in inspect.signature(服務.查詢管理員完整詳情).parameters

@pytest.mark.parametrize("authorized", [False, 1, "admin"])
def test_非精確管理員授權固定為查詢錯誤(呼叫資料庫, authorized):
    with pytest.raises(管理員呼叫查詢錯誤):
        SQLite呼叫查詢投影(str(呼叫資料庫)).查詢管理員原始資料(
            authorized, "ep-1", "inv-1"
        )


@pytest.mark.parametrize(
    ("endpoint_id", "invocation_id"),
    [("ep-other", "inv-1"), ("ep-1", "inv-missing")],
)
def test_管理員missing與wrong_pairing同一不存在錯誤(
    呼叫資料庫, endpoint_id, invocation_id
):
    with pytest.raises(管理員呼叫不存在錯誤) as error:
        SQLite呼叫查詢投影(str(呼叫資料庫)).查詢管理員原始資料(
            True, endpoint_id, invocation_id
        )
    assert error.value.args == ("找不到呼叫紀錄",)
    assert error.value.__cause__ is error.value.__context__ is None


def test_缺少空檔symlink與schema漂移都失敗關閉(tmp_path, 呼叫資料庫):
    empty = tmp_path / "empty.sqlite"
    empty.touch()
    link = tmp_path / "link.sqlite"
    link.symlink_to(呼叫資料庫)
    for path in (tmp_path / "missing.sqlite", empty, link):
        with pytest.raises(查詢投影錯誤):
            SQLite呼叫查詢投影(str(path)).查詢擁有者診斷("owner-1", "ep-1", "inv-1")
    with closing(sqlite3.connect(呼叫資料庫)) as 連線, 連線:
        連線.execute("DROP INDEX idx_endpoint_invocations_status_created")
    for 呼叫 in (
        lambda: SQLite呼叫查詢投影(str(呼叫資料庫)).查詢擁有者診斷("owner-1", "ep-1", "inv-1"),
        lambda: SQLite呼叫查詢投影(str(呼叫資料庫)).查詢管理員原始資料(True, "ep-1", "inv-1"),
    ):
        with pytest.raises(查詢投影錯誤):
            呼叫()


def test_同名欄位與索引但缺少全域呼叫主鍵時兩種投影皆在payload前失敗(
    monkeypatch, 呼叫資料庫,
):
    from 繁中代理.發布介面.治理 import 查詢投影

    with closing(sqlite3.connect(呼叫資料庫)) as 連線:
        連線.execute("PRAGMA foreign_keys=OFF")
        建表語句 = 連線.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='endpoint_invocations'"
        ).fetchone()[0]
        with 連線:
            偽造語句 = 建表語句.replace(
                "CREATE TABLE endpoint_invocations", "CREATE TABLE counterfeit_invocations", 1
            ).replace("id TEXT PRIMARY KEY", "id TEXT", 1)
            連線.execute(偽造語句)
            欄位 = ",".join(列[1] for 列 in 連線.execute(
                "PRAGMA table_info(endpoint_invocations)"
            ))
            連線.execute(
                f"INSERT INTO counterfeit_invocations({欄位}) SELECT {欄位} FROM endpoint_invocations"
            )
            連線.execute("DROP TABLE endpoint_invocations")
            連線.execute("ALTER TABLE counterfeit_invocations RENAME TO endpoint_invocations")
            連線.execute("CREATE INDEX idx_endpoint_invocations_endpoint_created ON endpoint_invocations(endpoint_id,created_at)")
            連線.execute("CREATE INDEX idx_endpoint_invocations_status_created ON endpoint_invocations(status,created_at)")
            連線.execute("CREATE INDEX idx_endpoint_invocations_credential_created ON endpoint_invocations(credential_id,created_at)")
    解析次數 = []
    原解析 = 查詢投影._解析可空JSON

    def 記錄解析(*引數, **關鍵字):
        解析次數.append(1)
        return 原解析(*引數, **關鍵字)

    monkeypatch.setattr(查詢投影, "_解析可空JSON", 記錄解析)
    服務 = SQLite呼叫查詢投影(str(呼叫資料庫))
    for 呼叫 in (
        lambda: 服務.查詢擁有者診斷("owner-1", "ep-1", "inv-1"),
        lambda: 服務.查詢管理員原始資料(True, "ep-1", "inv-1"),
    ):
        with pytest.raises(查詢投影錯誤) as 錯誤:
            呼叫()
        assert 錯誤.value.args == ("呼叫紀錄不可取得",)
    assert 解析次數 == []


def test_非管理員在任何資料庫callback前拒絕(monkeypatch, 呼叫資料庫):
    from 繁中代理.發布介面.治理 import 查詢投影

    calls = []
    monkeypatch.setattr(查詢投影, "_建立連線", lambda *a, **k: calls.append(1))
    with pytest.raises(查詢投影錯誤):
        SQLite呼叫查詢投影(str(呼叫資料庫)).查詢管理員原始資料(False, "ep-1", "inv-1")
    assert calls == []


@pytest.mark.parametrize("method", ["owner", "admin"])
def test_敵對自訂base固定失敗且控制流程保留identity(monkeypatch, 呼叫資料庫, method):
    from 繁中代理.發布介面.治理 import 查詢投影

    class 敵對Base(BaseException):
        pass

    def 執行():
        服務 = SQLite呼叫查詢投影(str(呼叫資料庫))
        if method == "owner":
            return 服務.查詢擁有者診斷("owner-1", "ep-1", "inv-1")
        return 服務.查詢管理員原始資料(True, "ep-1", "inv-1")

    def 丟出(error):
        def connect(*_args, **_kwargs):
            raise error
        monkeypatch.setattr(查詢投影, "_建立連線", connect)

    丟出(敵對Base("PRIVATE_MARKER"))
    with pytest.raises(查詢投影錯誤) as fixed:
        執行()
    assert fixed.value.args == ("呼叫紀錄不可取得",)
    assert fixed.value.__cause__ is fixed.value.__context__ is None
    for 類型 in (KeyboardInterrupt, SystemExit, GeneratorExit):
        control = 類型("CONTROL_MARKER")
        丟出(control)
        with pytest.raises(類型) as caught:
            執行()
        assert caught.value is control
        assert caught.value.args == ("CONTROL_MARKER",)
        assert caught.value.__cause__ is caught.value.__context__ is None


def test_擁有者動態儲存類型失敗而不輸出blob(呼叫資料庫):
    with closing(sqlite3.connect(呼叫資料庫)) as 連線, 連線:
        連線.execute("PRAGMA ignore_check_constraints=ON")
        連線.execute("UPDATE endpoint_invocations SET status=x'534543524554' WHERE id='inv-1'")
    with pytest.raises(查詢投影錯誤):
        SQLite呼叫查詢投影(str(呼叫資料庫)).查詢擁有者診斷("owner-1", "ep-1", "inv-1")


def test_投影原始碼不得使用select星號():
    import inspect
    from 繁中代理.發布介面.治理 import 查詢投影

    assert "SELECT *" not in inspect.getsource(查詢投影).upper()


class _清理連線代理:
    def __init__(self, 連線, *, 主要=None, 回滾=None, 關閉=None):
        self.連線, self.主要, self.回滾, self.關閉 = 連線, 主要, 回滾, 關閉
        self.順序 = []
        self.語句 = []

    def execute(self, sql, *args):
        self.語句.append(sql)
        if self.主要 is not None and sql.startswith("SELECT version,name"):
            raise self.主要
        return self.連線.execute(sql, *args)

    def commit(self):
        return self.連線.commit()

    def rollback(self):
        self.順序.append("rollback")
        if self.回滾 is not None:
            raise self.回滾
        return self.連線.rollback()

    def close(self):
        self.順序.append("close")
        self.連線.close()
        if self.關閉 is not None:
            raise self.關閉


def _assert投影框架無標記(error, marker):
    for frame, _ in traceback.walk_tb(error.__traceback__):
        if frame.f_globals.get("__name__") == "繁中代理.發布介面.治理.查詢投影":
            assert marker not in repr(tuple(frame.f_locals.values()))


@pytest.mark.parametrize("method", ["owner", "admin"])
def test_主要自訂base仍依序rollback與close(monkeypatch, 呼叫資料庫, method):
    from 繁中代理.發布介面.治理 import 查詢投影

    class 敵對Base(BaseException):
        pass

    真實 = sqlite3.connect(呼叫資料庫, isolation_level=None)
    代理 = _清理連線代理(
        真實, 主要=敵對Base("PRIMARY_PRIVATE"),
        回滾=敵對Base("ROLLBACK_PRIVATE"), 關閉=敵對Base("CLOSE_PRIVATE"),
    )
    monkeypatch.setattr(查詢投影, "_建立連線", lambda *_a, **_k: 代理)
    服務 = SQLite呼叫查詢投影(str(呼叫資料庫))
    with pytest.raises(查詢投影錯誤):
        (服務.查詢擁有者診斷("owner-1", "ep-1", "inv-1") if method == "owner"
         else 服務.查詢管理員原始資料(True, "ep-1", "inv-1"))
    assert 代理.順序 == ["rollback", "close"]


@pytest.mark.parametrize("method", ["owner", "admin"])
def test_postcommit_close控制保留identity且清空helper敏感locals(
    monkeypatch, 呼叫資料庫, method,
):
    from 繁中代理.發布介面.治理 import 查詢投影

    marker = "OWNER_PRIVATE_MARKER" if method == "owner" else "INPUT_SECRET"
    if method == "owner":
        with closing(sqlite3.connect(呼叫資料庫)) as 連線, 連線:
            連線.execute("UPDATE published_endpoints SET owner_user_id=?", (marker,))
    control = KeyboardInterrupt(marker)
    代理 = _清理連線代理(sqlite3.connect(呼叫資料庫, isolation_level=None), 關閉=control)
    monkeypatch.setattr(查詢投影, "_建立連線", lambda *_a, **_k: 代理)
    服務 = SQLite呼叫查詢投影(str(呼叫資料庫))
    with pytest.raises(KeyboardInterrupt) as caught:
        (服務.查詢擁有者診斷(marker, "ep-1", "inv-1") if method == "owner"
         else 服務.查詢管理員原始資料(True, "ep-1", "inv-1"))
    assert caught.value is control
    assert caught.value.__cause__ is caught.value.__context__ is None
    assert 代理.順序 == ["close"]
    _assert投影框架無標記(caught.value, marker)


@pytest.mark.parametrize("payload", ['{"x":NaN}', '{"x":1e999}', '{"x":1,"x":2}'])
def test_管理員原始JSON拒絕非有限與重複鍵(呼叫資料庫, payload):
    with closing(sqlite3.connect(呼叫資料庫)) as 連線, 連線:
        連線.execute("PRAGMA ignore_check_constraints=ON")
        連線.execute("UPDATE endpoint_invocations SET input_json=?", (payload,))
    with pytest.raises(查詢投影錯誤):
        SQLite呼叫查詢投影(str(呼叫資料庫)).查詢管理員原始資料(True, "ep-1", "inv-1")


def test_管理員全部動態欄位與child列數都失敗關閉(monkeypatch, 呼叫資料庫):
    from 繁中代理.發布介面.治理 import 查詢投影

    with closing(sqlite3.connect(呼叫資料庫)) as 連線, 連線:
        連線.execute("PRAGMA ignore_check_constraints=ON")
        連線.execute("UPDATE endpoint_invocations SET message_id=x'534543524554'")
    with pytest.raises(查詢投影錯誤):
        SQLite呼叫查詢投影(str(呼叫資料庫)).查詢管理員原始資料(True, "ep-1", "inv-1")
    with closing(sqlite3.connect(呼叫資料庫)) as 連線, 連線:
        連線.execute("UPDATE endpoint_invocations SET message_id='message-1'")
    monkeypatch.setattr(查詢投影, "_最大子列", 0)
    for 呼叫 in (
        lambda: SQLite呼叫查詢投影(str(呼叫資料庫)).查詢擁有者診斷("owner-1", "ep-1", "inv-1"),
        lambda: SQLite呼叫查詢投影(str(呼叫資料庫)).查詢管理員原始資料(True, "ep-1", "inv-1"),
    ):
        with pytest.raises(查詢投影錯誤):
            呼叫()


@pytest.mark.parametrize(("預算名稱", "預算值"), [("_最大JSON位元組", 120), ("_最大JSON節點", 12)])
def test_管理員跨多列聚合JSON預算超限會失敗並關閉真實資源(
    monkeypatch, 呼叫資料庫, 預算名稱, 預算值,
):
    from 繁中代理.發布介面.治理 import 查詢投影

    with closing(sqlite3.connect(呼叫資料庫)) as 連線, 連線:
        連線.execute("DELETE FROM endpoint_tool_calls")
        連線.execute("DELETE FROM run_events")
        連線.execute(
            "UPDATE endpoint_invocations SET input_json='{}',metadata_json=NULL,output_json=NULL,"
            "error_json=NULL,usage_json=NULL"
        )
        for 順序 in range(1, 10):
            連線.execute(
                "INSERT INTO run_events VALUES(?,?,?,?,?,?)",
                (f"run-{順序}", "inv-1", 順序, "chunk", '{"chunk":"abcdefghij"}', 20 + 順序),
            )
    真建立 = 查詢投影._建立連線
    代理列 = []

    def 建立代理(*引數, **關鍵字):
        代理 = _清理連線代理(真建立(*引數, **關鍵字))
        代理列.append(代理)
        return 代理

    monkeypatch.setattr(查詢投影, 預算名稱, 預算值)
    monkeypatch.setattr(查詢投影, "_建立連線", 建立代理)
    with pytest.raises(查詢投影錯誤):
        SQLite呼叫查詢投影(str(呼叫資料庫)).查詢管理員原始資料(True, "ep-1", "inv-1")
    assert len(代理列) == 1
    assert 代理列[0].順序 == ["rollback", "close"]
    with pytest.raises(sqlite3.ProgrammingError):
        代理列[0].連線.execute("SELECT 1")


@pytest.mark.parametrize("目標", ["invocation", "run_event"])
def test_單一超大有效JSON只取長度且不執行任何payload查詢(
    monkeypatch, 呼叫資料庫, 目標,
):
    from 繁中代理.發布介面.治理 import 查詢投影

    with closing(sqlite3.connect(呼叫資料庫)) as 連線, 連線:
        if 目標 == "invocation":
            連線.execute(
                "UPDATE endpoint_invocations SET input_json="
                "'{\"huge\":\"' || printf('%.*c',1100000,'x') || '\"}' WHERE id='inv-1'"
            )
        else:
            連線.execute(
                "UPDATE run_events SET payload_json="
                "'{\"huge\":\"' || printf('%.*c',1100000,'x') || '\"}' WHERE id='run-1'"
            )
    真建立 = 查詢投影._建立連線
    代理列 = []

    def 建立代理(*引數, **關鍵字):
        代理 = _清理連線代理(真建立(*引數, **關鍵字))
        代理列.append(代理)
        return 代理

    monkeypatch.setattr(查詢投影, "_建立連線", 建立代理)
    with pytest.raises(查詢投影錯誤):
        SQLite呼叫查詢投影(str(呼叫資料庫)).查詢管理員原始資料(
            True, "ep-1", "inv-1"
        )
    assert len(代理列) == 1
    assert not any(語句.startswith("SELECT input_json") for 語句 in 代理列[0].語句)
    assert not any(語句.startswith("SELECT payload_json") for 語句 in 代理列[0].語句)


def test_管理員原始資料先提交安全稽核才呼叫detail且不保存raw(呼叫資料庫):
    順序 = []

    def detail(端點識別碼, 呼叫識別碼):
        with closing(sqlite3.connect(呼叫資料庫)) as 連線:
            順序.append(連線.execute(
                "SELECT event_id,action,outcome,actor_type,actor_id,request_id,endpoint_id,"
                "invocation_id,metadata_json FROM audit_events"
            ).fetchone())
        return SQLite呼叫查詢投影(str(呼叫資料庫)).查詢管理員原始資料(
            True, 端點識別碼, 呼叫識別碼
        )

    閘門 = 管理員原始資料稽核閘門(
        SQLite稽核服務(str(呼叫資料庫), 時鐘=lambda: 101.0), detail,
        lambda _端點, _呼叫: True,
    )
    結果 = 閘門.查詢管理員原始資料(
        True, "admin-1", "req-admin-1", "evt-admin-1", 100.0, "ep-1", "inv-1"
    )

    assert 結果.建立JSON()["input"] == {"raw_input": "INPUT_SECRET"}
    assert 順序 == [(
        "evt-admin-1", "audit.detail.view", "success", "user", "admin-1", "req-admin-1",
        "ep-1", "inv-1", "{}",
    )]
    with closing(sqlite3.connect(呼叫資料庫)) as 連線:
        稽核文字 = repr(連線.execute("SELECT * FROM audit_events").fetchall())
    for 標記 in ("INPUT_SECRET", "METADATA_SECRET", "OUTPUT_SECRET", "ARG_SECRET", "RESULT_SECRET"):
        assert 標記 not in 稽核文字
        assert 標記 not in repr(閘門)


def test_A18_missing_pairing仍提交nullable_FK稽核後固定404且detail零呼叫(呼叫資料庫):
    """不存在的requested IDs不得因audit FK失敗變成503，也不得先讀raw。"""
    呼叫 = []

    def pairing_exists(端點識別碼, 呼叫識別碼):
        return SQLite呼叫查詢投影(str(呼叫資料庫)).管理員呼叫配對存在(
            端點識別碼, 呼叫識別碼
        )

    def detail(*引數):
        呼叫.append(引數)
        pytest.fail("missing pairing不得讀取raw detail")

    閘門 = 管理員原始資料稽核閘門(
        SQLite稽核服務(str(呼叫資料庫)), detail, pairing_exists,
    )
    with pytest.raises(管理員呼叫不存在錯誤) as 捕捉:
        閘門.查詢管理員原始資料(
            True, "admin-1", "req-missing", "evt-missing", 100.0,
            "ep-1", "inv-missing",
        )
    assert 捕捉.value.args == ("找不到呼叫紀錄",)
    assert 呼叫 == []
    with closing(sqlite3.connect(呼叫資料庫)) as 連線:
        assert 連線.execute(
            "SELECT event_id,action,outcome,actor_id,resource_type,resource_id,"
            "request_id,endpoint_id,invocation_id,metadata_json FROM audit_events"
        ).fetchone() == (
            "evt-missing", "audit.detail.view", "success", "admin-1",
            "endpoint.invocation", "inv-missing", "req-missing", None, None, "{}",
        )


def test_A18_pairing_preflight只查存在性且故障不寫audit不讀raw(呼叫資料庫, monkeypatch):
    """Preflight必須零raw；非bool／一般故障固定query error並停在audit前。"""
    投影 = SQLite呼叫查詢投影(str(呼叫資料庫))
    assert 投影.管理員呼叫配對存在("ep-1", "inv-1") is True
    assert 投影.管理員呼叫配對存在("ep-1", "inv-missing") is False

    for 結果 in (None, 1, "true", RuntimeError("PAIRING_PRIVATE")):
        稽核呼叫 = []
        detail呼叫 = []

        class Sink:
            def append_audit_event(self, event):
                稽核呼叫.append(event)

        def pairing(*_引數):
            if isinstance(結果, BaseException):
                raise 結果
            return 結果

        def detail(*引數):
            detail呼叫.append(引數)

        with pytest.raises(管理員呼叫查詢錯誤) as 捕捉:
            管理員原始資料稽核閘門(Sink(), detail, pairing).查詢管理員原始資料(
                True, "admin-1", "req-pair", "evt-pair", 100.0, "ep-1", "inv-1",
            )
        assert 捕捉.value.args == ("呼叫紀錄不可取得",)
        assert "PAIRING_PRIVATE" not in repr(捕捉.value)
        assert 稽核呼叫 == [] and detail呼叫 == []

    class 連線代理:
        def __init__(self, 連線):
            self._連線 = 連線
            self.語句 = []

        def execute(self, SQL, *參數):
            self.語句.append(SQL)
            return self._連線.execute(SQL, *參數)

        def __getattr__(self, 名稱):
            return getattr(self._連線, 名稱)

    代理列 = []
    原連線 = sqlite3.connect

    def 建立代理(*引數, **關鍵字):
        代理 = 連線代理(原連線(*引數, **關鍵字))
        代理列.append(代理)
        return 代理

    monkeypatch.setattr(查詢投影, "_建立連線", 建立代理)
    assert SQLite呼叫查詢投影(str(呼叫資料庫)).管理員呼叫配對存在("ep-1", "inv-1")
    配對SQL = [
        語句 for 語句 in 代理列[-1].語句
        if 語句.startswith("SELECT") and "endpoint_invocations" in 語句
    ]
    assert 配對SQL == ["SELECT 1 FROM endpoint_invocations WHERE endpoint_id=? AND id=? LIMIT 2"]
    assert not any(欄位 in 配對SQL[0] for 欄位 in ("input_json", "metadata_json", "output_json", "error_json"))


@pytest.mark.parametrize("授權", [False, 0, 1, "admin", None])
def test_非精確管理員仍先安全稽核denied且detail零呼叫(呼叫資料庫, 授權):
    呼叫 = []

    def detail(*_引數):
        呼叫.append(1)
        return {"raw": "不得取得"}

    閘門 = 管理員原始資料稽核閘門(
        SQLite稽核服務(str(呼叫資料庫)), detail, lambda _端點, _呼叫: True,
    )
    結果 = 閘門.查詢管理員原始資料(
        授權, "admin-1", "req-denied", f"evt-denied-{type(授權).__name__}",
        100.0, "ep-1", "inv-1",
    )
    assert 結果 is 管理員拒絕稽核已提交
    assert 呼叫 == []
    with closing(sqlite3.connect(呼叫資料庫)) as 連線:
        assert 連線.execute("SELECT outcome FROM audit_events").fetchone() == ("denied",)


@pytest.mark.parametrize("偽造收據", [False, True])
def test_稽核失敗或偽造收據都fail_closed且detail零呼叫(呼叫資料庫, 偽造收據):
    from 繁中代理.發布介面 import AuditAppendReceipt

    class 敵對Base(BaseException):
        pass

    class Sink:
        def append_audit_event(self, event):
            if not 偽造收據:
                raise 敵對Base("SINK_PRIVATE")
            收據 = AuditAppendReceipt(event.event_id, True, 1)
            object.__setattr__(收據, "committed", False)
            return 收據

    呼叫 = []

    def detail(*_引數):
        呼叫.append(1)
        return {"raw": "RAW_PRIVATE"}

    閘門 = 管理員原始資料稽核閘門(Sink(), detail, lambda _端點, _呼叫: True)
    with pytest.raises(管理員呼叫稽核錯誤) as 錯誤:
        閘門.查詢管理員原始資料(
            True, "admin-1", "req-fail", "evt-fail", 100.0, "ep-1", "inv-1"
        )
    assert 錯誤.value.__cause__ is 錯誤.value.__context__ is None
    assert 呼叫 == []


def test_A18稽核stage禁止偽裝404且已提交後provider不存在原樣穿透(呼叫資料庫):
    """Outcome分類由可信stage決定，不信任hostile sink自行選擇例外型別。"""
    class 偽裝不存在Sink:
        def append_audit_event(self, _event):
            raise 管理員呼叫不存在錯誤("HOSTILE_404")

    def 不應呼叫(*_引數):
        pytest.fail("audit未提交不得進detail")

    with pytest.raises(管理員呼叫稽核錯誤) as 稽核錯誤:
        管理員原始資料稽核閘門(
            偽裝不存在Sink(), 不應呼叫, lambda _端點, _呼叫: True,
        ).查詢管理員原始資料(
            True, "admin-1", "req-hostile", "evt-hostile", 100.0, "ep-1", "inv-1"
        )
    assert "HOSTILE_404" not in repr(稽核錯誤.value)

    class 已提交Sink:
        def append_audit_event(self, event):
            from 繁中代理.發布介面 import AuditAppendReceipt
            return AuditAppendReceipt(event.event_id, True, 1)

    def 真detail(_端點, _呼叫):
        raise 管理員呼叫不存在錯誤("PROVIDER_PRIVATE")

    with pytest.raises(管理員呼叫不存在錯誤) as 不存在:
        管理員原始資料稽核閘門(
            已提交Sink(), 真detail, lambda _端點, _呼叫: True,
        ).查詢管理員原始資料(
            True, "admin-1", "req-missing", "evt-missing", 100.0, "ep-1", "inv-missing"
        )
    assert 不存在.value.args == ("找不到呼叫紀錄",)
    assert "PROVIDER_PRIVATE" not in repr(不存在.value)


def test_A18稽核閘門在committed後仍重建typed_detail並拒絕callback_raw繞過():
    """Audit成功不是raw dict直通權限；callback結果仍須typed/bounded/secret gate。"""
    class 已提交Sink:
        def append_audit_event(self, event):
            from 繁中代理.發布介面 import AuditAppendReceipt
            return AuditAppendReceipt(event.event_id, True, 1)

    for raw in (
        {"metadata": "wrong-type", "input": {"Authorization": "SECRET"}},
        {"input": {"Authorization": "SECRET"}},
    ):
        def detail(*_引數, _raw=raw):
            return _raw

        with pytest.raises(管理員呼叫查詢錯誤):
            管理員原始資料稽核閘門(
                已提交Sink(), detail, lambda _端點, _呼叫: True,
            ).查詢管理員原始資料(
                True, "admin-1", "req-1", "audit-1", 1.0, "ep-1", "inv-1",
            )


def test_detail只接受exact_function而不觸發敵對callable():
    class 敵對Callable:
        def __init__(self):
            self.呼叫次數 = 0

        def __call__(self, *_引數):
            self.呼叫次數 += 1

    detail = 敵對Callable()
    with pytest.raises(查詢投影錯誤):
        管理員原始資料稽核閘門(object(), detail, lambda _端點, _呼叫: True)
    assert detail.呼叫次數 == 0


def test_detail自訂base固定失敗而KISG保留identity且清空閘門locals(呼叫資料庫):
    class 敵對Base(BaseException):
        pass

    狀態 = [敵對Base("RAW_PRIVATE")]

    def detail(*_引數):
        raise 狀態[0]

    閘門 = 管理員原始資料稽核閘門(
        SQLite稽核服務(str(呼叫資料庫)), detail, lambda _端點, _呼叫: True,
    )
    with pytest.raises(查詢投影錯誤) as 固定:
        閘門.查詢管理員原始資料(
            True, "admin-1", "req-custom", "evt-custom", 100.0, "ep-1", "inv-1"
        )
    assert 固定.value.__cause__ is 固定.value.__context__ is None
    for 索引, 類型 in enumerate((KeyboardInterrupt, SystemExit, GeneratorExit), 1):
        控制 = 類型("RAW_PRIVATE")
        狀態[0] = 控制
        with pytest.raises(類型) as 捕捉:
            閘門.查詢管理員原始資料(
                True, "admin-1", f"req-control-{索引}", f"evt-control-{索引}",
                100.0, "ep-1", "inv-1",
            )
        assert 捕捉.value is 控制
        _assert投影框架無標記(捕捉.value, "RAW_PRIVATE")


def test_A18管理員安全列表依endpoint_filter與keyset排序且零raw(呼叫資料庫):
    """Safe list只回metadata，固定created_at/id倒序且position不重複。"""
    with closing(sqlite3.connect(呼叫資料庫)) as 連線, 連線:
        for 識別碼, 請求, 狀態, 錯誤, 建立 in (
            ("inv-2", "req-2", "succeeded", None, 20.0),
            ("inv-3", "req-3", "failed", '{"code":"timeout","internal":"LIST_SECRET"}', 20.0),
        ):
            連線.execute(
                "INSERT INTO endpoint_invocations("
                "id,endpoint_id,endpoint_version_id,request_id,status,input_json,error_json,"
                "latency_ms,created_at,completed_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
                (識別碼, "ep-1", "ver-1", 請求, 狀態,
                 '{"raw":"LIST_INPUT_SECRET"}', 錯誤, 2.0, 建立, 建立 + 1),
            )
    服務 = SQLite呼叫查詢投影(str(呼叫資料庫))
    條件 = 管理員呼叫查詢條件("ep-1", 0.0, 30.0, None, None, 2)

    第一頁 = 服務.列出管理員安全呼叫(條件, None)
    assert [項目.呼叫識別碼 for 項目 in 第一頁.項目] == ["inv-3", "inv-2"]
    assert 第一頁.下一頁位置 == 管理員呼叫游標位置(20.0, "inv-2")
    assert all(項目.端點識別碼 == "ep-1" for 項目 in 第一頁.項目)
    assert 第一頁.項目[0].錯誤碼 == "timeout"
    assert "LIST_SECRET" not in repr(第一頁)
    assert "LIST_INPUT_SECRET" not in repr(第一頁)

    第二頁 = 服務.列出管理員安全呼叫(條件, 第一頁.下一頁位置)
    assert [項目.呼叫識別碼 for 項目 in 第二頁.項目] == ["inv-1"]
    assert 第二頁.下一頁位置 is None
    失敗 = 服務.列出管理員安全呼叫(
        管理員呼叫查詢條件("ep-1", None, None, "failed", "timeout", 10), None,
    )
    assert [項目.呼叫識別碼 for 項目 in 失敗.項目] == ["inv-3"]


def test_A18管理員安全列表錯誤scope與projection原始碼均失敗關閉(呼叫資料庫):
    """Provider只接受exact contract DTO，且owned SQL禁止SELECT星號。"""
    服務 = SQLite呼叫查詢投影(str(呼叫資料庫))
    for 條件, 位置 in (
        (object(), None),
        (管理員呼叫查詢條件("ep-1", None, None, None, None, 20), object()),
    ):
        with pytest.raises(查詢投影錯誤) as 錯誤:
            服務.列出管理員安全呼叫(條件, 位置)
        assert 錯誤.value.args == ("呼叫紀錄不可取得",)
        assert 錯誤.value.__cause__ is 錯誤.value.__context__ is None
    from 繁中代理.發布介面.治理 import 查詢投影
    assert "SELECT *" not in inspect.getsource(查詢投影).upper()


def test_A18管理員detail欄位allowlist與canonical墓碑不洩漏原文(呼叫資料庫):
    """#20 DTO未凍結；detail只凍結payload內canonical tombstone。"""
    SQLite不可逆遮蔽服務(str(呼叫資料庫)).遮蔽(
        True, "red-1", "audit-red-1", "admin-1", "req-red-1", "inv-1",
        "invocation_input", "inv-1", "/raw_input", "privacy", 12.0,
    )
    服務 = SQLite呼叫查詢投影(str(呼叫資料庫))
    列表 = 服務.列出管理員安全呼叫(
        管理員呼叫查詢條件("ep-1", None, None, None, None, 20), None,
    )
    assert len(列表.項目) == 1 and 列表.項目[0].是否有遮蔽 is True
    結果 = 服務.查詢管理員原始資料(
        True, "ep-1", "inv-1"
    )
    assert set(結果) == ADMIN_INVOCATION_DETAIL_FIELDS
    assert 結果["input"] == {
        "raw_input": {"$tombstone": {"redaction_id": "red-1", "redacted_at": 12.0}}
    }
    assert 結果["redactions"] == [{
        "id": "red-1", "target_type": "invocation_input", "target_row_id": "inv-1",
        "json_path": "/raw_input", "reason": "privacy",
        "is_tombstone": True, "redacted_at": 12.0,
    }]
    for 禁止 in ("INPUT_SECRET", "original_sha256", "admin-1", "audit-red-1", "req-red-1"):
        assert 禁止 not in repr(結果)


def test_A18安全列表在遮蔽schema漂移時不信任has_redactions(呼叫資料庫):
    """List的redaction indicator與raw detail共用完整治理schema gate。"""
    with closing(sqlite3.connect(呼叫資料庫)) as 連線, 連線:
        連線.execute("DROP TRIGGER endpoint_redactions_no_update")
    with pytest.raises(查詢投影錯誤):
        SQLite呼叫查詢投影(str(呼叫資料庫)).列出管理員安全呼叫(
            管理員呼叫查詢條件("ep-1", None, None, None, None, 20), None,
        )
