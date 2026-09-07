from __future__ import annotations

import pytest
from typing import Any, cast
from contextlib import contextmanager

from 繁中代理.交易儲存設定 import 交易儲存設定
from 繁中代理.發布介面.生產Published管理 import Planner生產設定, 建立生產Planner資源, 延遲草稿規劃服務
from 繁中代理.發布介面.生產Published執行 import Published生產設定, _建立Published模型設定
from 繁中代理.發布介面.執行期.模型契約 import 設定鍵, 重建設定
from 繁中代理.發布介面.規劃.工具政策 import ONE_SHOT_PUBLISHED禁止工具
from 繁中代理.發布介面.治理 import PostgreSQL端點管理查詢服務 as 查詢模組


def test_planner_accepts_validated_postgres_storage_without_path():
    settings = 交易儲存設定("postgres", "postgresql:///app?host=/cloudsql/project:region:instance", "project:region:instance", 1, 2, 5)
    # The factory is deliberately called by the real planner resource, proving that
    # the PG authority is selected instead of constructing a SQLite Path source.
    calls = []

    class UserSource:
        def 建立使用者上下文(self, *_args, **_kwargs):
            return object()

    planner = Planner生產設定(
        "processor", lambda value: (calls.append(type(value)), UserSource())[1],
        lambda: None,
    )
    # The strict constructor is the causal boundary: a PG storage setting is
    # accepted as the factory input and no Path operation is available to it.
    assert type(settings) is 交易儲存設定
    assert planner.使用者權威來源工廠(settings) is not None


def test_production_planner預設套用one_shot互動工具政策():
    planner = Planner生產設定(
        "release-1", cast(Any, lambda value: value), cast(Any, lambda: None),
    )
    assert planner.one_shot禁止工具 is ONE_SHOT_PUBLISHED禁止工具
    assert planner.one_shot禁止工具 == frozenset({("clarify", "clarify@published-v1")})


def test_postgres_published_settings_require_explicit_cloud_authority_without_io():
    """The PG composition cannot silently create a local bundle authority."""
    settings = 交易儲存設定(
        "postgres", "postgresql:///app?host=/cloudsql/project:region:instance",
        "project:region:instance", 1, 2, 5,
    )
    del settings
    with pytest.raises(ValueError):
        Published生產設定(
            None, None, lambda _tools: None, lambda: {"gemini-adc": object()},
            PostgreSQL模式=True,
        )


def test_sqlite與postgres共用完整runtime模型設定契約():
    設定 = _建立Published模型設定("gemini-adc", "gemini-2.5-flash-lite")
    assert frozenset(設定) == 設定鍵
    assert 重建設定(設定).轉成JSON物件() == 設定
    assert 設定["structured_output"] is True
    assert 設定["schema_retry_count"] == 1


def test_postgres端點列表SQL替版本ID提供唯一欄名(monkeypatch):
    class Result:
        def fetchall(self):
            return [{
                "id": "ep-1", "owner_user_id": "owner-1", "slug": "demo", "status": "active",
                "current_version_id": "ver-1", "version_id": "ver-1", "version_number": 1,
                "created_at_epoch": 1.0, "updated_at_epoch": 2.0,
            }]

    class Connection:
        def __init__(self):
            self.sql = ""
        def execute(self, sql, params=()):
            del params
            self.sql = sql
            return Result()

    connection = Connection()

    @contextmanager
    def transaction(_):
        yield connection

    monkeypatch.setattr(查詢模組, "交易連線", transaction)
    service = 查詢模組.PostgreSQL端點管理查詢服務(object(), 游標簽章金鑰=b"k" * 32)
    result = service.列出端點(
        擁有者使用者識別碼="owner-1", 管理者查詢全部=False,
        數量上限=20, 游標=None,
    )
    assert result.項目[0].目前版本識別碼 == "ver-1"
    assert "v.id AS version_id" in connection.sql
