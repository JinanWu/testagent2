"""PostgreSQL 交易儲存設定的 fail-closed、正規化與秘密遮蔽測試。"""

from dataclasses import FrozenInstanceError
from typing import Any, cast

import pytest

from 繁中代理.環境設定 import 交易儲存設定, 讀取交易儲存設定


_連線名稱 = "lab-cola-rd:asia-east1:testagent2-postgres-lab"
_Socket路徑 = f"/cloudsql/{_連線名稱}"
_DSN = f"postgresql://alice:***@/app?host={_Socket路徑}"


def _postgres環境(**額外: str) -> dict[str, str]:
    return {
        "STORAGE_BACKEND": "postgres",
        "DATABASE_URL": _DSN,
        "CLOUD_SQL_INSTANCE_CONNECTION_NAME": _連線名稱,
        **額外,
    }


@pytest.mark.parametrize(("環境", "預期後端"), (
    ({}, "sqlite"),
    ({"STORAGE_BACKEND": ""}, "sqlite"),
    ({"STORAGE_BACKEND": " SQLite "}, "sqlite"),
    ({"STORAGE_BACKEND": "BIGQUERY"}, "bigquery"),
    ({"STORAGE_BACKEND": "  BigQuery  "}, "bigquery"),
    ({**_postgres環境(), "STORAGE_BACKEND": " POSTGRES "}, "postgres"),
))
def test_STORAGE_BACKEND保留exact_base正規化(環境, 預期後端):
    assert 讀取交易儲存設定(環境).後端 == 預期後端


@pytest.mark.parametrize("後端", (" ", "\t", "unknown", 1, None, True))
def test_STORAGE_BACKEND_whitespace_only未知與非字串皆fail_closed(後端):
    with pytest.raises(ValueError, match="^交易儲存設定無效$"):
        讀取交易儲存設定({"STORAGE_BACKEND": cast(Any, 後端)})


@pytest.mark.parametrize("後端", ("sqlite", " SQLite ", "bigquery", " BIGQUERY "))
def test_SQLite與BigQuery忽略PostgreSQL秘密及ambient_PG設定(後端):
    設定 = 讀取交易儲存設定({
        "STORAGE_BACKEND": 後端,
        "DATABASE_URL": "not-a-dsn-with-secret",
        "CLOUD_SQL_INSTANCE_CONNECTION_NAME": "not/a/connection/name",
        "POSTGRES_POOL_MIN_SIZE": "invalid",
        "PGHOSTADDR": "attacker.example",
    })
    assert 設定.後端 == 後端.strip().lower()
    assert 設定.資料庫URL is None


def test_postgres缺少或畸形DSN皆fail_closed且不反射秘密():
    with pytest.raises(ValueError, match="DATABASE_URL"):
        讀取交易儲存設定({"STORAGE_BACKEND": "postgres"})
    秘密 = "super-secret-password"
    with pytest.raises(ValueError) as 捕捉:
        讀取交易儲存設定({
            "STORAGE_BACKEND": "postgres",
            "DATABASE_URL": f"mysql://alice:{秘密}@/app?host={_Socket路徑}",
            "CLOUD_SQL_INSTANCE_CONNECTION_NAME": _連線名稱,
        })
    assert 秘密 not in str(捕捉.value)
    assert 秘密 not in repr(捕捉.value)


@pytest.mark.parametrize("查詢", (
    f"hostaddr={_Socket路徑}",
    f"host={_Socket路徑}&port=5432",
    f"host={_Socket路徑}&service=production",
    f"host={_Socket路徑}&host={_Socket路徑}",
    f"host={_Socket路徑}&host=",
    "host=",
    "=unexpected",
    "host",
    f"host={_Socket路徑}&",
    f"host={_Socket路徑}&future=value",
))
def test_postgres_query_pairs必須exactly一組有效host(查詢):
    環境 = _postgres環境()
    環境["DATABASE_URL"] = f"postgresql://alice:***@/app?{查詢}"
    with pytest.raises(ValueError, match="^PostgreSQL 儲存設定無效$"):
        讀取交易儲存設定(環境)


@pytest.mark.parametrize("DSN", (
    f"postgresql://alice:***@:6543/app?host={_Socket路徑}",
    f"postgresql://alice:***@/app?host={_Socket路徑}#",
    f"postgresql://alice:***@/app?host={_Socket路徑}#host=evil",
))
def test_postgres拒絕authority_port與任何fragment_delimiter(DSN):
    環境 = _postgres環境()
    環境["DATABASE_URL"] = DSN
    with pytest.raises(ValueError, match="^PostgreSQL 儲存設定無效$") as 捕捉:
        讀取交易儲存設定(環境)
    assert "***" not in str(捕捉.value)


@pytest.mark.parametrize("名稱", (
    "PGHOSTADDR",
    "PGPORT",
    "PGSERVICE",
    "PGSERVICEFILE",
    "PGFUTURE_DEFAULT",
))
def test_postgres拒絕所有符合完整PG環境名稱regex的ambient設定(名稱):
    with pytest.raises(ValueError, match="^PostgreSQL 儲存設定無效$"):
        讀取交易儲存設定(_postgres環境(**{名稱: "ambient-value"}))


def test_postgres不因無關非PG環境名稱而拒絕():
    assert 讀取交易儲存設定(_postgres環境(APP_MODE="test")).後端 == "postgres"


def test_postgres設定完整不可變且repr不含DSN():
    秘密 = "super-secret-password"
    設定 = 讀取交易儲存設定({
        "STORAGE_BACKEND": "postgres",
        "DATABASE_URL": f"postgresql://alice:{秘密}@/app?host={_Socket路徑}",
        "CLOUD_SQL_INSTANCE_CONNECTION_NAME": _連線名稱,
        "POSTGRES_POOL_MIN_SIZE": "2",
        "POSTGRES_POOL_MAX_SIZE": "7",
        "POSTGRES_POOL_TIMEOUT_SECONDS": "11",
    })
    assert 設定 == 交易儲存設定(
        "postgres", f"postgresql://alice:{秘密}@/app?host={_Socket路徑}",
        _連線名稱, 2, 7, 11,
    )
    assert 秘密 not in repr(設定)
    assert 設定.資料庫URL is not None
    with pytest.raises(FrozenInstanceError):
        設定.後端 = "sqlite"


def test_postgres_explicit設定拒絕非整數pool等待秒數():
    with pytest.raises(ValueError, match="^PostgreSQL 儲存設定無效$"):
        交易儲存設定(
            "postgres",
            "postgresql://alice:***@/app?host=/cloudsql/p:r:i",
            "p:r:i",
            1,
            5,
            cast(Any, 1.5),
        )


def test_未知後端_連線名稱與pool邊界固定拒絕且不反射值():
    for 環境 in (
        {"STORAGE_BACKEND": "mystery", "DATABASE_URL": "postgresql://db/app"},
        {"STORAGE_BACKEND": "postgres", "DATABASE_URL": "postgresql://u:***@/app?host=/cloudsql/bad/name", "CLOUD_SQL_INSTANCE_CONNECTION_NAME": "bad/name"},
        {"STORAGE_BACKEND": "postgres", "DATABASE_URL": "postgresql://u:***@/app?host=/cloudsql/p:r:i", "CLOUD_SQL_INSTANCE_CONNECTION_NAME": "p:r:i", "POSTGRES_POOL_MIN_SIZE": "21"},
        {"STORAGE_BACKEND": "postgres", "DATABASE_URL": "postgresql://u:***@/app?host=/cloudsql/p:r:i", "CLOUD_SQL_INSTANCE_CONNECTION_NAME": "p:r:i", "POSTGRES_POOL_MIN_SIZE": "8", "POSTGRES_POOL_MAX_SIZE": "7"},
        {"STORAGE_BACKEND": "postgres", "DATABASE_URL": "postgresql://u:***@db.example/app", "CLOUD_SQL_INSTANCE_CONNECTION_NAME": "p:r:i"},
    ):
        with pytest.raises(ValueError) as 捕捉:
            讀取交易儲存設定(環境)
        assert all(str(值) not in str(捕捉.value) for 值 in 環境.values())
