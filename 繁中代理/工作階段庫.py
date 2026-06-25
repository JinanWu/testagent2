"""SQLite 工作階段儲存層。

功能：
    提供 Hermes-style session persistence。此儲存層不再把 messages 視為可任意
    刪除重建的 transcript snapshot，而是以 append-first 的方式保存可查詢的完整
    訊息歷史，並用 session lineage 保存 context compression 前後的原始紀錄。
"""

from __future__ import annotations

import json
import os
import random
import sqlite3
import threading
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator


SCHEMA_VERSION = 2
寫入最大重試次數 = 15
寫入重試最短秒數 = 0.020
寫入重試最長秒數 = 0.150
WAL檢查點寫入間隔 = 50
每百萬Token價格表: dict[tuple[str, str], dict[str, float | str]] = {
    ("fake", "fake"): {"input": 0.0, "output": 0.0, "version": "local-pricing-v1"},
    ("gemini-adc", "gemini-2.5-flash-lite"): {"input": 0.10, "output": 0.40, "version": "local-pricing-v1"},
    ("gemini", "gemini-2.5-flash-lite"): {"input": 0.10, "output": 0.40, "version": "local-pricing-v1"},
}
預設每百萬Token價格 = {"input": 0.0, "output": 0.0, "version": "local-pricing-v1"}


class 工作階段庫:
    """管理 SQLite 內的會話、訊息、全文搜尋索引與壓縮鎖。"""

    def __init__(self, 資料庫路徑: str | Path) -> None:
        """初始化工作階段庫並建立必要資料表。

        參數：
            資料庫路徑: SQLite state database 檔案路徑。

        返回值：
            None。初始化會建立父目錄、開啟 SQLite connection、設定 WAL，並執行
            schema reconcile / migration。
        """
        self.資料庫路徑 = Path(資料庫路徑)
        self.資料庫路徑.parent.mkdir(parents=True, exist_ok=True)
        self._鎖 = threading.RLock()
        self._成功寫入次數 = 0
        self.連線 = sqlite3.connect(self.資料庫路徑, timeout=1, isolation_level=None, check_same_thread=False)
        self.連線.row_factory = sqlite3.Row
        self.套用WAL模式()
        self.建立資料表()

    def 套用WAL模式(self) -> None:
        """啟用 WAL journal mode，失敗時退回 DELETE mode。

        參數：無。
        返回值：None。此方法會修改 SQLite journal_mode；若檔案系統不支援 WAL，
        會使用 DELETE mode 以確保功能仍可使用。
        """
        try:
            self.連線.execute("PRAGMA journal_mode=WAL")
            self.連線.execute("PRAGMA synchronous=NORMAL")
        except sqlite3.OperationalError:
            self.連線.execute("PRAGMA journal_mode=DELETE")

    def _執行寫入(self, 函式):
        """以 BEGIN IMMEDIATE、retry 與 jitter 執行寫入交易。

        參數：
            函式: 接受 SQLite connection 的 callable，會在交易內執行。

        返回值：
            callable 的回傳值。若重試後仍鎖定，會重新丟出最後一個 SQLite 例外。
        """
        最後錯誤: Exception | None = None
        for _ in range(寫入最大重試次數):
            with self._鎖:
                try:
                    self.連線.execute("BEGIN IMMEDIATE")
                    結果 = 函式(self.連線)
                    try:
                        self.連線.execute("COMMIT")
                    except sqlite3.OperationalError as 錯誤:
                        if "no transaction is active" not in str(錯誤).lower():
                            raise
                    self._成功寫入次數 += 1
                    if self._成功寫入次數 % WAL檢查點寫入間隔 == 0:
                        try:
                            self.連線.execute("PRAGMA wal_checkpoint(PASSIVE)")
                        except sqlite3.Error:
                            pass
                    return 結果
                except sqlite3.OperationalError as 錯誤:
                    try:
                        self.連線.execute("ROLLBACK")
                    except sqlite3.Error:
                        pass
                    if "locked" not in str(錯誤).lower() and "busy" not in str(錯誤).lower():
                        raise
                    最後錯誤 = 錯誤
                except Exception:
                    try:
                        self.連線.execute("ROLLBACK")
                    except sqlite3.Error:
                        pass
                    raise
            time.sleep(random.uniform(寫入重試最短秒數, 寫入重試最長秒數))
        if 最後錯誤:
            raise 最後錯誤
        raise sqlite3.OperationalError("write transaction failed")

    def 建立資料表(self) -> None:
        """建立 sessions、messages、FTS、metadata 與 compression_locks 資料表。

        參數：無。
        返回值：None。此方法可重複執行，會補齊舊欄位並更新 schema_version。
        """
        def 寫入(conn: sqlite3.Connection) -> None:
            """在交易中建立/補齊 session store schema。

            參數：
                conn: 目前交易中的 SQLite connection。

            返回值：None。
            """
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS schema_version (
                    version INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS state_meta (
                    key TEXT PRIMARY KEY,
                    value TEXT
                );
                CREATE TABLE IF NOT EXISTS sessions (
                    id TEXT PRIMARY KEY,
                    source TEXT NOT NULL DEFAULT 'cli',
                    user_id TEXT,
                    model TEXT,
                    model_config TEXT,
                    system_prompt TEXT,
                    parent_session_id TEXT,
                    title TEXT,
                    end_reason TEXT,
                    compressed_from_session_id TEXT,
                    prompt_tokens INTEGER DEFAULT 0,
                    input_tokens INTEGER DEFAULT 0,
                    output_tokens INTEGER DEFAULT 0,
                    cache_read_tokens INTEGER DEFAULT 0,
                    cache_write_tokens INTEGER DEFAULT 0,
                    reasoning_tokens INTEGER DEFAULT 0,
                    message_count INTEGER DEFAULT 0,
                    tool_call_count INTEGER DEFAULT 0,
                    api_call_count INTEGER DEFAULT 0,
                    compression_count INTEGER DEFAULT 0,
                    cwd TEXT,
                    billing_provider TEXT,
                    billing_base_url TEXT,
                    billing_mode TEXT,
                    estimated_cost_usd REAL,
                    actual_cost_usd REAL,
                    cost_status TEXT,
                    cost_source TEXT,
                    pricing_version TEXT,
                    handoff_state TEXT,
                    handoff_platform TEXT,
                    handoff_error TEXT,
                    rewind_count INTEGER NOT NULL DEFAULT 0,
                    archived INTEGER NOT NULL DEFAULT 0,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL,
                    started_at REAL,
                    ended_at REAL,
                    FOREIGN KEY(parent_session_id) REFERENCES sessions(id)
                );
                CREATE TABLE IF NOT EXISTS messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT NOT NULL,
                    message_index INTEGER NOT NULL,
                    role TEXT NOT NULL,
                    content TEXT,
                    content_json TEXT NOT NULL,
                    tool_call_id TEXT,
                    tool_calls TEXT,
                    tool_name TEXT,
                    token_count INTEGER,
                    finish_reason TEXT,
                    reasoning TEXT,
                    reasoning_content TEXT,
                    reasoning_details TEXT,
                    codex_reasoning_items TEXT,
                    codex_message_items TEXT,
                    platform_message_id TEXT,
                    observed INTEGER DEFAULT 0,
                    active INTEGER NOT NULL DEFAULT 1,
                    created_at REAL NOT NULL,
                    timestamp REAL,
                    FOREIGN KEY(session_id) REFERENCES sessions(id)
                );
                CREATE INDEX IF NOT EXISTS idx_messages_session_index
                    ON messages(session_id, message_index);
                CREATE TABLE IF NOT EXISTS compression_locks (
                    session_id TEXT PRIMARY KEY,
                    holder TEXT NOT NULL,
                    acquired_at REAL NOT NULL,
                    expires_at REAL NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_compression_locks_expires
                    ON compression_locks(expires_at);
                """
            )
            self.補齊欄位(conn)
            conn.executescript(
                """
                CREATE INDEX IF NOT EXISTS idx_messages_session_active
                    ON messages(session_id, active, id);
                CREATE INDEX IF NOT EXISTS idx_sessions_parent
                    ON sessions(parent_session_id);
                CREATE INDEX IF NOT EXISTS idx_sessions_source
                    ON sessions(source);
                CREATE INDEX IF NOT EXISTS idx_sessions_started
                    ON sessions(started_at DESC);
                """
            )
            self.建立FTS(conn)
            版本 = conn.execute("SELECT version FROM schema_version LIMIT 1").fetchone()
            if 版本 is None:
                conn.execute("INSERT INTO schema_version(version) VALUES (?)", (SCHEMA_VERSION,))
            else:
                conn.execute("UPDATE schema_version SET version=?", (SCHEMA_VERSION,))
        self._執行寫入(寫入)

    def 補齊欄位(self, conn: sqlite3.Connection) -> None:
        """對既有 SQLite 檔補齊新欄位。

        參數：
            conn: 目前交易中的 SQLite connection。

        返回值：None。此方法只會 ADD 缺少欄位，不會刪除既有資料。
        """
        欄位定義 = {
            "sessions": {
                "source": "TEXT NOT NULL DEFAULT 'cli'",
                "user_id": "TEXT",
                "model": "TEXT",
                "model_config": "TEXT",
                "system_prompt": "TEXT",
                "parent_session_id": "TEXT",
                "title": "TEXT",
                "end_reason": "TEXT",
                "compressed_from_session_id": "TEXT",
                "prompt_tokens": "INTEGER DEFAULT 0",
                "input_tokens": "INTEGER DEFAULT 0",
                "output_tokens": "INTEGER DEFAULT 0",
                "cache_read_tokens": "INTEGER DEFAULT 0",
                "cache_write_tokens": "INTEGER DEFAULT 0",
                "reasoning_tokens": "INTEGER DEFAULT 0",
                "message_count": "INTEGER DEFAULT 0",
                "tool_call_count": "INTEGER DEFAULT 0",
                "api_call_count": "INTEGER DEFAULT 0",
                "compression_count": "INTEGER DEFAULT 0",
                "cwd": "TEXT",
                "billing_provider": "TEXT",
                "billing_base_url": "TEXT",
                "billing_mode": "TEXT",
                "estimated_cost_usd": "REAL",
                "actual_cost_usd": "REAL",
                "cost_status": "TEXT",
                "cost_source": "TEXT",
                "pricing_version": "TEXT",
                "handoff_state": "TEXT",
                "handoff_platform": "TEXT",
                "handoff_error": "TEXT",
                "rewind_count": "INTEGER NOT NULL DEFAULT 0",
                "archived": "INTEGER NOT NULL DEFAULT 0",
                "started_at": "REAL",
                "ended_at": "REAL",
                "updated_at": "REAL",
                "created_at": "REAL",
            },
            "messages": {
                "message_index": "INTEGER DEFAULT 0",
                "content": "TEXT",
                "content_json": "TEXT DEFAULT '{}'",
                "tool_call_id": "TEXT",
                "tool_calls": "TEXT",
                "tool_name": "TEXT",
                "token_count": "INTEGER",
                "finish_reason": "TEXT",
                "reasoning": "TEXT",
                "reasoning_content": "TEXT",
                "reasoning_details": "TEXT",
                "codex_reasoning_items": "TEXT",
                "codex_message_items": "TEXT",
                "platform_message_id": "TEXT",
                "observed": "INTEGER DEFAULT 0",
                "active": "INTEGER NOT NULL DEFAULT 1",
                "timestamp": "REAL",
                "created_at": "REAL",
            },
            "compression_locks": {
                "holder": "TEXT",
                "owner": "TEXT",
                "acquired_at": "REAL",
                "expires_at": "REAL",
            },
        }
        for 表名, 定義表 in 欄位定義.items():
            現有 = {列["name"] for 列 in conn.execute(f"PRAGMA table_info({表名})").fetchall()}
            for 名稱, 定義 in 定義表.items():
                if 名稱 not in 現有:
                    try:
                        conn.execute(f"ALTER TABLE {表名} ADD COLUMN {名稱} {定義}")
                    except sqlite3.OperationalError as 錯誤:
                        if "duplicate column" not in str(錯誤).lower():
                            raise
        現在 = time.time()
        conn.execute("UPDATE sessions SET started_at=COALESCE(started_at, created_at, ?), created_at=COALESCE(created_at, started_at, ?), updated_at=COALESCE(updated_at, created_at, ?)", (現在, 現在, 現在))
        conn.execute("UPDATE messages SET timestamp=COALESCE(timestamp, created_at, ?), created_at=COALESCE(created_at, timestamp, ?), content=COALESCE(content, json_extract(content_json, '$.content'))", (現在, 現在))
        messages_count = conn.execute("SELECT COUNT(*) AS count FROM messages").fetchone()["count"]
        marker = conn.execute("SELECT value FROM state_meta WHERE key='fts_rebuilt_schema_version'").fetchone()
        if messages_count and (marker is None or marker["value"] != str(SCHEMA_VERSION)):
            conn.execute("INSERT OR REPLACE INTO state_meta(key, value) VALUES ('fts_needs_rebuild', 'true')")
        # 舊 schema 使用 owner；新 schema 使用 holder，保留 owner 只為 migration 相容。
        鎖欄位 = {列["name"] for 列 in conn.execute("PRAGMA table_info(compression_locks)").fetchall()}
        if "owner" in 鎖欄位 and "holder" in 鎖欄位:
            conn.execute("UPDATE compression_locks SET holder=COALESCE(holder, owner, 'unknown')")

    def 建立FTS(self, conn: sqlite3.Connection) -> None:
        """建立一般 FTS5 與 trigram FTS5 索引及同步 triggers。

        參數：
            conn: 目前交易中的 SQLite connection。

        返回值：None。若 SQLite 不支援 trigram tokenizer，會保留一般 FTS，不中斷
        session store 初始化。
        """
        conn.executescript(
            """
            CREATE VIRTUAL TABLE IF NOT EXISTS messages_fts USING fts5(content);
            CREATE TRIGGER IF NOT EXISTS messages_fts_insert AFTER INSERT ON messages BEGIN
                INSERT INTO messages_fts(rowid, content) VALUES (
                    new.id,
                    COALESCE(new.content, '') || ' ' || COALESCE(new.tool_name, '') || ' ' || COALESCE(new.tool_calls, '')
                );
            END;
            CREATE TRIGGER IF NOT EXISTS messages_fts_delete AFTER DELETE ON messages BEGIN
                DELETE FROM messages_fts WHERE rowid = old.id;
            END;
            CREATE TRIGGER IF NOT EXISTS messages_fts_update AFTER UPDATE ON messages BEGIN
                DELETE FROM messages_fts WHERE rowid = old.id;
                INSERT INTO messages_fts(rowid, content) VALUES (
                    new.id,
                    COALESCE(new.content, '') || ' ' || COALESCE(new.tool_name, '') || ' ' || COALESCE(new.tool_calls, '')
                );
            END;
            """
        )
        try:
            conn.executescript(
                """
                CREATE VIRTUAL TABLE IF NOT EXISTS messages_fts_trigram USING fts5(content, tokenize='trigram');
                CREATE TRIGGER IF NOT EXISTS messages_fts_trigram_insert AFTER INSERT ON messages BEGIN
                    INSERT INTO messages_fts_trigram(rowid, content) VALUES (
                        new.id,
                        COALESCE(new.content, '') || ' ' || COALESCE(new.tool_name, '') || ' ' || COALESCE(new.tool_calls, '')
                    );
                END;
                CREATE TRIGGER IF NOT EXISTS messages_fts_trigram_delete AFTER DELETE ON messages BEGIN
                    DELETE FROM messages_fts_trigram WHERE rowid = old.id;
                END;
                CREATE TRIGGER IF NOT EXISTS messages_fts_trigram_update AFTER UPDATE ON messages BEGIN
                    DELETE FROM messages_fts_trigram WHERE rowid = old.id;
                    INSERT INTO messages_fts_trigram(rowid, content) VALUES (
                        new.id,
                        COALESCE(new.content, '') || ' ' || COALESCE(new.tool_name, '') || ' ' || COALESCE(new.tool_calls, '')
                    );
                END;
                """
            )
        except sqlite3.OperationalError:
            conn.execute("INSERT OR REPLACE INTO state_meta(key, value) VALUES ('fts_trigram_available', 'false')")
        marker = conn.execute("SELECT value FROM state_meta WHERE key='fts_needs_rebuild'").fetchone()
        if marker and marker["value"] == "true":
            self.重建FTS(conn)
        elif not conn.execute("SELECT value FROM state_meta WHERE key='fts_rebuilt_schema_version'").fetchone():
            conn.execute("INSERT OR REPLACE INTO state_meta(key, value) VALUES ('fts_rebuilt_schema_version', ?)", (str(SCHEMA_VERSION),))

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
