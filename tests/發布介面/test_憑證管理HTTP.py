"""Acceptance 07 端點憑證管理 HTTP 契約與路由驗證。"""

from __future__ import annotations

from fastapi import FastAPI, Response
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
    """記錄管理 route 委派的 safe test adapter。

    描述：記錄管理 route 委派的 safe test adapter。
    參數：建構資料由類別欄位或建構器簽章明確提供，不讀取隱含輸入。
    返回值：可供呼叫端使用的``_管理服務``類型或實例。
    """

    def __init__(self) -> None:
        """描述：執行__init__的單一明確責任。

        參數：無；使用已封裝狀態或固定測試資料。
        返回值：依函式型別標註或既有協定回傳結果。
        """
        self.呼叫: list[tuple[str, dict[str, object]]] = []

    def 列出憑證(self, **參數):
        """描述：執行列出憑證的單一明確責任。

        參數：``**參數``。
        返回值：無；完成指定操作或更新可觀測測試狀態。
        """
        self.呼叫.append(("list", 參數))
        return 憑證列表結果((_建立摘要(),))

    def 建立憑證(self, **參數):
        """描述：執行建立憑證的單一明確責任。

        參數：``**參數``。
        返回值：無；完成指定操作或更新可觀測測試狀態。
        """
        self.呼叫.append(("create", 參數))
        摘要 = _建立摘要()
        return 一次性憑證建立收據(
            摘要.憑證識別碼, 摘要.名稱, 摘要.用途, 摘要.金鑰前綴, 摘要.金鑰末四碼,
            摘要.狀態, 摘要.到期時間, 摘要.最後使用時間, 摘要.建立時間,
            摘要.撤銷時間, 摘要.IP允許清單, 摘要.速率限制請求數, "[REDACTED]",
        )

    def 撤銷憑證(self, **參數):
        """描述：執行撤銷憑證的單一明確責任。

        參數：``**參數``。
        返回值：無；完成指定操作或更新可觀測測試狀態。
        """
        self.呼叫.append(("revoke", 參數))
        return 憑證撤銷收據("cred-example", 150.0, False)


def _建立客戶端(服務=None, *, csrf_owner="owner-1"):
    """建立具 canonical session/CSRF seam 的 isolated router app。

    描述：建立具 canonical session/CSRF seam 的 isolated router app。
    參數：``服務``、``csrf_owner``。
    返回值：無；完成指定操作或更新可觀測測試狀態。
    """
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


def _讀取錯誤碼綱要(規格: dict, 方法: str, 狀態: str) -> list[str]:
    """描述：從憑證集合route的OpenAPI response讀取exact code enum。
    參數：``規格``為paths；``方法``與``狀態``定位response。
    返回值：OpenAPI宣告的code enum清單。
    """
    回應 = 規格["/api/published-endpoints/{endpoint_id}/credentials"][方法]["responses"][狀態]
    return 回應["content"]["application/json"]["schema"]["properties"]["detail"]["properties"]["code"]["enum"]


def test_建立請求與固定HTTP錯誤碼形成封閉契約() -> None:
    """凍結 create exact keys 與 public failure codes。

    描述：凍結 create exact keys 與 public failure codes。
    參數：無；使用已封裝狀態或固定測試資料。
    返回值：無；所有驗收結果由assertions表達。
    """
    assert 建立憑證請求欄位 == (
        "name", "purpose", "expires_at", "ip_allowlist", "rate_limit_requests",
    )
    assert tuple(項目.value for 項目 in 憑證管理HTTP錯誤碼) == (
        "credential_not_found", "endpoint_status_conflict", "invalid_request",
        "credential_management_failed",
    )


def test_安全摘要與列表只序列化凍結英文鍵() -> None:
    """證明 ordinary projections 無法攜帶 create-only 或 crypto 欄位。

    描述：證明 ordinary projections 無法攜帶 create-only 或 crypto 欄位。
    參數：無；使用已封裝狀態或固定測試資料。
    返回值：無；所有驗收結果由assertions表達。
    """
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
    """固定 create 201 是唯一具有 ``initial_api_key`` 的成功 DTO。

    描述：固定 create 201 是唯一具有 ``initial_api_key`` 的成功 DTO。
    參數：無；使用已封裝狀態或固定測試資料。
    返回值：無；所有驗收結果由assertions表達。
    """
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
    清單 = 規格["/api/published-endpoints/{endpoint_id}/credentials"]["get"]["responses"]["200"]
    建立 = 規格["/api/published-endpoints/{endpoint_id}/credentials"]["post"]["responses"]["201"]
    撤銷 = 規格["/api/published-endpoints/{endpoint_id}/credentials/{credential_id}/revoke"]["post"]["responses"]["204"]
    assert 清單["content"]["application/json"]["schema"]["properties"]["items"]["items"]["additionalProperties"] is False
    assert "initial_api_key" in 建立["content"]["application/json"]["schema"]["required"]
    assert "content" not in 撤銷
    assert _讀取錯誤碼綱要(規格, "get", "404") == ["credential_not_found"]
    assert _讀取錯誤碼綱要(規格, "get", "422") == ["invalid_request"]
    assert _讀取錯誤碼綱要(規格, "get", "503") == ["auth_unavailable"]
    assert _讀取錯誤碼綱要(規格, "post", "401") == ["unauthorized"]
    assert _讀取錯誤碼綱要(規格, "post", "403") == ["csrf_invalid"]
    assert _讀取錯誤碼綱要(規格, "post", "503") == ["auth_unavailable"]
    assert _讀取錯誤碼綱要(規格, "post", "409") == ["endpoint_status_conflict"]
    assert _讀取錯誤碼綱要(規格, "post", "500") == ["credential_management_failed"]


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


def test_mutation錯誤仍交付已輪替的CSRF接續() -> None:
    """CSRF 已消耗後的 validation error 仍交付 successor，避免鎖死合法工作階段。"""
    服務 = _管理服務()
    session = lambda: 網頁使用者("owner-1", "alice", "member")

    def csrf(回應: Response):
        """模擬 canonical dependency 在 handler 前輪替並寫入 successor。"""
        回應.headers["X-CSRF-Token"] = "successor"
        回應.headers.append("set-cookie", "csrf_token=successor; Path=/; SameSite=strict")
        return 網頁使用者("owner-1", "alice", "member")

    應用 = FastAPI(redirect_slashes=False)
    應用.include_router(建立憑證管理路由器(
        服務, session, csrf, 時鐘=lambda: 100.0, 請求識別碼工廠=lambda: "request-1",
    ))
    with TestClient(應用, raise_server_exceptions=False) as 客戶端:
        回應們 = [
            客戶端.post("/api/published-endpoints/endpoint-1/credentials?x=1", json={}),
            客戶端.post(
                "/api/published-endpoints/endpoint-1/credentials",
                content=b"{}", headers={"content-type": "text/plain"},
            ),
            客戶端.post(
                "/api/published-endpoints/endpoint-1/credentials",
                content=b"{", headers={"content-type": "application/json"},
            ),
            客戶端.post(
                "/api/published-endpoints/endpoint-1/credentials",
                json={
                    "name": "production", "purpose": "partner integration", "expires_at": 100.0,
                    "ip_allowlist": [], "rate_limit_requests": 60,
                },
            ),
        ]
    assert all(回應.status_code == 422 for 回應 in 回應們)
    assert all(回應.headers["X-CSRF-Token"] == "successor" for 回應 in 回應們)
    assert all("csrf_token=successor" in 回應.headers["set-cookie"] for 回應 in 回應們)
    assert 服務.呼叫 == []


def test_revoke逐段讀取真實本文且任何位元組都在副作用前拒絕() -> None:
    """缺少或偽造 Content-Length 皆不能繞過 byte-exact empty request 契約。"""
    客戶端, _, 服務, *_ = _建立客戶端()
    路徑 = "/api/published-endpoints/endpoint-1/credentials/cred-example/revoke"
    with 客戶端:
        串流 = 客戶端.post(路徑, content=iter([b"x"]))
        偽造 = 客戶端.post(路徑, content=b"NOT-EMPTY", headers={"Content-Length": "0"})
    assert [(回應.status_code, 回應.json()) for 回應 in (串流, 偽造)] == [
        (422, {"detail": {"code": "invalid_request"}}),
    ] * 2
    assert 服務.呼叫 == []


def test_malformed路徑固定422且不回顯輸入() -> None:
    """路徑格式錯誤由 A07 adapter 固定化，不交給 framework validation detail。"""
    客戶端, _, 服務, *_ = _建立客戶端()
    with 客戶端:
        回應們 = (
            客戶端.get("/api/published-endpoints/bad%21/credentials"),
            客戶端.post(
                "/api/published-endpoints/endpoint-1/credentials/bad%21/revoke", content=b"",
            ),
        )
    assert [(回應.status_code, 回應.json()) for 回應 in 回應們] == [
        (422, {"detail": {"code": "invalid_request"}}),
    ] * 2
    assert all("bad!" not in 回應.text for 回應 in 回應們)
    assert 服務.呼叫 == []


def test_revoke兩次權威身份的角色漂移時fail_closed() -> None:
    """CSRF交易重讀到的admin role若與current-session不同，不得進入撤銷服務。"""
    服務 = _管理服務()
    session = lambda: 網頁使用者("admin-1", "alice", "admin")
    def csrf(回應: Response):
        回應.headers["X-CSRF-Token"] = "successor"
        回應.headers.append("set-cookie", "csrf_token=successor; Path=/; SameSite=strict")
        return 網頁使用者("admin-1", "alice", "member")
    應用 = FastAPI(redirect_slashes=False)
    應用.include_router(建立憑證管理路由器(
        服務, session, csrf, 時鐘=lambda: 100.0, 請求識別碼工廠=lambda: "request-1",
    ))
    with TestClient(應用, raise_server_exceptions=False) as 客戶端:
        回應 = 客戶端.post(
            "/api/published-endpoints/endpoint-1/credentials/cred-example/revoke", content=b"",
        )
    assert 回應.status_code == 500
    assert 回應.json() == {"detail": {"code": "credential_management_failed"}}
    assert 回應.headers["X-CSRF-Token"] == "successor"
    assert "csrf_token=successor" in 回應.headers["set-cookie"]
    assert 服務.呼叫 == []


def test_create權威時鐘失敗仍交付CSRF接續() -> None:
    """CSRF輪替後clock失敗固定500，且合法session仍收到successor。"""
    服務 = _管理服務()
    session = lambda: 網頁使用者("owner-1", "alice", "member")

    def csrf(回應: Response):
        回應.headers["X-CSRF-Token"] = "successor"
        回應.headers.append("set-cookie", "csrf_token=successor; Path=/; SameSite=strict")
        return 網頁使用者("owner-1", "alice", "member")

    應用 = FastAPI(redirect_slashes=False)
    應用.include_router(建立憑證管理路由器(
        服務, session, csrf, 時鐘=lambda: float("nan"), 請求識別碼工廠=lambda: "request-1",
    ))
    with TestClient(應用, raise_server_exceptions=False) as 客戶端:
        回應 = 客戶端.post("/api/published-endpoints/endpoint-1/credentials", json={
            "name": "production", "purpose": "partner integration", "expires_at": 200.0,
            "ip_allowlist": [], "rate_limit_requests": 60,
        })
    assert 回應.status_code == 500
    assert 回應.json() == {"detail": {"code": "credential_management_failed"}}
    assert 回應.headers["X-CSRF-Token"] == "successor"
    assert "csrf_token=successor" in 回應.headers["set-cookie"]
    assert 服務.呼叫 == []


def test_create_principal重建失敗仍交付CSRF接續() -> None:
    """CSRF輪替後任一authoritative principal畸形，固定500且仍交付successor。"""
    def 畸形使用者(種類: str):
        if 種類 == "wrong-type":
            return object()
        使用者 = object.__new__(網頁使用者)
        if 種類 != "missing-id":
            object.__setattr__(使用者, "識別碼", 7 if 種類 == "invalid-id" else "owner-1")
        if 種類 != "missing-role":
            object.__setattr__(使用者, "角色", {"empty-role": "", "unknown-role": "root"}.get(種類, "member"))
        return 使用者

    for 種類 in ("wrong-type", "missing-id", "missing-role", "invalid-id", "empty-role", "unknown-role"):
        for 無效來源 in ("session", "csrf"):
            服務 = _管理服務()
            有效使用者 = 網頁使用者("owner-1", "alice", "member")
            session = lambda: 畸形使用者(種類) if 無效來源 == "session" else 有效使用者

            def csrf(回應: Response):
                回應.headers["X-CSRF-Token"] = "successor"
                回應.headers.append("set-cookie", "csrf_token=successor; Path=/; SameSite=strict")
                return 畸形使用者(種類) if 無效來源 == "csrf" else 有效使用者

            應用 = FastAPI(redirect_slashes=False)
            應用.include_router(建立憑證管理路由器(
                服務, session, csrf, 時鐘=lambda: 100.0, 請求識別碼工廠=lambda: "request-1",
            ))
            with TestClient(應用, raise_server_exceptions=False) as 客戶端:
                回應 = 客戶端.post("/api/published-endpoints/endpoint-1/credentials", json={
                    "name": "production", "purpose": "partner integration", "expires_at": 200.0,
                    "ip_allowlist": [], "rate_limit_requests": 60,
                })
            assert 回應.status_code == 500, (種類, 無效來源, 回應.text)
            assert 回應.json() == {"detail": {"code": "credential_management_failed"}}
            assert 回應.headers["X-CSRF-Token"] == "successor"
            assert "csrf_token=successor" in 回應.headers["set-cookie"]
            assert 服務.呼叫 == []
