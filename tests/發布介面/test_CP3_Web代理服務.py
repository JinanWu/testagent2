"""CP3 WEB-CHAT：Web Chat 服務安全邊界測試。"""

import importlib
import os
from types import SimpleNamespace

import pytest

from 繁中代理.使用者 import 使用者上下文
from 繁中代理.發布介面.Web代理服務 import (
    Web代理服務, Web服務不可用, Web資源不存在, 序列化工作階段列表,
    序列化工作階段詳情, 序列化技能列表, 序列化技能詳情, 序列化聊天回應,
)


class 假工作階段庫:
    """提供 Chat 測試需要的最小工作階段能力。"""

    def __init__(self, 工作階段=None, 根="root-1"):
        self.工作階段 = 工作階段
        self.根 = 根

    def 檢查工作階段存取(self, 工作階段識別碼, user_id=None, source=None):
        if isinstance(self.工作階段, Exception):
            raise self.工作階段
        if type(self.工作階段) is dict:
            return {"id": 工作階段識別碼, **self.工作階段}
        return self.工作階段

    def 取得工作階段譜系(self, 工作階段識別碼):
        return [self.根, 工作階段識別碼]

    def 列出工作階段(self, **條件):
        self.列表條件 = 條件
        return [self.工作階段] if self.工作階段 else []

    def 解析Resume工作階段(self, 工作階段識別碼, **條件):
        self.resume條件 = 條件
        return "tip-1"

    def 讀取工作階段(self, 工作階段識別碼):
        return {"id": 工作階段識別碼, "title": "目前標題", "updated_at": 20.0, "source": "web", "user_id": "user-1"}

    def 讀取訊息(self, 工作階段識別碼, **條件):
        self.訊息條件 = 條件
        return [
            {"role": "system", "content": "秘密"},
            {"role": "user", "content": "問題", "reasoning": "不可見"},
            {"role": "assistant", "content": "回答", "tool_calls": [{"args": "秘密"}]},
            {"role": "tool", "content": "工具秘密"},
        ]


class 假使用者庫:
    """依登入識別碼建立完整使用者上下文。"""

    def __init__(self, *, skill_roots=None, enabled_skills=None):
        self.skill_roots = [] if skill_roots is None else skill_roots
        self.enabled_skills = enabled_skills

    def 建立使用者上下文(self, user_id=None):
        return 使用者上下文(
            user_id=user_id, username="alice", roles=["user"],
            skill_roots=self.skill_roots, enabled_skills=self.enabled_skills,
        )


def test_聊天以完整登入上下文及web來源建立執行階段():
    """WEB-CHAT-01：factory 不得只收到可偽造的 username。"""
    捕捉 = {}

    def 工廠(*, 使用者上下文物件, source):
        捕捉.update(上下文=使用者上下文物件, source=source)
        return SimpleNamespace(執行使用者訊息=lambda message, session_id=None: SimpleNamespace(
            最終回答="安全回答", 工作階段識別碼="tip-1"
        ))

    服務 = Web代理服務(假工作階段庫({"id": "root-1", "source": "web", "user_id": "user-1"}), 假使用者庫(), 工廠)
    回應 = 服務.聊天("user-1", "你好", "root-1")

    assert 捕捉 == {"上下文": 捕捉["上下文"], "source": "web"}
    assert 捕捉["上下文"].user_id == "user-1"
    assert 序列化聊天回應(回應) == {
        "session_id": "root-1", "reply": {"role": "assistant", "content": "安全回答"}
    }


@pytest.mark.parametrize("工作階段", [None, PermissionError("secret")])
def test_聊天將不存在或跨範圍工作階段統一為不存在(工作階段):
    """WEB-CHAT-02：跨 owner/source 與缺少不可被枚舉。"""
    def 不可呼叫(**kwargs):
        raise AssertionError(kwargs)

    服務 = Web代理服務(假工作階段庫(工作階段), 假使用者庫(), 不可呼叫)
    with pytest.raises(Web資源不存在):
        服務.聊天("user-1", "你好", "hidden")


@pytest.mark.parametrize("缺少欄位", ["user_id", "source"])
def test_聊天拒絕缺少owner或source的legacy資料(缺少欄位):
    """Quality P1：repository 未拒絕 NULL/missing scope 時，服務仍須 404。"""
    資料 = {"id": "root-1", "user_id": "user-1", "source": "web"}
    del 資料[缺少欄位]
    服務 = Web代理服務(假工作階段庫(資料), 假使用者庫(), lambda **kwargs: None)
    with pytest.raises(Web資源不存在):
        服務.聊天("user-1", "你好", "root-1")


def test_聊天resume只接受logical_root且結果不得跨譜系():
    """Quality P1：compression child 與 runtime cross-lineage result 一律 404。"""
    資料 = {"user_id": "user-1", "source": "web"}
    child服務 = Web代理服務(假工作階段庫(資料, 根="root-1"), 假使用者庫(), lambda **kwargs: None)
    with pytest.raises(Web資源不存在):
        child服務.聊天("user-1", "你好", "child-1")

    庫 = 假工作階段庫(資料, 根="other-root")
    def 工廠(**kwargs):
        return SimpleNamespace(執行使用者訊息=lambda *args: SimpleNamespace(最終回答="回答", 工作階段識別碼="other-tip"))
    服務 = Web代理服務(庫, 假使用者庫(), 工廠)
    庫.根 = "root-1"
    庫.取得工作階段譜系 = lambda 識別碼: ["root-1"] if 識別碼 == "root-1" else ["other-root", 識別碼]
    with pytest.raises(Web資源不存在):
        服務.聊天("user-1", "你好", "root-1")


def test_工作階段列表使用既有篩選並只序列化安全欄位():
    """CP3-WEB-SESSION-01：列表固定 web/owner/root/active 投影。"""
    原始 = {
        "id": "tip-1", "_lineage_root_id": "root-1", "title": "標題",
        "updated_at": 10.0, "message_count": 3, "system_prompt": "秘密", "cwd": "/secret",
    }
    庫 = 假工作階段庫(原始)
    服務 = Web代理服務(庫, 假使用者庫(), lambda **kwargs: None)

    回應 = 序列化工作階段列表(服務.列出工作階段("user-1", 20))

    assert 庫.列表條件 == {
        "limit": 20, "include_children": False, "include_archived": False,
        "source": "web", "user_id": "user-1",
    }
    assert 回應 == {"sessions": [{
        "id": "root-1", "title": "標題", "updated_at": 10.0, "message_count": 3,
    }]}


def test_工作階段詳情解析tip且只保留使用者與助理文字():
    """CP3-WEB-SESSION-02：detail 排除 system/tool/reasoning/tool_calls。"""
    庫 = 假工作階段庫({"id": "root-1", "source": "web", "user_id": "user-1"})
    服務 = Web代理服務(庫, 假使用者庫(), lambda **kwargs: None)

    回應 = 序列化工作階段詳情(服務.讀取工作階段("user-1", "root-1"))

    assert 庫.resume條件 == {"user_id": "user-1", "source": "web"}
    assert 庫.訊息條件 == {"include_ancestors": True, "user_id": "user-1"}
    assert 回應 == {
        "session": {"id": "root-1", "title": "目前標題", "updated_at": 20.0},
        "messages": [
            {"role": "user", "content": "問題"},
            {"role": "assistant", "content": "回答"},
        ],
    }


def test_工作階段詳情拒絕以compression_child作為公開識別碼():
    """CP3-WEB-SESSION-03：detail 僅接受 canonical logical root。"""
    庫 = 假工作階段庫({"id": "child-1", "source": "web", "user_id": "user-1"}, 根="root-1")
    服務 = Web代理服務(庫, 假使用者庫(), lambda **kwargs: None)

    with pytest.raises(Web資源不存在):
        服務.讀取工作階段("user-1", "child-1")

    assert not hasattr(庫, "resume條件")


@pytest.mark.parametrize("缺少欄位", ["user_id", "source"])
def test_工作階段詳情拒絕root或tip缺少scope(缺少欄位):
    """Quality P1：root/tip 都必須 exact owner/source mapping。"""
    資料 = {"id": "root-1", "user_id": "user-1", "source": "web"}
    del 資料[缺少欄位]
    服務 = Web代理服務(假工作階段庫(資料), 假使用者庫(), lambda **kwargs: None)
    with pytest.raises(Web資源不存在):
        服務.讀取工作階段("user-1", "root-1")


def test_工作階段詳情限制訊息筆數與aggregate_bytes(monkeypatch):
    """Quality P2：transcript entries 與總 UTF-8 bytes 皆有固定上限。"""
    模組 = importlib.import_module("繁中代理.發布介面.Web代理服務")
    庫 = 假工作階段庫({"user_id": "user-1", "source": "web"})
    服務 = Web代理服務(庫, 假使用者庫(), lambda **kwargs: None)
    monkeypatch.setattr(模組, "_最大工作階段訊息數量", 1)
    with pytest.raises(Web服務不可用):
        服務.讀取工作階段("user-1", "root-1")
    monkeypatch.setattr(模組, "_最大工作階段訊息數量", 10)
    monkeypatch.setattr(模組, "_最大工作階段總位元組", 3)
    with pytest.raises(Web服務不可用):
        服務.讀取工作階段("user-1", "root-1")
