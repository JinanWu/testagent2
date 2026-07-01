"""使用者身份、權限設定與 CLI 本機登入狀態。

功能：
    提供 `使用者上下文`、SQLite 使用者資料表、密碼雜湊、本機 auth token
    驗證，以及將使用者權限轉成 runtime 可用上下文的工具函式。
"""

from __future__ import annotations

import base64
import getpass
import hashlib
import hmac
import json
import os
import secrets
import sqlite3
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


預設使用者識別碼 = "local"
預設使用者名稱 = "local"
預設密碼迭代次數 = 200_000


@dataclass
class 使用者上下文:
    """描述 runtime 內一致使用的使用者身份與權限。

    參數：
        user_id: 內部使用者識別碼，會寫入 session 與審計欄位。
        username: 登入帳號或本機預設帳號。
        display_name: 顯示名稱。
        roles: 角色清單；包含 admin 時可執行管理動作。
        enabled_tools: 可用工具名稱集合；None 表示允許全部工具。
        enabled_skills: 可用技能名稱集合；None 表示允許全部技能。
        skill_roots: 可讀技能根目錄。
        allowed_workdirs: 可操作檔案與 terminal 工作目錄；None 表示不限制。
        memory_home: 此使用者的記憶檔根目錄。
        is_admin: 是否具備管理權限。
        disabled: 使用者是否停用。

    返回值：
        可傳入 AgentRuntime、工具登錄器與提示詞組裝流程的使用者上下文。
    """

    user_id: str = 預設使用者識別碼
    username: str = 預設使用者名稱
    display_name: str = "Local User"
    roles: list[str] = field(default_factory=lambda: ["admin"])
    enabled_tools: set[str] | None = None
    enabled_skills: set[str] | None = None
    skill_roots: list[Path] = field(default_factory=list)
    allowed_workdirs: list[Path] | None = None
    memory_home: Path | None = None
    is_admin: bool = True
    disabled: bool = False

    def 工具是否允許(self, 名稱: str) -> bool:
        """判斷工具是否可暴露或執行。

        參數：
            名稱: 工具名稱。

        返回值：
            True 表示此使用者可使用該工具。
        """
        return self.enabled_tools is None or 名稱 in self.enabled_tools

    def 技能是否允許(self, 名稱: str) -> bool:
        """判斷技能是否可被列出或讀取。

        參數：
            名稱: 技能名稱。

        返回值：
            True 表示此使用者可讀取該技能。
        """
        return self.enabled_skills is None or 名稱 in self.enabled_skills

    def 序列化(self) -> dict[str, Any]:
        """轉成可保存或輸出的 dict。

        參數：無。
        返回值：包含身份與權限設定的 dict。
        """
        return {
            "user_id": self.user_id,
            "username": self.username,
            "display_name": self.display_name,
            "roles": self.roles,
            "enabled_tools": sorted(self.enabled_tools) if self.enabled_tools is not None else None,
            "enabled_skills": sorted(self.enabled_skills) if self.enabled_skills is not None else None,
            "skill_roots": [str(路徑) for 路徑 in self.skill_roots],
            "allowed_workdirs": [str(路徑) for 路徑 in self.allowed_workdirs] if self.allowed_workdirs is not None else None,
            "memory_home": str(self.memory_home) if self.memory_home else None,
            "is_admin": self.is_admin,
            "disabled": self.disabled,
        }


def 解析字串清單(原始值: str | None) -> list[str]:
    """解析逗號分隔或 JSON array 字串。

    參數：
        原始值: SQLite 中保存的字串。

    返回值：
        去除空白後的字串清單。
    """
    if not 原始值:
        return []
    try:
        資料 = json.loads(原始值)
        if isinstance(資料, list):
            return [str(項目).strip() for 項目 in 資料 if str(項目).strip()]
    except json.JSONDecodeError:
