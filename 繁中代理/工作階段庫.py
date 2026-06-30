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
        def 寫入(資料庫連線: sqlite3.Connection) -> None:
            """在交易中建立/補齊 session store schema。

            參數：
                資料庫連線: 目前交易中的 SQLite connection。

            返回值：None。
            """
            資料庫連線.executescript(
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
            self.補齊欄位(資料庫連線)
            資料庫連線.executescript(
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
            self.建立FTS(資料庫連線)
            版本 = 資料庫連線.execute("SELECT version FROM schema_version LIMIT 1").fetchone()
            if 版本 is None:
                資料庫連線.execute("INSERT INTO schema_version(version) VALUES (?)", (SCHEMA_VERSION,))
            else:
                資料庫連線.execute("UPDATE schema_version SET version=?", (SCHEMA_VERSION,))
        self._執行寫入(寫入)

    def 補齊欄位(self, 資料庫連線: sqlite3.Connection) -> None:
        """對既有 SQLite 檔補齊新欄位。

        參數：
            資料庫連線: 目前交易中的 SQLite connection。

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
            現有 = {列["name"] for 列 in 資料庫連線.execute(f"PRAGMA table_info({表名})").fetchall()}
            for 名稱, 定義 in 定義表.items():
                if 名稱 not in 現有:
                    try:
                        資料庫連線.execute(f"ALTER TABLE {表名} ADD COLUMN {名稱} {定義}")
                    except sqlite3.OperationalError as 錯誤:
                        if "duplicate column" not in str(錯誤).lower():
                            raise
        現在 = time.time()
        資料庫連線.execute("UPDATE sessions SET started_at=COALESCE(started_at, created_at, ?), created_at=COALESCE(created_at, started_at, ?), updated_at=COALESCE(updated_at, created_at, ?)", (現在, 現在, 現在))
        資料庫連線.execute("UPDATE messages SET timestamp=COALESCE(timestamp, created_at, ?), created_at=COALESCE(created_at, timestamp, ?), content=COALESCE(content, json_extract(content_json, '$.content'))", (現在, 現在))
        訊息數量 = 資料庫連線.execute("SELECT COUNT(*) AS count FROM messages").fetchone()["count"]
        標記 = 資料庫連線.execute("SELECT value FROM state_meta WHERE key='fts_rebuilt_schema_version'").fetchone()
        if 訊息數量 and (標記 is None or 標記["value"] != str(SCHEMA_VERSION)):
            資料庫連線.execute("INSERT OR REPLACE INTO state_meta(key, value) VALUES ('fts_needs_rebuild', 'true')")
        # 舊 schema 使用 owner；新 schema 使用 holder，保留 owner 只為 migration 相容。
        鎖欄位 = {列["name"] for 列 in 資料庫連線.execute("PRAGMA table_info(compression_locks)").fetchall()}
        if "owner" in 鎖欄位 and "holder" in 鎖欄位:
            資料庫連線.execute("UPDATE compression_locks SET holder=COALESCE(holder, owner, 'unknown')")

    def 建立FTS(self, 資料庫連線: sqlite3.Connection) -> None:
        """建立一般 FTS5 與 trigram FTS5 索引及同步 triggers。

        參數：
            資料庫連線: 目前交易中的 SQLite connection。

        返回值：None。若 SQLite 不支援 trigram tokenizer，會保留一般 FTS，不中斷
        session store 初始化。
        """
        資料庫連線.executescript(
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
            資料庫連線.executescript(
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
            資料庫連線.execute("INSERT OR REPLACE INTO state_meta(key, value) VALUES ('fts_trigram_available', 'false')")
        標記 = 資料庫連線.execute("SELECT value FROM state_meta WHERE key='fts_needs_rebuild'").fetchone()
        if 標記 and 標記["value"] == "true":
            self.重建FTS(資料庫連線)
        elif not 資料庫連線.execute("SELECT value FROM state_meta WHERE key='fts_rebuilt_schema_version'").fetchone():
            資料庫連線.execute("INSERT OR REPLACE INTO state_meta(key, value) VALUES ('fts_rebuilt_schema_version', ?)", (str(SCHEMA_VERSION),))

    def 重建FTS(self, 資料庫連線: sqlite3.Connection) -> None:
        """從 canonical messages 重建 FTS 索引。

        參數：
            資料庫連線: 目前交易中的 SQLite connection。

        返回值：None。此方法只重建衍生搜尋索引，不會修改 messages 原始資料。
        """
        資料庫連線.execute("DELETE FROM messages_fts")
        資料庫連線.execute("INSERT INTO messages_fts(rowid, content) SELECT id, COALESCE(content, '') || ' ' || COALESCE(tool_name, '') || ' ' || COALESCE(tool_calls, '') FROM messages")
        try:
            資料庫連線.execute("DELETE FROM messages_fts_trigram")
            資料庫連線.execute("INSERT INTO messages_fts_trigram(rowid, content) SELECT id, COALESCE(content, '') || ' ' || COALESCE(tool_name, '') || ' ' || COALESCE(tool_calls, '') FROM messages")
        except sqlite3.OperationalError:
            pass
        資料庫連線.execute("INSERT OR REPLACE INTO state_meta(key, value) VALUES ('fts_rebuilt_schema_version', ?)", (str(SCHEMA_VERSION),))
        資料庫連線.execute("DELETE FROM state_meta WHERE key='fts_needs_rebuild'")

    def 建立或讀取工作階段(
        self,
        工作階段識別碼: str | None = None,
        parent_session_id: str | None = None,
        source: str = "cli",
        user_id: str | None = None,
        model: str | None = None,
        model_config: dict[str, Any] | None = None,
        cwd: str | None = None,
    ) -> str:
        """建立新工作階段或確認既有工作階段存在。

        參數：
            工作階段識別碼: 可選 session id；未提供時會產生新 id。
            parent_session_id: 壓縮或分支來源 session id。
            source: 來源平台，例如 cli、telegram、discord 或 api。
            user_id: 使用者識別碼，CLI 可用於區分不同操作人。
            model: 使用模型名稱。
            model_config: 模型初始化設定，會 JSON 序列化保存。
            cwd: 工作目錄。

        返回值：
            str：存在於資料庫中的 session id。
        """
        目前時間 = time.time()
        識別碼 = 工作階段識別碼 or f"session-{uuid.uuid4().hex[:12]}"
        def 寫入(資料庫連線: sqlite3.Connection) -> None:
            """在交易中建立缺少的 session row。

            參數：
                資料庫連線: 目前交易中的 SQLite connection。

            返回值：None。
            """
            資料庫連線.execute(
                """
                INSERT OR IGNORE INTO sessions(
                    id, source, user_id, title, parent_session_id, model, model_config, cwd,
                    created_at, started_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (識別碼, source, user_id, 識別碼, parent_session_id, model, json.dumps(model_config, ensure_ascii=False) if model_config else None, cwd, 目前時間, 目前時間, 目前時間),
            )
            資料庫連線.execute(
                """
                UPDATE sessions
                SET source=COALESCE(?, source), user_id=COALESCE(?, user_id),
                    model=COALESCE(?, model), model_config=COALESCE(?, model_config),
                    cwd=COALESCE(?, cwd), updated_at=?
                WHERE id=?
                """,
                (source, user_id, model, json.dumps(model_config, ensure_ascii=False) if model_config else None, cwd, 目前時間, 識別碼),
            )
        self._執行寫入(寫入)
        return 識別碼

    def 建立壓縮後工作階段(self, 舊工作階段識別碼: str, 壓縮訊息清單: list[dict[str, Any]], 系統提示詞: str) -> str:
        """結束舊 session 並建立載入 compressed messages 的新 session。

        參數：
            舊工作階段識別碼: 被壓縮前的 session id。
            壓縮訊息清單: 壓縮後要寫入 child session 的 messages。
            系統提示詞: child session 要保存的 stable system prompt。

        返回值：
            str：新建立的 child session id。
        """
        舊工作階段 = self.讀取工作階段(舊工作階段識別碼) or {}
        新識別碼 = f"session-{uuid.uuid4().hex[:12]}"
        目前時間 = time.time()
        def 寫入(資料庫連線: sqlite3.Connection) -> None:
            """在交易中結束 parent 並建立 compression child。

            參數：
                資料庫連線: 目前交易中的 SQLite connection。

            返回值：None。
            """
            資料庫連線.execute(
                "UPDATE sessions SET end_reason='compression', ended_at=COALESCE(ended_at, ?), updated_at=? WHERE id=?",
                (目前時間, 目前時間, 舊工作階段識別碼),
            )
            資料庫連線.execute(
                """
                INSERT INTO sessions(
                    id, source, user_id, title, system_prompt, parent_session_id, compressed_from_session_id,
                    prompt_tokens, input_tokens, output_tokens, cache_read_tokens, cache_write_tokens,
                    reasoning_tokens, compression_count, model, model_config, cwd,
                    billing_provider, billing_base_url, billing_mode, estimated_cost_usd, actual_cost_usd,
                    cost_status, cost_source, pricing_version, created_at, started_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    新識別碼,
                    舊工作階段.get("source") or "cli",
                    舊工作階段.get("user_id"),
                    舊工作階段.get("title") or 新識別碼,
                    系統提示詞,
                    舊工作階段識別碼,
                    舊工作階段識別碼,
                    舊工作階段.get("prompt_tokens") or 0,
                    舊工作階段.get("input_tokens") or 0,
                    舊工作階段.get("output_tokens") or 0,
                    舊工作階段.get("cache_read_tokens") or 0,
                    舊工作階段.get("cache_write_tokens") or 0,
                    舊工作階段.get("reasoning_tokens") or 0,
                    int(舊工作階段.get("compression_count") or 0) + 1,
                    舊工作階段.get("model"),
                    舊工作階段.get("model_config"),
                    舊工作階段.get("cwd"),
                    舊工作階段.get("billing_provider"),
                    舊工作階段.get("billing_base_url"),
                    舊工作階段.get("billing_mode"),
                    舊工作階段.get("estimated_cost_usd"),
                    舊工作階段.get("actual_cost_usd"),
                    舊工作階段.get("cost_status"),
                    舊工作階段.get("cost_source"),
                    舊工作階段.get("pricing_version"),
                    目前時間,
                    目前時間,
                    目前時間,
                ),
            )
            self._附加訊息清單(資料庫連線, 新識別碼, 壓縮訊息清單, 起始索引=0)
        self._執行寫入(寫入)
        return 新識別碼

    def 讀取工作階段(self, 工作階段識別碼: str) -> dict[str, Any] | None:
        """讀取單一工作階段資料。

        參數：
            工作階段識別碼: session id。

        返回值：
            dict | None：session row；找不到時回傳 None。
        """
        with self._鎖:
            資料列 = self.連線.execute("SELECT * FROM sessions WHERE id=?", (工作階段識別碼,)).fetchone()
        return dict(資料列) if 資料列 else None

    def 更新系統提示詞(self, 工作階段識別碼: str, 系統提示詞: str) -> None:
        """把穩定的 system prompt 快照寫回 session row。

        參數：
            工作階段識別碼: session id。
            系統提示詞: stable system prompt。

        返回值：None。
        """
        self._執行寫入(lambda 資料庫連線: 資料庫連線.execute("UPDATE sessions SET system_prompt=?, updated_at=? WHERE id=?", (系統提示詞, time.time(), 工作階段識別碼)))

    def 更新提示Token數(self, 工作階段識別碼: str, token數: int) -> None:
        """保存 provider prompt token usage，方便觀測壓縮判斷。

        參數：
            工作階段識別碼: session id。
            token數: provider 回報的 prompt token 數。

        返回值：None。
        """
        self._執行寫入(lambda 資料庫連線: 資料庫連線.execute("UPDATE sessions SET prompt_tokens=?, updated_at=? WHERE id=?", (token數, time.time(), 工作階段識別碼)))

    def 更新模型使用量(self, 工作階段識別碼: str, 使用量: dict[str, Any] | None, api呼叫增量: int = 1, billing_provider: str | None = None) -> None:
        """累計 provider usage、API 呼叫次數與本地預估成本。

        參數：
            工作階段識別碼: session id。
            使用量: provider 回傳的 usage dict，可包含 input/output/cache/reasoning token。
            api呼叫增量: 本次要累加的 API call 數。
            billing_provider: 可選成本 provider 名稱；未提供時沿用 session 欄位。

        返回值：None。
        """
        使用量 = 使用量 or {}
        輸入Token數 = int(使用量.get("input_tokens") or 使用量.get("prompt_tokens") or 使用量.get("prompt_token_count") or 0)
        輸出Token數 = int(使用量.get("output_tokens") or 使用量.get("completion_tokens") or 使用量.get("candidates_token_count") or 0)
        快取讀取Token數 = int(使用量.get("cache_read_tokens") or 使用量.get("cached_content_token_count") or 0)
        快取寫入Token數 = int(使用量.get("cache_write_tokens") or 0)
        推理Token數 = int(使用量.get("reasoning_tokens") or 使用量.get("thoughts_token_count") or 0)
        def 寫入(資料庫連線: sqlite3.Connection) -> None:
            """在交易中累計 usage counters 與估算成本。

            參數：
                資料庫連線: 目前交易中的 SQLite connection。

            返回值：None。
            """
            工作階段 = 資料庫連線.execute("SELECT model, billing_provider, estimated_cost_usd FROM sessions WHERE id=?", (工作階段識別碼,)).fetchone()
            模型名稱文字 = 工作階段["model"] if 工作階段 else None
            供應商 = billing_provider or (工作階段["billing_provider"] if 工作階段 else None) or "unknown"
            計價 = 每百萬Token價格表.get((供應商, 模型名稱文字 or "")) or 每百萬Token價格表.get(("gemini-adc", 模型名稱文字 or "")) or 預設每百萬Token價格
            增量成本 = (輸入Token數 * float(計價["input"]) + 輸出Token數 * float(計價["output"])) / 1_000_000
            資料庫連線.execute(
                """
                UPDATE sessions
                SET input_tokens=input_tokens+?, output_tokens=output_tokens+?,
                    cache_read_tokens=cache_read_tokens+?, cache_write_tokens=cache_write_tokens+?,
                    reasoning_tokens=reasoning_tokens+?, api_call_count=api_call_count+?,
                    prompt_tokens=CASE WHEN ? > 0 THEN ? ELSE prompt_tokens END,
                    billing_provider=COALESCE(?, billing_provider), billing_mode=COALESCE(billing_mode, 'estimated'),
                    estimated_cost_usd=COALESCE(estimated_cost_usd, 0)+?,
                    cost_status='estimated', cost_source='local_pricing_table', pricing_version=?,
                    updated_at=?
                WHERE id=?
                """,
                (輸入Token數, 輸出Token數, 快取讀取Token數, 快取寫入Token數, 推理Token數, api呼叫增量, 輸入Token數, 輸入Token數, 供應商, 增量成本, str(計價["version"]), time.time(), 工作階段識別碼),
            )
        self._執行寫入(寫入)

    def 附加單一訊息(self, 工作階段識別碼: str, 訊息: dict[str, Any]) -> int:
        """append 單一 message 並回傳 SQLite row id。

        參數：
            工作階段識別碼: session id。
            訊息: OpenAI-compatible message dict。

        返回值：int。新增 message row id。
        """
        def 寫入(資料庫連線: sqlite3.Connection) -> int:
            """在交易中 append 單一 message。

            參數：
                資料庫連線: 目前交易中的 SQLite connection。

            返回值：int。新增 row id。
            """
            資料列 = 資料庫連線.execute("SELECT COALESCE(MAX(message_index), -1) AS max_idx FROM messages WHERE session_id=? AND active=1", (工作階段識別碼,)).fetchone()
            起始索引 = int(資料列["max_idx"] if 資料列 and 資料列["max_idx"] is not None else -1) + 1
            self._附加訊息清單(資料庫連線, 工作階段識別碼, [訊息], 起始索引=起始索引)
            return int(資料庫連線.execute("SELECT last_insert_rowid()").fetchone()[0])
        return int(self._執行寫入(寫入))

    def 替換訊息清單(self, 工作階段識別碼: str, 訊息清單: list[dict[str, Any]]) -> None:
        """以 inactive soft-delete 舊 messages 後 append 新 transcript。

        參數：
            工作階段識別碼: session id。
            訊息清單: rewrite 後的 canonical messages。

        返回值：None。舊 rows 會保留為 active=0，供 audit/debug。
        """
        def 寫入(資料庫連線: sqlite3.Connection) -> None:
            """在交易中停用舊 rows 並重建 active transcript。

            參數：
                資料庫連線: 目前交易中的 SQLite connection。

            返回值：None。
            """
            資料庫連線.execute("UPDATE messages SET active=0 WHERE session_id=? AND active=1", (工作階段識別碼,))
            資料庫連線.execute("UPDATE sessions SET message_count=0, tool_call_count=0, rewind_count=rewind_count+1 WHERE id=?", (工作階段識別碼,))
            self._附加訊息清單(資料庫連線, 工作階段識別碼, 訊息清單, 起始索引=0)
        self._執行寫入(寫入)

    def 讀取訊息(self, 工作階段識別碼: str, 包含停用: bool = False, include_ancestors: bool = False) -> list[dict[str, Any]]:
        """依序讀取某工作階段的 canonical messages。

        參數：
            工作階段識別碼: session id。
            包含停用: 是否包含 active=0 的 rewind/audit 訊息。
            include_ancestors: 是否讀取 root→tip 整條 compression lineage。

        返回值：
            list[dict[str, Any]]：OpenAI-compatible message dict 清單。
        """
        工作階段識別碼清單 = [工作階段識別碼]
        if include_ancestors:
            工作階段識別碼清單 = self.取得工作階段譜系(工作階段識別碼)
        啟用條件 = "" if 包含停用 else " AND active=1"
        佔位符清單 = ",".join("?" for _ in 工作階段識別碼清單)
        with self._鎖:
            資料列清單 = self.連線.execute(
                f"SELECT * FROM messages WHERE session_id IN ({佔位符清單}){啟用條件} ORDER BY timestamp, id",
                tuple(工作階段識別碼清單),
            ).fetchall()
        return [self._資料列轉訊息(資料列) for 資料列 in 資料列清單]

    def _資料列轉訊息(self, 資料列: sqlite3.Row) -> dict[str, Any]:
        """把 messages row 還原成 canonical message dict。

        參數：
            資料列: SQLite Row。

        返回值：
            dict[str, Any]：包含 role/content/tool metadata 的 message。
        """
        訊息 = json.loads(資料列["content_json"] or "{}")
        訊息["role"] = 資料列["role"]
        if 資料列["content"] is not None:
            訊息["content"] = 資料列["content"]
        if 資料列["tool_call_id"]:
            訊息["tool_call_id"] = 資料列["tool_call_id"]
        if 資料列["tool_name"]:
            訊息["name"] = 資料列["tool_name"]
            訊息["tool_name"] = 資料列["tool_name"]
        if 資料列["tool_calls"]:
            try:
                訊息["tool_calls"] = json.loads(資料列["tool_calls"])
            except json.JSONDecodeError:
                訊息["tool_calls"] = []
        if 資料列["finish_reason"]:
            訊息["finish_reason"] = 資料列["finish_reason"]
        if 資料列["token_count"] is not None:
            訊息["token_count"] = 資料列["token_count"]
        if 資料列["reasoning"]:
            訊息["reasoning"] = 資料列["reasoning"]
        if 資料列["reasoning_content"] is not None:
            訊息["reasoning_content"] = 資料列["reasoning_content"]
        for 欄位 in ("reasoning_details", "codex_reasoning_items", "codex_message_items"):
            if 資料列[欄位]:
                try:
                    訊息[欄位] = json.loads(資料列[欄位])
                except json.JSONDecodeError:
                    訊息[欄位] = 資料列[欄位]
        if 資料列["platform_message_id"]:
            訊息["message_id"] = 資料列["platform_message_id"]
            訊息["platform_message_id"] = 資料列["platform_message_id"]
        if 資料列["observed"]:
            訊息["observed"] = True
        return 訊息

    def 寫入訊息清單(self, 工作階段識別碼: str, 訊息清單: list[dict[str, Any]], 是否使用既有交易: bool = False) -> None:
        """append-first 寫入 working messages，不刪除舊資料。

        參數：
            工作階段識別碼: session id。
            訊息清單: 目前 runtime working messages；方法只會 append DB 尚未保存的尾端。
            是否使用既有交易: 舊版相容參數；新實作會自動判斷是否在外部交易中使用。

        返回值：None。此方法永遠不會 DELETE 舊 messages；rewrite 應使用專門方法。
        """
        def 寫入(資料庫連線: sqlite3.Connection) -> None:
            """在交易中 append 尚未持久化的尾端訊息。

            參數：
                資料庫連線: 目前交易中的 SQLite connection。

            返回值：None。
            """
            資料列 = 資料庫連線.execute("SELECT COALESCE(MAX(message_index), -1) AS max_idx FROM messages WHERE session_id=? AND active=1", (工作階段識別碼,)).fetchone()
            起始索引 = int(資料列["max_idx"] if 資料列 and 資料列["max_idx"] is not None else -1) + 1
            if 起始索引 >= len(訊息清單):
                資料庫連線.execute("UPDATE sessions SET updated_at=? WHERE id=?", (time.time(), 工作階段識別碼))
                return
            self._附加訊息清單(資料庫連線, 工作階段識別碼, 訊息清單[起始索引:], 起始索引=起始索引)
        if 是否使用既有交易:
            寫入(self.連線)
        else:
            self._執行寫入(寫入)

    def _附加訊息清單(self, 資料庫連線: sqlite3.Connection, 工作階段識別碼: str, 訊息清單: list[dict[str, Any]], 起始索引: int) -> None:
        """在目前交易中 append 訊息清單。

        參數：
            資料庫連線: 目前交易中的 SQLite connection。
            工作階段識別碼: session id。
            訊息清單: 要 append 的 message dict 清單。
            起始索引: 第一則訊息的 message_index。

        返回值：None。會同步更新 session message/tool counters。
        """
        目前時間 = time.time()
        新增數 = 0
        新增工具呼叫數 = 0
        for 偏移, 訊息 in enumerate(訊息清單):
            索引 = 起始索引 + 偏移
            角色 = str(訊息.get("role", ""))
            內容 = 訊息.get("content")
            if not isinstance(內容, str) and 內容 is not None:
                內容字串 = json.dumps(內容, ensure_ascii=False)
            else:
                內容字串 = 內容
            工具呼叫清單 = 訊息.get("tool_calls")
            工具呼叫JSON = json.dumps(工具呼叫清單, ensure_ascii=False) if 工具呼叫清單 else None
            工具名 = 訊息.get("name") or 訊息.get("tool_name")
            if not 工具名 and 工具呼叫清單:
                try:
                    工具名 = 工具呼叫清單[0].get("function", {}).get("name")
                except Exception:
                    工具名 = None
            資料庫連線.execute(
                """
                INSERT INTO messages(
                    session_id, message_index, role, content, content_json, tool_call_id,
                    tool_calls, tool_name, token_count, finish_reason, reasoning,
                    reasoning_content, reasoning_details, codex_reasoning_items,
                    codex_message_items, platform_message_id, observed, active,
                    created_at, timestamp
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)
                """,
                (
                    工作階段識別碼,
                    索引,
                    角色,
                    內容字串,
                    json.dumps(訊息, ensure_ascii=False),
                    訊息.get("tool_call_id"),
                    工具呼叫JSON,
                    工具名,
                    訊息.get("token_count"),
                    訊息.get("finish_reason"),
                    訊息.get("reasoning"),
                    訊息.get("reasoning_content"),
                    json.dumps(訊息.get("reasoning_details"), ensure_ascii=False) if 訊息.get("reasoning_details") else None,
                    json.dumps(訊息.get("codex_reasoning_items"), ensure_ascii=False) if 訊息.get("codex_reasoning_items") else None,
                    json.dumps(訊息.get("codex_message_items"), ensure_ascii=False) if 訊息.get("codex_message_items") else None,
                    訊息.get("platform_message_id") or 訊息.get("message_id"),
                    1 if 訊息.get("observed") else 0,
                    目前時間 + 偏移 * 0.000001,
                    目前時間 + 偏移 * 0.000001,
                ),
            )
            新增數 += 1
            if 角色 == "tool":
                新增工具呼叫數 += 1
            elif 工具呼叫清單:
                新增工具呼叫數 += len(工具呼叫清單) if isinstance(工具呼叫清單, list) else 1
        資料庫連線.execute(
            "UPDATE sessions SET message_count=message_count+?, tool_call_count=tool_call_count+?, updated_at=? WHERE id=?",
            (新增數, 新增工具呼叫數, time.time(), 工作階段識別碼),
        )

    def 取得壓縮鎖(self, 工作階段識別碼: str, 擁有者: str | None = None, ttl秒: int = 300) -> str | None:
        """取得 DB-backed compression lock；失敗回傳 None。

        參數：
            工作階段識別碼: 要壓縮的 session id。
            擁有者: 可選 holder；未提供時使用 pid/tid/agent/nonce 診斷格式。
            ttl秒: lock 過期秒數。

        返回值：
            str | None：成功時回傳 holder，失敗時回傳 None。
        """
        擁有者 = 擁有者 or self.建立壓縮鎖Holder()
        目前時間 = time.time()
        過期時間 = 目前時間 + ttl秒
        def 寫入(資料庫連線: sqlite3.Connection) -> bool:
            """在交易中嘗試取得 compression lock。

            參數：
                資料庫連線: 目前交易中的 SQLite connection。

            返回值：bool。True 表示目前呼叫者持有鎖。
            """
            資料庫連線.execute("DELETE FROM compression_locks WHERE expires_at < ?", (目前時間,))
            資料庫連線.execute(
                "INSERT OR IGNORE INTO compression_locks(session_id, holder, acquired_at, expires_at) VALUES (?, ?, ?, ?)",
                (工作階段識別碼, 擁有者, 目前時間, 過期時間),
            )
            資料列 = 資料庫連線.execute("SELECT holder FROM compression_locks WHERE session_id=?", (工作階段識別碼,)).fetchone()
            return bool(資料列 and 資料列["holder"] == 擁有者)
        return 擁有者 if self._執行寫入(寫入) else None

    def 建立壓縮鎖Holder(self, agent標籤: str | None = None) -> str:
        """建立 Hermes-style 壓縮鎖 holder 診斷字串。

        參數：
            agent標籤: 可選 agent 識別；未提供時使用目前物件 id。

        返回值：
            str：包含 pid、tid、agent 與 nonce 的 holder 字串。
        """
        return f"pid={os.getpid()}:tid={threading.get_ident()}:agent={agent標籤 or id(self):x}:nonce={uuid.uuid4().hex[:8]}"

    def 讀取壓縮鎖Holder(self, 工作階段識別碼: str) -> str | None:
        """讀取目前未過期的 compression lock holder。

        參數：
            工作階段識別碼: session id。

        返回值：
            str | None：目前 holder；沒有鎖或已過期時回傳 None。
        """
        with self._鎖:
            資料列 = self.連線.execute("SELECT holder FROM compression_locks WHERE session_id=? AND expires_at>=?", (工作階段識別碼, time.time())).fetchone()
        return 資料列["holder"] if 資料列 else None

    def 釋放壓縮鎖(self, 工作階段識別碼: str, 擁有者: str) -> None:
        """釋放 compression lock。

        參數：
            工作階段識別碼: session id。
            擁有者: 只有符合 holder 的呼叫者能釋放鎖。

        返回值：None。
        """
        self._執行寫入(lambda 資料庫連線: 資料庫連線.execute("DELETE FROM compression_locks WHERE session_id=? AND holder=?", (工作階段識別碼, 擁有者)))

    @contextmanager
    def 壓縮鎖(self, 工作階段識別碼: str, ttl秒: int = 300) -> Iterator[bool]:
        """以 context manager 使用 compression lock。

        參數：
            工作階段識別碼: session id。
            ttl秒: lock 過期秒數。

        返回值：
            Iterator[bool]：yield True 表示取得鎖，False 表示應跳過本次壓縮。
        """
        擁有者 = None
        try:
            try:
                擁有者 = self.取得壓縮鎖(工作階段識別碼, ttl秒=ttl秒)
            except Exception:
                # version skew / lock subsystem failure：避免無限壓縮重試，先 fail-open。
                yield True
                return
            yield 擁有者 is not None
        finally:
            if 擁有者:
                self.釋放壓縮鎖(工作階段識別碼, 擁有者)

    def 取得壓縮Tip(self, 工作階段識別碼: str) -> str:
        """沿 compression lineage 找到最新 active tip。

        參數：
            工作階段識別碼: root、middle 或 tip session id。

        返回值：
            str：該 logical conversation 的最新 tip session id。
        """
        目前識別碼 = 工作階段識別碼
        for _ in range(100):
            with self._鎖:
                資料列 = self.連線.execute(
                    """
                    SELECT child.id FROM sessions child
                    JOIN sessions parent ON parent.id = child.parent_session_id
                    WHERE child.parent_session_id=?
                      AND parent.end_reason='compression'
                      AND child.started_at >= COALESCE(parent.ended_at, 0)
                    ORDER BY child.started_at DESC, child.id DESC LIMIT 1
                    """,
                    (目前識別碼,),
                ).fetchone()
            if not 資料列:
                return 目前識別碼
            目前識別碼 = 資料列["id"]
        return 目前識別碼

    def 解析Resume工作階段(self, 工作階段識別碼: str) -> str:
        """把任意 lineage 內的 session id 導向 compression tip。

        參數：
            工作階段識別碼: 使用者指定的 session id。

        返回值：
            str：應實際 resume 的 session id。
        """
        return self.取得壓縮Tip(工作階段識別碼)

    def 取得工作階段譜系(self, 工作階段識別碼: str) -> list[str]:
        """取得 root→tip 的 compression lineage。

        參數：
            工作階段識別碼: lineage 中任一 session id。

        返回值：
            list[str]：依時間由 root 到指定 tip 的 session id 清單。
        """
        譜系鏈 = []
        目前識別碼 = 工作階段識別碼
        已見集合: set[str] = set()
        with self._鎖:
            for _ in range(100):
                if not 目前識別碼 or 目前識別碼 in 已見集合:
                    break
                已見集合.add(目前識別碼)
                譜系鏈.append(目前識別碼)
                資料列 = self.連線.execute("SELECT parent_session_id FROM sessions WHERE id=?", (目前識別碼,)).fetchone()
                if not 資料列 or not 資料列["parent_session_id"]:
                    break
                目前識別碼 = 資料列["parent_session_id"]
        return list(reversed(譜系鏈)) or [工作階段識別碼]

    def 設定封存狀態(self, 工作階段識別碼: str, 是否封存: bool = True) -> None:
        """設定 session archived 狀態。

        參數：
            工作階段識別碼: session id。
            是否封存: True 表示封存；False 表示取消封存。

        返回值：None。
        """
        self._執行寫入(lambda 資料庫連線: 資料庫連線.execute("UPDATE sessions SET archived=?, updated_at=? WHERE id=?", (1 if 是否封存 else 0, time.time(), 工作階段識別碼)))

    def 封存工作階段(self, 工作階段識別碼: str) -> None:
        """封存指定 session，讓預設列表與搜尋排除它。

        參數：
            工作階段識別碼: session id。

        返回值：None。
        """
        self.設定封存狀態(工作階段識別碼, True)

    def 取消封存工作階段(self, 工作階段識別碼: str) -> None:
        """取消封存指定 session。

        參數：
            工作階段識別碼: session id。

        返回值：None。
        """
        self.設定封存狀態(工作階段識別碼, False)

    def 列出工作階段(self, limit: int = 20, include_children: bool = False, include_archived: bool = False, source: str | None = None, user_id: str | None = None) -> list[dict[str, Any]]:
        """列出 session，預設把 compression chain 投影成單一 logical conversation。

        參數：
            limit: 最多回傳筆數。
            include_children: True 時回傳原始 child sessions；False 時隱藏 compression child。
            include_archived: 是否包含已封存 session。
            source: 可選來源平台篩選。
            user_id: 可選使用者識別碼篩選。

        返回值：
            list[dict[str, Any]]：session metadata 清單；投影列會包含 _lineage_root_id。
        """
        條件 = [] if include_archived else ["archived=0"]
        參數: list[Any] = []
        if source:
            條件.append("source=?")
            參數.append(source)
        if user_id:
            條件.append("user_id=?")
            參數.append(user_id)
        查詢條件片段 = " WHERE " + " AND ".join(條件) if 條件 else ""
        參數.append(max(limit * 5, limit))
        with self._鎖:
            資料列清單 = [dict(資料列) for 資料列 in self.連線.execute(f"SELECT * FROM sessions{查詢條件片段} ORDER BY started_at DESC LIMIT ?", tuple(參數)).fetchall()]
        if include_children:
            return 資料列清單[:limit]
        投影結果清單: list[dict[str, Any]] = []
        已使用根識別碼集合: set[str] = set()
        for 資料列 in 資料列清單:
            根工作階段識別碼 = self.取得工作階段譜系(資料列["id"])[0]
            if 根工作階段識別碼 in 已使用根識別碼集合:
                continue
            末端工作階段識別碼 = self.取得壓縮Tip(根工作階段識別碼)
            末端資料列 = self.讀取工作階段(末端工作階段識別碼) or 資料列
            末端資料列 = dict(末端資料列)
            if not include_archived and 末端資料列.get("archived"):
                continue
            if source and 末端資料列.get("source") != source:
                continue
            if user_id and 末端資料列.get("user_id") != user_id:
                continue
            末端資料列["_lineage_root_id"] = 根工作階段識別碼
            投影結果清單.append(末端資料列)
            已使用根識別碼集合.add(根工作階段識別碼)
            if len(投影結果清單) >= limit:
                break
        return 投影結果清單

    def 搜尋訊息(self, 查詢: str, limit: int = 20, include_archived: bool = False, source: str | None = None, user_id: str | None = None) -> list[dict[str, Any]]:
        """使用 FTS 搜尋訊息內容、工具名稱與 tool_calls。

        參數：
            查詢: FTS query 或一般關鍵字。
            limit: 最多回傳筆數。
            include_archived: 是否包含 archived sessions。
            source: 可選來源平台篩選。
            user_id: 可選使用者識別碼篩選。

        返回值：
            list[dict[str, Any]]：包含 message/session metadata 與 snippet 的搜尋結果。
        """
        查詢 = 查詢.strip()
        if not 查詢:
            return []
        with self._鎖:
            try:
                資料列清單 = self.連線.execute(
                    """
                    SELECT m.*, snippet(messages_fts, 0, '>>>', '<<<', '…', 8) AS snippet
                    FROM messages_fts JOIN messages m ON m.id = messages_fts.rowid
                    WHERE messages_fts MATCH ? AND m.active=1
                    ORDER BY m.id DESC LIMIT ?
                    """,
                    (查詢, limit),
                ).fetchall()
            except sqlite3.OperationalError:
                資料列清單 = []
            if not 資料列清單:
                try:
                    資料列清單 = self.連線.execute(
                        """
                        SELECT m.*, snippet(messages_fts_trigram, 0, '>>>', '<<<', '…', 8) AS snippet
                        FROM messages_fts_trigram JOIN messages m ON m.id = messages_fts_trigram.rowid
                        WHERE messages_fts_trigram MATCH ? AND m.active=1
                        ORDER BY m.id DESC LIMIT ?
                        """,
                        (查詢, limit),
                    ).fetchall()
                except sqlite3.OperationalError:
                    資料列清單 = []
            if not 資料列清單:
                模糊查詢字串 = f"%{查詢}%"
                資料列清單 = self.連線.execute(
                    "SELECT *, content AS snippet FROM messages WHERE active=1 AND (content LIKE ? OR tool_name LIKE ? OR tool_calls LIKE ?) ORDER BY id DESC LIMIT ?",
                    (模糊查詢字串, 模糊查詢字串, 模糊查詢字串, limit),
                ).fetchall()
        篩選結果清單 = []
        for 資料列 in 資料列清單:
            工作階段 = self.讀取工作階段(資料列["session_id"]) or {}
            if not include_archived and 工作階段.get("archived"):
                continue
            if source and 工作階段.get("source") != source:
                continue
            if user_id and 工作階段.get("user_id") != user_id:
                continue
            篩選結果清單.append(資料列)
        return [{"id": 資料列["id"], "session_id": 資料列["session_id"], "role": 資料列["role"], "content": 資料列["content"], "tool_name": 資料列["tool_name"], "snippet": 資料列["snippet"]} for 資料列 in 篩選結果清單[:limit]]

    def 取得錨點視圖(self, 工作階段識別碼: str, 訊息id: int, window: int = 5, bookend: int = 3) -> dict[str, Any]:
        """取得搜尋命中訊息周邊視窗與首尾 bookends。

        參數：
            工作階段識別碼: session id。
            訊息id: anchor message row id。
            window: anchor 前後各取幾則。
            bookend: session 開頭/結尾各取幾則 user/assistant 訊息。

        返回值：dict[str, Any]。包含 messages、bookend_start、bookend_end 與邊界計數。
        """
        with self._鎖:
            前方資料列清單 = self.連線.execute("SELECT * FROM messages WHERE session_id=? AND id<=? AND active=1 ORDER BY id DESC LIMIT ?", (工作階段識別碼, 訊息id, window + 1)).fetchall()
            後方資料列清單 = self.連線.execute("SELECT * FROM messages WHERE session_id=? AND id>? AND active=1 ORDER BY id ASC LIMIT ?", (工作階段識別碼, 訊息id, window)).fetchall()
            開頭資料列清單 = self.連線.execute("SELECT * FROM messages WHERE session_id=? AND active=1 AND role IN ('user','assistant') AND length(COALESCE(content,''))>0 ORDER BY id ASC LIMIT ?", (工作階段識別碼, bookend)).fetchall()
            結尾資料列清單 = self.連線.execute("SELECT * FROM messages WHERE session_id=? AND active=1 AND role IN ('user','assistant') AND length(COALESCE(content,''))>0 ORDER BY id DESC LIMIT ?", (工作階段識別碼, bookend)).fetchall()
        視窗列 = list(reversed(前方資料列清單)) + list(後方資料列清單)
        return {
            "messages": [self._資料列轉訊息(資料列) | {"id": 資料列["id"]} for 資料列 in 視窗列],
            "messages_before": max(0, len(前方資料列清單) - 1),
            "messages_after": len(後方資料列清單),
            "bookend_start": [self._資料列轉訊息(資料列) | {"id": 資料列["id"]} for 資料列 in 開頭資料列清單],
            "bookend_end": [self._資料列轉訊息(資料列) | {"id": 資料列["id"]} for 資料列 in reversed(結尾資料列清單)],
        }

    def 搜尋工作階段(self, 查詢: str, limit: int = 3, window: int = 5, include_archived: bool = False, source: str | None = None, user_id: str | None = None) -> list[dict[str, Any]]:
        """以 Hermes session_search discovery 形狀搜尋歷史 session。

        參數：
            查詢: 搜尋關鍵字。
            limit: 最多回傳幾個不同 session。
            window: 命中訊息前後視窗大小。

        返回值：list[dict[str, Any]]。每筆包含 session metadata、snippet、bookends 與命中周邊訊息。
        """
        命中清單 = self.搜尋訊息(查詢, limit=max(limit * 5, limit), include_archived=include_archived, source=source, user_id=user_id)
        結果: list[dict[str, Any]] = []
        已見集合: set[str] = set()
        for 命中項目 in 命中清單:
            命中工作階段識別碼 = 命中項目["session_id"]
            根工作階段識別碼 = self.取得工作階段譜系(命中工作階段識別碼)[0]
            if 根工作階段識別碼 in 已見集合:
                continue
            已見集合.add(根工作階段識別碼)
            工作階段 = self.讀取工作階段(命中工作階段識別碼) or {}
            錨點視圖 = self.取得錨點視圖(命中工作階段識別碼, int(命中項目["id"]), window=window)
            結果.append({
                "session_id": 命中工作階段識別碼,
                "title": 工作階段.get("title"),
                "source": 工作階段.get("source"),
                "snippet": 命中項目.get("snippet"),
                "match_message_id": 命中項目.get("id"),
                "bookend_start": 錨點視圖["bookend_start"],
                "messages": 錨點視圖["messages"],
                "bookend_end": 錨點視圖["bookend_end"],
                "messages_before": 錨點視圖["messages_before"],
                "messages_after": 錨點視圖["messages_after"],
                "_lineage_root_id": 根工作階段識別碼,
            })
            if len(結果) >= limit:
                break
        return 結果

    def 讀取工作階段全文(self, 工作階段識別碼: str) -> dict[str, Any]:
        """讀取指定 session 的 metadata 與訊息；大量訊息時回傳首尾摘要。

        參數：
            工作階段識別碼: session id。

        返回值：dict[str, Any]。包含 session、messages、total_messages 與 truncated。
        """
        工作階段 = self.讀取工作階段(工作階段識別碼)
        if not 工作階段:
            raise ValueError(f"session not found: {工作階段識別碼}")
        with self._鎖:
            資料列清單 = self.連線.execute("SELECT * FROM messages WHERE session_id=? AND active=1 ORDER BY id", (工作階段識別碼,)).fetchall()
        總訊息數 = len(資料列清單)
        if 總訊息數 > 35:
            保留資料列清單 = 資料列清單[:20] + 資料列清單[-10:]
            是否截斷 = True
        else:
            保留資料列清單 = 資料列清單
            是否截斷 = False
        return {
            "session_id": 工作階段識別碼,
            "session": 工作階段,
            "messages": [self._資料列轉訊息(資料列) | {"id": 資料列["id"]} for 資料列 in 保留資料列清單],
            "total_messages": 總訊息數,
            "truncated": 是否截斷,
        }

    def 捲動工作階段訊息(self, 工作階段識別碼: str, around_message_id: int, window: int = 5) -> dict[str, Any]:
        """讀取 anchor message 周邊視窗，供 session_search scroll 形狀使用。

        參數：
            工作階段識別碼: session id。
            around_message_id: 視窗中心 message row id。
            window: 前後各取幾則。

        返回值：dict[str, Any]。包含 messages 與邊界計數。
        """
        視圖 = self.取得錨點視圖(工作階段識別碼, around_message_id, window=window, bookend=0)
        return {
            "session_id": 工作階段識別碼,
            "around_message_id": around_message_id,
            "messages": 視圖["messages"],
            "messages_before": 視圖["messages_before"],
            "messages_after": 視圖["messages_after"],
        }

    def 瀏覽近期工作階段(self, limit: int = 10, include_archived: bool = False, source: str | None = None, user_id: str | None = None) -> dict[str, Any]:
        """瀏覽近期 logical sessions，供 session_search browse 形狀使用。

        參數：
            limit: 最多回傳幾個 sessions。
            include_archived: 是否包含 archived sessions。
            source: 可選來源平台篩選。
            user_id: 可選使用者識別碼篩選。

        返回值：dict[str, Any]。包含 sessions 與 total_count。
        """
        工作階段清單 = self.列出工作階段(limit=limit, include_archived=include_archived, source=source, user_id=user_id)
        return {"sessions": 工作階段清單, "total_count": len(工作階段清單)}

    def 重新命名工作階段(self, 工作階段識別碼: str, 標題: str) -> None:
        """更新 session title。

        參數：
            工作階段識別碼: 要重新命名的 session id。
            標題: 新 title，會去除前後空白。

        返回值：None。找不到 session 或標題為空時會丟出 ValueError。
        """
        新標題 = 標題.strip()
        if not 新標題:
            raise ValueError("title 不可為空")
        def 寫入(conn: sqlite3.Connection) -> None:
            """在交易中更新 title。

            參數：
                conn: 目前交易中的 SQLite connection。

            返回值：None。
            """
            result = conn.execute("UPDATE sessions SET title=?, updated_at=? WHERE id=?", (新標題, time.time(), 工作階段識別碼))
            if result.rowcount == 0:
                raise ValueError(f"session not found: {工作階段識別碼}")
        self._執行寫入(寫入)

    def 統計工作階段(self, include_archived: bool = False, source: str | None = None, user_id: str | None = None) -> dict[str, Any]:
        """統計 session store 的主要計數與 usage。

        參數：
            include_archived: 是否包含封存 sessions。
            source: 可選來源平台篩選。
            user_id: 可選使用者識別碼篩選。

        返回值：dict[str, Any]。包含 session/message/tool/API/token/cost 統計。
        """
        條件 = [] if include_archived else ["archived=0"]
        參數: list[Any] = []
        if source:
            條件.append("source=?")
            參數.append(source)
        if user_id:
            條件.append("user_id=?")
            參數.append(user_id)
        where = " WHERE " + " AND ".join(條件) if 條件 else ""
        with self._鎖:
            session_stats = self.連線.execute(
                f"""
                SELECT COUNT(*) AS session_count,
                       SUM(CASE WHEN archived=1 THEN 1 ELSE 0 END) AS archived_count,
                       SUM(message_count) AS message_count,
                       SUM(tool_call_count) AS tool_call_count,
                       SUM(api_call_count) AS api_call_count,
                       SUM(input_tokens) AS input_tokens,
                       SUM(output_tokens) AS output_tokens,
                       SUM(reasoning_tokens) AS reasoning_tokens,
                       SUM(estimated_cost_usd) AS estimated_cost_usd
                FROM sessions{where}
                """,
                tuple(參數),
            ).fetchone()
            active_messages = self.連線.execute(
                "SELECT COUNT(*) AS count FROM messages WHERE active=1"
            ).fetchone()["count"]
            inactive_messages = self.連線.execute(
                "SELECT COUNT(*) AS count FROM messages WHERE active=0"
            ).fetchone()["count"]
            db_size = self.資料庫路徑.stat().st_size if self.資料庫路徑.exists() else 0
        統計 = dict(session_stats) if session_stats else {}
        return {
            "session_count": int(統計.get("session_count") or 0),
            "archived_count": int(統計.get("archived_count") or 0),
            "message_count": int(統計.get("message_count") or 0),
            "active_message_rows": int(active_messages or 0),
            "inactive_message_rows": int(inactive_messages or 0),
            "tool_call_count": int(統計.get("tool_call_count") or 0),
            "api_call_count": int(統計.get("api_call_count") or 0),
            "input_tokens": int(統計.get("input_tokens") or 0),
            "output_tokens": int(統計.get("output_tokens") or 0),
            "reasoning_tokens": int(統計.get("reasoning_tokens") or 0),
            "estimated_cost_usd": float(統計.get("estimated_cost_usd") or 0.0),
            "db_path": str(self.資料庫路徑),
            "db_size_bytes": int(db_size),
        }

    def 匯出工作階段JSONL(self, 輸出路徑: str | Path, limit: int = 1000, include_archived: bool = False, source: str | None = None, user_id: str | None = None) -> dict[str, Any]:
        """把 logical sessions 匯出成 JSONL 檔案。

        參數：
            輸出路徑: JSONL 輸出檔案。
            limit: 最多匯出幾個 logical sessions。
            include_archived: 是否包含封存 sessions。
            source: 可選來源平台篩選。
            user_id: 可選使用者識別碼篩選。

        返回值：dict[str, Any]。包含 output、session_count 與 message_count。
        """
        sessions = self.列出工作階段(limit=limit, include_archived=include_archived, source=source, user_id=user_id)
        路徑 = Path(輸出路徑).expanduser()
        路徑.parent.mkdir(parents=True, exist_ok=True)
        訊息總數 = 0
        with 路徑.open("w", encoding="utf-8") as handle:
            for session in sessions:
                sid = str(session["id"])
                messages = self.讀取訊息(sid, include_ancestors=True)
                訊息總數 += len(messages)
                handle.write(json.dumps({"session": session, "messages": messages}, ensure_ascii=False) + "\n")
        return {"output": str(路徑), "session_count": len(sessions), "message_count": 訊息總數}

    def rewind到訊息(self, 工作階段識別碼: str, 目標訊息id: int) -> dict[str, Any]:
        """把指定訊息及之後的訊息標記為 inactive，保留 audit 紀錄。

        參數：
            工作階段識別碼: session id。
            目標訊息id: 要 rewind 到的 message row id；該 row 也會被停用。

        返回值：
            dict[str, Any]：包含 rewound_count、target_message 與 new_head_id。
        """
        with self._鎖:
            目標資料列 = self.連線.execute("SELECT * FROM messages WHERE id=? AND session_id=?", (目標訊息id, 工作階段識別碼)).fetchone()
        if not 目標資料列:
            raise ValueError(f"message {目標訊息id} not found in session {工作階段識別碼}")
        def 寫入(資料庫連線: sqlite3.Connection) -> list[int]:
            """在交易中把 rewind 範圍標記為 inactive。

            參數：
                資料庫連線: 目前交易中的 SQLite connection。

            返回值：list[int]。本次被停用的 message id 清單。
            """
            資料列清單 = 資料庫連線.execute("SELECT id FROM messages WHERE session_id=? AND id>=? AND active=1", (工作階段識別碼, 目標訊息id)).fetchall()
            停用識別碼清單 = [資料列["id"] for 資料列 in 資料列清單]
            if 停用識別碼清單:
                佔位符清單 = ",".join("?" for _ in 停用識別碼清單)
                資料庫連線.execute(f"UPDATE messages SET active=0 WHERE id IN ({佔位符清單})", 停用識別碼清單)
            資料庫連線.execute("UPDATE sessions SET rewind_count=rewind_count+1, updated_at=? WHERE id=?", (time.time(), 工作階段識別碼))
            return 停用識別碼清單
        停用識別碼清單 = self._執行寫入(寫入)
        with self._鎖:
            新前端資料列 = self.連線.execute("SELECT MAX(id) AS id FROM messages WHERE session_id=? AND active=1", (工作階段識別碼,)).fetchone()
        return {"rewound_count": len(停用識別碼清單), "target_message": self._資料列轉訊息(目標資料列), "new_head_id": 新前端資料列["id"] if 新前端資料列 else None}


# Hermes-parity API 相容別名；專案內部仍使用繁中方法名稱。
setattr(工作階段庫, "append_message", 工作階段庫.附加單一訊息)
setattr(工作階段庫, "replace_messages", 工作階段庫.替換訊息清單)
