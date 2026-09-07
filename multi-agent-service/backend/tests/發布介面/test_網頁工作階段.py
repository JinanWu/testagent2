"""AUTH A02 Web session、cookie、single-use CSRF 與 CORS 測試。"""

import asyncio
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi import APIRouter, Depends
from fastapi.testclient import TestClient

from 繁中代理.使用者 import 使用者庫
from 繁中代理.發布介面.相依項 import 發布介面相依項
from 繁中代理.發布介面.應用程式 import 建立應用程式, 建立網頁應用程式
from 繁中代理.發布介面.設定 import 網頁安全設定
from 繁中代理.發布介面.網頁工作階段 import (
    網頁CSRF無效,
    網頁管理權限不足,
    網頁使用者,
    網頁未授權,
    網頁工作階段服務,
    網頁認證不可用,
)
from 繁中代理.發布介面.路由 import (
    建立CSRF相依項,
    建立SQLite帳密驗證器,
    建立網頁認證路由器,
)


def _建立資料庫(tmp_path):
    path = tmp_path / "auth.sqlite3"
    users = 使用者庫(path)
    alice = users.建立使用者("alice", "correct horse", roles=["admin"])
    cli = users.建立登入Token(alice["id"])
    migration = (
        __import__("pathlib").Path(__file__).parents[2]
        / "繁中代理/發布介面/遷移/0005_建立網頁工作階段.sql"
    ).read_text()
    users.連線.executescript(migration)
    users.連線.close()
    return path, alice, cli


def _建立認證客戶端(tmp_path):
    path, alice, cli = _建立資料庫(tmp_path)
    settings = 網頁安全設定(("http://localhost:5173",), Cookie安全=False, 工作階段有效秒數=60)
    service = 網頁工作階段服務(path, 有效秒數=60)
    router = 建立網頁認證路由器(service, 建立SQLite帳密驗證器(path), 設定=settings)
    app = 建立網頁應用程式(發布介面相依項((router,), ()), settings)
    return TestClient(app), app, path, alice, cli


def test_網頁安全設定拒絕非exact安全origin():
    """拒絕 wildcard、null、重複、路徑、credential 與非 loopback HTTP。"""
    invalid = [
        ("*",), ("null",), ("https://good.example/",),
        ("https://u:p@good.example",), ("http://good.example",),
        ("https://good.example", "https://good.example"),
    ]
    for origins in invalid:
        with pytest.raises(ValueError, match="^Web安全設定無效$"):
            網頁安全設定(origins)
    assert 網頁安全設定(("https://good.example",)).Cookie安全 is True
    assert 網頁安全設定(("http://localhost:5173",), Cookie安全=False).允許來源 == (
        "http://localhost:5173",
    )
    with pytest.raises(ValueError):
        網頁安全設定((), Cookie安全=False)


def test_exact_credentialed_CORS矩陣(tmp_path):
    """canonical composition 允許 exact origin/header/method 並拒絕 hostile origin。"""
    path, _, _ = _建立資料庫(tmp_path)
    settings = 網頁安全設定(("https://web.example",))
    router = 建立網頁認證路由器(
        網頁工作階段服務(path), 建立SQLite帳密驗證器(path), 設定=settings,
    )
    app = 建立網頁應用程式(發布介面相依項((router,), ()), settings)
    with TestClient(app) as client:
        headers = {
            "Origin": "https://web.example",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "Content-Type,X-CSRF-Token",
        }
        response = client.options("/api/auth/login", headers=headers)
        assert response.status_code == 200
        assert response.headers["access-control-allow-origin"] == "https://web.example"
        assert response.headers["access-control-allow-credentials"] == "true"
        assert "origin" in response.headers["vary"].lower()
        denied = client.options(
            "/api/auth/login", headers={**headers, "Origin": "https://web.example.evil"}
        )
        assert "access-control-allow-origin" not in denied.headers
        bad_header = client.options(
            "/api/auth/login",
            headers={**headers, "Access-Control-Request-Headers": "Authorization"},
        )
        assert bad_header.status_code == 400
        same_origin = client.get("/api/auth/session")
        assert same_origin.status_code == 401


def test_hash_only發行fixation恢復與CLI隔離(tmp_path):
    """只儲存 digest；重新登入撤銷舊 cookie，Web 撤銷不碰 CLI token。"""
    path, alice, cli = _建立資料庫(tmp_path)
    service = 網頁工作階段服務(path, 時鐘=lambda: 1000.0)
    user = 網頁使用者(alice["id"], "alice", "admin")
    first = service.發行(user)
    second = service.發行(user, first.工作階段權杖)
    assert first.工作階段權杖 != second.工作階段權杖
    assert first.CSRF權杖 != second.CSRF權杖
    with pytest.raises(網頁未授權):
        service.恢復(first.工作階段權杖, first.CSRF權杖)
    restored = service.恢復(second.工作階段權杖, second.CSRF權杖)
    assert restored.使用者 == user and restored.CSRF權杖 == second.CSRF權杖
    with sqlite3.connect(path) as connection:
        row = connection.execute(
            "SELECT session_token_hash,csrf_token_hash FROM web_sessions WHERE id=?", (second.識別碼,)
        ).fetchone()
        assert len(row[0]) == len(row[1]) == 32
        assert second.工作階段權杖.encode() not in row and second.CSRF權杖.encode() not in row
    service.撤銷(second.工作階段權杖, second.CSRF權杖)
    users = 使用者庫(path)
    assert users.驗證登入Token(cli) is not None
    users.連線.close()


def test_恢復遺失csrf會輪替且single_use可並行只贏一次(tmp_path):
    """recovery cookie mismatch 會換新值；相同 CSRF 的競爭只有一個 CAS 成功。"""
    path, alice, _ = _建立資料庫(tmp_path)
    service = 網頁工作階段服務(path, 時鐘=lambda: 1000.0)
    issued = service.發行(網頁使用者(alice["id"], "alice", "member"))
    recovered = service.恢復(issued.工作階段權杖, None)
    assert recovered.CSRF權杖 != issued.CSRF權杖
    barrier = threading.Barrier(2)

    def consume():
        barrier.wait()
        try:
            return service.輪替(issued.工作階段權杖, recovered.CSRF權杖)
        except 網頁CSRF無效:
            return None

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: consume(), range(2)))
    assert sum(result is not None for result in results) == 1
    winner = next(result for result in results if result is not None)
    with pytest.raises(網頁CSRF無效):
        service.輪替(issued.工作階段權杖, recovered.CSRF權杖)
    assert service.輪替(issued.工作階段權杖, winner.CSRF權杖).CSRF權杖 != winner.CSRF權杖


def test_read_only身份驗證不接觸CSRF或session_state(tmp_path):
    """Authority-first mutation可先驗principal，且不輪替CSRF、不更新last_seen。"""
    path, alice, _ = _建立資料庫(tmp_path)
    service = 網頁工作階段服務(path, 時鐘=lambda: 1000.0)
    issued = service.發行(網頁使用者(alice["id"], "alice", "admin"))
    with sqlite3.connect(path) as connection:
        before = connection.execute(
            "SELECT csrf_token_hash,last_seen_at,revoked_at FROM web_sessions WHERE id=?",
            (issued.識別碼,),
        ).fetchone()
    assert service.驗證身份(issued.工作階段權杖) == issued.使用者
    with sqlite3.connect(path) as connection:
        after = connection.execute(
            "SELECT csrf_token_hash,last_seen_at,revoked_at FROM web_sessions WHERE id=?",
            (issued.識別碼,),
        ).fetchone()
    assert after == before


def test_管理操作role_first原子撤銷disabled且member完全不改session(tmp_path):
    path, alice, _ = _建立資料庫(tmp_path)
    service = 網頁工作階段服務(path, 時鐘=lambda: 1001.0)
    issued = service.發行(網頁使用者(alice["id"], "alice", "admin"))
    with sqlite3.connect(path) as connection:
        connection.execute("UPDATE users SET disabled=1 WHERE id=?", (alice["id"],))
        before = connection.execute(
            "SELECT csrf_token_hash,last_seen_at,revoked_at FROM web_sessions WHERE id=?",
            (issued.識別碼,),
        ).fetchone()
    with pytest.raises(網頁未授權):
        service.授權管理操作(issued.工作階段權杖, issued.CSRF權杖)
    with sqlite3.connect(path) as connection:
        after = connection.execute(
            "SELECT csrf_token_hash,last_seen_at,revoked_at FROM web_sessions WHERE id=?",
            (issued.識別碼,),
        ).fetchone()
        connection.execute("UPDATE users SET disabled=0 WHERE id=?", (alice["id"],))
    assert after[:2] == before[:2] and after[2] == 1001.0
    with pytest.raises(網頁未授權):
        service.授權管理操作(issued.工作階段權杖, issued.CSRF權杖)

    with sqlite3.connect(path) as connection:
        connection.execute("UPDATE users SET roles_json='[]' WHERE id=?", (alice["id"],))
    member = service.發行(網頁使用者(alice["id"], "alice", "member"))
    with sqlite3.connect(path) as connection:
        before = connection.execute(
            "SELECT csrf_token_hash,last_seen_at,revoked_at FROM web_sessions WHERE id=?",
            (member.識別碼,),
        ).fetchone()
    with pytest.raises(網頁管理權限不足):
        service.授權管理操作(member.工作階段權杖, "wrong-csrf")
    with sqlite3.connect(path) as connection:
        after = connection.execute(
            "SELECT csrf_token_hash,last_seen_at,revoked_at FROM web_sessions WHERE id=?",
            (member.識別碼,),
        ).fetchone()
    assert after == before


def test_read_only身份驗證的clock_cancellation保持exact_identity(tmp_path):
    cancellation = asyncio.CancelledError("CANCEL_AUTH")
    service = 網頁工作階段服務(
        tmp_path / "unused.sqlite3",
        時鐘=lambda: (_ for _ in ()).throw(cancellation),
    )
    with pytest.raises(asyncio.CancelledError) as caught:
        service.驗證身份("x" * 32)
    assert caught.value is cancellation


def test_expiry_boundary與disabled使用者永久失效(tmp_path):
    """now >= expiry 無效；disabled owner 被撤銷後不能因重新啟用復活。"""
    path, alice, _ = _建立資料庫(tmp_path)
    now = [1000.0]
    service = 網頁工作階段服務(path, 時鐘=lambda: now[0], 有效秒數=60)
    issued = service.發行(網頁使用者(alice["id"], "alice", "member"))
    now[0] = 1060.0
    with pytest.raises(網頁未授權):
        service.恢復(issued.工作階段權杖, issued.CSRF權杖)
    now[0] = 2000.0
    active = service.發行(網頁使用者(alice["id"], "alice", "member"))
    with sqlite3.connect(path) as connection:
        connection.execute("UPDATE users SET disabled=1 WHERE id=?", (alice["id"],))
    with pytest.raises(網頁未授權):
        service.恢復(active.工作階段權杖, active.CSRF權杖)
    with sqlite3.connect(path) as connection:
        connection.execute("UPDATE users SET disabled=0 WHERE id=?", (alice["id"],))
    with pytest.raises(網頁未授權):
        service.恢復(active.工作階段權杖, active.CSRF權杖)


def test_W102_exact登入恢復登出與cookie_scope(tmp_path):
    """actual app 回 exact DTO/status 並以 /api HttpOnly Lax cookie 維持與刪除 pair。"""
    client, _, path, _, cli = _建立認證客戶端(tmp_path)
    with client:
        login = client.post(
            "/api/auth/login", json={"username": "alice", "password": "correct horse"}
        )
        assert login.status_code == 200
        assert set(login.json()) == {"user", "csrf_token"}
        assert login.json()["user"] == {"id": login.json()["user"]["id"], "username": "alice", "role": "admin"}
        cookies = login.headers.get_list("set-cookie")
        assert len(cookies) == 2
        assert all("HttpOnly" in item and "Path=/api" in item and "SameSite=lax" in item for item in cookies)
        session = client.get("/api/auth/session")
        assert session.status_code == 200 and session.json() == login.json()
        wrong = client.post("/api/auth/logout", headers={"X-CSRF-Token": "wrong-token"})
        assert wrong.status_code == 403 and wrong.json() == {"detail": {"code": "csrf_invalid"}}
        assert all("Max-Age=0" in item and "Path=/api" in item for item in wrong.headers.get_list("set-cookie"))
        client.cookies.update({
            "published_web_session": login.cookies["published_web_session"],
            "published_web_csrf": login.cookies["published_web_csrf"],
        })
        logout = client.post("/api/auth/logout", headers={"X-CSRF-Token": login.json()["csrf_token"]})
        assert logout.status_code == 204 and logout.content == b""
        deleted = logout.headers.get_list("set-cookie")
        assert len(deleted) == 2
        assert all(
            "Max-Age=0" in item and "HttpOnly" in item
            and "Path=/api" in item and "SameSite=lax" in item
            for item in deleted
        )
    users = 使用者庫(path)
    assert users.驗證登入Token(cli) is not None
    users.連線.close()


@pytest.mark.parametrize("payload", [
    {"username": "alice", "password": "wrong"},
    {"username": "missing", "password": "wrong"},
])
def test_帳密失敗固定401且strict_unknown_fields(tmp_path, payload):
    """unknown/wrong password indistinguishable，extra field 與非 exact JSON 為 422。"""
    client, _, _, _, _ = _建立認證客戶端(tmp_path)
    with client:
        denied = client.post("/api/auth/login", json=payload)
        assert denied.status_code == 401
        assert denied.json() == {"detail": {"code": "invalid_credentials"}}
        assert client.post("/api/auth/login", json={**payload, "extra": True}).status_code == 422
        assert client.post(
            "/api/auth/login", content='{"username":"alice","password":"wrong"}',
            headers={"Content-Type": "application/json; charset=utf-8"},
        ).status_code == 422


def test_OpenAPI_exact_auth_inventory(tmp_path):
    """四條 auth routes 有 exact operation IDs、schemas 與 response inventory。"""
    _, app, _, _, _ = _建立認證客戶端(tmp_path)
    spec = app.openapi()
    assert {path for path in spec["paths"] if path.startswith("/api/auth")} == {
        "/api/auth/login", "/api/auth/me", "/api/auth/session", "/api/auth/logout"
    }
    expected = {
        ("/api/auth/me", "get"): ("取得目前網頁認證使用者_api_auth_me_get", {"200", "401", "503"}),
        ("/api/auth/session", "get"): ("取得網頁認證工作階段_api_auth_session_get", {"200", "401", "503"}),
        ("/api/auth/login", "post"): ("登入網頁認證工作階段_api_auth_login_post", {"200", "401", "422", "503"}),
        ("/api/auth/logout", "post"): ("登出網頁認證工作階段_api_auth_logout_post", {"204", "401", "403", "503"}),
    }
    for (path, method), (operation, responses) in expected.items():
        item = spec["paths"][path][method]
        assert item["operationId"] == operation
        assert set(item["responses"]) == responses
    schemas = spec["components"]["schemas"]
    assert set(schemas["AuthUser"]["required"]) == {"id", "username", "role"}
    assert schemas["AuthUser"]["additionalProperties"] is False
    assert set(schemas["AuthSessionResponse"]["required"]) == {"user", "csrf_token"}
    assert schemas["LoginRequest"]["additionalProperties"] is False


def test_OpenAPI文件化手動cookie_header與successor_headers(tmp_path):
    """手動 transport 解析仍完整文件化，且 session/logout 不產生自動 422。"""
    _, app, _, _, _ = _建立認證客戶端(tmp_path)
    paths = app.openapi()["paths"]

    login = paths["/api/auth/login"]["post"]
    session = paths["/api/auth/session"]["get"]
    logout = paths["/api/auth/logout"]["post"]
    assert [(item["name"], item["in"], item["required"]) for item in login["parameters"]] == [
        ("published_web_session", "cookie", False),
    ]
    assert [(item["name"], item["in"], item["required"]) for item in session["parameters"]] == [
        ("published_web_session", "cookie", True),
        ("published_web_csrf", "cookie", False),
    ]
    assert [(item["name"], item["in"], item["required"]) for item in logout["parameters"]] == [
        ("published_web_session", "cookie", True),
        ("X-CSRF-Token", "header", True),
    ]
    assert set(login["responses"]["200"]["headers"]) == {"Set-Cookie"}
    assert set(session["responses"]["200"]["headers"]) == {"Set-Cookie", "X-CSRF-Token"}
    assert set(logout["responses"]["204"]["headers"]) == {"Set-Cookie"}
    assert "422" not in session["responses"] and "422" not in logout["responses"]


def test_login完整body在JSON解析前有位元組上限(tmp_path):
    """巨大 login body 由 factory middleware 固定拒絕，且不觸及帳密驗證器。"""
    path, _, _ = _建立資料庫(tmp_path)
    settings = 網頁安全設定(("http://localhost:5173",), Cookie安全=False, 工作階段有效秒數=60)
    calls = []

    def verifier(username, password):
        calls.append((username, password))
        raise AssertionError("oversized body reached verifier")

    router = 建立網頁認證路由器(網頁工作階段服務(path, 有效秒數=60), verifier, 設定=settings)
    app = 建立網頁應用程式(發布介面相依項((router,), ()), settings)
    with TestClient(app) as client:
        response = client.post(
            "/api/auth/login",
            content=b'{"username":"alice","password":"' + b"x" * 1024 + b'"}',
            headers={"Content-Type": "application/json"},
        )
    assert response.status_code == 422
    assert response.json() == {"detail": {"code": "request_invalid"}}
    assert calls == []


def test_手動cookie與header解析固定拒絕且不自動422(tmp_path):
    """缺少、巨大或 malformed transport token 只走固定 401/403 並清 cookie。"""
    client, _, _, _, _ = _建立認證客戶端(tmp_path)
    with client:
        missing = client.get("/api/auth/session")
        assert missing.status_code == 401
        assert missing.json() == {"detail": {"code": "unauthorized"}}
        assert len(missing.headers.get_list("set-cookie")) == 2
        client.cookies.set("published_web_session", "x" * 513, path="/api")
        oversized = client.get("/api/auth/session")
        assert oversized.status_code == 401
        # 前一段故意植入的超大 cookie 已完成驗收；先清除，避免它與登入新發的
        # host-only cookie 形成真實 duplicate-cookie ambiguity。Duplicate 必須 fail closed。
        client.cookies.clear()
        login = client.post(
            "/api/auth/login", json={"username": "alice", "password": "correct horse"}
        )
        session_token = login.cookies["published_web_session"]
        csrf_cookie = login.cookies["published_web_csrf"]
        malformed = client.post("/api/auth/logout", headers={"X-CSRF-Token": "bad token"})
        assert malformed.status_code == 403
        assert malformed.json() == {"detail": {"code": "csrf_invalid"}}
        assert len(malformed.headers.get_list("set-cookie")) == 2
        client.cookies.update({
            "published_web_session": session_token,
            "published_web_csrf": csrf_cookie,
        })
        valid = client.post("/api/auth/logout", headers={"X-CSRF-Token": login.json()["csrf_token"]})
        assert valid.status_code == 204
        client.cookies.update({"published_web_session": session_token})
        replay = client.post("/api/auth/logout", headers={"X-CSRF-Token": login.json()["csrf_token"]})
        assert replay.status_code == 401


def test_不同owner成功登入仍撤銷presented工作階段(tmp_path):
    """成功換帳號登入也必須撤銷瀏覽器帶來的任何有效舊 session。"""
    path, alice, _ = _建立資料庫(tmp_path)
    users = 使用者庫(path)
    bob = users.建立使用者("bob", "bob-password")
    users.連線.close()
    service = 網頁工作階段服務(path, 時鐘=lambda: 1000.0)
    alice_session = service.發行(網頁使用者(alice["id"], "alice", "admin"))
    service.發行(網頁使用者(bob["id"], "bob", "member"), alice_session.工作階段權杖)
    with pytest.raises(網頁未授權):
        service.恢復(alice_session.工作階段權杖, alice_session.CSRF權杖)


def test_發行遇session_digest碰撞會bounded重試(tmp_path):
    """session secret UNIQUE collision 會換新 pair，且最多只做 bounded attempts。"""
    path, alice, _ = _建立資料庫(tmp_path)
    user = 網頁使用者(alice["id"], "alice", "member")
    colliding = "a" * 32
    seed = 網頁工作階段服務(path, 時鐘=lambda: 1000.0, 密鑰工廠=iter((colliding, "b" * 32)).__next__).發行(user)
    values = iter((colliding, "c" * 32, "d" * 32, "e" * 32))

    issued = 網頁工作階段服務(
        path, 時鐘=lambda: 1000.0, 密鑰工廠=values.__next__,
    ).發行(user)

    assert issued.工作階段權杖 == "d" * 32
    assert issued.CSRF權杖 == "e" * 32
    assert seed.工作階段權杖 == colliding


def test_發行session_digest持續碰撞最多嘗試三次(tmp_path):
    """三次 session digest UNIQUE collision 後固定失敗且不留下半成品。"""
    path, alice, _ = _建立資料庫(tmp_path)
    user = 網頁使用者(alice["id"], "alice", "member")
    colliding = "a" * 32
    網頁工作階段服務(
        path, 時鐘=lambda: 1000.0, 密鑰工廠=iter((colliding, "b" * 32)).__next__,
    ).發行(user)
    values = iter((colliding, "1" * 32, colliding, "2" * 32, colliding, "3" * 32))
    calls = []

    def factory():
        calls.append(None)
        return next(values)

    with pytest.raises(網頁認證不可用, match="^auth_unavailable$"):
        網頁工作階段服務(path, 時鐘=lambda: 1000.0, 密鑰工廠=factory).發行(user)
    assert len(calls) == 6
    with sqlite3.connect(path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM web_sessions").fetchone()[0] == 1


def test_共用CSRF相依項手動解析輪替且OpenAPI無422(tmp_path):
    """protected mutation 成功回 successor，舊 token replay 403 且 schema 不宣告 422。"""
    path, _, _ = _建立資料庫(tmp_path)
    settings = 網頁安全設定(("http://localhost:5173",), Cookie安全=False, 工作階段有效秒數=60)
    service = 網頁工作階段服務(path, 有效秒數=60)
    router = 建立網頁認證路由器(service, 建立SQLite帳密驗證器(path), 設定=settings)
    dependency = 建立CSRF相依項(service, settings)

    def mutation(user=Depends(dependency)):
        return {"id": user.識別碼}

    router.add_api_route(
        "/mutation", mutation, methods=["POST"],
        operation_id="測試網頁認證mutation_api_auth_mutation_post",
    )
    app = 建立網頁應用程式(發布介面相依項((router,), ()), settings)
    assert "422" not in app.openapi()["paths"]["/api/auth/mutation"]["post"]["responses"]
    with TestClient(app) as client:
        assert client.post("/api/auth/mutation").status_code == 401
        login = client.post(
            "/api/auth/login", json={"username": "alice", "password": "correct horse"}
        )
        token = login.json()["csrf_token"]
        success = client.post("/api/auth/mutation", headers={"X-CSRF-Token": token})
        assert success.status_code == 200
        assert success.headers["X-CSRF-Token"] != token
        assert client.post("/api/auth/mutation", headers={"X-CSRF-Token": token}).status_code == 403


def test_canonical_auth要求設定且四前綴mutation預檢CSRF標記(tmp_path):
    """factory 在 startup 前拒絕缺 config、缺 marker 與只偽造舊 attribute 的 route。"""
    path, _, _ = _建立資料庫(tmp_path)
    settings = 網頁安全設定(("https://web.example",))
    for prefix in ("/api/auth", "/api/chat", "/api/admin", "/api/published-endpoints"):
        auth = 建立網頁認證路由器(
            網頁工作階段服務(path), 建立SQLite帳密驗證器(path), 設定=settings,
        )
        target = auth if prefix == "/api/auth" else APIRouter(prefix=prefix)

        def forged():
            return {"ok": True}

        setattr(forged, "_published_single_use_csrf", True)
        target.add_api_route("/unsafe", forged, methods=["POST"])
        routers = (auth,) if target is auth else (auth, target)
        with pytest.raises(ValueError, match="^發布介面路由設定無效$"):
            建立網頁應用程式(發布介面相依項(routers, ()), settings)
    clean_auth = 建立網頁認證路由器(
        網頁工作階段服務(path), 建立SQLite帳密驗證器(path), 設定=settings,
    )
    with pytest.raises(ValueError, match="^發布介面路由設定無效$"):
        建立應用程式(發布介面相依項((clean_auth,), ()))
