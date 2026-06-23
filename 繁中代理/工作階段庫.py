"""SQLite 工作階段儲存層。

功能：
    提供 Hermes-style session persistence。使用者訊息進入 turn 後會先寫入
    SQLite，降低程式中途崩潰時遺失使用者請求的風險。assistant tool_call
    訊息與 tool result 會先放入 working messages，並在 tool loop 的持久化點
    一次 flush。

主要資料表：
    sessions：保存 session id、標題、建立時間、更新時間與 system prompt 快照。
    messages：保存 OpenAI-compatible canonical message dict 的 JSON。

所有專案自有函數名稱採用動詞 + 受詞，布林值使用「是否...」形式。
"""

from __future__ import annotations

import json
import sqlite3
import time
import uuid
from pathlib import Path
from typing import Any


class 工作階段庫:
    """管理 SQLite 內的會話與訊息。

    參數：
        資料庫路徑: SQLite 檔案路徑；若父層資料夾不存在會自動建立。

    返回值：
        類別實例；可用於建立、讀取、寫入與更新工作階段。
    """

    def __init__(self, 資料庫路徑: str | Path) -> None:
        """初始化工作階段庫並建立必要資料表。

        參數：
            資料庫路徑: SQLite 檔案路徑。

        返回值：
            None。
        """
        self.資料庫路徑 = Path(資料庫路徑)
        self.資料庫路徑.parent.mkdir(parents=True, exist_ok=True)
        self.連線 = sqlite3.connect(self.資料庫路徑)
        self.連線.row_factory = sqlite3.Row
        self.建立資料表()

    def 建立資料表(self) -> None:
        """建立 sessions 與 messages 資料表。

        參數：
            無。

        返回值：
            None。
        """
        self.連線.executescript(
            """
            CREATE TABLE IF NOT EXISTS sessions (
                id TEXT PRIMARY KEY,
                title TEXT,
                system_prompt TEXT,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL
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
            """
        )
        self.連線.commit()

    def 建立或讀取工作階段(self, 工作階段識別碼: str | None = None) -> str:
        """建立新工作階段或確認既有工作階段存在。

        參數：
            工作階段識別碼: 可選的 session id；若為 None 則自動產生。

        返回值：
            可用的 session id。
        """
        目前時間 = time.time()
        識別碼 = 工作階段識別碼 or f"session-{uuid.uuid4().hex[:12]}"
        self.連線.execute(
            "INSERT OR IGNORE INTO sessions(id, title, created_at, updated_at) VALUES (?, ?, ?, ?)",
            (識別碼, 識別碼, 目前時間, 目前時間),
        )
        self.連線.commit()
        return 識別碼

    def 讀取工作階段(self, 工作階段識別碼: str) -> dict[str, Any] | None:
        """讀取單一工作階段資料。

        參數：
            工作階段識別碼: 欲讀取的 session id。

        返回值：
            dict 或 None；包含 id、title、system_prompt 等欄位。
        """
        資料列 = self.連線.execute("SELECT * FROM sessions WHERE id=?", (工作階段識別碼,)).fetchone()
        return dict(資料列) if 資料列 else None

    def 更新系統提示詞(self, 工作階段識別碼: str, 系統提示詞: str) -> None:
        """把穩定的 system prompt 快照寫回 session row。

        參數：
            工作階段識別碼: session id。
            系統提示詞: 完整 system prompt 字串。

        返回值：
            None。
        """
        self.連線.execute(
            "UPDATE sessions SET system_prompt=?, updated_at=? WHERE id=?",
            (系統提示詞, time.time(), 工作階段識別碼),
        )
        self.連線.commit()

    def 讀取訊息(self, 工作階段識別碼: str) -> list[dict[str, Any]]:
        """依序讀取某工作階段的 canonical messages。

        參數：
            工作階段識別碼: session id。

        返回值：
            OpenAI-compatible message dict 清單。
        """
        資料列清單 = self.連線.execute(
            "SELECT content_json FROM messages WHERE session_id=? ORDER BY message_index",
            (工作階段識別碼,),
        ).fetchall()
        return [json.loads(資料列["content_json"]) for 資料列 in 資料列清單]

    def 寫入訊息清單(self, 工作階段識別碼: str, 訊息清單: list[dict[str, Any]]) -> None:
        """以目前 working messages 覆寫 session 的訊息快照。

        參數：
            工作階段識別碼: session id。
            訊息清單: OpenAI-compatible message dict 清單。

        返回值：
            None。
        """
        with self.連線:
            self.連線.execute("DELETE FROM messages WHERE session_id=?", (工作階段識別碼,))
            for 索引, 訊息 in enumerate(訊息清單):
                self.連線.execute(
                    "INSERT INTO messages(session_id, message_index, role, content_json, created_at) VALUES (?, ?, ?, ?, ?)",
                    (工作階段識別碼, 索引, 訊息.get("role", ""), json.dumps(訊息, ensure_ascii=False), time.time()),
                )
            self.連線.execute("UPDATE sessions SET updated_at=? WHERE id=?", (time.time(), 工作階段識別碼))
