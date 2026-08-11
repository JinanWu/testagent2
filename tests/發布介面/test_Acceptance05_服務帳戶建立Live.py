"""Acceptance #5 SA-1：凍結服務帳戶建立的 canonical HTTP 契約。

本模組只從 ``建立CP4ASGI應用程式`` 觀測公開路由與 OpenAPI seam；
服務帳戶只能是 Endpoint Create 的內部原子副作用，不得成為 client claim 或獨立 CRUD。
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

import pytest
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from 繁中代理.使用者 import 使用者庫
from 繁中代理.工具 import 工具定義
from 繁中代理.發布介面.asgi import 建立CP4ASGI應用程式
from 繁中代理.發布介面.執行期.工具發布庫 import 工具發布描述, 工具發布註冊
from 繁中代理.發布介面.憑證.加密 import AESGCM憑證封套
from 繁中代理.發布介面.規劃.規劃器供應商 import 決定性假規劃器
from 繁中代理.發布介面.路由.規劃發布 import 發布確認
from 繁中代理.發布介面.生產Published執行 import Published生產設定
from 繁中代理.發布介面.生產Published管理 import Planner生產設定
from 繁中代理.發布介面.設定 import (
    生產設定,
    網頁CSRFHeader名稱,
    網頁CSRFCookie名稱,
    網頁工作階段Cookie名稱,
)


端點建立路徑 = "/api/published-endpoints"
公開建立欄位 = {
    "endpoint_id",
    "version_id",
    "version_number",
    "status",
    "initial_api_key",
}


def _安裝固定工具(工具發布庫物件, 工廠呼叫: list[str]) -> None:
    """安裝 Planner owner resolver 使用的 deterministic tool release。

    參數：
        工具發布庫物件: canonical startup 建立的 per-app registry。
        工廠呼叫: 記錄 installer exact-once 呼叫。
    返回值：
        無；安裝一個無外部副作用的工具定義。
    """
    工廠呼叫.append("tools")
    工具發布庫物件.登錄發布(工具發布描述(
        "acceptance-release",
        (工具發布註冊(
            "revision-1",
            工具定義(
                "acceptance-tool",
                "Acceptance deterministic tool",
                {"type": "object", "properties": {}, "additionalProperties": False},
                lambda _參數: {"ok": True},
            ),
        ),),
    ))


def _解析綱要(規格: dict[str, Any], 綱要: dict[str, Any]) -> dict[str, Any]:
    """解析 OpenAPI 本地元件參照。

    參數：
        規格: canonical app 產生的完整 OpenAPI 文件。
        綱要: 內嵌綱要或只含本地 ``$ref`` 的綱要。
    返回值：
        可直接檢查的綱要物件。
    """
    if "$ref" not in 綱要:
        return 綱要
    return 規格["components"]["schemas"][綱要["$ref"].rsplit("/", 1)[1]]


def _建立完整管理應用程式(暫存目錄: Path, 工廠呼叫: list[str]):
    """以 explicit factories 建立完整管理能力，但不啟動 lifespan。

    參數：
        暫存目錄: 提供彼此隔離的 Web DB、Published DB 與 bundle root 路徑。
        工廠呼叫: 若 app construction 錯誤執行 callback，會留下可觀測事件。
    返回值：
        尚未啟動、但 OpenAPI 應已公開完整管理路由的 canonical app。
    """
    網頁設定 = 生產設定(
        暫存目錄 / "web.sqlite3",
        ("http://localhost:5173",),
        "fake",
        "fake",
        Cookie安全=False,
        工作階段有效秒數=60,
    )
    planner設定 = Planner生產設定(
        "acceptance-release",
        lambda 路徑: 工廠呼叫.append("owner") or 使用者庫(路徑),
        lambda: 工廠呼叫.append("planner") or 決定性假規劃器(),
        3600.0,
    )
    published設定 = Published生產設定(
        暫存目錄 / "published.sqlite3",
        暫存目錄 / "bundles",
        lambda 工具庫: _安裝固定工具(工具庫, 工廠呼叫),
        lambda: 工廠呼叫.append("models") or {"fake": object()},
        Planner設定=planner設定,
        憑證封套工廠=lambda: 工廠呼叫.append("envelope") or AESGCM憑證封套(
            {1: b"A" * 32}, 1,
        ),
    )
    return 建立CP4ASGI應用程式(網頁設定, published設定)


def test_canonical_OpenAPI只有一個endpoint_create且不接受service_account_id(tmp_path):
    """SA-1：Endpoint Create 是唯一 SA 建立入口，且 client／public DTO 都看不到 SA ID。

    參數：
        tmp_path: pytest 提供的隔離絕對路徑。
    返回值：
        無；route inventory、strict request 與 public response 契約皆由 assertion 固定。
    重要副作用：
        只建立 app 與 OpenAPI；不得建立 DB、bundle root 或執行 startup factories。
    """
    工廠呼叫: list[str] = []
    應用程式 = _建立完整管理應用程式(tmp_path, 工廠呼叫)

    符合建立路由 = [
        路由
        for 路由 in 應用程式.routes
        if isinstance(路由, APIRoute) and 路由.path == 端點建立路徑
    ]
    assert len(符合建立路由) == 1
    assert 符合建立路由[0].methods == {"POST"}

    規格 = 應用程式.openapi()
    assert set(規格["paths"][端點建立路徑]) == {"post"}
    assert not any("service-account" in 路徑 for 路徑 in 規格["paths"])

    請求綱要 = _解析綱要(
        規格,
        規格["paths"][端點建立路徑]["post"]["requestBody"]
        ["content"]["application/json"]["schema"],
    )
    assert 請求綱要["additionalProperties"] is False
    assert set(請求綱要["required"]) == {
        "draft_id",
        "slug",
        "configuration_confirmation",
    }
    assert set(請求綱要["properties"]) == set(請求綱要["required"])
    assert {"service_account_id", "owner_user_id", "created_by_user_id", "role"}.isdisjoint(
        請求綱要["properties"]
    )

    回應綱要 = _解析綱要(
        規格,
        規格["paths"][端點建立路徑]["post"]["responses"]["201"]
        ["content"]["application/json"]["schema"],
    )
    assert set(回應綱要["required"]) == 公開建立欄位
    assert set(回應綱要["properties"]) == 公開建立欄位
    assert "service_account_id" not in 回應綱要["properties"]

    assert 工廠呼叫 == []
    assert not (tmp_path / "published.sqlite3").exists()
    assert not (tmp_path / "web.sqlite3").exists()
    assert not (tmp_path / "bundles").exists()


def test_startup重用A3資源並於shutdown撤銷服務帳戶建立authority(tmp_path):
    """SA-2：Create coordinator 重用同一 Draft／Owner／Registry，關閉後固定 fail closed。

    參數：
        tmp_path: 隔離 Web DB、Published DB 與 bundle root。
    返回值：
        無；lifespan identity 與 shutdown authority assertions 皆成立。
    重要副作用：
        啟動並關閉一次 canonical app，建立隔離 SQLite DB；不建立 endpoint 或 SA。
    """
    (tmp_path / "bundles").mkdir()
    工廠呼叫: list[str] = []
    應用程式 = _建立完整管理應用程式(tmp_path, 工廠呼叫)
    捕捉管理代理 = None

    with TestClient(應用程式):
        published資源 = 應用程式.state.發布介面資源[-1]
        planner資源 = published資源.取得Planner資源()
        管理服務 = published資源.取得發布管理服務()
        assert planner資源 is not None and 管理服務 is not None
        assert 管理服務._草稿服務 is planner資源.取得規劃服務()
        assert 管理服務._擁有者解析器 is planner資源.取得擁有者解析器()
        assert planner資源.取得工具發布庫() is published資源._工具庫
        assert 管理服務._套件協調器 is published資源._技能套件協調器
        捕捉管理代理 = published資源._發布管理代理

    assert 工廠呼叫 == ["tools", "models", "owner", "planner", "envelope"]
    assert 捕捉管理代理 is not None
    with pytest.raises(RuntimeError, match="發布管理服務不可用"):
        捕捉管理代理.原子發布(
            擁有者使用者識別碼="owner",
            確認=發布確認("draft", "safe-api", {}),
        )


def _建立Owner(暫存目錄: Path, 帳號: str, 密碼: str) -> str:
    """建立具固定技能與工具權限的真 Web owner。

    參數：暫存目錄定位技能及 Web DB；帳號與密碼供 canonical login。
    返回值：權威使用者識別碼。
    """
    技能目錄 = 暫存目錄 / "skills" / "demo"
    技能目錄.mkdir(parents=True)
    (技能目錄 / "SKILL.md").write_text(
        "---\nname: demo\ndescription: acceptance skill\n---\n\n# Demo\n",
        encoding="utf-8",
    )
    使用者儲存庫 = 使用者庫(暫存目錄 / "web.sqlite3")
    try:
        使用者 = 使用者儲存庫.建立使用者(
            帳號,
            密碼,
            roles=["user"],
            enabled_tools=["acceptance-tool"],
            enabled_skills=["demo"],
            skill_roots=[str(暫存目錄 / "skills")],
            allowed_workdirs=[str(暫存目錄)],
        )
        return str(使用者["id"])
    finally:
        使用者儲存庫.連線.close()


def _登入(客戶端: TestClient, 帳號: str, 密碼: str) -> str:
    """經 canonical login 建立真 session 並回傳本次 CSRF token。"""
    回應 = 客戶端.post("/api/auth/login", json={"username": 帳號, "password": 密碼})
    assert 回應.status_code == 200
    assert 網頁工作階段Cookie名稱 in 客戶端.cookies
    assert 網頁CSRFCookie名稱 in 客戶端.cookies
    return str(回應.json()["csrf_token"])


def _建立草稿(客戶端: TestClient, csrf: str):
    """經 canonical Draft route 建立 server-owned configuration。"""
    return 客戶端.post(
        "/api/published-endpoints/draft",
        json={
            "original_requirement_text": "建立 Demo API",
            "selected_skills": ["demo"],
            "response_mode": "structured",
        },
        headers={網頁CSRFHeader名稱: csrf},
    )


def _建立確認(預覽: dict[str, Any]) -> dict[str, Any]:
    """只從 server preview 建立 route 允許的五個確認欄位。"""
    return json.loads(json.dumps({
        "system_prompt": 預覽["system_prompt"],
        "input_schema": 預覽["input_schema"],
        "response_schema": 預覽["response_schema"],
        "human_docs": 預覽["human_docs"],
        "rate_limit": 預覽["rate_limit"],
    }))


def _送出建立(客戶端: TestClient, csrf: str, 草稿: dict[str, Any], slug: str, **額外欄位):
    """經 canonical Create route 送出三鍵本文及測試指定的敵對額外欄位。"""
    本文 = {
        "draft_id": 草稿["draft_id"],
        "slug": slug,
        "configuration_confirmation": _建立確認(草稿["preview"]),
        **額外欄位,
    }
    return 客戶端.post(
        端點建立路徑,
        json=本文,
        headers={網頁CSRFHeader名稱: csrf},
    )


def test_live登入草稿建立兩端點並產生不同服務帳戶且拒絕client_claim(tmp_path, caplog):
    """SA-3：真 HTTP 建立完整 SQLite 圖形，第二端點不可重用 SA，敵對 claim 零副作用。"""
    (tmp_path / "bundles").mkdir()
    應用程式 = _建立完整管理應用程式(tmp_path, [])

    with TestClient(應用程式, raise_server_exceptions=False) as 客戶端:
        擁有者 = _建立Owner(tmp_path, "alice", "correct horse")
        csrf = _登入(客戶端, "alice", "correct horse")
        草稿回應 = _建立草稿(客戶端, csrf)
        assert 草稿回應.status_code == 201
        草稿 = 草稿回應.json()

        偽造 = _送出建立(
            客戶端,
            草稿回應.headers[網頁CSRFHeader名稱],
            草稿,
            "forged-api",
            service_account_id="client-sa",
        )
        assert 偽造.status_code == 422
        with sqlite3.connect(tmp_path / "published.sqlite3") as 連線:
            assert 連線.execute("SELECT COUNT(*) FROM service_accounts").fetchone()[0] == 0

        csrf = _登入(客戶端, "alice", "correct horse")
        建立一 = _送出建立(客戶端, csrf, 草稿, "first-api")
        assert 建立一.status_code == 201
        本文一 = 建立一.json()
        assert set(本文一) == 公開建立欄位
        assert "service_account_id" not in 本文一
        初始金鑰 = 本文一["initial_api_key"]
        assert type(初始金鑰) is str and 初始金鑰

        csrf = _登入(客戶端, "alice", "correct horse")
        草稿二回應 = _建立草稿(客戶端, csrf)
        assert 草稿二回應.status_code == 201
        建立二 = _送出建立(
            客戶端,
            草稿二回應.headers[網頁CSRFHeader名稱],
            草稿二回應.json(),
            "second-api",
        )
        assert 建立二.status_code == 201

        with sqlite3.connect(tmp_path / "published.sqlite3") as 連線:
            連線.row_factory = sqlite3.Row
            端點 = [dict(列) for 列 in 連線.execute(
                "SELECT id, owner_user_id, service_account_id, current_version_id "
                "FROM published_endpoints ORDER BY slug"
            )]
            服務帳戶 = [列[0] for 列 in 連線.execute("SELECT id FROM service_accounts ORDER BY id")]
            assert len(端點) == len(服務帳戶) == 2
            assert {列["owner_user_id"] for 列 in 端點} == {擁有者}
            assert len({列["service_account_id"] for 列 in 端點}) == 2
            assert {列["service_account_id"] for 列 in 端點} == set(服務帳戶)
            for 表 in (
                "published_endpoint_versions",
                "endpoint_credentials",
                "published_skill_bundles",
                "published_draft_consumptions",
                "published_endpoint_version_metadata",
            ):
                assert 連線.execute(f'SELECT COUNT(*) FROM "{表}"').fetchone()[0] == 2

        金鑰位元 = 初始金鑰.encode()
        assert 金鑰位元 not in (tmp_path / "published.sqlite3").read_bytes()
        assert all(
            金鑰位元 not in 路徑.read_bytes()
            for 路徑 in (tmp_path / "bundles").rglob("*")
            if 路徑.is_file()
        )
        assert all(初始金鑰 not in 紀錄.getMessage() for 紀錄 in caplog.records)
        初始金鑰 = "[REDACTED]"
