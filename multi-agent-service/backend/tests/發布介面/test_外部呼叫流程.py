"""INV I02 slug、呼叫紀錄與憑證分類的傳輸中立流程。"""

import copy
import gc
import inspect
from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum
from threading import Barrier, BrokenBarrierError, Event, Lock, Thread
import traceback
from types import MappingProxyType, SimpleNamespace
import weakref

import pytest

import 繁中代理.發布介面.呼叫.編排器 as 編排器模組
from 繁中代理.發布介面.呼叫.生產橋接 import 驗證釘選輸入結構

from 繁中代理.發布介面.呼叫.編排器 import (
    執行嘗試結果,
    執行嘗試紀錄收據,
    執行嘗試請求,
    呼叫成功結果,
    外部呼叫編排器,
    外部呼叫編排錯誤,
)
from 繁中代理.發布介面.領域模型 import (
    EndpointRef,
    InvocationRef,
    InvokeEnvelope,
    PublishedUsage,
    PublishedWarning,
)


class 階段(Enum):
    INVALID_API_KEY = "invalid_api_key"
    PRE_CREDENTIAL_REJECTION = "pre_credential_rejection"
    AUTHENTICATED = "authenticated"


class 狀態(Enum):
    有效 = "authenticated"
    無效 = "invalid_api_key"
    已過期 = "api_key_expired"
    已撤銷 = "api_key_revoked"


@dataclass(frozen=True, slots=True)
class 釘選:
    endpoint_id: str
    version_id: str
    version_number: int
    service_account_id: str = "sa-default"


class 生產結構釘選:
    """可由 INV 重建且直接提供 production schema snapshot 的 exact pin。"""

    __slots__ = ("endpoint_id", "service_account_id", "version_id", "version_number")
    已驗證: list[object] = []

    def __init__(self, endpoint_id, service_account_id, version_id, version_number):
        self.endpoint_id = endpoint_id
        self.service_account_id = service_account_id
        self.version_id = version_id
        self.version_number = version_number

    def 取得版本快照(self):
        """記錄 schema 讀取者並回傳 fresh input schema。"""
        type(self).已驗證.append(self)
        return SimpleNamespace(
            input_schema={
                "type": "object", "properties": {"q": {"type": "string"}},
                "required": ["q"], "additionalProperties": False,
            },
            response_schema={"type": "object"},
        )


@dataclass(frozen=True, slots=True)
class I06釘選:
    endpoint_id: str
    service_account_id: str
    version_id: str
    version_number: int
    model_snapshot: str
    runtime_snapshot: str
    tool_snapshot: str
    schema_snapshot: str


@dataclass(frozen=True, slots=True)
class PUB已釘選版本:
    endpoint_id: str
    service_account_id: str
    version_id: str
    version_number: int
    schema_changed: bool
    created_at: float
    _版本JSON: str


class 控制釘選:
    __slots__ = ("endpoint_id", "service_account_id", "version_id", "version_number")

    def __init__(self, endpoint_id, service_account_id, version_id, version_number):
        del endpoint_id, service_account_id, version_id, version_number
        raise KeyboardInterrupt("PIN_CONTROL_MARKER")


@dataclass(frozen=True, slots=True)
class 驗證結果:
    status: 狀態
    credential_id: str | None = None
    endpoint_id: str | None = None
    endpoint_status: str | None = None
    credential_rate_limit: int | None = None
    endpoint_rate_limit: int | None = None


@dataclass(frozen=True, slots=True)
class 限流決策:
    允許: bool
    端點計數: int
    憑證計數: int
    超限範圍: str | None = None
    重試秒數: int | None = None


class 假解析器:
    def __init__(self, 結果=None, 錯誤=None):
        self.結果, self.錯誤, self.呼叫 = 結果, 錯誤, []

    def 依slug解析(self, slug):
        self.呼叫.append(slug)
        if self.錯誤:
            raise self.錯誤
        return self.結果


class 假憑證服務:
    def __init__(self, 結果):
        self.結果, self.驗證呼叫, self.刷新呼叫 = 結果, [], []

    def 驗證(self, endpoint_id, api_key):
        self.驗證呼叫.append((endpoint_id, api_key))
        return self.結果

    def 刷新已認證使用(self, authentication, at):
        self.刷新呼叫.append((authentication, at))
        return "refreshed"


class 假政策:
    def __init__(self):
        self.準備呼叫, self.寫入呼叫, self.限流呼叫, self.輸入呼叫 = [], [], [], []

    def 準備(self, stage, input, metadata):
        self.準備呼叫.append((stage, input, metadata))
        return object()

    def 寫入(self, repository, command, endpoint_id, version_id, request_id, **kwargs):
        self.寫入呼叫.append((repository, command, endpoint_id, version_id, request_id, kwargs))
        return "inv-1"

    def 計數(self, endpoint_id, credential_id, endpoint_limit, credential_limit, at):
        self.限流呼叫.append((endpoint_id, credential_id, endpoint_limit, credential_limit, at))
        return 限流決策(True, 1, 1)

    def 驗證輸入(self, pinned_version, input):
        self.輸入呼叫.append((pinned_version, input))
        return True


class 假呼叫儲存庫:
    def __init__(self):
        self.結案 = []

    def 完成呼叫(self, *args, **kwargs):
        self.結案.append((args, kwargs))


def _編排(解析器, 憑證服務, 政策, *, 執行嘗試=None, 驗證輸出=None, 記錄執行嘗試=None,
        釘選類型: type = 釘選, 驗證輸入=None, 工作階段儲存庫=None, 呼叫儲存庫=None):
    if 記錄執行嘗試 is None:
        記錄執行嘗試 = lambda invocation, request, result, schema_valid: 執行嘗試紀錄收據(
            invocation.id, request.attempt, True, request.attempt,
        )
    if 驗證輸入 is None:
        驗證輸入 = 政策.驗證輸入
    return 外部呼叫編排器(
        解析器, 假呼叫儲存庫() if 呼叫儲存庫 is None else 呼叫儲存庫, 憑證服務,
        解析未找到型別=LookupError,
        釘選型別=釘選類型, 驗證型別=驗證結果, 驗證狀態型別=狀態,
        階段型別=階段, 準備擷取=政策.準備, 寫入擷取=政策.寫入,
        限流決策型別=限流決策, 提交雙層計數=政策.計數, 驗證輸入=驗證輸入,
        執行嘗試=執行嘗試, 驗證輸出=驗證輸出, 記錄執行嘗試=記錄執行嘗試,
        工作階段儲存庫=工作階段儲存庫,
    )


class 假工作階段儲存庫:
    """提供可觀測的session repository test double。

    描述：保存固定成功歷史，並記錄所有讀取與附加呼叫。
    參數：無；建構器不接受外部依賴。
    返回值：可供編排器測試注入的repository替身。
    """

    def __init__(self):
        """建立可觀測的session repository test double。

        參數：無。
        返回值：無；初始化讀寫紀錄與一組成功歷史。
        """
        self.讀取 = []
        self.附加 = []
        self.歷史 = (SimpleNamespace(
            sequence_number=1, endpoint_version_id="ver",
            user_message={"role": "user", "content": "先前"},
            assistant_message={"role": "assistant", "content": "收到"},
        ),)

    def 讀取成功歷史(self, *scope):
        """記錄並回傳固定成功歷史。

        參數：``scope``為endpoint、service account與session identity。
        返回值：預設不可變成功歷史。
        """
        self.讀取.append(scope)
        return self.歷史

    def 附加成功對話組(self, *args, **kwargs):
        """記錄成功pair append而不進行持久化。

        參數：原樣保存append位置與命名參數。
        返回值：呼叫方提供的expected sequence。
        """
        self.附加.append((args, kwargs))
        return kwargs["expected_sequence"]


def test_session_string依服務帳戶scope讀取並只在成功後附加完整pair():
    """驗證明確session依三元scope讀取並交由ledger成功路徑結案。

    參數：無；使用隔離test doubles。
    返回值：無；scope、history transport與completion assertions必須通過。
    """
    解析器 = 假解析器(釘選("ep", "ver", 1, "sa-default"))
    憑證 = 假憑證服務(驗證結果(狀態.有效, "cred", "ep", "active", 30, 60))
    政策 = 假政策()
    工作階段 = 假工作階段儲存庫()
    看到歷史 = []
    紀錄請求 = []

    def 執行(request):
        """記錄runtime收到的bounded history並回成功結果。

        參數：``request``為execution attempt DTO。
        返回值：固定成功attempt result。
        """
        看到歷史.append(request.history)
        return 執行嘗試結果("success", {"answer": "ok"}, PublishedUsage(7), ())

    def 紀錄(invocation, request, result, schema_valid):
        """記錄terminal callback所見session與history。

        參數：invocation、request、result與schema validity。
        返回值：固定已提交execution receipt。
        """
        紀錄請求.append((invocation.session_id, request.history, result.data, schema_valid))
        return 執行嘗試紀錄收據(invocation.id, request.attempt, True, request.attempt)

    編排器 = _編排(
        解析器, 憑證, 政策, 執行嘗試=執行, 驗證輸出=lambda *_: True,
        工作階段儲存庫=工作階段, 記錄執行嘗試=紀錄,
    )
    結果 = 編排器.執行("demo", "req", "key", {"q": 1}, None, 1, "case-1")

    assert 結果.轉為JSON()["envelope"]["invocation"]["session_id"] == "case-1"
    assert 工作階段.讀取 == [("ep", "sa-default", "case-1")]
    assert 看到歷史 == [工作階段.歷史]
    assert 紀錄請求 == [("case-1", 工作階段.歷史, {"answer": "ok"}, True)]
    assert 工作階段.附加 == []


def test_session省略時history_repository零讀零寫():
    """驗證省略session維持stateless且history repository零接觸。

    參數：無；使用隔離test doubles。
    返回值：無；HTTP成功且read/write紀錄皆空。
    """
    工作階段 = 假工作階段儲存庫()
    編排器 = _編排(
        假解析器(釘選("ep", "ver", 1)),
        假憑證服務(驗證結果(狀態.有效, "cred", "ep", "active", 30, 60)),
        假政策(), 執行嘗試=lambda _: 執行嘗試結果("success", {"ok": True}),
        驗證輸出=lambda *_: True, 工作階段儲存庫=工作階段,
    )
    assert 編排器.執行("demo", "req", "key", {}, None, 1).status_code == 200
    assert 工作階段.讀取 == [] and 工作階段.附加 == []


def _邊界錯誤編排(boundary, error):
    """把同一敵對例外精確注入 resolver/CRED/LOG/D19 邊界。"""
    解析器 = 假解析器(釘選("ep", "ver", 1))
    憑證服務 = 假憑證服務(驗證結果(狀態.有效, "cred", "ep", "active", 30, 60))
    政策 = 假政策()

    def 拋出(*args, **kwargs):
        raise error

    if boundary == "resolve":
        解析器.錯誤 = error
    elif boundary == "verify":
        憑證服務.驗證 = 拋出
    elif boundary == "prepare":
        政策.準備 = 拋出
    elif boundary == "write":
        政策.寫入 = 拋出
    elif boundary == "rate":
        政策.計數 = 拋出
    elif boundary == "input":
        政策.驗證輸入 = 拋出
    else:
        憑證服務.刷新已認證使用 = 拋出
    return _編排(解析器, 憑證服務, 政策)


def _精確內建樹含有標記(值, 標記, 已訪問):
    """只循 exact-known slots 與 trusted builtins，不觸發 hostile callbacks。"""
    if id(值) in 已訪問:
        return False
    已訪問.add(id(值))
    if type(值) is str:
        return 標記 in 值
    if type(值) is dict:
        return any(
            _精確內建樹含有標記(鍵, 標記, 已訪問)
            or _精確內建樹含有標記(項目, 標記, 已訪問)
            for 鍵, 項目 in dict.items(值)
        )
    if type(值) is MappingProxyType:
        參照物 = gc.get_referents(值)
        return (len(參照物) == 1 and type(參照物[0]) is dict
                and _精確內建樹含有標記(參照物[0], 標記, 已訪問))
    if type(值) in (KeyboardInterrupt, SystemExit, GeneratorExit):
        return _精確內建樹含有標記(值.args, 標記, 已訪問)
    固定欄位 = {
        限流決策: ("允許", "端點計數", "憑證計數", "超限範圍", "重試秒數"),
        I06釘選: ("endpoint_id", "service_account_id", "version_id", "version_number", "model_snapshot",
                  "runtime_snapshot", "tool_snapshot", "schema_snapshot"),
        控制釘選: ("endpoint_id", "service_account_id", "version_id", "version_number"),
        編排器模組._正規呼叫快照: ("輸入文字", "中繼資料文字"),
        編排器模組._終局結果快照: ("種類", "結構有效", "資料文字", "有用量", "權杖數", "警告純量"),
        編排器模組.外部呼叫入口: (
            "endpoint", "invocation", "pinned_version", "authentication", "error", "_續行快照",
        ),
        EndpointRef: ("id", "slug", "version"),
        InvocationRef: ("id", "request_id", "session_id"),
        PublishedUsage: ("total_tokens",),
        PublishedWarning: ("code", "message"),
        InvokeEnvelope: ("ok", "endpoint", "invocation", "data", "usage", "warnings", "error"),
        執行嘗試請求: ("pinned_version", "input", "metadata", "attempt"),
        執行嘗試結果: ("kind", "data", "usage", "warnings"),
        執行嘗試紀錄收據: ("invocation_id", "attempt", "committed", "sequence"),
    }.get(type(值))
    if 固定欄位 is not None:
        return any(
            _精確內建樹含有標記(object.__getattribute__(值, 欄位), 標記, 已訪問)
            for 欄位 in 固定欄位
        )
    if type(值) in (tuple, list, set, frozenset):
        return any(_精確內建樹含有標記(項目, 標記, 已訪問) for 項目 in 值)
    return False


def _生產追蹤不含標記(例外, 標記):
    """每個 production frame local 都以 fresh visited set 獨立掃描。"""
    for frame, _ in traceback.walk_tb(例外.__traceback__):
        if frame.f_globals.get("__name__") != "繁中代理.發布介面.呼叫.編排器":
            continue
        for 值 in tuple(frame.f_locals.values()):
            assert not _精確內建樹含有標記(值, 標記, set())


def test_追蹤隱私oracle可看見所有權威快照與公開信封的巢狀標記():
    標記 = "ORACLE_LEAK_MARKER"
    快照 = 編排器模組._正規呼叫快照('{"nested":["ORACLE_LEAK_MARKER"]}', '{"m":"ORACLE_LEAK_MARKER"}')
    入口 = 編排器模組.外部呼叫入口(
        EndpointRef("ep", "slug", 1), InvocationRef("inv", "req"), object(), None, None, 快照,
    )
    信封 = InvokeEnvelope(
        ok=True, endpoint=EndpointRef("ep", "slug", 1), invocation=InvocationRef("inv", "req"),
        data={"nested": [標記]}, usage=None, warnings=(), error=None,
    )
    終局 = 編排器模組._終局結果快照("success", True, '{"nested":["ORACLE_LEAK_MARKER"]}', False, None, ())

    assert _精確內建樹含有標記(快照, 標記, set())
    assert _精確內建樹含有標記(入口, 標記, set())
    assert _精確內建樹含有標記(信封, 標記, set())
    assert _精確內建樹含有標記(終局, 標記, set())


def test_slug_miss映射not_found且所有後續副作用為零():
    解析器 = 假解析器(錯誤=LookupError("missing"))
    憑證服務, 政策 = 假憑證服務(驗證結果(狀態.無效)), 假政策()

    結果 = _編排(解析器, 憑證服務, 政策).開始(
        "missing", "req-1", "sk-raw-secret", {"q": 1}, {"secret": "raw"}, 10.0,
    )

    assert 結果.error.status_code == 404
    assert 結果.error.to_json()["envelope"]["error"]["code"] == "endpoint_not_found"
    assert 憑證服務.驗證呼叫 == 憑證服務.刷新呼叫 == []
    assert 政策.準備呼叫 == 政策.寫入呼叫 == []


@pytest.mark.parametrize(
    ("credential_status", "stage", "code"),
    [
        (狀態.無效, 階段.INVALID_API_KEY, "invalid_api_key"),
        (狀態.已過期, 階段.PRE_CREDENTIAL_REJECTION, "api_key_expired"),
        # I01 沒有 api_key_revoked 公開碼，D10 分類依既有契約收斂為 invalid_api_key。
        (狀態.已撤銷, 階段.PRE_CREDENTIAL_REJECTION, "invalid_api_key"),
    ],
)
def test_拒絕憑證建立呼叫但不刷新且沿用穩定錯誤映射(credential_status, stage, code):
    憑證服務, 政策 = 假憑證服務(驗證結果(credential_status, "不可寫入")), 假政策()
    結果 = _編排(假解析器(釘選("ep-1", "ver-3", 3)), 憑證服務, 政策).開始(
        "demo", "req-1", "raw-key", {"q": 1}, {"authorization": "raw-key"}, 10.0,
    )

    assert 結果.error.to_json()["envelope"]["error"]["code"] == code
    assert 結果.invocation.id == "inv-1" and 結果.endpoint.version == 3
    assert 政策.準備呼叫[0][0] is stage
    assert 政策.寫入呼叫[0][-1]["credential_id"] is None
    assert 憑證服務.刷新呼叫 == []


def test_執行拒絕憑證時精確結案且不留下pending():
    憑證服務, 政策, 儲存庫 = 假憑證服務(驗證結果(狀態.已撤銷, "不可寫入")), 假政策(), 假呼叫儲存庫()
    結果 = _編排(
        假解析器(釘選("ep-1", "ver-3", 3)), 憑證服務, 政策,
        呼叫儲存庫=儲存庫,
    ).執行("demo", "req-1", "raw-key", {"q": 1}, {"trace": 2}, 10.0)

    assert 結果.status_code == 401
    assert 儲存庫.結案 == [(("inv-1", "invalid_api_key"), {
        "error": {"code": "invalid_api_key", "message": "API key 無效。", "details": {}},
        "latency_ms": 0.0,
    })]


def test_exact_authenticated寫入後才刷新並進入主流程():
    authentication = 驗證結果(狀態.有效, "cred-1", "ep-1", "active", 30, 60)
    憑證服務, 政策 = 假憑證服務(authentication), 假政策()
    原始刷新, 原始計數, 原始輸入 = 憑證服務.刷新已認證使用, 政策.計數, 政策.驗證輸入
    順序 = []

    def 寫入後刷新(authentication, at):
        assert len(政策.寫入呼叫) == 1
        順序.append("refresh")
        return 原始刷新(authentication, at)

    def 刷新後計數(*args):
        assert 順序 == ["refresh"]
        順序.append("rate")
        return 原始計數(*args)

    def 計數後輸入(pinned_version, input):
        assert 順序 == ["refresh", "rate"]
        順序.append("input")
        return 原始輸入(pinned_version, input)

    憑證服務.刷新已認證使用, 政策.計數, 政策.驗證輸入 = 寫入後刷新, 刷新後計數, 計數後輸入
    結果 = _編排(假解析器(釘選("ep-1", "ver-3", 3)), 憑證服務, 政策).開始(
        "demo", "req-1", "raw-key", {"q": 1}, {"trace": 2}, 10.0,
    )

    assert 結果.error is None and 結果.authentication is authentication
    assert 政策.準備呼叫[0][0] is 階段.AUTHENTICATED
    assert 政策.寫入呼叫[0][-1]["credential_id"] == "cred-1"
    assert 憑證服務.刷新呼叫 == [(authentication, 10.0)]
    assert 政策.限流呼叫 == [("ep-1", "cred-1", 60, 30, 10.0)]
    assert 政策.輸入呼叫 == [(結果.pinned_version, {"q": 1})]
    assert 順序 == ["refresh", "rate", "input"]


@pytest.mark.parametrize(
    ("endpoint_status", "status_code", "code"),
    [("disabled", 403, "endpoint_disabled"), ("archived", 410, "endpoint_archived")],
)
def test_雙增後端點狀態優先於同時超限(endpoint_status, status_code, code):
    authentication = 驗證結果(狀態.有效, "cred-1", "ep-1", endpoint_status, 30, 60)
    憑證服務, 政策 = 假憑證服務(authentication), 假政策()
    政策.計數 = lambda *args: (
        政策.限流呼叫.append(args) or 限流決策(False, 61, 31, "endpoint", 50)
    )

    結果 = _編排(假解析器(釘選("ep-1", "ver-3", 3)), 憑證服務, 政策).開始(
        "demo", "req-1", "raw-key", {"q": 1}, None, 10,
    )

    assert (結果.error.status_code, 結果.error.to_json()["envelope"]["error"]["code"]) == (status_code, code)
    assert 憑證服務.刷新呼叫 == [(authentication, 10.0)]
    assert 政策.限流呼叫 == [("ep-1", "cred-1", 60, 30, 10.0)]
    assert 政策.輸入呼叫 == []


def test_active超限以同一決策映射429且不進入輸入():
    authentication = 驗證結果(狀態.有效, "cred-1", "ep-1", "active", 2, 5)
    憑證服務, 政策 = 假憑證服務(authentication), 假政策()
    政策.計數 = lambda *args: 限流決策(False, 3, 3, "credential", 17)

    結果 = _編排(假解析器(釘選("ep-1", "ver", 1)), 憑證服務, 政策).開始(
        "demo", "req", "raw", "hello", None, 43,
    )

    payload = 結果.error.to_json()
    assert payload["status_code"] == 429 and payload["headers"] == {"Retry-After": "17"}
    assert payload["envelope"]["error"]["details"] == {"scope": "credential", "retry_after_seconds": 17}
    assert 政策.輸入呼叫 == []


def test_input拒絕仍先提交雙層計數再映射422():
    authentication = 驗證結果(狀態.有效, "cred-1", "ep-1", "active", 30, 60)
    憑證服務, 政策 = 假憑證服務(authentication), 假政策()
    順序 = []

    def 計數(*args):
        順序.append(("rate", args))
        return 限流決策(True, 2, 2)

    def 驗證輸入(pinned_version, input):
        順序.append(("input", pinned_version, input))
        return False

    政策.計數, 政策.驗證輸入 = 計數, 驗證輸入
    結果 = _編排(假解析器(釘選("ep-1", "ver", 1)), 憑證服務, 政策).開始(
        "demo", "req", "raw", ["unsupported"], None, 1,
    )

    assert [項[0] for 項 in 順序] == ["rate", "input"]
    assert 結果.error.status_code == 422
    assert 結果.error.to_json()["envelope"]["error"]["code"] == "input_schema_invalid"


def test_orchestrator直接呼叫production輸入schema且使用重建後private_pin():
    """callback contract 是 (pin,input)，不靠 closure adapter，schema 讀取重建後 pin。"""
    assert tuple(inspect.signature(驗證釘選輸入結構).parameters) == ("釘選版本", "輸入資料")
    原始釘選 = 生產結構釘選("ep", "svc", "ver", 1)
    生產結構釘選.已驗證.clear()
    驗證 = 驗證結果(狀態.有效, "cred", "ep", "active", 30, 60)
    結果 = _編排(
        假解析器(原始釘選), 假憑證服務(驗證), 假政策(),
        釘選類型=生產結構釘選, 驗證輸入=驗證釘選輸入結構,
    ).開始("demo", "req", "raw", {"q": 7}, None, 1)
    assert 結果.error.status_code == 422
    assert 生產結構釘選.已驗證 == [結果.pinned_version]
    assert 結果.pinned_version is not 原始釘選


def test_capture_mutation不改變validation與runtime使用的canonical來源():
    authentication = 驗證結果(狀態.有效, "cred", "ep", "active", 30, 60)
    政策, requests = 假政策(), []

    def 準備(stage, input, metadata):
        政策.準備呼叫.append((stage, input, metadata))
        input["nested"].append("mutated")
        metadata["trace"] = "mutated"
        return object()

    def adapter(request):
        requests.append(request)
        return 執行嘗試結果("success", {"answer": 1})

    政策.準備 = 準備
    result = _編排(
        假解析器(釘選("ep", "ver", 1)), 假憑證服務(authentication), 政策,
        執行嘗試=adapter, 驗證輸出=lambda pinned, data: True,
    ).執行("demo", "req", "raw", {"nested": ["original"]}, {"trace": "original"}, 1)

    assert result.status_code == 200
    assert 政策.輸入呼叫 == [(政策.輸入呼叫[0][0], {"nested": ["original"]})]
    assert requests[0].input == {"nested": ["original"]}
    assert requests[0].metadata == {"trace": "original"}


def test_快照失敗仍以固定null建立呼叫且authenticated計數後才映射422():
    authentication = 驗證結果(狀態.有效, "cred", "ep", "active", 30, 60)
    憑證服務, 政策 = 假憑證服務(authentication), 假政策()
    cyclic = []
    cyclic.append(cyclic)

    結果 = _編排(假解析器(釘選("ep", "ver", 1)), 憑證服務, 政策).開始(
        "demo", "req", "raw", cyclic, {"trace": 1}, 1,
    )

    assert 政策.準備呼叫 == [(階段.AUTHENTICATED, None, None)]
    assert len(政策.寫入呼叫) == len(政策.限流呼叫) == 1
    assert 結果.error.status_code == 422 and 政策.輸入呼叫 == []


def test_無效憑證的快照失敗只擷取固定null且仍建立401呼叫():
    政策 = 假政策()
    hostile = object()
    結果 = _編排(
        假解析器(釘選("ep", "ver", 1)), 假憑證服務(驗證結果(狀態.無效)), 政策,
    ).開始("demo", "req", "bad", hostile, {"raw": hostile}, 1)

    assert 政策.準備呼叫 == [(階段.INVALID_API_KEY, None, None)]
    assert len(政策.寫入呼叫) == 1 and 結果.error.status_code == 401


def test_無效憑證不呼叫主限流或輸入驗證():
    憑證服務, 政策 = 假憑證服務(驗證結果(狀態.無效)), 假政策()
    結果 = _編排(假解析器(釘選("ep", "ver", 1)), 憑證服務, 政策).開始(
        "demo", "req", "bad", {}, None, 1,
    )
    assert 結果.error.status_code == 401
    assert 政策.限流呼叫 == 政策.輸入呼叫 == []


def test_偽造限流決策固定失敗關閉且不進入輸入():
    authentication = 驗證結果(狀態.有效, "cred", "ep", "active", 30, 60)
    政策 = 假政策()
    forged = 限流決策(True, 1, 1)
    object.__setattr__(forged, "允許", "yes")
    政策.計數 = lambda *args: forged
    with pytest.raises(外部呼叫編排錯誤, match="^外部呼叫編排失敗$") as caught:
        _編排(假解析器(釘選("ep", "ver", 1)), 假憑證服務(authentication), 政策).開始(
            "demo", "req", "raw", {}, None, 1,
        )
    assert caught.value.__cause__ is caught.value.__context__ is None
    assert 政策.輸入呼叫 == []


@pytest.mark.parametrize(
    ("decision", "endpoint_status"),
    [
        (限流決策(True, 61, 1), "active"),
        (限流決策(True, 1, 31), "disabled"),
        (限流決策(False, 60, 31, "endpoint", 1), "active"),
        (限流決策(False, 60, 30, "endpoint", 1), "active"),
        (限流決策(False, 61, 31, "credential", 1), "active"),
        (限流決策(False, 61, 30, "credential", 1), "active"),
        (限流決策(False, 60, 30, "credential", 1), "active"),
        (限流決策(True, 1, 1, "endpoint", 1), "active"),
        (限流決策(False, 61, 1, "endpoint", None), "active"),
    ],
)
def test_exact限流決策語意矛盾在status與input前固定失敗(decision, endpoint_status):
    authentication = 驗證結果(狀態.有效, "cred", "ep", endpoint_status, 30, 60)
    政策 = 假政策()
    政策.計數 = lambda *args: decision

    with pytest.raises(外部呼叫編排錯誤, match="^外部呼叫編排失敗$") as caught:
        _編排(假解析器(釘選("ep", "ver", 1)), 假憑證服務(authentication), 政策).開始(
            "demo", "req", "raw", {}, None, 1,
        )

    assert type(decision) is 限流決策
    assert caught.value.__cause__ is caught.value.__context__ is None
    assert 政策.輸入呼叫 == []


def test_雙層同時超限只接受endpoint優先且映射決策原始計數():
    authentication = 驗證結果(狀態.有效, "cred", "ep", "active", 30, 60)
    政策 = 假政策()
    政策.計數 = lambda *args: 限流決策(False, 61, 31, "endpoint", 9)

    結果 = _編排(假解析器(釘選("ep", "ver", 1)), 假憑證服務(authentication), 政策).開始(
        "demo", "req", "raw", {}, None, 1,
    )

    payload = 結果.error.to_json()
    assert payload["status_code"] == 429
    assert payload["envelope"]["error"]["details"] == {
        "scope": "endpoint", "retry_after_seconds": 9,
    }
    assert 政策.輸入呼叫 == []


def test_偽造限流決策巢狀標記不留在固定錯誤traceback():
    authentication = 驗證結果(狀態.有效, "cred", "ep", "active", 30, 60)
    政策 = 假政策()
    標記 = "DECISION_MARKER"
    forged = 限流決策(False, 61, 31, "endpoint", 17)
    object.__setattr__(forged, "重試秒數", {"nested": [標記]})
    assert _精確內建樹含有標記(forged, 標記, set())

    def 偽造計數(endpoint_id, credential_id, endpoint_limit, credential_limit, at):
        del endpoint_id, credential_id, endpoint_limit, credential_limit, at
        return forged

    政策.計數 = 偽造計數

    with pytest.raises(外部呼叫編排錯誤) as caught:
        _編排(假解析器(釘選("ep", "ver", 1)), 假憑證服務(authentication), 政策).開始(
            "demo", "req", "raw", {}, None, 1,
        )
    _生產追蹤不含標記(caught.value, "DECISION_MARKER")


def test_resolver_operational_failure不偽裝slug_miss且後續零副作用():
    憑證服務, 政策 = 假憑證服務(驗證結果(狀態.無效)), 假政策()
    with pytest.raises(外部呼叫編排錯誤, match="^外部呼叫編排失敗$") as caught:
        _編排(假解析器(錯誤=RuntimeError("raw-key")), 憑證服務, 政策).開始(
            "demo", "req", "raw-key", {"raw-key": 1}, {"raw-key": 2}, 1.0,
        )
    assert caught.value.__cause__ is caught.value.__context__ is None
    assert 憑證服務.驗證呼叫 == 憑證服務.刷新呼叫 == []
    assert 政策.準備呼叫 == 政策.寫入呼叫 == []
    _生產追蹤不含標記(caught.value, "raw-key")


def test_forged_authentication與callback自訂BaseException固定失敗關閉():
    class 自訂Base(BaseException):
        pass

    class 偽造:
        status = 狀態.有效
        credential_id = "cred-1"

    政策 = 假政策()
    with pytest.raises(外部呼叫編排錯誤, match="^外部呼叫編排失敗$"):
        _編排(假解析器(釘選("ep", "ver", 1)), 假憑證服務(偽造()), 政策).開始(
            "demo", "req", "raw", {}, None, 1.0,
        )
    forged = 驗證結果(狀態.有效, "cred-1")
    object.__setattr__(forged, "status", Enum("偽狀態", {"有效": "authenticated"}).有效)
    with pytest.raises(外部呼叫編排錯誤):
        _編排(假解析器(釘選("ep", "ver", 1)), 假憑證服務(forged), 政策).開始(
            "demo", "req", "raw", {}, None, 1.0,
        )
    with pytest.raises(外部呼叫編排錯誤, match="^外部呼叫編排失敗$") as caught:
        _編排(假解析器(錯誤=自訂Base("秘密")), 假憑證服務(None), 政策).開始(
            "demo", "req", "raw", {}, None, 1.0,
        )
    assert caught.value.__cause__ is caught.value.__context__ is None


@pytest.mark.parametrize("boundary", ["resolve", "verify", "prepare", "write", "refresh", "rate", "input"])
def test_每個依賴邊界的自訂BaseException固定無鏈結且不洩漏(boundary):
    class 自訂Base(BaseException):
        pass

    with pytest.raises(外部呼叫編排錯誤, match="^外部呼叫編排失敗$") as caught:
        _邊界錯誤編排(boundary, 自訂Base("SECRET_MARKER")).開始(
            "demo", "req", "SECRET_MARKER", {"SECRET_MARKER": 1}, None, 1.0,
        )
    assert caught.value.__cause__ is caught.value.__context__ is None
    _生產追蹤不含標記(caught.value, "SECRET_MARKER")


@pytest.mark.parametrize("control_type", [KeyboardInterrupt, SystemExit, GeneratorExit])
@pytest.mark.parametrize("boundary", ["resolve", "verify", "prepare", "write", "refresh", "rate", "input"])
def test_control_flow跨每個依賴邊界保持identity與args且清理隱私(control_type, boundary):
    control = control_type("EXCEPTION_MARKER", 7)
    with pytest.raises(control_type) as caught:
        _邊界錯誤編排(boundary, control).開始(
            "demo", "req", "KEY_MARKER",
            {"nested": ["INPUT_MARKER"]}, {"nested": {"value": "METADATA_MARKER"}}, 1.0,
        )
    assert caught.value is control and caught.value.args == ("EXCEPTION_MARKER", 7)
    for marker in ("KEY_MARKER", "INPUT_MARKER", "METADATA_MARKER", "EXCEPTION_MARKER"):
        _生產追蹤不含標記(caught.value, marker)


def _執行案例(outcomes, validator, input_data=None, metadata=None, recorder=None):
    pin = 釘選("ep", "ver", 1)
    authentication = 驗證結果(狀態.有效, "cred", "ep", "active", 30, 60)
    credential, policy, requests, validations = 假憑證服務(authentication), 假政策(), [], []
    input_data = {"q": 1} if input_data is None else input_data
    metadata = {"trace": 2} if metadata is None else metadata

    def adapter(request):
        requests.append(request)
        outcome = outcomes[len(requests) - 1]
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome

    def validate(pinned, data):
        validations.append((pinned, data))
        if isinstance(validator, BaseException):
            raise validator
        return validator[len(validations) - 1]

    orchestrator = _編排(
        假解析器(pin), credential, policy, 執行嘗試=adapter,
        驗證輸出=validate, 記錄執行嘗試=recorder,
    )
    result = orchestrator.執行("demo", "req", "raw", input_data, metadata, 1)
    return result, pin, credential, policy, requests, validations


def test_第一次成功回傳200資料用量且所有前置副作用恰好一次():
    outcome = 執行嘗試結果("success", {"answer": 1}, PublishedUsage(9))
    result, pin, credential, policy, requests, validations = _執行案例([outcome], [True])

    payload = result.to_json()
    assert payload["status_code"] == 200 and payload["envelope"]["data"] == {"answer": 1}
    assert payload["envelope"]["usage"] == {"total_tokens": 9}
    assert [request.attempt for request in requests] == [1] and requests[0].pinned_version == pin
    assert requests[0].pinned_version is not pin and validations == [(requests[0].pinned_version, {"answer": 1})]
    assert len(credential.刷新呼叫) == len(policy.限流呼叫) == 1


def test_第一次schema失敗只重試一次且沿用同一釘選後可成功():
    outcomes = [執行嘗試結果("success", {"bad": 1}), 執行嘗試結果("success", {"answer": 2})]
    result, pin, credential, policy, requests, _ = _執行案例(outcomes, [False, True])

    assert result.status_code == 200
    assert [request.attempt for request in requests] == [1, 2]
    assert all(request.pinned_version is requests[0].pinned_version for request in requests)
    assert requests[0].pinned_version == pin and requests[0].pinned_version is not pin
    assert len(credential.刷新呼叫) == len(policy.限流呼叫) == 1


def test_兩次schema失敗固定502且沒有部分資料或第三次嘗試():
    outcomes = [執行嘗試結果("success", {"SECRET": 1}), 執行嘗試結果("success", {"SECRET": 2})]
    result, _, _, _, requests, _ = _執行案例(outcomes, [False, False])
    payload = result.to_json()
    assert payload["status_code"] == 502
    assert payload["envelope"]["error"]["code"] == "model_output_schema_invalid"
    assert payload["envelope"]["data"] is None and "SECRET" not in repr(payload)
    assert len(requests) == 2


@pytest.mark.parametrize(
    ("kind", "status"),
    [("model_timeout", 504), ("tool_execution_failed", 502), ("tool_timeout", 504),
     ("endpoint_misconfigured", 500), ("internal_error", 500)],
)
def test_typed執行結果使用canonical映射且不重試(kind, status):
    result, _, _, _, requests, validations = _執行案例([執行嘗試結果(kind)], [])
    payload = result.to_json()
    assert payload["status_code"] == status and payload["envelope"]["error"]["code"] == kind
    assert len(requests) == 1 and validations == []


def test_recoverable工具警告成功回傳200且不含任意內部token欄位():
    warning = PublishedWarning("tool_recovered", "工具失敗後已恢復。")
    outcome = 執行嘗試結果("success", {"answer": 1}, None, (warning,))
    result, *_ = _執行案例([outcome], [True])
    payload = result.to_json()
    assert payload["status_code"] == 200
    assert payload["envelope"]["warnings"] == [{"code": "tool_recovered", "message": "工具失敗後已恢復。"}]
    assert "internal_outcome_token" not in repr(payload)


@pytest.mark.parametrize(
    ("outcomes", "validations", "status", "public_value", "schemas"),
    [
        ([執行嘗試結果("success", {"answer": "original"}, PublishedUsage(7),
                                  (PublishedWarning("ok", "original"),))],
         [True], 200, "original", [True]),
        ([執行嘗試結果("success", {"answer": "bad"}),
          執行嘗試結果("success", {"answer": "retry-original"})],
         [False, True], 200, "retry-original", [False, True]),
        ([執行嘗試結果("model_timeout")], [], 504, "model_timeout", [None]),
    ],
)
def test_recorder竄改fresh參數不改變權威終局(outcomes, validations, status, public_value, schemas):
    authentication = 驗證結果(狀態.有效, "cred", "ep", "active", 30, 60)
    政策, adapter_requests, recorder_args = 假政策(), [], []

    def adapter(request):
        adapter_requests.append(request)
        return outcomes[len(adapter_requests) - 1]

    def validator(pinned, data):
        del pinned, data
        return validations[len(recorder_args)]

    def recorder(invocation, request, result, schema_valid):
        index, original_id = len(recorder_args), invocation.id
        assert original_id == "inv-1"
        assert request is not adapter_requests[index] and result is not outcomes[index]
        assert request.input is not adapter_requests[index].input
        if request.metadata is not None:
            assert request.metadata is not adapter_requests[index].metadata
        if result.data is not None:
            assert result.data is not outcomes[index].data
        if result.usage is not None:
            assert result.usage is not outcomes[index].usage
        if result.warnings:
            assert result.warnings is not outcomes[index].warnings
            assert result.warnings[0] is not outcomes[index].warnings[0]
        recorder_args.append((invocation, request, result, schema_valid))
        object.__setattr__(invocation, "id", "FORGED_MARKER")
        object.__setattr__(request, "input", {"FORGED_MARKER": 1})
        object.__setattr__(request, "metadata", {"FORGED_MARKER": 2})
        object.__setattr__(result, "kind", "FORGED_MARKER")
        object.__setattr__(result, "data", {"FORGED_MARKER": 3})
        object.__setattr__(result, "usage", PublishedUsage(999))
        object.__setattr__(result, "warnings", (PublishedWarning("FORGED_MARKER", "FORGED_MARKER"),))
        return 執行嘗試紀錄收據(original_id, request.attempt, True, request.attempt)

    編排器 = _編排(
        假解析器(釘選("ep", "ver", 1)), 假憑證服務(authentication), 政策,
        執行嘗試=adapter, 驗證輸出=validator, 記錄執行嘗試=recorder,
    )
    payload = 編排器.執行("demo", "req", "raw", {"nested": ["input"]}, {"trace": [1]}, 1).to_json()

    assert payload["status_code"] == status and [項[3] for 項 in recorder_args] == schemas
    assert "FORGED_MARKER" not in repr(payload)
    if status == 200:
        assert payload["envelope"]["data"]["answer"] == public_value
    else:
        assert payload["envelope"]["error"]["code"] == public_value


def test_recorder以竄改後識別簽收仍依原始呼叫識別固定失敗():
    def recorder(invocation, request, result, schema_valid):
        del result, schema_valid
        object.__setattr__(invocation, "id", "FORGED_MARKER")
        return 執行嘗試紀錄收據(invocation.id, request.attempt, True, 1)

    authentication = 驗證結果(狀態.有效, "cred", "ep", "active", 30, 60)
    result = _編排(
        假解析器(釘選("ep", "ver", 1)), 假憑證服務(authentication), 假政策(),
        執行嘗試=lambda request: 執行嘗試結果("success", {"answer": request.attempt}),
        驗證輸出=lambda pinned, data: True, 記錄執行嘗試=recorder,
    ).執行("demo", "req", "raw", {}, None, 1).to_json()
    assert result["status_code"] == 500
    assert result["envelope"]["error"]["code"] == "internal_error"
    assert "FORGED_MARKER" not in repr(result)


def _警告結果(warnings):
    return 執行嘗試結果("success", {}, None, warnings)


def test_警告數量與每欄UTF8精確邊界及一超即固定拒絕():
    assert len(_警告結果(tuple(PublishedWarning("c", "m") for _ in range(64))).warnings) == 64
    with pytest.raises(ValueError, match="^執行嘗試結果不符合契約$"):
        _警告結果(tuple(PublishedWarning("c", "m") for _ in range(65)))
    for accepted, rejected in (("c" * 256, "c" * 257), ("界" * 85 + "a", "界" * 85 + "é")):
        assert _警告結果((PublishedWarning(accepted, "m"),)).warnings[0].code == accepted
        with pytest.raises(ValueError, match="^執行嘗試結果不符合契約$"):
            _警告結果((PublishedWarning(rejected, "m"),))
    assert len(_警告結果((PublishedWarning("c", "m" * 2048),)).warnings[0].message) == 2048
    with pytest.raises(ValueError, match="^執行嘗試結果不符合契約$"):
        _警告結果((PublishedWarning("c", "m" * 2049),))


def test_警告aggregate精確16KiB且一byte超限():
    exact = tuple(PublishedWarning("c" * 256, "m" * 1792) for _ in range(8))
    assert sum(len((w.code + w.message).encode()) for w in _警告結果(exact).warnings) == 16 * 1024
    over = exact[:-1] + (PublishedWarning("c" * 256, "m" * 1793),)
    with pytest.raises(ValueError, match="^執行嘗試結果不符合契約$"):
        _警告結果(over)


def test_警告只接受exact_tuple_DTO_str且敵對子類零callback():
    calls = []

    class 敵對Tuple(tuple):
        def __len__(self):
            calls.append("len")
            return 0

        def __iter__(self):
            calls.append("iter")
            return super().__iter__()

    class 敵對Str(str):
        def encode(self, *args, **kwargs):
            calls.append("encode")
            return super().encode(*args, **kwargs)

    class 敵對Warning(PublishedWarning):
        pass

    for warnings in (敵對Tuple(), (敵對Warning("c", "m"),),
                     (PublishedWarning(敵對Str("c"), "m"),)):
        with pytest.raises(ValueError, match="^執行嘗試結果不符合契約$"):
            _警告結果(warnings)
    assert calls == []


def test_過大警告在資料複製與recorder前固定終局且不洩漏():
    calls = []

    class 敵對資料(dict):
        def __len__(self):
            calls.append("copy")
            return super().__len__()

    forged = 執行嘗試結果("success", {})
    object.__setattr__(forged, "data", 敵對資料({"FORGED_MARKER": 1}))
    object.__setattr__(forged, "warnings", (PublishedWarning("c" * 257, "FORGED_MARKER"),))
    result, *_ = _執行案例([forged], [True], recorder=lambda *args: calls.append("recorder"))
    payload = result.to_json()
    assert payload["status_code"] == 500 and payload["envelope"]["error"]["code"] == "internal_error"
    assert calls == [] and "FORGED_MARKER" not in repr(payload)


def test_generic_adapter失敗沿用internal_error且不反射raw_outcome():
    class AdapterFailure(BaseException):
        pass

    result, _, _, _, requests, _ = _執行案例([AdapterFailure("PROVIDER_SECRET")], [])
    payload = result.to_json()
    assert payload["envelope"]["error"]["code"] == "internal_error"
    assert "PROVIDER_SECRET" not in repr(payload) and len(requests) == 1


@pytest.mark.parametrize("control_type", [KeyboardInterrupt, SystemExit, GeneratorExit])
def test_runtime_callback控制流程保留identity並清理輸入metadata_raw與provider標記(control_type):
    control = control_type("PROVIDER_MARKER")
    outcome = 執行嘗試結果("success", {"RAW_MARKER": 1})
    with pytest.raises(control_type) as caught:
        _執行案例(
            [outcome], control,
            {"nested": ["INPUT_MARKER"]}, {"nested": {"value": "METADATA_MARKER"}},
        )
    assert caught.value is control
    for marker in ("INPUT_MARKER", "METADATA_MARKER", "RAW_MARKER", "PROVIDER_MARKER"):
        _生產追蹤不含標記(caught.value, marker)


def test_成功結果協調偽造所有公開私有anchor仍固定無鏈結失敗():
    原始, *_ = _執行案例([執行嘗試結果("success", {"answer": "original"})], [True])
    攻擊者, *_ = _執行案例([
        執行嘗試結果("success", {"SECRET_MARKER": ["forged"]}),
    ], [True])
    for 欄位 in ("status_code", "envelope", "_正規信封", "_正規文字", "_固定狀態", "_公開資料"):
        if hasattr(攻擊者, 欄位):
            object.__setattr__(原始, 欄位, object.__getattribute__(攻擊者, 欄位))

    with pytest.raises(ValueError, match="^呼叫成功結果不符合契約$") as 捕捉:
        原始.to_json()
    assert 捕捉.value.__cause__ is 捕捉.value.__context__ is None
    _生產追蹤不含標記(捕捉.value, "SECRET_MARKER")
    assert 攻擊者.to_json()["envelope"]["data"] == {"SECRET_MARKER": ["forged"]}


def test_成功結果拒絕未登錄object_new與淺複製():
    正常, *_ = _執行案例([執行嘗試結果("success", {"answer": 1})], [True])
    偽造 = object.__new__(呼叫成功結果)
    object.__setattr__(偽造, "status_code", 200)
    object.__setattr__(偽造, "envelope", 正常.envelope)
    for 值 in (偽造, copy.copy(正常)):
        with pytest.raises(ValueError, match="^呼叫成功結果不符合契約$"):
            值.to_json()
    try:
        深複製 = copy.deepcopy(正常)
    except TypeError:
        深複製 = None
    if 深複製 is not None:
        with pytest.raises(ValueError, match="^呼叫成功結果不符合契約$"):
            深複製.to_json()


def test_object_new後直接呼叫init仍不得取得來源且正常實例只能初始化一次():
    正常, *_ = _執行案例([執行嘗試結果("success", {"answer": 1})], [True])
    偽造 = object.__new__(呼叫成功結果)
    with pytest.raises(ValueError, match="^呼叫成功結果不符合契約$"):
        呼叫成功結果.__init__(偽造, 正常.envelope)
    object.__setattr__(偽造, "status_code", 200)
    object.__setattr__(偽造, "envelope", 正常.envelope)
    with pytest.raises(ValueError, match="^呼叫成功結果不符合契約$"):
        偽造.to_json()
    with pytest.raises(ValueError, match="^呼叫成功結果不符合契約$"):
        呼叫成功結果.__init__(正常, 正常.envelope)
    assert 正常.to_json()["envelope"]["data"] == {"answer": 1}


def test_模組全域沒有可讀寫實際成功來源登錄的能力():
    正常, *_ = _執行案例([執行嘗試結果("success", {"answer": 1})], [True])
    偽造 = object.__new__(呼叫成功結果)
    object.__setattr__(偽造, "status_code", 200)
    object.__setattr__(偽造, "envelope", 正常.envelope)
    正規文字 = '{"attacker":"chosen"}'
    全域項目 = tuple(vars(編排器模組).items())
    assert not any(名稱 in {
        "_建立成功結果登錄", "_登錄成功結果", "_讀取成功結果",
        "_移除成功結果", "_成功結果登錄數量",
    } for 名稱, _ in 全域項目)
    for _, 能力 in 全域項目:
        if not inspect.isfunction(能力) or 能力.__module__ != 編排器模組.__name__:
            continue
        try:
            inspect.signature(能力).bind(偽造, 正規文字)
        except TypeError:
            pass
        else:
            try:
                能力(偽造, 正規文字)
            except BaseException:
                pass
        try:
            inspect.signature(能力).bind(偽造)
        except TypeError:
            continue
        try:
            assert 能力(偽造) != 正規文字
        except BaseException:
            pass
    with pytest.raises(ValueError, match="^呼叫成功結果不符合契約$"):
        偽造.to_json()


def test_成功結果拒絕敵對mappingproxy且不觸發任何mapping_callback():
    class 敵對映射(Mapping):
        def __init__(self):
            self.次數 = 0

        def __getitem__(self, key):
            self.次數 += 1
            raise AssertionError(key)

        def __iter__(self):
            self.次數 += 1
            raise AssertionError("iter")

        def __len__(self):
            self.次數 += 1
            raise AssertionError("len")

    結果, *_ = _執行案例([執行嘗試結果("success", {"answer": 1})], [True])
    敵對 = 敵對映射()
    object.__setattr__(結果.envelope, "data", MappingProxyType(敵對))
    with pytest.raises(ValueError, match="^呼叫成功結果不符合契約$"):
        結果.to_json()
    assert 敵對.次數 == 0


def test_成功結果弱登錄隨實例回收且輸出永遠fresh():
    gc.collect()
    結果, *_ = _執行案例([執行嘗試結果("success", {"nested": [1]})], [True])
    參照 = weakref.ref(結果)
    第一次 = 結果.to_json()
    第一次["envelope"]["data"]["nested"].append(2)
    assert 結果.to_json()["envelope"]["data"] == {"nested": [1]}
    del 結果
    gc.collect()
    assert 參照() is None


def test_成功結果平行序列化與公開欄位竄改只會原值或固定失敗():
    結果, *_ = _執行案例([執行嘗試結果("success", {"answer": "original"})], [True])
    開始, 停止 = Event(), Event()
    工作錯誤 = []

    def 竄改():
        try:
            開始.set()
            while not 停止.is_set():
                object.__setattr__(結果.envelope.endpoint, "id", "forged")
                object.__setattr__(結果.envelope.endpoint, "id", "ep")
        except BaseException as 錯誤:
            工作錯誤.append(錯誤)
            停止.set()

    執行緒 = Thread(target=竄改)
    執行緒.start()
    assert 開始.wait(1)
    try:
        for _ in range(200):
            try:
                輸出 = 結果.to_json()
            except ValueError as 錯誤:
                assert str(錯誤) == "呼叫成功結果不符合契約"
            else:
                assert 輸出["envelope"]["endpoint"]["id"] == "ep"
                assert 輸出["envelope"]["data"] == {"answer": "original"}
    finally:
        停止.set()
        執行緒.join(2)
    assert not 執行緒.is_alive() and 工作錯誤 == []


class 可切換解析器:
    """以同一把鎖原子讀取／切換 current pointer 的 deterministic I06 fake。"""

    def __init__(self, current):
        self._current, self._lock, self.呼叫 = current, Lock(), []

    def 依slug解析(self, slug):
        with self._lock:
            self.呼叫.append((slug, self._current.version_id))
            return self._current

    def 發布(self, current):
        with self._lock:
            self._current = current


def _I06版本(number):
    return I06釘選("ep", f"sa-{number}", f"ver-{number}", number, *(f"v{number}-{name}" for name in (
        "model", "runtime", "tool", "schema",
    )))


def _I06編排(resolver, credential, policy, requests, validations):
    def adapter(request):
        requests.append(request)
        pin = request.pinned_version
        assert (pin.model_snapshot, pin.runtime_snapshot, pin.tool_snapshot, pin.schema_snapshot) == tuple(
            f"v{pin.version_number}-{name}" for name in ("model", "runtime", "tool", "schema")
        )
        return 執行嘗試結果("success", {"version": pin.version_number})

    def validator(pin, data):
        validations.append((pin, data))
        return len(validations) % 2 == 0

    return _編排(
        resolver, credential, policy, 執行嘗試=adapter, 驗證輸出=validator,
        釘選類型=I06釘選,
    )


def test_相容PUB已釘選版本exact_shape並重建request_private服務帳戶():
    raw = PUB已釘選版本("ep-1", "sa-1", "ver-1", 1, False, 1.0, "{}")
    credential = 假憑證服務(驗證結果(狀態.有效, "cred", "ep-1", "active", 30, 60))
    entry = _編排(假解析器(raw), credential, 假政策(), 釘選類型=PUB已釘選版本).開始(
        "demo", "req", "raw", {}, None, 1,
    )

    assert entry.error is None and entry.pinned_version is not raw
    assert PUB已釘選版本.__slots__ == (
        "endpoint_id", "service_account_id", "version_id", "version_number",
        "schema_changed", "created_at", "_版本JSON",
    )
    assert object.__getattribute__(entry.pinned_version, "service_account_id") == "sa-1"


def test_service_account_pin缺漏空值子類敵對與竄改在副作用前失敗關閉():
    @dataclass(frozen=True, slots=True)
    class 舊釘選:
        endpoint_id: str
        version_id: str
        version_number: int

    class 敵對字串(str):
        def __str__(self):
            callbacks.append("str")
            return super().__str__()

    callbacks = []
    none_pin, subclass_pin, hostile_pin = _I06版本(1), _I06版本(1), _I06版本(1)
    object.__setattr__(none_pin, "service_account_id", None)
    object.__setattr__(subclass_pin, "service_account_id", 敵對字串("sa-1"))
    object.__setattr__(hostile_pin, "service_account_id", {"PIN_SA_MARKER": 1})
    cases = ((舊釘選("ep", "ver-1", 1), 舊釘選), (none_pin, I06釘選),
             (subclass_pin, I06釘選), (hostile_pin, I06釘選))
    for raw, pin_type in cases:
        credential, policy = 假憑證服務(None), 假政策()
        with pytest.raises(外部呼叫編排錯誤):
            _編排(假解析器(raw), credential, policy, 釘選類型=pin_type).開始(
                "demo", "req", "raw", {}, None, 1,
            )
        assert credential.驗證呼叫 == policy.準備呼叫 == policy.寫入呼叫 == []
    assert callbacks == []


@pytest.mark.parametrize("field", ["endpoint_id", "service_account_id", "version_id"])
@pytest.mark.parametrize("invalid", ["", "bad id", "x" * 129])
def test_pin三個權威識別套用相同bounded_safe規則且零副作用(field, invalid):
    raw = _I06版本(1)
    object.__setattr__(raw, field, invalid)
    credential, policy = 假憑證服務(None), 假政策()

    with pytest.raises(外部呼叫編排錯誤):
        _編排(假解析器(raw), credential, policy, 釘選類型=I06釘選).開始(
            "demo", "req", "raw", {}, None, 1,
        )

    assert credential.驗證呼叫 == policy.準備呼叫 == policy.寫入呼叫 == []


def test_請求開始原子釘選且發布與來源物件竄改不影響重試終局或下一請求():
    v1, v2 = _I06版本(1), _I06版本(2)
    resolver = 可切換解析器(v1)
    已釘選, 繼續A = Event(), Event()
    authentication = 驗證結果(狀態.有效, "cred", "ep", "active", 30, 60)
    credential_a, policy_a, requests_a, validations_a = 假憑證服務(authentication), 假政策(), [], []
    original_verify = credential_a.驗證

    def pause_after_pin(endpoint_id, api_key):
        已釘選.set()
        assert 繼續A.wait(2)
        return original_verify(endpoint_id, api_key)

    credential_a.驗證 = pause_after_pin
    orchestrator_a = _I06編排(resolver, credential_a, policy_a, requests_a, validations_a)
    result_a, worker_errors = [], []

    def run_a():
        error = None
        try:
            result_a.append(orchestrator_a.執行("demo", "req-a", "raw", {}, None, 1).to_json())
        except BaseException as caught:
            error = caught
            worker_errors.append(caught)
        finally:
            if error is not None:
                繼續A.set()

    thread_a = Thread(target=run_a)
    thread_a.start()
    try:
        assert 已釘選.wait(5)
        for field in I06釘選.__slots__:
            object.__setattr__(v1, field, object.__getattribute__(v2, field))
        resolver.發布(v2)
        credential_b, policy_b, requests_b, validations_b = 假憑證服務(authentication), 假政策(), [], []
        result_b = _I06編排(resolver, credential_b, policy_b, requests_b, validations_b).執行(
            "demo", "req-b", "raw", {}, None, 2,
        ).to_json()
    finally:
        繼續A.set()
        thread_a.join(5)

    assert not thread_a.is_alive() and worker_errors == []
    assert result_a[0]["envelope"]["endpoint"]["version"] == 1
    assert result_b["envelope"]["endpoint"]["version"] == 2
    assert [r.attempt for r in requests_a] == [1, 2] and [r.attempt for r in requests_b] == [1, 2]
    assert {r.pinned_version.version_number for r in requests_a} == {1}
    assert {r.pinned_version.version_number for r in requests_b} == {2}
    assert {r.pinned_version.service_account_id for r in requests_a} == {"sa-1"}
    assert {r.pinned_version.service_account_id for r in requests_b} == {"sa-2"}
    assert {pin.service_account_id for pin, _ in validations_a} == {"sa-1"}
    assert {pin.service_account_id for pin, _ in validations_b} == {"sa-2"}
    assert requests_a[0].pinned_version is requests_a[1].pinned_version
    assert requests_a[0].pinned_version is not v1 and requests_b[0].pinned_version is not v2
    assert policy_a.寫入呼叫[0][3] == "ver-1" and policy_b.寫入呼叫[0][3] == "ver-2"
    for credential, policy in ((credential_a, policy_a), (credential_b, policy_b)):
        assert len(credential.刷新呼叫) == len(policy.限流呼叫) == len(policy.寫入呼叫) == 1


def test_同時N請求跨發布每請求只有單一不可變版本且副作用彼此隔離():
    resolver = 可切換解析器(_I06版本(1))
    first_count, second_count = 4, 4
    pinned_barrier, release_first = Barrier(first_count + 1), Event()
    results, observations, worker_errors, result_lock, threads = [], [], [], Lock(), []

    def launch(index, before_publish):
        error = None
        credential = 假憑證服務(驗證結果(狀態.有效, f"cred-{index}", "ep", "active", 30, 60))
        policy, requests, validations = 假政策(), [], []
        if before_publish:
            original = credential.驗證

            def pause(endpoint_id, api_key):
                pinned_barrier.wait(timeout=5)
                assert release_first.wait(5)
                return original(endpoint_id, api_key)

            credential.驗證 = pause
        try:
            result = _I06編排(resolver, credential, policy, requests, validations).執行(
                "demo", f"req-{index}", "raw", {}, None, index + 1,
            ).to_json()
            with result_lock:
                results.append(result)
                observations.append((requests, validations, credential, policy))
        except BaseException as caught:
            error = caught
            with result_lock:
                worker_errors.append(caught)
        finally:
            if error is not None:
                pinned_barrier.abort()
                release_first.set()

    for index in range(first_count):
        thread = Thread(target=launch, args=(index, True))
        threads.append(thread)
        thread.start()
    try:
        pinned_barrier.wait(timeout=5)
        resolver.發布(_I06版本(2))
        for index in range(first_count, first_count + second_count):
            thread = Thread(target=launch, args=(index, False))
            threads.append(thread)
            thread.start()
    finally:
        release_first.set()
        pinned_barrier.abort()
        for thread in threads:
            thread.join(5)

    assert all(not thread.is_alive() for thread in threads) and worker_errors == [] and len(results) == 8
    assert sorted(result["envelope"]["endpoint"]["version"] for result in results) == [1] * 4 + [2] * 4
    for requests, validations, credential, policy in observations:
        versions = {request.pinned_version.version_number for request in requests}
        service_accounts = {request.pinned_version.service_account_id for request in requests}
        assert len(versions) == 1 and all(pin is requests[0].pinned_version for pin, _ in validations)
        assert service_accounts == {f"sa-{next(iter(versions))}"}
        assert [request.attempt for request in requests] == [1, 2]
        assert len(credential.刷新呼叫) == len(policy.限流呼叫) == len(policy.寫入呼叫) == 1


def test_pre_barrier_worker失敗會中止屏障並有界傳回所有工作錯誤():
    barrier, release = Barrier(3), Event()
    errors, threads, lock = [], [], Lock()

    def worker(fail):
        error = None
        try:
            if fail:
                raise RuntimeError("pre-barrier")
            barrier.wait(timeout=5)
            assert release.wait(5)
        except BaseException as caught:
            error = caught
            with lock:
                errors.append(caught)
        finally:
            if error is not None:
                barrier.abort()
            release.set()

    for fail in (False, True):
        thread = Thread(target=worker, args=(fail,))
        threads.append(thread)
        thread.start()
    try:
        for thread in threads:
            thread.join(5)
    finally:
        barrier.abort()
        release.set()
        for thread in threads:
            thread.join(5)

    assert all(not thread.is_alive() for thread in threads)
    assert {type(error) for error in errors} == {RuntimeError, BrokenBarrierError}


def test_發布發生於schema重試期間終局錯誤ref仍固定原版本():
    resolver, runtime_entered, release = 可切換解析器(_I06版本(1)), Event(), Event()
    requests = []

    def adapter(request):
        requests.append(request)
        if request.attempt == 1:
            runtime_entered.set()
            assert release.wait(2)
        return 執行嘗試結果("success", {"invalid": request.attempt})

    result, worker_errors = [], []
    orchestrator = _編排(
        resolver, 假憑證服務(驗證結果(狀態.有效, "cred", "ep", "active", 30, 60)), 假政策(),
        執行嘗試=adapter, 驗證輸出=lambda pin, data: False, 釘選類型=I06釘選,
    )
    def run():
        error = None
        try:
            result.append(orchestrator.執行("demo", "req-old", "raw", {}, None, 1).to_json())
        except BaseException as caught:
            error = caught
            worker_errors.append(caught)
        finally:
            if error is not None:
                release.set()

    thread = Thread(target=run)
    thread.start()
    try:
        assert runtime_entered.wait(5)
        resolver.發布(_I06版本(2))
    finally:
        release.set()
        thread.join(5)

    envelope = result[0]["envelope"]
    assert not thread.is_alive() and worker_errors == []
    assert envelope["error"]["code"] == "model_output_schema_invalid"
    assert envelope["endpoint"]["version"] == 1 and envelope["invocation"]["request_id"] == "req-old"
    assert {request.pinned_version.version_number for request in requests} == {1}
    assert {request.pinned_version.service_account_id for request in requests} == {"sa-1"}


def test_釘選子類可變欄位與instance_shadow在任何副作用前固定拒絕():
    class 子類釘選(I06釘選):
        pass

    class 影子釘選:
        __slots__ = ("endpoint_id", "version_id", "version_number", "__dict__")

        def __init__(self):
            self.endpoint_id, self.version_id, self.version_number = "ep", "ver-1", 1

    forged = _I06版本(1)
    object.__setattr__(forged, "tool_snapshot", {"FORGED_PIN_MARKER": 1})
    cases = ((子類釘選("ep", "sa-1", "ver-1", 1, "m", "r", "t", "s"), I06釘選),
             (forged, I06釘選), (影子釘選(), 影子釘選))
    for raw, expected in cases:
        credential, policy = 假憑證服務(驗證結果(狀態.有效)), 假政策()
        with pytest.raises(外部呼叫編排錯誤) as caught:
            _編排(假解析器(raw), credential, policy, 釘選類型=expected).開始(
                "demo", "req", "raw", {}, None, 1,
            )
        assert credential.驗證呼叫 == policy.準備呼叫 == policy.寫入呼叫 == []
        _生產追蹤不含標記(caught.value, "FORGED_PIN_MARKER")


def test_釘選重建控制流程保持identity且清理來源標記():
    raw = object.__new__(控制釘選)
    object.__setattr__(raw, "endpoint_id", "ep")
    object.__setattr__(raw, "service_account_id", "sa-control")
    object.__setattr__(raw, "version_id", "PIN_SOURCE_MARKER")
    object.__setattr__(raw, "version_number", 1)
    credential, policy = 假憑證服務(None), 假政策()
    with pytest.raises(KeyboardInterrupt) as caught:
        _編排(假解析器(raw), credential, policy, 釘選類型=控制釘選).開始(
            "demo", "req", "raw", {}, None, 1,
        )
    assert caught.value.args == ("PIN_CONTROL_MARKER",)
    assert credential.驗證呼叫 == policy.準備呼叫 == policy.寫入呼叫 == []
    for marker in ("PIN_SOURCE_MARKER", "PIN_CONTROL_MARKER"):
        _生產追蹤不含標記(caught.value, marker)
