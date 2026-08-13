"""Acceptance 07 API Key canonical HTTP、SQLite 與 restart 生命週期驗收。"""

from __future__ import annotations

import sqlite3
import time

from fastapi.testclient import TestClient

from 繁中代理.使用者 import 使用者庫
from 繁中代理.發布介面.asgi import 建立CP4ASGI應用程式
from 繁中代理.發布介面.憑證.加密 import AESGCM憑證封套
from 繁中代理.發布介面.憑證.儲存庫 import SQLite憑證儲存庫
from 繁中代理.發布介面.生產Published執行 import Published生產設定
from 繁中代理.發布介面.設定 import 生產設定, 網頁CSRFHeader名稱
from 繁中代理.發布介面.領域模型 import WebOwnerPrincipal


def _建立設定(tmp_path):
    """建立 restart 可重用且 keyring 固定的 explicit production settings。"""
    web = 生產設定(
        tmp_path / "web.sqlite3", ("http://localhost:5173",), "fake", "fake", None, None,
        Cookie安全=False, 工作階段有效秒數=60,
    )
    published = Published生產設定(
        tmp_path / "published.sqlite3", tmp_path / "bundles",
        lambda _工具庫: None, lambda: {"fake": object()},
        憑證封套工廠=lambda: AESGCM憑證封套({1: b"A" * 32}, 1),
    )
    return web, published


def _建立擁有者(web路徑) -> str:
    """建立可由 canonical login 驗證的真 Web owner。"""
    使用者們 = 使用者庫(web路徑)
    try:
        return str(使用者們.建立使用者("alice", "correct horse", roles=["user"])["id"])
    finally:
        使用者們.連線.close()


def _登入(client: TestClient) -> str:
    """登入並回傳目前 single-use CSRF token。"""
    回應 = client.post("/api/auth/login", json={"username": "alice", "password": "correct horse"})
    assert 回應.status_code == 200
    return 回應.json()["csrf_token"]


def _建立端點圖形(資料庫, owner: str) -> None:
    """以 production-equivalent SQLite rows 建立 active endpoint authority。"""
    with sqlite3.connect(資料庫) as 連線:
        連線.execute("INSERT INTO service_accounts VALUES('sa-a07',1,NULL)")
        連線.execute(
            "INSERT INTO published_endpoints VALUES(?,?,?,?,?,?,?,?,?,?)",
            ("endpoint-a07", owner, "sa-a07", "a07", "active", None, 1, 1, 60, 60),
        )


def test_create_multi_key_expire_inactive_revoke與restart_readback(tmp_path) -> None:
    """經真 cookie/CSRF/canonical app 證明安全摘要與 lifecycle durability。"""
    web, published = _建立設定(tmp_path)
    published.技能套件發布根.mkdir()
    owner = _建立擁有者(web.資料庫路徑)
    app = 建立CP4ASGI應用程式(web, published)
    now = time.time()
    additional_key = None

    with TestClient(app, raise_server_exceptions=False) as client:
        _建立端點圖形(published.發布資料庫路徑, owner)
        initial = SQLite憑證儲存庫(
            published.發布資料庫路徑, AESGCM憑證封套({1: b"A" * 32}, 1),
            clock=lambda: now, id_factory=lambda: "credential-initial",
        ).建立管理憑證(
            "endpoint-a07", WebOwnerPrincipal(owner), name="initial", purpose="initial integration",
            expires_at=now + 86_400, ip_allowlist=(), rate_limit_requests=60,
        )
        csrf = _登入(client)
        建立 = client.post(
            "/api/published-endpoints/endpoint-a07/credentials",
            headers={網頁CSRFHeader名稱: csrf},
            json={
                "name": "additional", "purpose": "partner integration",
                "expires_at": now + 172_800, "ip_allowlist": [], "rate_limit_requests": 60,
            },
        )
        assert 建立.status_code == 201
        additional_key = 建立.json().pop("initial_api_key")
        assert type(additional_key) is str and additional_key != initial.api_key
        csrf = 建立.headers[網頁CSRFHeader名稱]

        列表 = client.get("/api/published-endpoints/endpoint-a07/credentials")
        assert 列表.status_code == 200 and "initial_api_key" not in 列表.text
        assert {項目["credential_id"] for 項目 in 列表.json()["items"]} == {
            "credential-initial", 建立.json()["credential_id"],
        }

        with sqlite3.connect(published.發布資料庫路徑) as 連線:
            連線.execute(
                "UPDATE endpoint_credentials SET created_at=?,expires_at=? "
                "WHERE id='credential-initial'",
                (now - 172_800, now - 1),
            )
        過期列表 = client.get("/api/published-endpoints/endpoint-a07/credentials").json()["items"]
        assert {項目["credential_id"]: 項目["status"] for 項目 in 過期列表}["credential-initial"] == "expired"

        credential_id = 建立.json()["credential_id"]
        撤銷 = client.post(
            f"/api/published-endpoints/endpoint-a07/credentials/{credential_id}/revoke",
            headers={網頁CSRFHeader名稱: csrf}, content=b"",
        )
        assert 撤銷.status_code == 204 and 撤銷.content == b""
        csrf = 撤銷.headers[網頁CSRFHeader名稱]
        重複撤銷 = client.post(
            f"/api/published-endpoints/endpoint-a07/credentials/{credential_id}/revoke",
            headers={網頁CSRFHeader名稱: csrf}, content=b"",
        )
        assert 重複撤銷.status_code == 204 and 重複撤銷.content == b""

    restarted = 建立CP4ASGI應用程式(web, published)
    with TestClient(restarted, raise_server_exceptions=False) as client:
        _登入(client)
        items = client.get("/api/published-endpoints/endpoint-a07/credentials").json()["items"]
        status = {項目["credential_id"]: 項目["status"] for 項目 in items}
        assert status["credential-initial"] == "expired"
        assert status[credential_id] == "revoked"

    assert additional_key is not None
