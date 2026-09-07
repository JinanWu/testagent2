"""以 PostgreSQL 儲存 owner-scoped 使用者技能、使用量快照與使用事件。"""
from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import Any, Iterable

from psycopg.types.json import Jsonb

from .PostgreSQL連線 import 交易連線
from .環境設定 import 交易儲存設定

使用量欄位 = ("user_id", "use_count", "last_used_at", "state", "pinned", "created_at")
技能欄位 = ("skill_id", "user_id", "name", "category", "content", "created_at", "updated_at")
事件欄位 = ("skill_id", "user_id", "used_at")


class PostgreSQL技能庫:
    """只經由 ``交易連線`` 操作 PostgreSQL；不提供檔案系統降級路徑。"""

    def __init__(self, 凍結設定: 交易儲存設定) -> None:
        self.凍結設定 = 凍結設定

    @staticmethod
    def _owner(user_id: str | None) -> str:
        if not user_id:
            raise ValueError("user_id 不可為空")
        return str(user_id)

    @staticmethod
    def _字典列(列: Any, 欄位: tuple[str, ...]) -> dict[str, Any]:
        if isinstance(列, dict):
            return {鍵: 列.get(鍵) for 鍵 in 欄位}
        return dict(zip(欄位, 列, strict=False))

    @staticmethod
    def _時間(值: datetime | str | None) -> datetime:
        """將舊 API 的 ISO 字串轉成 PostgreSQL timestamptz 所需的 aware datetime。"""
        if 值 is None:
            return datetime.now(timezone.utc)
        if isinstance(值, str):
            try:
                值 = datetime.fromisoformat(值.replace("Z", "+00:00"))
            except ValueError as 錯誤:
                raise ValueError("時間必須是含時區的 ISO 8601 datetime") from 錯誤
        if not isinstance(值, datetime) or 值.tzinfo is None or 值.utcoffset() is None:
            raise ValueError("時間必須是 aware datetime")
        return 值

    def 建立技能(
        self, skill_id: str, 名稱: str, 內容: str, 分類: str | None = None,
        user_id: str | None = None, 建立時間: datetime | str | None = None,
    ) -> None:
        """建立內容並在同一交易初始化 owner 的使用量快照。"""
        擁有者 = self._owner(user_id)
        frontmatter = Jsonb({"category": 分類} if 分類 else {})
        摘要 = hashlib.sha256(內容.encode("utf-8")).hexdigest()
        時間 = self._時間(建立時間)
        with 交易連線(self.凍結設定) as 連線:
            with 連線.cursor() as 游標:
                游標.execute(
                    """INSERT INTO user_skills
                       (user_id, skill_id, name, content, frontmatter, content_sha256,
                        created_at, updated_at)
                       VALUES (%s, %s, %s, %s, %s, %s, %s, %s)""",
                    (擁有者, skill_id, 名稱, 內容, frontmatter, 摘要, 時間, 時間),
                )
                游標.execute(
                    """INSERT INTO skill_usage
                       (user_id, skill_id, use_count, last_used_at, state, pinned, created_at)
                       VALUES (%s, %s, 0, NULL, 'active', FALSE, %s)
                       ON CONFLICT (user_id, skill_id) DO NOTHING""",
                    (擁有者, skill_id, 時間),
                )

    def 讀取技能內容(
        self, 名稱: str, user_id: str | None = None, 限定使用者: bool = True,
    ) -> dict[str, Any] | None:
        del 限定使用者  # 本 adapter 永遠 owner-scoped。
        擁有者 = self._owner(user_id)
        with 交易連線(self.凍結設定) as 連線:
            with 連線.cursor() as 游標:
                游標.execute(
                    """SELECT skill_id, user_id, name,
                              frontmatter ->> 'category' AS category,
                              content, created_at, updated_at
                       FROM user_skills
                       WHERE user_id = %s AND name = %s LIMIT 1""",
                    (擁有者, 名稱),
                )
                列 = 游標.fetchone()
        return None if 列 is None else self._字典列(列, 技能欄位)

    def 更新技能內容(
        self, skill_id: str, 內容: str, 更新時間: datetime | str | None = None,
        user_id: str | None = None,
    ) -> None:
        擁有者 = self._owner(user_id)
        摘要 = hashlib.sha256(內容.encode("utf-8")).hexdigest()
        with 交易連線(self.凍結設定) as 連線:
            with 連線.cursor() as 游標:
                游標.execute(
                    """UPDATE user_skills
                       SET content = %s, content_sha256 = %s, updated_at = %s
                       WHERE user_id = %s AND skill_id = %s""",
                    (內容, 摘要, self._時間(更新時間), 擁有者, skill_id),
                )

    def 刪除技能(self, skill_id: str, user_id: str | None = None) -> None:
        """刪除 owner 的技能；schema 外鍵負責 cascade 使用量與事件。"""
        擁有者 = self._owner(user_id)
        with 交易連線(self.凍結設定) as 連線:
            with 連線.cursor() as 游標:
                游標.execute(
                    "DELETE FROM user_skills WHERE user_id = %s AND skill_id = %s",
                    (擁有者, skill_id),
                )

    def 列出技能身分(
        self, user_id: str | None = None, 是否包含封存: bool = False,
        限定使用者: bool = True,
    ) -> list[dict[str, Any]]:
        del 限定使用者
        擁有者 = self._owner(user_id)
        封存條件 = "" if 是否包含封存 else "AND COALESCE(u.state, 'active') <> 'archived'"
        with 交易連線(self.凍結設定) as 連線:
            with 連線.cursor() as 游標:
                游標.execute(
                    f"""SELECT s.skill_id, s.user_id, s.name,
                               s.frontmatter ->> 'category' AS category,
                               s.content, s.created_at, s.updated_at
                        FROM user_skills AS s
                        LEFT JOIN skill_usage AS u
                          ON u.user_id = s.user_id AND u.skill_id = s.skill_id
                        WHERE s.user_id = %s {封存條件}
                        ORDER BY s.created_at, s.skill_id""",
                    (擁有者,),
                )
                return [self._字典列(列, 技能欄位) for 列 in 游標.fetchall()]

    def 讀取全部使用量(self, user_id: str | None = None) -> dict[str, dict[str, Any]]:
        擁有者 = self._owner(user_id)
        with 交易連線(self.凍結設定) as 連線:
            with 連線.cursor() as 游標:
                游標.execute(
                    """SELECT skill_id, user_id, use_count, last_used_at, state, pinned, created_at
                       FROM skill_usage WHERE user_id = %s ORDER BY skill_id""",
                    (擁有者,),
                )
                列清單 = 游標.fetchall()
        結果: dict[str, dict[str, Any]] = {}
        for 原列 in 列清單:
            列 = self._字典列(原列, ("skill_id", *使用量欄位))
            skill_id = 列.pop("skill_id")
            if skill_id is not None:
                結果[str(skill_id)] = 列
        return 結果

    def 讀取使用量列(self, skill_id: str, user_id: str | None = None) -> dict[str, Any] | None:
        if not skill_id:
            return None
        擁有者 = self._owner(user_id)
        with 交易連線(self.凍結設定) as 連線:
            with 連線.cursor() as 游標:
                游標.execute(
                    """SELECT user_id, use_count, last_used_at, state, pinned, created_at
                       FROM skill_usage WHERE user_id = %s AND skill_id = %s LIMIT 1""",
                    (擁有者, skill_id),
                )
                列 = 游標.fetchone()
        return None if 列 is None else self._字典列(列, 使用量欄位)

    def 覆寫使用量列(
        self, skill_id: str, 記錄: dict[str, Any], user_id: str | None = None,
    ) -> None:
        if not skill_id or not isinstance(記錄, dict):
            return
        狀態 = 記錄.get("state") or "active"
        if 狀態 not in {"active", "stale", "archived"}:
            return
        擁有者 = self._owner(user_id if user_id is not None else 記錄.get("user_id"))
        最後使用時間 = 記錄.get("last_used_at")
        if 最後使用時間 is not None:
            最後使用時間 = self._時間(最後使用時間)
        建立時間 = self._時間(記錄.get("created_at"))
        with 交易連線(self.凍結設定) as 連線:
            with 連線.cursor() as 游標:
                游標.execute(
                    """INSERT INTO skill_usage
                       (user_id, skill_id, use_count, last_used_at, state, pinned, created_at)
                       VALUES (%s, %s, %s, %s, %s, %s, %s)
                       ON CONFLICT (user_id, skill_id) DO UPDATE SET
                         use_count = EXCLUDED.use_count,
                         last_used_at = EXCLUDED.last_used_at,
                         state = EXCLUDED.state,
                         pinned = EXCLUDED.pinned,
                         updated_at = CURRENT_TIMESTAMP""",
                    (擁有者, skill_id, int(記錄.get("use_count") or 0),
                     最後使用時間, 狀態, bool(記錄.get("pinned")), 建立時間),
                )

    def 寫入全部使用量(
        self, 使用量資料: dict[str, dict[str, Any]], user_id: str | None = None,
    ) -> None:
        for skill_id, 記錄 in 使用量資料.items():
            if isinstance(記錄, dict):
                self.覆寫使用量列(str(skill_id), 記錄, user_id=user_id)

    def 刪除使用量列(self, skill_id: str, user_id: str | None = None) -> None:
        if not skill_id:
            return
        擁有者 = self._owner(user_id)
        with 交易連線(self.凍結設定) as 連線:
            with 連線.cursor() as 游標:
                游標.execute(
                    "DELETE FROM skill_usage WHERE user_id = %s AND skill_id = %s",
                    (擁有者, skill_id),
                )

    def 設定狀態(self, skill_id: str, 狀態: str, user_id: str | None = None) -> None:
        if 狀態 not in {"active", "stale", "archived"}:
            return
        擁有者 = self._owner(user_id)
        with 交易連線(self.凍結設定) as 連線:
            with 連線.cursor() as 游標:
                游標.execute(
                    "UPDATE skill_usage SET state = %s, updated_at = CURRENT_TIMESTAMP WHERE user_id = %s AND skill_id = %s",
                    (狀態, 擁有者, skill_id),
                )

    def 設定pin(self, skill_id: str, pinned: bool, user_id: str | None = None) -> None:
        擁有者 = self._owner(user_id)
        with 交易連線(self.凍結設定) as 連線:
            with 連線.cursor() as 游標:
                游標.execute(
                    "UPDATE skill_usage SET pinned = %s, updated_at = CURRENT_TIMESTAMP WHERE user_id = %s AND skill_id = %s",
                    (bool(pinned), 擁有者, skill_id),
                )

    def 記錄多筆事件(
        self, 技能識別碼清單: Iterable[str], 使用者識別碼: str | None,
        使用時間: datetime | str,
    ) -> int:
        擁有者 = self._owner(使用者識別碼)
        識別碼清單 = [str(識別碼) for 識別碼 in 技能識別碼清單 if 識別碼]
        if not 識別碼清單:
            return 0
        時間 = self._時間(使用時間)
        with 交易連線(self.凍結設定) as 連線:
            with 連線.cursor() as 游標:
                游標.executemany(
                    "INSERT INTO skill_usage_events (user_id, skill_id, used_at) VALUES (%s, %s, %s)",
                    [(擁有者, skill_id, 時間) for skill_id in 識別碼清單],
                )
        return len(識別碼清單)

    def 讀取所有事件(self, user_id: str | None = None) -> list[dict[str, Any]]:
        擁有者 = self._owner(user_id)
        with 交易連線(self.凍結設定) as 連線:
            with 連線.cursor() as 游標:
                游標.execute(
                    """SELECT skill_id, user_id, used_at FROM skill_usage_events
                       WHERE user_id = %s ORDER BY used_at, skill_id""",
                    (擁有者,),
                )
                return [self._字典列(列, 事件欄位) for 列 in 游標.fetchall()]

    def 彙總事件(self, user_id: str | None = None) -> list[dict[str, Any]]:
        擁有者 = self._owner(user_id)
        欄位 = ("skill_id", "user_id", "use_count", "last_used_at")
        with 交易連線(self.凍結設定) as 連線:
            with 連線.cursor() as 游標:
                游標.execute(
                    """SELECT skill_id, user_id, COUNT(*) AS use_count, MAX(used_at) AS last_used_at
                       FROM skill_usage_events WHERE user_id = %s
                       GROUP BY user_id, skill_id ORDER BY skill_id""",
                    (擁有者,),
                )
                return [self._字典列(列, 欄位) for 列 in 游標.fetchall()]

    設定技能生命狀態 = 設定狀態
    設定技能Pin = 設定pin
    彙總 = 彙總事件
