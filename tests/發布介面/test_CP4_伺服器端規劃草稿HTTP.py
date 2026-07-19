"""CP4-PLANNER-HTTP：安全 draft request、CSRF composition 與固定錯誤。"""
from fastapi import FastAPI
from fastapi.testclient import TestClient

from 繁中代理.發布介面.網頁工作階段 import 網頁使用者
from 繁中代理.發布介面.規劃.綱要 import 規劃服務
from 繁中代理.發布介面.路由.規劃發布 import 建立安全草稿路由器

class 假服務:
    def __init__(self):
        self.呼叫 = []
        self.草稿 = 規劃服務(識別碼產生器=lambda: "draft-http")
    def 建立草稿(self, owner, requirement, skills, mode, *, 現在):
        self.呼叫.append((owner, requirement, skills, mode, 現在))
        return self.草稿.建立草稿(owner, requirement, {
            "endpoint_name": "Alpha API", "suggested_slug": "alpha-api",
            "behavior_summary": "摘要", "selected_skills": ["alpha"],
            "recommended_tools": [], "tool_capabilities": {}, "system_prompt": "提示",
            "input_schema": None, "response_schema": {"type": "object"},
            "human_docs": "文件", "rate_limit": {"endpoint_per_minute": 60, "credential_per_minute": 30},
            "warnings": [],
        }, 現在=現在)

def _客戶端():
    服務 = 假服務()
    次數 = {"session": 0, "csrf": 0}
    def session():
        次數["session"] += 1
        return 網頁使用者("owner-1", "alice", "member")
    def csrf():
        次數["csrf"] += 1
        return 網頁使用者("owner-1", "alice", "member")
    路由 = 建立安全草稿路由器(服務, session, csrf, 時鐘=lambda: 100.0)
    assert [項目.path for 項目 in 路由.routes] == ["/api/published-endpoints/draft"]
    app = FastAPI()
    app.include_router(路由)
    return TestClient(app), 服務, 次數

def test_CP4_PLANNER_HTTP_01_exact_body_owner_session與CSRF各一次():
    client, 服務, 次數 = _客戶端()
    with client:
        回應 = client.post("/api/published-endpoints/draft", json={
            "original_requirement_text": "建立 API", "selected_skills": ["alpha"],
            "response_mode": "structured",
        })
    assert 回應.status_code == 201
    assert set(回應.json()) == {"draft_id", "expires_at", "preview"}
    assert 服務.呼叫 == [("owner-1", "建立 API", ("alpha",), "structured", 100.0)]
    assert 次數 == {"session": 1, "csrf": 1}

def test_CP4_PLANNER_HTTP_02_duplicate_extra_client_tools與錯誤content_type皆在服務前拒絕():
    client, 服務, _ = _客戶端()
    cases = [
        ('{"original_requirement_text":"x","original_requirement_text":"y","selected_skills":["alpha"],"response_mode":"text"}', "application/json"),
        ('{"original_requirement_text":"x","selected_skills":["alpha"],"response_mode":"text","planner_content":{}}', "application/json"),
        ('{"original_requirement_text":"x","selected_skills":["alpha"],"response_mode":"text","selected_tools":[]}', "application/json"),
        ('{"original_requirement_text":"x","selected_skills":["alpha"],"response_mode":"text"}', "application/json; charset=utf-8"),
    ]
    with client:
        for body, content_type in cases:
            回應 = client.post("/api/published-endpoints/draft", content=body, headers={"content-type": content_type})
            assert 回應.status_code == 422
            assert 回應.json() == {"detail": {"code": "invalid_request"}}
    assert 服務.呼叫 == []

def test_CP4_PLANNER_HTTP_03_raw_body_cap排序唯一與非object矩陣():
    client, 服務, _ = _客戶端()
    bodies = [
        "x" * 32769,
        '[]',
        '{"original_requirement_text":"x","selected_skills":[],"response_mode":"text"}',
        '{"original_requirement_text":"x","selected_skills":["alpha","alpha"],"response_mode":"text"}',
        '{"original_requirement_text":"x","selected_skills":["beta","alpha"],"response_mode":"text"}',
    ]
    with client:
        for body in bodies:
            回應 = client.post("/api/published-endpoints/draft", content=body, headers={"content-type": "application/json"})
            assert 回應.status_code == 422
    assert 服務.呼叫 == []
