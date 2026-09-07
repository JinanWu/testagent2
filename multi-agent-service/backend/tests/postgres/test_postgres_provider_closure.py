from __future__ import annotations

import pytest

from 繁中代理.交易儲存設定 import 交易儲存設定
from 繁中代理.發布介面.生產Published執行 import (
    Published生產設定, 生產Published執行資源, 延遲外部呼叫編排器,
)
from 繁中代理.發布介面.執行期.工具發布庫 import 工具發布庫


def _假的精確服務(類別):
    """建立不含外部資源的 exact instance，讓 closure test 只驗 proxy lease。"""
    return object.__new__(類別)


def test_pg_resource_owns_provider_bundle_publicly_not_private_adapter_registry():
    resource = 生產Published執行資源(
        延遲外部呼叫編排器(), object(), 工具發布庫(), {"gemini-adc": object()},
    )
    assert "_postgres_adapters" not in vars(resource)
    assert "_postgres_lazy_authorities" not in vars(resource)
    assert resource.PostgreSQL提供者 is None


def test_postgres_published_settings_reject_filesystem_bundle_fallback():
    try:
        Published生產設定(None, None, lambda _: None, lambda: {"x": object()}, PostgreSQL模式=True)
    except ValueError:
        pass
    else:
        raise AssertionError("PG mode must require an explicit Cloud Storage authority")


def test_all_lazy_provider_proxies_make_real_delegation_and_reject_structural_objects(monkeypatch):
    from 繁中代理.發布介面.生產Published管理 import (
        延遲草稿規劃服務, 延遲發布管理服務, 伺服器端草稿規劃服務,
        發布管理協調器,
    )
    from 繁中代理.發布介面.生產管理稽核 import 延遲管理稽核服務, 管理稽核提供者
    from 繁中代理.發布介面.生產Owner觀測 import 延遲Owner觀測服務
    from 繁中代理.發布介面.生產端點查詢 import 延遲端點管理查詢服務, SQLite端點管理查詢服務
    from 繁中代理.發布介面.生產端點文件 import 延遲端點文件服務, SQLite端點文件服務
    from 繁中代理.發布介面.治理.觀測供應器 import SQLite端點觀測查詢服務

    # Every call below crosses the lease boundary; the installed object is exact,
    # while a same-shaped object is deliberately refused.
    draft_service = _假的精確服務(伺服器端草稿規劃服務)
    monkeypatch.setattr(伺服器端草稿規劃服務, "建立草稿", lambda self, *a, **k: (a, k))
    draft = 延遲草稿規劃服務(); draft.安裝(draft_service)
    assert draft.建立草稿("u", "r", (), "text", 現在=1) == (("u", "r", (), "text"), {"現在": 1})
    draft.清除(draft_service)
    with pytest.raises(Exception): draft.建立草稿("u", "r", (), "text", 現在=1)

    manager_service = _假的精確服務(發布管理協調器)
    monkeypatch.setattr(發布管理協調器, "原子發布", lambda self, **k: k)
    manager = 延遲發布管理服務(); manager.安裝(manager_service)
    assert manager.原子發布(擁有者使用者識別碼="u", 確認="ok") == {"擁有者使用者識別碼": "u", "確認": "ok"}
    manager.清除(manager_service)

    cases = (
        (延遲管理稽核服務(), 管理稽核提供者, "列出管理員安全呼叫", ("q", "p")),
        (延遲Owner觀測服務(), SQLite端點觀測查詢服務, "讀取端點指標", ()),
        (延遲端點管理查詢服務(), SQLite端點管理查詢服務, "列出端點", ()),
        (延遲端點文件服務(), SQLite端點文件服務, "讀取管理文件", ()),
    )
    for proxy, service_type, method, args in cases:
        service = _假的精確服務(service_type)
        monkeypatch.setattr(service_type, method, lambda self, *a, **k: (method, a, k))
        if method == "列出管理員安全呼叫":
            monkeypatch.setattr(service_type, "查詢管理員原始資料", lambda self, *a, **k: ("detail", a, k))
        proxy.安裝(service)
        if method == "列出管理員安全呼叫":
            assert proxy.列出管理員安全呼叫(*args) == (method, args, {})
            assert proxy.查詢管理員原始資料(True, "e", "i") == ("detail", (True, "e", "i"), {})
        elif method == "讀取端點指標":
            assert proxy.讀取端點指標() == (method, (), {})
        elif method == "列出端點":
            assert proxy.列出端點() == (method, (), {})
        else:
            assert proxy.讀取管理文件() == (method, (), {})
        proxy.清除(service, 1)
        with pytest.raises(Exception): proxy.安裝(type("Hostile", (), {method: lambda self: None})())
