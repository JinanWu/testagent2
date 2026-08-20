"""CP4-W1-T3：三條 management routes 的 canonical 安全工廠。"""
import asyncio
from concurrent.futures import ThreadPoolExecutor
from threading import Event, Lock

import pytest
from fastapi import FastAPI, Response
from fastapi import HTTPException
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient
from jsonschema import Draft202012Validator

from 繁中代理.使用者 import 使用者上下文
from 繁中代理.發布介面.相依項 import 發布介面相依項
from 繁中代理.發布介面.應用程式 import 建立應用程式
from 繁中代理.發布介面.設定 import 網頁安全設定
from 繁中代理.發布介面.網頁工作階段 import 網頁工作階段服務
from 繁中代理.發布介面.網頁工作階段 import 網頁使用者
from 繁中代理.發布介面.規劃.綱要 import 規劃服務
from 繁中代理.發布介面.路由.規劃發布 import (
    端點發布結果,
    版本建立結果,
    建立發布版本路由器,
    建立安全規劃發布路由器,
    OpenAPI本文符合專案契約,
)
from 繁中代理.發布介面.路由.網頁認證 import 建立CSRF相依項


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


@pytest.mark.parametrize("path,body,expected_status", [
    ("/api/published-endpoints/draft", _草稿本文(), 503),
    ("/api/published-endpoints", _發布本文(), 500),
    ("/api/published-endpoints/endpoint-1/versions", {"configuration": {}}, 500),
])
def test_management_handler錯誤仍交付已消耗CSRF的successor(path, body, expected_status):
    class 失敗草稿:
        def 建立草稿(self, *_args, **_kwargs):
            raise RuntimeError

    class 失敗管理:
        def 原子發布(self, **_kwargs):
            raise RuntimeError
        def 原子建立並切換版本(self, **_kwargs):
            raise RuntimeError

    def session():
        return 網頁使用者("owner-1", "alice", "admin")

    def csrf(response: Response):
        response.headers["X-CSRF-Token"] = "successor"
        response.headers.append("set-cookie", "csrf_token=successor; Path=/; SameSite=strict")
        return 網頁使用者("owner-1", "alice", "admin")

    app = FastAPI(redirect_slashes=False)
    app.include_router(建立安全規劃發布路由器(
        失敗草稿(), 失敗管理(), session, csrf, 時鐘=lambda: 100.0,
    ))
    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.post(path, json=body)
    assert response.status_code == expected_status
    assert response.headers["X-CSRF-Token"] == "successor"
    assert "csrf_token=successor" in response.headers["set-cookie"]


def _含標記(值, 標記, 已訪):
    if 值 is None or id(值) in 已訪:
        return False
    已訪.add(id(值))
    if type(值) is str:
        return 標記 in 值
    if type(值) is bytes:
        return 標記.encode() in 值
    if type(值) is dict:
        return any(_含標記(項, 標記, 已訪) for 對 in dict.items(值) for 項 in 對)
    if type(值) in (list, tuple, set, frozenset):
        return any(_含標記(項, 標記, 已訪) for 項 in 值)
    欄位 = getattr(type(值), "__slots__", ())
    if type(欄位) is str:
        欄位 = (欄位,)
    for 欄 in 欄位:
        try:
            if _含標記(object.__getattribute__(值, 欄), 標記, 已訪):
                return True
        except (AttributeError, TypeError):
            pass
    return False


def _請求(本文):
    原始 = __import__("json").dumps(本文, ensure_ascii=False).encode()
    已送 = False
    async def receive():
        nonlocal 已送
        if 已送:
            return {"type": "http.request", "body": b"", "more_body": False}
        已送 = True
        return {"type": "http.request", "body": 原始, "more_body": False}
    from starlette.requests import Request
    return Request({"type": "http", "method": "POST", "path": "/", "headers": [(b"content-type", b"application/json")]}, receive)


def test_CP4_W1_T3_01單一工廠路由_OpenAPI與canonical相依identity():
    client, 路由器, _, _, 次數, session, csrf = _建立()
    assert 路由器.prefix == "/api/published-endpoints"
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


def test_CP4_W1_T3_06三個POST_OpenAPI本文皆為完整嚴格契約():
    client, *_ = _建立()
    with client:
        路徑 = client.get("/openapi.json").json()["paths"]
    草稿 = 路徑["/api/published-endpoints/draft"]["post"]["requestBody"]
    發布 = 路徑["/api/published-endpoints"]["post"]["requestBody"]
    版本 = 路徑["/api/published-endpoints/{endpoint_id}/versions"]["post"]["requestBody"]
    for 本文 in (草稿, 發布, 版本):
        assert 本文["required"] is True
        assert set(本文["content"]) == {"application/json"}
        綱要 = 本文["content"]["application/json"]["schema"]
        assert 綱要["type"] == "object" and 綱要["additionalProperties"] is False
        assert set(綱要["required"]) == set(綱要["properties"])
    草稿欄位 = 草稿["content"]["application/json"]["schema"]["properties"]
    assert 草稿欄位["original_requirement_text"] == {
        "type": "string", "minLength": 1, "x-maxUtf8Bytes": 16_384,
    }
    assert 草稿欄位["selected_skills"] == {
        "type": "array", "minItems": 1, "maxItems": 32, "uniqueItems": True,
        "items": {"type": "string", "minLength": 1, "maxLength": 128,
                  "pattern": "^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$"},
    }
    發布欄位 = 發布["content"]["application/json"]["schema"]["properties"]
    assert 發布欄位["draft_id"]["pattern"] == "^[A-Za-z0-9_.:-]+$"
    assert 發布欄位["slug"]["pattern"] == "^[a-z0-9][a-z0-9-]*$"
    assert 發布欄位["configuration_confirmation"]["maxProperties"] == 256
    assert 發布欄位["configuration_confirmation"]["propertyNames"] == {
        "type": "string", "x-maxUtf8Bytes": 256,
    }
    assert 版本["content"]["application/json"]["schema"]["properties"]["configuration"]["maxProperties"] == 256


def test_CP4_W1_T3_09專案OpenAPI契約明確執行UTF8位元組上限():
    client, *_ = _建立()
    with client:
        路徑 = client.get("/openapi.json").json()["paths"]
    草稿綱要 = 路徑["/api/published-endpoints/draft"]["post"]["requestBody"]["content"]["application/json"]["schema"]
    發布綱要 = 路徑["/api/published-endpoints"]["post"]["requestBody"]["content"]["application/json"]["schema"]
    超位元需求 = {"original_requirement_text": "界" * 5_462, "selected_skills": ["alpha"], "response_mode": "text"}
    百個中文字鍵 = {"configuration_confirmation": {"界" * 100: 1}, "draft_id": "d", "slug": "safe"}
    assert Draft202012Validator(草稿綱要).is_valid(超位元需求)
    assert Draft202012Validator(發布綱要).is_valid(百個中文字鍵)
    assert not OpenAPI本文符合專案契約(草稿綱要, 超位元需求)
    assert not OpenAPI本文符合專案契約(發布綱要, 百個中文字鍵)
    assert OpenAPI本文符合專案契約(草稿綱要, _草稿本文())


@pytest.mark.parametrize("錯誤", [KeyboardInterrupt("K"), SystemExit("S"), GeneratorExit("G")])
@pytest.mark.parametrize("路由索引,本文,必要框", [
    (1, {"draft_id": "d", "slug": "safe", "configuration_confirmation": {"marker": "ROUTE-SENSITIVE"}}, {"發布端點", "_安全發布端點", "_呼叫服務", "_重拋控制流"}),
    (2, {"configuration": {"marker": "ROUTE-SENSITIVE"}}, {"建立不可變版本", "_安全建立版本", "_呼叫服務", "_重拋控制流"}),
])
def test_CP4_W1_T3_10控制流identity_args不變且路由traceback零敏感locals(錯誤, 路由索引, 本文, 必要框):
    class _失敗服務:
        def 原子發布(self, **_):
            raise 錯誤
        def 原子建立並切換版本(self, **_):
            raise 錯誤
    路由器 = 建立安全規劃發布路由器(_草稿服務(), _失敗服務(), lambda: None, lambda: None)
    端點 = 路由器.routes[路由索引].endpoint
    使用者 = 網頁使用者("owner-ROUTE-SENSITIVE", "alice", "member")
    參數 = (_請求(本文), 使用者, 使用者) if 路由索引 == 1 else (_請求(本文), "endpoint-1", 使用者, 使用者)
    原args = 錯誤.args
    錯誤.__traceback__ = None
    with pytest.raises(type(錯誤)) as 捕捉:
        asyncio.run(端點(*參數))
    assert 捕捉.value is 錯誤 and 錯誤.args == 原args
    名稱 = set()
    追蹤 = 錯誤.__traceback__
    while 追蹤 is not None:
        框 = 追蹤.tb_frame
        if 框.f_code.co_filename.endswith("規劃發布.py"):
            名稱.add(框.f_code.co_name)
            assert all(not _含標記(值, "ROUTE-SENSITIVE", set()) for 值 in tuple(框.f_locals.values()))
        追蹤 = 追蹤.tb_next
    assert 必要框 <= 名稱


def test_CP4_W1_T3_07canonical路由通過production組裝政策(tmp_path):
    設定 = 網頁安全設定(("https://example.test",), Cookie安全=True, 工作階段有效秒數=60)
    工作階段服務 = 網頁工作階段服務(tmp_path / "session.sqlite3", 有效秒數=60)
    csrf = 建立CSRF相依項(工作階段服務, 設定)
    路由器 = 建立安全規劃發布路由器(
        _草稿服務(), _管理服務(), lambda: 網頁使用者("owner-1", "alice", "member"), csrf,
    )
    應用 = 建立應用程式(發布介面相依項((路由器,), ()))
    assert {(方法, 路由.path) for 路由 in 應用.routes if isinstance(路由, APIRoute)
            for 方法 in 路由.methods} == {
        ("GET", "/healthz"),
        ("POST", "/api/published-endpoints/draft"),
        ("POST", "/api/published-endpoints"),
        ("POST", "/api/published-endpoints/{endpoint_id}/versions"),
    }
    規格路徑 = 應用.openapi()["paths"]
    assert all("requestBody" in 規格路徑[路徑]["post"] for 路徑 in 規格路徑 if 路徑 != "/healthz")


def test_CP4_W1_T3_08legacy版本工廠符合新服務協定且不傳管理者聲明():
    class _精確服務:
        def __init__(self):
            self.呼叫 = []

        def 原子建立並切換版本(self, *, 擁有者使用者識別碼, 端點識別碼, 配置):
            self.呼叫.append((擁有者使用者識別碼, 端點識別碼, 配置))
            return 版本建立結果(端點識別碼, "version-2", 2, "version-2", False)

    服務 = _精確服務()
    身份 = lambda: 使用者上下文(user_id="owner-1", is_admin=True)
    app = FastAPI(redirect_slashes=False)
    app.include_router(建立發布版本路由器(服務, 身份))
    with TestClient(app, raise_server_exceptions=False) as client:
        回應 = client.post(
            "/api/published-endpoints/endpoint-1/versions", json={"configuration": {"x": 1}},
        )
    assert 回應.status_code == 201
    assert 服務.呼叫 == [("owner-1", "endpoint-1", {"x": 1})]


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
