"""測試 Gemini 3 thought signature 的擷取、保存與回送。

Gemini 3 系列在回傳 function call 時附帶不透明的 thought signature，下一輪必須
原樣帶回，否則 Vertex 會以 400 `missing a thought_signature` 拒絕整個請求。
Gemini 2.5 不產生簽章，因此這些測試同時確認「沒有簽章時行為不變」。
"""

import json

from google.genai import types

from 繁中代理.模型供應商 import (
    GeminiADC供應商,
    思考簽章欄位,
    解碼思考簽章,
    編碼思考簽章,
)


class 假函數呼叫:
    """模擬 provider 回傳的 function_call。"""

    def __init__(self, name, args):
        """保存名稱與參數。"""
        self.name = name
        self.args = args


class 假零件:
    """模擬 provider 回應的一個 part。"""

    def __init__(self, function_call=None, text=None, thought_signature=None):
        """保存 function call、文字與簽章。"""
        self.function_call = function_call
        self.text = text
        self.thought_signature = thought_signature


class 假內容:
    """模擬候選的 content。"""

    def __init__(self, parts):
        """保存 parts。"""
        self.parts = parts


class 假候選:
    """模擬回應候選。"""

    def __init__(self, parts, finish_reason="STOP"):
        """保存 content 與結束原因。"""
        self.content = 假內容(parts)
        self.finish_reason = finish_reason


class 假回應:
    """模擬 provider 回應物件。"""

    def __init__(self, parts):
        """保存候選清單。"""
        self.candidates = [假候選(parts)]
        self.usage_metadata = None


def 建立供應商():
    """建立不會實際連線的供應商實例。"""
    return GeminiADC供應商(模型名稱="gemini-3.7-flash", 專案識別碼="p", 位置="global")


def test_編碼解碼來回一致():
    """確認簽章經過 base64 存取後與原始 bytes 相同。"""
    原始 = b"\x00\x01opaque\xff\xfe"

    assert 解碼思考簽章(編碼思考簽章(原始)) == 原始


def test_非bytes或壞資料一律回None():
    """確認不合預期的輸入不會炸掉，只是退回無簽章行為。"""
    assert 編碼思考簽章(None) is None
    assert 編碼思考簽章("已經是字串") is None
    assert 解碼思考簽章(None) is None
    assert 解碼思考簽章("") is None
    assert 解碼思考簽章("這不是合法的 base64!!!") is None


def test_解析回應時擷取簽章():
    """確認 Gemini 3 回傳的簽章被收進 tool_calls。"""
    簽章 = b"sig-abc"
    回應 = 假回應([假零件(function_call=假函數呼叫("todo", {"merge": True}), thought_signature=簽章)])

    結果 = 建立供應商().轉成模型回應(回應)

    呼叫 = 結果.工具呼叫清單[0]
    assert 呼叫["function"]["name"] == "todo"
    assert 解碼思考簽章(呼叫[思考簽章欄位]) == 簽章


def test_沒有簽章時不新增欄位():
    """確認 Gemini 2.5 的回應不會多出空欄位。"""
    回應 = 假回應([假零件(function_call=假函數呼叫("todo", {}), thought_signature=None)])

    呼叫 = 建立供應商().轉成模型回應(回應).工具呼叫清單[0]

    assert 思考簽章欄位 not in 呼叫


def test_組裝請求時把簽章帶回去():
    """核心迴歸：帶簽章的歷史訊息必須重建出帶 thought_signature 的 Part。"""
    簽章 = b"sig-xyz"
    訊息清單 = [
        {"role": "user", "content": "查一下"},
        {"role": "assistant", "content": "", "tool_calls": [{
            "id": "call_1", "type": "function",
            "function": {"name": "todo", "arguments": json.dumps({"merge": True})},
            思考簽章欄位: 編碼思考簽章(簽章),
        }]},
        {"role": "tool", "tool_call_id": "call_1", "name": "todo", "content": "{}"},
    ]

    內容清單 = 建立供應商().轉成Gemini內容(訊息清單)

    函數零件們 = [
        零件 for 內容 in 內容清單 for 零件 in (內容.parts or [])
        if getattr(零件, "function_call", None) is not None
    ]
    assert len(函數零件們) == 1
    assert 函數零件們[0].thought_signature == 簽章


def test_沒有簽章的歷史仍可組裝():
    """確認 Gemini 2.5 的舊對話（無簽章）不受影響。"""
    訊息清單 = [
        {"role": "user", "content": "查一下"},
        {"role": "assistant", "content": "", "tool_calls": [{
            "id": "call_1", "type": "function",
            "function": {"name": "todo", "arguments": "{}"},
        }]},
        {"role": "tool", "tool_call_id": "call_1", "name": "todo", "content": "{}"},
    ]

    內容清單 = 建立供應商().轉成Gemini內容(訊息清單)

    函數零件們 = [
        零件 for 內容 in 內容清單 for 零件 in (內容.parts or [])
        if getattr(零件, "function_call", None) is not None
    ]
    assert len(函數零件們) == 1
    assert 函數零件們[0].thought_signature is None
    assert isinstance(函數零件們[0], types.Part)
