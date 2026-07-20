"""CP4-W1-T3：三條 management routes 的 canonical 安全工廠。"""
from concurrent.futures import ThreadPoolExecutor
from threading import Event, Lock

from fastapi import FastAPI
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from 繁中代理.發布介面.網頁工作階段 import 網頁使用者
from 繁中代理.發布介面.規劃.綱要 import 規劃服務
from 繁中代理.發布介面.路由.規劃發布 import (
    端點發布結果,
    版本建立結果,
    建立安全規劃發布路由器,
)


class _草稿服務:
    def __init__(self):
        self.呼叫 = []
        self._草稿 = 規劃服務(識別碼產生器=lambda: "draft-safe")

    def 建立草稿(self, owner, requirement, skills, mode, *, 現在):
        self.呼叫.append((owner, requirement, skills, mode, 現在))
        return self._草稿.建立草稿(owner, requirement, {
            "endpoint_name": "API", "suggested_slug": "safe-api", "behavior_summary": "摘要",
            "selected_skills": ["alpha"], "recommended_tools": [], "tool_capabilities": {},
            "system_prompt": "提示", "input_schema": None, "response_schema": {"type": "object"},
            "human_docs": "文件", "rate_limit": {"endpoint_per_minute": 60, "credential_per_minute": 30},
            "warnings": [],
        }, 現在=現在)


class _管理服務:
    def __init__(self):
        self.發布呼叫 = []
        self.版本呼叫 = []

    def 原子發布(self, **參數):
        self.發布呼叫.append(參數)
        return 端點發布結果("endpoint-1", "version-1", 1, "active", "pak_INITIAL_SECRET_123")

    def 原子建立並切換版本(self, **參數):
        self.版本呼叫.append(參數)
        return 版本建立結果("endpoint-1", "version-2", 2, "version-2", False)


def _建立(草稿=None, 管理=None, *, csrf_id="owner-1"):
    草稿, 管理 = 草稿 or _草稿服務(), 管理 or _管理服務()
    次數 = {"session": 0, "csrf": 0}

    def session():
        次數["session"] += 1
        return 網頁使用者("owner-1", "alice", "admin")

    def csrf():
        次數["csrf"] += 1
        return 網頁使用者(csrf_id, "alice", "member")

    路由器 = 建立安全規劃發布路由器(草稿, 管理, session, csrf, 時鐘=lambda: 100.0)
    app = FastAPI(redirect_slashes=False)
    app.include_router(路由器)
    return TestClient(app, raise_server_exceptions=False), 路由器, 草稿, 管理, 次數, session, csrf


def _草稿本文():
    return {"original_requirement_text": "建立 API", "selected_skills": ["alpha"], "response_mode": "text"}


def _發布本文():
    return {"draft_id": "draft-safe", "slug": "safe-api", "configuration_confirmation": {"system_prompt": "safe"}}


def test_CP4_W1_T3_01單一工廠路由_OpenAPI與canonical相依identity():
    client, 路由器, _, _, 次數, session, csrf = _建立()
    assert [r.path for r in 路由器.routes] == [
        "/api/published-endpoints/draft", "/api/published-endpoints",
        "/api/published-endpoints/{endpoint_id}/versions",
    ]
    for route in 路由器.routes:
        assert type(route) is APIRoute
        assert [d.call for d in route.dependant.dependencies] == [session, csrf]
    with client:
        規格 = client.get("/openapi.json").json()
        for path in [r.path for r in 路由器.routes]:
            assert set(規格["paths"][path]) == {"post"}
        assert client.post("/api/published-endpoints/draft", json=_草稿本文()).status_code == 201
        assert client.post("/api/published-endpoints", json=_發布本文()).status_code == 201
        assert client.post("/api/published-endpoints/endpoint-1/versions", json={"configuration": {}}).status_code == 201
    assert 次數 == {"session": 3, "csrf": 3}


def test_CP4_W1_T3_02只傳權威user_id且不信任UI_role():
    _, _, _, 管理, _, _, _ = _建立()
    client, _, _, 管理, _, _, _ = _建立(管理=管理)
    with client:
        client.post("/api/published-endpoints", json=_發布本文())
        回應 = client.post("/api/published-endpoints/endpoint-1/versions", json={"configuration": {"x": 1}})
    assert 回應.status_code == 201
    assert 管理.發布呼叫[0]["擁有者使用者識別碼"] == "owner-1"
    assert 管理.版本呼叫 == [{
        "擁有者使用者識別碼": "owner-1", "端點識別碼": "endpoint-1", "配置": {"x": 1},
    }]


def test_CP4_W1_T3_03_CSRF_identity不一致固定500且服務零呼叫():
    client, _, 草稿, 管理, _, _, _ = _建立(csrf_id="other-user")
    with client:
        responses = [
            client.post("/api/published-endpoints/draft", json=_草稿本文()),
            client.post("/api/published-endpoints", json=_發布本文()),
            client.post("/api/published-endpoints/endpoint-1/versions", json={"configuration": {}}),
        ]
    assert [r.status_code for r in responses] == [500, 500, 500]
    assert 草稿.呼叫 == 管理.發布呼叫 == 管理.版本呼叫 == []


def test_CP4_W1_T3_04三條body皆bounded_strict_JSON且固定422():
    client, _, 草稿, 管理, _, _, _ = _建立()
    cases = [
        ("/api/published-endpoints/draft", b'{"original_requirement_text":"x","original_requirement_text":"y","selected_skills":["alpha"],"response_mode":"text"}'),
        ("/api/published-endpoints", b'{"draft_id":"d","slug":"safe","configuration_confirmation":{},"extra":1}'),
        ("/api/published-endpoints/endpoint-1/versions", b'{"configuration":{},"configuration":{}}'),
        ("/api/published-endpoints", b"x" * 32769),
    ]
    with client:
        for path, body in cases:
            response = client.post(path, content=body, headers={"content-type": "application/json"})
            assert (response.status_code, response.json()) == (422, {"detail": {"code": "invalid_request"}})
    assert 草稿.呼叫 == 管理.發布呼叫 == 管理.版本呼叫 == []


def test_CP4_W1_T3_05三個同步服務操作都在threadpool並行():
    class _閘門:
        def __init__(self):
            self.lock, self.entered, self.both = Lock(), 0, Event()
        def wait(self):
            with self.lock:
                self.entered += 1
                if self.entered == 2:
                    self.both.set()
            assert self.both.wait(timeout=0.25)

    for kind in ("draft", "publish", "version"):
        gate = _閘門()
        草稿, 管理 = _草稿服務(), _管理服務()
        if kind == "draft":
            original = 草稿.建立草稿
            result = original("owner-1", "建立 API", ("alpha",), "text", 現在=100.0)
            def slow(*args, **kwargs): gate.wait(); return result
            草稿.建立草稿 = slow
            path, body = "/api/published-endpoints/draft", _草稿本文()
        elif kind == "publish":
            original = 管理.原子發布
            def slow(**kwargs): gate.wait(); return original(**kwargs)
            管理.原子發布 = slow
            path, body = "/api/published-endpoints", _發布本文()
        else:
            original = 管理.原子建立並切換版本
            def slow(**kwargs): gate.wait(); return original(**kwargs)
            管理.原子建立並切換版本 = slow
            path, body = "/api/published-endpoints/endpoint-1/versions", {"configuration": {}}
        client, *_ = _建立(草稿, 管理)
        with client, ThreadPoolExecutor(max_workers=2) as pool:
            responses = list(pool.map(lambda _: client.post(path, json=body), range(2)))
        assert [r.status_code for r in responses] == [201, 201]
