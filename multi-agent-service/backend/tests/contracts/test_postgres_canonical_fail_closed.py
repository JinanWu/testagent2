"""Canonical explicit/environment factories 的 PostgreSQL fail-closed gate。"""

from dataclasses import replace

import pytest

from 繁中代理.環境設定 import 讀取交易儲存設定
from 繁中代理.發布介面 import asgi as asgi模組
from 繁中代理.發布介面 import 生產組裝 as 組裝模組
from 繁中代理.發布介面.設定 import 生產設定


_連線名稱 = "lab-cola-rd:asia-east1:testagent2-postgres-lab"
_DSN = f"postgresql://alice:canonical-secret@/app?host=/cloudsql/{_連線名稱}"


def _postgres設定(tmp_path):
    基礎 = 生產設定(tmp_path / "web.sqlite3", ("https://client.example",), "fake", "fake")
    return replace(基礎, 交易儲存=讀取交易儲存設定({
        "STORAGE_BACKEND": "postgres", "DATABASE_URL": _DSN,
        "CLOUD_SQL_INSTANCE_CONNECTION_NAME": _連線名稱,
    }))


def test_explicit_ASGI_factory在任何SQLite與builder前拒絕(tmp_path, monkeypatch):
    設定 = _postgres設定(tmp_path)
    monkeypatch.setattr(組裝模組, "網頁工作階段服務", lambda *_a, **_k: pytest.fail("不得建立 SQLite web session authority"))
    monkeypatch.setattr(組裝模組, "建立SQLite帳密驗證器", lambda *_: pytest.fail("不得建立 SQLite auth authority"))

    class 不可呼叫建構器:
        def 建立附加相依項(self, *_):
            pytest.fail("不得呼叫 provider/bundle/local skill builder")

    with pytest.raises(RuntimeError, match="^PostgreSQL 儲存後端尚未接線$") as 捕捉:
        組裝模組.建立生產相依項(設定, 不可呼叫建構器())
    assert "canonical-secret" not in str(捕捉.value)
    assert not 設定.資料庫路徑.exists()


@pytest.mark.parametrize("工廠", (asgi模組.建立ASGI應用程式,))
def test_explicit_public_factory同樣拒絕(tmp_path, 工廠):
    with pytest.raises(RuntimeError, match="^PostgreSQL 儲存後端尚未接線$"):
        工廠(_postgres設定(tmp_path))


def test_canonical環境parser先辨識postgres再拒絕_不要求SQLite_JSON或provider欄位(tmp_path, monkeypatch):
    環境 = {
        "STORAGE_BACKEND": "postgres", "DATABASE_URL": _DSN,
        "CLOUD_SQL_INSTANCE_CONNECTION_NAME": _連線名稱,
    }
    def 不可觸及(*_a, **_k):
        pytest.fail("global readiness gate 後不得觸及 local/JSON/bundle/provider authority")
    for 名稱 in ("Path", "解析嚴格JSON", "使用者庫", "GeminiADC供應商", "安裝生產技能工具"):
        monkeypatch.setattr(asgi模組, 名稱, 不可觸及)
    with pytest.raises(RuntimeError, match="^PostgreSQL 儲存後端尚未接線$") as 捕捉:
        asgi模組.解析Canonical環境設定(環境)
    assert "canonical-secret" not in str(捕捉.value)
    assert tuple(tmp_path.iterdir()) == ()


def test_canonical_postgres環境名稱已納入exact_allowlist且畸形DSN不洩密():
    秘密 = "bad-canonical-secret"
    with pytest.raises(ValueError) as 捕捉:
        asgi模組.解析Canonical環境設定({
            "STORAGE_BACKEND": "postgres",
            "DATABASE_URL": f"mysql://alice:{秘密}@/app?host=/cloudsql/{_連線名稱}",
            "CLOUD_SQL_INSTANCE_CONNECTION_NAME": "lab-cola-rd:asia-east1:testagent2-postgres-lab",
        })
    assert 秘密 not in str(捕捉.value)


def test_canonical未知PostgreSQL設定名稱固定拒絕():
    環境 = {
        "STORAGE_BACKEND": "postgres",
        "DATABASE_URL": _DSN,
        "CLOUD_SQL_INSTANCE_CONNECTION_NAME": _連線名稱,
        "POSTGRES_POOL_MAX_SZE": "9",
    }
    with pytest.raises(ValueError, match="^Canonical環境設定無效$"):
        asgi模組.解析Canonical環境設定(環境)
