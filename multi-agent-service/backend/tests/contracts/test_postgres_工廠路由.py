"""核心工廠在 PostgreSQL adapter 未接線時不得回退。"""

import pytest

from 繁中代理 import 儲存 as 儲存模組


_POSTGRES_ENV = {
    "STORAGE_BACKEND": "postgres",
    "DATABASE_URL": "postgresql://alice:top-secret@/app?host=/cloudsql/p:r:i",
    "CLOUD_SQL_INSTANCE_CONNECTION_NAME": "p:r:i",
}


def test_取得儲存後端使用中央交易設定(monkeypatch):
    monkeypatch.setattr(儲存模組, "讀取交易儲存設定", lambda: type("設定", (), {"後端": "postgres"})())
    assert 儲存模組.取得儲存後端() == "postgres"


@pytest.mark.parametrize("工廠名稱", ("建立工作階段庫", "建立使用者庫"))
def test_postgres核心工廠回傳PostgreSQL且不建立SQLite或BigQuery(tmp_path, monkeypatch, 工廠名稱):
    monkeypatch.setattr(儲存模組, "讀取交易儲存設定", lambda: __import__("繁中代理.環境設定", fromlist=["讀取交易儲存設定"]).讀取交易儲存設定(_POSTGRES_ENV))
    monkeypatch.setattr(儲存模組, "工作階段庫", lambda *_: pytest.fail("不得建立 SQLite 工作階段庫"))
    monkeypatch.setattr(儲存模組, "使用者庫", lambda *_: pytest.fail("不得建立 SQLite 使用者庫"))
    結果 = getattr(儲存模組, 工廠名稱)(tmp_path / "must-not-exist.sqlite3")
    assert type(結果).__name__.startswith("PostgreSQL")
    assert not (tmp_path / "must-not-exist.sqlite3").exists()
