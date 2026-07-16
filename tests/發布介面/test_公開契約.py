"""發布介面共同嚴格 JSON 契約測試。"""

import math

import pytest

from 繁中代理.發布介面 import (
    嚴格JSON錯誤,
    建立正規JSON,
    解析嚴格JSON,
    計算正規JSON雜湊,
)


def test_解析嚴格JSON接受一般JSON值並保留陣列順序():
    """解析 strict JSON 後回傳標準 Python JSON value。"""
    資料 = 解析嚴格JSON('{"b":[2,1],"a":{"文字":"繁中","空":null}}')

    assert 資料 == {"b": [2, 1], "a": {"文字": "繁中", "空": None}}


@pytest.mark.parametrize(
    "原始文字",
    [
        '{"a":1,"a":2}',
        '{"outer":{"x":1,"x":2}}',
    ],
)
def test_解析嚴格JSON拒絕頂層與巢狀重複鍵(原始文字):
    """object key 在任何層級重複都不是公開契約允許的 JSON。"""
    with pytest.raises(嚴格JSON錯誤):
        解析嚴格JSON(原始文字)


@pytest.mark.parametrize("原始文字", ["NaN", "Infinity", "-Infinity", '{"x": NaN}'])
def test_解析嚴格JSON拒絕非有限數值(原始文字):
    """stdlib json 預設接受的 NaN/Infinity 必須被拒絕。"""
    with pytest.raises(嚴格JSON錯誤):
        解析嚴格JSON(原始文字)


def test_解析嚴格JSON拒絕語法錯誤且錯誤不含原始payload():
    """錯誤訊息不可回洩原始 payload。"""
    原始文字 = '{"secret":"不可出現在錯誤訊息",'

    with pytest.raises(嚴格JSON錯誤) as 錯誤:
        解析嚴格JSON(原始文字)

    assert 原始文字 not in str(錯誤.value)
    assert "不可出現在錯誤訊息" not in str(錯誤.value)


def test_解析嚴格JSON只接受字串輸入():
    """bytes 等非 str 輸入必須明確拒絕。"""
    with pytest.raises(嚴格JSON錯誤):
        解析嚴格JSON(b'{"a":1}')


def test_建立正規JSON輸出穩定排序無多餘空白且保留Unicode():
    """正規 JSON 必須穩定、精簡，且不把 Unicode escape 成 ASCII。"""
    資料 = {"b": [2, 1], "a": {"文字": "繁中", "布林": True, "空": None}}

    assert 建立正規JSON(資料) == '{"a":{"布林":true,"文字":"繁中","空":null},"b":[2,1]}'


def test_建立正規JSON不修改輸入資料():
    """canonical 建立過程不可改變呼叫端傳入的 dict/list。"""
    資料 = {"b": [{"z": 1, "a": 2}], "a": [3, 2, 1]}
    原本 = {"b": [{"z": 1, "a": 2}], "a": [3, 2, 1]}

    建立正規JSON(資料)

    assert 資料 == 原本


@pytest.mark.parametrize(
    "資料",
    [
        {1: "非字串鍵"},
        {"tuple": (1, 2)},
        {"set": {1, 2}},
        {"bytes": b"abc"},
        {"object": object()},
        {"nan": math.nan},
        {"inf": math.inf},
        {"neg_inf": -math.inf},
    ],
)
def test_建立正規JSON拒絕非JSON值(資料):
    """只接受 JSON value 型別與有限 float。"""
    with pytest.raises(嚴格JSON錯誤):
        建立正規JSON(資料)


def test_建立正規JSON允許bool且不被int判斷誤傷():
    """bool 是 int subclass，但在 JSON 契約中是合法布林值。"""
    assert 建立正規JSON({"否": False, "是": True}) == '{"否":false,"是":true}'


def test_正規JSON雜湊忽略dict順序與來源空白():
    """同一 JSON object 的插入順序與 parse 來源空白不影響 digest。"""
    第一份 = {"b": 2, "a": {"y": 1, "x": [True, None]}}
    第二份 = 解析嚴格JSON(' { "a" : { "x" : [ true , null ] , "y" : 1 } , "b" : 2 } ')

    assert 計算正規JSON雜湊(第一份) == 計算正規JSON雜湊(第二份)


def test_正規JSON雜湊保留陣列順序差異():
    """array order 是語意的一部分，順序不同 digest 必須不同。"""
    assert 計算正規JSON雜湊([1, 2, 3]) != 計算正規JSON雜湊([3, 2, 1])
