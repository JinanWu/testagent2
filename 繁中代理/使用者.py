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


class 使用者庫:
    """管理 SQLite 中的 users、user_settings 與 auth_sessions。

    參數：
        資料庫路徑: 與 session store 共用或獨立的 SQLite 檔案。

    返回值：
        可建立、驗證與解析使用者上下文的資料庫物件。
    """

    def __init__(self, 資料庫路徑: str | Path) -> None:
        """初始化使用者資料庫。

        參數：
            資料庫路徑: SQLite 檔案路徑。

        返回值：None。
        """
        self.資料庫路徑 = Path(資料庫路徑)
        self.資料庫路徑.parent.mkdir(parents=True, exist_ok=True)
        self.連線 = sqlite3.connect(self.資料庫路徑, timeout=1, isolation_level=None, check_same_thread=False)
        self.連線.row_factory = sqlite3.Row
        self.建立資料表()

    def 建立資料表(self) -> None:
        """建立使用者與登入狀態資料表。

        參數：無。
        返回值：None。
        """
        self.連線.executescript(
            """
            CREATE TABLE IF NOT EXISTS users (
                id TEXT PRIMARY KEY,
                username TEXT UNIQUE NOT NULL,
                display_name TEXT,
                password_hash TEXT,
                auth_provider TEXT NOT NULL DEFAULT 'local',
                external_subject TEXT,
                roles_json TEXT NOT NULL DEFAULT '["user"]',
                disabled INTEGER NOT NULL DEFAULT 0,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS user_settings (
                user_id TEXT PRIMARY KEY,
                enabled_tools_json TEXT,
                enabled_skills_json TEXT,
                skill_roots_json TEXT,
                allowed_workdirs_json TEXT,
                memory_home TEXT,
                settings_json TEXT,
                updated_at REAL NOT NULL,
                FOREIGN KEY(user_id) REFERENCES users(id)
            );
            CREATE TABLE IF NOT EXISTS auth_sessions (
                token_hash TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                created_at REAL NOT NULL,
                expires_at REAL,
                last_used_at REAL,
                revoked_at REAL,
                FOREIGN KEY(user_id) REFERENCES users(id)
            );
            """
        )

    def 建立使用者(
        self,
        username: str,
        password: str | None = None,
        display_name: str | None = None,
        roles: list[str] | None = None,
        enabled_tools: list[str] | None = None,
        enabled_skills: list[str] | None = None,
        skill_roots: list[str] | None = None,
        allowed_workdirs: list[str] | None = None,
        memory_home: str | None = None,
    ) -> dict[str, Any]:
        """建立本機使用者與初始權限設定。

        參數：
            username: 登入帳號。
            password: 可選密碼；未提供時建立無密碼帳號供測試或外部 provider 使用。
            display_name: 顯示名稱。
            roles: 角色清單。
            enabled_tools: 可用工具清單；`*` 或空值表示全部。
            enabled_skills: 可用技能清單；`*` 或空值表示全部。
            skill_roots: 技能根目錄清單。
            allowed_workdirs: 允許工作目錄清單。
            memory_home: 使用者記憶根目錄。

        返回值：
            新使用者資料。
        """
        帳號 = username.strip()
        if not 帳號:
            raise ValueError("username 不可為空")
        目前時間 = time.time()
        user_id = f"user-{secrets.token_hex(8)}"
        密碼雜湊 = 產生密碼雜湊(password) if password else None
        角色 = roles or ["user"]
        self.連線.execute(
            "INSERT INTO users(id, username, display_name, password_hash, roles_json, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (user_id, 帳號, display_name or 帳號, 密碼雜湊, json.dumps(角色, ensure_ascii=False), 目前時間, 目前時間),
        )
        self.連線.execute(
            "INSERT INTO user_settings(user_id, enabled_tools_json, enabled_skills_json, skill_roots_json, allowed_workdirs_json, memory_home, settings_json, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                user_id,
                json.dumps(enabled_tools or ["*"], ensure_ascii=False),
                json.dumps(enabled_skills or ["*"], ensure_ascii=False),
                json.dumps(skill_roots or [], ensure_ascii=False),
                json.dumps(allowed_workdirs or [], ensure_ascii=False),
                memory_home or str(取得預設記憶根目錄(user_id)),
                json.dumps({}, ensure_ascii=False),
                目前時間,
            ),
        )
        return self.讀取使用者(username=帳號) or {"id": user_id, "username": 帳號}

    def 讀取使用者(self, user_id: str | None = None, username: str | None = None) -> dict[str, Any] | None:
        """依 id 或 username 讀取使用者 row。

        參數：
            user_id: 使用者識別碼。
            username: 登入帳號。

        返回值：
            使用者 row dict；找不到時回傳 None。
        """
        if user_id:
            資料列 = self.連線.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
        elif username:
            資料列 = self.連線.execute("SELECT * FROM users WHERE username=?", (username,)).fetchone()
        else:
            return None
        return dict(資料列) if 資料列 else None

    def 列出使用者(self) -> list[dict[str, Any]]:
        """列出所有使用者。

        參數：無。
        返回值：使用者資料清單。
        """
        return [dict(資料列) for 資料列 in self.連線.execute("SELECT id, username, display_name, roles_json, disabled, created_at, updated_at FROM users ORDER BY username").fetchall()]

    def 設定使用者停用(self, username: str, disabled: bool) -> None:
        """啟用或停用使用者。

        參數：
            username: 登入帳號。
            disabled: True 表示停用。

        返回值：None。
        """
        結果 = self.連線.execute("UPDATE users SET disabled=?, updated_at=? WHERE username=?", (1 if disabled else 0, time.time(), username))
        if 結果.rowcount == 0:
            raise ValueError(f"找不到使用者：{username}")

    def 設定權限欄位(self, username: str, 欄位: str, 項目清單: list[str]) -> None:
        """更新 user_settings 中的 JSON 權限欄位。

        參數：
            username: 登入帳號。
            欄位: 欲更新的資料庫欄位。
            項目清單: 新權限清單。

        返回值：None。
        """
        使用者 = self.讀取使用者(username=username)
        if not 使用者:
            raise ValueError(f"找不到使用者：{username}")
        if 欄位 not in {"enabled_tools_json", "enabled_skills_json", "skill_roots_json", "allowed_workdirs_json"}:
            raise ValueError(f"不支援的權限欄位：{欄位}")
        self.連線.execute(f"UPDATE user_settings SET {欄位}=?, updated_at=? WHERE user_id=?", (json.dumps(項目清單, ensure_ascii=False), time.time(), 使用者["id"]))

    def 驗證使用者密碼(self, username: str, password: str) -> dict[str, Any]:
        """驗證帳密並回傳使用者資料。

        參數：
            username: 登入帳號。
            password: 明文密碼。

        返回值：
            使用者資料；失敗時丟出 ValueError。
        """
        使用者 = self.讀取使用者(username=username)
        if not 使用者 or 使用者.get("disabled"):
            raise ValueError("使用者不存在或已停用")
        if not 使用者.get("password_hash") or not 驗證密碼雜湊(password, str(使用者["password_hash"])):
            raise ValueError("帳號或密碼錯誤")
        return 使用者

    def 建立登入Token(self, user_id: str, expires_at: float | None = None) -> str:
        """建立本機登入 token 並保存雜湊。

        參數：
            user_id: 使用者識別碼。
            expires_at: 可選過期時間。

        返回值：
            明文 token；只會回傳一次並寫入本機 auth 檔。
        """
        token = secrets.token_urlsafe(32)
        目前時間 = time.time()
        self.連線.execute(
            "INSERT INTO auth_sessions(token_hash, user_id, created_at, expires_at, last_used_at) VALUES (?, ?, ?, ?, ?)",
            (雜湊Token(token), user_id, 目前時間, expires_at, 目前時間),
        )
        return token

    def 驗證登入Token(self, token: str) -> 使用者上下文:
        """驗證本機 token 並回傳使用者上下文。

        參數：
            token: auth.json 內保存的 token。

        返回值：
            對應使用者上下文。
        """
        資料列 = self.連線.execute(
            "SELECT * FROM auth_sessions WHERE token_hash=? AND revoked_at IS NULL",
            (雜湊Token(token),),
        ).fetchone()
        if not 資料列:
            raise ValueError("登入 token 無效")
        if 資料列["expires_at"] and float(資料列["expires_at"]) < time.time():
            raise ValueError("登入 token 已過期")
        self.連線.execute("UPDATE auth_sessions SET last_used_at=? WHERE token_hash=?", (time.time(), 資料列["token_hash"]))
        return self.建立使用者上下文(user_id=str(資料列["user_id"]))

    def 撤銷登入Token(self, token: str) -> None:
        """撤銷本機登入 token。

        參數：
            token: auth.json 內保存的 token。

        返回值：None。
        """
        self.連線.execute("UPDATE auth_sessions SET revoked_at=? WHERE token_hash=?", (time.time(), 雜湊Token(token)))

    def 建立使用者上下文(self, user_id: str | None = None, username: str | None = None, 工作目錄: str | Path | None = None) -> 使用者上下文:
        """從 users 與 user_settings 建立 runtime UserContext。

        參數：
            user_id: 使用者識別碼。
            username: 登入帳號。
            工作目錄: fallback allowed workdir。

        返回值：
            使用者上下文。
        """
        使用者 = self.讀取使用者(user_id=user_id, username=username)
        if not 使用者:
            raise ValueError("找不到使用者")
        設定 = self.連線.execute("SELECT * FROM user_settings WHERE user_id=?", (使用者["id"],)).fetchone()
        設定資料 = dict(設定) if 設定 else {}
        角色 = 解析字串清單(使用者.get("roles_json") or "[\"user\"]")
        技能根 = [Path(路徑).expanduser().resolve() for 路徑 in 解析字串清單(設定資料.get("skill_roots_json"))]
        允許目錄清單 = 解析字串清單(設定資料.get("allowed_workdirs_json"))
        if 允許目錄清單 and "*" not in 允許目錄清單:
            允許目錄 = [Path(路徑).expanduser().resolve() for 路徑 in 允許目錄清單]
        elif "*" in 允許目錄清單:
            允許目錄 = None
        else:
            允許目錄 = [Path(工作目錄 or os.getcwd()).expanduser().resolve()]
        記憶根 = Path(str(設定資料.get("memory_home") or 取得預設記憶根目錄(str(使用者["id"]))))
        return 使用者上下文(
            user_id=str(使用者["id"]),
            username=str(使用者["username"]),
            display_name=str(使用者.get("display_name") or 使用者["username"]),
            roles=角色,
            enabled_tools=正規化可選集合(解析字串清單(設定資料.get("enabled_tools_json"))),
            enabled_skills=正規化可選集合(解析字串清單(設定資料.get("enabled_skills_json"))),
            skill_roots=技能根,
            allowed_workdirs=允許目錄,
            memory_home=記憶根.expanduser().resolve(),
            is_admin="admin" in 角色,
            disabled=bool(使用者.get("disabled")),
        )
