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
    代理執行階段(庫, 假模型供應商(), "fake", 供應商名稱="fake", 工作目錄=str(tmp_path), 使用者上下文物件=alice).執行使用者訊息("你好", 工作階段識別碼="shared")
    with pytest.raises(PermissionError):
        代理執行階段(庫, 假模型供應商(), "fake", 供應商名稱="fake", 工作目錄=str(tmp_path), 使用者上下文物件=bob).執行使用者訊息("偷看", 工作階段識別碼="shared")
    assert 庫.讀取工作階段("shared")["user_id"] == "alice"


def test_session_read_rename_archive_rewind都檢查_owner(tmp_path):
    """確認 direct read、rename、archive、rewind 都拒絕跨使用者。"""
    庫 = 工作階段庫(tmp_path / "sessions.sqlite3")
    sid = 庫.建立或讀取工作階段("owned", user_id="alice")
    庫.寫入訊息清單(sid, [{"role": "user", "content": "秘密"}, {"role": "assistant", "content": "回答"}])
    target = 庫.連線.execute("SELECT id FROM messages WHERE session_id=? ORDER BY id DESC LIMIT 1", (sid,)).fetchone()["id"]
    with pytest.raises(PermissionError):
        庫.讀取工作階段全文(sid, user_id="bob")
    with pytest.raises(PermissionError):
        庫.捲動工作階段訊息(sid, target, user_id="bob")
    with pytest.raises(PermissionError):
        庫.重新命名工作階段(sid, "bad", user_id="bob")
    with pytest.raises(PermissionError):
        庫.封存工作階段(sid, user_id="bob")
    with pytest.raises(PermissionError):
        庫.rewind到訊息(sid, target, user_id="bob")


def test_session_search_tool_忽略模型傳入_user_id並使用目前上下文(tmp_path):
    """確認 session_search tool 不能靠參數冒充其他 user。"""
    庫 = 工作階段庫(tmp_path / "sessions.sqlite3")
    sid = 庫.建立或讀取工作階段("alice-session", user_id="alice")
    庫.寫入訊息清單(sid, [{"role": "user", "content": "alice secret"}])
    設定目前使用者("bob", 建立上下文("bob", tmp_path))
    登錄器 = 建立預設工具登錄器(tmp_path, 建立上下文("bob", tmp_path, tools={"session_search"}))
    結果 = json.loads(登錄器.呼叫工具("session_search", {"session_id": sid, "user_id": "alice", "db_path": str(tmp_path / "sessions.sqlite3")}))
    assert 結果["success"] is False
    assert "無權" in 結果["error"]


def test_tool_schema與硬呼叫都依使用者權限(tmp_path):
    """確認不允許的 tool 不暴露，硬呼叫也被拒。"""
    上下文 = 建立上下文("alice", tmp_path, tools={"read_file"})
    登錄器 = 建立預設工具登錄器(tmp_path, 上下文)
    工具名稱 = {結構["function"]["name"] for 結構 in 登錄器.列出工具結構()}
    assert "read_file" in 工具名稱
    assert "terminal" not in 工具名稱
    結果 = json.loads(登錄器.呼叫工具("terminal", {"command": "pwd"}))
    assert 結果["permission_denied"] is True


def test_file與terminal工具限制_workdir(tmp_path):
    """確認檔案與 terminal 工具不能越出 allowed_workdirs。"""
    允許 = tmp_path / "allowed"
    禁止 = tmp_path / "denied"
    允許.mkdir()
    禁止.mkdir()
    (禁止 / "secret.txt").write_text("secret", encoding="utf-8")
    上下文 = 建立上下文("alice", tmp_path, tools={"read_file", "terminal"}, workdir=允許)
    登錄器 = 建立預設工具登錄器(允許, 上下文)
    讀取結果 = json.loads(登錄器.呼叫工具("read_file", {"path": str(禁止 / "secret.txt")}))
    assert 讀取結果["success"] is False and "超出" in 讀取結果["error"]
    終端結果 = json.loads(登錄器.呼叫工具("terminal", {"command": "pwd", "workdir": str(禁止)}))
    assert 終端結果["success"] is False and "超出" in 終端結果["error"]


def test_skill_prompt與skill_view依使用者隔離(tmp_path):
    """確認 prompt skill 摘要與 skill_view 都依使用者技能權限隔離。"""
    寫入技能(tmp_path / "skills", "cat", "skill_a", "A only")
