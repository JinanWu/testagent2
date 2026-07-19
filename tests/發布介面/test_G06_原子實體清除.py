"""GOV G06 R44 原子、有界實體清除與guard復原。"""
from contextlib import closing
from datetime import datetime, timezone
import sqlite3
import threading


import pytest

from 繁中代理.發布介面.資料庫 import 初始化發布介面資料庫
from 繁中代理.發布介面.治理 import 保存期限 as 模組
from 繁中代理.發布介面.治理.保存期限 import (
    SQLite保存清除服務, 保存清除錯誤,
)


def _秒(年, 月=1, 日=1):
    return datetime(年, 月, 日, tzinfo=timezone.utc).timestamp()


@pytest.fixture
def 資料庫(tmp_path):
    路徑 = tmp_path / "purge.sqlite"
    初始化發布介面資料庫(路徑)
    with closing(sqlite3.connect(路徑)) as 連線, 連線:
        連線.execute("PRAGMA foreign_keys=ON")
        連線.execute("INSERT INTO service_accounts VALUES('sa',0,NULL)")
        連線.execute("INSERT INTO published_endpoints(id,owner_user_id,service_account_id,slug,status,created_at,updated_at) VALUES('ep','owner','sa','slug','active',0,0)")
        連線.execute("INSERT INTO published_endpoint_versions VALUES('ver','ep',1,'r','s','[]','[]','{}','rev','{}','{}','{}',NULL,'{}',0,'owner',0)")
        連線.execute("UPDATE published_endpoints SET current_version_id='ver' WHERE id='ep'")
    return 路徑


def _根(連線, 識別碼, 建立, 狀態="succeeded", payload="{}"):
    連線.execute(
        "INSERT INTO endpoint_invocations(id,endpoint_id,endpoint_version_id,request_id,status,input_json,created_at) VALUES(?,?,?,?,?,?,?)",
        (識別碼, "ep", "ver", "req-" + 識別碼, 狀態, payload, 建立),
    )


def _完整相依(連線, 識別碼, 建立):
    連線.execute("INSERT INTO run_events VALUES(?,?,?,?,?,?)", ("run-" + 識別碼, 識別碼, 1, "event", '{"private":"run"}', 建立))
    連線.execute(
        "INSERT INTO endpoint_tool_calls(id,invocation_id,run_event_id,sequence_number,tool_name,arguments_json,outcome,result_json,created_at) VALUES(?,?,?,?,?,'{}','success','{}',?)",
        ("tool-" + 識別碼, 識別碼, "run-" + 識別碼, 1, "tool", 建立),
    )
    連線.execute(
        "INSERT INTO audit_events VALUES(?,?,?,?,?,'system',NULL,'invocation',?,NULL,'ep',?,'{}',?)",
        ("audit-" + 識別碼, "event-" + 識別碼, 建立, "retention.test", "success", 識別碼, 識別碼, 建立),
    )
    連線.execute(
        "INSERT INTO endpoint_redactions VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
        ("red-" + 識別碼, 識別碼, "run_event", "run-" + 識別碼, "", "a" * 64,
         "privacy", "system", None, "audit-" + 識別碼, 1, 建立),
    )


def _guards(連線):
    名稱 = 模組._保存刪除guard名稱
    return tuple(連線.execute(
        "SELECT name,sql FROM sqlite_master WHERE type='trigger' AND name IN (?,?,?,?) ORDER BY name", 名稱,
    ))


def test_到期根連同所有新鮮相依原子清除且第二次為零並復原guards(資料庫):
    with closing(sqlite3.connect(資料庫)) as 連線, 連線:
        _根(連線, "eligible", _秒(2020, 2, 29), "rate_limited", '{"private":"root"}')
        _完整相依(連線, "eligible", _秒(2025, 2, 27))
        _根(連線, "fresh", _秒(2024))
        原guards = _guards(連線)
    服務 = SQLite保存清除服務(str(資料庫))
    結果 = 服務.清除(_秒(2025, 2, 28))
    assert 結果 == 模組.保存清除結果(1, 1, 1, 1, 1)
    assert 服務.清除(_秒(2025, 2, 28)) == 模組.保存清除結果(0, 0, 0, 0, 0)
    with closing(sqlite3.connect(資料庫)) as 連線:
        assert 連線.execute("SELECT id FROM endpoint_invocations").fetchall() == [("fresh",)]
        assert all(連線.execute(f"SELECT count(*) FROM {表}").fetchone()[0] == 0 for 表 in
                   ("run_events", "endpoint_tool_calls", "endpoint_redactions", "audit_events"))
        assert _guards(連線) == 原guards
        連線.execute("INSERT INTO audit_events VALUES('standalone','standalone',0,'test','success','system',NULL,'system','x',NULL,NULL,NULL,'{}',0)")
        with pytest.raises(sqlite3.IntegrityError):
            連線.execute("DELETE FROM audit_events WHERE id='standalone'")


def test_批次依實際期限再依ID且精確閏日邊界(資料庫):
    with closing(sqlite3.connect(資料庫)) as 連線, 連線:
        _根(連線, "z", _秒(2020, 2, 28))
        _根(連線, "a", _秒(2020, 2, 29))
        _根(連線, "future", _秒(2020, 3, 1))
    結果 = SQLite保存清除服務(str(資料庫)).清除(_秒(2025, 2, 28), 批次上限=1)
    assert 結果.呼叫數 == 1
    with closing(sqlite3.connect(資料庫)) as 連線:
        assert 連線.execute("SELECT id FROM endpoint_invocations ORDER BY id").fetchall() == [("future",), ("z",)]


_失敗階段 = (
    *(("execute", SQL) for SQL in 模組._保存刪除guardDROP),
    *(("execute", SQL) for SQL in 模組._保存刪除guardSQL),
    *(("execute", f"DELETE FROM {表} WHERE " +
       ("id IN (?)" if 表 == "endpoint_invocations" else "invocation_id IN (?)"))
      for 表 in 模組.保存刪除順序),
    ("commit", "COMMIT"),
)


@pytest.mark.parametrize("方法,階段", _失敗階段, ids=[f"{方法}:{階段.split()[0]}:{序號}" for 序號, (方法, 階段) in enumerate(_失敗階段)])
def test_每個精確DDL刪除與提交階段失敗均完整回滾(資料庫, monkeypatch, 方法, 階段):
    with closing(sqlite3.connect(資料庫)) as 連線, 連線:
        _根(連線, "eligible", 0)
        _完整相依(連線, "eligible", 0)
        連線.execute(
            "INSERT INTO endpoint_redactions VALUES('red-tool','eligible','tool_result','tool-eligible','',?,'privacy','system',NULL,'audit-eligible',1,0)",
            ("b" * 64,),
        )
        原guards = _guards(連線)
    真實 = sqlite3.connect
    class 代理:
        def __init__(self):
            self.c = 真實(資料庫, isolation_level=None)
            self.命中 = 0
        def execute(self, sql, *args):
            if 方法 == "execute" and sql == 階段:
                self.命中 += 1
                raise sqlite3.OperationalError("injected")
            return self.c.execute(sql, *args)
        def commit(self):
            if 方法 == "commit":
                self.命中 += 1
                raise sqlite3.OperationalError("injected")
            return self.c.commit()
        def rollback(self): return self.c.rollback()
        def close(self): return self.c.close()
    連線代理 = 代理()
    monkeypatch.setattr(模組, "_建立寫入連線", lambda _路徑: 連線代理)
    with pytest.raises(保存清除錯誤):
        SQLite保存清除服務(str(資料庫)).清除(_秒(2026))
    with closing(sqlite3.connect(資料庫)) as 連線:
        assert 連線代理.命中 == 1
        assert 連線.execute("SELECT id FROM endpoint_invocations").fetchall() == [("eligible",)]
        assert 連線.execute("SELECT id FROM run_events").fetchall() == [("run-eligible",)]
        assert 連線.execute("SELECT id FROM endpoint_tool_calls").fetchall() == [("tool-eligible",)]
        assert 連線.execute("SELECT id FROM audit_events").fetchall() == [("audit-eligible",)]
        assert 連線.execute("SELECT id FROM endpoint_redactions ORDER BY id").fetchall() == [("red-eligible",), ("red-tool",)]
        assert _guards(連線) == 原guards
        for SQL in (
            "DELETE FROM audit_events WHERE id='audit-eligible'",
            "DELETE FROM endpoint_redactions WHERE id='red-eligible'",
            "DELETE FROM run_events WHERE id='run-eligible'",
            "DELETE FROM endpoint_tool_calls WHERE id='tool-eligible'",
        ):
            with pytest.raises(sqlite3.IntegrityError):
                連線.execute(SQL)


def test_不信任偽造G05計畫且SELECT從不讀payload也不VACUUM(資料庫, monkeypatch):
    with closing(sqlite3.connect(資料庫)) as 連線, 連線:
        _根(連線, "fresh", _秒(2024), payload='{"PRIVATE_PAYLOAD":"x"}')
    SQL = []
    真實 = sqlite3.connect
    def 建立(_路徑):
        連線 = 真實(資料庫, isolation_level=None)
        連線.set_trace_callback(SQL.append)
        return 連線
    monkeypatch.setattr(模組, "_建立寫入連線", 建立)
    偽造 = 模組.保存候選計畫("fresh", 0, (), (), (), (), 0, 0, 0, 0, (), ())
    assert not hasattr(SQLite保存清除服務, "依計畫清除")
    assert 偽造.呼叫識別碼 == "fresh"
    assert SQLite保存清除服務(str(資料庫)).清除(_秒(2025)).呼叫數 == 0
    禁止 = ("input_json", "metadata_json", "payload_json", "arguments_json", "result_json", "error_json", "original_sha256", "reason")
    assert not any(欄位 in sql.lower() for 欄位 in 禁止 for sql in SQL if sql.lstrip().upper().startswith("SELECT"))
    assert not any("VACUUM" in sql.upper() for sql in SQL)


def test_standalone稽核不屬於root相依政策且不會overreach(資料庫):
    with closing(sqlite3.connect(資料庫)) as 連線, 連線:
        連線.execute("INSERT INTO audit_events VALUES('old','old',0,'test','success','system',NULL,'system','x',NULL,NULL,NULL,'{}',0)")
        _根(連線, "eligible", 0)
    assert SQLite保存清除服務(str(資料庫)).清除(_秒(2026)).呼叫數 == 1
    with closing(sqlite3.connect(資料庫)) as 連線:
        assert 連線.execute("SELECT id FROM audit_events").fetchall() == [("old",)]


def test_BEGIN_IMMEDIATE失敗回滾時writer鎖定且reader快照之後見新值(資料庫, monkeypatch):
    with closing(sqlite3.connect(資料庫)) as 連線, 連線:
        _根(連線, "eligible", 0)
    with closing(sqlite3.connect(資料庫)) as 設定:
        assert 設定.execute("PRAGMA journal_mode=WAL").fetchone() == ("wal",)
    到達 = threading.Event(); 釋放 = threading.Event(); 清除結束 = threading.Event()
    writer鎖定 = threading.Event(); writer結束 = threading.Event()
    清除錯誤 = []; writer錯誤 = []
    真實 = sqlite3.connect
    class 閘門代理:
        def __init__(self): self.c = 真實(資料庫, isolation_level=None)
        def execute(self, sql, *args):
            if sql == "DELETE FROM endpoint_redactions WHERE invocation_id IN (?)":
                到達.set()
                if not 釋放.wait(3): raise AssertionError("purge gate timeout")
                raise sqlite3.OperationalError("injected rollback")
            return self.c.execute(sql, *args)
        def commit(self): return self.c.commit()
        def rollback(self): return self.c.rollback()
        def close(self): return self.c.close()
    monkeypatch.setattr(模組, "_建立寫入連線", lambda _路徑: 閘門代理())
    def 清除工作():
        try: SQLite保存清除服務(str(資料庫)).清除(_秒(2026))
        except 保存清除錯誤: pass
        except BaseException as 錯誤: 清除錯誤.append(錯誤)
        finally: 清除結束.set()
    def writer工作():
        try:
            with closing(真實(資料庫, isolation_level=None, timeout=0)) as 寫入:
                try: 寫入.execute("UPDATE endpoint_invocations SET status='failed' WHERE id='eligible'")
                except sqlite3.OperationalError as 錯誤:
                    if "locked" not in str(錯誤): raise
                    writer鎖定.set()
                if not 釋放.wait(3) or not 清除結束.wait(3): raise AssertionError("writer gate timeout")
                寫入.execute("UPDATE endpoint_invocations SET status='failed' WHERE id='eligible'")
        except BaseException as 錯誤: writer錯誤.append(錯誤)
        finally: writer結束.set()
    reader = 真實(資料庫, isolation_level=None)
    reader.execute("BEGIN")
    assert reader.execute("SELECT status FROM endpoint_invocations").fetchone() == ("succeeded",)
    清除執行緒 = threading.Thread(target=清除工作)
    writer執行緒 = threading.Thread(target=writer工作)
    清除執行緒.start(); assert 到達.wait(3)
    writer執行緒.start(); assert writer鎖定.wait(3)
    assert reader.execute("SELECT status FROM endpoint_invocations").fetchone() == ("succeeded",)
    釋放.set()
    assert 清除結束.wait(3) and writer結束.wait(3)
    清除執行緒.join(3); writer執行緒.join(3)
    assert not 清除執行緒.is_alive() and not writer執行緒.is_alive()
    assert not 清除錯誤 and not writer錯誤
    assert reader.execute("SELECT status FROM endpoint_invocations").fetchone() == ("succeeded",)
    reader.rollback()
    assert reader.execute("SELECT status FROM endpoint_invocations").fetchone() == ("failed",)
    reader.close()


class _結構計數游標:
    def __init__(self, 游標, 代理, metadata):
        self._游標 = 游標; self._代理 = 代理; self._metadata = metadata
    def __iter__(self):
        for 列 in self._游標:
            if self._metadata: self._代理.metadata列 += 1
            yield 列
    def __getattr__(self, 名稱): return getattr(self._游標, 名稱)


class _結構計數代理:
    def __init__(self, 連線):
        self.c = 連線; self.metadata列 = self.SQL原文查詢 = 0
    def execute(self, sql, *args):
        metadata = sql.startswith("SELECT type,name,typeof(sql),length(CAST(sql AS BLOB)) FROM sqlite_master")
        if "sqlite_master" in sql and ",sql" in sql.replace(" ", ""):
            self.SQL原文查詢 += 1
        return _結構計數游標(self.c.execute(sql, *args), self, metadata)
    def commit(self): return self.c.commit()
    def rollback(self): return self.c.rollback()
    def close(self): return self.c.close()


def test_敵對額外物件只讀expected加一metadata且不物化SQL(資料庫, monkeypatch):
    with closing(sqlite3.connect(資料庫)) as 連線, 連線:
        for 序號 in range(20):
            連線.execute(f"CREATE TABLE adversarial_extra_{序號}(value TEXT)")
    代理 = _結構計數代理(sqlite3.connect(資料庫, isolation_level=None))
    monkeypatch.setattr(模組, "_建立寫入連線", lambda _路徑: 代理)
    with pytest.raises(保存清除錯誤):
        SQLite保存清除服務(str(資料庫)).清除(_秒(2026))
    assert 代理.metadata列 == 模組._完整結構數 + 1
    assert 代理.SQL原文查詢 == 0


def test_aggregate_SQL預算超限亦在原文物化前失敗(資料庫, monkeypatch):
    代理 = _結構計數代理(sqlite3.connect(資料庫, isolation_level=None))
    monkeypatch.setattr(模組, "_建立寫入連線", lambda _路徑: 代理)
    monkeypatch.setattr(模組, "_完整結構SQL總最大位元組", 1)
    with pytest.raises(保存清除錯誤):
        SQLite保存清除服務(str(資料庫)).清除(_秒(2026))
    assert 代理.metadata列 == 模組._完整結構數
    assert 代理.SQL原文查詢 == 0


@pytest.mark.parametrize("種類,名稱,建立", [
    ("TRIGGER", "audit_events_no_update", "BEFORE UPDATE ON audit_events BEGIN /*{填充}*/ SELECT RAISE(ABORT,'x'); END"),
    ("INDEX", "idx_audit_events_endpoint_time", "ON audit_events(endpoint_id,created_at) /*{填充}*/"),
])
def test_巨大SQL物件在原文物化前失敗(資料庫, monkeypatch, 種類, 名稱, 建立):
    with closing(sqlite3.connect(資料庫)) as 連線, 連線:
        連線.execute(f"DROP {種類} {名稱}")
        連線.execute(f"CREATE {種類} {名稱} " + 建立.format(填充="x" * 65537))
    代理 = _結構計數代理(sqlite3.connect(資料庫, isolation_level=None))
    monkeypatch.setattr(模組, "_建立寫入連線", lambda _路徑: 代理)
    with pytest.raises(保存清除錯誤):
        SQLite保存清除服務(str(資料庫)).清除(_秒(2026))
    assert 代理.metadata列 <= 模組._完整結構數
    assert 代理.SQL原文查詢 == 0
