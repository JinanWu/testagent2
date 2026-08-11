"""Acceptance #4 EP-1：凍結正式草稿與端點建立 HTTP 契約的 RED 測試。

本模組只從 ``建立CP4ASGI應用程式`` 建立正式應用，不建立手工 FastAPI
應用或替代路由。測試固定 Draft／Create 路徑、方法、OpenAPI 本文與回應、
正式 Session／單次 CSRF 相依身分，以及不得由客戶端聲稱的內部權威欄位。
目前預期因正式應用尚未掛載兩條管理路由而 RED；不得在本卡修改 production source。
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

import pytest
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from 繁中代理.使用者 import 使用者庫
from 繁中代理.發布介面.asgi import 建立CP4ASGI應用程式
from 繁中代理.發布介面.生產Published執行 import Published生產設定
from 繁中代理.發布介面.設定 import 生產設定


草稿路徑 = "/api/published-endpoints/draft"
端點建立路徑 = "/api/published-endpoints"
禁止客戶端聲稱欄位 = (
    "owner_id",
    "service_account_id",
    "role",
    "selected_tools",
    "system_prompt",
)


def _安裝空工具(_工具發布庫) -> None:
    """提供正式 Published 設定所需但不安裝任何工具的測試注入。

    參數：
        _工具發布庫: 正式 startup 建立的工具發布庫；本卡不需使用。
    回傳值：
        無。
    例外：
        無預期例外。
    重要副作用：
        無；不修改工具發布庫。
    """


def _建立假模型表() -> dict[str, object]:
    """建立足以通過正式 startup 契約的隔離假模型表。

    參數：
        無。
    回傳值：
        只含 ``fake`` provider 的新字典。
    例外：
        無預期例外。
    重要副作用：
        無；每次呼叫都回傳新的記憶體物件。
    """
    return {"fake": object()}


def _建立正式應用程式(暫存目錄: Path):
    """建立不讀隱含環境且可由正式 lifespan 啟動的 CP4 應用。

    參數：
        暫存目錄: pytest 提供的隔離目錄，用來配置 Web DB、Published DB 與 bundle root。
    回傳值：
        ``建立CP4ASGI應用程式`` 回傳的正式 FastAPI 應用。
    例外：
        正式設定或應用組裝違反既有契約時，原樣傳出對應例外。
    重要副作用：
        只建立 bundle 目錄；應用建構本身不得建立資料庫或呼叫外部注入。
    """
    技能套件根目錄 = 暫存目錄 / "bundles"
    技能套件根目錄.mkdir()
    網頁設定 = 生產設定(
        暫存目錄 / "web.sqlite3",
        ("http://localhost:5173",),
        "fake",
        "fake",
        Cookie安全=False,
        工作階段有效秒數=60,
    )
    發布設定 = Published生產設定(
        暫存目錄 / "published.sqlite3",
        技能套件根目錄,
        _安裝空工具,
        _建立假模型表,
    )
    return 建立CP4ASGI應用程式(網頁設定, 發布設定)


def _取得唯一正式路由(應用程式, 路徑: str) -> APIRoute:
    """從正式應用 inventory 取得指定路徑唯一一條 API 路由。

    參數：
        應用程式: 正式 CP4 FastAPI 應用。
        路徑: 必須唯一存在的 exact HTTP route path。
    回傳值：
        指定路徑唯一的 ``APIRoute``。
    例外：
        路徑缺失或重複時以測試 assertion 明確回報目前 route inventory。
    重要副作用：
        無；只讀取應用路由表。
    """
    符合路由清單 = [
        路由
        for 路由 in 應用程式.routes
        if isinstance(路由, APIRoute) and 路由.path == 路徑
    ]
    assert len(符合路由清單) == 1, (
        f"正式應用必須有且只有一條 {路徑}；目前數量={len(符合路由清單)}；"
        f"inventory={[路由.path for 路由 in 應用程式.routes if isinstance(路由, APIRoute)]}"
    )
    return 符合路由清單[0]


def _解析OpenAPI綱要(規格: dict[str, Any], 綱要: dict[str, Any]) -> dict[str, Any]:
    """解析 OpenAPI 元件參照並回傳可直接檢查的綱要。

    參數：
        規格: 正式應用產生的完整 OpenAPI 文件。
        綱要: 內嵌綱要或只含本地 ``$ref`` 的綱要。
    回傳值：
        內嵌綱要本身，或 ``components.schemas`` 中被參照的綱要。
    例外：
        缺少元件或參照格式錯誤時傳出 ``KeyError``，使契約漂移明確失敗。
    重要副作用：
        無；只讀取輸入字典。
    """
    if "$ref" not in 綱要:
        return 綱要
    元件名稱 = 綱要["$ref"].rsplit("/", 1)[1]
    return 規格["components"]["schemas"][元件名稱]


def _取得請求綱要(規格: dict[str, Any], 路徑: str) -> dict[str, Any]:
    """取得指定正式 POST 操作唯一的 JSON request schema。

    參數：
        規格: 正式應用產生的完整 OpenAPI 文件。
        路徑: Draft 或 Endpoint Create exact path。
    回傳值：
        已解析元件參照的 ``application/json`` request schema。
    例外：
        路徑、POST、必要本文或 JSON media type 缺失時傳出 ``KeyError`` 或 assertion。
    重要副作用：
        無；只讀取 OpenAPI 文件。
    """
    本文契約 = 規格["paths"][路徑]["post"]["requestBody"]
    assert 本文契約["required"] is True
    assert set(本文契約["content"]) == {"application/json"}
    return _解析OpenAPI綱要(
        規格,
        本文契約["content"]["application/json"]["schema"],
    )


def test_canonical_OpenAPI包含唯一draft與endpoint_create(tmp_path):
    """固定正式 Draft／Create 的唯一 POST、strict schema、public 201 DTO 與 canonical 身分相依。

    參數：
        tmp_path: pytest 提供的隔離目錄，用來建立明確正式設定。
    回傳值：
        無；所有 frozen contract 都以 assertion 表達。
    例外：
        正式路由缺失、重複、方法漂移、schema 漂移或相依身分不一致時測試失敗。
    重要副作用：
        建立 bundle 目錄與應用物件；不啟動 lifespan、不建立資料庫、不呼叫服務。
    """
    應用程式 = _建立正式應用程式(tmp_path)
    assert not (tmp_path / "web.sqlite3").exists()
    assert not (tmp_path / "published.sqlite3").exists()

    草稿路由 = _取得唯一正式路由(應用程式, 草稿路徑)
    建立路由 = _取得唯一正式路由(應用程式, 端點建立路徑)
    assert 草稿路由.methods == 建立路由.methods == {"POST"}

    規格 = 應用程式.openapi()
    assert set(規格["paths"][草稿路徑]) == {"post"}
    assert set(規格["paths"][端點建立路徑]) == {"post"}

    草稿綱要 = _取得請求綱要(規格, 草稿路徑)
    assert 草稿綱要["additionalProperties"] is False
    assert set(草稿綱要["required"]) == {
        "original_requirement_text",
        "selected_skills",
        "response_mode",
    }
    assert set(草稿綱要["properties"]) == set(草稿綱要["required"])

    建立綱要 = _取得請求綱要(規格, 端點建立路徑)
    assert 建立綱要["additionalProperties"] is False
    assert set(建立綱要["required"]) == {
        "draft_id",
        "slug",
        "configuration_confirmation",
    }
    assert set(建立綱要["properties"]) == set(建立綱要["required"])
    assert 建立綱要["properties"]["slug"] == {
        "type": "string",
        "minLength": 1,
        "maxLength": 63,
        "pattern": "^[a-z0-9][a-z0-9-]*$",
    }
    assert 建立綱要["properties"]["configuration_confirmation"]["type"] == "object"

    成功回應綱要 = _解析OpenAPI綱要(
        規格,
        規格["paths"][端點建立路徑]["post"]["responses"]["201"]
        ["content"]["application/json"]["schema"],
    )
    公開回應欄位 = {
        "endpoint_id",
        "version_id",
        "version_number",
        "status",
        "initial_api_key",
    }
    assert set(成功回應綱要["required"]) == 公開回應欄位
    assert set(成功回應綱要["properties"]) == 公開回應欄位
    assert 公開回應欄位.isdisjoint({
        "service_account_id",
        "bundle_id",
        "bundle_path",
        "manifest_path",
        "credential_id",
        "ciphertext",
    })

    草稿相依清單 = [相依.call for 相依 in 草稿路由.dependant.dependencies]
    建立相依清單 = [相依.call for 相依 in 建立路由.dependant.dependencies]
    登出路由 = _取得唯一正式路由(應用程式, "/api/auth/logout")
    登出相依清單 = [相依.call for 相依 in 登出路由.dependant.dependencies]
    assert len(草稿相依清單) == 2
    assert 草稿相依清單 == 建立相依清單 == 登出相依清單


@pytest.mark.parametrize("請求方法", ["GET", "PUT", "PATCH", "DELETE"])
@pytest.mark.parametrize("路徑", [草稿路徑, 端點建立路徑])
def test_正式草稿與端點建立拒絕其他HTTP方法(tmp_path, 請求方法: str, 路徑: str):
    """固定 Draft／Create 除 POST 外的 GET／PUT／PATCH／DELETE 都回 405。

    參數：
        tmp_path: pytest 提供的隔離目錄。
        請求方法: pytest 參數化提供的禁止方法。
        路徑: pytest 參數化提供的 Draft 或 Endpoint Create 路徑。
    回傳值：
        無；回應必須精確為 HTTP 405。
    例外：
        正式路由缺失或錯誤接受其他方法時測試失敗。
    重要副作用：
        建立測試客戶端並送出一個不啟動服務操作的 HTTP 請求。
    """
    應用程式 = _建立正式應用程式(tmp_path)
    with TestClient(應用程式, raise_server_exceptions=False) as 客戶端:
        回應 = 客戶端.request(請求方法, 路徑)
    assert 回應.status_code == 405


@pytest.mark.parametrize("禁止欄位", 禁止客戶端聲稱欄位)
def test_正式端點建立拒絕客戶端權威聲稱且零發布副作用(tmp_path, 禁止欄位: str):
    """固定客戶端 Owner／SA／Role／Tools／Prompt 聲稱為 422 且不建立發布資料。

    參數：
        tmp_path: pytest 提供的隔離目錄。
        禁止欄位: 每次送入的一個禁止 top-level JSON key。
    回傳值：
        無；驗證固定錯誤與 Published DB 零 endpoint rows。
    例外：
        權威欄位未被 strict body 拒絕、路由缺失或產生發布副作用時測試失敗。
    重要副作用：
        啟動正式 lifespan、建立隔離測試使用者、登入並送出一次無效 Create 請求。
    """
    應用程式 = _建立正式應用程式(tmp_path)
    with TestClient(應用程式, raise_server_exceptions=False) as 客戶端:
        使用者儲存庫 = 使用者庫(tmp_path / "web.sqlite3")
        使用者儲存庫.建立使用者(
            "alice",
            "correct horse",
            roles=["user"],
            enabled_tools=[],
            enabled_skills=[],
            skill_roots=[],
            allowed_workdirs=[str(tmp_path)],
        )
        使用者儲存庫.連線.close()
        登入回應 = 客戶端.post(
            "/api/auth/login",
            json={"username": "alice", "password": "correct horse"},
        )
        assert 登入回應.status_code == 200
        建立本文 = {
            "draft_id": "draft-contract",
            "slug": "contract-api",
            "configuration_confirmation": {"system_prompt": "server-preview-value"},
            禁止欄位: "forged-client-claim",
        }
        建立回應 = 客戶端.post(
            端點建立路徑,
            json=建立本文,
            headers={"X-CSRF-Token": 登入回應.json()["csrf_token"]},
        )

    assert (建立回應.status_code, 建立回應.json()) == (
        422,
        {"detail": {"code": "invalid_request"}},
    )
    with sqlite3.connect(tmp_path / "published.sqlite3") as 資料庫連線:
        發布端點數量 = 資料庫連線.execute(
            "SELECT COUNT(*) FROM published_endpoints"
        ).fetchone()[0]
    assert 發布端點數量 == 0


def test_正式端點建立只接受伺服器草稿相等確認(tmp_path):
    """固定 Create 不得用客戶端修改值覆寫正式 Draft 的 Planner 內容。

    參數：
        tmp_path: pytest 提供的隔離目錄，用來保存技能、Web DB、Published DB 與 bundle root。
    回傳值：
        無；修改 ``system_prompt`` 的 confirmation 必須固定回 422 且零發布資料。
    例外：
        Draft 路由缺失、正式規劃失敗、修改值被接受或產生發布副作用時測試失敗。
    重要副作用：
        啟動正式 lifespan、建立隔離技能與使用者、建立一份 Draft 並送出一次不相等確認。
    """
    技能根目錄 = tmp_path / "skills"
    技能目錄 = 技能根目錄 / "demo"
    技能目錄.mkdir(parents=True)
    (技能目錄 / "SKILL.md").write_text(
        "---\nname: demo\ndescription: contract skill\n---\n\n# Demo\n",
        encoding="utf-8",
    )
    應用程式 = _建立正式應用程式(tmp_path)
    with TestClient(應用程式, raise_server_exceptions=False) as 客戶端:
        使用者儲存庫 = 使用者庫(tmp_path / "web.sqlite3")
        使用者儲存庫.建立使用者(
            "alice",
            "correct horse",
            roles=["user"],
            enabled_tools=[],
            enabled_skills=["demo"],
            skill_roots=[str(技能根目錄)],
            allowed_workdirs=[str(tmp_path)],
        )
        使用者儲存庫.連線.close()
        登入回應 = 客戶端.post(
            "/api/auth/login",
            json={"username": "alice", "password": "correct horse"},
        )
        assert 登入回應.status_code == 200
        草稿回應 = 客戶端.post(
            草稿路徑,
            json={
                "original_requirement_text": "建立合約測試 API",
                "selected_skills": ["demo"],
                "response_mode": "text",
            },
            headers={"X-CSRF-Token": 登入回應.json()["csrf_token"]},
        )
        assert 草稿回應.status_code == 201
        草稿本文 = 草稿回應.json()
        修改確認 = dict(草稿本文["preview"])
        修改確認["system_prompt"] = "客戶端不得覆寫的提示"
        建立回應 = 客戶端.post(
            端點建立路徑,
            json={
                "draft_id": 草稿本文["draft_id"],
                "slug": "contract-api",
                "configuration_confirmation": 修改確認,
            },
            headers={"X-CSRF-Token": 草稿回應.headers["X-CSRF-Token"]},
        )

    assert (建立回應.status_code, 建立回應.json()) == (
        422,
        {"detail": "管理操作輸入無效"},
    )
    with sqlite3.connect(tmp_path / "published.sqlite3") as 資料庫連線:
        發布端點數量 = 資料庫連線.execute(
            "SELECT COUNT(*) FROM published_endpoints"
        ).fetchone()[0]
    assert 發布端點數量 == 0
