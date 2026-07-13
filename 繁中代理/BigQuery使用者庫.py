"""BigQuery 版使用者庫（雲端儲存後端）。

功能：
    以 BigQuery 儲存 users / user_settings / auth_sessions，與本機 SQLite 版
    （`使用者庫`）維持相同方法介面，供 `儲存.py` 工廠切換。

    密碼雜湊、token 雜湊、使用者上下文組裝等邏輯直接沿用 `使用者.py`，此類只替換
    儲存層。使用者相關操作（註冊、登入、權限變更）頻率低，且需要「寫後即讀」，因此
    一律走 DML（非 streaming insert）以確保一致性。

環境變數：見 `環境設定.讀取核心BigQuery設定`（CORE_BQ_PROJECT 等）。
"""

from __future__ import annotations

import json
import secrets
import time
from pathlib import Path
from typing import Any

from .環境設定 import 讀取核心BigQuery設定, 應跳過建表
from .使用者 import (
    使用者上下文,
    產生密碼雜湊,
    驗證密碼雜湊,
    雜湊Token,
    解析字串清單,
    正規化可選集合,
    取得預設記憶根目錄,
    預設登入Token有效秒數,
)

使用者表 = "users"
使用者設定表 = "user_settings"
認證表 = "auth_sessions"

# 每個 process 已確保過資料表的 dataset（避免重複跑 DDL）
_已確保使用者資料表: set[str] = set()


class BigQuery使用者庫:
    """以 BigQuery 儲存 users / user_settings / auth_sessions 的使用者庫。"""

    def __init__(self, 資料庫路徑: str | Path) -> None:
        """初始化 BigQuery 使用者庫並確保資料表存在。

        參數：
            資料庫路徑: 相容參數；BigQuery 模式下不使用本機檔案。

        返回值：None。
        """
        self.資料庫路徑 = Path(資料庫路徑)
        self.設定 = 讀取核心BigQuery設定()
        self._客戶端 = None
        self.確保資料表()

    @property
    def 客戶端(self):
        """延遲建立並快取 BigQuery client。"""
        if self._客戶端 is None:
            try:
                from google.cloud import bigquery
            except ImportError as 錯誤:
                raise RuntimeError("缺少 google-cloud-bigquery，無法使用 BigQuery 儲存後端") from 錯誤
            self._客戶端 = bigquery.Client(project=self.設定["project"], location=self.設定["location"])
        return self._客戶端

    def _表名(self, 表: str) -> str:
        """回傳完整表名 `project.dataset.table`。"""
        return f"{self.設定['project']}.{self.設定['dataset']}.{表}"

    # ── DDL ──────────────────────────────────────────────────────────────
    def 確保資料表(self) -> None:
        """建立 users / user_settings / auth_sessions（若不存在）。

        每個 process 對同一 dataset 只實際執行一次 DDL。
        """
        鍵 = f"{self.設定['project']}.{self.設定['dataset']}"
        if 鍵 in _已確保使用者資料表:
            return
        if 應跳過建表():
            _已確保使用者資料表.add(鍵)
            return
        from google.cloud import bigquery

        資料集參照 = bigquery.Dataset(f"{self.設定['project']}.{self.設定['dataset']}")
        if self.設定["location"]:
            資料集參照.location = self.設定["location"]
        self.客戶端.create_dataset(資料集參照, exists_ok=True)
        for 語句 in (self._使用者表DDL(), self._使用者設定表DDL(), self._認證表DDL()):
            self.客戶端.query(語句).result()
        _已確保使用者資料表.add(鍵)

    def _使用者表DDL(self) -> str:
        """users 資料表 DDL。"""
        return f"""
        CREATE TABLE IF NOT EXISTS `{self._表名(使用者表)}` (
            id STRING NOT NULL,
            username STRING,
            display_name STRING,
            password_hash STRING,
            auth_provider STRING,
            external_subject STRING,
            roles_json STRING,
            disabled BOOL,
            created_at FLOAT64,
            updated_at FLOAT64
        )
        """

    def _使用者設定表DDL(self) -> str:
        """user_settings 資料表 DDL。"""
        return f"""
        CREATE TABLE IF NOT EXISTS `{self._表名(使用者設定表)}` (
            user_id STRING NOT NULL,
            enabled_tools_json STRING,
            enabled_skills_json STRING,
            skill_roots_json STRING,
            allowed_workdirs_json STRING,
            memory_home STRING,
            settings_json STRING,
            updated_at FLOAT64
        )
        """

    def _認證表DDL(self) -> str:
        """auth_sessions 資料表 DDL。"""
        return f"""
        CREATE TABLE IF NOT EXISTS `{self._表名(認證表)}` (
            token_hash STRING NOT NULL,
            user_id STRING NOT NULL,
            created_at FLOAT64,
            expires_at FLOAT64,
            last_used_at FLOAT64,
            revoked_at FLOAT64
        )
        """

    # ── 使用者 CRUD ───────────────────────────────────────────────────────
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
        """建立使用者與初始權限設定。"""
        from google.cloud import bigquery

        帳號 = username.strip()
        if not 帳號:
            raise ValueError("username 不可為空")
        目前時間 = time.time()
        user_id = f"user-{secrets.token_hex(8)}"
        密碼雜湊 = 產生密碼雜湊(password) if password else None
        角色 = roles or ["user"]
        # BQ 沒有 UNIQUE 約束，且 INSERT 可並發。改用 MERGE：第二個 MERGE 會看到第一個插入的列而不再插入。
        影響列數 = self._執行DML(
            f"""
            MERGE `{self._表名(使用者表)}` AS T
            USING (SELECT @username AS username) AS S
            ON T.username = S.username
            WHEN NOT MATCHED THEN
              INSERT (id, username, display_name, password_hash, auth_provider, roles_json, disabled, created_at, updated_at)
              VALUES (@id, @username, @display_name, @password_hash, 'local', @roles_json, FALSE, @now, @now)
            """,
            [
                bigquery.ScalarQueryParameter("id", "STRING", user_id),
                bigquery.ScalarQueryParameter("username", "STRING", 帳號),
                bigquery.ScalarQueryParameter("display_name", "STRING", display_name or 帳號),
                bigquery.ScalarQueryParameter("password_hash", "STRING", 密碼雜湊),
                bigquery.ScalarQueryParameter("roles_json", "STRING", json.dumps(角色, ensure_ascii=False)),
                bigquery.ScalarQueryParameter("now", "FLOAT64", 目前時間),
            ],
        )
        if 影響列數 == 0:
            raise ValueError(f"使用者已存在：{帳號}")
        self._執行DML(
            f"""
            INSERT INTO `{self._表名(使用者設定表)}`
                (user_id, enabled_tools_json, enabled_skills_json, skill_roots_json, allowed_workdirs_json, memory_home, settings_json, updated_at)
            VALUES (@user_id, @tools, @skills, @roots, @workdirs, @memory_home, @settings, @now)
            """,
            [
                bigquery.ScalarQueryParameter("user_id", "STRING", user_id),
                bigquery.ScalarQueryParameter("tools", "STRING", json.dumps(enabled_tools or ["*"], ensure_ascii=False)),
                bigquery.ScalarQueryParameter("skills", "STRING", json.dumps(enabled_skills or ["*"], ensure_ascii=False)),
                bigquery.ScalarQueryParameter("roots", "STRING", json.dumps(skill_roots or [], ensure_ascii=False)),
                bigquery.ScalarQueryParameter("workdirs", "STRING", json.dumps(allowed_workdirs or [], ensure_ascii=False)),
                bigquery.ScalarQueryParameter("memory_home", "STRING", memory_home or str(取得預設記憶根目錄(user_id))),
                bigquery.ScalarQueryParameter("settings", "STRING", json.dumps({}, ensure_ascii=False)),
                bigquery.ScalarQueryParameter("now", "FLOAT64", 目前時間),
            ],
        )
        return self.讀取使用者(username=帳號) or {"id": user_id, "username": 帳號}

    def 讀取使用者(self, user_id: str | None = None, username: str | None = None) -> dict[str, Any] | None:
        """依 id 或 username 讀取使用者。"""
        from google.cloud import bigquery

        if user_id:
            語句 = f"SELECT * FROM `{self._表名(使用者表)}` WHERE id=@v LIMIT 1"
            值 = user_id
        elif username:
            語句 = f"SELECT * FROM `{self._表名(使用者表)}` WHERE username=@v LIMIT 1"
            值 = username
        else:
            return None
        return self._查詢單列(語句, [bigquery.ScalarQueryParameter("v", "STRING", 值)])

    def 列出使用者(self) -> list[dict[str, Any]]:
        """列出所有使用者。"""
        return self._查詢多列(
            f"SELECT id, username, display_name, roles_json, disabled, created_at, updated_at "
            f"FROM `{self._表名(使用者表)}` ORDER BY username"
        )

    def 設定使用者停用(self, username: str, disabled: bool) -> None:
        """啟用或停用使用者。"""
        from google.cloud import bigquery

        影響列數 = self._執行DML(
            f"UPDATE `{self._表名(使用者表)}` SET disabled=@disabled, updated_at=@now WHERE username=@username",
            [
                bigquery.ScalarQueryParameter("disabled", "BOOL", bool(disabled)),
                bigquery.ScalarQueryParameter("now", "FLOAT64", time.time()),
                bigquery.ScalarQueryParameter("username", "STRING", username),
            ],
        )
        if not 影響列數:
            raise ValueError(f"找不到使用者：{username}")

    def 設定權限欄位(self, username: str, 欄位: str, 項目清單: list[str]) -> None:
        """更新 user_settings 中的 JSON 權限欄位。"""
        from google.cloud import bigquery

        使用者 = self.讀取使用者(username=username)
        if not 使用者:
            raise ValueError(f"找不到使用者：{username}")
        if 欄位 not in {"enabled_tools_json", "enabled_skills_json", "skill_roots_json", "allowed_workdirs_json"}:
            raise ValueError(f"不支援的權限欄位：{欄位}")
        self._執行DML(
            f"UPDATE `{self._表名(使用者設定表)}` SET {欄位}=@val, updated_at=@now WHERE user_id=@user_id",
            [
                bigquery.ScalarQueryParameter("val", "STRING", json.dumps(項目清單, ensure_ascii=False)),
                bigquery.ScalarQueryParameter("now", "FLOAT64", time.time()),
                bigquery.ScalarQueryParameter("user_id", "STRING", 使用者["id"]),
            ],
        )

    def 驗證使用者密碼(self, username: str, password: str) -> dict[str, Any]:
        """驗證帳密並回傳使用者資料。"""
        使用者 = self.讀取使用者(username=username)
        if not 使用者 or 使用者.get("disabled"):
            raise ValueError("使用者不存在或已停用")
        if not 使用者.get("password_hash") or not 驗證密碼雜湊(password, str(使用者["password_hash"])):
            raise ValueError("帳號或密碼錯誤")
        return 使用者

    # ── 登入 token ────────────────────────────────────────────────────────
    def 建立登入Token(self, user_id: str, expires_at: float | None = None) -> str:
        """建立登入 token 並保存雜湊。"""
        from google.cloud import bigquery

        token = secrets.token_urlsafe(32)
        目前時間 = time.time()
        if expires_at is None:
            expires_at = 目前時間 + 預設登入Token有效秒數
        elif expires_at == 0:
            expires_at = None
        self._執行DML(
            f"""
            INSERT INTO `{self._表名(認證表)}` (token_hash, user_id, created_at, expires_at, last_used_at)
            VALUES (@token_hash, @user_id, @now, @expires_at, @now)
            """,
            [
                bigquery.ScalarQueryParameter("token_hash", "STRING", 雜湊Token(token)),
                bigquery.ScalarQueryParameter("user_id", "STRING", user_id),
                bigquery.ScalarQueryParameter("now", "FLOAT64", 目前時間),
                bigquery.ScalarQueryParameter("expires_at", "FLOAT64", expires_at),
            ],
        )
        return token

    def 驗證登入Token(self, token: str) -> 使用者上下文:
        """驗證 token 並回傳使用者上下文。"""
        from google.cloud import bigquery

        資料列 = self._查詢單列(
            f"SELECT * FROM `{self._表名(認證表)}` WHERE token_hash=@token_hash AND revoked_at IS NULL LIMIT 1",
            [bigquery.ScalarQueryParameter("token_hash", "STRING", 雜湊Token(token))],
        )
        if not 資料列:
            raise ValueError("登入 token 無效")
        if 資料列.get("expires_at") and float(資料列["expires_at"]) < time.time():
            raise ValueError("登入 token 已過期")
        self._執行DML(
            f"UPDATE `{self._表名(認證表)}` SET last_used_at=@now WHERE token_hash=@token_hash",
            [
                bigquery.ScalarQueryParameter("now", "FLOAT64", time.time()),
                bigquery.ScalarQueryParameter("token_hash", "STRING", str(資料列["token_hash"])),
            ],
        )
        return self.建立使用者上下文(user_id=str(資料列["user_id"]))

    def 撤銷登入Token(self, token: str) -> None:
        """撤銷登入 token。"""
        from google.cloud import bigquery

        self._執行DML(
            f"UPDATE `{self._表名(認證表)}` SET revoked_at=@now WHERE token_hash=@token_hash",
            [
                bigquery.ScalarQueryParameter("now", "FLOAT64", time.time()),
                bigquery.ScalarQueryParameter("token_hash", "STRING", 雜湊Token(token)),
            ],
        )

    # ── 使用者上下文 ──────────────────────────────────────────────────────
    def 建立使用者上下文(self, user_id: str | None = None, username: str | None = None, 工作目錄: str | Path | None = None) -> 使用者上下文:
        """從 users 與 user_settings 組出 runtime 使用者上下文。"""
        import os
        from google.cloud import bigquery

        使用者 = self.讀取使用者(user_id=user_id, username=username)
        if not 使用者:
            raise ValueError("找不到使用者")
        設定資料 = self._查詢單列(
            f"SELECT * FROM `{self._表名(使用者設定表)}` WHERE user_id=@id LIMIT 1",
            [bigquery.ScalarQueryParameter("id", "STRING", 使用者["id"])],
        ) or {}
        角色 = 解析字串清單(使用者.get("roles_json") or '["user"]')
        技能根清單 = 解析字串清單(設定資料.get("skill_roots_json"))
        技能根 = None if "*" in 技能根清單 else [Path(路徑).expanduser().resolve() for 路徑 in 技能根清單]
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

    # ── BigQuery 查詢小工具 ────────────────────────────────────────────────
    def _查詢單列(self, 語句: str, 參數: list[Any] | None = None) -> dict[str, Any] | None:
        """執行查詢並回傳第一列 dict；無資料回傳 None。"""
        from google.cloud import bigquery

        工作設定 = bigquery.QueryJobConfig(query_parameters=參數 or [])
        for 列 in self.客戶端.query(語句, job_config=工作設定).result():
            return dict(列)
        return None

    def _查詢多列(self, 語句: str, 參數: list[Any] | None = None) -> list[dict[str, Any]]:
        """執行查詢並回傳所有列。"""
        from google.cloud import bigquery

        工作設定 = bigquery.QueryJobConfig(query_parameters=參數 or [])
        return [dict(列) for 列 in self.客戶端.query(語句, job_config=工作設定).result()]

    def _執行DML(self, 語句: str, 參數: list[Any] | None = None) -> int:
        """執行 DML 並回傳受影響列數。"""
        from google.cloud import bigquery

        工作設定 = bigquery.QueryJobConfig(query_parameters=參數 or [])
        工作 = self.客戶端.query(語句, job_config=工作設定)
        工作.result()
        return int(工作.num_dml_affected_rows or 0)
