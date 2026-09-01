"""Gemini ADC Published Runtime bridge 的 pinned config 與錯誤邊界測試。"""

from types import ModuleType, SimpleNamespace
import sys

import pytest

from 繁中代理.模型供應商 import GeminiADC供應商, 模型回應
from 繁中代理.發布介面.執行期.模型契約 import (
    供應商逾時,
    模型回應快照,
    模型轉接錯誤,
    模型轉接請求,
)
from 繁中代理.發布介面.執行期.模型轉接器 import 建立模型轉接器


@pytest.fixture
def 假SDK(monkeypatch):
    """安裝不碰網路且完整記錄 Client/config/generate 呼叫的 google-genai 假模組。"""
    狀態 = SimpleNamespace(客戶端=[], 設定=[], 產生=[], 回傳=object(), 例外=None, 建構例外={})

    def 檢查建構(名稱):
        if 名稱 in 狀態.建構例外:
            raise 狀態.建構例外[名稱]

    class SDK物件:
        def __init__(self, **參數):
            檢查建構(type(self).__name__)
            self.參數 = 參數

    class Content(SDK物件):
        pass

    class Part(SDK物件):
        pass

    class FunctionResponse(SDK物件):
        pass

    class FunctionCall(SDK物件):
        pass

    class FunctionDeclaration(SDK物件):
        model_fields = {"parameters_json_schema": object()}

    class Tool(SDK物件):
        pass

    class Schema:
        model_fields = {"type": object(), "properties": object(), "items": object()}

    class HttpOptions:
        def __init__(self, **參數):
            檢查建構("HttpOptions")
            self.參數 = 參數

    class GenerateContentConfig:
        def __init__(self, **參數):
            檢查建構("GenerateContentConfig")
            self.參數 = 參數
            狀態.設定.append(self)

    class 模型端點:
        def generate_content(self, **參數):
            狀態.產生.append(參數)
            if 狀態.例外 is not None:
                raise 狀態.例外
            return 狀態.回傳

    class Client:
        def __init__(self, **參數):
            檢查建構("Client")
            self.參數 = 參數
            self.models = 模型端點()
            狀態.客戶端.append(self)

    google模組 = ModuleType("google")
    genai模組 = ModuleType("google.genai")
    types模組 = ModuleType("google.genai.types")
    genai模組.Client = Client
    genai模組.types = types模組
    google模組.genai = genai模組
    types模組.HttpOptions = HttpOptions
    types模組.GenerateContentConfig = GenerateContentConfig
    types模組.Content = Content
    types模組.Part = Part
    types模組.FunctionResponse = FunctionResponse
    types模組.FunctionCall = FunctionCall
    types模組.FunctionDeclaration = FunctionDeclaration
    types模組.Tool = Tool
    types模組.Schema = Schema
    monkeypatch.setitem(sys.modules, "google", google模組)
    monkeypatch.setitem(sys.modules, "google.genai", genai模組)
    monkeypatch.setitem(sys.modules, "google.genai.types", types模組)
    return 狀態


def _發布參數(**覆寫):
    """建立有效的非預設 published bridge keyword arguments。"""
    參數 = {
        "model": "pinned-model", "temperature": 1.1, "max_tokens": 456,
        "timeout_seconds": 1.0001, "structured_output": True,
        "schema_retry_count": 1, "messages": [{"role": "user", "content": "hi"}],
        "tools": [{"type": "function"}], "response_schema": {"type": "object"},
    }
    參數.update(覆寫)
    return 參數


def test_Gemini發布橋接逐字套用釘選設定且輸出脫離(假SDK, monkeypatch):
    """Client/HttpOptions/config/model 都只使用呼叫方的 pinned snapshot。"""
    供應商 = GeminiADC供應商("instance-live-model", "project-x", "asia-east1")
    舊回應 = 模型回應(文字="完成", 完成原因="STOP", 使用量={"total": 3}, 工具呼叫清單=[{"id": "x"}])
    monkeypatch.setattr(供應商, "轉成Gemini內容", lambda 值: ["CONTENTS"])
    monkeypatch.setattr(供應商, "轉成Gemini工具", lambda 值: ["TOOLS"])
    monkeypatch.setattr(供應商, "轉成模型回應", lambda 值: 舊回應)

    結果 = 供應商.產生發布回應(**_發布參數())

    assert len(假SDK.客戶端) == 1 and len(假SDK.產生) == 1
    客戶參數 = 假SDK.客戶端[0].參數
    assert {鍵: 客戶參數[鍵] for 鍵 in ("vertexai", "project", "location")} == {
        "vertexai": True, "project": "project-x", "location": "asia-east1",
    }
    assert 客戶參數["http_options"].參數 == {"timeout": 1001}
    assert 假SDK.產生[0]["model"] == "pinned-model"
    assert 假SDK.產生[0]["contents"] == ["CONTENTS"]
    assert 假SDK.設定[0].參數 == {
        "temperature": 1.1, "max_output_tokens": 456, "tools": ["TOOLS"],
        "response_mime_type": "application/json",
        "response_json_schema": {"type": "object"},
    }
    assert 結果 == 模型回應快照("完成", "STOP", {"total": 3}, [{"id": "x"}])
    舊回應.使用量["total"] = 99
    assert 結果.usage == {"total": 3}


def test_structured_false不送schema且拒絕非None_schema(假SDK, monkeypatch):
    """非 structured 模式省略 MIME/schema，並在 SDK 前拒絕矛盾 schema。"""
    供應商 = GeminiADC供應商("live", "project")
    monkeypatch.setattr(供應商, "轉成Gemini內容", lambda 值: [])
    monkeypatch.setattr(供應商, "轉成Gemini工具", lambda 值: [])
    monkeypatch.setattr(供應商, "轉成模型回應", lambda 值: 模型回應())
    供應商.產生發布回應(**_發布參數(structured_output=False, response_schema=None))
    assert 假SDK.設定[0].參數 == {"temperature": 1.1, "max_output_tokens": 456, "tools": None}

    with pytest.raises(ValueError):
        供應商.產生發布回應(**_發布參數(structured_output=False, response_schema={"x": 1}))
    assert len(假SDK.客戶端) == 1


@pytest.mark.parametrize("例外", [TimeoutError("marker"), pytest.importorskip("httpx").TimeoutException("marker")])
def test_SDK逾時轉成全新無鏈結供應商逾時(假SDK, monkeypatch, 例外):
    """builtin/httpx timeout 都在 provider boundary 轉成專用 fresh signal。"""
    供應商 = GeminiADC供應商("live", "project")
    monkeypatch.setattr(供應商, "轉成Gemini內容", lambda 值: [])
    monkeypatch.setattr(供應商, "轉成Gemini工具", lambda 值: [])
    假SDK.例外 = 例外
    with pytest.raises(供應商逾時, match="^Gemini 供應商逾時$") as 錯誤:
        供應商.產生發布回應(**_發布參數())
    assert 錯誤.value is not 例外
    assert 錯誤.value.__cause__ is None and 錯誤.value.__context__ is None


def _外層呼叫(供應商, marker="marker"):
    設定 = {
        "provider": "gemini", "model": "pinned", "temperature": 1.0,
        "max_tokens": 20, "timeout_seconds": 2.0, "structured_output": True,
        "schema_retry_count": 1,
    }
    請求 = 模型轉接請求(
        [{"role": "user", "content": marker}],
        [{"type": "function", "function": {"name": marker, "parameters": {"type": "object"}}}],
        {"type": "object", "marker": marker},
    )
    return 建立模型轉接器({"gemini": 供應商}, 設定).產生回應(請求)


@pytest.mark.parametrize("階段", ["Content", "FunctionDeclaration", "GenerateContentConfig", "HttpOptions", "Client", "response"])
def test_非request階段TimeoutError不得誤分類為模型逾時(假SDK, 階段):
    """只有 generate_content boundary 可產生 dedicated timeout signal。"""
    marker = f"TIMEOUT/{階段}"
    供應商 = GeminiADC供應商("live", "project")
    if 階段 == "response":
        class 惡意回應:
            @property
            def candidates(self):
                raise TimeoutError(marker)
        假SDK.回傳 = 惡意回應()
    else:
        假SDK.建構例外[階段] = TimeoutError(marker)
    with pytest.raises(模型轉接錯誤, match="^模型供應商呼叫失敗$") as 錯誤:
        _外層呼叫(供應商, marker)
    assert 錯誤.value.code == "model_adapter_error"
    assert 錯誤.value.__cause__ is None and 錯誤.value.__context__ is None


def test_任意SDK失敗由outer_adapter固定化(假SDK, monkeypatch):
    """非 timeout SDK failure 不由 bridge 洩漏，outer adapter 固定為 public error。"""
    供應商 = GeminiADC供應商("live", "project")
    monkeypatch.setattr(供應商, "轉成Gemini內容", lambda 值: [])
    monkeypatch.setattr(供應商, "轉成Gemini工具", lambda 值: [])
    假SDK.例外 = RuntimeError("SECRET")
    設定 = {
        "provider": "gemini", "model": "pinned", "temperature": 1.0,
        "max_tokens": 20, "timeout_seconds": 2.0, "structured_output": True,
        "schema_retry_count": 1,
    }
    with pytest.raises(模型轉接錯誤, match="^模型供應商呼叫失敗$") as 錯誤:
        建立模型轉接器({"gemini": 供應商}, 設定).產生回應(
            模型轉接請求([], [], {"type": "object"})
        )
    assert 錯誤.value.__cause__ is None and 錯誤.value.__context__ is None


def _assert_Gemini生產框架無標記(traceback, marker):
    def 含標記(值, 已看):
        if id(值) in 已看:
            return False
        已看.add(id(值))
        if type(值) is str:
            return marker in 值
        if type(值) is dict:
            for 鍵, 子值 in dict.items(值):
                if 含標記(鍵, 已看) or 含標記(子值, 已看):
                    return True
        elif type(值) in (tuple, list, set, frozenset):
            for 子值 in 值:
                if 含標記(子值, 已看):
                    return True
        elif isinstance(值, BaseException):
            return 含標記(值.args, 已看)
        if isinstance(值, ModuleType) or isinstance(值, type) or callable(值):
            return False
        try:
            欄位 = object.__getattribute__(值, "__dict__")
        except (AttributeError, TypeError):
            欄位 = None
        return type(欄位) is dict and 含標記(欄位, 已看)

    框架名稱 = []
    while traceback is not None:
        框架 = traceback.tb_frame
        if 框架.f_globals.get("__name__", "") in (
            "繁中代理.模型供應商", "繁中代理.發布介面.執行期.模型轉接器",
        ):
            框架名稱.append(框架.f_code.co_name)
            for 值 in tuple(框架.f_locals.values()):
                assert not 含標記(值, set()), 框架.f_code.co_name
        traceback = traceback.tb_next
    return 框架名稱


class 惡意SDK錯誤(BaseException):
    pass


@pytest.mark.parametrize("來源", ["Content", "FunctionDeclaration", "response_part"])
@pytest.mark.parametrize("例外型別", [惡意SDK錯誤, KeyboardInterrupt, SystemExit, GeneratorExit])
def test_nested_helper例外穿透政策與所有生產框架清理(假SDK, 來源, 例外型別):
    marker = f"NESTED/{來源}/{例外型別.__name__}"
    原例外 = 例外型別(marker)
    供應商 = GeminiADC供應商("live", "project")
    if 來源 in ("Content", "FunctionDeclaration"):
        假SDK.建構例外[來源] = 原例外
    else:
        class 惡意內容:
            @property
            def parts(self):
                raise 原例外
        假SDK.回傳 = SimpleNamespace(
            candidates=[SimpleNamespace(finish_reason="STOP", content=惡意內容())]
        )
    預期 = 模型轉接錯誤 if 例外型別 is 惡意SDK錯誤 else 例外型別
    with pytest.raises(預期) as 錯誤:
        _外層呼叫(供應商, marker)
    if 例外型別 is 惡意SDK錯誤:
        assert str(錯誤.value) == "模型供應商呼叫失敗"
        assert 錯誤.value.__cause__ is None and 錯誤.value.__context__ is None
    else:
        assert 錯誤.value is 原例外 and 錯誤.value.args == (marker,)
        框架 = _assert_Gemini生產框架無標記(錯誤.tb, marker)
        assert "產生回應" in 框架 and "產生發布回應" in 框架
        assert {"轉成Gemini內容", "轉成Gemini工具", "轉成模型回應"} & set(框架)


@pytest.mark.parametrize("例外型別", [KeyboardInterrupt, SystemExit, GeneratorExit])
def test_控制流程identity_args與provider_adapter框架清理(假SDK, monkeypatch, 例外型別):
    """K/I/S/G 原物件穿透，且 provider 與 adapter production frames 不保留 marker。"""
    marker = "GEMINI/FRAME/MARKER"
    原例外 = 例外型別(marker)
    供應商 = GeminiADC供應商("live", "project")
    monkeypatch.setattr(供應商, "轉成Gemini內容", lambda 值: [])
    monkeypatch.setattr(供應商, "轉成Gemini工具", lambda 值: [])
    假SDK.例外 = 原例外
    設定 = {
        "provider": "gemini", "model": marker, "temperature": 1.0,
        "max_tokens": 20, "timeout_seconds": 2.0, "structured_output": True,
        "schema_retry_count": 1,
    }
    with pytest.raises(例外型別) as 錯誤:
        建立模型轉接器({"gemini": 供應商}, 設定).產生回應(
            模型轉接請求([{"content": marker}], [], {"marker": marker})
        )
    assert 錯誤.value is 原例外 and 錯誤.value.args == (marker,)
    追蹤 = 錯誤.tb
    生產框架 = []
    while 追蹤 is not None:
        名稱 = 追蹤.tb_frame.f_globals.get("__name__", "")
        if 名稱 in ("繁中代理.模型供應商", "繁中代理.發布介面.執行期.模型轉接器"):
            生產框架.append(追蹤.tb_frame.f_code.co_name)
            assert marker not in repr(tuple(追蹤.tb_frame.f_locals.values()))
        追蹤 = 追蹤.tb_next
    assert "產生發布回應" in 生產框架 and "產生回應" in 生產框架


def test_legacy產生回應仍使用instance_model(假SDK, monkeypatch):
    """既有 legacy 產生回應介面維持 instance model 與舊回應型別。"""
    供應商 = GeminiADC供應商("legacy-model", "project")
    預期 = 模型回應(文字="legacy")
    monkeypatch.setattr(供應商, "轉成Gemini內容", lambda 值: ["legacy-content"])
    monkeypatch.setattr(供應商, "轉成Gemini工具", lambda 值: [])
    monkeypatch.setattr(供應商, "轉成模型回應", lambda 值: 預期)
    assert 供應商.產生回應([], []) is 預期
    assert 假SDK.產生[0]["model"] == "legacy-model"
