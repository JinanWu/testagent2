"""測試 Hermes prompt 與 core tool schema 的來源一致性。"""

import json
import sys
from pathlib import Path

import pytest

from 繁中代理.工具註冊 import 建立預設工具登錄器
from 繁中代理.提示詞常數 import 完成任務指引, 工具使用強制指引, 壓縮摘要前綴


def test_prompt_常數_使用_hermes_原文():
    """確認關鍵提示詞常數與 Hermes 原始碼匯出的文字一致。"""
    Hermes原始碼路徑 = Path("/Users/wujinan/Documents/hermes-agent")
    if not Hermes原始碼路徑.exists():
        pytest.skip("本機沒有 Hermes 原始碼 checkout，略過原文 parity 測試")
    sys.path.insert(0, str(Hermes原始碼路徑))
    from agent import context_compressor as hermes壓縮器
    from agent import prompt_builder as hermes提示詞

    assert 完成任務指引 == hermes提示詞.TASK_COMPLETION_GUIDANCE
    assert 工具使用強制指引 == hermes提示詞.TOOL_USE_ENFORCEMENT_GUIDANCE
    assert 壓縮摘要前綴 == hermes壓縮器.SUMMARY_PREFIX


def test_core_tool_schema_完整載入_hermes_核心工具():
    """確認本專案載入 Hermes 48 個 core tool schema，並額外載入專案自訂工具。"""
    結構路徑 = Path("assets/hermes_core_tool_schemas.json")
    結構清單 = json.loads(結構路徑.read_text(encoding="utf-8"))
    自訂結構路徑 = Path("assets/hermes_custom_tool_schemas.json")
    自訂結構清單 = json.loads(自訂結構路徑.read_text(encoding="utf-8"))
    登錄器 = 建立預設工具登錄器()
    assert len(結構清單) == 48
    assert len(登錄器.工具表) == len(結構清單) + len(自訂結構清單)
    for 名稱 in ["read_file", "write_file", "patch", "search_files", "terminal", "skill_view", "memory", "session_search", "delegate_task"]:
        assert 名稱 in 登錄器.工具表
    assert "administrative_search" in 登錄器.工具表
    assert 登錄器.工具表["read_file"].說明 == 結構清單[5]["schema"]["description"]
