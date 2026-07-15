"""測試 BigQuery 技能庫 skill_usage 單列 MERGE 讀寫（mock client）。"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from 繁中代理.BigQuery技能庫 import BigQuery技能庫, 技能使用量儲存錯誤
from 繁中代理.工具集 import 技能使用量


class 假查詢工作:
    """保存 fake 查詢結果。"""

    def __init__(self, 列清單=None):
        self.列清單 = 列清單 or []

    def result(self):
        return self.列清單


class 假BigQuery客戶端:
    """攔截 SQL 的 fake BigQuery client。"""

    def __init__(self, 查詢結果=None, 查詢錯誤: Exception | None = None):
        self.sql清單: list[str] = []
        self.查詢結果 = 查詢結果 or []
        self.查詢錯誤 = 查詢錯誤

    def query(self, sql, job_config=None):
        if self.查詢錯誤 is not None:
            raise self.查詢錯誤
        self.sql清單.append(sql)
        return 假查詢工作(self.查詢結果)


@pytest.fixture
def 假技能庫(monkeypatch):
    """建立跳過 DDL 的 BigQuery技能庫，並注入假 client。"""
    monkeypatch.setenv("STORAGE_BACKEND", "bigquery")
    monkeypatch.setenv("CORE_BQ_SKIP_DDL", "1")
    monkeypatch.setenv("CORE_BQ_PROJECT", "test-project")
    monkeypatch.setenv("CORE_BQ_DATASET", "test_dataset")
    with patch.object(BigQuery技能庫, "確保資料表", lambda self: None):
        庫 = BigQuery技能庫()
    return 庫


def test_讀取全部使用量失敗時拋出錯誤而非空dict(假技能庫):
    假技能庫._已建立用戶端 = 假BigQuery客戶端(查詢錯誤=RuntimeError("bq down"))
    with pytest.raises(技能使用量儲存錯誤):
        假技能庫.讀取全部使用量()


def test_覆寫使用量列使用merge而非truncate(假技能庫):
    假技能庫._已建立用戶端 = 假BigQuery客戶端()
    假技能庫.覆寫使用量列("sid-1", {
        "user_id": "alice",
        "use_count": 2,
        "last_used_at": "2026-07-13T00:00:00+00:00",
        "state": "active",
        "pinned": False,
        "created_at": "2026-07-12T00:00:00+00:00",
    })
    assert len(假技能庫.用戶端.sql清單) == 1
    sql = 假技能庫.用戶端.sql清單[0].upper()
    assert "MERGE" in sql
    assert "TRUNCATE" not in sql
    assert "WHEN MATCHED" in sql
    assert "WHEN NOT MATCHED" in sql


def test_刪除使用量列執行delete(假技能庫):
    假技能庫._已建立用戶端 = 假BigQuery客戶端()
    假技能庫.刪除使用量列("sid-1")
    assert "DELETE" in 假技能庫.用戶端.sql清單[0].upper()
    assert "skill_id=@sid" in 假技能庫.用戶端.sql清單[0]


def test_寫入全部使用量改為逐列merge(假技能庫):
    假技能庫._已建立用戶端 = 假BigQuery客戶端()
    假技能庫.寫入全部使用量({
        "sid-1": {"user_id": None, "use_count": 1, "last_used_at": None, "state": "active", "pinned": False, "created_at": "t1"},
        "sid-2": {"user_id": None, "use_count": 0, "last_used_at": None, "state": "active", "pinned": True, "created_at": "t2"},
    })
    assert len(假技能庫.用戶端.sql清單) == 2
    assert all("MERGE" in s.upper() for s in 假技能庫.用戶端.sql清單)
    assert all("TRUNCATE" not in s.upper() for s in 假技能庫.用戶端.sql清單)


def test_bigquery變更使用單列讀寫(monkeypatch, 假技能庫):
    """_變更 在 BQ 模式應呼叫 讀取使用量列 + 覆寫使用量列，不走整表寫入。"""
    讀取紀錄: list[str] = []
    寫入紀錄: list[str] = []

    def 假讀取(skill_id):
        讀取紀錄.append(skill_id)
        return None

    def 假覆寫(skill_id, 記錄):
        寫入紀錄.append(skill_id)

    monkeypatch.setattr(技能使用量, "_取得BigQuery技能庫", lambda: 假技能庫)
    monkeypatch.setattr(假技能庫, "讀取使用量列", 假讀取)
    monkeypatch.setattr(假技能庫, "覆寫使用量列", 假覆寫)

    技能使用量.設定pin("sid-pin", True)
    assert 讀取紀錄 == ["sid-pin"]
    assert 寫入紀錄 == ["sid-pin"]


def test_bigquery變更讀取失敗不寫入(monkeypatch, 假技能庫):
    寫入紀錄: list[str] = []

    def 假讀取(skill_id):
        raise 技能使用量儲存錯誤("read failed")

    def 假覆寫(skill_id, 記錄):
        寫入紀錄.append(skill_id)

    monkeypatch.setattr(技能使用量, "_取得BigQuery技能庫", lambda: 假技能庫)
    monkeypatch.setattr(假技能庫, "讀取使用量列", 假讀取)
    monkeypatch.setattr(假技能庫, "覆寫使用量列", 假覆寫)

    技能使用量.設定pin("sid-fail", True)
    assert 寫入紀錄 == []


def test_bigquery遺忘使用刪除列(monkeypatch, 假技能庫):
    刪除紀錄: list[str] = []

    def 假刪除(skill_id):
        刪除紀錄.append(skill_id)

    monkeypatch.setattr(技能使用量, "_取得BigQuery技能庫", lambda: 假技能庫)
    monkeypatch.setattr(假技能庫, "刪除使用量列", 假刪除)

    技能使用量.遺忘("sid-del")
    assert 刪除紀錄 == ["sid-del"]


def test_bigquery補齊只讀寫雲端技能庫(monkeypatch):
    """補齊時從 BQ 技能表找缺漏，逐列寫入 BQ，不碰本機技能目錄。"""
    class 假雲端技能庫:
        def __init__(self):
            self.寫入紀錄 = []

        def 讀取全部使用量(self):
            return {"sid-old": {"use_count": 3}}

        def 列出技能身分(self, user_id=None, 是否包含封存=False, 限定使用者=False):
            assert 是否包含封存 is True
            return [
                {"skill_id": "sid-old", "user_id": "alice"},
                {"skill_id": "sid-new", "user_id": "bob"},
            ]

        def 覆寫使用量列(self, skill_id, 記錄):
            self.寫入紀錄.append((skill_id, 記錄))

    技能庫 = 假雲端技能庫()
    monkeypatch.setattr(技能使用量, "_取得BigQuery技能庫", lambda: 技能庫)

    assert 技能使用量.補齊缺少的記錄() == 1
    assert len(技能庫.寫入紀錄) == 1
    skill_id, 記錄 = 技能庫.寫入紀錄[0]
    assert skill_id == "sid-new"
    assert 記錄["user_id"] == "bob"
    assert 記錄["use_count"] == 0
    assert 記錄["state"] == "active"
    assert 記錄["created_at"]
