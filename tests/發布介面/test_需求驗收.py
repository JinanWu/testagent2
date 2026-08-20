"""A3-05 驗收關閉：Canonical Live OpenAPI Snapshot Gate 與需求逐條收斂。

用途：把 A3 五張卡的驗收條件收斂成可機器檢查的單一關卡。

Snapshot Gate 的形式刻意不是雜湊：期望值以完整、可讀、可 diff 的字面結構寫在本檔案內，
任何 route inventory 或草稿契約漂移都會在 diff 中顯示為具體欄位變化，而不是一行 hash 改動，
因此無法以「覆寫 hash」的方式讓關卡通過。

不在本檔案涵蓋範圍（屬 repo 外的流程性關卡，需由人工在追蹤系統關閉）：
Spec Review PASS、Quality Review APPROVED、Ledger #3 更新為 IMPLEMENTED 與 Checkpoint Ancestry。
"""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from 繁中代理.發布介面.規劃.規劃器契約 import 文字回應結構

from test_CP4_規劃草稿E2E import (
    _建立草稿, _建立環境, _登入, _草稿路徑, _草稿聚合, _發布面快照, _斷言零發布副作用,
    _使用者識別碼, _技能名稱, _帳號, _帳號二, _密碼二, _回應頂層鍵, _結構化回應結構,
    _預覽鍵, _建立合法Published基準, _草稿代理,
)

# ---------------------------------------------------------------------------
# Canonical Route Inventory Snapshot（逐條可 diff，非雜湊）
# ---------------------------------------------------------------------------

_預期路由清單 = {
    "/api/admin/published-endpoints/{endpoint_id}/invocations/{invocation_id}/redactions": ("post",),
    "/api/admin/endpoints/{endpoint_id}/invocations": ("get",),
    "/api/admin/endpoints/{endpoint_id}/invocations/{invocation_id}": ("get",),
    "/api/auth/login": ("post",),
    "/api/auth/logout": ("post",),
    "/api/auth/me": ("get",),
    "/api/auth/session": ("get",),
    "/api/chat": ("post",),
    "/api/published-endpoints/{endpoint_id}/docs": ("get",),
    "/api/published-endpoints/draft": ("post",),
    "/api/sessions": ("get",),
    "/api/sessions/{session_id}": ("get",),
    "/api/skills": ("get",),
    "/api/skills/{skill_id}": ("get",),
    "/healthz": ("get",),
    "/v1/endpoints/{slug}/docs": ("get",),
    "/v1/endpoints/{slug}/invoke": ("post",),
}

_預期草稿請求綱要 = {
    "additionalProperties": False,
    "type": "object",
    "required": ["original_requirement_text", "selected_skills", "response_mode"],
    "properties": {
        "original_requirement_text": {
            "minLength": 1, "type": "string", "x-maxUtf8Bytes": 16384,
        },
        "response_mode": {"enum": ["text", "structured"], "type": "string"},
        "selected_skills": {
            "items": {
                "maxLength": 128, "minLength": 1,
                "pattern": "^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$", "type": "string",
            },
            "maxItems": 32, "minItems": 1, "type": "array", "uniqueItems": True,
        },
    },
}

_預期草稿請求 = {
    "content": {"application/json": {"schema": _預期草稿請求綱要}},
    "required": True,
}

_預期草稿201回應 = {
    "description": "Successful Response",
    "content": {
        "application/json": {
            "schema": {
                "additionalProperties": False,
                "description": "安全草稿 Route 對外公開的 exact 三鍵建立結果。",
                "properties": {
                    "draft_id": {"title": "Draft Id", "type": "string"},
                    "expires_at": {"title": "Expires At", "type": "number"},
                    "preview": {
                        "additionalProperties": False,
                        "description": "安全草稿 Route 對外公開的 exact 十二鍵預覽資料傳輸物件。",
                        "properties": {
                            "behavior_summary": {"title": "Behavior Summary", "type": "string"},
                            "endpoint_name": {"title": "Endpoint Name", "type": "string"},
                            "human_docs": {"title": "Human Docs", "type": "string"},
                            "input_schema": {
                                "anyOf": [
                                    {
                                        "additionalProperties": {},
                                        "type": "object",
                                    },
                                    {"type": "null"},
                                ],
                                "title": "Input Schema",
                            },
                            "rate_limit": {
                                "additionalProperties": False,
                                "description": "安全草稿預覽中的 exact 兩鍵限流資料傳輸物件。",
                                "properties": {
                                    "credential_per_minute": {
                                        "title": "Credential Per Minute", "type": "integer",
                                    },
                                    "endpoint_per_minute": {
                                        "title": "Endpoint Per Minute", "type": "integer",
                                    },
                                },
                                "required": ["endpoint_per_minute", "credential_per_minute"],
                                "title": "安全草稿限流預覽",
                                "type": "object",
                            },
                            "recommended_tools": {
                                "items": {"type": "string"},
                                "title": "Recommended Tools", "type": "array",
                            },
                            "response_schema": {
                                "additionalProperties": {},
                                "title": "Response Schema", "type": "object",
                            },
                            "selected_skills": {
                                "items": {"type": "string"},
                                "title": "Selected Skills", "type": "array",
                            },
                            "suggested_slug": {"title": "Suggested Slug", "type": "string"},
                            "system_prompt": {"title": "System Prompt", "type": "string"},
                            "tool_capabilities": {
                                "additionalProperties": {"type": "string"},
                                "title": "Tool Capabilities", "type": "object",
                            },
                            "warnings": {
                                "items": {"type": "string"},
                                "title": "Warnings", "type": "array",
                            },
                        },
                        "required": [
                            "endpoint_name", "suggested_slug", "behavior_summary",
                            "selected_skills", "recommended_tools", "tool_capabilities",
                            "system_prompt", "input_schema", "response_schema", "human_docs",
                            "rate_limit", "warnings",
                        ],
                        "title": "安全草稿預覽",
                        "type": "object",
                    },
                },
                "required": ["draft_id", "expires_at", "preview"],
                "title": "安全草稿建立結果",
                "type": "object",
            },
        },
    },
    "headers": {
        "X-CSRF-Token": {
            "description": "Successor single-use CSRF token when rotated",
            "schema": {"type": "string", "minLength": 32, "maxLength": 512},
        },
    },
}


def _解析參照(規格: dict, 值):
    """只展開 local schema refs；期望 snapshot 始終是本檔案中的人工 literal。"""
    if type(值) is list:
        return [_解析參照(規格, 項目) for 項目 in 值]
    if type(值) is not dict:
        return 值
    if set(值) == {"$ref"}:
        區段 = 值["$ref"].split("/")
        assert 區段[:3] == ["#", "components", "schemas"], 值["$ref"]
        return _解析參照(規格, 規格["components"]["schemas"][區段[3]])
    return {鍵: _解析參照(規格, 項目) for 鍵, 項目 in 值.items()}


@pytest.fixture()
def 規格(tmp_path: Path) -> dict:
    """建立 canonical app 並取得其 live OpenAPI 規格。"""
    環境 = _建立環境(tmp_path)
    應用 = 環境["建立應用"]()
    return 應用.openapi()


# ---------------------------------------------------------------------------
# OpenAPI Snapshot Gate
# ---------------------------------------------------------------------------


def test_snapshot_gate_canonical路由清單完全相符(規格: dict):
    """live OpenAPI 的 path 與 method inventory 必須與逐條 snapshot 完全相同。"""
    實際 = {路徑: tuple(sorted(操作)) for 路徑, 操作 in 規格["paths"].items()}
    assert 實際 == _預期路由清單


def test_snapshot_gate_草稿請求契約完全相符(規格: dict):
    """草稿 request schema 必須是 exact 三鍵且禁止額外欄位。"""
    請求 = 規格["paths"][_草稿路徑]["post"]["requestBody"]
    assert 請求 == _預期草稿請求


def test_snapshot_gate_草稿回應契約完全相符(規格: dict):
    """201本文與runtime可達錯誤status分母必須逐欄exact相符。"""
    操作 = 規格["paths"][_草稿路徑]["post"]
    assert set(操作["responses"]) == {"201", "401", "403", "422", "500", "502", "503"}
    assert _解析參照(規格, 操作["responses"]["201"]) == _預期草稿201回應


def test_snapshot_gate_不得公開legacy規劃內容契約(規格: dict):
    """canonical OpenAPI 全文不得出現 legacy ``planner_content`` 欄位。"""
    import json

    assert "planner_content" not in json.dumps(規格, ensure_ascii=False)


def test_snapshot_gate_UI仍明確Deferred(規格: dict):
    """UI 延後到 #22：canonical app 不得公開任何草稿管理、列舉或發布 UI 路由。"""
    路徑們 = set(規格["paths"])
    assert [路徑 for 路徑 in 路徑們 if "draft" in 路徑] == [_草稿路徑]
    for 未實作 in (
        "/api/published-endpoints",
        "/api/published-endpoints/{endpoint_id}/versions",
        "/api/published-endpoints/draft/{draft_id}",
        "/api/published-endpoints/drafts",
    ):
        assert 未實作 not in 路徑們


# ---------------------------------------------------------------------------
# 需求驗收逐條收斂
# ---------------------------------------------------------------------------


def test_驗收_真Login與單次CSRF流程通過(tmp_path):
    """真 Login、Cookie 與 single-use CSRF 必須構成草稿建立的唯一入口。"""
    環境 = _建立環境(tmp_path)
    應用 = 環境["建立應用"]()

    with TestClient(應用) as 客戶端:
        assert _建立草稿(客戶端, "x" * 32, "text").status_code == 401
        _, csrf = _登入(客戶端)
        assert _建立草稿(客戶端, "y" * 32, "text").status_code == 403
        成功 = _建立草稿(客戶端, csrf, "text")
        assert 成功.status_code == 201, 成功.text
        重放之前 = _發布面快照(環境)
        assert _建立草稿(客戶端, csrf, "text").status_code == 403
        _斷言零發布副作用(環境, 重放之前)


def test_驗收_兩種模式各一個201且契約固定(tmp_path):
    """Text 與 Structured 各至少一個 201，頂層三鍵、預覽十二鍵。"""
    環境 = _建立環境(tmp_path)
    應用 = 環境["建立應用"]()

    with TestClient(應用) as 客戶端:
        _, csrf = _登入(客戶端)
        文字 = _建立草稿(客戶端, csrf, "text")
        assert 文字.status_code == 201, 文字.text
        結構 = _建立草稿(客戶端, 文字.headers["X-CSRF-Token"], "structured")
        assert 結構.status_code == 201, 結構.text

    for 回應, 期望結構 in ((文字, 文字回應結構), (結構, _結構化回應結構)):
        本文 = 回應.json()
        assert set(本文) == _回應頂層鍵
        assert set(本文["preview"]) == _預覽鍵
        assert 本文["preview"]["response_schema"] == 期望結構


def test_驗收_擁有者與期限正確且跨擁有者不可存取(tmp_path):
    """草稿必須綁真 session principal、帶固定期限，且第二位使用者完全無法觸及。"""
    from 繁中代理.發布介面.規劃.綱要 import 草稿不可執行錯誤, 草稿存取錯誤

    環境 = _建立環境(tmp_path)
    應用 = 環境["建立應用"]()
    擁有者一 = _使用者識別碼(環境, _帳號)
    擁有者二 = _使用者識別碼(環境, _帳號二)

    with TestClient(應用) as 客戶端:
        _, csrf = _登入(客戶端)
        本文 = _建立草稿(客戶端, csrf, "structured").json()
        聚合 = _草稿聚合(應用)
        現在 = 本文["expires_at"] - 1

        草稿 = 聚合.讀取草稿(擁有者一, 本文["draft_id"], 現在=現在)
        assert 草稿.擁有者識別碼 == 擁有者一
        assert 草稿.到期時間 == 本文["expires_at"]
        assert 草稿.綱要 == 本文["preview"]
        assert [項目.名稱 for 項目 in 草稿.能力摘要.技能] == [_技能名稱]

        with pytest.raises(草稿存取錯誤):
            聚合.讀取草稿(擁有者二, 本文["draft_id"], 現在=現在)
        with pytest.raises(草稿不可執行錯誤):
            聚合.呼叫草稿(擁有者一, 本文["draft_id"], 現在=現在)


def test_驗收_全流程零發布副作用(tmp_path):
    """整段驗收流程結束後，發布面必須逐列、逐位元組維持 baseline。"""
    環境 = _建立環境(tmp_path)
    應用 = 環境["建立應用"]()

    with TestClient(應用) as 客戶端:
        API金鑰 = _建立合法Published基準(環境)
        之前 = _發布面快照(環境)
        _, csrf = _登入(客戶端)
        文字 = _建立草稿(客戶端, csrf, "text")
        assert 文字.status_code == 201, 文字.text
        結構 = _建立草稿(客戶端, 文字.headers["X-CSRF-Token"], "structured")
        assert 結構.status_code == 201, 結構.text
        草稿識別碼 = 結構.json()["draft_id"]
        呼叫之前 = _發布面快照(環境)
        呼叫 = 客戶端.post(
            f"/v1/endpoints/{草稿識別碼}/invoke", json={"input": {}},
            headers={"Authorization": f"Bearer {API金鑰}"},
        )
        assert 呼叫.status_code == 404, 呼叫.text
        assert 呼叫.json()["error"]["code"] == "endpoint_not_found"
        _斷言零發布副作用(環境, 呼叫之前)

    _斷言零發布副作用(環境, 之前)


def test_驗收_Restart與Shutdown契約(tmp_path):
    """Restart 依 A3-01 in-memory 凍結契約失效；Shutdown 後 proxy 固定 fail closed。"""
    from 繁中代理.發布介面.生產Published管理 import 草稿規劃服務不可用
    from 繁中代理.發布介面.規劃.綱要 import 草稿存取錯誤

    環境 = _建立環境(tmp_path)
    擁有者識別碼 = _使用者識別碼(環境, _帳號)

    第一應用 = 環境["建立應用"]()
    with TestClient(第一應用) as 客戶端:
        _, csrf = _登入(客戶端)
        本文 = _建立草稿(客戶端, csrf, "structured").json()
        第一代理 = _草稿代理(第一應用)
        關閉之前 = _發布面快照(環境)

    assert 第一代理._服務 is None
    with pytest.raises(草稿規劃服務不可用):
        第一代理.建立草稿(擁有者識別碼, "建立 Alpha API", (_技能名稱,), "text", 現在=0.0)
    _斷言零發布副作用(環境, 關閉之前)

    重啟之前 = _發布面快照(環境)
    第二應用 = 環境["建立應用"]()
    with TestClient(第二應用):
        第二聚合 = _草稿聚合(第二應用)
        assert 第二聚合._草稿 == {}
        with pytest.raises(草稿存取錯誤):
            第二聚合.讀取草稿(擁有者識別碼, 本文["draft_id"], 現在=本文["expires_at"] - 1)
    _斷言零發布副作用(環境, 重啟之前)
