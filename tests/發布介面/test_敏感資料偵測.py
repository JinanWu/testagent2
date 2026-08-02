"""LOG L05 有界 regex 敏感資料偵測與安全位置 DTO 測試。"""

from dataclasses import asdict
import inspect

import pytest

import 繁中代理.發布介面.呼叫.敏感偵測 as 偵測模組
import 繁中代理.發布介面.呼叫.擷取政策 as 政策模組
from 繁中代理.發布介面.呼叫.敏感偵測 import (
    敏感偵測錯誤,
    敏感命中,
    偵測敏感資料,
)
from 繁中代理.發布介面.呼叫.擷取政策 import (
    敏感旁路錯誤, 目標敏感命中, 敏感偵測擷取結果, 擷取階段,
    準備含敏感偵測的呼叫擷取,
)


def _形狀(命中們):
    return tuple((項目.JSON路徑, 項目.開始, 項目.結束, 項目.類型代碼) for 項目 in 命中們)


def _含標記(值, 標記, 已見):
    if id(值) in 已見:
        return False
    已見.add(id(值))
    if type(值) is str:
        return 標記 in 值
    if type(值) in (tuple, list, set, frozenset):
        return any(_含標記(項目, 標記, 已見) for 項目 in 值)
    if type(值) is dict:
        return any(_含標記(鍵, 標記, 已見) or _含標記(項目, 標記, 已見)
                   for 鍵, 項目 in dict.items(值))
    if isinstance(值, BaseException):
        return _含標記(object.__getattribute__(值, "args"), 標記, 已見)
    return False


def _斷言模組框架無標記(資訊, 標記):
    框架們 = [項目.frame for 項目 in 資訊.traceback
           if str(項目.frame.code.path) == 偵測模組.__file__]
    assert 框架們
    for 框架 in 框架們:
        for 值 in 框架.f_locals.values():
            assert not _含標記(值, 標記, set()), 框架.f_code.co_name


def _斷言旁路框架無標記(資訊, 標記):
    路徑們 = {偵測模組.__file__, 政策模組.__file__}
    框架們 = [項目.frame for 項目 in 資訊.traceback
           if str(項目.frame.code.path) in 路徑們]
    assert 框架們
    for 框架 in 框架們:
        for 值 in 框架.f_locals.values():
            assert not _含標記(值, 標記, set()), 框架.f_code.co_name


def test_巢狀路徑跳脫精確unicode位移與固定排序():
    文字 = "前綴🙂 mail=a@example.com，電話 0912-345-678"
    結果 = 偵測敏感資料({"a/b~c": [文字], "z": "A123456789"})
    assert isinstance(結果, tuple)
    assert _形狀(結果) == (
        ("/a~1b~0c/0", 9, 22, "email"),
        ("/a~1b~0c/0", 26, 38, "phone"),
        ("/z", 0, 10, "tw_national_id_format"),
    )
    assert 文字[結果[0].開始:結果[0].結束] == "a@example.com"


def test_卡號luhn憑證指定重疊政策與無命中():
    結果 = 偵測敏感資料({"items": [
        "card=4111 1111 1111 1111 bad=4111 1111 1111 1112",
        'api_key = "token_ABC1234567" and x@example.org',
        "password=4111111111111111",
        "ordinary text",
    ]})
    assert _形狀(結果) == (
        ("/items/0", 5, 24, "payment_card_candidate"),
        ("/items/1", 11, 27, "credential_assignment"),
        ("/items/1", 33, 46, "email"),
        ("/items/2", 9, 25, "credential_assignment"),
    )
    assert 偵測敏感資料({"ok": True, "n": 7, "none": None}) == ()


def test_DTO只含固定位置純值且不可變不洩漏原文():
    原文 = "marker-secret@example.com"
    命中 = 偵測敏感資料(原文)[0]
    assert 敏感命中.__slots__ == ("類型代碼", "JSON路徑", "開始", "結束")
    assert asdict(命中) == {
        "類型代碼": "email", "JSON路徑": "", "開始": 0, "結束": len(原文),
    }
    assert 原文 not in repr(命中) and not hasattr(命中, "值")
    with pytest.raises((AttributeError, TypeError)):
        命中.開始 = 1


@pytest.mark.parametrize("欄位們", [("other", "", 0, 1), ("email", "x", 0, 1),
    ("email", "/bad~2", 0, 1),
    ("email", "", True, 1), ("email", "", 1, 1), ("email", "", 0, 4097),
    (object(), "", 0, 1), ("email", [], 0, 1), ("email", "", 0, {}),
])
def test_DTO公開建構子固定拒絕所有錯誤槽位(欄位們):
    with pytest.raises(敏感偵測錯誤, match="敏感命中格式無效") as 資訊:
        敏感命中(*欄位們)
    assert 資訊.value.__cause__ is None and 資訊.value.__context__ is None


def test_DTO精確類別且遭竄改後消費會拒絕():
    class 子命中(敏感命中):
        pass
    with pytest.raises(敏感偵測錯誤):
        子命中("email", "", 0, 1)
    命中 = 敏感命中("email", "", 0, 1)
    object.__setattr__(命中, "JSON路徑", "/bad~")
    with pytest.raises(ValueError):
        偵測模組._命中排序鍵(命中)


def test_精確內建型別且敵意子類零呼叫():
    次數 = [0]

    class 敵意字典(dict):
        def items(self):
            次數[0] += 1
            raise AssertionError("不得呼叫")

    class 敵意字串(str):
        def encode(self, *_參數, **_關鍵字):
            次數[0] += 1
            raise AssertionError("不得呼叫")

    for 壞值 in (敵意字典(a="x"), 敵意字串("a@example.com")):
        with pytest.raises(敏感偵測錯誤, match="敏感資料偵測失敗"):
            偵測敏感資料(壞值)
    assert 次數 == [0]


@pytest.mark.parametrize("壞值", [[], 1, True, 1.5, None])
def test_top_level只接受精確字串或字典且traversal零呼叫(monkeypatch, 壞值):
    次數 = [0]
    monkeypatch.setattr(偵測模組, "_走訪JSON", lambda *_: 次數.__setitem__(0, 1))
    with pytest.raises(敏感偵測錯誤):
        偵測敏感資料(壞值)
    assert 次數 == [0]


@pytest.mark.parametrize("壞值", [float("nan"), float("inf"), {1: "x"}])
def test_非有限與非字串鍵固定拒絕(壞值):
    with pytest.raises(敏感偵測錯誤) as 資訊:
        偵測敏感資料(壞值)
    assert 資訊.value.__cause__ is None and 資訊.value.__context__ is None


def test_cycle深度寬度字串與總量限制():
    循環 = []
    循環.append(循環)
    過深 = "x"
    for _ in range(9):
        過深 = [過深]
    壞值們 = [{"x": 循環}, {"x": 過深}, {str(i): i for i in range(129)}, "密" * 4097,
           {"x": ["x" * 4096 for _ in range(9)]}]
    for 壞值 in 壞值們:
        with pytest.raises(敏感偵測錯誤, match="敏感資料偵測失敗"):
            偵測敏感資料(壞值)


def test_遍歷期間替換值會fail_closed且不改輸入(monkeypatch):
    payload = {"a": "a@example.com", "b": "safe"}
    原掃描 = 偵測模組._掃描字串
    已改 = [False]

    def 突變掃描(文字, 路徑, 命中們):
        if not 已改[0]:
            已改[0] = True
            payload["b"] = "b@example.com"
        return 原掃描(文字, 路徑, 命中們)

    monkeypatch.setattr(偵測模組, "_掃描字串", 突變掃描)
    with pytest.raises(敏感偵測錯誤):
        偵測敏感資料(payload)
    assert payload == {"a": "a@example.com", "b": "b@example.com"}


def test_list最後子項掃描期間append也會fail_closed(monkeypatch):
    清單 = ["safe", "last"]
    原掃描 = 偵測模組._掃描字串
    def 突變掃描(文字, 路徑, 命中們):
        if 路徑 == "/items/1":
            清單.append("late@example.com")
        return 原掃描(文字, 路徑, 命中們)
    monkeypatch.setattr(偵測模組, "_掃描字串", 突變掃描)
    with pytest.raises(敏感偵測錯誤):
        偵測敏感資料({"items": 清單})
    assert 清單[-1] == "late@example.com"


def test_dict最後子項掃描替換先前值會fail_closed且不回傳部分命中(monkeypatch):
    payload = {"a": "old@example.com", "z": "last"}
    原掃描 = 偵測模組._掃描字串
    def 突變掃描(文字, 路徑, 命中們):
        if 路徑 == "/z":
            payload["a"] = "new@example.com"
        return 原掃描(文字, 路徑, 命中們)
    monkeypatch.setattr(偵測模組, "_掃描字串", 突變掃描)
    with pytest.raises(敏感偵測錯誤):
        偵測敏感資料(payload)
    assert payload == {"a": "new@example.com", "z": "last"}


def test_dict最後子項掃描刪除重插先前鍵改序會fail_closed(monkeypatch):
    payload = {"a": "old@example.com", "z": "last"}
    原掃描 = 偵測模組._掃描字串
    def 突變掃描(文字, 路徑, 命中們):
        if 路徑 == "/z":
            payload["a"] = payload.pop("a")
        return 原掃描(文字, 路徑, 命中們)
    monkeypatch.setattr(偵測模組, "_掃描字串", 突變掃描)
    with pytest.raises(敏感偵測錯誤):
        偵測敏感資料(payload)
    assert tuple(payload) == ("z", "a")


def test_dict最終重驗先拒絕敵意字串子類鍵且零比較(monkeypatch):
    比較次數 = [0]
    class 敵意鍵(str):
        __hash__ = str.__hash__
        def __eq__(self, _其他):
            比較次數[0] += 1
            raise AssertionError("不得比較")
    payload = {"a": "safe", "z": "last"}
    原掃描 = 偵測模組._掃描字串
    def 突變掃描(文字, 路徑, 命中們):
        if 路徑 == "/z":
            先前項目 = payload.pop("a")
            最後項目 = payload.pop("z")
            payload[敵意鍵("a")] = 先前項目
            payload["z"] = 最後項目
            比較次數[0] = 0
        return 原掃描(文字, 路徑, 命中們)
    monkeypatch.setattr(偵測模組, "_掃描字串", 突變掃描)
    with pytest.raises(敏感偵測錯誤):
        偵測敏感資料(payload)
    assert 比較次數 == [0]


class _自訂基礎錯誤(BaseException):
    """供非控制流程 BaseException 正規化測試。"""


def test_自訂BaseException固定且錯誤不保留來源(monkeypatch):
    標記 = "raw-marker@example.com"
    monkeypatch.setattr(偵測模組, "_找出字串命中",
                lambda *_參數: (_ for _ in ()).throw(_自訂基礎錯誤(標記)))
    with pytest.raises(敏感偵測錯誤, match="敏感資料偵測失敗") as 資訊:
        偵測敏感資料({"secret/path": 標記})
    assert 資訊.value.__cause__ is None and 資訊.value.__context__ is None
    assert 標記 not in repr(資訊.value)
    _斷言模組框架無標記(資訊, 標記)


@pytest.mark.parametrize("錯誤類型", [KeyboardInterrupt, SystemExit, GeneratorExit])
def test_控制流程identity_args且遞迴框架清除來源(monkeypatch, 錯誤類型):
    標記 = "control-marker@example.com"
    錯誤 = 錯誤類型(標記, 17)
    monkeypatch.setattr(偵測模組, "_找出字串命中",
                lambda *_參數: (_ for _ in ()).throw(錯誤))
    with pytest.raises(錯誤類型) as 資訊:
        偵測敏感資料({"secret/path": [標記]})
    assert 資訊.value is 錯誤 and 資訊.value.args == (標記, 17)
    _斷言模組框架無標記(資訊, 標記)


@pytest.mark.parametrize("邊界", ["DTO", "Luhn", "排序", "重疊"])
@pytest.mark.parametrize("錯誤類型", [_自訂基礎錯誤, KeyboardInterrupt, SystemExit, GeneratorExit])
def test_巢狀helper自訂與控制流程皆清除traceback(monkeypatch, 邊界, 錯誤類型):
    標記 = f"{邊界}-nested-marker@example.com"
    錯誤 = 錯誤類型(標記, 23)
    def 投擲(*_參數):
        raise 錯誤
    if 邊界 == "DTO":
        monkeypatch.setattr(偵測模組, "_是canonical_JSON路徑", 投擲)
        呼叫 = lambda: 敏感命中("email", "/x", 0, 1)
    elif 邊界 == "Luhn":
        monkeypatch.setattr(偵測模組, "ord", 投擲, raising=False)
        呼叫 = lambda: 偵測敏感資料({"x": "4111111111111111"})
    else:
        次數 = [0]
        原驗證 = 偵測模組._驗證敏感命中
        def 延後投擲(命中):
            次數[0] += 1
            if 次數[0] == (1 if 邊界 == "排序" else 2):
                投擲()
            return 原驗證(命中)
        monkeypatch.setattr(偵測模組, "_驗證敏感命中", 延後投擲)
        呼叫 = lambda: 偵測敏感資料({"x": "x@example.com"})
    預期 = 敏感偵測錯誤 if 錯誤類型 is _自訂基礎錯誤 else 錯誤類型
    with pytest.raises(預期) as 資訊:
        呼叫()
    if 錯誤類型 is not _自訂基礎錯誤:
        assert 資訊.value is 錯誤 and 資訊.value.args == (標記, 23)
    _斷言模組框架無標記(資訊, 標記)


def test_偵測器無整合供應商提示輸出或修改介面():
    參數 = set(inspect.signature(偵測敏感資料).parameters)
    assert 參數 == {"輸入"}
    assert not ({"provider", "prompt", "output", "audit", "mutation"} & 參數)


def test_L06從canonical脫離值偵測且不修改來源並可重複():
    input值 = {"z": ["safe"], "a": "input@example.com"}
    metadata = {"contact": "meta@example.com"}
    response = {"phone": "0912-345-678"}
    原形狀 = (repr(input值), repr(metadata), repr(response))
    結果 = 準備含敏感偵測的呼叫擷取(
        擷取階段.AUTHENTICATED, input值, metadata, response_data=response,
    )
    canonical們 = (結果.命令.input_json, 結果.命令.metadata_json)
    assert tuple((x.目標代碼, x.JSON路徑, x.類型代碼) for x in 結果.命中們) == (
        ("input", "/a", "email"), ("metadata", "/contact", "email"),
        ("response_data", "/phone", "phone"),
    )
    assert 結果.警告代碼們 == ("sensitive_data_detected",)
    assert (repr(input值), repr(metadata), repr(response)) == 原形狀
    assert (結果.命令.input_json, 結果.命令.metadata_json) == canonical們
    assert 結果 == 準備含敏感偵測的呼叫擷取(
        擷取階段.AUTHENTICATED, input值, metadata, response_data=response,
    )


@pytest.mark.parametrize("階段", [擷取階段.PRE_CREDENTIAL_REJECTION, 擷取階段.INVALID_API_KEY])
def test_L06憑證前不偵測raw_metadata且input仍偵測(階段):
    結果 = 準備含敏感偵測的呼叫擷取(
        階段, {"mail": "input@example.com"}, {"mail": "metadata@example.com"},
    )
    assert tuple(x.目標代碼 for x in 結果.命中們) == ("input",)
    assert 結果.命令.metadata_json is None
    assert "metadata@example.com" not in repr(結果)


def test_L06_slug_miss不讀任何payload或response():
    class 敵意(dict):
        def items(self):
            raise AssertionError("不得讀取")
    assert 準備含敏感偵測的呼叫擷取(
        擷取階段.SLUG_MISS, 敵意(), 敵意(), response_data=敵意(),
    ) is None


def test_L06_response只允許驗證後且無命中不產生警告():
    with pytest.raises(敏感旁路錯誤, match="敏感資料旁路建立失敗"):
        準備含敏感偵測的呼叫擷取(
            擷取階段.INVALID_API_KEY, {}, None, response_data={"x": "safe"},
        )
    結果 = 準備含敏感偵測的呼叫擷取(擷取階段.AUTHENTICATED, {}, None)
    assert 結果.命中們 == () and 結果.警告代碼們 == ()


def test_L06_DTO固定槽位並重建命令與命中避免共享竄改():
    結果 = 準備含敏感偵測的呼叫擷取(
        擷取階段.AUTHENTICATED, {"mail": "x@example.com"}, None,
    )
    原命令 = 結果.命令
    重建 = 敏感偵測擷取結果(原命令, 結果.命中們, 結果.警告代碼們)
    assert 重建.命令 is not 原命令 and 重建.命中們[0] is not 結果.命中們[0]
    object.__setattr__(原命令, "input_json", "{}")
    object.__setattr__(結果.命中們[0], "JSON路徑", "/forged")
    assert 重建.命令.input_json != "{}" and 重建.命中們[0].JSON路徑 == "/mail"
    assert 目標敏感命中.__slots__ == ("目標代碼", "類型代碼", "JSON路徑", "開始", "結束")
    assert 敏感偵測擷取結果.__slots__ == ("命令", "命中們", "警告代碼們")


def test_L06偵測失敗固定無鏈且不回傳部分旁路(monkeypatch):
    呼叫次數 = [0]
    def 失敗(_值):
        呼叫次數[0] += 1
        if 呼叫次數[0] == 2:
            raise _自訂基礎錯誤("metadata-marker@example.com")
        return ()
    monkeypatch.setattr(政策模組, "偵測敏感資料", 失敗)
    with pytest.raises(敏感旁路錯誤, match="敏感資料旁路建立失敗") as 資訊:
        準備含敏感偵測的呼叫擷取(
            擷取階段.AUTHENTICATED, {}, {"x": "metadata-marker@example.com"},
        )
    assert 呼叫次數 == [2]
    assert 資訊.value.__cause__ is None and 資訊.value.__context__ is None


@pytest.mark.parametrize("錯誤類型", [KeyboardInterrupt, SystemExit, GeneratorExit])
def test_L06控制流程identity_args且所有旁路框架清除來源(monkeypatch, 錯誤類型):
    標記 = "l06-control-marker@example.com"
    錯誤 = 錯誤類型(標記, 31)
    monkeypatch.setattr(政策模組, "偵測敏感資料",
                lambda _值: (_ for _ in ()).throw(錯誤))
    with pytest.raises(錯誤類型) as 資訊:
        準備含敏感偵測的呼叫擷取(擷取階段.AUTHENTICATED, {"x": 標記}, None)
    assert 資訊.value is 錯誤 and 資訊.value.args == (標記, 31)
    _斷言旁路框架無標記(資訊, 標記)


def test_L06公開API沒有儲存稽核供應商提示或schema_handle(monkeypatch):
    寫入次數 = [0]
    monkeypatch.setattr(政策模組, "寫入呼叫擷取",
                lambda *_a, **_k: 寫入次數.__setitem__(0, 1))
    準備含敏感偵測的呼叫擷取(擷取階段.AUTHENTICATED, {}, None)
    參數 = set(inspect.signature(準備含敏感偵測的呼叫擷取).parameters)
    assert 參數 == {"階段", "input", "metadata", "response_data"}
    assert not ({"repository", "audit", "provider", "prompt", "schema_validator"} & 參數)
    assert not any("audit" in 名稱.lower() for 名稱 in vars(政策模組))
    assert 寫入次數 == [0]
