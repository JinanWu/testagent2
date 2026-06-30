"""測試 prompt 組裝順序與結構。"""

from pathlib import Path
專案根目錄 = Path(__file__).resolve().parents[1]

from 繁中代理.提示詞組裝器 import 提示詞設定, 提示詞組裝器


def test_prompt_組裝_保持_hermes_三層順序():
    """確認 stable/context/volatile 結構與關鍵順序。"""
    設定 = 提示詞設定(
        模型名稱="gemini-2.5-flash-lite",
        供應商名稱="gemini-adc",
        工作階段識別碼="s1",
        工具名稱清單=["read_file", "skills_list", "skill_view"],
        技能摘要="<available_skills>\n  - hermes-agent\n</available_skills>",
        工作目錄=str(專案根目錄),
    )
    區塊 = 提示詞組裝器(設定).組裝提示詞區塊("額外系統訊息")
    assert set(區塊) == {"stable", "context", "volatile"}
    assert 區塊["stable"].index("You are Hermes Agent") < 區塊["stable"].index("# Finishing the job")
    assert 區塊["stable"].index("# Tool-use enforcement") < 區塊["stable"].index("# Google model operational directives")
    assert "<available_skills>" in 區塊["stable"]
    assert "額外系統訊息" in 區塊["context"]
    assert "Session ID: s1" in 區塊["volatile"]
    assert "Model: gemini-2.5-flash-lite" in 區塊["volatile"]


def test_prompt_完整字串_依序串接三層():
    """確認完整 system prompt 是 stable、context、volatile 依序串接。"""
    設定 = 提示詞設定(工具名稱清單=["read_file"], 工作階段識別碼="s2")
    完整 = 提示詞組裝器(設定).組裝系統提示詞("context-marker")
    assert 完整.index("You are Hermes Agent") < 完整.index("context-marker") < 完整.index("Conversation started:")
