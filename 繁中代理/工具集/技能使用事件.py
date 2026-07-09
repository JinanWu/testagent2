"""技能使用事件（append-only）— 未來 BigQuery `skill_usage_events` 表的本地對應。

每當技能被使用，會 append 一筆 {skill_id, user_id, used_at} 到
`assets/skill_usage_events.jsonl`。依設計由 runtime 在**每一輪結束時**，把該輪
用過（skill_view 成功）的技能各記一筆（去重後），而非每次呼叫就記，避免灌水。

Curator 讀取本檔，先彙總成每 (user_id, skill_id) 的 use_count / last_used_at，
再依 skill_id 合併後寫進技能使用量（表三）。events 為 **append-only**，Curator 只讀不改。

未來規劃：本檔以 JSONL 當儲存後端；日後改接 BigQuery `skill_usage_events` 表時，
只需替換 `記錄多筆技能使用事件` / `讀取全部技能使用事件` 兩個 I/O 出入口。
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from .. import 基本工具

logger = logging.getLogger(__name__)


def 取得技能使用事件檔路徑() -> Path:
    """回傳 skill_usage_events.jsonl 路徑（assets 根目錄，與 user_skill 同層）。"""
    return 基本工具.使用者技能根目錄().parent / "skill_usage_events.jsonl"


def _產生現在ISO時間() -> str:
    return datetime.now(timezone.utc).isoformat()


def 記錄多筆技能使用事件(skill_ids: Iterable[str], user_id: str | None, used_at: str | None = None) -> int:
    """把多個技能各 append 一筆使用事件；回傳實際寫入筆數。

    參數：
        skill_ids: 該輪用過的技能 id（呼叫端應先去重）。
        user_id: 觸發使用的使用者（來源為工具收到的 `_current_user_id`）。
        used_at: 使用時間 ISO 字串；預設為現在。
    """
    識別碼清單 = [str(s) for s in skill_ids if s]
    if not 識別碼清單:
        return 0
    時間 = used_at or _產生現在ISO時間()
    使用者 = str(user_id) if user_id else None
    路徑 = 取得技能使用事件檔路徑()
    內容 = "".join(
        json.dumps(
            {"skill_id": skill_id, "user_id": 使用者, "used_at": 時間},
            ensure_ascii=False,
        ) + "\n"
        for skill_id in 識別碼清單
    )
    try:
        路徑.parent.mkdir(parents=True, exist_ok=True)
        with open(路徑, "a", encoding="utf-8") as f:
            f.write(內容)
    except OSError as 錯誤:
        logger.debug("寫入 %s 失敗：%s", 路徑, 錯誤, exc_info=True)
        return 0
    return len(識別碼清單)


def 記錄技能使用事件(skill_id: str, user_id: str | None, used_at: str | None = None) -> int:
    """append 單筆使用事件；回傳寫入筆數（0 或 1）。"""
    if not skill_id:
        return 0
    return 記錄多筆技能使用事件([skill_id], user_id, used_at)


def 讀取全部技能使用事件() -> list[dict[str, Any]]:
    """讀取整份事件檔；毀損的行會被略過。"""
    路徑 = 取得技能使用事件檔路徑()
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


def 彙總技能使用事件() -> list[dict[str, Any]]:
    """把事件彙總成每 (user_id, skill_id) 的 use_count / last_used_at。

    回傳 [{skill_id, user_id, use_count, last_used_at}, ...]，供 Curator 更新
    技能使用量（表三）。同名技能在不同 user 下分屬不同列（key 為
    (user_id, skill_id)）。
    """
    彙整表: dict[tuple[str | None, str], dict[str, Any]] = {}
    for 事件 in 讀取全部技能使用事件():
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


def 彙總技能使用事件依技能() -> list[dict[str, Any]]:
    """把事件彙總成每 skill_id 的 use_count / last_used_at（跨 user 合併）。

    回傳 [{skill_id, user_id, use_count, last_used_at}, ...]，供 Curator 寫入
    技能使用量（表三）。表三僅以 skill_id 為鍵，因此需合併各 user 的事件：
    use_count 加總、last_used_at 取最新，user_id 取最後使用該技能的使用者。
    """
    彙整表: dict[str, dict[str, Any]] = {}
    for 列 in 彙總技能使用事件():
        skill_id = 列["skill_id"]
        if skill_id not in 彙整表:
            彙整表[skill_id] = {
                "skill_id": skill_id,
                "user_id": 列.get("user_id"),
                "use_count": 0,
                "last_used_at": None,
            }
        合併 = 彙整表[skill_id]
        合併["use_count"] += 列.get("use_count") or 0
        last_used_at = 列.get("last_used_at")
        if last_used_at and (
            合併["last_used_at"] is None or str(last_used_at) > str(合併["last_used_at"])
        ):
            合併["last_used_at"] = last_used_at
            合併["user_id"] = 列.get("user_id")
    return sorted(彙整表.values(), key=lambda 列: 列["skill_id"])
