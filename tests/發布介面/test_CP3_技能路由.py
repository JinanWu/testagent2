"""CP3 WEB-SKILL：exact skills 路由契約測試。"""

from fastapi import FastAPI
from fastapi.testclient import TestClient

from 繁中代理.發布介面.Web代理服務 import Web服務不可用, Web資源不存在, 技能詳情, 技能項目
from 繁中代理.發布介面.網頁工作階段 import 網頁使用者
from 繁中代理.發布介面.路由.技能 import 建立技能路由器


class 假技能服務:
    """記錄 current-session user 與 skill ID 查詢。"""

    def __init__(self, 錯誤=None):
        self.錯誤, self.呼叫 = 錯誤, []

    def 列出技能(self, 使用者識別碼):
        self.呼叫.append(("list", 使用者識別碼))
        if self.錯誤:
            raise self.錯誤
        return (技能項目("demo", "demo", "tools", "說明"),)

    def 讀取技能(self, 使用者識別碼, 技能識別碼):
        self.呼叫.append(("detail", 使用者識別碼, 技能識別碼))
        if self.錯誤:
            raise self.錯誤
        return 技能詳情(技能項目("demo", "demo", "tools", "說明"), "# Demo")


def _客戶端(服務):
    """用 caller-injected current-session dependency 建立測試 app。"""
    def 目前使用者():
        return 網頁使用者("user-1", "alice", "member")

    應用 = FastAPI()
    路由器 = 建立技能路由器(服務, 目前使用者)
    assert 路由器.prefix == "/api/skills"
    assert [路由.path for 路由 in 路由器.routes] == ["/api/skills", "/api/skills/{skill_id}"]
    應用.include_router(路由器)
    return TestClient(應用)


def test_技能路由只使用current_session並回固定allowlist():
    """CP3-WEB-ROUTE-SKILL-01：list/detail 不接受 caller 指定 owner。"""
    服務 = 假技能服務()
    with _客戶端(服務) as 客戶端:
        列表 = 客戶端.get("/api/skills")
        詳情 = 客戶端.get("/api/skills/demo")

    assert 服務.呼叫 == [("list", "user-1"), ("detail", "user-1", "demo")]
    assert 列表.json() == {"skills": [{"id": "demo", "name": "demo", "category": "tools", "description": "說明"}]}
    assert 詳情.json() == {"id": "demo", "name": "demo", "category": "tools", "description": "說明", "content": "# Demo"}
    assert "path" not in 列表.text + 詳情.text


def test_技能路由限制識別碼並固定映射404與503():
    """CP3-WEB-ROUTE-SKILL-02：unsafe/missing 與 I/O failure 使用固定分類。"""
    with _客戶端(假技能服務()) as 客戶端:
        標記 = "PATH_SKILL_SECRET"
        無效回應 = 客戶端.get("/api/skills/" + 標記 * 10)
        assert 無效回應.status_code == 422
        assert 無效回應.json() == {"detail": {"code": "invalid_request"}}
        assert 標記 not in 無效回應.text

    with _客戶端(假技能服務(Web資源不存在("path secret"))) as 客戶端:
        回應 = 客戶端.get("/api/skills/missing")
    assert 回應.status_code == 404
    assert 回應.json() == {"detail": {"code": "skill_not_found"}}
    assert "secret" not in 回應.text

    with _客戶端(假技能服務(Web服務不可用("io secret"))) as 客戶端:
        回應 = 客戶端.get("/api/skills")
    assert 回應.status_code == 503
    assert 回應.json() == {"detail": {"code": "skills_unavailable"}}


def test_技能繁中路徑參數維持skill_id別名():
    """Quality P2：Python 參數使用繁中，OpenAPI path alias 仍是 frozen skill_id。"""
    with _客戶端(假技能服務()) as 客戶端:
        參數 = 客戶端.get("/openapi.json").json()["paths"]["/api/skills/{skill_id}"]["get"]["parameters"]
    assert [(項目["name"], 項目["in"]) for 項目 in 參數] == [("skill_id", "path")]
