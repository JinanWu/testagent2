"""CP4-PLANNER-01/02：server-side Planner、strict DTO 與同快照 pins。"""
import json

import pytest

from 繁中代理.發布介面.協定 import 授權工具, 授權技能, 規劃權限快照
from 繁中代理.發布介面.規劃.綱要 import 規劃服務
from 繁中代理.發布介面.規劃.規劃器供應商 import 決定性假規劃器, Gemini規劃器
from 繁中代理.發布介面.規劃.規劃器契約 import 規劃器不可用, 規劃器輸出無效
from 繁中代理.發布介面.規劃.規劃器服務 import 伺服器端草稿規劃服務

雜湊 = "a" * 64

class 權限查詢:
    def __init__(self):
        self.次數 = 0
    def 查詢規劃權限(self, owner, /):
        self.次數 += 1
        return 規劃權限快照(
            "perm-r7", (授權技能("alpha", "技能摘要", 雜湊),),
            (授權工具("read_file", "tool-r3"), 授權工具("terminal", "tool-r4")),
        )

class 原始轉接器:
    def __init__(self, 修改):
        self.修改 = 修改
    def 產生(self, 輸入, /):
        值 = json.loads(決定性假規劃器().產生(輸入))
        self.修改(值)
        return json.dumps(值)

def _服務(adapter=None):
    查詢 = 權限查詢()
    草稿庫 = 規劃服務(識別碼產生器=lambda: "draft-cp4")
    return 伺服器端草稿規劃服務(查詢, adapter or 決定性假規劃器(), 草稿服務=草稿庫), 查詢

def test_CP4_PLANNER_01只查一次權威快照並保存hash_revision_pins():
    服務, 查詢 = _服務()
    草稿 = 服務.建立草稿("owner-1", "建立查詢 API", ("alpha",), "structured", 現在=10)
    assert 查詢.次數 == 1
    assert 草稿.綱要["selected_skills"] == ["alpha"]
    assert 草稿.綱要["recommended_tools"] == ["read_file"]
    assert 草稿.能力摘要.權限修訂 == "perm-r7"
    assert 草稿.能力摘要.技能[0].內容sha256參照 == 雜湊
    assert 草稿.能力摘要.工具[0].釘選修訂 == "tool-r3"

@pytest.mark.parametrize("修改", [
    lambda 值: 值.update(extra="forbidden"),
    lambda 值: 值.pop("system_prompt"),
    lambda 值: 值.__setitem__("recommended_tools", ["root_shell"]),
    lambda 值: 值.__setitem__("selected_skills", ["other"]),
    lambda 值: 值.__setitem__("response_schema", {"type": "not-a-json-type"}),
    lambda 值: 值.__setitem__("rate_limit", {"endpoint_per_minute": 0, "credential_per_minute": 1}),
])
def test_CP4_PLANNER_02敵對provider輸出整體fail_closed(修改):
    服務, _ = _服務(原始轉接器(修改))
    with pytest.raises(規劃器輸出無效, match="^規劃器輸出無效$"):
        服務.建立草稿("owner-1", "需求", ("alpha",), "structured", 現在=1)

@pytest.mark.parametrize("技能", [(), ("alpha", "alpha"), ("beta", "alpha"), ("../bad",), tuple(f"s{i:02}" for i in range(33))])
def test_CP4_PLANNER_03技能至少一項唯一排序且有界(技能):
    服務, 查詢 = _服務()
    with pytest.raises((ValueError, Exception)):
        服務.建立草稿("owner-1", "需求", 技能, "text", 現在=1)
    if 技能 != ("beta", "alpha"):
        assert 查詢.次數 == 0

class 洩漏供應商:
    def 產生JSON(self, **kwargs):
        raise RuntimeError("PROVIDER_SECRET_DIAGNOSTIC")

def test_CP4_PLANNER_04_Gemini普通錯誤固定且不洩漏診斷():
    服務, _ = _服務(Gemini規劃器(洩漏供應商()))
    with pytest.raises(規劃器不可用) as 捕捉:
        服務.建立草稿("owner-1", "需求", ("alpha",), "text", 現在=1)
    assert str(捕捉.value) == "規劃器暫時不可用"
    assert 捕捉.value.__cause__ is None
    assert "SECRET" not in repr(捕捉.value)
