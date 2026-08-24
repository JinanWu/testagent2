"""Owner endpoint list/detail production adapter 與 canonical wiring 測試。"""

from __future__ import annotations

import hashlib
import sqlite3
import threading
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from 繁中代理.發布介面.資料庫 import 初始化發布介面資料庫
from 繁中代理.發布介面.路由.端點查詢 import 建立端點查詢路由器
from 繁中代理.發布介面.網頁工作階段 import 網頁使用者
from 繁中代理.發布介面.設定 import 生產設定
from 繁中代理.發布介面.生產Published執行 import Published生產設定, 生產Published執行建構器
from 繁中代理.發布介面.生產組裝 import 建立生產應用程式
from 繁中代理.發布介面.生產端點查詢 import (
    SQLite端點管理查詢服務,
    延遲端點管理查詢服務,
    建立端點管理身份相依,
)


_KEY = hashlib.sha256(b"owner-observability-deployment-key").digest()


def _資料庫(tmp_path: Path) -> Path:
    path = tmp_path / "published.db"
    初始化發布介面資料庫(path)
    with sqlite3.connect(path) as connection:
        connection.executemany(
            "INSERT INTO service_accounts(id,created_at) VALUES(?,0)",
            (("sa-a1",), ("sa-a2",), ("sa-b1",), ("sa-null",)),
        )
        connection.executemany(
            "INSERT INTO published_endpoints(id,owner_user_id,service_account_id,slug,status,current_version_id,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)",
            (
                ("e-a1", "owner-a", "sa-a1", "alpha", "active", None, 1, 30),
                ("e-a2", "owner-a", "sa-a2", "beta", "disabled", None, 2, 30),
                ("e-b1", "owner-b", "sa-b1", "gamma", "archived", None, 3, 40),
                ("e-null", "owner-a", "sa-null", "null-current", "active", None, 4, 20),
            ),
        )
        connection.executemany(
            "INSERT INTO published_endpoint_versions(id,endpoint_id,version_number,original_requirement_text,system_prompt,allowed_skills_json,allowed_tools_json,tool_schema_snapshot_json,tool_runtime_revision,model_config_snapshot_json,retry_policy_json,skill_bundle_manifest_json,input_schema_json,response_schema_json,schema_changed,created_by_user_id,created_at) VALUES(?,?,?,?,'prompt','[]','[]','{}','rev','{}','{}','{}',NULL,'{}',0,?,?)",
            (
                ("v-a1", "e-a1", 1, "requirement", "owner-a", 1),
                ("v-a2", "e-a2", 2, "requirement", "owner-a", 2),
                ("v-b1", "e-b1", 3, "requirement", "owner-b", 3),
            ),
        )
        connection.executemany(
            "UPDATE published_endpoints SET current_version_id=? WHERE id=?",
            (("v-a1", "e-a1"), ("v-a2", "e-a2"), ("v-b1", "e-b1")),
        )
    return path


def _服務(path: Path) -> SQLite端點管理查詢服務:
    return SQLite端點管理查詢服務(path, 游標簽章金鑰=_KEY)


def test_SQLite_owner隔離_admin矩陣_null_current與決定性分頁(tmp_path):
    service = _服務(_資料庫(tmp_path))
    first = service.列出端點(
        擁有者使用者識別碼="owner-a", 管理者查詢全部=False, 數量上限=2, 游標=None,
    )
    assert [item.端點識別碼 for item in first.項目] == ["e-a1", "e-a2"]
    assert first.下一頁游標 is not None and len(first.下一頁游標) <= 512
    second = service.列出端點(
        擁有者使用者識別碼="owner-a", 管理者查詢全部=False, 數量上限=2,
        游標=first.下一頁游標,
    )
    assert [item.端點識別碼 for item in second.項目] == ["e-null"]
    assert second.項目[0].目前版本識別碼 is second.項目[0].目前版本編號 is None
    assert second.下一頁游標 is None

    admin_owner = service.列出端點(
        擁有者使用者識別碼="owner-a", 管理者查詢全部=False, 數量上限=100, 游標=None,
    )
    admin_all = service.列出端點(
        擁有者使用者識別碼="owner-a", 管理者查詢全部=True, 數量上限=100, 游標=None,
    )
    assert {item.端點識別碼 for item in admin_owner.項目} == {"e-a1", "e-a2", "e-null"}
    assert {item.端點識別碼 for item in admin_all.項目} == {"e-a1", "e-a2", "e-b1", "e-null"}


def test_SQLite_detail_foreign_missing同None且只投影safe_fields(tmp_path):
    service = _服務(_資料庫(tmp_path))
    own = service.讀取端點(端點識別碼="e-a1", 擁有者使用者識別碼="owner-a", 管理者查詢全部=False)
    assert own is not None
    assert own.端點識別碼 == "e-a1" and own.擁有者使用者識別碼 == "owner-a"
    assert own.目前版本識別碼 == "v-a1" and own.目前版本編號 == 1
    assert service.讀取端點(端點識別碼="e-b1", 擁有者使用者識別碼="owner-a", 管理者查詢全部=False) is None
    assert service.讀取端點(端點識別碼="missing", 擁有者使用者識別碼="owner-a", 管理者查詢全部=False) is None
    assert service.讀取端點(端點識別碼="e-b1", 擁有者使用者識別碼="owner-a", 管理者查詢全部=True).端點識別碼 == "e-b1"


def test_cursor_tamper_cross_owner_cross_scope固定拒絕且HTTP為422(tmp_path):
    service = _服務(_資料庫(tmp_path))
    page = service.列出端點(
        擁有者使用者識別碼="owner-a", 管理者查詢全部=False, 數量上限=1, 游標=None,
    )
    cursor = page.下一頁游標
    assert cursor is not None
    for kwargs in (
        {"擁有者使用者識別碼": "owner-b", "管理者查詢全部": False},
        {"擁有者使用者識別碼": "owner-a", "管理者查詢全部": True},
    ):
        with pytest.raises(ValueError, match="^端點查詢游標無效$"):
            service.列出端點(**kwargs, 數量上限=1, 游標=cursor)

    tampered = cursor[:-1] + ("A" if cursor[-1] != "A" else "B")
    app = FastAPI()
    identity = 建立端點管理身份相依(lambda: 網頁使用者("owner-a", "alice", "member"))
    app.include_router(建立端點查詢路由器(service, identity))
    response = TestClient(app).get(f"/api/published-endpoints?limit=1&cursor={tampered}")
    assert response.status_code == 422
    assert response.json() == {"detail": "端點查詢游標無效"}


def test_128字元owner與endpoint_cursor仍在route_512上限內(tmp_path):
    path = _資料庫(tmp_path)
    owner = "o" * 128
    endpoint = "e" * 128
    with sqlite3.connect(path) as connection:
        connection.execute("INSERT INTO service_accounts(id,created_at) VALUES('sa-long',0)")
        connection.execute(
            "INSERT INTO published_endpoints(id,owner_user_id,service_account_id,slug,status,current_version_id,created_at,updated_at) VALUES(?,?,?,?,?,NULL,0,99)",
            (endpoint, owner, "sa-long", "long", "active"),
        )
        connection.execute("INSERT INTO service_accounts(id,created_at) VALUES('sa-long-2',0)")
        connection.execute(
            "INSERT INTO published_endpoints(id,owner_user_id,service_account_id,slug,status,current_version_id,created_at,updated_at) VALUES(?,?,?,?,?,NULL,0,98)",
            (endpoint[:-1] + "f", owner, "sa-long-2", "long-2", "active"),
        )
    service = _服務(path)
    cursor = service.列出端點(
        擁有者使用者識別碼=owner, 管理者查詢全部=False, 數量上限=1, 游標=None,
    ).下一頁游標
    assert cursor is not None and 128 < len(cursor) <= 512
    app = FastAPI()
    identity = 建立端點管理身份相依(lambda: 網頁使用者(owner, "alice", "member"))
    app.include_router(建立端點查詢路由器(service, identity))
    response = TestClient(app).get(
        "/api/published-endpoints", params={"limit": 1, "cursor": cursor},
    )
    assert response.status_code == 200
    assert [item["endpoint_id"] for item in response.json()["items"]] == [endpoint[:-1] + "f"]


def test_readonly_fresh_connection與corrupt_pointer_status_type_fail_closed(tmp_path):
    path = _資料庫(tmp_path)
    service = _服務(path)
    assert service.列出端點(
        擁有者使用者識別碼="owner-a", 管理者查詢全部=False, 數量上限=10, 游標=None,
    ).項目
    with sqlite3.connect(path) as connection:
        connection.execute("PRAGMA foreign_keys=OFF")
        connection.execute("UPDATE published_endpoints SET current_version_id='v-b1' WHERE id='e-a1'")
    with pytest.raises(RuntimeError, match="^端點管理查詢失敗$"):
        service.讀取端點(端點識別碼="e-a1", 擁有者使用者識別碼="owner-a", 管理者查詢全部=False)

    for column, value in (
        ("status", 7), ("updated_at", "not-a-time"), ("slug", "bad\x00slug"),
    ):
        with sqlite3.connect(path) as connection:
            connection.execute("PRAGMA ignore_check_constraints=ON")
            connection.execute("UPDATE published_endpoints SET current_version_id='v-a1', status='active', updated_at=30 WHERE id='e-a1'")
            connection.execute(f"UPDATE published_endpoints SET {column}=? WHERE id='e-a1'", (value,))
        with pytest.raises(RuntimeError, match="^端點管理查詢失敗$"):
            service.列出端點(
                擁有者使用者識別碼="owner-a", 管理者查詢全部=False, 數量上限=10, 游標=None,
            )


def test_lazy_proxy_generation_safe_shutdown等待active_lease(tmp_path):
    real = _服務(_資料庫(tmp_path))
    proxy = 延遲端點管理查詢服務()
    generation = proxy.安裝(real)
    entered = threading.Event()
    release = threading.Event()
    original = real.讀取端點

    def blocked(**kwargs):
        entered.set()
        release.wait(2)
        return original(**kwargs)

    real.讀取端點 = blocked  # type: ignore[method-assign]
    worker = threading.Thread(target=lambda: proxy.讀取端點(
        端點識別碼="e-a1", 擁有者使用者識別碼="owner-a", 管理者查詢全部=False,
    ))
    worker.start(); assert entered.wait(1)
    drained = threading.Event()
    shutdown = threading.Thread(target=lambda: (proxy.清除(real, generation), drained.set()))
    shutdown.start()
    assert not drained.wait(.05)
    release.set(); worker.join(2); shutdown.join(2)
    assert drained.is_set()
    with pytest.raises(RuntimeError, match="^Published端點查詢服務不可用$"):
        proxy.讀取端點(端點識別碼="e-a1", 擁有者使用者識別碼="owner-a", 管理者查詢全部=False)


def test_canonical_root零IO_OpenAPI共存_cookie_session唯一identity與startup_shutdown(tmp_path, monkeypatch):
    web_path = (tmp_path / "web.db").resolve()
    published_path = (tmp_path / "published.db").resolve()
    bundles = (tmp_path / "bundles").resolve()
    calls = []
    web = 生產設定(web_path, ("https://client.example",), "fake", "fake")
    published = Published生產設定(
        published_path, bundles, lambda _庫: calls.append("install"),
        lambda: calls.append("models") or {"fake": object()}, Owner觀測游標金鑰=_KEY,
    )
    builder = 生產Published執行建構器(published)
    original_connect = sqlite3.connect
    monkeypatch.setattr(sqlite3, "connect", lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("construction I/O")))
    app = 建立生產應用程式(web, builder)
    assert calls == [] and not web_path.exists() and not published_path.exists()
    paths = app.openapi()["paths"]
    assert set(paths["/api/published-endpoints"]) == {"get"}
    assert set(paths["/api/published-endpoints/{endpoint_id}"]) == {"get"}
    metrics = next(r for r in app.routes if getattr(r, "path", None) == "/api/published-endpoints/{endpoint_id}/metrics")
    listing = next(r for r in app.routes if getattr(r, "path", None) == "/api/published-endpoints")
    assert listing.dependant.dependencies[0].call.__canonical_dependency__ is metrics.dependant.dependencies[0].call.__canonical_dependency__

    monkeypatch.setattr(sqlite3, "connect", original_connect)
    bundles.mkdir()
    canonical = listing.dependant.dependencies[0].call.__canonical_dependency__
    app.dependency_overrides[canonical] = lambda: 網頁使用者("owner-a", "alice", "admin")
    with TestClient(app) as client:
        assert client.get("/api/published-endpoints").json() == {"items": [], "next_cursor": None}
        assert client.get("/api/published-endpoints?scope=all").json() == {"items": [], "next_cursor": None}
        assert calls == ["install", "models"]
    with pytest.raises(RuntimeError, match="^Published端點查詢服務不可用$"):
        builder._端點查詢代理.列出端點(
            擁有者使用者識別碼="owner-a", 管理者查詢全部=False, 數量上限=1, 游標=None,
        )
