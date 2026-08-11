"""A3-03：把安全草稿路由掛進 Canonical Controller App。

驗證 ``建立安全草稿路由器()`` 以真 Current Session／Single-use CSRF dependencies 掛在
canonical app 上，只公開 exact ``POST /api/published-endpoints/draft``，request 契約只有
三個鍵，且絕不掛載帶 ``planner_content`` 的 legacy 草稿 route。
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from 繁中代理.使用者 import 使用者庫
from 繁中代理.工具 import 工具定義
from 繁中代理.發布介面.asgi import 建立CP4ASGI應用程式
from 繁中代理.發布介面.執行期.工具發布庫 import 工具發布庫, 工具發布描述, 工具發布註冊
from 繁中代理.發布介面.生產Published執行 import (
    Published生產設定, 生產Controller建構器,
)
from 繁中代理.發布介面.生產Published管理 import Planner生產設定, 延遲草稿規劃服務
from 繁中代理.發布介面.生產組裝 import 建立生產應用程式
from 繁中代理.發布介面.規劃.規劃器供應商 import 決定性假規劃器
from 繁中代理.發布介面.設定 import 生產設定

_草稿路徑 = "/api/published-endpoints/draft"
_技能名稱 = "alpha"
_帳號 = "owner"
_密碼 = "correct horse battery"
_發布識別 = "release-1"


def _工具發布描述() -> 工具發布描述:
    """建立 startup 安裝的固定 pinned release。"""
    return 工具發布描述(_發布識別, (工具發布註冊("revision-1", 工具定義(
        "alpha-tool", "Alpha tool",
        {"type": "object", "properties": {}, "additionalProperties": False},
        lambda _參數: "ok",
    )),))


def _建立技能與使用者(tmp_path: Path) -> Path:
    """建立技能來源與具授權的真實 Web 使用者。"""
    技能根 = tmp_path / "skills"
    (技能根 / _技能名稱).mkdir(parents=True)
    (技能根 / _技能名稱 / "SKILL.md").write_text(
        "---\nname: alpha\ndescription: Alpha skill\n---\n# Alpha\n", encoding="utf-8",
    )
    使用者庫物件 = 使用者庫(tmp_path / "web.sqlite3")
    使用者庫物件.建立使用者(
        _帳號, _密碼, roles=["admin"], enabled_tools=["alpha-tool"],
        enabled_skills=[_技能名稱], skill_roots=[str(技能根)],
        allowed_workdirs=[str(技能根)],
    )
    使用者庫物件.連線.close()
    return 技能根


def _建立設定(tmp_path: Path, 事件: list[str] | None = None):
    """建立含 Planner 組裝的 exact CP3 與 CP4 生產設定。"""
    紀錄 = 事件 if 事件 is not None else []
    套件根 = tmp_path / "bundles"
    套件根.mkdir(exist_ok=True)
    網頁設定 = 生產設定(
        tmp_path / "web.sqlite3", ("http://localhost:5173",), "fake", "fake",
        Cookie安全=False, 工作階段有效秒數=3600,
    )

    def 安裝工具(儲存庫: 工具發布庫) -> None:
        """在 startup 安裝 exact pinned release 一次。"""
        紀錄.append("工具安裝")
        儲存庫.登錄發布(_工具發布描述())

    def 模型表():
        """在 startup 建立 detached model registry 一次。"""
        紀錄.append("模型安裝")
        return {"fake": object()}

    def 權威來源(路徑: Path):
        """以 startup 傳入的 Web DB 路徑建立真實權威來源。"""
        紀錄.append("權威來源")
        return 使用者庫(路徑)

    def 規劃器():
        """建立 fake 模式的決定性 Planner。"""
        紀錄.append("規劃器")
        return 決定性假規劃器()

    發布設定 = Published生產設定(
        tmp_path / "published.sqlite3", 套件根, 安裝工具, 模型表, 60.0,
        Planner設定=Planner生產設定(_發布識別, 權威來源, 規劃器, 3600.0),
    )
    return 網頁設定, 發布設定, 紀錄


def _建立應用(tmp_path: Path, 事件: list[str] | None = None):
    """建立 canonical CP4 Controller 應用程式。"""
    網頁設定, 發布設定, 紀錄 = _建立設定(tmp_path, 事件)
    return 建立CP4ASGI應用程式(網頁設定, 發布設定), 紀錄


def _登入(客戶端: TestClient) -> str:
    """以真實帳密取得 cookie session 與首枚 single-use CSRF。"""
    回應 = 客戶端.post(
        "/api/auth/login", json={"username": _帳號, "password": _密碼},
    )
    assert 回應.status_code == 200, 回應.text
    return 回應.json()["csrf_token"]


def _草稿本文() -> dict:
    """建立唯一合法的三鍵 request body。"""
    return {
        "original_requirement_text": "建立 Alpha API",
        "selected_skills": [_技能名稱],
        "response_mode": "structured",
    }


# ---------------------------------------------------------------------------
# Route Inventory
# ---------------------------------------------------------------------------


def test_canonical_app_route_inventory含草稿路由(tmp_path):
    """canonical live OpenAPI 必須公開 exact 草稿 route，且只有 POST。"""
    tmp_path = tmp_path.resolve()
    _建立技能與使用者(tmp_path)
    應用, _ = _建立應用(tmp_path)

    路徑們 = 應用.openapi()["paths"]

    assert _草稿路徑 in 路徑們, (
        "canonical Controller App 未掛載安全草稿 route："
        "建立安全草稿路由器() 已實作但未接入生產組裝"
    )
    assert tuple(路徑們[_草稿路徑]) == ("post",)


def test_草稿路由未配置Planner時不公開(tmp_path):
    """未配置 Planner 組裝的部署不得公開無法服務的草稿 route。"""
    tmp_path = tmp_path.resolve()
    套件根 = tmp_path / "bundles"
    套件根.mkdir()
    網頁設定 = 生產設定(
        tmp_path / "web.sqlite3", ("http://localhost:5173",), "fake", "fake",
        Cookie安全=False, 工作階段有效秒數=3600,
    )
    發布設定 = Published生產設定(
        tmp_path / "published.sqlite3", 套件根, lambda _儲存庫: None,
        lambda: {"fake": object()}, 60.0,
    )

    路徑們 = 建立CP4ASGI應用程式(網頁設定, 發布設定).openapi()["paths"]

    assert _草稿路徑 not in 路徑們


# ---------------------------------------------------------------------------
# Exact POST／405
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("方法", ["get", "put", "patch", "delete"])
def test_草稿路由只接受POST其餘方法405(tmp_path, 方法: str):
    """exact method 契約：非 POST 一律 405，且不觸及服務。"""
    tmp_path = tmp_path.resolve()
    _建立技能與使用者(tmp_path)
    應用, _ = _建立應用(tmp_path)

    with TestClient(應用) as 客戶端:
        _登入(客戶端)
        回應 = getattr(客戶端, 方法)(_草稿路徑)

    assert 回應.status_code == 405


def test_草稿路由POST在真Session與CSRF下建立草稿(tmp_path):
    """完整合法請求必須回 201 與 exact response 三鍵。"""
    tmp_path = tmp_path.resolve()
    _建立技能與使用者(tmp_path)
    應用, _ = _建立應用(tmp_path)

    with TestClient(應用) as 客戶端:
        csrf = _登入(客戶端)
        回應 = 客戶端.post(
            _草稿路徑, json=_草稿本文(), headers={"X-CSRF-Token": csrf},
        )

    assert 回應.status_code == 201, 回應.text
    assert set(回應.json()) == {"draft_id", "expires_at", "preview"}


# ---------------------------------------------------------------------------
# 真 Session＋CSRF Principal 一致
# ---------------------------------------------------------------------------


def test_缺Session或CSRF皆在服務前關閉(tmp_path):
    """未登入 401；有 session 但缺／錯 CSRF 403，兩者都不得建立草稿。"""
    tmp_path = tmp_path.resolve()
    _建立技能與使用者(tmp_path)
    應用, _ = _建立應用(tmp_path)

    with TestClient(應用) as 客戶端:
        無工作階段 = 客戶端.post(_草稿路徑, json=_草稿本文())
        assert 無工作階段.status_code == 401
        assert 無工作階段.json() == {"detail": {"code": "unauthorized"}}

        csrf = _登入(客戶端)
        缺CSRF = 客戶端.post(_草稿路徑, json=_草稿本文())
        assert 缺CSRF.status_code == 403
        assert 缺CSRF.json() == {"detail": {"code": "csrf_invalid"}}

        錯CSRF = 客戶端.post(
            _草稿路徑, json=_草稿本文(), headers={"X-CSRF-Token": "x" * 43},
        )
        assert 錯CSRF.status_code == 403

        成功 = 客戶端.post(
            _草稿路徑, json=_草稿本文(), headers={"X-CSRF-Token": csrf},
        )
        assert 成功.status_code == 201, 成功.text


def test_草稿擁有者取自真Session而非客戶端(tmp_path):
    """草稿必須綁定 session principal；客戶端無從指定擁有者。"""
    tmp_path = tmp_path.resolve()
    _建立技能與使用者(tmp_path)
    應用, _ = _建立應用(tmp_path)
    使用者庫物件 = 使用者庫(tmp_path / "web.sqlite3")
    預期擁有者 = str(使用者庫物件.讀取使用者(username=_帳號)["id"])
    使用者庫物件.連線.close()
    觀察: list[tuple] = []

    with TestClient(應用) as 客戶端:
        代理 = 應用.state.發布介面資源[1].取得Planner資源()._代理
        原始 = type(代理).建立草稿

        def 記錄(自身, 擁有者識別碼, *參數, **選項):
            """記錄 route 實際傳入的擁有者身份。"""
            觀察.append((擁有者識別碼, 參數))
            return 原始(自身, 擁有者識別碼, *參數, **選項)

        代理.建立草稿 = 記錄.__get__(代理, type(代理))
        csrf = _登入(客戶端)
        回應 = 客戶端.post(
            _草稿路徑, json=_草稿本文(), headers={"X-CSRF-Token": csrf},
        )

    assert 回應.status_code == 201, 回應.text
    assert [項目[0] for 項目 in 觀察] == [預期擁有者]


def test_單次CSRF在草稿路由輪替(tmp_path):
    """草稿 route 消耗單次 CSRF 後必須回傳接續權杖。"""
    tmp_path = tmp_path.resolve()
    _建立技能與使用者(tmp_path)
    應用, _ = _建立應用(tmp_path)

    with TestClient(應用) as 客戶端:
        csrf = _登入(客戶端)
        第一次 = 客戶端.post(
            _草稿路徑, json=_草稿本文(), headers={"X-CSRF-Token": csrf},
        )
        assert 第一次.status_code == 201, 第一次.text
        接續 = 第一次.headers.get("X-CSRF-Token") or 客戶端.cookies.get(
            "published_web_csrf",
        )
        assert 接續 and 接續 != csrf

        重放 = 客戶端.post(
            _草稿路徑, json=_草稿本文(), headers={"X-CSRF-Token": csrf},
        )
        assert 重放.status_code == 403, "舊權杖必須失效"

        第二次 = 客戶端.post(
            _草稿路徑, json=_草稿本文(), headers={"X-CSRF-Token": 接續},
        )
        assert 第二次.status_code == 201, 第二次.text


# ---------------------------------------------------------------------------
# 同一 Lazy Service
# ---------------------------------------------------------------------------


def test_草稿路由使用建構器的同一Lazy服務(tmp_path):
    """route 必須捕捉 builder 在 app construction 建立的同一個 per-app proxy。"""
    tmp_path = tmp_path.resolve()
    _建立技能與使用者(tmp_path)
    網頁設定, 發布設定, _ = _建立設定(tmp_path)
    建構器 = 生產Controller建構器(發布設定)
    應用 = 建立生產應用程式(網頁設定, 建構器)
    代理 = 建構器._Published.取得草稿規劃代理()
    assert type(代理) is 延遲草稿規劃服務
    命中: list[str] = []

    with TestClient(應用) as 客戶端:
        原始 = type(代理).建立草稿

        def 記錄(自身, *參數, **選項):
            """證明 HTTP 請求確實抵達這個 proxy 實例。"""
            命中.append("代理")
            return 原始(自身, *參數, **選項)

        代理.建立草稿 = 記錄.__get__(代理, type(代理))
        csrf = _登入(客戶端)
        回應 = 客戶端.post(
            _草稿路徑, json=_草稿本文(), headers={"X-CSRF-Token": csrf},
        )
        assert 回應.status_code == 201, 回應.text
        assert 應用.state.發布介面資源[1].取得Planner資源()._代理 is 代理

    assert 命中 == ["代理"]


# ---------------------------------------------------------------------------
# OpenAPI Exact Contract：不得公開 legacy planner_content
# ---------------------------------------------------------------------------


def test_OpenAPI草稿Request只有三鍵且無planner_content(tmp_path):
    """request schema 必須 exact 三鍵、禁額外欄位，且全文不得出現 planner_content。"""
    tmp_path = tmp_path.resolve()
    _建立技能與使用者(tmp_path)
    應用, _ = _建立應用(tmp_path)

    規格 = 應用.openapi()
    綱要 = 規格["paths"][_草稿路徑]["post"]["requestBody"]["content"]
    assert set(綱要) == {"application/json"}
    本文綱要 = 綱要["application/json"]["schema"]
    assert set(本文綱要["properties"]) == {
        "original_requirement_text", "selected_skills", "response_mode",
    }
    assert sorted(本文綱要["required"]) == sorted(本文綱要["properties"])
    assert 本文綱要.get("additionalProperties") is False

    import json as _json
    assert "planner_content" not in _json.dumps(規格, ensure_ascii=False)


def test_Legacy規劃內容請求在canonical_app不可達(tmp_path):
    """帶 planner_content 的 legacy body 必須被拒，且不得建立草稿。"""
    tmp_path = tmp_path.resolve()
    _建立技能與使用者(tmp_path)
    應用, _ = _建立應用(tmp_path)

    with TestClient(應用) as 客戶端:
        csrf = _登入(客戶端)
        回應 = 客戶端.post(
            _草稿路徑,
            json={
                "original_requirement_text": "建立 Alpha API",
                "planner_content": {"system_prompt": "被注入的提示"},
            },
            headers={"X-CSRF-Token": csrf},
        )

    assert 回應.status_code == 422
    assert 回應.json() == {"detail": {"code": "invalid_request"}}


def test_OpenAPI草稿Response為固定三鍵契約(tmp_path):
    """response schema 必須是 exact 的 draft_id／expires_at／preview。"""
    tmp_path = tmp_path.resolve()
    _建立技能與使用者(tmp_path)
    應用, _ = _建立應用(tmp_path)

    規格 = 應用.openapi()
    操作 = 規格["paths"][_草稿路徑]["post"]
    assert set(操作["responses"]) >= {"201"}
    參照 = 操作["responses"]["201"]["content"]["application/json"]["schema"]["$ref"]
    名稱 = 參照.rsplit("/", 1)[-1]
    綱要 = 規格["components"]["schemas"][名稱]
    assert set(綱要["properties"]) == {"draft_id", "expires_at", "preview"}


# ---------------------------------------------------------------------------
# Construction／Startup 副作用邊界
# ---------------------------------------------------------------------------


def test_App_Construction不產生任何副作用(tmp_path):
    """建立 app 不得建立資料庫、呼叫 installer、權威來源或 Planner 工廠。"""
    tmp_path = tmp_path.resolve()
    _建立技能與使用者(tmp_path)
    事件: list[str] = []

    應用, 紀錄 = _建立應用(tmp_path, 事件)

    assert 紀錄 == []
    assert not (tmp_path / "published.sqlite3").exists()
    assert _草稿路徑 in 應用.openapi()["paths"]


def test_Startup依序建立且每個工廠恰好一次(tmp_path):
    """startup 必須在安裝 route authority 前完成組裝，且每個 callback 恰好一次。"""
    tmp_path = tmp_path.resolve()
    _建立技能與使用者(tmp_path)
    事件: list[str] = []
    應用, 紀錄 = _建立應用(tmp_path, 事件)

    with TestClient(應用) as 客戶端:
        assert 紀錄.count("工具安裝") == 1
        assert 紀錄.count("模型安裝") == 1
        assert 紀錄.count("權威來源") == 1
        assert 紀錄.count("規劃器") == 1
        assert 紀錄.index("工具安裝") < 紀錄.index("權威來源")
        csrf = _登入(客戶端)
        assert 客戶端.post(
            _草稿路徑, json=_草稿本文(), headers={"X-CSRF-Token": csrf},
        ).status_code == 201

    assert 紀錄.count("規劃器") == 1


def test_Shutdown後草稿服務不再可用(tmp_path):
    """lifespan 結束後 proxy 必須失去 authority，不得殘留可用服務。"""
    tmp_path = tmp_path.resolve()
    _建立技能與使用者(tmp_path)
    網頁設定, 發布設定, _ = _建立設定(tmp_path)
    建構器 = 生產Controller建構器(發布設定)
    應用 = 建立生產應用程式(網頁設定, 建構器)
    代理 = 建構器._Published.取得草稿規劃代理()

    with TestClient(應用):
        pass

    assert 代理._服務 is None


# ---------------------------------------------------------------------------
# CP3 不 Regression
# ---------------------------------------------------------------------------


def test_CP3_Login_Chat_Session路由不regression(tmp_path):
    """掛載草稿 route 後，CP3 既有 browser 流程必須完全不受影響。"""
    tmp_path = tmp_path.resolve()
    _建立技能與使用者(tmp_path)
    應用, _ = _建立應用(tmp_path)

    路徑們 = set(應用.openapi()["paths"])
    for 必要 in (
        "/healthz", "/api/auth/login", "/api/auth/session", "/api/auth/me",
        "/api/auth/logout", "/api/chat", "/api/sessions",
        "/api/sessions/{session_id}", "/api/skills", "/api/skills/{skill_id}",
        "/v1/endpoints/{slug}/invoke",
    ):
        assert 必要 in 路徑們

    with TestClient(應用) as 客戶端:
        csrf = _登入(客戶端)
        對話 = 客戶端.post(
            "/api/chat", json={"message": "hello"}, headers={"X-CSRF-Token": csrf},
        )
        assert 對話.status_code == 200, 對話.text
        工作階段識別碼 = 對話.json()["session_id"]
        列表 = 客戶端.get("/api/sessions")
        assert 列表.status_code == 200
        assert [項目["id"] for 項目 in 列表.json()["sessions"]] == [工作階段識別碼]
        assert 客戶端.get("/api/skills").status_code == 200
