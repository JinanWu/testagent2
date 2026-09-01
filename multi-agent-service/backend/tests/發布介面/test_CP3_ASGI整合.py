"""CP3 Controller：production Web Agent composition、OpenAPI與fake smoke。"""

from __future__ import annotations

import importlib
import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from 繁中代理.使用者 import 使用者庫
from 繁中代理.發布介面.asgi import 建立ASGI應用程式, 解析環境生產設定
from 繁中代理.發布介面.資料庫 import 初始化發布介面資料庫, 載入發布介面遷移
from 繁中代理.發布介面.設定 import 生產設定


def _設定(資料庫: Path) -> 生產設定:
    """建立loopback fake-mode生產設定。"""
    return 生產設定(
        資料庫,
        ("http://localhost:5173",),
        "fake",
        "fake",
        Cookie安全=False,
        工作階段有效秒數=60,
    )


def test_canonical_asgi_import與app_factory不建立資料庫(tmp_path, monkeypatch):
    """import root asgi與明確app factory都不得建立DB或讀任意cwd。"""
    資料庫 = tmp_path / "state.sqlite3"
    monkeypatch.setenv("TESTAGENT2_DB_PATH", str(資料庫))
    monkeypatch.setenv("TESTAGENT2_WEB_ORIGINS", '["http://localhost:5173"]')
    monkeypatch.setenv("TESTAGENT2_MODEL_PROVIDER", "fake")
    模組 = importlib.import_module("asgi")
    importlib.reload(模組)
    assert not 資料庫.exists()
    應用 = 建立ASGI應用程式(_設定(資料庫))
    assert not 資料庫.exists()
    assert 應用.title == "繁中代理發布介面"


def test_環境設定缺值與非法值fail_closed(tmp_path):
    """DB、origins、provider、cookie與TTL不合法時只回固定設定錯誤。"""
    有效 = {
        "TESTAGENT2_DB_PATH": str(tmp_path / "state.sqlite3"),
        "TESTAGENT2_WEB_ORIGINS": '["http://localhost:5173"]',
        "TESTAGENT2_MODEL_PROVIDER": "fake",
        "TESTAGENT2_MODEL_NAME": "fake",
        "TESTAGENT2_COOKIE_SECURE": "false",
        "TESTAGENT2_SESSION_TTL_SECONDS": "60",
    }
    assert 解析環境生產設定(有效) == _設定(tmp_path / "state.sqlite3")
    for 覆寫 in (
        {"TESTAGENT2_DB_PATH": "relative.sqlite3"},
        {"TESTAGENT2_WEB_ORIGINS": "[]"},
        {"TESTAGENT2_WEB_ORIGINS": '["http://example.com"]'},
        {"TESTAGENT2_MODEL_PROVIDER": ""},
        {"TESTAGENT2_COOKIE_SECURE": "0"},
        {"TESTAGENT2_SESSION_TTL_SECONDS": "5000000"},
    ):
        with pytest.raises(ValueError, match="^ASGI設定無效$"):
            解析環境生產設定(有效 | 覆寫)
    for 缺少 in ("TESTAGENT2_DB_PATH", "TESTAGENT2_WEB_ORIGINS", "TESTAGENT2_MODEL_PROVIDER"):
        環境 = dict(有效)
        環境.pop(缺少)
        with pytest.raises(ValueError, match="^ASGI設定無效$"):
            解析環境生產設定(環境)


def test_環境設定有界且hostile_mapping被固定正規化(tmp_path):
    """所有get與origins parser皆在固定錯誤邊界內，control flow除外。"""
    有效 = {
        "TESTAGENT2_DB_PATH": str(tmp_path / "state.sqlite3"),
        "TESTAGENT2_WEB_ORIGINS": '["http://localhost:5173"]',
        "TESTAGENT2_MODEL_PROVIDER": "fake",
        "TESTAGENT2_MODEL_NAME": "fake",
    }
    for 來源 in ('{"x":1,"x":2}', '"' + "x" * 16_385 + '"', "[" + ",".join('"http://localhost"' for _ in range(65)) + "]", '["http://localhost/' + "x" * 2_049 + '"]'):
        with pytest.raises(ValueError, match="^ASGI設定無效$"):
            解析環境生產設定(有效 | {"TESTAGENT2_WEB_ORIGINS": 來源})

    class 敵意(dict):
        def get(self, *_args, **_kwargs):
            raise RuntimeError("SECRET_MAPPING_DIAGNOSTIC")

    with pytest.raises(ValueError, match="^ASGI設定無效$"):
        解析環境生產設定(敵意())

    class 中斷(dict):
        def get(self, *_args, **_kwargs):
            raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        解析環境生產設定(中斷())


def test_fake與Gemini環境設定為exact不可變composition(tmp_path):
    """factory一次解析model/project/location，Gemini缺值與未知provider皆fail closed。"""
    基礎 = {
        "TESTAGENT2_DB_PATH": str(tmp_path / "state.sqlite3"),
        "TESTAGENT2_WEB_ORIGINS": '["https://web.example"]',
        "TESTAGENT2_MODEL_PROVIDER": "gemini-adc",
        "TESTAGENT2_MODEL_NAME": "gemini-2.5-flash-lite",
        "AIAGENT_GCP_PROJECT": "project-a",
        "AIAGENT_GCP_LOCATION": "global",
    }
    設定 = 解析環境生產設定(基礎)
    assert (設定.模型名稱, 設定.Gemini專案識別碼, 設定.Gemini位置) == (
        "gemini-2.5-flash-lite", "project-a", "global",
    )
    for 移除 in ("AIAGENT_GCP_PROJECT", "AIAGENT_GCP_LOCATION"):
        環境 = dict(基礎); 環境.pop(移除)
        with pytest.raises(ValueError, match="^ASGI設定無效$"):
            解析環境生產設定(環境)
    with pytest.raises(ValueError, match="^ASGI設定無效$"):
        解析環境生產設定(基礎 | {"TESTAGENT2_MODEL_PROVIDER": "other"})


@pytest.mark.parametrize("先初始化", [False, True])
def test_canonical_migration_fresh預先遷移與restart_exact_manifest(tmp_path, 先初始化):
    """startup只使用canonical manifest，fresh/pre-migrated/restart ledger皆逐項一致。"""
    資料庫 = tmp_path / "migration.sqlite3"
    if 先初始化:
        初始化發布介面資料庫(資料庫)
    預期 = tuple((項目.版本, 項目.名稱) for 項目 in 載入發布介面遷移())
    應用 = 建立ASGI應用程式(_設定(資料庫))
    for _ in range(2):
        with TestClient(應用):
            pass
        with sqlite3.connect(資料庫) as 連線:
            實際 = tuple(連線.execute("SELECT version,name FROM published_api_schema_migrations ORDER BY version"))
        assert 實際 == 預期


def test_CP3_OpenAPI_exact_production_inventory(tmp_path):
    """只公開health/auth/chat/sessions/skills，不掛載management fake routes。"""
    應用 = 建立ASGI應用程式(_設定(tmp_path / "openapi.sqlite3"))
    規格 = 應用.openapi()
    預期 = {
        ("/healthz", "get"): ("取得健康狀態_healthz_get", {"200"}),
        ("/api/auth/login", "post"): ("登入網頁認證工作階段_api_auth_login_post", {"200", "401", "422", "503"}),
        ("/api/auth/session", "get"): ("取得網頁認證工作階段_api_auth_session_get", {"200", "401", "503"}),
        ("/api/auth/me", "get"): ("取得目前網頁認證使用者_api_auth_me_get", {"200", "401", "503"}),
        ("/api/auth/logout", "post"): ("登出網頁認證工作階段_api_auth_logout_post", {"204", "401", "403", "503"}),
        ("/api/chat", "post"): ("聊天_api_chat_post", {"200", "400", "404", "422", "503"}),
        ("/api/sessions", "get"): ("列出工作階段_api_sessions_get", {"200", "400", "422", "503"}),
        ("/api/sessions/{session_id}", "get"): ("讀取工作階段_api_sessions__session_id__get", {"200", "400", "404", "422", "503"}),
        ("/api/skills", "get"): ("列出技能_api_skills_get", {"200", "503"}),
        ("/api/skills/{skill_id}", "get"): ("讀取技能_api_skills__skill_id__get", {"200", "400", "404", "422", "503"}),
    }
    實際 = {}
    for 路徑, 路徑項目 in 規格["paths"].items():
        for 方法, 項目 in 路徑項目.items():
            if 方法 in {"get", "post"}:
                實際[(路徑, 方法)] = (項目["operationId"], set(項目["responses"]))
    assert 實際 == 預期
    assert all(not 路徑.startswith(("/api/admin", "/api/published-endpoints", "/v1/endpoints")) for 路徑 in 規格["paths"])


def test_CP3_OpenAPI成功模型逐欄strict且巢狀型別完整(tmp_path):
    """五個成功操作公開required/type/nested/additionalProperties契約。"""
    規格 = 建立ASGI應用程式(_設定(tmp_path / "schema.sqlite3")).openapi()
    schemas = 規格["components"]["schemas"]

    def 解析(schema):
        return schemas[schema["$ref"].rsplit("/", 1)[1]] if "$ref" in schema else schema

    def 成功(path, method):
        return 解析(規格["paths"][path][method]["responses"]["200"]["content"]["application/json"]["schema"])

    def strict(schema, required, types):
        assert schema["additionalProperties"] is False
        assert set(schema["required"]) == set(required)
        assert {name: schema["properties"][name].get("type") for name in types} == types

    chat = 成功("/api/chat", "post")
    strict(chat, {"session_id", "reply"}, {"session_id": "string"})
    strict(解析(chat["properties"]["reply"]), {"role", "content"}, {"role": "string", "content": "string"})

    sessions = 成功("/api/sessions", "get")
    strict(sessions, {"sessions"}, {"sessions": "array"})
    strict(解析(sessions["properties"]["sessions"]["items"]),
           {"id", "title", "updated_at", "message_count"},
           {"id": "string", "title": "string", "updated_at": "number", "message_count": "integer"})

    detail = 成功("/api/sessions/{session_id}", "get")
    strict(detail, {"session", "messages"}, {"messages": "array"})
    strict(解析(detail["properties"]["session"]), {"id", "title", "updated_at"},
           {"id": "string", "title": "string", "updated_at": "number"})
    strict(解析(detail["properties"]["messages"]["items"]), {"role", "content"},
           {"role": "string", "content": "string"})

    skills = 成功("/api/skills", "get")
    strict(skills, {"skills"}, {"skills": "array"})
    skill_fields = {"id": "string", "name": "string", "category": "string", "description": "string"}
    strict(解析(skills["properties"]["skills"]["items"]), set(skill_fields), skill_fields)
    skill = 成功("/api/skills/{skill_id}", "get")
    strict(skill, {*skill_fields, "content"}, skill_fields | {"content": "string"})


def test_fake_provider_clean_smoke_login_chat_session_resume_new與skills(tmp_path):
    """真實SQLite與fake runtime完成CP3 browser垂直流程。"""
    資料庫 = tmp_path / "smoke.sqlite3"
    技能根 = tmp_path / "skills"
    技能目錄 = 技能根 / "demo"
    技能目錄.mkdir(parents=True)
    (技能目錄 / "SKILL.md").write_text(
        "---\nname: demo\ndescription: deterministic demo\n---\n\n# Demo\n",
        encoding="utf-8",
    )
    應用 = 建立ASGI應用程式(_設定(資料庫))
    assert not 資料庫.exists()
    with TestClient(應用) as 客戶端:
        assert 資料庫.exists()
        使用者 = 使用者庫(資料庫)
        使用者.建立使用者(
            "alice",
            "correct horse",
            roles=["user"],
            enabled_tools=[],
            enabled_skills=["demo"],
            skill_roots=[str(技能根)],
            allowed_workdirs=[str(tmp_path)],
        )
        使用者.連線.close()
        登入 = 客戶端.post("/api/auth/login", json={"username": "alice", "password": "correct horse"})
        assert 登入.status_code == 200
        csrf = 登入.json()["csrf_token"]

        新對話 = 客戶端.post("/api/chat", json={"message": "hello"}, headers={"X-CSRF-Token": csrf})
        assert 新對話.status_code == 200
        根識別碼 = 新對話.json()["session_id"]
        assert 新對話.json()["reply"] == {"role": "assistant", "content": "假模型回覆：我可以運作。"}
        csrf = 新對話.headers["X-CSRF-Token"]

        列表 = 客戶端.get("/api/sessions")
        assert 列表.status_code == 200
        assert [項目["id"] for 項目 in 列表.json()["sessions"]] == [根識別碼]
        詳情 = 客戶端.get(f"/api/sessions/{根識別碼}")
        assert 詳情.status_code == 200
        assert 詳情.json()["session"]["id"] == 根識別碼
        assert [項目["role"] for 項目 in 詳情.json()["messages"]] == ["user", "assistant"]

        恢復 = 客戶端.post(
            "/api/chat",
            json={"message": "resume", "session_id": 根識別碼},
            headers={"X-CSRF-Token": csrf},
        )
        assert 恢復.status_code == 200 and 恢復.json()["session_id"] == 根識別碼
        csrf = 恢復.headers["X-CSRF-Token"]
        第二對話 = 客戶端.post("/api/chat", json={"message": "new"}, headers={"X-CSRF-Token": csrf})
        assert 第二對話.status_code == 200
        assert 第二對話.json()["session_id"] != 根識別碼

        技能列表 = 客戶端.get("/api/skills")
        assert 技能列表.status_code == 200
        assert [項目["id"] for 項目 in 技能列表.json()["skills"]] == ["demo"]
        技能詳情 = 客戶端.get("/api/skills/demo")
        assert 技能詳情.status_code == 200
        assert 技能詳情.json()["id"] == "demo"
        assert "# Demo" in 技能詳情.json()["content"]
