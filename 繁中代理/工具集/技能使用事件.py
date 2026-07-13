"""技能使用事件（append-only）— 未來 BigQuery `skill_usage_events` 表的本地對應。

每當技能被使用，會 append 一筆 {skill_id, user_id, used_at} 到
`assets/skill_usage_events.jsonl`。依設計由 runtime 在**每一輪結束時**，把該輪
用過（skill_view 成功）的技能各記一筆（去重後），而非每次呼叫就記，避免灌水。

Curator 讀取本檔，彙總成每 (user_id, skill_id) 的 use_count / last_used_at，寫進
技能使用量（表三）。events 為 **append-only**，Curator 只讀不改。

未來規劃：本檔以 JSONL 當儲存後端；日後改接 BigQuery `skill_usage_events` 表時，
只需替換 `記錄多筆事件` / `讀取所有事件` 兩個 I/O 出入口。
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from .. import 基本工具

logger = logging.getLogger(__name__)


def _取得BigQuery技能庫():
    """取得雲端技能庫；未啟用 BigQuery 後端時回傳 None（改走本機 JSONL）。

    參數：無。
    返回值：BigQuery技能庫 實例（STORAGE_BACKEND=bigquery 時），否則 None。
    """
    from ..BigQuery技能庫 import 取得啟用中的技能庫

    return 取得啟用中的技能庫()


def 事件檔路徑() -> Path:
    """回傳 skill_usage_events.jsonl 路徑（assets 根目錄，與 user_skill 同層）。"""
    return 基本工具.使用者技能根目錄().parent / "skill_usage_events.jsonl"


def 產生目前時間字串() -> str:
    """產生目前 UTC 時間的 ISO 8601 字串，供使用事件的 used_at 欄位使用。

    參數：無。
    返回值：str，ISO 8601 時間字串，例如 `2026-07-13T08:30:00+00:00`。
    """
    return datetime.now(timezone.utc).isoformat()


def 記錄多筆事件(skill_ids: Iterable[str], user_id: str | None, used_at: str | None = None) -> int:
    """把多個技能各 append 一筆使用事件；回傳實際寫入筆數。

    參數：
        skill_ids: 該輪用過的技能 id（呼叫端應先去重）。
        user_id: 觸發使用的使用者（來源為工具收到的 `_current_user_id`）。
        used_at: 使用時間 ISO 字串；預設為現在。
    """
    識別碼清單 = [str(s) for s in skill_ids if s]
    if not 識別碼清單:
        return 0
    時間 = used_at or 產生目前時間字串()
    使用者 = str(user_id) if user_id else None
    技能庫 = _取得BigQuery技能庫()
    if 技能庫 is not None:
        return 技能庫.記錄多筆事件(識別碼清單, 使用者, 時間)
    路徑 = 事件檔路徑()
    try:
        路徑.parent.mkdir(parents=True, exist_ok=True)
        with open(路徑, "a", encoding="utf-8") as f:
            for skill_id in 識別碼清單:
                f.write(json.dumps(
                    {"skill_id": skill_id, "user_id": 使用者, "used_at": 時間},
                    ensure_ascii=False,
                ) + "\n")
    except OSError as 錯誤:
        logger.debug("寫入 %s 失敗：%s", 路徑, 錯誤, exc_info=True)
        return 0
    return len(識別碼清單)


def 記錄事件(skill_id: str, user_id: str | None, used_at: str | None = None) -> int:
    """append 單筆使用事件；回傳寫入筆數（0 或 1）。"""
    if not skill_id:
        return 0
    return 記錄多筆事件([skill_id], user_id, used_at)


def 讀取所有事件() -> list[dict[str, Any]]:
    """讀取整份事件檔；毀損的行會被略過。"""
    技能庫 = _取得BigQuery技能庫()
    if 技能庫 is not None:
        return 技能庫.讀取所有事件()
    路徑 = 事件檔路徑()
    if not 路徑.exists():
        return []
    事件清單: list[dict[str, Any]] = []
    try:
        內容 = 路徑.read_text(encoding="utf-8")
    except OSError as 錯誤:
        logger.debug("讀取 %s 失敗：%s", 路徑, 錯誤)
        return []
    for 行 in 內容.splitlines():
        行 = 行.strip()
        if not 行:
            continue
        try:
            物件 = json.loads(行)
        except json.JSONDecodeError:
            continue  # 略過毀損行，不讓整份掛掉
        if isinstance(物件, dict):
            事件清單.append(物件)
    return 事件清單


def 彙總() -> list[dict[str, Any]]:
    """把事件彙總成每 (user_id, skill_id) 的 use_count / last_used_at。

    回傳 [{skill_id, user_id, use_count, last_used_at}, ...]，供 Curator 更新
    技能使用量（表三）。同名技能在不同 user 下分屬不同列（key 為
    (user_id, skill_id)）。
    """
    彙整表: dict[tuple[str | None, str], dict[str, Any]] = {}
    for 事件 in 讀取所有事件():
        skill_id = 事件.get("skill_id")
        if not skill_id:
            continue
        skill_id = str(skill_id)
        user_id = 事件.get("user_id")
        used_at = 事件.get("used_at")
        鍵 = (user_id, skill_id)
        列 = 彙整表.setdefault(鍵, {
            "skill_id": skill_id,
            "user_id": user_id,
            "use_count": 0,
            "last_used_at": None,
        })
        列["use_count"] += 1
        # ISO 8601 UTC 字串可直接字典序比較取最大
        if used_at and (列["last_used_at"] is None or str(used_at) > str(列["last_used_at"])):
            列["last_used_at"] = used_at
    return sorted(彙整表.values(), key=lambda 列: (str(列["user_id"]), 列["skill_id"]))
