"""A21-02 五目標 observer-only 敏感偵測契約。"""

from dataclasses import asdict
import hmac
import inspect

import pytest

import 繁中代理.發布介面.呼叫.擷取政策 as 政策模組
import 繁中代理.發布介面.呼叫.敏感偵測 as 偵測模組
from 繁中代理.發布介面.嚴格JSON import 建立正規JSON
from 繁中代理.發布介面.呼叫.擷取政策 import (
    敏感旁路錯誤,
    目標敏感命中,
    擷取階段,
    準備含敏感偵測的呼叫擷取,
)


固定目標 = ("input", "metadata", "response_data", "tool_arguments", "tool_result")


def _安全標記們():
    email = "observer" + chr(64) + "safe.invalid"
    phone = "".join(("09", "12", "-", "345", "-", "678"))
    card = "4" + "1" * 15
    return email, phone, card


def _canonical_bytes(值):
    return 建立正規JSON(值).encode("utf-8")


def test_五目標只輸出位置_unicode_offset_RFC6901與固定排序():
    email, phone, card = _安全標記們()
    payload們 = {
        "input": {"z": "safe", "a/b~c": ["🙂" + email]},
        "metadata": {"non_strings": [None, True, 7, 1.5]},
        "response_data": {"phone": phone},
        "tool_arguments": {"nested": [{"card": card}]},
        "tool_result": {"mail": email},
    }

    結果 = 準備含敏感偵測的呼叫擷取(
        擷取階段.AUTHENTICATED,
        payload們["input"],
        payload們["metadata"],
        response_data=payload們["response_data"],
        tool_arguments=payload們["tool_arguments"],
        tool_result=payload們["tool_result"],
    )

    assert tuple(asdict(命中) for 命中 in 結果.命中們) == (
        {"目標代碼": "input", "類型代碼": "email", "JSON路徑": "/a~1b~0c/0", "開始": 1, "結束": 1 + len(email)},
        {"目標代碼": "response_data", "類型代碼": "phone", "JSON路徑": "/phone", "開始": 0, "結束": len(phone)},
        {"目標代碼": "tool_arguments", "類型代碼": "payment_card_candidate", "JSON路徑": "/nested/0/card", "開始": 0, "結束": len(card)},
        {"目標代碼": "tool_result", "類型代碼": "email", "JSON路徑": "/mail", "開始": 0, "結束": len(email)},
    )
    assert 目標敏感命中.__slots__ == ("目標代碼", "類型代碼", "JSON路徑", "開始", "結束")
    assert 結果.警告代碼們 == ("sensitive_data_detected",)


def test_五目標偵測前後獨立canonical_JSON_bytes完全相同且不提供修改介面():
    email, phone, card = _安全標記們()
    payload們 = (
        {"input": [email]},
        {"metadata": [phone]},
        {"response": [card]},
        {"arguments": {"mail": email}},
        {"result": {"phone": phone}},
    )
    偵測前 = tuple(_canonical_bytes(值) for 值 in payload們)

    準備含敏感偵測的呼叫擷取(
        擷取階段.AUTHENTICATED, payload們[0], payload們[1],
        response_data=payload們[2], tool_arguments=payload們[3], tool_result=payload們[4],
    )
    偵測後 = tuple(_canonical_bytes(值) for 值 in payload們)

    assert all(前 is not 後 for 前, 後 in zip(偵測前, 偵測後))
    assert all(hmac.compare_digest(前, 後) for 前, 後 in zip(偵測前, 偵測後))
    參數 = set(inspect.signature(準備含敏感偵測的呼叫擷取).parameters)
    assert 參數 == {"階段", "input", "metadata", "response_data", "tool_arguments", "tool_result"}
    assert not ({"redact", "redaction", "anonymize", "mutation", "writer", "repository"} & 參數)


@pytest.mark.parametrize("目標", 固定目標)
def test_五目標cycle_nonfinite與非exact_builtin皆fail_closed(目標):
    class 字典子類(dict):
        pass

    循環 = []
    循環.append(循環)
    for 壞值 in (循環, float("nan"), 字典子類()):
        payload們 = {名稱: {} for 名稱 in 固定目標}
        payload們[目標] = 壞值
        with pytest.raises(敏感旁路錯誤, match="敏感資料旁路建立失敗") as 資訊:
            準備含敏感偵測的呼叫擷取(
                擷取階段.AUTHENTICATED,
                payload們["input"], payload們["metadata"],
                response_data=payload們["response_data"],
                tool_arguments=payload們["tool_arguments"],
                tool_result=payload們["tool_result"],
            )
        assert 資訊.value.__cause__ is None and 資訊.value.__context__ is None


def test_detector_callback一般失敗固定拒絕且控制流程保留identity(monkeypatch):
    marker = "callback-" + chr(64) + "safe.invalid"
    次數 = [0]

    def 一般失敗(_值):
        次數[0] += 1
        if 次數[0] == 4:
            raise RuntimeError("safe detector failure")
        return ()

    monkeypatch.setattr(政策模組, "偵測敏感資料", 一般失敗)
    with pytest.raises(敏感旁路錯誤, match="敏感資料旁路建立失敗") as 資訊:
        準備含敏感偵測的呼叫擷取(
            擷取階段.AUTHENTICATED, {}, {}, response_data={},
            tool_arguments={"x": marker}, tool_result={},
        )
    assert 次數 == [4]
    assert 資訊.value.__cause__ is None and 資訊.value.__context__ is None

    控制流程 = KeyboardInterrupt("safe-control", 21)
    次數[0] = 0

    def 控制流程失敗(_值):
        次數[0] += 1
        if 次數[0] == 5:
            raise 控制流程
        return ()

    monkeypatch.setattr(政策模組, "偵測敏感資料", 控制流程失敗)
    with pytest.raises(KeyboardInterrupt) as 控制資訊:
        準備含敏感偵測的呼叫擷取(
            擷取階段.AUTHENTICATED, {}, {}, response_data={},
            tool_arguments={}, tool_result={"x": marker},
        )
    assert 次數 == [5]
    assert 控制資訊.value is 控制流程 and 控制資訊.value.args == ("safe-control", 21)


def test_hit_bound固定拒絕且不回傳部分結果(monkeypatch):
    email, _, _ = _安全標記們()
    monkeypatch.setattr(偵測模組, "_最大命中數", 1)
    with pytest.raises(敏感旁路錯誤, match="敏感資料旁路建立失敗"):
        準備含敏感偵測的呼叫擷取(
            擷取階段.AUTHENTICATED, {"a": email, "b": email}, None,
        )


def test_target_allowlist精確且DTO不含原值相關欄位():
    email, _, _ = _安全標記們()
    for 目標 in 固定目標:
        命中 = 目標敏感命中(目標, "email", "/x", 0, len(email))
        assert set(asdict(命中)) == {"目標代碼", "類型代碼", "JSON路徑", "開始", "結束"}
    with pytest.raises(敏感旁路錯誤, match="目標敏感命中格式無效"):
        目標敏感命中("tool_error", "email", "/x", 0, len(email))
