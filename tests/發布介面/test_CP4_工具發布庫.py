"""CP4 release-addressed sealed 工具發布庫的生命週期與隔離測試。"""

import json
import threading

import pytest

from 繁中代理.工具 import 工具定義
from 繁中代理.發布介面.執行期.工具版本庫 import 計算工具修訂摘要
from 繁中代理.發布介面.執行期.工具發布庫 import (
    工具發布庫,
    工具發布描述,
    工具發布註冊,
    工具發布錯誤,
)


def _工具(名稱: str, 結果: str, 結構=None) -> 工具定義:
    """建立測試工具；參數為名稱、結果與 schema；回傳工具定義；不拋例外；無副作用。"""
    return 工具定義(名稱, f"說明-{名稱}", 結構 or {"type": "object"}, lambda _: 結果)


def _描述(release: str, *註冊: 工具發布註冊) -> 工具發布描述:
    """建立測試描述；回傳 immutable DTO；欄位不合法時傳出契約錯誤；無副作用。"""
    return 工具發布描述(release, tuple(註冊))


def _呼叫(發布版, 名稱: str) -> str:
    """呼叫發布工具；回傳 result；工具失敗時傳出測試解析錯誤；只執行測試 handler。"""
    return json.loads(發布版.建立工具登錄器().呼叫工具(名稱, {}))["result"]


def test_摘要與既有版本庫演算法byte_identical且工具順序保留():
    """同一 canonical schema 的公開摘要與發布快照完全一致。"""
    工具甲, 工具乙 = _工具("alpha", "甲"), _工具("beta", "乙")
    版 = 工具發布庫().登錄發布(_描述(
        "release-1", 工具發布註冊("rev-1", 工具甲), 工具發布註冊("rev-2", 工具乙)
    ))
    assert [項.tool.名稱 for 項 in 版.tools] == ["alpha", "beta"]
    修訂 = 版.取得工具修訂("alpha", "rev-1")
    assert 修訂.摘要 == 計算工具修訂摘要(
        name="alpha", revision="rev-1",
        description="說明-alpha", parameters={"type": "object"},
    )


def test_發布封存DTO_schema與handler且每次回傳新鮮檢視():
    """發布後的呼叫端 mutation 不得改變 authoritative schema 或 handler。"""
    schema = {"type": "object", "properties": {"x": {"type": "string"}}}
    原工具 = _工具("lookup", "舊", schema)
    描述 = _描述("release-1", 工具發布註冊("rev-1", 原工具))
    庫 = 工具發布庫()
    第一 = 庫.登錄發布(描述)
    schema["properties"]["x"]["type"] = "integer"
    object.__setattr__(原工具, "說明", "竄改")
    object.__setattr__(原工具, "處理函數", lambda _: "新")
    object.__setattr__(描述, "handler_release", "release-evil")
    第一._內容.revisions.移除修訂("lookup", "rev-1")

    第二, 第三 = 庫.取得發布("release-1"), 庫.取得發布("release-1")
    assert 第二 is not 第一 and 第三 is not 第二
    assert 第二.tools is not 第二.tools
    assert 第二.tools[0].tool.參數結構["properties"]["x"]["type"] == "string"
    assert 第二.tools[0].tool.說明 == "說明-lookup"
    assert _呼叫(第二, "lookup") == "舊"
    assert 庫.取得發布("release-evil") is None


def test_相同名稱修訂跨release使用不同handler不串線且無fallback():
    """release 是 handler authority，查詢缺失時不得回退任何其他發布。"""
    庫 = 工具發布庫()
    甲 = 庫.登錄發布(_描述("release-a", 工具發布註冊("rev-1", _工具("lookup", "甲"))))
    乙 = 庫.登錄發布(_描述("release-b", 工具發布註冊("rev-1", _工具("lookup", "乙"))))
    assert _呼叫(甲, "lookup") == "甲"
    assert _呼叫(乙, "lookup") == "乙"
    assert 庫.取得發布("release-missing") is None
    assert not any(hasattr(庫, 名稱) for 名稱 in ("目前", "最新", "預設", "current", "latest", "default"))


def test_重複工具名稱完整預檢失敗且不占用release():
    """失敗批次不得部分安裝或留下 release 墓碑。"""
    庫 = 工具發布庫()
    壞描述 = _描述(
        "release-1",
        工具發布註冊("rev-1", _工具("same", "甲")),
        工具發布註冊("rev-2", _工具("same", "乙")),
    )
    with pytest.raises(工具發布錯誤, match="^工具發布不可用$"):
        庫.登錄發布(壞描述)
    assert 庫.取得發布("release-1") is None
    assert _呼叫(庫.登錄發布(_描述(
        "release-1", 工具發布註冊("rev-ok", _工具("ok", "好"))
    )), "ok") == "好"


def test_移除後release墓碑永久不可重用且舊登錄器仍封存():
    """移除只撤下 live release，不允許 identity resurrection。"""
    庫 = 工具發布庫()
    版 = 庫.登錄發布(_描述("release-1", 工具發布註冊("rev-1", _工具("lookup", "舊"))))
    登錄器 = 版.建立工具登錄器()
    庫.移除發布("release-1")
    assert 庫.取得發布("release-1") is None
    with pytest.raises(工具發布錯誤, match="^工具發布不可用$"):
        庫.登錄發布(_描述("release-1", 工具發布註冊("rev-1", _工具("lookup", "新"))))
    assert json.loads(登錄器.呼叫工具("lookup", {}))["result"] == "舊"


def test_一般錯誤固定且控制流程例外保持identity():
    """資料存取錯誤不得洩漏，三種控制流程訊號不得被替換。"""
    工具 = _工具("lookup", "x")
    object.__setattr__(工具, "參數結構", {"bad": object()})
    with pytest.raises(工具發布錯誤, match="^工具發布不可用$") as 錯誤:
        工具發布庫().登錄發布(_描述("release-1", 工具發布註冊("rev-1", 工具)))
    assert 錯誤.value.__cause__ is None and 錯誤.value.__context__ is None

    for 例外型別 in (KeyboardInterrupt, SystemExit, GeneratorExit):
        中斷 = 例外型別("stop")

        class 中斷處理器:
            def __call__(self, 參數):
                return None

            def __deepcopy__(self, memo):
                raise 中斷

        壞工具 = 工具定義("lookup", "說明", {}, 中斷處理器())
        with pytest.raises(例外型別) as 收到:
            工具發布庫().登錄發布(_描述("release-x", 工具發布註冊("rev-1", 壞工具)))
        assert 收到.value is 中斷


def test_並行登錄同一release只有一個成功且移除後仍不可重用():
    """生命週期鎖確保 one-shot release 線性化。"""
    庫, 起跑, 結果 = 工具發布庫(), threading.Barrier(3), []

    def 登錄(值: str) -> None:
        起跑.wait()
        try:
            版 = 庫.登錄發布(_描述("release-1", 工具發布註冊("rev-1", _工具("lookup", 值))))
            結果.append(("成功", _呼叫(版, "lookup")))
        except 工具發布錯誤:
            結果.append(("失敗", 值))

    執行緒們 = [threading.Thread(target=登錄, args=(值,)) for 值 in ("甲", "乙")]
    for 執行緒 in 執行緒們:
        執行緒.start()
    起跑.wait()
    for 執行緒 in 執行緒們:
        執行緒.join()
    assert sorted(狀態 for 狀態, _ in 結果) == ["失敗", "成功"]
    成功值 = next(值 for 狀態, 值 in 結果 if 狀態 == "成功")
    assert _呼叫(庫.取得發布("release-1"), "lookup") == 成功值
    庫.移除發布("release-1")
    with pytest.raises(工具發布錯誤):
        庫.登錄發布(_描述("release-1", 工具發布註冊("rev-1", _工具("lookup", "丙"))))
