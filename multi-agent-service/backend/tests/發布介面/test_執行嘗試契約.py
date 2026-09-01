"""INV I04 自有、傳輸中立的 runtime 嘗試 DTO 契約。"""

from dataclasses import FrozenInstanceError

import pytest

from 繁中代理.發布介面.呼叫.編排器 import 執行嘗試結果, 執行嘗試紀錄收據, 執行嘗試請求
from 繁中代理.發布介面.領域模型 import PublishedUsage, PublishedWarning


def test_嘗試請求不可變且保留同一釘選與不受信任輸入參照():
    pinned, input_data, metadata = object(), {"q": 1}, {"trace": 2}
    request = 執行嘗試請求(pinned, input_data, metadata, 1)

    assert request.pinned_version is pinned
    assert request.input is input_data and request.metadata is metadata
    assert request.attempt == 1
    assert not hasattr(request, "__dict__")
    with pytest.raises(FrozenInstanceError):
        request.attempt = 2


@pytest.mark.parametrize("attempt", [0, 3, True])
def test_嘗試請求只接受精確的一或二(attempt):
    with pytest.raises(ValueError, match="^執行嘗試請求不符合契約$"):
        執行嘗試請求(object(), {}, None, attempt)


def test_成功結果只攜帶detached公開資料用量與警告():
    usage = PublishedUsage(7)
    warning = PublishedWarning("tool_recovered", "工具失敗後已恢復。")
    data = {"answer": [1]}
    result = 執行嘗試結果("success", data, usage, (warning,))
    data["answer"].append(2)

    assert result.kind == "success" and result.data == {"answer": [1]}
    assert result.usage == usage and result.usage is not usage
    assert result.warnings == (warning,) and result.warnings[0] is not warning
    assert not hasattr(result, "internal_outcome_token")
    assert not hasattr(result, "__dict__")


@pytest.mark.parametrize(
    "kind",
    ["model_timeout", "tool_execution_failed", "tool_timeout", "endpoint_misconfigured", "internal_error"],
)
def test_失敗結果只允許canonical_terminal_kind且無部分公開資料(kind):
    result = 執行嘗試結果(kind)
    assert result.kind == kind and result.data is result.usage is None
    assert result.warnings == () and not hasattr(result, "internal_outcome_token")


@pytest.mark.parametrize(
    "args",
    [
        ("unknown", None, None, ()),
        ("model_timeout", {"partial": 1}, None, ()),
        ("tool_timeout", None, PublishedUsage(1), ()),
        ("internal_error", None, None, (PublishedWarning("x", "y"),)),
        ("success", None, object(), ()),
        ("success", None, None, [PublishedWarning("x", "y")]),
    ],
)
def test_嘗試結果拒絕未知矛盾或非精確欄位(args):
    with pytest.raises(ValueError, match="^執行嘗試結果不符合契約$"):
        執行嘗試結果(*args)


def test_執行嘗試紀錄收據只接受匹配範圍與exact_committed_true():
    receipt = 執行嘗試紀錄收據("inv-1", 2, True, 9)
    assert (receipt.invocation_id, receipt.attempt, receipt.committed, receipt.sequence) == (
        "inv-1", 2, True, 9,
    )
    assert not hasattr(receipt, "__dict__")
    for args in (("", 1, True, 1), ("inv", 3, True, 1), ("inv", 1, False, 1), ("inv", 1, 1, 1)):
        with pytest.raises(ValueError, match="^執行嘗試紀錄收據不符合契約$"):
            執行嘗試紀錄收據(*args)
