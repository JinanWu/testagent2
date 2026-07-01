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
