"""INV I05 external invoke HTTP route contract。"""

import asyncio
import json
from dataclasses import dataclass

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from 繁中代理.發布介面.呼叫.錯誤映射 import 映射呼叫錯誤
from 繁中代理.發布介面.呼叫.編排器 import 呼叫成功結果
from 繁中代理.發布介面.契約 import 建立成功信封
from 繁中代理.發布介面.路由.外部呼叫 import 建立外部呼叫路由
from 繁中代理.發布介面.領域模型 import EndpointRef, InvocationRef


端點 = EndpointRef("ep", "demo", 2)
呼叫 = InvocationRef("inv", "req-fixed")


@dataclass
class 假編排器:
    結果: object

    def __post_init__(self):
        self.呼叫 = []

    def 執行(self, slug, request_id, api_key, input, metadata, at):
        self.呼叫.append((slug, request_id, api_key, input, metadata, at))
        if isinstance(self.結果, BaseException):
            raise self.結果
        return self.結果


def _成功():
    return 呼叫成功結果(建立成功信封(端點, 呼叫, {"answer": 1}))


def _客戶端(結果=None, *, 最大=65536, 產生器=lambda: "req-fixed"):
    編排器 = 假編排器(_成功() if 結果 is None else 結果)
    app = FastAPI(redirect_slashes=False)
    app.include_router(建立外部呼叫路由(編排器, 請求識別產生器=產生器, 時鐘=lambda: 123.5, 本文最大位元組=最大))
    return TestClient(app, raise_server_exceptions=False), 編排器, app


def _送出(client, body=b'{"input":{"q":1}}', headers=None):
    標頭 = {"Authorization": "Bearer raw-secret", "Content-Type": "application/json"}
    標頭.update(headers or {})
    return client.post("/v1/endpoints/demo/invoke", content=body, headers=標頭)


def test_精確路徑方法與OpenAPI且不重導斜線():
    client, _, app = _客戶端()
    assert _送出(client).status_code == 200
    assert client.get("/v1/endpoints/demo/invoke").status_code == 405
    assert client.post("/v1/endpoints/demo/invoke/", follow_redirects=False).status_code == 404
    assert client.post("/v1/endpoints/unknown/other").status_code == 404
    路徑 = app.openapi()["paths"]
    assert list(路徑) == ["/v1/endpoints/{slug}/invoke"]
    assert list(路徑["/v1/endpoints/{slug}/invoke"]) == ["post"]
    schema = 路徑["/v1/endpoints/{slug}/invoke"]["post"]["requestBody"]["content"]["application/json"]["schema"]
    assert schema["required"] == ["input"] and schema["additionalProperties"] is False


def test_成功傳入精確欄位且回fresh七欄JSON():
    client, 編排器, _ = _客戶端()
    response = _送出(client, b'{"input":[1,true,null],"metadata":{"trace":"x"}}')
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/json"
    assert list(response.json()) == ["ok", "endpoint", "invocation", "data", "usage", "warnings", "error"]
    assert 編排器.呼叫 == [("demo", "req-fixed", "raw-secret", [1, True, None], {"trace": "x"}, 123.5)]
    assert "retry-after" not in response.headers


@pytest.mark.parametrize("metadata", ["", ",\"metadata\":null", ",\"metadata\":{}"])
def test_metadata省略null或物件(metadata):
    client, 編排器, _ = _客戶端()
    assert _送出(client, ('{"input":"hi"' + metadata + '}').encode()).status_code == 200
    assert 編排器.呼叫[0][4] is None or 編排器.呼叫[0][4] == {}


@pytest.mark.parametrize("body", [
    b"", b"{", b"[]", b'{"input":1,"input":2}', b'{"input":NaN}',
    b'{"input":Infinity}', b'{"input":1,"extra":2}', b'{"metadata":{}}',
    b'{"input":1,"metadata":[]}', b'{"input":"\xff"}',
])
def test_拒絕無效UTF8_JSON重複鍵非有限值頂層與欄位(body):
    client, 編排器, _ = _客戶端()
    response = _送出(client, body)
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "invalid_request"
    assert 編排器.呼叫 == []


def test_ContentLength早拒與實際串流都受本文byte上限():
    body = b'{"input":"1234"}'
    client, 編排器, _ = _客戶端(最大=len(body))
    assert _送出(client, body).status_code == 200
    assert _送出(client, body + b" ").status_code == 413
    assert _送出(client, body, {"Content-Length": str(len(body) + 1)}).status_code == 413
    streamed = client.post(
        "/v1/endpoints/demo/invoke", content=iter([body, b" "]),
        headers={"Authorization": "Bearer raw-secret", "Content-Type": "application/json"},
    )
    assert streamed.status_code == 413
    assert len(編排器.呼叫) == 1


def test_病態超長ContentLength固定視為無效請求而非500():
    client, 編排器, _ = _客戶端()
    response = _送出(client, headers={"Content-Length": "9" * 5000})
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "invalid_request"
    assert 編排器.呼叫 == []


@pytest.mark.parametrize("authorization", [None, "", "Bearer", "Bearer ", "Basic abc", "bearer abc", "Bearer a b"])
def test_Authorization只接受精確Bearer文法且header名稱不分大小寫(authorization):
    client, 編排器, _ = _客戶端()
    headers = {"Content-Type": "application/json"}
    if authorization is not None:
        headers["authorization"] = authorization
    response = client.post("/v1/endpoints/demo/invoke", content=b'{"input":1}', headers=headers)
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "invalid_api_key"
    assert 編排器.呼叫 == []
    assert "retry-after" not in response.headers


def test_slug_header_request_id與本文資源上限在adapter前關閉():
    client, 編排器, _ = _客戶端(產生器=lambda: "x" * 129)
    assert _送出(client).status_code == 500
    assert 編排器.呼叫 == []
    client, 編排器, _ = _客戶端()
    assert client.post("/v1/endpoints/" + "s" * 129 + "/invoke", headers={"Authorization": "Bearer k"}, json={"input": 1}).status_code == 400
    assert client.post("/v1/endpoints/demo/invoke", headers={"Authorization": "Bearer " + "k" * 4097}, json={"input": 1}).status_code == 401
    assert 編排器.呼叫 == []


@pytest.mark.parametrize("code,status", [
    ("endpoint_not_found", 404), ("invalid_api_key", 401), ("api_key_expired", 401),
    ("endpoint_disabled", 403), ("endpoint_archived", 410), ("input_schema_invalid", 422),
    ("model_output_schema_invalid", 502), ("model_timeout", 504),
    ("tool_execution_failed", 502), ("tool_timeout", 504),
    ("endpoint_misconfigured", 500), ("internal_error", 500),
])
def test_所有I01錯誤狀態與七欄信封直接轉送且無RetryAfter(code, status):
    結果 = 映射呼叫錯誤(code) if code == "endpoint_not_found" else 映射呼叫錯誤(code, endpoint=端點, invocation=呼叫)
    client, _, _ = _客戶端(結果)
    response = _送出(client)
    assert response.status_code == status
    assert list(response.json()) == ["ok", "endpoint", "invocation", "data", "usage", "warnings", "error"]
    assert response.json()["error"]["code"] == code
    assert "retry-after" not in response.headers


@pytest.mark.parametrize(("decision", "scope"), [("endpoint", "endpoint"), ("credential", "credential"), ("both", "endpoint")])
def test_429各層決策標頭與同一details秒數完全一致(decision, scope):
    del decision
    結果 = 映射呼叫錯誤("rate_limit_exceeded", endpoint=端點, invocation=呼叫, details={"scope": scope, "retry_after_seconds": 17})
    client, _, _ = _客戶端(結果)
    response = _送出(client)
    assert response.status_code == 429 and response.headers["retry-after"] == "17"
    assert response.json()["error"]["details"] == {"scope": scope, "retry_after_seconds": 17}


@pytest.mark.parametrize("結果", [{"status_code": 200}, object()])
def test_拒絕任意adapter結果並回固定internal信封(結果):
    client, _, _ = _客戶端(結果)
    response = _送出(client)
    assert response.status_code == 500 and response.json()["error"]["code"] == "internal_error"
    assert "retry-after" not in response.headers


def test_偽造exact結果的狀態標頭provenance不一致固定失敗():
    結果 = 映射呼叫錯誤("rate_limit_exceeded", endpoint=端點, invocation=呼叫, details={"scope": "endpoint", "retry_after_seconds": 9})
    object.__setattr__(結果, "_標頭項目", (("Retry-After", "8"),))
    client, _, _ = _客戶端(結果)
    response = _送出(client)
    assert response.status_code == 500 and "retry-after" not in response.headers


def test_錯誤結果instance_shadow_serializer不能繞過I01來源():
    結果 = 映射呼叫錯誤("internal_error", endpoint=端點, invocation=呼叫)
    object.__setattr__(結果, "to_json", lambda: {
        "status_code": 201, "headers": {"X-Forged": "yes"},
        "envelope": {"ok": True, "endpoint": None, "invocation": None, "data": {"forged": 1},
                     "usage": None, "warnings": [], "error": None},
    })
    client, _, _ = _客戶端(結果)
    response = _送出(client)
    assert response.status_code == 500
    assert response.json()["error"]["code"] == "internal_error"
    assert "x-forged" not in response.headers


class 自訂基礎錯誤(BaseException):
    pass


@pytest.mark.parametrize("錯誤", [RuntimeError("raw-secret"), 自訂基礎錯誤("raw-secret")])
def test_adapter例外不外洩raw_key且固定internal(錯誤):
    client, _, _ = _客戶端(錯誤)
    response = _送出(client)
    assert response.status_code == 500
    assert response.json()["error"]["code"] == "internal_error"
    assert "raw-secret" not in response.text and "raw-secret" not in repr(response.headers)


@pytest.mark.parametrize("錯誤類型", [KeyboardInterrupt, SystemExit, GeneratorExit])
def test_KISG控制流程原樣穿透(錯誤類型):
    _, _, app = _客戶端(錯誤類型("stop"))
    endpoint = next(route.endpoint for route in app.routes if getattr(route, "path", "") == "/v1/endpoints/{slug}/invoke")
    sent = False

    async def receive():
        nonlocal sent
        if sent:
            return {"type": "http.disconnect"}
        sent = True
        return {"type": "http.request", "body": b'{"input":1}', "more_body": False}

    request = Request({"type": "http", "method": "POST", "path": "/", "headers": [(b"authorization", b"Bearer raw-secret")], "query_string": b""}, receive)
    with pytest.raises(錯誤類型, match="stop"):
        asyncio.run(endpoint(請求=request, 路徑短名="demo"))
