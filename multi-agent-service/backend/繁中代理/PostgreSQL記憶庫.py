"""PostgreSQL owner-scoped MEMORY/USER repository 與 frozen prompt snapshot。"""
from __future__ import annotations

import hashlib
from typing import Any

from psycopg.types.json import Jsonb

from .PostgreSQL連線 import 交易連線
from .環境設定 import 交易儲存設定
from .提示詞組裝器 import 掃描提示注入內容

記憶分隔符 = "\n§\n"
有效目標 = {"memory", "user"}


class PostgreSQL記憶庫:
    """以 user_memories 為唯一權威；不讀寫 MEMORY.md 或 USER.md。"""

    def __init__(
        self,
        凍結設定: 交易儲存設定,
        user_id: str,
        記憶字數限制: int = 2200,
        使用者字數限制: int = 1375,
    ) -> None:
        if not user_id:
            raise ValueError("user_id 不可為空")
        self.凍結設定 = 凍結設定
        self.user_id = str(user_id)
        self.記憶字數限制 = 記憶字數限制
        self.使用者字數限制 = 使用者字數限制
        self.記憶項目: list[str] = []
        self.使用者項目: list[str] = []
        self.快照 = {"memory": "", "user": ""}

    def 載入(self) -> None:
        """讀取 owner 的兩個 target，並固定後續 prompt 使用的安全快照。"""
        self.記憶項目 = self._讀取("memory")
        self.使用者項目 = self._讀取("user")
        self.快照 = {
            目標: self._格式化區塊(目標, self._清理快照(self._項目(目標), 檔名))
            for 目標, 檔名 in (("memory", "MEMORY.md"), ("user", "USER.md"))
        }

    def 新增(self, 目標: str, 內容: str) -> dict[str, Any]:
        錯誤 = self._驗證目標(目標)
        if 錯誤:
            return 錯誤
        內容 = 內容.strip()
        錯誤 = self._驗證內容(內容)
        if 錯誤:
            return 錯誤
        項目 = self._項目(目標)
        if 內容 in 項目:
            return self._成功(目標, "Entry already exists")
        檢查 = self._檢查容量(目標, [*項目, 內容])
        if 檢查:
            return 檢查
        新項目 = [*項目, 內容]
        self._寫入(目標, 新項目)
        項目[:] = 新項目
        return self._成功(目標, "Entry added")

    def 取代(self, 目標: str, 舊文字: str, 新內容: str) -> dict[str, Any]:
        return self._更新(目標, 舊文字, 新內容.strip())

    def 移除(self, 目標: str, 舊文字: str) -> dict[str, Any]:
        return self._更新(目標, 舊文字, None)

    def 格式化給系統提示(self, 目標: str) -> str:
        """只回傳最近一次 ``載入`` 固定的快照，不讀 live PostgreSQL。"""
        return self.快照.get(目標, "")

    def _更新(self, 目標: str, 舊文字: str, 新內容: str | None) -> dict[str, Any]:
        錯誤 = self._驗證目標(目標)
        if 錯誤:
            return 錯誤
        項目 = self._項目(目標)
        符合 = [i for i, 值 in enumerate(項目) if 舊文字.strip() and 舊文字 in 值]
        if len(符合) != 1:
            return {"success": False, "error": "找不到唯一符合項目", "matches": len(符合)}
        新項目 = [*項目]
        if 新內容 is None:
            新項目.pop(符合[0])
            訊息 = "Entry removed"
        else:
            錯誤 = self._驗證內容(新內容)
            if 錯誤:
                return 錯誤
            新項目[符合[0]] = 新內容
            檢查 = self._檢查容量(目標, 新項目)
            if 檢查:
                return 檢查
            訊息 = "Entry replaced"
        self._寫入(目標, 新項目)
        項目[:] = 新項目
        return self._成功(目標, 訊息)

    @staticmethod
    def _驗證目標(目標: str) -> dict[str, Any] | None:
        if 目標 not in 有效目標:
            return {"success": False, "error": "target 必須是 memory 或 user"}
        return None

    @staticmethod
    def _驗證內容(內容: str) -> dict[str, Any] | None:
        if not 內容:
            return {"success": False, "error": "content 不可為空"}
        掃描結果 = 掃描提示注入內容(內容, "memory")
        return None if 掃描結果 == 內容 else {"success": False, "error": 掃描結果}

    def _項目(self, 目標: str) -> list[str]:
        return self.使用者項目 if 目標 == "user" else self.記憶項目

    def _限制(self, 目標: str) -> int:
        return self.使用者字數限制 if 目標 == "user" else self.記憶字數限制

    def _讀取(self, 目標: str) -> list[str]:
        with 交易連線(self.凍結設定) as 連線:
            with 連線.cursor() as 游標:
                游標.execute(
                    """SELECT content, content_json FROM user_memories
                       WHERE user_id = %s AND namespace = 'hermes'
                         AND memory_key = %s LIMIT 1""",
                    (self.user_id, 目標),
                )
                列 = 游標.fetchone()
        if 列 is None:
            return []
        if isinstance(列, dict):
            原文 = 列.get("content")
            結構化 = 列.get("content_json")
        else:
            原文 = 列[0]
            結構化 = 列[1] if len(列) > 1 else None
        if isinstance(結構化, list) and all(isinstance(項, str) for 項 in 結構化):
            return list(dict.fromkeys(項.strip() for 項 in 結構化 if 項.strip()))
        if not 原文:
            return []
        return list(dict.fromkeys(項.strip() for 項 in str(原文).split(記憶分隔符) if 項.strip()))

    def _寫入(self, 目標: str, 項目: list[str]) -> None:
        內容 = 記憶分隔符.join(項目)
        記憶識別碼 = hashlib.sha256(
            f"{self.user_id}\0hermes\0{目標}".encode("utf-8")
        ).hexdigest()
        with 交易連線(self.凍結設定) as 連線:
            with 連線.cursor() as 游標:
                游標.execute(
                    """INSERT INTO user_memories
                       (id, user_id, namespace, memory_key, content, content_json,
                        metadata, updated_at)
                       VALUES (%s, %s, 'hermes', %s, %s, %s, %s, CURRENT_TIMESTAMP)
                       ON CONFLICT (user_id, namespace, memory_key) DO UPDATE SET
                         content = EXCLUDED.content,
                         content_json = EXCLUDED.content_json,
                         metadata = EXCLUDED.metadata,
                         updated_at = CURRENT_TIMESTAMP""",
                    (記憶識別碼, self.user_id, 目標, 內容, Jsonb(項目), Jsonb({})),
                )

    def _檢查容量(self, 目標: str, 項目: list[str]) -> dict[str, Any] | None:
        總長 = len(記憶分隔符.join(項目))
        限制 = self._限制(目標)
        if 總長 <= 限制:
            return None
        return {
            "success": False,
            "error": f"Memory at {總長}/{限制} chars",
            "current_entries": self._項目(目標),
        }

    @staticmethod
    def _清理快照(項目: list[str], 檔名: str) -> list[str]:
        return [
            值
            if 掃描提示注入內容(值, 檔名) == 值
            else f"[BLOCKED: {檔名} entry contained threat pattern.]"
            for 值 in 項目
        ]

    def _格式化區塊(self, 目標: str, 項目: list[str]) -> str:
        if not 項目:
            return ""
        內容 = 記憶分隔符.join(項目)
        標題 = "USER PROFILE (who the user is)" if 目標 == "user" else "MEMORY (your personal notes)"
        return (
            f"{'═' * 46}\n{標題} [{len(內容)}/{self._限制(目標)} chars]\n"
            f"{'═' * 46}\n{內容}"
        )

    def _成功(self, 目標: str, 訊息: str) -> dict[str, Any]:
        return {"success": True, "target": 目標, "entries": self._項目(目標), "message": 訊息}


# 組裝端可用與本機類別一致的短名稱。
記憶存放 = PostgreSQL記憶庫
