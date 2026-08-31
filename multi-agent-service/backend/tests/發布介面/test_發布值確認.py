"""PUB P03 發布 slug、回應結構、文件與固定窗限流值確認。"""

import pytest

from 繁中代理.發布介面.規劃.服務 import 發布規劃服務
from 繁中代理.發布介面.規劃.權限協調 import 權限協調器
from 繁中代理.發布介面.規劃.綱要 import (
    發布值確認,
    建議憑證每窗請求上限,
    建議端點每窗請求上限,
    規劃服務,
    草稿存取錯誤,
)


def _建立():
    服務 = 規劃服務(存續秒數=60, 識別碼產生器=lambda: "draft-p03")
    草稿 = 服務.建立草稿("owner", "原始需求不可改", {"step": 1}, 現在=100)
    return 服務, 草稿


def _確認(服務, **覆寫):
    引數 = {
        "slug": "customer-support-v1",
        "response_schema": {
            "type": "object",
            "properties": {"answer": {"type": "string"}},
            "required": ["answer"],
            "additionalProperties": False,
        },
        "docs": "輸入問題並取得結構化答案。",
        "endpoint_limit": 60,
        "credential_limit": 30,
        "現在": 120,
    }
    引數.update(覆寫)
    return 服務.確認發布值("owner", "draft-p03", **引數)


def test_確認值精確綁定草稿且回應結構只回傳獨立副本():
    服務, 草稿 = _建立()
    結構 = {"type": "string"}

    確認 = _確認(服務, response_schema=結構)
    結構["type"] = "number"
    讀取結構 = 確認.response_schema
    讀取結構["type"] = "boolean"

    assert 確認.草稿識別碼 == 草稿.草稿識別碼
    assert 確認.草稿世代 == 0
    assert 確認.slug == "customer-support-v1"
    assert 確認.response_schema == {"type": "string"}
    assert 確認.docs == "輸入問題並取得結構化答案。"
    assert (確認.endpoint_limit, 確認.credential_limit, 確認.window_seconds) == (60, 30, 60)
    assert (建議端點每窗請求上限, 建議憑證每窗請求上限) == (60, 30)
    assert not hasattr(確認, "system_prompt")
    assert 服務.讀取已確認草稿("owner", "draft-p03", 現在=159).發布確認 == 確認


def test_結構化物件結構通過meta_schema且可重新確認取代():
    服務, _ = _建立()
    第一版 = _確認(服務)
    第二版 = _確認(
        服務,
        response_schema={"type": "object", "properties": {}, "additionalProperties": False},
        docs="第二版文件",
        endpoint_limit=1,
        credential_limit=10_000,
    )

    assert 第一版 is not 第二版
    assert 服務.讀取已確認草稿("owner", "draft-p03", 現在=121).發布確認 == 第二版


def test_確認與重新確認保留第二世代且直接與wrapper皆可讀取():
    服務, _ = _建立()
    服務.更新草稿("owner", "draft-p03", {"step": 2}, 現在=110)
    第二世代 = 服務.更新草稿("owner", "draft-p03", {"step": 3}, 現在=111)
    wrapper = 發布規劃服務(權限協調器(object()), 草稿服務=服務)

    第一版 = _確認(服務)
    直接讀取 = 服務.讀取已確認草稿("owner", "draft-p03", 現在=121)
    assert 直接讀取._世代 == 第一版.草稿世代 == 第二世代._世代 == 2

    第二版 = _確認(服務, docs="第二版文件")
    wrapper讀取 = wrapper.讀取已確認草稿("owner", "draft-p03", 現在=122)
    assert wrapper讀取._世代 == 第二版.草稿世代 == 2


@pytest.mark.parametrize(
    "結構",
    [
        {"type": "array", "items": {"type": "number"}, "minItems": 1},
        {"type": "number", "minimum": 0},
        {"type": "boolean"},
        {"type": "string", "minLength": 1},
        {"type": "object", "properties": {"answer": {"type": "string"}}},
        {"anyOf": [{"type": "string"}, {"type": "null"}]},
        {"$defs": {"answer": {"type": "string"}}, "$ref": "#/$defs/answer"},
    ],
)
def test_任何Draft202012有效物件schema皆可確認(結構):
    服務, _ = _建立()
    assert _確認(服務, response_schema=結構).response_schema == 結構


@pytest.mark.parametrize(
    "覆寫",
    [
        {"slug": "Upper"}, {"slug": "-bad"}, {"slug": "bad--slug"},
        {"slug": "a" * 64}, {"docs": " "}, {"docs": "中" * 5462},
        {"endpoint_limit": True}, {"endpoint_limit": 0}, {"endpoint_limit": 10_001},
        {"credential_limit": False}, {"credential_limit": 10**400},
        {"response_schema": True}, {"response_schema": [True]},
        {"response_schema": {"type": "object", "unknownKeyword": object()}},
        {"response_schema": {"type": "invalid"}}, {"response_schema": {"required": "answer"}},
    ],
)
def test_非法值固定拒絕且不留下確認(覆寫):
    服務, _ = _建立()
    with pytest.raises(ValueError, match="^發布值確認輸入無效$") as 錯誤:
        _確認(服務, **覆寫)

    assert 錯誤.value.__cause__ is None and 錯誤.value.__context__ is None
    assert 服務.讀取草稿("owner", "draft-p03", 現在=121).發布確認 is None


def test_回傳確認與已確認草稿皆完全detached且偽造無法安裝():
    服務, 公開草稿 = _建立()
    確認 = _確認(服務, response_schema={"type": "array"})
    已確認草稿 = 服務.讀取已確認草稿("owner", "draft-p03", 現在=121)
    偽造 = 發布值確認("draft-p03", 0, "forged", '{"type":"string"}', "偽造", 1, 1)

    for 物件, 欄位, 值 in (
        (確認, "草稿識別碼", "evil"), (確認, "草稿世代", 99),
        (確認, "slug", "evil"), (確認, "_回應結構正規JSON", '{"type":"boolean"}'),
        (確認, "docs", "evil"), (確認, "endpoint_limit", 999),
        (確認, "credential_limit", 999), (確認, "window_seconds", 999),
        (已確認草稿.發布確認, "slug", "nested-evil"),
        (公開草稿, "發布確認", 偽造),
    ):
        object.__setattr__(物件, 欄位, 值)
    schema = 已確認草稿.發布確認.response_schema
    schema["type"] = "number"

    留存 = 服務.讀取已確認草稿("owner", "draft-p03", 現在=122)
    assert 留存.發布確認.slug == "customer-support-v1"
    assert 留存.發布確認.response_schema == {"type": "array"}
    assert 留存.發布確認.window_seconds == 60
    assert 留存.發布確認 is not 已確認草稿.發布確認


@pytest.mark.parametrize(
    "引數",
    [
        ("", 0, "safe", '{}', "文件", 1, 1),
        ("draft", True, "safe", '{}', "文件", 1, 1),
        ("draft", 0, "Upper", '{}', "文件", 1, 1),
        ("draft", 0, "safe", '{ "type":"string"}', "文件", 1, 1),
        ("draft", 0, "safe", '[]', "文件", 1, 1),
        ("draft", 0, "safe", '{}', " ", 1, 1),
    ],
)
def test_發布值確認建構子防禦所有欄位(引數):
    with pytest.raises(ValueError, match="^發布值確認輸入無效$"):
        發布值確認(*引數)


def test_權威留存確認畸形時讀取fail_closed():
    服務, _ = _建立()
    _確認(服務)
    留存 = 服務._草稿["draft-p03"].發布確認
    object.__setattr__(留存, "_回應結構正規JSON", "[]")

    with pytest.raises(草稿存取錯誤, match="^規劃草稿不可用$"):
        服務.讀取已確認草稿("owner", "draft-p03", 現在=122)


@pytest.mark.parametrize("正規JSON", ['{"type":"invalid"}', '{"required":"answer"}'])
def test_權威留存確認canonical但meta_invalid時直接與wrapper皆fail_closed且不改狀態(正規JSON):
    服務, _ = _建立()
    _確認(服務)
    留存草稿 = 服務._草稿["draft-p03"]
    object.__setattr__(留存草稿.發布確認, "_回應結構正規JSON", 正規JSON)
    wrapper = 發布規劃服務(權限協調器(object()), 草稿服務=服務)

    for 讀取 in (服務.讀取已確認草稿, wrapper.讀取已確認草稿):
        with pytest.raises(草稿存取錯誤, match="^規劃草稿不可用$") as 錯誤:
            讀取("owner", "draft-p03", 現在=122)
        assert 錯誤.value.__cause__ is None and 錯誤.value.__context__ is None
        assert 服務._草稿["draft-p03"] is 留存草稿
