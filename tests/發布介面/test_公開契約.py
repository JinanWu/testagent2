"""發布介面共同公開契約測試。"""

from dataclasses import FrozenInstanceError
import math
import traceback

import pytest

from 繁中代理.發布介面 import (
    嚴格JSON錯誤,
    建立正規JSON,
    解析嚴格JSON,
    計算正規JSON雜湊,
)
from 繁中代理.發布介面.領域模型 import EndpointRef
from 繁中代理.發布介面.領域模型 import InvocationRef
from 繁中代理.發布介面.領域模型 import PublishedError
from 繁中代理.發布介面.領域模型 import PublishedUsage
from 繁中代理.發布介面.領域模型 import PublishedWarning
from 繁中代理.發布介面.領域模型 import ServiceAccountSnapshotRef


解析錯誤唯一SECRET_MARKER = "唯一SECRET_MARKER_解析_不外洩"
深層錯誤唯一SECRET_MARKER = "唯一SECRET_MARKER_深層_不外洩"


def _錯誤狀態不含marker(錯誤, marker):
    assert marker not in str(錯誤)
    assert marker not in repr(錯誤)
    assert 錯誤.__cause__ is None
    assert 錯誤.__context__ is None
    for frame_summary in traceback.extract_tb(錯誤.__traceback__):
        assert marker not in repr(frame_summary)
    traceback物件 = 錯誤.__traceback__
    while traceback物件 is not None:
        for 區域值 in traceback物件.tb_frame.f_locals.values():
            assert marker not in repr(區域值)
        traceback物件 = traceback物件.tb_next


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


def test_解析嚴格JSON語法錯誤不保留payload於例外鏈與traceback_locals():
    """public error 物件、例外鏈與 traceback locals 都不可保留 raw payload。"""
    原始文字 = f'{{"secret":"{解析錯誤唯一SECRET_MARKER}",'

    with pytest.raises(嚴格JSON錯誤) as 錯誤:
        解析嚴格JSON(原始文字)

    原始文字 = None
    _錯誤狀態不含marker(錯誤.value, 解析錯誤唯一SECRET_MARKER)


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


def test_建立正規JSON拒絕self_referential_list且不保留例外鏈():
    """cyclic list 必須轉成公開嚴格 JSON 錯誤而非 RecursionError。"""
    資料 = []
    資料.append(資料)

    with pytest.raises(嚴格JSON錯誤) as 錯誤:
        建立正規JSON(資料)

    assert 錯誤.value.__cause__ is None
    assert 錯誤.value.__context__ is None


def test_建立正規JSON拒絕self_referential_dict且不保留例外鏈():
    """cyclic dict 必須轉成公開嚴格 JSON 錯誤而非 RecursionError。"""
    資料 = {}
    資料["self"] = 資料

    with pytest.raises(嚴格JSON錯誤) as 錯誤:
        建立正規JSON(資料)

    assert 錯誤.value.__cause__ is None
    assert 錯誤.value.__context__ is None


def test_建立正規JSON過深nested_list轉公開錯誤且不洩漏marker():
    """實際 Python recursion overflow 必須轉成 sanitized public error。"""
    資料 = 深層錯誤唯一SECRET_MARKER
    for _ in range(2000):
        資料 = [資料]

    with pytest.raises(嚴格JSON錯誤) as 錯誤:
        建立正規JSON(資料)

    資料 = None
    _錯誤狀態不含marker(錯誤.value, 深層錯誤唯一SECRET_MARKER)


def test_建立正規JSON允許不同keys共享同一child_list且無cycle():
    """cycle detection 只追蹤目前路徑，不能把共享 child 當成 cycle。"""
    child = [1, {"ok": True}]
    資料 = {"a": child, "b": child}

    assert 建立正規JSON(資料) == '{"a":[1,{"ok":true}],"b":[1,{"ok":true}]}'


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


@pytest.mark.parametrize(
    "dto",
    [
        EndpointRef("ep_1", "hello", 3),
        InvocationRef("inv_1", "req_1"),
        PublishedUsage(7),
        PublishedWarning("notice", "metadata omitted"),
        PublishedError("endpoint_not_found", "not found"),
        ServiceAccountSnapshotRef("sa_1", "ver_1", "digest"),
    ],
)
def test_公開DTO全部凍結(dto):
    """公開 DTO 都是 frozen dataclass。"""
    欄位名稱 = next(iter(dto.to_json()))

    with pytest.raises(FrozenInstanceError):
        setattr(dto, 欄位名稱, "changed")


def test_endpoint_ref_to_json_exact_fields():
    """EndpointRef 輸出固定欄位與順序。"""
    輸出 = EndpointRef("ep_1", "hello", 3).to_json()

    assert list(輸出) == ["id", "slug", "version"]
    assert 輸出 == {"id": "ep_1", "slug": "hello", "version": 3}


def test_invocation_ref_to_json_exact_fields且session_nullable():
    """InvocationRef 輸出固定欄位，session_id 可為 None。"""
    無session輸出 = InvocationRef("inv_1", "req_1").to_json()
    有session輸出 = InvocationRef("inv_2", "req_2", "session_1").to_json()

    assert list(無session輸出) == ["id", "request_id", "session_id"]
    assert 無session輸出 == {"id": "inv_1", "request_id": "req_1", "session_id": None}
    assert 有session輸出 == {"id": "inv_2", "request_id": "req_2", "session_id": "session_1"}


def test_published_usage_to_json_exact_fields且tokens_nullable():
    """PublishedUsage 輸出固定欄位，total_tokens 可為 None。"""
    未知用量輸出 = PublishedUsage().to_json()
    已知用量輸出 = PublishedUsage(7).to_json()

    assert list(未知用量輸出) == ["total_tokens"]
    assert 未知用量輸出 == {"total_tokens": None}
    assert 已知用量輸出 == {"total_tokens": 7}


def test_published_warning_to_json_exact_fields():
    """PublishedWarning 輸出固定欄位與順序。"""
    輸出 = PublishedWarning("notice", "metadata omitted").to_json()

    assert list(輸出) == ["code", "message"]
    assert 輸出 == {"code": "notice", "message": "metadata omitted"}


def test_published_error_to_json_exact_fields():
    """PublishedError 輸出固定欄位與順序。"""
    輸出 = PublishedError("endpoint_not_found", "not found").to_json()

    assert list(輸出) == ["code", "message"]
    assert 輸出 == {"code": "endpoint_not_found", "message": "not found"}


def test_service_account_snapshot_ref_to_json_exact_fields且不帶runtime_context():
    """ServiceAccountSnapshotRef 只公開參照欄位，不暴露 runtime context。"""
    參考 = ServiceAccountSnapshotRef("sa_1", "ver_1", "digest")
    輸出 = 參考.to_json()
    禁止欄位 = {
        "owner",
        "memory",
        "session",
        "global_skill",
        "workdir",
        "provider_secret",
        "provider_secrets",
    }

    assert list(輸出) == [
        "service_account_id",
        "endpoint_version_id",
        "permission_snapshot_digest",
    ]
    assert 輸出 == {
        "service_account_id": "sa_1",
        "endpoint_version_id": "ver_1",
        "permission_snapshot_digest": "digest",
    }
    assert 禁止欄位.isdisjoint(輸出)
    for 欄位 in 禁止欄位:
        assert 欄位 not in repr(參考)


def test_to_json回傳新dict且修改輸出不影響DTO():
    """共用 DTO to_json 使用新 dict，避免呼叫端修改輸出影響 frozen 實例。"""
    參考 = EndpointRef("ep_1", "hello", 3)
    輸出 = 參考.to_json()

    輸出["slug"] = "changed"

    assert 參考.slug == "hello"
    assert 參考.to_json() == {"id": "ep_1", "slug": "hello", "version": 3}
