"""GOV 端點觀測 transport-neutral exact 契約測試。"""

from dataclasses import FrozenInstanceError
from typing import get_type_hints

import pytest

from 繁中代理.發布介面.治理.觀測契約 import (
    安全錯誤排行,
    每日端點指標,
    定價版本成本,
    延遲摘要,
    指標查詢成功,
    指標查詢結果,
    用量摘要,
    端點不可見結果,
    端點指標,
    端點觀測查詢服務,
    診斷查詢成功,
    診斷查詢結果,
    診斷用量,
    診斷項目,
    診斷頁,
    觀測視窗,
)


def test_指標契約為凍結槽位精確結構():
    視窗 = 觀測視窗(1000.0, 87400.0, "UTC")
    指標 = 端點指標(
        "ep-1", 視窗, 12, 11, 3, 3 / 11,
        延遲摘要(10, 42.5, 30.0, 100.0, 120.0),
        用量摘要(8, 100, 40, 140), "0.0125",
        (定價版本成本("price-v1", "0.0125"),),
        (每日端點指標("1970-01-01", 12, 11, 3, 140, "0.0125"),),
        (安全錯誤排行("schema_invalid", 3),),
    )
    assert 指標查詢成功(指標).指標 is 指標
    assert not hasattr(指標, "__dict__")
    with pytest.raises(FrozenInstanceError):
        指標.invocation_count = 0


def test_診斷契約只含安全欄位且容器不可變():
    項目 = 診斷項目(
        "inv-1", "req-1", "ver-1", "failed", "schema_invalid", "$.answer",
        12.5, 診斷用量(17), ("safe_tool",), 1000.0, 1012.5, (),
    )
    頁 = 診斷頁((項目,), "signed.cursor")
    assert 診斷查詢成功(頁).頁.items == (項目,)
    assert tuple(項目.__slots__) == (
        "invocation_id", "request_id", "endpoint_version_id", "status", "error_code",
        "schema_path", "latency_ms", "usage", "tool_names", "created_at", "completed_at",
        "redacted_fields",
    )


@pytest.mark.parametrize(
    "factory",
    [
        lambda: 觀測視窗(2.0, 1.0),
        lambda: 觀測視窗(1.0, 2.0, "Asia/Taipei"),
        lambda: 每日端點指標("2026-8-01", 1, 1, 0, 0, "0"),
        lambda: 安全錯誤排行("bad code", 1),
        lambda: 延遲摘要(1, None, None, None, None),
        lambda: 用量摘要(1, 1, 2, 2),
        lambda: 定價版本成本("bad version", "1"),
        lambda: 端點不可見結果("ep-1"),
        lambda: 診斷用量(-1),
        lambda: 診斷項目("i", "r", "v", "bad", None, None, None, None, (), 1.0, None, ()),
        lambda: 診斷頁([], None),
    ],
)
def test_契約拒絕畸形與任意容器(factory):
    with pytest.raises((TypeError, ValueError)):
        factory()


def test_結果聯集與協定固定方法():
    assert set(get_type_hints(端點觀測查詢服務.讀取端點指標)) >= {
        "擁有者使用者識別碼", "是否管理者", "端點識別碼", "視窗秒數", "return"
    }
    assert get_type_hints(端點觀測查詢服務.讀取端點指標)["return"] == 指標查詢結果
    assert get_type_hints(端點觀測查詢服務.列出端點診斷)["return"] == 診斷查詢結果


def test_聚合成本接受數學上界並拒絕溢位與非canonical():
    上界 = "9223372036854775806999999999999999999.9999999990776627963145224193"
    assert 定價版本成本("v", 上界).estimated_cost_usd == 上界
    for 壞值 in (
        "9223372036854775806999999999999999999.9999999990776627963145224194",
        "10000000000000000000000000000000000000", "1e2", "01", "1.0", "-1",
    ):
        with pytest.raises(ValueError):
            定價版本成本("v", 壞值)


def test_成功outcome拒絕任意物件與DTO子類且零callback():
    呼叫 = []
    class 敵對:
        def __getattribute__(self, 名稱):
            呼叫.append(名稱)
            raise AssertionError
    with pytest.raises(TypeError):
        指標查詢成功(敵對())
    with pytest.raises(TypeError):
        診斷查詢成功(敵對())
    assert 呼叫 == []

    class 子指標(端點指標):
        pass
    class 子頁(診斷頁):
        pass
    指標 = 子指標("ep", 觀測視窗(1.0, 2.0), 0, 0, 0, 0.0,
              延遲摘要(0, None, None, None, None), 用量摘要(0, 0, 0, 0), "0", (), (), ())
    with pytest.raises(TypeError):
        指標查詢成功(指標)
    with pytest.raises(TypeError):
        診斷查詢成功(子頁((), None))


def test_指標契約拒絕daily與overall跨總計或排序不一致():
    """Daily、成本分項與Top Errors在DTO邊界即須自洽。"""
    共用 = (
        "ep", 觀測視窗(0.0, 172800.0), 2, 2, 1, 0.5,
        延遲摘要(0, None, None, None, None), 用量摘要(1, 2, 3, 5), "0.3",
        (定價版本成本("v1", "0.3"),),
    )
    with pytest.raises(ValueError):
        端點指標(*共用, (每日端點指標("1970-01-01", 1, 1, 1, 5, "0.3"),), ())
    with pytest.raises(ValueError):
        端點指標(*共用, (
            每日端點指標("1970-01-02", 1, 1, 1, 5, "0.3"),
            每日端點指標("1970-01-01", 1, 1, 0, 0, "0"),
        ), (安全錯誤排行("z", 1),))
    with pytest.raises(ValueError):
        端點指標(*共用, (每日端點指標("1970-01-01", 2, 2, 1, 5, "0.2"),), ())


def test_端點指標重建nested_DTO並拒絕poison與窗外日期():
    """Parent release seam不信任已建構child slot，也不共享可毒化參照。"""
    原始錯誤 = 安全錯誤排行("safe", 1)
    基本 = (
        "ep", 觀測視窗(0.0, 86400.0), 1, 1, 1, 1.0,
        延遲摘要(0, None, None, None, None), 用量摘要(0, 0, 0, 0), "0", (),
        (每日端點指標("1970-01-01", 1, 1, 1, 0, "0"),),
    )
    指標 = 端點指標(*基本, (原始錯誤,))
    object.__setattr__(原始錯誤, "error_code", "RAW_SECRET_MARKER")
    assert 指標.top_errors[0].error_code == "safe"
    毒化錯誤 = 安全錯誤排行("safe", 1)
    object.__setattr__(毒化錯誤, "error_code", "RAW_SECRET_MARKER")
    with pytest.raises(ValueError, match="端點指標不符合契約"):
        端點指標(*基本, (毒化錯誤,))
    with pytest.raises(ValueError, match="端點指標不符合契約"):
        端點指標(
            "ep", 觀測視窗(0.0, 86400.0), 1, 1, 0, 0.0,
            延遲摘要(0, None, None, None, None), 用量摘要(0, 0, 0, 0), "0", (),
            (每日端點指標("1970-01-02", 1, 1, 0, 0, "0"),), (),
        )
    object.__setattr__(指標.top_errors[0], "error_code", "RAW_SECRET_MARKER")
    object.__setattr__(指標.daily[0], "date", "RAW_SECRET_MARKER")
    object.__setattr__(指標.window, "timezone", "RAW_SECRET_MARKER")
    assert "RAW_SECRET_MARKER" not in repr(指標)
    assert "RAW_SECRET_MARKER" not in repr(指標.top_errors[0])
    assert "RAW_SECRET_MARKER" not in repr(指標.daily[0])
    assert "RAW_SECRET_MARKER" not in repr(指標.window)


def test_端點指標接受契約最大成本的精確cross_total():
    上界 = "9223372036854775806999999999999999999.9999999990776627963145224193"
    指標 = 端點指標(
        "ep", 觀測視窗(0.0, 1.0), 1, 1, 0, 0.0,
        延遲摘要(0, None, None, None, None), 用量摘要(0, 0, 0, 0), 上界,
        (定價版本成本("v", 上界),),
        (每日端點指標("1970-01-01", 1, 1, 0, 0, 上界),), (),
    )
    assert 指標.estimated_cost_usd == 上界


def test_空半開視窗只接受全零aggregate():
    with pytest.raises(ValueError, match="端點指標不符合契約"):
        端點指標(
            "ep", 觀測視窗(86400.0, 86400.0), 1, 1, 0, 0.0,
            延遲摘要(0, None, None, None, None), 用量摘要(0, 0, 0, 0), "0", (),
            (每日端點指標("1970-01-02", 1, 1, 0, 0, "0"),), (),
        )
