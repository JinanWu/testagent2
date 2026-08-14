"""A18方案一：canonical ASGI直接托管production-built SPA。"""

from __future__ import annotations

import base64
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from 繁中代理.發布介面 import asgi as asgi模組
from 繁中代理.發布介面.asgi import 建立CP4SPAASGI應用程式
from 繁中代理.發布介面.生產Published執行 import Published生產設定
from 繁中代理.發布介面.生產SPA import ProductionSPA設定
from 繁中代理.發布介面.設定 import 生產設定


_HTML = b'<!doctype html><html><body><div id="root"></div><script type="module" src="/assets/app-ABCDEFGH.js"></script><link rel="stylesheet" href="/assets/app-ABCDEFGH.css"></body></html>'
_JS = b'document.querySelector("#root").textContent="production"'
_CSS = b'body{margin:0}'
_安全標頭 = {
    "content-security-policy": "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'",
    "x-content-type-options": "nosniff",
    "referrer-policy": "no-referrer",
    "x-frame-options": "DENY",
    "permissions-policy": "camera=(), microphone=(), geolocation=()",
}


def _建立dist(tmp_path: Path, *, index: bytes = _HTML) -> Path:
    """建立最小Vite production artifact，不使用dev server。"""
    dist = tmp_path / "dist"
    assets = dist / "assets"
    assets.mkdir(parents=True)
    (dist / "index.html").write_bytes(index)
    (assets / "app-ABCDEFGH.js").write_bytes(_JS)
    (assets / "app-ABCDEFGH.css").write_bytes(_CSS)
    return dist


def _建立應用(tmp_path: Path, dist: Path):
    """建立真canonical Web／Published lifespan与production SPA composition。"""
    bundle_root = tmp_path / "bundles"
    bundle_root.mkdir(exist_ok=True)
    web = 生產設定(
        (tmp_path / "web.sqlite3").resolve(), ("http://localhost:5173",), "fake", "fake",
        Cookie安全=False, 工作階段有效秒數=60,
    )
    published = Published生產設定(
        (tmp_path / "published.sqlite3").resolve(), bundle_root.resolve(),
        lambda _工具庫: None, lambda: {"fake": object()},
    )
    return 建立CP4SPAASGI應用程式(web, published, ProductionSPA設定(dist))


def _建立Production環境(tmp_path: Path) -> dict[str, str]:
    """建立root factory需要的完整非敏感测试环境。"""
    return {
        "TESTAGENT2_DB_PATH": str(tmp_path / "web.sqlite3"),
        "TESTAGENT2_PUBLISHED_DB_PATH": str(tmp_path / "published.sqlite3"),
        "TESTAGENT2_PUBLISHED_BUNDLE_ROOT": str(tmp_path / "bundles"),
        "TESTAGENT2_WEB_ORIGINS": '["https://client.example"]',
        "TESTAGENT2_MODEL_NAME": "gemini-2.5-flash-lite",
        "AIAGENT_GCP_PROJECT": "example-project",
        "AIAGENT_GCP_LOCATION": "global",
        "TESTAGENT2_PUBLISHED_CREDENTIAL_ACTIVE_KEY_VERSION": "1",
        "TESTAGENT2_PUBLISHED_CREDENTIAL_KEYS_JSON": json.dumps({
            "1": base64.urlsafe_b64encode(b"J" * 32).rstrip(b"=").decode("ascii"),
        }, separators=(",", ":")),
    }


def test_SPA設定只接受absolute_lexical路徑(tmp_path: Path):
    """Dist authority必须明示，不得使用cwd fallback或父层穿越。"""
    with pytest.raises(ValueError, match="^Production SPA設定無效$"):
        ProductionSPA設定(Path("apps/web-app/dist"))
    with pytest.raises(ValueError, match="^Production SPA設定無效$"):
        ProductionSPA設定(tmp_path / ".." / "dist")



def test_missing_dist在lifespan啟動固定fail_closed且construction零IO(tmp_path: Path):
    """App construction不碰FS；server startup在任何request前固定拒绝missing artifact。"""
    dist = (tmp_path / "missing-dist").resolve()
    app = _建立應用(tmp_path, dist)
    assert not dist.exists()
    with pytest.raises(RuntimeError, match="^發布介面啟動失敗$"):
        with TestClient(app):
            pass


@pytest.mark.parametrize(
    "破壞",
    (
        "missing-index", "source-entry", "missing-asset", "asset-symlink", "root-symlink",
        "nested-assets", "unhashed-asset", "invalid-html",
    ),
)
def test_損壞或alias_dist在startup固定fail_closed(tmp_path: Path, 破壞: str):
    """缺入口、dev source、遗失引用或symlink asset都不得启动。"""
    dist = _建立dist(tmp_path)
    if 破壞 == "missing-index":
        (dist / "index.html").unlink()
    elif 破壞 == "source-entry":
        (dist / "index.html").write_text(
            '<div id="root"></div><script type="module" src="/src/main.tsx"></script>',
            encoding="utf-8",
        )
    elif 破壞 == "missing-asset":
        (dist / "assets/app-ABCDEFGH.js").unlink()
    elif 破壞 == "asset-symlink":
        asset = dist / "assets/app-ABCDEFGH.js"
        asset.unlink()
        asset.symlink_to(dist / "index.html")
    elif 破壞 == "root-symlink":
        真dist = dist
        dist = tmp_path / "linked-dist"
        dist.symlink_to(真dist, target_is_directory=True)
    elif 破壞 == "nested-assets":
        (dist / "assets/nested").mkdir()
    elif 破壞 == "unhashed-asset":
        (dist / "assets/runtime.js").write_bytes(b"export default 1")
    else:
        (dist / "index.html").write_bytes(b"\xff")
    app = _建立應用(tmp_path, dist)
    with pytest.raises(RuntimeError, match="^發布介面啟動失敗$"):
        with TestClient(app):
            pass


def test_production_SPA同源服務deep_link_assets與安全cache_headers(tmp_path: Path):
    """Production HTML、deep link与hashed assets经同一canonical ASGI提供。"""
    dist = _建立dist(tmp_path)
    app = _建立應用(tmp_path, dist)
    with TestClient(app, raise_server_exceptions=False) as client:
        for path in ("/", "/admin/invocations"):
            response = client.get(path)
            assert response.status_code == 200 and response.content == _HTML
            assert response.headers["content-type"].startswith("text/html")
            assert response.headers["cache-control"] == "no-store"
            assert {鍵: response.headers[鍵] for 鍵 in _安全標頭} == _安全標頭
        js = client.get("/assets/app-ABCDEFGH.js")
        assert js.status_code == 200 and js.content == _JS
        assert js.headers["content-type"].startswith("text/javascript")
        assert js.headers["cache-control"] == "public, max-age=31536000, immutable"
        assert {鍵: js.headers[鍵] for 鍵 in _安全標頭} == _安全標頭
        head = client.head("/admin/invocations")
        assert head.status_code == 200 and head.content == b""
        assert head.headers["content-length"] == str(len(_HTML))


def test_backend與API_404永不被SPA_fallback吞掉(tmp_path: Path):
    """Backend routes优先；unknown API/assets及非读取method不回index HTML。"""
    app = _建立應用(tmp_path, _建立dist(tmp_path))
    with TestClient(app, raise_server_exceptions=False) as client:
        assert client.get("/healthz").json() == {"status": "ok"}
        OpenAPI路徑 = client.get("/openapi.json").json()["paths"]
        assert "/api/auth/login" in OpenAPI路徑
        assert "/" not in OpenAPI路徑
        assert "/{frontend_path}" not in OpenAPI路徑
        assert not any(路徑.startswith("/assets") for 路徑 in OpenAPI路徑)
        for 已知錯誤方法 in ("/api/auth/login", "/v1/endpoints/demo/invoke"):
            回應 = client.get(已知錯誤方法)
            assert 回應.status_code == 405
            assert 回應.headers["content-type"].startswith("application/json")
            assert "POST" in 回應.headers["allow"]
        for path in (
            "/api/not-a-route", "/v1/not-a-route", "/assets", "/%61ssets",
            "/assets/missing.js",
            "/assets/%2e%2e/index.html", "/api/%2e%2e/private", "/v1/%2e%2e/private",
        ):
            response = client.get(path)
            assert response.status_code == 404
            assert response.headers["content-type"].startswith("application/json")
            assert b"<div id=\"root\">" not in response.content
        mutation = client.post("/admin/invocations")
        assert mutation.status_code == 404
        assert b"<div id=\"root\">" not in mutation.content
        for path in ("/unknown", "/api/unknown", "/assets/missing.js"):
            response = client.request("PROPFIND", path)
            assert response.status_code == 404
            assert response.headers["content-type"].startswith("application/json")
            assert b"<div id=\"root\">" not in response.content


def test_startup快照不可被後續磁碟變更且shutdown清除authority(tmp_path: Path):
    """Startup snapshot消除TOCTOU；shutdown后不保留可发布artifact。"""
    dist = _建立dist(tmp_path)
    app = _建立應用(tmp_path, dist)
    client = TestClient(app, raise_server_exceptions=False)
    with client:
        (dist / "index.html").write_bytes(b"MUTATED_DISK_MARKER")
        (dist / "assets/app-ABCDEFGH.js").write_bytes(b"MUTATED_ASSET_MARKER")
        assert client.get("/").content == _HTML
        assert client.get("/assets/app-ABCDEFGH.js").content == _JS
    response = client.get("/")
    assert response.status_code == 503
    assert b"MUTATED" not in response.content and _HTML not in response.content


def test_production環境dist路徑必填且root_factory組成SPA(tmp_path: Path, monkeypatch):
    """Root uvicorn factory唯一消费exact absolute dist authority。"""
    env = _建立Production環境(tmp_path)
    with pytest.raises(ValueError, match="^Production環境設定無效$"):
        asgi模組.解析Production環境設定(env)
    env["TESTAGENT2_WEB_DIST_ROOT"] = "relative-dist"
    with pytest.raises(ValueError, match="^Production環境設定無效$"):
        asgi模組.解析Production環境設定(env)
    dist = _建立dist(tmp_path)
    env["TESTAGENT2_WEB_DIST_ROOT"] = str(dist.resolve())
    (tmp_path / "bundles").mkdir()
    web, published, spa = asgi模組.解析Production環境設定(env)
    assert spa == ProductionSPA設定(dist.resolve())
    monkeypatch.setattr(asgi模組.os, "environ", env)
    app = asgi模組.建立環境應用程式()
    with TestClient(app, raise_server_exceptions=False) as client:
        assert client.get("/admin/invocations").content == _HTML
    del web, published
