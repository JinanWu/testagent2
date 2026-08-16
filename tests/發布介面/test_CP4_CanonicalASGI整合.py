"""A08-2 root ASGI factory 與 canonical live OpenAPI 整合測試。"""

from __future__ import annotations

import base64
import json
import os
from pathlib import Path

from fastapi.testclient import TestClient

import asgi as root_asgi
from production_spa_support import 建立ProductionDist


_預期路由清單 = {
    "/api/admin/endpoints/{endpoint_id}/invocations": ("get",),
    "/api/admin/endpoints/{endpoint_id}/invocations/{invocation_id}": ("get",),
    "/api/auth/login": ("post",),
    "/api/auth/logout": ("post",),
    "/api/auth/me": ("get",),
    "/api/auth/session": ("get",),
    "/api/chat": ("post",),
    "/api/published-endpoints/{endpoint_id}/credentials": ("get", "post"),
    "/api/published-endpoints/{endpoint_id}/credentials/{credential_id}/revoke": ("post",),
    "/api/published-endpoints/{endpoint_id}/metrics": ("get",),
    "/api/published-endpoints/{endpoint_id}/diagnostics": ("get",),
    "/api/published-endpoints/draft": ("post",),
    "/api/sessions": ("get",),
    "/api/sessions/{session_id}": ("get",),
    "/api/skills": ("get",),
    "/api/skills/{skill_id}": ("get",),
    "/healthz": ("get",),
    "/v1/endpoints/{slug}/invoke": ("post",),
}


def _設定Canonical環境(tmp_path: Path, monkeypatch) -> None:
    """設定 root factory 唯一核准的 production environment。"""
    技能根 = tmp_path / "bundles"
    技能根.mkdir()
    Dist根 = 建立ProductionDist(tmp_path)
    環境 = {
        "TESTAGENT2_DB_PATH": str(tmp_path / "web.sqlite3"),
        "TESTAGENT2_PUBLISHED_DB_PATH": str(tmp_path / "published.sqlite3"),
        "TESTAGENT2_PUBLISHED_BUNDLE_ROOT": str(技能根),
        "TESTAGENT2_WEB_DIST_ROOT": str(Dist根),
        "TESTAGENT2_WEB_ORIGINS": '["https://client.example"]',
        "TESTAGENT2_MODEL_NAME": "gemini-2.5-flash-lite",
        "AIAGENT_GCP_PROJECT": "example-project",
        "AIAGENT_GCP_LOCATION": "global",
        "TESTAGENT2_PUBLISHED_CREDENTIAL_ACTIVE_KEY_VERSION": "1",
        "TESTAGENT2_PUBLISHED_CREDENTIAL_KEYS_JSON": json.dumps({
            "1": base64.urlsafe_b64encode(b"A" * 32).rstrip(b"=").decode("ascii"),
        }, separators=(",", ":")),
        "TESTAGENT2_OWNER_OBSERVABILITY_CURSOR_KEY": base64.urlsafe_b64encode(
            b"O" * 32
        ).rstrip(b"=").decode("ascii"),
    }
    for 名稱 in tuple(os.environ):
        if 名稱.startswith("TESTAGENT2_") or 名稱.startswith("AIAGENT_"):
            monkeypatch.delenv(名稱, raising=False)
    for 名稱, 值 in 環境.items():
        monkeypatch.setenv(名稱, 值)


def test_root_factory啟動lifespan並公開唯一stable_POST與既有CP3路由(tmp_path: Path, monkeypatch):
    """部署 root factory 即取得完整 Controller inventory，且 stable URL 不重導。"""
    _設定Canonical環境(tmp_path, monkeypatch)
    應用 = root_asgi.建立應用程式()
    assert 應用.router.redirect_slashes is False

    with TestClient(應用, raise_server_exceptions=False) as 客戶端:
        assert 客戶端.get("/healthz").json() == {"status": "ok"}
        規格回應 = 客戶端.get("/openapi.json")
        assert 規格回應.status_code == 200
        規格 = 規格回應.json()
        實際路由 = {路徑: tuple(sorted(操作)) for 路徑, 操作 in 規格["paths"].items()}
        assert 實際路由 == _預期路由清單

        Stable操作 = 規格["paths"]["/v1/endpoints/{slug}/invoke"]
        assert tuple(Stable操作) == ("post",)
        assert Stable操作["post"]["requestBody"] == {
            "required": True,
            "content": {"application/json": {"schema": {
                "type": "object",
                "required": ["input"],
                "additionalProperties": False,
                "properties": {
                    "input": {},
                    "session_id": {
                        "anyOf": [{"type": "string", "maxLength": 128}, {"type": "null"}],
                        "x-utf8-max-bytes": 128,
                        "description": "Optional Published session identifier；上限 128 UTF-8 bytes。",
                    },
                    "metadata": {"anyOf": [{"type": "object"}, {"type": "null"}]},
                },
            }}},
        }
        assert 客戶端.get("/v1/endpoints/demo/invoke").status_code == 405
        assert 客戶端.post(
            "/v1/endpoints/demo/invoke/", follow_redirects=False,
            headers={"Authorization": "Bearer example"}, json={"input": {}},
        ).status_code == 404
