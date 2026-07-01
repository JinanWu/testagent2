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
        pass
    return [項目.strip() for 項目 in 原始值.split(",") if 項目.strip()]


def 正規化可選集合(項目清單: list[str]) -> set[str] | None:
    """把 `*` 或空白清單轉成不限制集合。

    參數：
        項目清單: 權限項目清單。

    返回值：
        None 表示不限制；否則回傳項目集合。
    """
    if not 項目清單 or "*" in 項目清單:
        return None
    return set(項目清單)


def 取得預設記憶根目錄(user_id: str) -> Path:
    """取得使用者預設記憶根目錄。

    參數：
        user_id: 使用者識別碼。

    返回值：
        `.testagent2/users/<user_id>` 絕對路徑。
    """
    return Path.home().expanduser().resolve() / ".testagent2" / "users" / user_id


def 建立預設使用者上下文(工作目錄: str | Path | None = None) -> 使用者上下文:
    """建立未登入時的本機管理者上下文。

    參數：
        工作目錄: 目前工作目錄；會加入 allowed_workdirs 以利單機開發。

    返回值：
        local/admin 使用者上下文。
    """
    允許目錄 = [Path(工作目錄 or os.getcwd()).expanduser().resolve()]
    return 使用者上下文(
        user_id=預設使用者識別碼,
        username=預設使用者名稱,
        display_name="Local User",
        roles=["admin"],
        enabled_tools=None,
        enabled_skills=None,
        skill_roots=[],
        allowed_workdirs=允許目錄,
        memory_home=None,
        is_admin=True,
        disabled=False,
    )


def 產生密碼雜湊(密碼: str, salt: bytes | None = None) -> str:
    """產生 PBKDF2 密碼雜湊字串。

    參數：
        密碼: 使用者輸入的明文密碼。
        salt: 可選 salt；未提供時自動產生。

    返回值：
        `pbkdf2_sha256$iterations$salt$hash` 格式字串。
    """
    salt = salt or secrets.token_bytes(16)
    雜湊 = hashlib.pbkdf2_hmac("sha256", 密碼.encode("utf-8"), salt, 預設密碼迭代次數)
    return "$".join([
        "pbkdf2_sha256",
        str(預設密碼迭代次數),
        base64.urlsafe_b64encode(salt).decode("ascii"),
        base64.urlsafe_b64encode(雜湊).decode("ascii"),
    ])


def 驗證密碼雜湊(密碼: str, 儲存值: str) -> bool:
    """驗證明文密碼是否符合儲存雜湊。

    參數：
        密碼: 使用者輸入的明文密碼。
        儲存值: 資料庫中的 PBKDF2 雜湊字串。

    返回值：
        True 表示密碼正確。
    """
    try:
        演算法, 迭代文字, salt文字, 雜湊文字 = 儲存值.split("$", 3)
        if 演算法 != "pbkdf2_sha256":
            return False
        salt = base64.urlsafe_b64decode(salt文字.encode("ascii"))
        期望 = base64.urlsafe_b64decode(雜湊文字.encode("ascii"))
        實際 = hashlib.pbkdf2_hmac("sha256", 密碼.encode("utf-8"), salt, int(迭代文字))
        return hmac.compare_digest(實際, 期望)
    except Exception:
        return False


def 雜湊Token(token: str) -> str:
    """把 auth token 轉成資料庫可保存的 SHA256 值。

    參數：
        token: 本機登入 token。

    返回值：
        十六進位 SHA256 字串。
    """
    return hashlib.sha256(token.encode("utf-8")).hexdigest()
