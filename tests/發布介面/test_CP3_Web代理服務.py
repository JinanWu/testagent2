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


def _建立技能(根, 分類, 名稱, 描述="說明"):
    """建立最小真實 SKILL.md fixture。"""
    路徑 = 根 / 分類 / 名稱 / "SKILL.md"
    路徑.parent.mkdir(parents=True)
    路徑.write_text(f"---\nname: {名稱}\ndescription: {描述}\n---\n\n# 完整內容\n", encoding="utf-8")
    return 路徑


def test_技能列表與詳情只使用授權roots且不洩漏path(tmp_path, monkeypatch):
    """CP3-WEB-SKILL-01：enabled filter、allowlist 與完整 bounded 內容。"""
    根 = tmp_path / "skills"
    路徑 = _建立技能(根, "docs", "writer")
    monkeypatch.setenv("AIAGENT_SKILL_SNAPSHOT_PATH", str(tmp_path / "cache.json"))
    服務 = Web代理服務(
        假工作階段庫(), 假使用者庫(skill_roots=[根], enabled_skills={"writer"}),
        lambda **kwargs: None,
    )

    列表 = 序列化技能列表(服務.列出技能("user-1"))
    詳情 = 序列化技能詳情(服務.讀取技能("user-1", "writer"))

    assert 列表 == {"skills": [{"id": "writer", "name": "writer", "category": "docs", "description": "說明"}]}
    assert "path" not in str(列表) and 詳情["content"] == 路徑.read_text(encoding="utf-8")


def test_技能重複與symlink皆fail_closed(tmp_path, monkeypatch):
    """CP3-WEB-SKILL-02：duplicate 不任選第一個，symlink detail 統一 404。"""
    根一, 根二 = tmp_path / "one", tmp_path / "two"
    _建立技能(根一, "docs", "same")
    _建立技能(根二, "docs", "same")
    monkeypatch.setenv("AIAGENT_SKILL_SNAPSHOT_PATH", str(tmp_path / "cache.json"))
    重複服務 = Web代理服務(
        假工作階段庫(), 假使用者庫(skill_roots=[根一, 根二]), lambda **kwargs: None,
    )
    with pytest.raises(Web服務不可用):
        重複服務.列出技能("user-1")
    with pytest.raises(Web資源不存在):
        重複服務.讀取技能("user-1", "same")

    真檔 = _建立技能(tmp_path / "target", "docs", "linked")
    連結根 = tmp_path / "links"
    連結 = 連結根 / "docs" / "linked" / "SKILL.md"
    連結.parent.mkdir(parents=True)
    連結.symlink_to(真檔)
    連結服務 = Web代理服務(
        假工作階段庫(), 假使用者庫(skill_roots=[連結根]), lambda **kwargs: None,
    )
    with pytest.raises(Web資源不存在):
        連結服務.讀取技能("user-1", "linked")


def test_技能列表在解析前排除外部symlink與超大檔案(tmp_path, monkeypatch):
    """CP3-WEB-SKILL-03：unsafe candidates 不得被 metadata parser 讀取或洩漏。"""
    外部標記, 超大標記 = "OUTSIDE_LIST_SECRET", "OVERSIZED_LIST_SECRET"
    外部檔 = _建立技能(tmp_path / "outside", "docs", "linked", 外部標記)
    根 = tmp_path / "skills"
    連結 = 根 / "docs" / "linked" / "SKILL.md"
    連結.parent.mkdir(parents=True)
    連結.symlink_to(外部檔)
    超大檔 = _建立技能(根, "docs", "huge", 超大標記)
    超大檔.write_bytes(超大檔.read_bytes() + b"x" * (256 * 1024))
    monkeypatch.setenv("AIAGENT_SKILL_SNAPSHOT_PATH", str(tmp_path / "cache.json"))
    服務 = Web代理服務(
        假工作階段庫(), 假使用者庫(skill_roots=[根]), lambda **kwargs: None,
    )

    回應文字 = str(序列化技能列表(服務.列出技能("user-1")))

    assert 回應文字 == "{'skills': []}"
    assert 外部標記 not in 回應文字 and 超大標記 not in 回應文字


def test_技能合法與不安全同ID不得讓合法候選勝出(tmp_path, monkeypatch):
    """Quality P1：valid + unsafe duplicate 在 list/detail 都必須 fail closed。"""
    根 = tmp_path / "skills"
    _建立技能(根, "docs", "same")
    外部檔 = _建立技能(tmp_path / "outside", "docs", "same")
    連結 = 根 / "tools" / "same" / "SKILL.md"
    連結.parent.mkdir(parents=True)
    連結.symlink_to(外部檔)
    monkeypatch.setenv("AIAGENT_SKILL_SNAPSHOT_PATH", str(tmp_path / "cache.json"))
    服務 = Web代理服務(假工作階段庫(), 假使用者庫(skill_roots=[根]), lambda **kwargs: None)
    with pytest.raises(Web服務不可用):
        服務.列出技能("user-1")
    with pytest.raises(Web資源不存在):
        服務.讀取技能("user-1", "same")


def test_技能讀取偵測parent_directory替換競態(tmp_path, monkeypatch):
    """Quality P1：逐 component descriptor-relative open 後仍須偵測 parent replacement。"""
    模組 = importlib.import_module("繁中代理.發布介面.Web代理服務")
    根 = tmp_path / "skills"
    _建立技能(根, "docs", "demo")
    monkeypatch.setenv("AIAGENT_SKILL_SNAPSHOT_PATH", str(tmp_path / "cache.json"))
    原始開啟 = os.open
    已替換 = False

    def 競態開啟(路徑, flags, *args, **kwargs):
        nonlocal 已替換
        if 路徑 == "SKILL.md" and kwargs.get("dir_fd") is not None and not 已替換:
            已替換 = True
            舊目錄 = 根 / "docs" / "demo-old"
            (根 / "docs" / "demo").rename(舊目錄)
            _建立技能(根, "docs", "demo", "替換內容")
        return 原始開啟(路徑, flags, *args, **kwargs)

    monkeypatch.setattr(模組.os, "open", 競態開啟)
    服務 = Web代理服務(假工作階段庫(), 假使用者庫(skill_roots=[根]), lambda **kwargs: None)
    with pytest.raises(Web資源不存在):
        服務.讀取技能("user-1", "demo")
    assert 已替換


def test_技能索引限制entries與aggregate_bytes(tmp_path, monkeypatch):
    """Quality P2：大量技能 entry 與內容總量不得無界讀取。"""
    模組 = importlib.import_module("繁中代理.發布介面.Web代理服務")
    根 = tmp_path / "skills"
    _建立技能(根, "docs", "one")
    _建立技能(根, "docs", "two")
    monkeypatch.setenv("AIAGENT_SKILL_SNAPSHOT_PATH", str(tmp_path / "cache.json"))
    服務 = Web代理服務(假工作階段庫(), 假使用者庫(skill_roots=[根]), lambda **kwargs: None)
    monkeypatch.setattr(模組, "_最大技能索引項目數量", 1)
    with pytest.raises(Web服務不可用):
        服務.列出技能("user-1")
    monkeypatch.setattr(模組, "_最大技能索引項目數量", 10)
    monkeypatch.setattr(模組, "_最大技能索引總位元組", 1)
    with pytest.raises(Web服務不可用):
        服務.列出技能("user-1")


def test_技能索引巨大root只消耗走訪上限加一項目(tmp_path, monkeypatch):
    """Quality P2：非候選目錄也消耗 traversal budget，overflow 不得耗盡 root。"""
    模組 = importlib.import_module("繁中代理.發布介面.Web代理服務")
    根 = tmp_path / "skills"
    根.mkdir()
    for 編號 in range(100):
        (根 / f"junk-{編號:03d}").mkdir()
    monkeypatch.setenv("AIAGENT_SKILL_SNAPSHOT_PATH", str(tmp_path / "cache.json"))
    monkeypatch.setattr(模組, "_最大技能走訪項目數量", 2)
    原始scandir = os.scandir
    已消耗root項目 = 0

    class 計數掃描器:
        def __init__(self, 掃描器):
            self._掃描器 = 掃描器

        def __enter__(self):
            self._掃描器.__enter__()
            return self

        def __exit__(self, *args):
            return self._掃描器.__exit__(*args)

        def __iter__(self):
            return self

        def __next__(self):
            nonlocal 已消耗root項目
            項目 = next(self._掃描器)
            已消耗root項目 += 1
            return 項目

    def 計數scandir(路徑):
        掃描器 = 原始scandir(路徑)
        return 計數掃描器(掃描器) if os.fspath(路徑) == os.fspath(根) else 掃描器

    monkeypatch.setattr(模組.os, "scandir", 計數scandir)
    服務 = Web代理服務(假工作階段庫(), 假使用者庫(skill_roots=[根]), lambda **kwargs: None)

    with pytest.raises(Web服務不可用):
        服務.列出技能("user-1")

    assert 已消耗root項目 == 3


def test_有界技能走訪只在完整小批次後依名稱排序(tmp_path):
    """Quality P2：合法小 root 的每層完整 bounded batch 維持 deterministic。"""
    模組 = importlib.import_module("繁中代理.發布介面.Web代理服務")
    根 = tmp_path / "skills"
    for 分類 in ("c", "a", "b"):
        _建立技能(根, 分類, f"skill-{分類}")

    路徑清單 = list(模組._走訪有界技能索引檔案(根, "SKILL.md", 10))

    assert [路徑.parent.name for 路徑 in 路徑清單] == ["skill-a", "skill-b", "skill-c"]
