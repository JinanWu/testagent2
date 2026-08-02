"""GOV G05 R44/R51/R100 五年保存期限與候選計畫測試。"""

import math
import sqlite3
from contextlib import closing
from datetime import datetime, timezone

import pytest

from 繁中代理.發布介面.資料庫 import 初始化發布介面資料庫
from 繁中代理.發布介面.治理 import 保存期限 as 模組
from 繁中代理.發布介面.治理.保存期限 import (
    SQLite保存候選規劃器,
    保存候選規劃錯誤,
    五年保存期限,
    已達五年保存期限,
)


def _秒(年, 月, 日, 時=0, 分=0, 秒=0, 微秒=0):
    return datetime(年, 月, 日, 時, 分, 秒, 微秒, tzinfo=timezone.utc).timestamp()


@pytest.mark.parametrize("建立,到期", [
    ((2020, 2, 29, 12), (2025, 2, 28, 12)),
    ((2095, 2, 28, 12), (2100, 2, 28, 12)),
    ((2000, 2, 29, 12), (2005, 2, 28, 12)),
])
def test_五個Gregorian曆年且閏日固定落在二月二十八日(建立, 到期):
    assert 五年保存期限(_秒(*建立)) == _秒(*到期)


def test_UTC微秒邊界等於到期且前一微秒仍保留():
    建立 = _秒(2020, 2, 29, 12, 34, 56, 123456)
    到期 = 五年保存期限(建立)
    assert not 已達五年保存期限(建立, 到期 - 0.000001)
    assert 已達五年保存期限(建立, 到期)


class 整數子類(int):
    pass


class 浮點子類(float):
    pass


@pytest.mark.parametrize("值", [True, -1, math.inf, -math.inf, math.nan, 10**400, 整數子類(1), 浮點子類(1.0)])
def test_時間只接受可映射UTC的exact非負有限epoch秒(值):
    with pytest.raises(保存候選規劃錯誤) as 錯誤:
        五年保存期限(值)
    assert 錯誤.value.args == ("五年保存候選無法規劃",)
    assert 錯誤.value.__cause__ is 錯誤.value.__context__ is None


@pytest.fixture
def 資料庫(tmp_path):
    路徑 = tmp_path / "retention.sqlite"
    初始化發布介面資料庫(路徑)
    with closing(sqlite3.connect(路徑)) as 連線, 連線:
        連線.execute("PRAGMA foreign_keys=ON")
        連線.execute("INSERT INTO service_accounts VALUES('sa',0,NULL)")
        連線.execute("INSERT INTO published_endpoints(id,owner_user_id,service_account_id,slug,status,created_at,updated_at) VALUES('ep','owner','sa','slug','active',0,0)")
        連線.execute("INSERT INTO published_endpoint_versions VALUES('ver','ep',1,'r','s','[]','[]','{}','rev','{}','{}','{}',NULL,'{}',0,'owner',0)")
        連線.execute("UPDATE published_endpoints SET current_version_id='ver' WHERE id='ep'")
    return 路徑


def _呼叫(連線, 識別碼, 建立時間, 狀態="succeeded"):
    連線.execute(
        "INSERT INTO endpoint_invocations(id,endpoint_id,endpoint_version_id,request_id,status,input_json,created_at) VALUES(?,?,?,?,?,'{}',?)",
        (識別碼, "ep", "ver", "req-" + 識別碼, 狀態, 建立時間),
    )


def _規劃(資料庫, 現在, **選項):
    return SQLite保存候選規劃器(str(資料庫)).規劃(現在, **選項)


def test_rate_limited根與所有相依資料共用根期限且standalone稽核不誤選(資料庫):
    建立 = _秒(2020, 2, 29)
    with closing(sqlite3.connect(資料庫)) as 連線, 連線:
        連線.execute("PRAGMA foreign_keys=ON")
        _呼叫(連線, "inv", 建立, "rate_limited")
        連線.execute("INSERT INTO run_events VALUES('run-z','inv',1,'event','{}',?)", (_秒(2025, 2, 27),))
        連線.execute("INSERT INTO endpoint_tool_calls(id,invocation_id,run_event_id,sequence_number,tool_name,arguments_json,outcome,result_json,created_at) VALUES('tool-a','inv','run-z',1,'tool','{}','success','{}',?)", (_秒(2025, 2, 27),))
        連線.execute("INSERT INTO audit_events VALUES('audit-root','audit-root',?, 'retention.test','success','system',NULL,'invocation','inv',NULL,'ep','inv','{}',?)", (_秒(2025, 2, 27),) * 2)
        連線.execute("INSERT INTO endpoint_redactions VALUES('red-a','inv','invocation_input','inv','','" + "a" * 64 + "','privacy','system',NULL,'audit-root',1,?)", (_秒(2025, 2, 27),))
        連線.execute("INSERT INTO audit_events VALUES('audit-old','audit-old',0,'standalone.test','success','system',NULL,'system','standalone',NULL,NULL,NULL,'{}',0)")
    assert _規劃(資料庫, _秒(2025, 2, 28)) == (
        模組.保存候選計畫(
            "inv", _秒(2025, 2, 28), ("run-z",), ("tool-a",), ("red-a",),
            ("audit-root",), 1, 1, 1, 1,
            模組.保存刪除順序,
            ("endpoint_redactions_no_delete", "audit_events_no_delete"),
        ),
    )


def test_候選與每根相依均有硬上限且同時間依ID排序(資料庫):
    with closing(sqlite3.connect(資料庫)) as 連線, 連線:
        _呼叫(連線, "b", 0)
        _呼叫(連線, "a", 0)
        _呼叫(連線, "fresh", _秒(2024, 1, 1))
    assert [項.呼叫識別碼 for 項 in _規劃(資料庫, _秒(2026, 1, 1), 候選上限=1)] == ["a"]
    with pytest.raises(保存候選規劃錯誤):
        _規劃(資料庫, _秒(2026, 1, 1), 候選上限=True)
    with pytest.raises(保存候選規劃錯誤):
        _規劃(資料庫, _秒(2026, 1, 1), 相依上限=0)


def test_二月二十八未到期列不會遮住較晚建立但已到期的閏日根(資料庫):
    現在 = _秒(2025, 2, 28, 12)
    with closing(sqlite3.connect(資料庫)) as 連線, 連線:
        _呼叫(連線, "unexpired", _秒(2020, 2, 28, 23))
        _呼叫(連線, "leap", _秒(2020, 2, 29))
    assert [項.呼叫識別碼 for 項 in _規劃(資料庫, 現在, 候選上限=1)] == ["leap"]


def test_超過候選上限的未到期二月二十八列仍不會耗盡閏日範圍(資料庫):
    現在 = _秒(2025, 2, 28, 12)
    with closing(sqlite3.connect(資料庫)) as 連線, 連線:
        for 序號 in range(4):
            _呼叫(連線, f"unexpired-{序號}", _秒(2020, 2, 28, 13, 序號))
        _呼叫(連線, "leap-a", _秒(2020, 2, 29, 10))
        _呼叫(連線, "leap-b", _秒(2020, 2, 29, 11))
    assert [項.呼叫識別碼 for 項 in _規劃(資料庫, 現在, 候選上限=2)] == ["leap-a", "leap-b"]


def test_兩個不重疊範圍依實際期限再依ID合併排序(資料庫):
    現在 = _秒(2025, 2, 28, 12)
    with closing(sqlite3.connect(資料庫)) as 連線, 連線:
        _呼叫(連線, "z-normal", _秒(2020, 2, 28, 12))
        _呼叫(連線, "a-leap", _秒(2020, 2, 29, 12))
        _呼叫(連線, "m-earlier", _秒(2020, 2, 29, 11, 59, 59, 999999))
    計畫 = _規劃(資料庫, 現在)
    assert [(項.保存期限, 項.呼叫識別碼) for 項 in 計畫] == [
        (_秒(2025, 2, 28, 11, 59, 59, 999999), "m-earlier"),
        (現在, "a-leap"), (現在, "z-normal"),
    ]


@pytest.mark.parametrize("現在,建立,應選", [
    ((2025, 2, 28, 12, 0, 0, 123456), (2020, 2, 29, 12, 0, 0, 123455), True),
    ((2025, 2, 28, 12, 0, 0, 123456), (2020, 2, 29, 12, 0, 0, 123456), True),
    ((2025, 2, 28, 12, 0, 0, 123456), (2020, 2, 29, 12, 0, 0, 123457), False),
    ((2100, 2, 28, 12), (2095, 2, 28, 12), True),
    ((2100, 2, 28, 12), (2095, 2, 28, 12, 0, 0, 1), False),
    ((2101, 2, 28, 12), (2096, 2, 29, 12), True),
    ((2101, 2, 28, 12), (2096, 2, 29, 12, 0, 0, 1), False),
])
def test_候選精確邊界與2100世紀規則(資料庫, 現在, 建立, 應選):
    with closing(sqlite3.connect(資料庫)) as 連線, 連線:
        _呼叫(連線, "boundary", _秒(*建立))
    assert bool(_規劃(資料庫, _秒(*現在))) is 應選


def test_現在為閏日時正常範圍涵蓋五年前二月二十八日全日(資料庫):
    with closing(sqlite3.connect(資料庫)) as 連線, 連線:
        _呼叫(連線, "feb28-last", _秒(2019, 2, 28, 23, 59, 59, 999999))
        _呼叫(連線, "march1", _秒(2019, 3, 1))
    assert [項.呼叫識別碼 for 項 in _規劃(資料庫, _秒(2024, 2, 29))] == ["feb28-last"]


def test_根與相依查詢計畫使用釘選範圍索引且不另排序(資料庫):
    查詢 = (
        ("endpoint_invocations", "idx_endpoint_invocations_retention_candidates",
         "SELECT typeof(id),id,typeof(created_at),created_at FROM endpoint_invocations "
         "WHERE created_at<=? ORDER BY created_at,id LIMIT ?", (1, 2)),
        ("endpoint_invocations", "idx_endpoint_invocations_retention_candidates",
         "SELECT typeof(id),id,typeof(created_at),created_at FROM endpoint_invocations "
         "WHERE created_at>=? AND created_at<=? ORDER BY created_at,id LIMIT ?", (1, 2, 3)),
    )
    for 表格, 索引, SQL, 參數 in 查詢:
        with closing(sqlite3.connect(資料庫)) as 連線:
            細節 = " ".join(列[3] for 列 in 連線.execute("EXPLAIN QUERY PLAN " + SQL, 參數))
        assert 索引 in 細節 and "TEMP B-TREE" not in 細節
    相依 = {
        "run_events": ("typeof(id),id", "idx_run_events_retention_invocation_id"),
        "endpoint_tool_calls": ("typeof(id),id", "idx_endpoint_tool_calls_retention_invocation_id"),
        "endpoint_redactions": (
            "typeof(id),id,typeof(target_type),target_type",
            "idx_endpoint_redactions_retention_invocation_id",
        ),
        "audit_events": ("typeof(id),id", "idx_audit_events_retention_invocation_id"),
    }
    with closing(sqlite3.connect(資料庫)) as 連線:
        for 表格, (欄位, 索引) in 相依.items():
            SQL = f"SELECT {欄位} FROM {表格} WHERE invocation_id=? ORDER BY id LIMIT ?"
            細節 = " ".join(列[3] for 列 in 連線.execute("EXPLAIN QUERY PLAN " + SQL, ("x", 2)))
            assert 索引 in 細節 and "TEMP B-TREE" not in 細節


@pytest.mark.parametrize("改動", [
    "DROP INDEX idx_endpoint_invocations_retention_candidates",
    "ALTER TABLE endpoint_invocations ADD COLUMN drift TEXT",
])
def test_ledger_schema或索引漂移一律失敗關閉(資料庫, 改動):
    with closing(sqlite3.connect(資料庫)) as 連線, 連線:
        連線.execute(改動)
    with pytest.raises(保存候選規劃錯誤):
        _規劃(資料庫, _秒(2026, 1, 1))


def test_候選查詢從不選取payload雜湊原因或API金鑰(資料庫, monkeypatch):
    with closing(sqlite3.connect(資料庫)) as 連線, 連線:
        _呼叫(連線, "inv", 0)
    SQL = []
    原建立 = sqlite3.connect
    def 建立(*參數, **選項):
        連線 = 原建立(*參數, **選項)
        連線.set_trace_callback(SQL.append)
        return 連線
    monkeypatch.setattr(模組, "_建立連線", 建立)
    assert len(_規劃(資料庫, _秒(2026, 1, 1))) == 1
    禁止 = ("input_json", "payload_json", "arguments_json", "metadata_json", "original_sha256", "reason", "secret_ciphertext", "verification_hash")
    assert not any(欄位 in 陳述.lower() for 欄位 in 禁止 for 陳述 in SQL if 陳述.lstrip().upper().startswith("SELECT"))
