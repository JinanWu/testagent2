"""PostgreSQL users、CLI auth sessions 與權限設定 repository。"""
from __future__ import annotations

import os
import secrets
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from psycopg.types.json import Jsonb

from . import PostgreSQL連線
from .環境設定 import 交易儲存設定
from .使用者 import (
    使用者上下文,
    取得預設記憶根目錄,
    權限更新錯誤,
    正規化可選集合,
    產生密碼雜湊,
    解析字串清單,
    驗證密碼雜湊,
    雜湊Token,
    預設登入Token有效秒數,
    _正規化權限清單,
    _解析權限JSON,
)

_權限欄位 = {
    "enabled_tools_json": "enabled_tools",
    "enabled_skills_json": "enabled_skills",
    "skill_roots_json": "skill_roots",
    "allowed_workdirs_json": "allowed_workdirs",
}


def _設定有效(設定: object) -> bool:
    return type(設定) is 交易儲存設定 and 設定.後端 == "postgres"


def _列轉字典(列: Any) -> dict[str, Any] | None:
    if 列 is None:
        return None
    if isinstance(列, dict):
        結果 = dict(列)
        for 欄位, 值 in tuple(結果.items()):
            if isinstance(值, datetime):
                if 值.tzinfo is None or 值.utcoffset() is None:
                    raise RuntimeError("PostgreSQL 時間資料格式無效")
                結果[欄位] = 值.timestamp()
        return 結果
    raise RuntimeError("PostgreSQL 使用者資料格式無效")


def _時間戳(值: float) -> datetime:
    return datetime.fromtimestamp(值, timezone.utc)


def _字串清單(值: Any) -> list[str]:
    if isinstance(值, list):
        return [str(項目).strip() for 項目 in 值 if str(項目).strip()]
    return 解析字串清單(值)


class PostgreSQL使用者庫:
    """以 request-local PostgreSQL transaction 實作 ``使用者庫`` 公開介面。"""

    def __init__(self, 凍結設定: 交易儲存設定) -> None:
        if not _設定有效(凍結設定):
            raise ValueError("PostgreSQL 使用者庫設定無效")
        self.設定 = 凍結設定

    def 建立資料表(self) -> None:
        """Schema 由 migration 管理；保留 SQLite 相容方法但不執行 DDL。"""
        return None

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
        帳號 = username.strip()
        if not 帳號:
            raise ValueError("username 不可為空")
        現在 = _時間戳(time.time())
        user_id = f"user-{secrets.token_hex(8)}"
        密碼雜湊 = 產生密碼雜湊(password) if password else None
        with PostgreSQL連線.交易連線(self.設定) as 連線:
            with 連線.cursor() as 游標:
                try:
                    游標.execute(
                        "INSERT INTO users(id,username,display_name,password_hash,auth_provider,roles,disabled,created_at,updated_at) "
                        "VALUES(%s,%s,%s,%s,'local',%s,FALSE,%s,%s)",
                        (user_id, 帳號, display_name or 帳號, 密碼雜湊,
                         Jsonb(roles or ["user"]), 現在, 現在),
                    )
                    游標.execute(
                        "INSERT INTO user_settings(user_id,enabled_tools,enabled_skills,skill_roots,"
                        "allowed_workdirs,memory_home,settings,updated_at) VALUES(%s,%s,%s,%s,%s,%s,%s,%s)",
                        (user_id, Jsonb(enabled_tools or ["*"]), Jsonb(enabled_skills or ["*"]),
                         Jsonb(skill_roots or ["*"]), Jsonb(allowed_workdirs or []),
                         memory_home or str(取得預設記憶根目錄(user_id)), Jsonb({}), 現在),
                    )
                except Exception as 錯誤:
                    # PostgreSQL UNIQUE violations expose SQLSTATE 23505; never include driver text.
                    if getattr(錯誤, "sqlstate", None) == "23505":
                        raise ValueError(f"使用者已存在：{帳號}") from None
                    raise
                游標.execute(
                    "SELECT id,username,display_name,password_hash,auth_provider,external_subject,"
                    "roles::text AS roles_json,disabled,created_at,updated_at FROM users WHERE id=%s", (user_id,),
                )
                return _列轉字典(游標.fetchone()) or {"id": user_id, "username": 帳號}

    def 讀取使用者(self, user_id: str | None = None, username: str | None = None) -> dict[str, Any] | None:
        if user_id:
            語句, 參數 = ("SELECT id,username,display_name,password_hash,auth_provider,external_subject,"
                         "roles::text AS roles_json,disabled,created_at,updated_at FROM users WHERE id=%s"), (user_id,)
        elif username:
            語句, 參數 = ("SELECT id,username,display_name,password_hash,auth_provider,external_subject,"
                         "roles::text AS roles_json,disabled,created_at,updated_at FROM users WHERE username=%s"), (username,)
        else:
            return None
        with PostgreSQL連線.交易連線(self.設定) as 連線:
            with 連線.cursor() as 游標:
                游標.execute(語句, 參數)
                return _列轉字典(游標.fetchone())

    def 列出使用者(self) -> list[dict[str, Any]]:
        with PostgreSQL連線.交易連線(self.設定) as 連線:
            with 連線.cursor() as 游標:
                游標.execute(
                    "SELECT id,username,display_name,roles::text AS roles_json,disabled,created_at,updated_at FROM users ORDER BY username"
                )
                return [_列轉字典(列) or {} for 列 in 游標.fetchall()]

    def 設定使用者停用(self, username: str, disabled: bool) -> None:
        with PostgreSQL連線.交易連線(self.設定) as 連線:
            with 連線.cursor() as 游標:
                游標.execute(
                    "UPDATE users SET disabled=%s,updated_at=%s WHERE username=%s",
                    (bool(disabled), _時間戳(time.time()), username),
                )
                if 游標.rowcount == 0:
                    raise ValueError(f"找不到使用者：{username}")

    def 設定權限欄位(self, username: str, 欄位: str, 項目清單: list[str]) -> None:
        if type(username) is not str or not username.strip():
            raise ValueError("username 不可為空")
        if type(欄位) is not str or 欄位 not in _權限欄位:
            raise ValueError(f"不支援的權限欄位：{欄位}")
        新項目 = _正規化權限清單(項目清單)
        資料庫欄位 = _權限欄位[欄位]
        新JSON = Jsonb(list(新項目))
        try:
            with PostgreSQL連線.交易連線(self.設定) as 連線:
                with 連線.cursor() as 游標:
                    # Identifier comes only from the frozen allow-list above.
                    游標.execute(
                        f"SELECT u.id,s.{資料庫欄位} AS {欄位} FROM users u JOIN user_settings s ON s.user_id=u.id "
                        "WHERE u.username=%s FOR UPDATE OF s",
                        (username,),
                    )
                    列 = 游標.fetchone()
                    if 列 is None:
                        raise LookupError
                    if not isinstance(列, dict):
                        raise RuntimeError
                    user_id = str(列["id"])
                    舊JSON = 列[欄位]
                    if isinstance(舊JSON, list):
                        _正規化權限清單(舊JSON)
                        舊資料庫值 = Jsonb(舊JSON)
                    else:
                        _解析權限JSON(舊JSON)
                        舊資料庫值 = Jsonb(_字串清單(舊JSON))
                    游標.execute(
                        f"UPDATE user_settings SET {資料庫欄位}=%s,updated_at=%s "
                        f"WHERE user_id=%s AND {資料庫欄位} IS NOT DISTINCT FROM %s",
                        (新JSON, _時間戳(time.time()), user_id, 舊資料庫值),
                    )
                    if 游標.rowcount != 1:
                        raise RuntimeError
        except LookupError:
            raise ValueError("找不到使用者") from None
        except (KeyboardInterrupt, SystemExit, GeneratorExit):
            raise
        except BaseException:
            raise 權限更新錯誤("權限更新失敗") from None

    def 驗證使用者密碼(self, username: str, password: str) -> dict[str, Any]:
        使用者 = self.讀取使用者(username=username)
        if not 使用者 or 使用者.get("disabled"):
            raise ValueError("使用者不存在或已停用")
        儲存值 = 使用者.get("password_hash")
        if not 儲存值 or not 驗證密碼雜湊(password, str(儲存值)):
            raise ValueError("帳號或密碼錯誤")
        return 使用者

    def 建立登入Token(self, user_id: str, expires_at: float | None = None) -> str:
        token = secrets.token_urlsafe(32)
        現在 = time.time()
        到期 = 現在 + 預設登入Token有效秒數 if expires_at is None else (None if expires_at == 0 else expires_at)
        with PostgreSQL連線.交易連線(self.設定) as 連線:
            with 連線.cursor() as 游標:
                游標.execute(
                    "INSERT INTO auth_sessions(token_hash,user_id,created_at,expires_at,last_used_at) VALUES(%s,%s,%s,%s,%s)",
                    (雜湊Token(token), user_id, _時間戳(現在),
                     _時間戳(到期) if 到期 is not None else None, _時間戳(現在)),
                )
        return token

    def 驗證登入Token(self, token: str) -> 使用者上下文:
        token雜湊 = 雜湊Token(token)
        現在 = time.time()
        with PostgreSQL連線.交易連線(self.設定) as 連線:
            with 連線.cursor() as 游標:
                游標.execute(
                    "SELECT user_id,expires_at FROM auth_sessions WHERE token_hash=%s AND revoked_at IS NULL FOR UPDATE",
                    (token雜湊,),
                )
                列 = _列轉字典(游標.fetchone())
                if not 列:
                    raise ValueError("登入 token 無效")
                if 列.get("expires_at") is not None and float(列["expires_at"]) < 現在:
                    raise ValueError("登入 token 已過期")
                游標.execute(
                    "UPDATE auth_sessions SET last_used_at=%s WHERE token_hash=%s",
                    (_時間戳(現在), token雜湊),
                )
                user_id = str(列["user_id"])
        return self.建立使用者上下文(user_id=user_id)

    def 撤銷登入Token(self, token: str) -> None:
        with PostgreSQL連線.交易連線(self.設定) as 連線:
            with 連線.cursor() as 游標:
                游標.execute(
                    "UPDATE auth_sessions SET revoked_at=%s WHERE token_hash=%s AND revoked_at IS NULL",
                    (_時間戳(time.time()), 雜湊Token(token)),
                )

    def 建立使用者上下文(
        self, user_id: str | None = None, username: str | None = None,
        工作目錄: str | Path | None = None,
    ) -> 使用者上下文:
        if user_id:
            條件, 值 = "u.id=%s", user_id
        elif username:
            條件, 值 = "u.username=%s", username
        else:
            raise ValueError("找不到使用者")
        with PostgreSQL連線.交易連線(self.設定) as 連線:
            with 連線.cursor() as 游標:
                游標.execute(
                    "SELECT u.id,u.username,u.display_name,u.password_hash,u.auth_provider,u.external_subject,"
                    "u.roles::text AS roles_json,u.disabled,u.created_at,u.updated_at,"
                    "s.enabled_tools AS enabled_tools_json,s.enabled_skills AS enabled_skills_json,"
                    "s.skill_roots AS skill_roots_json,s.allowed_workdirs AS allowed_workdirs_json,s.memory_home "
                    "FROM users u LEFT JOIN user_settings s ON s.user_id=u.id "
                    f"WHERE {條件}", (值,),
                )
                資料 = _列轉字典(游標.fetchone())
        if not 資料:
            raise ValueError("找不到使用者")
        角色 = _字串清單(資料.get("roles_json") or ["user"])
        技能清單 = _字串清單(資料.get("skill_roots_json"))
        技能根 = None if "*" in 技能清單 else [Path(p).expanduser().resolve() for p in 技能清單]
        目錄清單 = _字串清單(資料.get("allowed_workdirs_json"))
        if 目錄清單 and "*" not in 目錄清單:
            允許目錄 = [Path(p).expanduser().resolve() for p in 目錄清單]
        elif "*" in 目錄清單:
            允許目錄 = None
        else:
            允許目錄 = [Path(工作目錄 or os.getcwd()).expanduser().resolve()]
        記憶根 = Path(str(資料.get("memory_home") or 取得預設記憶根目錄(str(資料["id"]))))
        return 使用者上下文(
            user_id=str(資料["id"]), username=str(資料["username"]),
            display_name=str(資料.get("display_name") or 資料["username"]), roles=角色,
            enabled_tools=正規化可選集合(_字串清單(資料.get("enabled_tools_json"))),
            enabled_skills=正規化可選集合(_字串清單(資料.get("enabled_skills_json"))),
            skill_roots=技能根, allowed_workdirs=允許目錄,
            memory_home=記憶根.expanduser().resolve(), is_admin="admin" in 角色,
            disabled=bool(資料.get("disabled")),
        )
