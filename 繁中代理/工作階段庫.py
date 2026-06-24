"""SQLite 工作階段儲存層。

功能：
    提供 Hermes-style session persistence。system prompt 作為 sessions 快照保存，
    不再作為 persisted transcript message；壓縮成功時會結束舊 session、建立
    parent_session_id 指向舊 session 的新 session，並把 compressed messages 寫入
    新 session，保留舊 session 完整原始歷史。
"""

from __future__ import annotations

import json
import sqlite3
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator


class 工作階段庫:
    """管理 SQLite 內的會話、訊息與壓縮鎖。"""

    def __init__(self, 資料庫路徑: str | Path) -> None:
        """初始化工作階段庫並建立必要資料表。"""
        self.資料庫路徑 = Path(資料庫路徑)
        self.資料庫路徑.parent.mkdir(parents=True, exist_ok=True)
        self.連線 = sqlite3.connect(self.資料庫路徑, timeout=30)
        self.連線.row_factory = sqlite3.Row
        self.建立資料表()

    def 建立資料表(self) -> None:
        """建立 sessions、messages 與 compression_locks 資料表並補齊舊 schema。"""
        self.連線.executescript(
            """
            CREATE TABLE IF NOT EXISTS sessions (
                id TEXT PRIMARY KEY,
                title TEXT,
                system_prompt TEXT,
                parent_session_id TEXT,
                end_reason TEXT,
                compressed_from_session_id TEXT,
                prompt_tokens INTEGER DEFAULT 0,
                compression_count INTEGER DEFAULT 0,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL,
                ended_at REAL
            );
            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL,
                message_index INTEGER NOT NULL,
                role TEXT NOT NULL,
                content_json TEXT NOT NULL,
                created_at REAL NOT NULL,
                FOREIGN KEY(session_id) REFERENCES sessions(id)
            );
            CREATE UNIQUE INDEX IF NOT EXISTS idx_messages_session_index
            ON messages(session_id, message_index);
            CREATE TABLE IF NOT EXISTS compression_locks (
                session_id TEXT PRIMARY KEY,
                owner TEXT NOT NULL,
                acquired_at REAL NOT NULL,
                expires_at REAL NOT NULL
            );
            """
        )
        self.補齊Sessions欄位()
        self.連線.commit()

    def 補齊Sessions欄位(self) -> None:
        """對既有 SQLite 檔補齊新欄位。"""
        欄位 = {列["name"] for 列 in self.連線.execute("PRAGMA table_info(sessions)").fetchall()}
        欄位定義 = {
            "parent_session_id": "TEXT",
            "end_reason": "TEXT",
            "compressed_from_session_id": "TEXT",
            "prompt_tokens": "INTEGER DEFAULT 0",
            "compression_count": "INTEGER DEFAULT 0",
            "ended_at": "REAL",
        }
        for 名稱, 定義 in 欄位定義.items():
            if 名稱 not in 欄位:
                self.連線.execute(f"ALTER TABLE sessions ADD COLUMN {名稱} {定義}")

    def 建立或讀取工作階段(self, 工作階段識別碼: str | None = None, parent_session_id: str | None = None) -> str:
        """建立新工作階段或確認既有工作階段存在。"""
        目前時間 = time.time()
        識別碼 = 工作階段識別碼 or f"session-{uuid.uuid4().hex[:12]}"
        self.連線.execute(
            "INSERT OR IGNORE INTO sessions(id, title, parent_session_id, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
            (識別碼, 識別碼, parent_session_id, 目前時間, 目前時間),
        )
        self.連線.commit()
        return 識別碼

    def 建立壓縮後工作階段(self, 舊工作階段識別碼: str, 壓縮訊息清單: list[dict[str, Any]], 系統提示詞: str) -> str:
        """結束舊 session 並建立載入 compressed messages 的新 session。"""
        舊工作階段 = self.讀取工作階段(舊工作階段識別碼) or {}
        新識別碼 = f"session-{uuid.uuid4().hex[:12]}"
        目前時間 = time.time()
        with self.連線:
            self.連線.execute(
                "UPDATE sessions SET end_reason='compression', ended_at=?, updated_at=? WHERE id=?",
                (目前時間, 目前時間, 舊工作階段識別碼),
            )
            self.連線.execute(
                """
                INSERT INTO sessions(
                    id, title, system_prompt, parent_session_id, compressed_from_session_id,
                    prompt_tokens, compression_count, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    新識別碼,
                    舊工作階段.get("title") or 新識別碼,
                    系統提示詞,
                    舊工作階段識別碼,
                    舊工作階段識別碼,
                    舊工作階段.get("prompt_tokens") or 0,
                    int(舊工作階段.get("compression_count") or 0) + 1,
                    目前時間,
                    目前時間,
                ),
            )
            self.寫入訊息清單(新識別碼, 壓縮訊息清單, 是否使用既有交易=True)
        return 新識別碼

    def 讀取工作階段(self, 工作階段識別碼: str) -> dict[str, Any] | None:
        """讀取單一工作階段資料。"""
        資料列 = self.連線.execute("SELECT * FROM sessions WHERE id=?", (工作階段識別碼,)).fetchone()
        return dict(資料列) if 資料列 else None

    def 更新系統提示詞(self, 工作階段識別碼: str, 系統提示詞: str) -> None:
        """把穩定的 system prompt 快照寫回 session row。"""
        self.連線.execute(
            "UPDATE sessions SET system_prompt=?, updated_at=? WHERE id=?",
            (系統提示詞, time.time(), 工作階段識別碼),
        )
        self.連線.commit()

    def 更新提示Token數(self, 工作階段識別碼: str, token數: int) -> None:
        """保存 provider prompt token usage，方便觀測壓縮判斷。"""
        self.連線.execute(
            "UPDATE sessions SET prompt_tokens=?, updated_at=? WHERE id=?",
            (token數, time.time(), 工作階段識別碼),
        )
        self.連線.commit()

    def 讀取訊息(self, 工作階段識別碼: str) -> list[dict[str, Any]]:
        """依序讀取某工作階段的 canonical messages。"""
        資料列清單 = self.連線.execute(
            "SELECT content_json FROM messages WHERE session_id=? ORDER BY message_index",
            (工作階段識別碼,),
        ).fetchall()
        return [json.loads(資料列["content_json"]) for 資料列 in 資料列清單]

    def 寫入訊息清單(self, 工作階段識別碼: str, 訊息清單: list[dict[str, Any]], 是否使用既有交易: bool = False) -> None:
        """以目前 working messages 覆寫 session 的訊息快照。"""
        def 寫入() -> None:
            """在目前交易中覆寫 messages。"""
            self.連線.execute("DELETE FROM messages WHERE session_id=?", (工作階段識別碼,))
            for 索引, 訊息 in enumerate(訊息清單):
                self.連線.execute(
                    "INSERT INTO messages(session_id, message_index, role, content_json, created_at) VALUES (?, ?, ?, ?, ?)",
                    (工作階段識別碼, 索引, 訊息.get("role", ""), json.dumps(訊息, ensure_ascii=False), time.time()),
                )
            self.連線.execute("UPDATE sessions SET updated_at=? WHERE id=?", (time.time(), 工作階段識別碼))

        if 是否使用既有交易:
            寫入()
        else:
            with self.連線:
                寫入()

    def 取得壓縮鎖(self, 工作階段識別碼: str, 擁有者: str | None = None, ttl秒: int = 120) -> str | None:
        """取得 DB-backed compression lock；失敗回傳 None。"""
        擁有者 = 擁有者 or f"owner-{uuid.uuid4().hex[:8]}"
        目前時間 = time.time()
        過期時間 = 目前時間 + ttl秒
        with self.連線:
            self.連線.execute("DELETE FROM compression_locks WHERE expires_at < ?", (目前時間,))
            try:
                self.連線.execute(
                    "INSERT INTO compression_locks(session_id, owner, acquired_at, expires_at) VALUES (?, ?, ?, ?)",
                    (工作階段識別碼, 擁有者, 目前時間, 過期時間),
                )
            except sqlite3.IntegrityError:
                return None
        return 擁有者

    def 釋放壓縮鎖(self, 工作階段識別碼: str, 擁有者: str) -> None:
        """釋放 compression lock。"""
        with self.連線:
            self.連線.execute("DELETE FROM compression_locks WHERE session_id=? AND owner=?", (工作階段識別碼, 擁有者))

    @contextmanager
    def 壓縮鎖(self, 工作階段識別碼: str, ttl秒: int = 120) -> Iterator[bool]:
        """以 context manager 使用 compression lock。"""
        擁有者 = self.取得壓縮鎖(工作階段識別碼, ttl秒=ttl秒)
        try:
            yield 擁有者 is not None
        finally:
            if 擁有者:
                self.釋放壓縮鎖(工作階段識別碼, 擁有者)
