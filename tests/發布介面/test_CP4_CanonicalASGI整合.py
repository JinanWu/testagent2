"""A08-2 root ASGI factory 與 canonical live OpenAPI 整合測試。"""

from __future__ import annotations

import base64
import json
import os
from pathlib import Path

from fastapi.testclient import TestClient

import asgi as root_asgi
from production_spa_support import 建立ProductionDist
from 繁中代理.使用者 import 使用者庫
from 繁中代理.模型供應商 import GeminiADC供應商
from 繁中代理.發布介面.執行期.模型契約 import 模型回應快照


_預期路由清單 = {
    "/api/admin/published-endpoints/{endpoint_id}/invocations/{invocation_id}/redactions": ("post",),
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
    "/api/published-endpoints": ("get", "post"),
    "/api/published-endpoints/{endpoint_id}": ("get",),
    "/api/published-endpoints/{endpoint_id}/versions": ("post",),
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


def test_root_factory真session_CSRF與Gemini邊界可建立Draft而非planner_unavailable(tmp_path: Path, monkeypatch):
    """從部署root走完整管理鏈，只替換最外層Gemini網路邊界。"""
    _設定Canonical環境(tmp_path, monkeypatch)
    技能根 = tmp_path / "skills"
    (技能根 / "alpha").mkdir(parents=True)
    (技能根 / "alpha" / "SKILL.md").write_text(
        "---\nname: alpha\ndescription: Alpha skill\n---\n# Alpha\n",
        encoding="utf-8",
    )
    使用者們 = 使用者庫(tmp_path / "web.sqlite3")
    try:
        使用者們.建立使用者(
            "alice", "correct horse", enabled_tools=["skills_list"],
            enabled_skills=["alpha"], skill_roots=[str(技能根)],
            allowed_workdirs=[str(技能根)],
        )
    finally:
        使用者們.連線.close()

    規劃結果 = {
        "endpoint_name": "Alpha API", "suggested_slug": "alpha-api",
        "behavior_summary": "建立 Alpha API", "selected_skills": ["alpha"],
        "recommended_tools": ["skills_list"],
        "tool_capabilities": {"skills_list": "列出技能"},
        "system_prompt": "只使用授權技能回應。", "input_schema": None,
        "response_schema": {
            "type": "object", "properties": {"result": {"type": "string"}},
            "required": ["result"], "additionalProperties": False,
        },
        "human_docs": "Alpha API 文件",
        "rate_limit": {"endpoint_per_minute": 60, "credential_per_minute": 30},
        "warnings": [],
    }

    def 假Gemini網路回應(self, **_參數):
        return 模型回應快照(
            json.dumps(規劃結果, ensure_ascii=False, separators=(",", ":")),
            "stop", {}, [],
        )

    monkeypatch.setattr(GeminiADC供應商, "產生發布回應", 假Gemini網路回應)
    應用 = root_asgi.建立應用程式()
    with TestClient(應用, base_url="https://client.example", raise_server_exceptions=False) as 客戶端:
        登入 = 客戶端.post(
            "/api/auth/login", json={"username": "alice", "password": "correct horse"},
        )
        assert 登入.status_code == 200, 登入.text
        Owner列表 = 客戶端.get(
            "/api/published-endpoints",
            headers={"x-user-id": "attacker", "x-admin": "true"},
        )
        assert Owner列表.status_code == 200
        assert Owner列表.json() == {"items": [], "next_cursor": None}
        回應 = 客戶端.post(
            "/api/published-endpoints/draft",
            headers={"X-CSRF-Token": 登入.json()["csrf_token"]},
            json={
                "original_requirement_text": "建立 Alpha API",
                "selected_skills": ["alpha"],
                "response_mode": "structured",
            },
        )

    assert 回應.status_code == 201, 回應.text
    assert set(回應.json()) == {"draft_id", "expires_at", "preview"}
    assert 回應.json()["preview"]["selected_skills"] == ["alpha"]
