"""U06 Draft 2020-12 結構化輸出與唯一一次綱要重試。"""

import copy
import hashlib
import json
import sys
import threading

import pytest

import 繁中代理.發布介面.執行期.執行器 as 執行器模組
from 繁中代理.發布介面.執行期.服務帳戶 import ServiceAccountContext
from 繁中代理.發布介面.執行期.工具版本庫 import 工具版本庫
from 繁中代理.發布介面.執行期.模型契約 import (
    供應商逾時, 模型回應快照, 模型設定快照, 模型設定錯誤,
)
from 繁中代理.發布介面.執行期.執行器 import (
    技能套件快照, 技能套件檔案, 發布執行快照, 發布執行請求,
    發布執行錯誤, 結構化輸出錯誤, 建立發布執行器, 計算技能套件雜湊,
)


class _模型:
    def __init__(self, 回應們):
        self.回應們 = list(回應們)
        self.呼叫 = []

    def 產生發布回應(self, **參數):
        self.呼叫.append(copy.deepcopy(參數))
        回應 = self.回應們.pop(0)
        if isinstance(回應, BaseException):
            raise 回應
        return 模型回應快照(回應, "stop", {}, [])


class _版本:
    def __init__(self, 快照):
        self.快照 = 快照
        self.呼叫數 = 0

    def 取得發布執行快照(self, version_id):
        self.呼叫數 += 1
        return self.快照


class _帳戶:
    def __init__(self, 上下文):
        self.上下文 = 上下文
        self.呼叫數 = 0

    def 載入服務帳戶上下文(self, *參數):
        self.呼叫數 += 1
        return self.上下文


class _套件:
    def __init__(self, 快照):
        self.快照 = 快照

    def 載入技能套件快照(self, *參數):
        return self.快照


def _建立(schema, 回應們, 觀察=None, 模型物件=None):
    檔案 = 技能套件檔案(
        path="SKILL.md", sha256=hashlib.sha256(b"skill").hexdigest(), content=b"skill",
    )
    雜湊 = 計算技能套件雜湊((檔案,))
    結構化 = schema is not None
    版本快照 = 發布執行快照(
        endpoint_id="ep-1", version_id="ver-1", service_account_id="sa-1",
        system_prompt="system", permission_snapshot_digest="a" * 64,
        skill_bundle_hash=雜湊, tool_handler_release="release-1", tool_snapshot=(),
        model_config=模型設定快照("fake", "m", 0, 100, 5, 結構化, 1),
        response_schema=schema, manifest_reference="manifest-1",
    )
    上下文 = ServiceAccountContext(
        service_account_id="sa-1", endpoint_version_id="ver-1",
        permission_snapshot_digest="a" * 64, allowed_tools=(), skill_bundle_hash=雜湊,
        tool_handler_release="release-1",
    )
    套件 = 技能套件快照(
        endpoint_version_id="ver-1", skill_bundle_hash=雜湊,
        manifest_digest=雜湊, files=(檔案,),
    )
    版本提供者 = _版本(版本快照)
    帳戶載入器 = _帳戶(上下文)
    模型 = _模型(回應們) if 模型物件 is None else 模型物件
    if 觀察 is not None:
        觀察.update(版本=版本提供者, 帳戶=帳戶載入器, 模型=模型)
    執行器 = 建立發布執行器(
        endpoint_version_id="ver-1", service_account_id="sa-1",
        發布快照提供者=版本提供者, 服務帳戶載入器=帳戶載入器,
        技能套件載入器=_套件(套件), 工具修訂提供者=工具版本庫(),
        模型供應商註冊表={"fake": 模型},
    )
    return 執行器, 模型, 版本提供者, 帳戶載入器


def test_無綱要任意文字原樣接受且只呼叫一次():
    執行器, 模型, *_ = _建立(None, ["not json NaN"])
    assert 執行器.執行(發布執行請求({"q": 1})).text == "not json NaN"
    assert len(模型.呼叫) == 1


@pytest.mark.parametrize(
    ("schema", "text"),
    [
        ({"type": "object"}, '{ "z": 1, "a": true }'),
        ({"type": "array"}, "[1, 2]"),
        ({"type": "string"}, '"ok"'),
        ({"type": "null"}, "null"),
    ],
)
def test_第一回有效物件陣列純量或null不重試(schema, text):
    執行器, 模型, *_ = _建立(schema, [text])
    assert 執行器.執行(發布執行請求({})).text == text
    assert len(模型.呼叫) == 1


@pytest.mark.parametrize("第一", ["not-json", '{"x":1,"x":2}', '{"x":NaN}', '{"x":"bad"}'])
def test_語法或綱要無效只重試一次且不洩漏第一回(第一):
    schema = {
        "type": "object", "required": ["x"], "additionalProperties": False,
        "properties": {"x": {"type": "integer"}},
    }
    執行器, 模型, *_ = _建立(schema, [第一, '{ "x": 2 }'])
    請求 = 發布執行請求({"q": [1]})
    assert 執行器.執行(請求).text == '{ "x": 2 }'
    assert len(模型.呼叫) == 2
    第一請求, 第二請求 = 模型.呼叫
    assert 第二請求["messages"][:-1] == 第一請求["messages"]
    assert 第二請求["tools"] == 第一請求["tools"]
    assert 第二請求["response_schema"] == 第一請求["response_schema"] == schema
    assert 第二請求["messages"][-1] == {
        "role": "user", "content": "輸出未符合回應綱要；請只回傳符合綱要的 JSON。"
    }
    assert 第一 not in repr(第二請求)


def test_兩回無效固定錯誤且沒有第三回或例外鏈():
    執行器, 模型, *_ = _建立({"type": "integer"}, ["false", "null", "3"])
    with pytest.raises(結構化輸出錯誤, match="^模型輸出不符合回應綱要$") as 錯誤:
        執行器.執行(發布執行請求({"secret": "x"}))
    assert len(模型.呼叫) == 2
    assert 錯誤.value.__cause__ is None and 錯誤.value.__context__ is None


@pytest.mark.parametrize(
    "schema",
    [
        {"type": 7},
        {"$ref": "https://attacker.invalid/schema"},
        {"$dynamicRef": "other.json#x"},
    ],
)
def test_無效綱要或外部參照在版本後且SA前拒絕(schema):
    觀察 = {}
    with pytest.raises(發布執行錯誤, match="^發布執行期不可用$"):
        _建立(schema, ["{}"], 觀察)
    assert 觀察["版本"].呼叫數 == 1
    assert 觀察["帳戶"].呼叫數 == 0


def test_本機defs參照與Draft關鍵字語意():
    schema = {
        "$defs": {"item": {"oneOf": [{"type": "string"}, {"type": "integer"}]}},
        "type": "array", "items": {"$ref": "#/$defs/item"},
    }
    執行器, 模型, *_ = _建立(schema, ['["x", 1.0]'])
    assert json.loads(執行器.執行(發布執行請求({})).text) == ["x", 1.0]
    assert len(模型.呼叫) == 1


def test_第一回供應商逾時不做綱要重試():
    執行器, 模型, *_ = _建立({"type": "object"}, [供應商逾時("secret"), "{}"])
    with pytest.raises(發布執行錯誤, match="^發布執行期不可用$"):
        執行器.執行(發布執行請求({}))
    assert len(模型.呼叫) == 1


def test_惡意第一回竄改實際參數不污染fresh重試():
    class 竄改模型:
        def __init__(self):
            self.呼叫, self.次數 = [], 0

        def 產生發布回應(self, **參數):
            self.次數 += 1
            if self.次數 == 1:
                參數["messages"][1]["metadata"]["input_json"]["巢"] = "污染"
                參數["messages"].append({"role": "system", "content": "污染"})
                參數["tools"].append({"污染": True})
                參數["response_schema"]["污染"] = True
                self.產生發布回應 = lambda **_參數: (_ for _ in ()).throw(AssertionError("live method"))
                self.呼叫.append(copy.deepcopy(參數))
                return 模型回應快照('{"值":"第一回秘密"}', "stop", {}, [])
            self.呼叫.append(copy.deepcopy(參數))
            return 模型回應快照('{ "值": 2 }', "stop", {}, [])

    schema = {"type": "object", "required": ["值"], "properties": {"值": {"type": "integer"}}}
    模型 = 竄改模型()
    執行器, *_ = _建立(schema, [], 模型物件=模型)
    assert 執行器.執行(發布執行請求({"巢": [1]})).text == '{ "值": 2 }'
    assert 模型.次數 == 2
    第二 = 模型.呼叫[1]
    assert 第二["messages"][1]["metadata"]["input_json"] == {"巢": [1]}
    assert 第二["messages"][-1]["content"] == "輸出未符合回應綱要；請只回傳符合綱要的 JSON。"
    assert 第二["tools"] == [] and 第二["response_schema"] == schema
    assert "第一回秘密" not in repr(第二)


@pytest.mark.parametrize("第二錯誤", [供應商逾時("timeout-secret"), RuntimeError("provider-secret")])
def test_第一回綱要無效後第二回供應商失敗不第三回也不改成綱要錯誤(第二錯誤):
    執行器, 模型, *_ = _建立({"type": "integer"}, ["false", 第二錯誤, "3"])
    with pytest.raises(發布執行錯誤, match="^發布執行期不可用$") as 錯誤:
        執行器.執行(發布執行請求({}))
    assert type(錯誤.value) is 發布執行錯誤 and len(模型.呼叫) == 2


def _深值():
    值 = 0
    for _ in range(70):
        值 = [值]
    return json.dumps(值)


@pytest.mark.parametrize("第一", [_深值(), json.dumps([0] * 10_001), json.dumps("x" * 500_001)])
def test_深度節點或純量資源超限恰好重試一次(第一):
    執行器, 模型, *_ = _建立({}, [第一, "{}"])
    assert 執行器.執行(發布執行請求({})).text == "{}"
    assert len(模型.呼叫) == 2


def test_兩回資源超限固定結構錯誤且恰好兩回():
    執行器, 模型, *_ = _建立({}, [_深值(), _深值(), "{}"])
    with pytest.raises(結構化輸出錯誤, match="^模型輸出不符合回應綱要$"):
        執行器.執行(發布執行請求({}))
    assert len(模型.呼叫) == 2


@pytest.mark.parametrize(("schema", "第一", "第二"), [
    ({"type": "object", "required": ["x"]}, "{}", '{"x":1}'),
    ({"type": "object", "additionalProperties": False}, '{"x":1}', "{}"),
    ({"oneOf": [{"type": "number"}, {"type": "integer"}]}, "1", "1.5"),
    ({"type": "array", "items": {"type": "array", "items": {"type": "integer"}}}, "[[true]]", "[[1]]"),
    ({"type": "number"}, "true", "1"),
])
def test_Draft必要欄位額外欄位oneOf巢狀陣列與bool_number語意(schema, 第一, 第二):
    執行器, 模型, *_ = _建立(schema, [第一, 第二])
    assert 執行器.執行(發布執行請求({})).text == 第二
    assert len(模型.呼叫) == 2


@pytest.mark.parametrize("鍵", ["$ref", "$dynamicRef"])
def test_深層外部參照遞迴拒絕且不進SA(鍵):
    schema = {"allOf": [{"items": {"properties": {"x": {鍵: "https://evil.invalid/x"}}}}]}
    觀察 = {}
    with pytest.raises(發布執行錯誤, match="^發布執行期不可用$"):
        _建立(schema, ["{}"], 觀察)
    assert 觀察["帳戶"].呼叫數 == 0


def test_schema_retry_count不是一在DTO建立即固定設定錯誤():
    with pytest.raises(模型設定錯誤, match="^發布模型設定不可用$"):
        模型設定快照("fake", "m", 0, 100, 5, True, 0)


控制們 = [KeyboardInterrupt, SystemExit, GeneratorExit]
控制們 += [type(f"U06控制子類{索引}", (型別,), {}) for 索引, 型別 in enumerate(控制們)]


def _執行器框架乾淨(錯誤, 標記):
    追蹤 = 錯誤.__traceback__
    while 追蹤 is not None:
        框架 = 追蹤.tb_frame
        if 框架.f_code.co_filename.endswith("執行器.py"):
            for 值 in tuple(框架.f_locals.values()):
                assert 標記 not in repr(值), 框架.f_code.co_name
        追蹤 = 追蹤.tb_next


@pytest.mark.parametrize("階段", ["check", "construct", "validate"])
def test_validator普通基礎設施錯誤固定且不當綱要無效(monkeypatch, 階段):
    schema = {"type": "integer"}
    if 階段 == "check":
        monkeypatch.setattr(執行器模組.Draft202012Validator, "check_schema", lambda *_: (_ for _ in ()).throw(RuntimeError("infra")))
        with pytest.raises(發布執行錯誤, match="^發布執行期不可用$"):
            _建立(schema, ["1"])
        return
    if 階段 == "construct":
        原類別 = 執行器模組.Draft202012Validator
        class 假類別:
            check_schema = staticmethod(原類別.check_schema)
            def __new__(cls, *_):
                raise RuntimeError("infra")
        monkeypatch.setattr(執行器模組, "Draft202012Validator", 假類別)
        with pytest.raises(發布執行錯誤, match="^發布執行期不可用$"):
            _建立(schema, ["1"])
        return
    執行器, 模型, *_ = _建立(schema, ["1", "2"])
    monkeypatch.setattr(執行器模組.Draft202012Validator, "validate", lambda *_: (_ for _ in ()).throw(RuntimeError("infra")))
    with pytest.raises(發布執行錯誤, match="^發布執行期不可用$"):
        執行器.執行(發布執行請求({}))
    assert len(模型.呼叫) == 1


def test_ValidationError子類視為validator基礎設施錯誤不重試(monkeypatch):
    from jsonschema.exceptions import ValidationError
    class 偽造錯誤(ValidationError):
        pass
    執行器, 模型, *_ = _建立({"type": "integer"}, ["1", "2"])
    monkeypatch.setattr(執行器模組.Draft202012Validator, "validate", lambda *_: (_ for _ in ()).throw(偽造錯誤("secret")))
    with pytest.raises(發布執行錯誤, match="^發布執行期不可用$"):
        執行器.執行(發布執行請求({}))
    assert len(模型.呼叫) == 1


@pytest.mark.parametrize("控制型別", 控制們)
@pytest.mark.parametrize("階段", ["parser1", "parser2", "model1", "model2", "validate"])
def test_執行期控制流程保留identity_args且所有執行器框架清理(monkeypatch, 控制型別, 階段):
    標記 = f"U06-{階段}-secret"
    中斷 = 控制型別(標記)
    if 階段.startswith("model"):
        回應 = ["false", 中斷] if 階段.endswith("2") else [中斷]
    else:
        回應 = ["false", "1"] if 階段.endswith("2") else ["1"]
    執行器, 模型, *_ = _建立({"type": "integer"}, 回應)
    if 階段.startswith("parser"):
        原函式, 次數 = 執行器模組.json.loads, 0
        def loads(*參數, **命名):
            nonlocal 次數
            if sys._getframe(1).f_code.co_name == "_解析模型JSON":
                次數 += 1
                if 次數 == int(階段[-1]):
                    raise 中斷
            return 原函式(*參數, **命名)
        monkeypatch.setattr(執行器模組.json, "loads", loads)
    elif 階段 == "validate":
        monkeypatch.setattr(執行器模組.Draft202012Validator, "validate", lambda *_: (_ for _ in ()).throw(中斷))
    with pytest.raises(控制型別) as 錯誤:
        執行器.執行(發布執行請求({}))
    assert 錯誤.value is 中斷 and 錯誤.value.args == (標記,)
    _執行器框架乾淨(錯誤.value, 標記)


def test_同一結構化執行器兩執行緒各自重試無串話():
    class 並行模型:
        def __init__(self):
            self.鎖, self.次數, self.紀錄 = threading.Lock(), {}, []
        def 產生發布回應(self, **參數):
            鍵 = 參數["messages"][1]["metadata"]["input_json"]["id"]
            with self.鎖:
                次數 = self.次數.get(鍵, 0) + 1
                self.次數[鍵] = 次數
                self.紀錄.append((鍵, 次數, copy.deepcopy(參數)))
            return 模型回應快照("false" if 次數 == 1 else json.dumps({"id": 鍵}), "stop", {}, [])
    模型 = 並行模型()
    schema = {"type": "object", "required": ["id"], "properties": {"id": {"type": "string"}}}
    執行器, *_ = _建立(schema, [], 模型物件=模型)
    barrier, 結果 = threading.Barrier(3), {}
    def 跑(鍵):
        barrier.wait()
        結果[鍵] = json.loads(執行器.執行(發布執行請求({"id": 鍵})).text)
    執行緒們 = [threading.Thread(target=跑, args=(鍵,)) for 鍵 in ("甲", "乙")]
    for 執行緒 in 執行緒們: 執行緒.start()
    barrier.wait()
    for 執行緒 in 執行緒們: 執行緒.join(5)
    assert 結果 == {"甲": {"id": "甲"}, "乙": {"id": "乙"}}
    assert 模型.次數 == {"甲": 2, "乙": 2}
    for 鍵, 次數, 參數 in 模型.紀錄:
        assert 參數["messages"][1]["metadata"]["input_json"] == {"id": 鍵}
        assert len(參數["messages"]) == 2 + (次數 == 2)


@pytest.mark.parametrize("控制型別", 控制們)
@pytest.mark.parametrize("階段", ["check", "construct_preflight", "construct_runtime"])
def test_validator建立各階段控制保留且清理(monkeypatch, 控制型別, 階段):
    標記 = f"U06-validator-{階段}"
    中斷 = 控制型別(標記)
    原類別 = 執行器模組.Draft202012Validator
    if 階段 == "check":
        def check_schema(*_參數):
            raise 中斷
        monkeypatch.setattr(原類別, "check_schema", check_schema)
    else:
        class 受控類別:
            check_schema = staticmethod(原類別.check_schema)
            次數 = 0
            def __new__(cls, 綱要):
                cls.次數 += 1
                if 階段 == "construct_preflight" or cls.次數 == 2:
                    raise 中斷
                return 原類別(綱要)
        monkeypatch.setattr(執行器模組, "Draft202012Validator", 受控類別)
    if 階段 == "construct_runtime":
        執行器, 模型, *_ = _建立({"type": "integer"}, ["1"])
        呼叫 = lambda: 執行器.執行(發布執行請求({}))
    else:
        模型 = None
        呼叫 = lambda: _建立({"type": "integer"}, ["1"])
    with pytest.raises(控制型別) as 錯誤:
        呼叫()
    assert 錯誤.value is 中斷 and 錯誤.value.args == (標記,)
    if 模型 is not None:
        assert len(模型.呼叫) == 1
    _執行器框架乾淨(錯誤.value, 標記)


class _基礎中斷(BaseException):
    pass


@pytest.mark.parametrize("階段", ["parser1", "parser2", "validate", "model1", "model2"])
def test_非控制BaseException一律固定執行錯誤不誤判為綱要錯誤(monkeypatch, 階段):
    中斷 = _基礎中斷(f"U06-base-{階段}")
    if 階段.startswith("model"):
        回應 = ["false", 中斷] if 階段.endswith("2") else [中斷]
    else:
        回應 = ["false", "1"] if 階段.endswith("2") else ["1"]
    執行器, 模型, *_ = _建立({"type": "integer"}, 回應)
    if 階段.startswith("parser"):
        原函式, 次數 = 執行器模組.json.loads, 0
        def loads(*參數, **命名):
            nonlocal 次數
            if sys._getframe(1).f_code.co_name == "_解析模型JSON":
                次數 += 1
                if 次數 == int(階段[-1]):
                    raise 中斷
            return 原函式(*參數, **命名)
        monkeypatch.setattr(執行器模組.json, "loads", loads)
    elif 階段 == "validate":
        monkeypatch.setattr(執行器模組.Draft202012Validator, "validate", lambda *_: (_ for _ in ()).throw(中斷))
    with pytest.raises(發布執行錯誤, match="^發布執行期不可用$") as 錯誤:
        執行器.執行(發布執行請求({}))
    assert type(錯誤.value) is 發布執行錯誤
    assert len(模型.呼叫) == (2 if 階段.endswith("2") else 1)
    assert 錯誤.value.__cause__ is None and 錯誤.value.__context__ is None


@pytest.mark.parametrize("階段", ["parser", "validate"])
def test_第一回模型後普通驗證依賴失敗不重試且固定無洩漏(monkeypatch, 階段):
    執行器, 模型, *_ = _建立({"type": "integer"}, ["1", "2"])
    if 階段 == "parser":
        原函式 = 執行器模組.json.loads
        def loads(*參數, **命名):
            if sys._getframe(1).f_code.co_name == "_解析模型JSON":
                raise RuntimeError("raw-output-secret")
            return 原函式(*參數, **命名)
        monkeypatch.setattr(執行器模組.json, "loads", loads)
    else:
        monkeypatch.setattr(執行器模組.Draft202012Validator, "validate", lambda *_: (_ for _ in ()).throw(RuntimeError("schema-path-secret")))
    with pytest.raises(發布執行錯誤, match="^發布執行期不可用$") as 錯誤:
        執行器.執行(發布執行請求({}))
    assert type(錯誤.value) is 發布執行錯誤 and len(模型.呼叫) == 1
    assert "secret" not in str(錯誤.value)
