"""RUN U03 immutable model snapshot 與 typed timeout 契約。"""

import gc
import inspect
import weakref
from concurrent.futures import ThreadPoolExecutor

import pytest

import 繁中代理.發布介面.執行期.模型契約 as 契約模組
import 繁中代理.發布介面.執行期.模型轉接器 as 轉接器模組
from 繁中代理.發布介面.執行期.模型轉接器 import (
    供應商逾時,
    模型回應快照,
    模型設定快照,
    模型設定錯誤,
    模型轉接錯誤,
    模型轉接請求,
    模型逾時錯誤,
    模型轉接器,
    建立模型轉接器,
)


def _設定(**覆寫):
    資料 = {
        "provider": "alpha", "model": "pinned-v7", "temperature": 1.25,
        "max_tokens": 321, "timeout_seconds": 17.5,
        "structured_output": True, "schema_retry_count": 1,
    }
    資料.update(覆寫)
    return 模型設定快照(**資料)


def _請求():
    return 模型轉接請求(
        messages=[{"role": "user", "content": "原文"}],
        tools=[{"type": "function", "function": {"name": "lookup"}}],
        response_schema={"type": "object", "required": ["answer"]},
    )


class 記錄供應商:
    def __init__(self, 回應=None, 例外=None):
        self.呼叫 = []
        self.回應 = 回應 or 模型回應快照(
            text="完成", finish_reason="stop", usage={"total_tokens": 9}, tool_calls=[]
        )
        self.例外 = 例外

    def 產生發布回應(self, **參數):
        self.呼叫.append(參數)
        if self.例外 is not None:
            raise self.例外
        return self.回應


def test_所有釘選欄位與結構逐字套用且輸入輸出皆脫離():
    供應商 = 記錄供應商()
    註冊表 = {"alpha": 供應商}
    設定 = _設定()
    請求 = _請求()
    原訊息 = [{"role": "user", "content": "原文"}]
    結果 = 建立模型轉接器(註冊表, 設定).產生回應(請求)

    assert 供應商.呼叫 == [{
        "model": "pinned-v7", "temperature": 1.25, "max_tokens": 321,
        "timeout_seconds": 17.5, "structured_output": True,
        "schema_retry_count": 1, "messages": 原訊息,
        "tools": [{"type": "function", "function": {"name": "lookup"}}],
        "response_schema": {"type": "object", "required": ["answer"]},
    }]
    assert 結果 == 模型回應快照(text="完成", finish_reason="stop", usage={"total_tokens": 9}, tool_calls=[])
    供應商.呼叫[0]["messages"][0]["content"] = "供應商竄改"
    供應商.回應.usage["total_tokens"] = 999
    assert 請求.messages == 原訊息
    assert 結果.usage == {"total_tokens": 9}


def test_只依快照選供應商且捕捉註冊表與設定後不受替換竄改影響():
    甲, 乙 = 記錄供應商(), 記錄供應商()
    註冊表 = {"alpha": 甲, "beta": 乙}
    設定 = _設定(provider="beta").轉成JSON物件()
    轉接器 = 建立模型轉接器(註冊表, 設定)
    註冊表["beta"] = 甲
    設定["model"] = "live-mutated"
    轉接器.產生回應(_請求())
    assert len(甲.呼叫) == 0
    assert len(乙.呼叫) == 1
    assert 乙.呼叫[0]["model"] == "pinned-v7"


def test_factory封存原始bound_method且不重讀provider屬性(monkeypatch):
    """factory 後即使 instance/class method 被替換，adapter 仍呼叫捕捉的原方法。"""
    供應商 = 記錄供應商()
    轉接器 = 建立模型轉接器({"alpha": 供應商}, _設定())

    def 替換(**參數):
        raise AssertionError("不可重讀 mutable provider method")

    monkeypatch.setattr(記錄供應商, "產生發布回應", 替換)
    供應商.產生發布回應 = 替換
    結果 = 轉接器.產生回應(_請求())
    assert 結果.text == "完成"
    assert len(供應商.呼叫) == 1


@pytest.mark.parametrize("覆寫", [
    {"temperature": True}, {"temperature": float("inf")}, {"temperature": 2.01},
    {"max_tokens": True}, {"max_tokens": 0}, {"timeout_seconds": 10**400},
    {"timeout_seconds": 901}, {"structured_output": 1}, {"schema_retry_count": 0},
])
def test_畸形或過大模型設定在lookup與呼叫前拒絕(覆寫):
    供應商 = 記錄供應商()
    with pytest.raises(模型設定錯誤, match="^發布模型設定不可用$") as 錯誤:
        _設定(**覆寫)
    assert 錯誤.value.code == "endpoint_misconfigured"
    assert 供應商.呼叫 == []


def test_持久JSON拒絕缺漏額外與secret鍵且不查provider():
    class 查詢陷阱(dict):
        def get(self, *參數):
            raise AssertionError("不可 lookup")

    基本 = _設定().轉成JSON物件()
    for 資料 in ({**基本, "api_key": "SECRET"}, {鍵: 值 for 鍵, 值 in 基本.items() if 鍵 != "model"}):
        with pytest.raises(模型設定錯誤, match="^發布模型設定不可用$"):
            建立模型轉接器(查詢陷阱(), 資料)


def test_unknown_provider固定設定錯誤且零呼叫():
    供應商 = 記錄供應商()
    with pytest.raises(模型設定錯誤) as 錯誤:
        建立模型轉接器({"alpha": 供應商}, _設定(provider="missing"))
    assert 錯誤.value.code == "endpoint_misconfigured"
    assert 供應商.呼叫 == []


def test_精確供應商逾時訊號轉成全新無鏈結typed_timeout():
    逾時 = 供應商逾時("SECRET")
    with pytest.raises(模型逾時錯誤, match="^模型供應商逾時$") as 錯誤:
        建立模型轉接器({"alpha": 記錄供應商(例外=逾時)}, _設定()).產生回應(_請求())
    assert 錯誤.value.code == "model_timeout"
    assert 錯誤.value.__cause__ is None and 錯誤.value.__context__ is None


def test_一般TimeoutError不是provider專用逾時訊號():
    with pytest.raises(模型轉接錯誤) as 錯誤:
        建立模型轉接器(
            {"alpha": 記錄供應商(例外=TimeoutError("SECRET"))}, _設定()
        ).產生回應(_請求())
    assert type(錯誤.value) is 模型轉接錯誤
    assert 錯誤.value.__cause__ is None and 錯誤.value.__context__ is None


def test_任意BaseException固定清理而控制流程原樣通過():
    class 惡意錯誤(BaseException):
        pass

    with pytest.raises(模型轉接錯誤, match="^模型供應商呼叫失敗$") as 錯誤:
        建立模型轉接器({"alpha": 記錄供應商(例外=惡意錯誤("SECRET"))}, _設定()).產生回應(_請求())
    assert 錯誤.value.code == "model_adapter_error"
    assert 錯誤.value.__context__ is None
    for 類型 in (KeyboardInterrupt, SystemExit, GeneratorExit):
        中斷 = 類型("MARKER")
        with pytest.raises(類型) as 捕捉:
            建立模型轉接器({"alpha": 記錄供應商(例外=中斷)}, _設定()).產生回應(_請求())
        assert 捕捉.value is 中斷


def test_structured_output關閉時schema必為None且偽造回應失敗關閉():
    供應商 = 記錄供應商()
    建立模型轉接器({"alpha": 供應商}, _設定(structured_output=False)).產生回應(
        模型轉接請求(messages=[], tools=[], response_schema=None)
    )
    assert 供應商.呼叫[0]["response_schema"] is None

    class 偽造(模型回應快照):
        pass

    惡意 = 記錄供應商(回應=偽造(text="x", finish_reason="stop", usage={}, tool_calls=[]))
    with pytest.raises(模型轉接錯誤):
        建立模型轉接器({"alpha": 惡意}, _設定()).產生回應(_請求())


def _assert_生產框架無標記(traceback, marker):
    def 含標記(值, 已看):
        if id(值) in 已看:
            return False
        已看.add(id(值))
        if type(值) is str:
            return marker in 值
        if type(值) is dict:
            return any(含標記(鍵, 已看) or 含標記(子值, 已看) for 鍵, 子值 in 值.items())
        if type(值) in (tuple, list, set, frozenset):
            return any(含標記(子值, 已看) for 子值 in 值)
        if isinstance(值, BaseException):
            return 含標記(值.args, 已看)
        for 類別 in type(值).__mro__:
            欄位們 = 類別.__dict__.get("__slots__", ())
            if type(欄位們) is str:
                欄位們 = (欄位們,)
            for 欄位 in 欄位們:
                try:
                    if 含標記(object.__getattribute__(值, 欄位), 已看):
                        return True
                except (AttributeError, TypeError):
                    pass
        return False

    while traceback is not None:
        框架 = traceback.tb_frame
        if 框架.f_globals.get("__name__", "").startswith("繁中代理.發布介面.執行期.模型"):
            assert not any(含標記(值, set()) for 值 in tuple(框架.f_locals.values())), 框架.f_code.co_name
        traceback = traceback.tb_next


@pytest.mark.parametrize("例外型別", [供應商逾時, KeyboardInterrupt, SystemExit, GeneratorExit])
def test_逾時與控制流程所有生產traceback框架皆清除marker(例外型別):
    marker = "FRAME/MODEL/SECRET"
    原例外 = 例外型別(marker)
    預期 = 模型逾時錯誤 if 例外型別 is 供應商逾時 else 例外型別
    with pytest.raises(預期) as 錯誤:
        建立模型轉接器(
            {"alpha": 記錄供應商(例外=原例外)}, _設定(model=marker)
        ).產生回應(模型轉接請求(
            messages=[{"role": "user", "content": marker}], tools=[],
            response_schema={"type": "object", "marker": marker},
        ))
    if 預期 is not 模型逾時錯誤:
        assert 錯誤.value is 原例外
    _assert_生產框架無標記(錯誤.tb, marker)


def test_DTO_repr不洩漏內容且extra_key與nested_subclass皆失敗關閉():
    marker = "REPR/SECRET"
    assert marker not in repr(_設定(model=marker))
    assert marker not in repr(模型轉接請求([{"content": marker}], [], None))
    with pytest.raises(模型設定錯誤):
        建立模型轉接器({"alpha": 記錄供應商()}, {**_設定().轉成JSON物件(), "extra": 1})

    class 惡意字串(str):
        pass

    with pytest.raises(模型轉接錯誤):
        模型轉接請求([{"content": 惡意字串(marker)}], [], None)


def test_公開轉接器所有直接建構路徑固定拒絕且不碰provider():
    class 陷阱:
        def __getattribute__(self, 名稱):
            raise AssertionError("不可讀取")

    for 動作 in (
        lambda: 模型轉接器(陷阱(), _設定()),
        lambda: 模型轉接器(供應商=陷阱(), 設定=_設定()),
    ):
        with pytest.raises(模型設定錯誤, match="^發布模型設定不可用$"):
            動作()
    assert "provider" not in str(inspect.signature(模型轉接器))
    assert isinstance(建立模型轉接器({"alpha": 記錄供應商()}, _設定()), 模型轉接器)


@pytest.mark.parametrize("類型", [模型轉接器, 轉接器模組._模型轉接器實作])
def test_object_new偽造公開或私有轉接器皆在讀取請求前固定拒絕(類型):
    偽造 = object.__new__(類型)
    for 欄位 in ("_設定", "_供應商", "_產生方法"):
        with pytest.raises(AttributeError):
            object.__setattr__(偽造, 欄位, object())
    with pytest.raises(模型轉接錯誤, match="^模型供應商呼叫失敗$") as 錯誤:
        偽造.產生回應(object())
    assert type(錯誤.value) is 模型轉接錯誤


def test_factory轉接器無資料屬性且無法竄改可信狀態():
    轉接器 = 建立模型轉接器({"alpha": 記錄供應商()}, _設定())
    for 欄位 in ("_設定", "_供應商", "_產生方法"):
        for 寫入 in (setattr, object.__setattr__):
            with pytest.raises(AttributeError):
                寫入(轉接器, 欄位, object())
    assert not hasattr(轉接器, "__dict__")


def test_subclass即使注入私有registry也因非精確型別拒絕():
    class 偽子類(轉接器模組._模型轉接器實作):
        pass

    正版 = 建立模型轉接器({"alpha": 記錄供應商()}, _設定())
    偽造 = object.__new__(偽子類)
    with 轉接器模組._轉接器狀態鎖:
        轉接器模組._轉接器狀態[偽造] = 轉接器模組._轉接器狀態[正版]
    with pytest.raises(模型轉接錯誤, match="^模型供應商呼叫失敗$"):
        偽造.產生回應(_請求())


def test_弱registry不留住轉接器且平行factory與呼叫不串線():
    gc.collect()
    起始數 = len(轉接器模組._轉接器狀態)
    暫存 = 建立模型轉接器({"alpha": 記錄供應商()}, _設定())
    弱參照 = weakref.ref(暫存)
    del 暫存
    gc.collect()
    assert 弱參照() is None and len(轉接器模組._轉接器狀態) == 起始數

    def 建立並呼叫(編號):
        供應商 = 記錄供應商()
        模型 = f"pinned-{編號}"
        結果 = 建立模型轉接器({"alpha": 供應商}, _設定(model=模型)).產生回應(_請求())
        return 結果.text, 供應商.呼叫[0]["model"]

    with ThreadPoolExecutor(max_workers=8) as 執行器:
        結果們 = list(執行器.map(建立並呼叫, range(32)))
    assert 結果們 == [("完成", f"pinned-{編號}") for 編號 in range(32)]


def test_設定請求與回應皆先捕捉所有slots再開始callback(monkeypatch):
    設定 = _設定()
    原判斷 = 契約模組._是短字串
    次數 = 0

    def 判斷並竄改(值):
        nonlocal 次數
        次數 += 1
        if 次數 == 1:
            object.__setattr__(設定, "model", "new-model")
        return 原判斷(值)

    monkeypatch.setattr(契約模組, "_是短字串", 判斷並竄改)
    assert 契約模組.重建設定(設定).model == "pinned-v7"
    monkeypatch.setattr(契約模組, "_是短字串", 原判斷)

    請求 = _請求()
    舊工具 = object.__getattribute__(請求, "tools")
    原複製 = 轉接器模組.複製JSON
    呼叫數 = 0

    def 複製並竄改(值, 上限):
        nonlocal 呼叫數
        呼叫數 += 1
        if 呼叫數 == 1:
            object.__setattr__(請求, "tools", [{"new": True}])
        return 原複製(值, 上限)

    monkeypatch.setattr(轉接器模組, "複製JSON", 複製並竄改)
    供應商 = 記錄供應商()
    建立模型轉接器({"alpha": 供應商}, _設定()).產生回應(請求)
    assert 供應商.呼叫[0]["tools"] == 舊工具

    回應 = 模型回應快照("x", "stop", {"old": 1}, [{"old": 2}])
    舊呼叫 = object.__getattribute__(回應, "tool_calls")
    呼叫數 = 0

    def 複製回應並竄改(值, 上限):
        nonlocal 呼叫數
        呼叫數 += 1
        if 呼叫數 == 1:
            object.__setattr__(回應, "tool_calls", [{"new": 3}])
        return 原複製(值, 上限)

    monkeypatch.setattr(轉接器模組, "複製JSON", 複製回應並竄改)
    assert 轉接器模組._重建回應(回應).tool_calls == 舊呼叫


def test_JSON深度節點與descriptor竄改皆失敗關閉(monkeypatch):
    太深 = None
    for _ in range(65):
        太深 = [太深]
    with pytest.raises(ValueError):
        契約模組.複製JSON(太深, 1_000_000)
    with pytest.raises(ValueError):
        契約模組.複製JSON([None] * 10_000, 1_000_000)

    根 = ["first", "last"]
    原序列化 = 契約模組.json.dumps

    def 序列化並替換(值, *參數, **命名參數):
        if 值 == "last":
            根[0] = "changed"
        return 原序列化(值, *參數, **命名參數)

    monkeypatch.setattr(契約模組.json, "dumps", 序列化並替換)
    with pytest.raises(ValueError):
        契約模組.複製JSON(根, 1000)


def test_JSON巨大字串在序列化配置前拒絕(monkeypatch):
    """字元數已超 budget 時不得呼叫 dumps 配置 multi-megabyte scalar。"""
    呼叫數 = 0

    def 不可序列化(*參數, **命名參數):
        nonlocal 呼叫數
        呼叫數 += 1
        raise AssertionError("超限字串不可傳給 dumps")

    monkeypatch.setattr(契約模組.json, "dumps", 不可序列化)
    with pytest.raises(ValueError):
        契約模組.複製JSON("甲" * 2_000_000, 1_000_000)
    assert 呼叫數 == 0


@pytest.mark.parametrize("值", ['"\\\n\x00', "甲😀"])
def test_JSON字串escape與UTF8精確邊界(值):
    """逐字成本與既有 canonical ensure_ascii=False bytes 完全相同。"""
    大小 = len(契約模組.json.dumps(值, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
    assert 契約模組.複製JSON(值, 大小) == 值
    with pytest.raises(ValueError):
        契約模組.複製JSON(值, 大小 - 1)


def test_JSON巢狀字串使用剩餘總budget且拒絕前不dumps該scalar(monkeypatch):
    原序列化 = 契約模組.json.dumps
    已序列化大值 = 0

    def 記錄序列化(值, *參數, **命名參數):
        nonlocal 已序列化大值
        if 值 == "x" * 100:
            已序列化大值 += 1
        return 原序列化(值, *參數, **命名參數)

    monkeypatch.setattr(契約模組.json, "dumps", 記錄序列化)
    with pytest.raises(ValueError):
        契約模組.複製JSON({"k": "x" * 100}, 106)
    assert 已序列化大值 == 0


def test_JSON重播先exact_type_check不呼叫惡意str_eq(monkeypatch):
    計數 = 0

    class 惡意字串(str):
        __hash__ = str.__hash__

        def __eq__(self, 其他):
            nonlocal 計數
            計數 += 1
            raise AssertionError

    根 = {"key": "last"}
    原序列化 = 契約模組.json.dumps

    def 序列化並替換(值, *參數, **命名參數):
        if 值 == "last":
            原值 = 根.pop("key")
            根[惡意字串("key")] = 原值
        return 原序列化(值, *參數, **命名參數)

    monkeypatch.setattr(契約模組.json, "dumps", 序列化並替換)
    with pytest.raises(ValueError):
        契約模組.複製JSON(根, 1000)
    assert 計數 == 0


def test_JSON先捕捉所有巢狀descriptor且最終序列化後再重播(monkeypatch):
    """第一個 dumps callback 前已封存 nested containers，最後 callback 竄改也會被抓到。"""
    內層 = ["old"]
    根 = {"nested": 內層, "trigger": "go"}
    原序列化 = 契約模組.json.dumps
    已竄改 = False

    def 序列化並竄改(值, *參數, **命名參數):
        nonlocal 已竄改
        結果 = 原序列化(值, *參數, **命名參數)
        if not 已竄改:
            已竄改 = True
            內層[0] = "new"
        return 結果

    monkeypatch.setattr(契約模組.json, "dumps", 序列化並竄改)
    with pytest.raises(ValueError):
        契約模組.複製JSON(根, 1000)


def test_轉成JSON先重建且偽造slot不會逸出identity():
    設定 = _設定()
    秘密 = object()
    object.__setattr__(設定, "model", 秘密)
    with pytest.raises(模型設定錯誤, match="^發布模型設定不可用$"):
        設定.轉成JSON物件()
