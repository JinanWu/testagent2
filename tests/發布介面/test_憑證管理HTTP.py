"""Acceptance 07 端點憑證管理 HTTP 契約與路由驗證。"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from 繁中代理.發布介面.憑證管理契約 import (
    一次性憑證建立收據,
    建立憑證請求欄位,
    憑證列表結果,
    憑證撤銷收據,
    憑證摘要,
    憑證管理HTTP錯誤碼,
    憑證管理狀態,
    序列化一次性憑證建立收據,
    序列化憑證列表,
    序列化憑證摘要,
)
from 繁中代理.發布介面.網頁工作階段 import 網頁使用者
from 繁中代理.發布介面.路由.憑證管理 import 建立憑證管理路由器


class _管理服務:
    """記錄管理 route 委派的 safe test adapter。"""

    def __init__(self) -> None:
        self.呼叫: list[tuple[str, dict[str, object]]] = []

    def 列出憑證(self, **參數):
        self.呼叫.append(("list", 參數))
        return 憑證列表結果((_建立摘要(),))

    def 建立憑證(self, **參數):
        self.呼叫.append(("create", 參數))
        摘要 = _建立摘要()
        return 一次性憑證建立收據(
            摘要.憑證識別碼, 摘要.名稱, 摘要.用途, 摘要.金鑰前綴, 摘要.金鑰末四碼,
            摘要.狀態, 摘要.到期時間, 摘要.最後使用時間, 摘要.建立時間,
            摘要.撤銷時間, 摘要.IP允許清單, 摘要.速率限制請求數, "[REDACTED]",
        )

    def 撤銷憑證(self, **參數):
        self.呼叫.append(("revoke", 參數))
        return 憑證撤銷收據("cred-example", 150.0, False)


def _建立客戶端(服務=None, *, csrf_owner="owner-1"):
    """建立具 canonical session/CSRF seam 的 isolated router app。"""
    服務 = 服務 or _管理服務()
    session = lambda: 網頁使用者("owner-1", "alice", "member")
    csrf = lambda: 網頁使用者(csrf_owner, "alice", "member")
    路由器 = 建立憑證管理路由器(
        服務, session, csrf, 時鐘=lambda: 100.0, 請求識別碼工廠=lambda: "request-1",
    )
    應用 = FastAPI(redirect_slashes=False)
    應用.include_router(路由器)
    return TestClient(應用, raise_server_exceptions=False), 路由器, 服務, session, csrf


def _建立摘要() -> 憑證摘要:
    """建立不含秘密材料的固定憑證摘要。

    描述：提供 exact-key serializer 測試使用的安全投影。
    參數：無。
    返回值：欄位完整且生命週期有效的 ``憑證摘要``。
    """
    return 憑證摘要(
        "cred-example", "production", "partner integration", "public-prefix", "last",
        憑證管理狀態.有效, 200.0, None, 100.0, None, (), 60,
    )


def test_建立請求與固定HTTP錯誤碼形成封閉契約() -> None:
    """凍結 create exact keys 與 public failure codes。"""
    assert 建立憑證請求欄位 == (
        "name", "purpose", "expires_at", "ip_allowlist", "rate_limit_requests",
    )
    assert tuple(項目.value for 項目 in 憑證管理HTTP錯誤碼) == (
        "credential_not_found", "endpoint_status_conflict", "invalid_request",
        "credential_management_failed",
    )


def test_安全摘要與列表只序列化凍結英文鍵() -> None:
    """證明 ordinary projections 無法攜帶 create-only 或 crypto 欄位。"""
    摘要 = _建立摘要()
    內容 = 序列化憑證摘要(摘要)
    assert tuple(內容) == (
        "credential_id", "name", "purpose", "key_prefix", "key_last4", "status",
        "expires_at", "last_used_at", "created_at", "revoked_at", "ip_allowlist",
        "rate_limit_requests",
    )
    assert 序列化憑證列表(憑證列表結果((摘要,))) == {"items": [內容]}
    禁止欄位 = {
        "initial_api_key", "api_key", "key_hash", "key_nonce", "key_ciphertext",
        "key_version", "revision", "proof", "master_key",
    }
    assert 禁止欄位.isdisjoint(內容)
    assert 禁止欄位.isdisjoint(序列化憑證列表(憑證列表結果((摘要,))))


def test_只有建立收據可序列化一次性明文() -> None:
    """固定 create 201 是唯一具有 ``initial_api_key`` 的成功 DTO。"""
    摘要 = _建立摘要()
    收據 = 一次性憑證建立收據(
        摘要.憑證識別碼, 摘要.名稱, 摘要.用途, 摘要.金鑰前綴, 摘要.金鑰末四碼,
        摘要.狀態, 摘要.到期時間, 摘要.最後使用時間, 摘要.建立時間,
        摘要.撤銷時間, 摘要.IP允許清單, 摘要.速率限制請求數, "[REDACTED]",
    )
    內容 = 序列化一次性憑證建立收據(收據)
    assert tuple(內容) == (
        "credential_id", "name", "purpose", "key_prefix", "key_last4", "status",
        "expires_at", "last_used_at", "created_at", "revoked_at", "ip_allowlist",
        "rate_limit_requests", "initial_api_key",
    )
    assert 內容["initial_api_key"] == "[REDACTED]"
    assert "[REDACTED]" not in repr(收據)


def test_路由清單方法狀態與相依項形成精確契約() -> None:
    """固定三條 endpoint-scoped routes，GET 不消耗 CSRF。"""
    客戶端, 路由器, _, session, csrf = _建立客戶端()
    assert [(路由.path, 路由.methods) for 路由 in 路由器.routes] == [
        ("/api/published-endpoints/{endpoint_id}/credentials", {"GET"}),
        ("/api/published-endpoints/{endpoint_id}/credentials", {"POST"}),
        ("/api/published-endpoints/{endpoint_id}/credentials/{credential_id}/revoke", {"POST"}),
    ]
    assert all(type(路由) is APIRoute for 路由 in 路由器.routes)
    assert [相依.call for 相依 in 路由器.routes[0].dependant.dependencies] == [session]
    assert [相依.call for 路由 in 路由器.routes[1:] for 相依 in 路由.dependant.dependencies] == [
        session, csrf, session, csrf,
    ]
    with 客戶端:
        規格 = 客戶端.get("/openapi.json").json()["paths"]
    assert set(規格["/api/published-endpoints/{endpoint_id}/credentials"]) == {"get", "post"}
    assert set(規格["/api/published-endpoints/{endpoint_id}/credentials/{credential_id}/revoke"]) == {"post"}


def test_list_create_revoke只使用權威身份且成功狀態固定() -> None:
    """證明 happy path、create-only secret 與 revoke empty 204。"""
    客戶端, _, 服務, *_ = _建立客戶端()
    本文 = {
        "name": "production", "purpose": "partner integration", "expires_at": 200.0,
        "ip_allowlist": [], "rate_limit_requests": 60,
    }
    with 客戶端:
        列表 = 客戶端.get("/api/published-endpoints/endpoint-1/credentials")
        建立 = 客戶端.post("/api/published-endpoints/endpoint-1/credentials", json=本文)
        撤銷 = 客戶端.post(
            "/api/published-endpoints/endpoint-1/credentials/cred-example/revoke",
            content=b"",
        )
    assert 列表.status_code == 200 and "initial_api_key" not in 列表.text
    assert 建立.status_code == 201 and 建立.json()["initial_api_key"] == "[REDACTED]"
    assert 撤銷.status_code == 204 and 撤銷.content == b""
    assert [名稱 for 名稱, _ in 服務.呼叫] == ["list", "create", "revoke"]
    for _, 參數 in 服務.呼叫:
        assert 參數["擁有者使用者識別碼"] == "owner-1"


def test_hostile_create與所有query在service前固定422() -> None:
    """拒絕 extra、到期邊界、錯誤 content type 與 owner query。"""
    客戶端, _, 服務, *_ = _建立客戶端()
    正常 = {
        "name": "production", "purpose": "partner integration", "expires_at": 200.0,
        "ip_allowlist": [], "rate_limit_requests": 60,
    }
    with 客戶端:
        回應們 = [
            客戶端.post("/api/published-endpoints/endpoint-1/credentials", json=正常 | {"owner_user_id": "attacker"}),
            客戶端.post("/api/published-endpoints/endpoint-1/credentials", json=正常 | {"expires_at": 100.0}),
            客戶端.post("/api/published-endpoints/endpoint-1/credentials", content=b"{}", headers={"content-type": "text/plain"}),
            客戶端.get("/api/published-endpoints/endpoint-1/credentials?owner_user_id=attacker"),
        ]
    assert [(回應.status_code, 回應.json()) for 回應 in 回應們] == [
        (422, {"detail": {"code": "invalid_request"}}),
    ] * 4
    assert 服務.呼叫 == []
