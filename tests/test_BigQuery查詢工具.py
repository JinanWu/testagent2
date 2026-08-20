"""測試通用 BigQuery 唯讀查詢工具（bigquery_query）的護欄與分派。"""

from __future__ import annotations

import pytest

from 繁中代理.工具集.BigQuery查詢 import (
    BigQuery查詢工具,
    BigQuery查詢錯誤,
    正規化資料集項目,
    執行查詢,
    描述資料表,
    列出可查資料集,
    列出資料表,
    解析資料集參數,
    讀取BigQuery查詢設定,
    轉成可序列化,
    驗證查詢,
)


class 假資料表參照:
    """對應 BigQuery TableReference 的最小欄位。"""

    def __init__(self, 專案: str, 資料集: str, 資料表: str):
        self.project = 專案
        self.dataset_id = 資料集
        self.table_id = 資料表


class 假查詢工作:
    """同時充當 dry run 工作與正式查詢工作。"""

    def __init__(self, 語句型別="SELECT", 引用資料表=None, 掃描位元組=0, 列清單=None, 欄位清單=None):
        self.statement_type = 語句型別
        self.referenced_tables = 引用資料表 or []
        self.total_bytes_processed = 掃描位元組
        self._列清單 = 列清單 or []
        self._欄位清單 = 欄位清單 or []

    def result(self, timeout=None):
        del timeout
        return 假查詢結果(self._列清單, self._欄位清單)


class 假欄位:
    """對應 BigQuery SchemaField 的最小欄位。"""

    def __init__(self, 名稱: str, 型別: str = "STRING", 模式: str = "NULLABLE"):
        self.name = 名稱
        self.field_type = 型別
        self.mode = 模式


class 假結果列:
    """支援 .get(欄位) 的結果列。"""

    def __init__(self, 資料: dict):
        self._資料 = 資料

    def get(self, 欄位):
        return self._資料.get(欄位)


class 假查詢結果:
    """可迭代並帶 schema 的查詢結果。"""

    def __init__(self, 列清單, 欄位清單):
        self._列清單 = [假結果列(列) for 列 in 列清單]
        self.schema = [假欄位(欄位) for 欄位 in 欄位清單]

    def __iter__(self):
        return iter(self._列清單)


class 假BigQuery客戶端:
    """依序回傳預先安排好的查詢工作。"""

    def __init__(self, 工作清單=None, 資料表清單=None, 表物件=None):
        self.工作清單 = list(工作清單 or [])
        self.sql清單: list[str] = []
        self.資料表清單 = 資料表清單 or []
        self.表物件 = 表物件

    def query(self, sql, job_config=None):
        self.sql清單.append(sql)
        return self.工作清單.pop(0)

    def list_tables(self, 完整資料集名稱):
        self.最後列表對象 = 完整資料集名稱
        return self.資料表清單

    def get_table(self, 完整資料表名稱):
        self.最後取表對象 = 完整資料表名稱
        return self.表物件


@pytest.fixture
def 設定(monkeypatch):
    """建立只允許 test-project.economy 的工具設定。"""
    monkeypatch.setenv("BQTOOL_PROJECT", "test-project")
    monkeypatch.setenv("BQTOOL_ALLOWED_DATASETS", "economy")
    monkeypatch.setenv("BQTOOL_MAX_BYTES", "1000")
    monkeypatch.setenv("BQTOOL_MAX_ROWS", "2")
    monkeypatch.delenv("BQTOOL_LOCATION", raising=False)
    monkeypatch.delenv("BQTOOL_TIMEOUT", raising=False)
    return 讀取BigQuery查詢設定()


def test_缺少專案時明確報錯(monkeypatch):
    monkeypatch.setenv("BQTOOL_PROJECT", "")
    monkeypatch.setenv("BQTOOL_ALLOWED_DATASETS", "")
    with pytest.raises(ValueError) as 錯誤:
        讀取BigQuery查詢設定()
    assert "BQTOOL_PROJECT" in str(錯誤.value)


def test_未設allowlist時預設為本專案全部但仍擋跨專案(monkeypatch):
    monkeypatch.setenv("BQTOOL_PROJECT", "test-project")
    monkeypatch.delenv("BQTOOL_ALLOWED_DATASETS", raising=False)
    設定物件 = 讀取BigQuery查詢設定()
    assert 設定物件.允許資料集 == ("test-project.*",)
    assert 設定物件.是否允許資料集("test-project", "economy") is True
    assert 設定物件.是否允許資料集("cola-rd-test", "economy") is False


def test_資料集項目未帶專案時補上預設專案():
    assert 正規化資料集項目("economy", "test-project") == "test-project.economy"
    assert 正規化資料集項目("其他專案".replace("其他專案", "other-proj") + ".economy", "test-project") == "other-proj.economy"


def test_資料集項目格式錯誤時拋出():
    with pytest.raises(ValueError):
        正規化資料集項目("a.b.c", "test-project")


def test_allowlist外的資料集被擋下(設定):
    with pytest.raises(BigQuery查詢錯誤) as 錯誤:
        解析資料集參數({"dataset": "secrets"}, 設定)
    assert "不在允許範圍" in str(錯誤.value)


def test_list_datasets不需連線BigQuery(設定, monkeypatch):
    def 不該被呼叫(_設定):
        raise AssertionError("list_datasets 不應建立 BigQuery client")

    monkeypatch.setattr("繁中代理.工具集.BigQuery查詢.建立BigQuery客戶端", 不該被呼叫)
    結果 = BigQuery查詢工具({"action": "list_datasets"})
    assert 結果["datasets"] == ["test-project.economy"]


def test_非SELECT語句被dry_run擋下(設定):
    客戶端 = 假BigQuery客戶端([假查詢工作(語句型別="DELETE")])
    with pytest.raises(BigQuery查詢錯誤) as 錯誤:
        驗證查詢(客戶端, "DELETE FROM `test-project.economy.t`", 設定)
    assert "只允許唯讀 SELECT" in str(錯誤.value)


def test_引用allowlist外資料表被擋下(設定):
    工作 = 假查詢工作(引用資料表=[假資料表參照("test-project", "secrets", "t")])
    客戶端 = 假BigQuery客戶端([工作])
    with pytest.raises(BigQuery查詢錯誤) as 錯誤:
        驗證查詢(客戶端, "SELECT * FROM `test-project.secrets.t`", 設定)
    assert "允許範圍外" in str(錯誤.value)


def test_掃描量超過上限時在正式查詢前擋下(設定):
    工作 = 假查詢工作(
        引用資料表=[假資料表參照("test-project", "economy", "t")],
        掃描位元組=5000,
    )
    客戶端 = 假BigQuery客戶端([工作])
    with pytest.raises(BigQuery查詢錯誤) as 錯誤:
        驗證查詢(客戶端, "SELECT * FROM `test-project.economy.t`", 設定)
    assert "超過上限" in str(錯誤.value)
    assert len(客戶端.工作清單) == 0  # 只跑了 dry run，沒送出正式查詢


def test_合法查詢回傳結果並附帶掃描量(設定):
    試跑 = 假查詢工作(引用資料表=[假資料表參照("test-project", "economy", "t")], 掃描位元組=100)
    正式 = 假查詢工作(列清單=[{"航點": "TPE-SIN"}], 欄位清單=["航點"])
    客戶端 = 假BigQuery客戶端([試跑, 正式])
    結果 = 執行查詢(客戶端, {"sql": "SELECT `航點` FROM `test-project.economy.t`"}, 設定)
    assert 結果["rows"] == [{"航點": "TPE-SIN"}]
    assert 結果["bytes_processed"] == 100
    assert 結果["truncated"] is False
    assert 結果["referenced_tables"] == ["test-project.economy.t"]


def test_超過列數上限時截斷並標記(設定):
    試跑 = 假查詢工作(引用資料表=[假資料表參照("test-project", "economy", "t")])
    正式 = 假查詢工作(列清單=[{"a": 1}, {"a": 2}, {"a": 3}], 欄位清單=["a"])
    客戶端 = 假BigQuery客戶端([試跑, 正式])
    結果 = 執行查詢(客戶端, {"sql": "SELECT a FROM `test-project.economy.t`"}, 設定)
    assert 結果["row_count"] == 2  # BQTOOL_MAX_ROWS=2
    assert 結果["truncated"] is True


def test_max_rows不得超過設定上限(設定):
    試跑 = 假查詢工作(引用資料表=[假資料表參照("test-project", "economy", "t")])
    正式 = 假查詢工作(列清單=[{"a": 1}, {"a": 2}, {"a": 3}], 欄位清單=["a"])
    客戶端 = 假BigQuery客戶端([試跑, 正式])
    結果 = 執行查詢(客戶端, {"sql": "SELECT a FROM `t`", "max_rows": 999}, 設定)
    assert 結果["max_rows"] == 2


def test_list_tables支援名稱前綴過濾(設定):
    class 假表項目:
        def __init__(self, 名稱):
            self.table_id = 名稱

    客戶端 = 假BigQuery客戶端(
        資料表清單=[假表項目("New_Eztravel"), 假表項目("New_Other"), 假表項目("Old_X")]
    )
    結果 = 列出資料表(客戶端, {"dataset": "economy", "name_prefix": "New_"}, 設定)
    assert 結果["tables"] == ["New_Eztravel", "New_Other"]
    assert 客戶端.最後列表對象 == "test-project.economy"


def test_describe_table回傳欄位名稱與型別(設定):
    class 假表物件:
        num_rows = 42
        schema = [假欄位("去程日期", "STRING"), 假欄位("票面價格", "INTEGER")]

    客戶端 = 假BigQuery客戶端(表物件=假表物件())
    結果 = 描述資料表(客戶端, {"dataset": "economy", "table": "New_X"}, 設定)
    assert 結果["num_rows"] == 42
    assert 結果["columns"][0] == {"name": "去程日期", "type": "STRING", "mode": "NULLABLE"}


def test_萬用字元允許同專案任一資料集但擋下跨專案(monkeypatch):
    monkeypatch.setenv("BQTOOL_PROJECT", "test-project")
    monkeypatch.setenv("BQTOOL_ALLOWED_DATASETS", "*")
    萬用設定 = 讀取BigQuery查詢設定()
    assert 萬用設定.是否允許資料集("test-project", "economy") is True
    assert 萬用設定.是否允許資料集("test-project", "任何新資料集".replace("任何新資料集", "anything")) is True
    assert 萬用設定.是否允許資料集("other-project", "economy") is False


def test_萬用字元的list_datasets實際展開(monkeypatch):
    monkeypatch.setenv("BQTOOL_PROJECT", "test-project")
    monkeypatch.setenv("BQTOOL_ALLOWED_DATASETS", "*")
    萬用設定 = 讀取BigQuery查詢設定()

    class 假資料集項目:
        def __init__(self, 名稱):
            self.dataset_id = 名稱

    class 假列表客戶端:
        def list_datasets(self, 專案):
            assert 專案 == "test-project"
            return [假資料集項目("economy"), 假資料集項目("marketing")]

    結果 = 列出可查資料集(萬用設定, 假列表客戶端())
    assert 結果["datasets"] == ["test-project.economy", "test-project.marketing"]


def test_未知action直接拒絕(設定):
    with pytest.raises(BigQuery查詢錯誤):
        BigQuery查詢工具({"action": "drop_table"})


def test_轉成可序列化處理日期與巢狀結構():
    import datetime

    assert 轉成可序列化(datetime.date(2026, 11, 23)) == "2026-11-23"
    assert 轉成可序列化([{"d": datetime.date(2026, 1, 1)}]) == [{"d": "2026-01-01"}]
