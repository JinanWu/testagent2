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
    基礎 = 生產設定(tmp_path / "web.sqlite3", ("https://client.example",), "gemini-adc", "gemini-2.5-flash", "test-project", "us-central1")
    return replace(基礎, 資料庫路徑=None, 交易儲存=讀取交易儲存設定({
        "STORAGE_BACKEND": "postgres", "DATABASE_URL": _DSN,
        "CLOUD_SQL_INSTANCE_CONNECTION_NAME": _連線名稱,
    }))


def test_explicit_ASGI_factory在任何SQLite與builder前成功組裝(tmp_path, monkeypatch):
    設定 = _postgres設定(tmp_path)
    monkeypatch.setattr(組裝模組, "網頁工作階段服務", lambda *_a, **_k: pytest.fail("不得建立 SQLite web session authority"))
    monkeypatch.setattr(組裝模組, "建立SQLite帳密驗證器", lambda *_: pytest.fail("不得建立 SQLite auth authority"))

    class 不可呼叫建構器:
        def 建立附加相依項(self, *_):
            pytest.fail("不得呼叫 provider/bundle/local skill builder")

    結果 = 組裝模組.建立生產相依項(設定, None)
    assert 結果.路由器清單 and len(結果.資源工廠清單) >= 1
    assert 設定.資料庫路徑 is None


@pytest.mark.parametrize("工廠", (asgi模組.建立ASGI應用程式,))
def test_explicit_public_factory同樣成功(tmp_path, 工廠):
    assert 工廠(_postgres設定(tmp_path)).router.routes


def test_canonical環境parser先辨識postgres且要求明示production_authorities(tmp_path, monkeypatch):
    環境 = {
        "STORAGE_BACKEND": "postgres", "DATABASE_URL": _DSN,
        "CLOUD_SQL_INSTANCE_CONNECTION_NAME": _連線名稱,
        "TESTAGENT2_MODEL_PROVIDER": "gemini-adc", "TESTAGENT2_MODEL_NAME": "gemini-2.5-flash",
        "AIAGENT_GCP_PROJECT": "test-project", "AIAGENT_GCP_LOCATION": "us-central1",
        "TESTAGENT2_PUBLISHED_CREDENTIAL_ACTIVE_KEY_VERSION": "1",
        "TESTAGENT2_PUBLISHED_CREDENTIAL_KEYS_JSON": '{"1":"AQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQE"}',
        "TESTAGENT2_OWNER_OBSERVABILITY_CURSOR_KEY": "AgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgI",
        "TESTAGENT2_PUBLISHED_BUNDLE_BUCKET": "testagent2-contract-bundles",
        "TESTAGENT2_WEB_ORIGINS": '["https://client.example"]',
    }
    設定, 發布 = asgi模組.解析Canonical環境設定(環境)
    assert 設定.交易儲存.後端 == "postgres" and 設定.資料庫路徑 is None
    assert 發布.發布資料庫路徑 is None and 發布.技能套件發布根 is None
    assert 發布.CloudStorageBucket名稱 == "testagent2-contract-bundles"
    assert tuple(tmp_path.iterdir()) == ()


def test_canonical_postgres缺少CloudStorage_bundle_authority_fail_closed():
    環境 = {
        "STORAGE_BACKEND": "postgres", "DATABASE_URL": _DSN,
        "CLOUD_SQL_INSTANCE_CONNECTION_NAME": _連線名稱,
        "TESTAGENT2_MODEL_PROVIDER": "gemini-adc", "TESTAGENT2_MODEL_NAME": "gemini-2.5-flash",
        "AIAGENT_GCP_PROJECT": "test-project", "AIAGENT_GCP_LOCATION": "us-central1",
        "TESTAGENT2_PUBLISHED_CREDENTIAL_ACTIVE_KEY_VERSION": "1",
        "TESTAGENT2_PUBLISHED_CREDENTIAL_KEYS_JSON": '{"1":"AQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQE"}',
        "TESTAGENT2_OWNER_OBSERVABILITY_CURSOR_KEY": "AgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgI",
        "TESTAGENT2_WEB_ORIGINS": '["https://client.example"]',
    }
    with pytest.raises(ValueError, match="^Canonical環境設定無效$"):
        asgi模組.解析Canonical環境設定(環境)


def test_canonical_postgres缺少credential_owner與Gemini_authority_fail_closed():
    with pytest.raises(ValueError, match="^Canonical環境設定無效$"):
        asgi模組.解析Canonical環境設定({
            "STORAGE_BACKEND": "postgres", "DATABASE_URL": _DSN,
            "CLOUD_SQL_INSTANCE_CONNECTION_NAME": _連線名稱,
            "TESTAGENT2_WEB_ORIGINS": '["https://client.example"]',
        })


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
