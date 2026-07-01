"""測試個人化使用者、session、工具、技能與記憶隔離。"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from 繁中代理.代理執行階段 import 代理執行階段
from 繁中代理.工作階段上下文 import 設定目前使用者
from 繁中代理.工作階段庫 import 工作階段庫
from 繁中代理.模型供應商 import 假模型供應商
from 繁中代理.工具 import 建立預設工具登錄器
from 繁中代理.使用者 import 使用者上下文, 使用者庫

專案根目錄 = Path(__file__).resolve().parents[1]


def 建立上下文(user_id: str, tmp_path: Path, tools: set[str] | None = None, skills: set[str] | None = None, workdir: Path | None = None) -> 使用者上下文:
    """建立測試用使用者上下文。

    參數：
        user_id: 使用者識別碼。
        tmp_path: pytest 暫存目錄。
        tools: 可用工具集合；None 表示全部。
        skills: 可用技能集合；None 表示全部。
        workdir: 允許工作目錄。

    返回值：
        測試用使用者上下文。
    """
    return 使用者上下文(
        user_id=user_id,
        username=user_id,
        display_name=user_id,
        roles=["user"],
        enabled_tools=tools,
        enabled_skills=skills,
        skill_roots=[tmp_path / "skills"],
        allowed_workdirs=[workdir or tmp_path],
        memory_home=tmp_path / "memory" / user_id,
        is_admin=False,
    )


def 寫入技能(root: Path, category: str, name: str, description: str) -> None:
    """建立測試用 SKILL.md。

    參數：
        root: skills root。
        category: 分類目錄。
        name: 技能名稱。
        description: 技能描述。

    返回值：None。
    """
    路徑 = root / category / name
    路徑.mkdir(parents=True)
    (路徑 / "SKILL.md").write_text(f"---\nname: {name}\ndescription: {description}\n---\n\n# {name}\n", encoding="utf-8")


def test_session_owner_不可被其他使用者_resume或覆蓋(tmp_path):
    """確認 session owner 不會被跨使用者覆蓋。"""
    庫 = 工作階段庫(tmp_path / "sessions.sqlite3")
    alice = 建立上下文("alice", tmp_path)
    bob = 建立上下文("bob", tmp_path)
