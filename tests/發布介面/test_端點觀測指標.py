"""GOV SQLite owner/admin metrics provider regression tests。"""

import sqlite3
from contextlib import closing

import pytest

from 繁中代理.發布介面.治理.觀測供應器 import SQLite端點觀測查詢服務, 端點觀測查詢錯誤
from 繁中代理.發布介面.治理.觀測契約 import 指標查詢成功, 端點不可見結果
from 繁中代理.發布介面.資料庫 import 初始化發布介面資料庫


@pytest.fixture
def 指標資料庫(tmp_path):
    """建立含混合狀態、latency 與 historical pricing 的資料庫。"""
    路徑 = tmp_path / "metrics.sqlite"
    初始化發布介面資料庫(路徑)
    with closing(sqlite3.connect(路徑)) as 連線, 連線:
        連線.execute("INSERT INTO service_accounts VALUES('sa-1',1,NULL)")
        連線.execute("INSERT INTO service_accounts VALUES('sa-2',1,NULL)")
        for 端點, 擁有者, 帳號 in (("ep-1", "owner-1", "sa-1"), ("ep-2", "owner-2", "sa-2")):
            連線.execute(
                "INSERT INTO published_endpoints VALUES(?,?,?,?,?,?,?,?,?,?)",
                (端點, 擁有者, 帳號, 端點, "active", None, 1, 1, 60, 60),
            )
            連線.execute(
                "INSERT INTO published_endpoint_versions VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (f"ver-{端點[-1]}", 端點, 1, "需求", "系統", "[]", "[]", "{}", "rev", "{}", "{}", "{}", None, "{}", 0, 擁有者, 1),
            )
            連線.execute("UPDATE published_endpoints SET current_version_id=? WHERE id=?", (f"ver-{端點[-1]}", 端點))
        資料列 = (
            ("i1", "succeeded", 10.0, '{"input_tokens":10,"output_tokens":2,"total_tokens":12,"estimated_cost_usd":"0.001"}', "price-b", 90.0),
            ("i2", "failed", 20.0, '{"input_tokens":3,"output_tokens":4,"total_tokens":7,"estimated_cost_usd":"0.002"}', "price-a", 80.0),
            ("i3", "pending", 30.0, None, None, 70.0),
            ("i4", "rate_limited", None, None, None, 60.0),
            ("old", "invalid_api_key", 999.0, None, None, 49.0),
        )
        for 識別碼, 狀態, 延遲, 用量, 定價, 建立時間 in 資料列:
            連線.execute(
                "INSERT INTO endpoint_invocations VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (識別碼, "ep-1", "ver-1", None, f"req-{識別碼}", None, None, 狀態,
                 "{}", None, None, None, 用量, None, None, 延遲, 定價, 建立時間,
                 建立時間 if 狀態 in ("pending", "running") else 建立時間 + 1),
            )
    return 路徑


def _服務(路徑):
    """建立固定 clock 與 module-owned signing key 的 provider。"""
    return SQLite端點觀測查詢服務(str(路徑), 時鐘=lambda: 100.0, 游標簽章金鑰=b"k" * 32)


def test_metrics聚合狀態延遲用量與歷史成本精確(指標資料庫):
    """凍結 nearest-rank p50/p95、error denominator 與 ASCII pricing 排序。"""
    結果 = _服務(指標資料庫).讀取端點指標(
        擁有者使用者識別碼="owner-1", 是否管理者=False, 端點識別碼="ep-1", 視窗秒數=50,
    )
    assert type(結果) is 指標查詢成功
    指標 = 結果.指標
    assert (指標.window.start_at, 指標.window.end_at) == (50.0, 100.0)
    assert (指標.invocation_count, 指標.terminal_count, 指標.error_count) == (4, 3, 2)
    assert 指標.error_rate == 2 / 3
    assert 指標.latency_ms == type(指標.latency_ms)(3, 20.0, 20.0, 30.0, 30.0)
    assert (指標.usage.sample_count, 指標.usage.input_tokens, 指標.usage.output_tokens, 指標.usage.total_tokens) == (2, 13, 6, 19)
    assert 指標.estimated_cost_usd == "0.003"
    assert tuple((項.pricing_version, 項.estimated_cost_usd) for 項 in 指標.cost_by_pricing_version) == (("price-a", "0.002"), ("price-b", "0.001"))


@pytest.mark.parametrize(("擁有者", "端點"), (("owner-2", "ep-1"), ("owner-1", "missing")))
def test_missing與foreign共用typed不可見(指標資料庫, 擁有者, 端點):
    """零列只回 anti-enumeration outcome。"""
    結果 = _服務(指標資料庫).讀取端點指標(
        擁有者使用者識別碼=擁有者, 是否管理者=False, 端點識別碼=端點, 視窗秒數=50,
    )
    assert type(結果) is 端點不可見結果


def test_admin可讀foreign且empty_window為exact_zero(指標資料庫):
    """管理員 authority 仍走同一 operation，空窗不產生 NaN。"""
    結果 = _服務(指標資料庫).讀取端點指標(
        擁有者使用者識別碼="admin-1", 是否管理者=True, 端點識別碼="ep-2", 視窗秒數=50,
    )
    assert type(結果) is 指標查詢成功
    assert 結果.指標.invocation_count == 結果.指標.terminal_count == 結果.指標.error_count == 0
    assert 結果.指標.error_rate == 0.0
    assert 結果.指標.latency_ms.sample_count == 結果.指標.usage.sample_count == 0


def test_畸形persisted_usage與動態欄位是operational_failure(指標資料庫):
    """Corruption 不得誤判成 endpoint missing。"""
    with closing(sqlite3.connect(指標資料庫)) as 連線, 連線:
        連線.execute("UPDATE endpoint_invocations SET usage_json='{\"total_tokens\":7}' WHERE id='i2'")
    with pytest.raises(端點觀測查詢錯誤) as 捕捉:
        _服務(指標資料庫).讀取端點指標(
            擁有者使用者識別碼="owner-1", 是否管理者=False, 端點識別碼="ep-1", 視窗秒數=50,
        )
    assert 捕捉.value.args == ("端點觀測不可取得",)
    assert 捕捉.value.__cause__ is 捕捉.value.__context__ is None


def test_兩筆18位成本同版本形成19位canonical總和(指標資料庫):
    with closing(sqlite3.connect(指標資料庫)) as 連線, 連線:
        for 序號, (版本, 成本) in enumerate((
            ("price-carry", "999999999999999999"),
            ("price-carry", "999999999999999999"),
            ("price-other", "0.9999999999999999999999999999"),
            ("price-carry", "0.0000000000000000000000000001"),
        )):
            用量 = ('{"input_tokens":0,"output_tokens":0,"total_tokens":0,'
                  f'"estimated_cost_usd":"{成本}"}}')
            連線.execute(
                "INSERT INTO endpoint_invocations(id,endpoint_id,endpoint_version_id,request_id,status,"
                "input_json,usage_json,pricing_version,created_at) VALUES(?,?,?,?,?,?,?,?,?)",
                (f"carry-{序號}", "ep-1", "ver-1", f"req-carry-{序號}", "succeeded",
                 "{}", 用量, 版本, 99.0),
            )
    指標 = _服務(指標資料庫).讀取端點指標(
        擁有者使用者識別碼="owner-1", 是否管理者=False, 端點識別碼="ep-1", 視窗秒數=50,
    ).指標
    分項 = {項.pricing_version: 項.estimated_cost_usd for 項 in 指標.cost_by_pricing_version}
    assert 分項["price-carry"] == "1999999999999999998.0000000000000000000000000001"
    assert 分項["price-other"] == "0.9999999999999999999999999999"
    assert 指標.estimated_cost_usd == "1999999999999999999.003"


def test_超大usage在任何payload_SELECT與fetchone前固定失敗(monkeypatch, 指標資料庫):
    from 繁中代理.發布介面.治理 import 查詢投影
    with closing(sqlite3.connect(指標資料庫)) as 連線, 連線:
        連線.execute(
            "UPDATE endpoint_invocations SET usage_json="
            "'{\"x\":\"' || printf('%.*c',1048576,'x') || '\"}' WHERE id='i2'"
        )
    原始 = 查詢投影._建立連線
    payload查詢 = []
    payload讀取 = []
    class 記錄游標:
        def __init__(self, 游標, 是payload): self._游標, self._是payload = 游標, 是payload
        def fetchone(self):
            if self._是payload: payload讀取.append(1)
            return self._游標.fetchone()
        def __getattr__(self, 名稱): return getattr(self._游標, 名稱)
    class 記錄連線:
        def __init__(self, 連線): self._連線 = 連線
        def execute(self, SQL, *參數):
            是payload = ",usage_json FROM endpoint_invocations" in SQL
            if 是payload: payload查詢.append(SQL)
            return 記錄游標(self._連線.execute(SQL, *參數), 是payload)
        def __getattr__(self, 名稱): return getattr(self._連線, 名稱)
    monkeypatch.setattr(查詢投影, "_建立連線", lambda *參數, **關鍵字: 記錄連線(原始(*參數, **關鍵字)))
    with pytest.raises(端點觀測查詢錯誤) as 捕捉:
        _服務(指標資料庫).讀取端點指標(
            擁有者使用者識別碼="owner-1", 是否管理者=False, 端點識別碼="ep-1", 視窗秒數=50)
    assert 捕捉.value.args == ("端點觀測不可取得",)
    assert payload查詢 == payload讀取 == []
