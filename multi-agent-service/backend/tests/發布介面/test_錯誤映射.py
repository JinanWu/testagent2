"""穩定外部呼叫錯誤映射器的契約與安全回歸測試。"""

from dataclasses import FrozenInstanceError, dataclass
import traceback

import pytest

from 繁中代理.發布介面.呼叫.錯誤映射 import (
    錯誤映射結果,
    錯誤映射錯誤,
    映射呼叫錯誤,
)
from 繁中代理.發布介面.領域模型 import EndpointRef, InvocationRef


端點 = EndpointRef("endpoint-1", "demo", 3)
呼叫 = InvocationRef("invocation-1", "request-1", "session-1")
錯誤契約 = [
    ("endpoint_not_found", 404, "找不到 endpoint slug。"),
    ("invalid_api_key", 401, "API key 無效。"),
    ("api_key_expired", 401, "API key 已過期。"),
    ("endpoint_disabled", 403, "Endpoint 已停用。"),
    ("endpoint_archived", 410, "Endpoint 已封存。"),
    ("input_schema_invalid", 422, "Input 不符合 schema。"),
    ("model_output_schema_invalid", 502, "模型輸出不符合 response schema。"),
    ("rate_limit_exceeded", 429, "呼叫頻率超過限制。"),
    ("model_timeout", 504, "模型供應商逾時。"),
    ("tool_execution_failed", 502, "工具執行失敗。"),
    ("tool_timeout", 504, "工具執行逾時。"),
    ("endpoint_misconfigured", 500, "Endpoint 設定錯誤。"),
    ("internal_error", 500, "伺服器內部錯誤。"),
]


def _映射(code, *, details=None):
    if code == "endpoint_not_found":
        return 映射呼叫錯誤(code, details=details)
    if code == "rate_limit_exceeded" and details is None:
        details = {"scope": "endpoint", "retry_after_seconds": 30}
    return 映射呼叫錯誤(code, endpoint=端點, invocation=呼叫, details=details)


@pytest.mark.parametrize(("code", "status", "message"), 錯誤契約)
def test_每個固定錯誤精確映射狀態訊息參照與信封形狀(code, status, message):
    結果 = _映射(code)
    輸出 = 結果.to_json()
    信封 = 輸出["envelope"]

    assert type(結果.status_code) is int
    assert 結果.status_code == status
    assert list(信封) == ["ok", "endpoint", "invocation", "data", "usage", "warnings", "error"]
    assert list(信封["error"]) == ["code", "message", "details"]
    assert 信封["error"]["code"] == code
    assert 信封["error"]["message"] == message
    assert 信封["data"] is 信封["usage"] is None
    assert 信封["warnings"] == []
    if code == "endpoint_not_found":
        assert 信封["endpoint"] is 信封["invocation"] is None
    else:
        assert 信封["endpoint"] == {"id": "endpoint-1", "slug": "demo", "version": 3}
        assert 信封["invocation"] == {
            "id": "invocation-1", "request_id": "request-1", "session_id": "session-1"
        }


@pytest.mark.parametrize("秒數", [0, 60])
@pytest.mark.parametrize("scope", ["endpoint", "credential"])
def test_限流邊界建立精確細節與標頭(scope, 秒數):
    結果 = _映射("rate_limit_exceeded", details={"scope": scope, "retry_after_seconds": 秒數})
    assert dict(結果.headers) == {"Retry-After": str(秒數)}
    assert 結果.to_json()["envelope"]["error"]["details"] == {
        "scope": scope,
        "retry_after_seconds": 秒數,
    }


class 整數子類(int):
    pass


class 字串子類(str):
    pass


class 字典子類(dict):
    pass


@pytest.mark.parametrize(
    "details",
    [
        None,
        {},
        {"scope": "endpoint"},
        {"retry_after_seconds": 1},
        {"scope": "endpoint", "retry_after_seconds": 1, "extra": 1},
        {"scope": "other", "retry_after_seconds": 1},
        {"scope": 字串子類("endpoint"), "retry_after_seconds": 1},
        {"scope": "endpoint", "retry_after_seconds": -1},
        {"scope": "endpoint", "retry_after_seconds": 61},
        {"scope": "endpoint", "retry_after_seconds": True},
        {"scope": "endpoint", "retry_after_seconds": 整數子類(1)},
        字典子類(scope="endpoint", retry_after_seconds=1),
    ],
)
def test_限流拒絕缺漏額外或非精確細節(details):
    with pytest.raises(錯誤映射錯誤, match="^錯誤映射失敗$"):
        映射呼叫錯誤(
            "rate_limit_exceeded",
            endpoint=端點,
            invocation=呼叫,
            details=details,
        )


@pytest.mark.parametrize("details", [{"x": 1}, {"scope": "endpoint", "retry_after_seconds": 1}])
def test_非限流錯誤拒絕任何非空細節(details):
    with pytest.raises(錯誤映射錯誤):
        _映射("internal_error", details=details)


@pytest.mark.parametrize("details", [None, {}])
def test_非限流錯誤接受省略或精確空字典且沒有標頭(details):
    結果 = _映射("internal_error", details=details)
    assert dict(結果.headers) == {}
    assert 結果.to_json()["envelope"]["error"]["details"] == {}


@pytest.mark.parametrize("code", ["unknown", 字串子類("internal_error"), 500, object()])
def test_未知或非精確代碼固定失敗且不反射輸入(code):
    with pytest.raises(錯誤映射錯誤) as 捕捉:
        映射呼叫錯誤(code, endpoint=端點, invocation=呼叫)
    assert str(捕捉.value) == "錯誤映射失敗"
    assert 捕捉.value.__cause__ is 捕捉.value.__context__ is None


@dataclass(frozen=True)
class 惡意端點(EndpointRef):
    secret: str = "secret"


@dataclass(frozen=True)
class 惡意呼叫(InvocationRef):
    secret: str = "secret"


@pytest.mark.parametrize(
    ("endpoint", "invocation"),
    [(惡意端點("e", "s", 1), 呼叫), (端點, 惡意呼叫("i", "r")), (None, 呼叫), (端點, None)],
)
def test_非not_found要求參照且拒絕子類(endpoint, invocation):
    with pytest.raises(錯誤映射錯誤):
        映射呼叫錯誤("invalid_api_key", endpoint=endpoint, invocation=invocation)


def test_not_found只允許雙空參照():
    with pytest.raises(錯誤映射錯誤):
        映射呼叫錯誤("endpoint_not_found", endpoint=端點)
    with pytest.raises(錯誤映射錯誤):
        映射呼叫錯誤("endpoint_not_found", invocation=呼叫)


@pytest.mark.parametrize(
    ("欄位", "值"),
    [("id", object()), ("slug", ""), ("version", True)],
)
def test_拒絕被竄改的exact端點參照(欄位, 值):
    forged = EndpointRef("e", "s", 1)
    object.__setattr__(forged, 欄位, 值)
    with pytest.raises(錯誤映射錯誤):
        映射呼叫錯誤("internal_error", endpoint=forged, invocation=呼叫)


@pytest.mark.parametrize("欄位", ["id", "request_id", "session_id"])
def test_拒絕被竄改的exact呼叫參照(欄位):
    forged = InvocationRef("i", "r")
    object.__setattr__(forged, 欄位, object())
    with pytest.raises(錯誤映射錯誤):
        映射呼叫錯誤("internal_error", endpoint=端點, invocation=forged)


def test_輸出不可變且每次序列化都是fresh普通容器():
    結果 = _映射("rate_limit_exceeded")
    with pytest.raises(TypeError):
        結果.headers["Retry-After"] = "1"
    with pytest.raises(FrozenInstanceError):
        結果.status_code = 500
    第一次 = 結果.to_json()
    第二次 = 結果.to_json()
    assert type(第一次) is type(第一次["headers"]) is type(第一次["envelope"]) is dict
    assert 第一次 is not 第二次
    assert 第一次["headers"] is not 第二次["headers"]
    assert 第一次["envelope"]["error"]["details"] is not 第二次["envelope"]["error"]["details"]
    第一次["envelope"]["error"]["details"]["retry_after_seconds"] = 9
    assert 結果.to_json()["envelope"]["error"]["details"]["retry_after_seconds"] == 30


def test_結果DTO拒絕bool狀態與可變標頭型別():
    正常 = _映射("internal_error")
    with pytest.raises(錯誤映射錯誤):
        錯誤映射結果(True, 正常.envelope)
    with pytest.raises(錯誤映射錯誤):
        錯誤映射結果(500, 正常.envelope, {"Retry-After": "61"})


def test_結果DTO會脫離呼叫端標頭字典():
    正常 = _映射("rate_limit_exceeded")
    原始標頭 = {"Retry-After": "30"}
    結果 = 錯誤映射結果(429, 正常.envelope, 原始標頭)
    原始標頭["Retry-After"] = "2"
    assert dict(結果.headers) == {"Retry-After": "30"}


def test_結果DTO拒絕狀態碼標頭與錯誤code不一致():
    """公開constructor不得建立繞過固定mapping的transport結果。"""
    一般 = _映射("internal_error")
    限流 = _映射("rate_limit_exceeded")
    for 狀態碼, 信封, 標頭 in (
        (401, 一般.envelope, {}),
        (500, 一般.envelope, {"Retry-After": "1"}),
        (429, 限流.envelope, {}),
        (429, 限流.envelope, {"Retry-After": "29"}),
    ):
        with pytest.raises(錯誤映射錯誤, match="^錯誤映射失敗$"):
            錯誤映射結果(狀態碼, 信封, 標頭)


def test_結果DTO重建信封並脫離來源後續竄改():
    """Result保存canonical envelope，不保留caller-owned nested DTO。"""
    來源 = _映射("internal_error")
    結果 = 錯誤映射結果(500, 來源.envelope, {})
    預期 = 結果.to_json()
    object.__setattr__(來源.envelope.error, "message", "竄改")
    object.__setattr__(來源.envelope.endpoint, "id", "changed")

    assert 結果.envelope is not 來源.envelope
    assert 結果.to_json() == 預期


@pytest.mark.parametrize("竄改類型", ["status", "headers", "message", "endpoint"])
def test_序列化重新驗證結果own_state並拒絕object_setattr竄改(竄改類型):
    """Frozen可被object.__setattr__繞過，transport邊界仍必須fail closed。"""
    結果 = _映射("rate_limit_exceeded" if 竄改類型 == "headers" else "internal_error")
    if 竄改類型 == "status":
        object.__setattr__(結果, "status_code", 401)
    elif 竄改類型 == "headers":
        object.__setattr__(結果, "_標頭項目", (("Retry-After", "29"),))
    elif 竄改類型 == "message":
        object.__setattr__(結果.envelope.error, "message", "竄改訊息")
    else:
        object.__setattr__(結果.envelope.endpoint, "id", object())

    with pytest.raises(錯誤映射錯誤, match="^錯誤映射失敗$"):
        結果.to_json()


def test_限流validator先驗證key_exact_type而不重做敵對hash():
    """Exact dict中的str subclass key不得在boundary被set轉換再次callback。"""
    class 敵對鍵(str):
        def __new__(cls, 值):
            實例 = super().__new__(cls, 值)
            實例.次數 = 0
            return 實例

        def __hash__(self):
            self.次數 += 1
            if self.次數 > 1:
                raise KeyboardInterrupt("不得二次hash")
            return super().__hash__()

    鍵 = 敵對鍵("scope")
    細節 = {鍵: "endpoint", "retry_after_seconds": 1}
    assert 鍵.次數 == 1
    with pytest.raises(錯誤映射錯誤, match="^錯誤映射失敗$"):
        映射呼叫錯誤(
            "rate_limit_exceeded", endpoint=端點, invocation=呼叫, details=細節,
        )
    assert 鍵.次數 == 1


def test_映射器接受固定英文關鍵字介面():
    結果 = 映射呼叫錯誤(code="internal_error", endpoint=端點, invocation=呼叫, details={})
    assert 結果.status_code == 500


@pytest.mark.parametrize(
    "輸入",
    ["唯一SECRET_MARKER_錯誤代碼", {"唯一SECRET_MARKER_細節": object()}],
)
def test_拒絕輸入的production_traceback不保留marker(輸入):
    marker = "唯一SECRET_MARKER"
    with pytest.raises(錯誤映射錯誤) as 捕捉:
        if type(輸入) is str:
            映射呼叫錯誤(輸入, endpoint=端點, invocation=呼叫)
        else:
            映射呼叫錯誤("internal_error", endpoint=端點, invocation=呼叫, details=輸入)
    assert marker not in str(捕捉.value) and marker not in repr(捕捉.value)
    for frame, _ in traceback.walk_tb(捕捉.value.__traceback__):
        if frame.f_globals.get("__name__") == "繁中代理.發布介面.呼叫.錯誤映射":
            assert marker not in repr(frame.f_locals)
