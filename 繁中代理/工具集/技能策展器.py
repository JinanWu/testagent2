"""技能策展器（Curator）— 技能生命週期自動維護。

設計上每 72 小時跑一次（cron / 排程），分兩階段：

  1. 彙總：讀技能使用事件（表二）→ 覆寫技能使用量（表三）的 use_count / last_used_at，
     讓表三成為事件表的物化快照。
  2. 轉移：讀表三的 last_used_at，依門檻天數決定生命週期：
       active   → stale      閒置超過 `閒置門檻天數`
       stale/active → archived  閒置超過 `封存門檻天數`（把技能目錄搬到 .archive/）
       stale    → active     又被使用（last_used_at 變新）就復活
     **pinned 技能一律跳過**，永不自動轉移。

只處理 user_skill 底下的技能（內建技能無 skill_id、不受管理）。門檻可用環境變數覆寫。
"""

from __future__ import annotations

import logging
import os
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .. import 基本工具
from . import 技能使用事件, 技能使用量

logger = logging.getLogger(__name__)

預設閒置門檻天數 = 30
預設封存門檻天數 = 60


def 閒置門檻天數() -> int:
    """閒置多久標記為 stale（天）；可用 TESTAGENT2_SKILL_STALE_DAYS 覆寫。"""
    try:
        return int(os.getenv("TESTAGENT2_SKILL_STALE_DAYS") or 預設閒置門檻天數)
    except ValueError:
        return 預設閒置門檻天數


def 封存門檻天數() -> int:
    """閒置多久搬去封存（天）；可用 TESTAGENT2_SKILL_ARCHIVE_DAYS 覆寫。"""
    try:
        return int(os.getenv("TESTAGENT2_SKILL_ARCHIVE_DAYS") or 預設封存門檻天數)
    except ValueError:
        return 預設封存門檻天數


def _解析ISO(值: Any) -> datetime | None:
    """把 ISO 字串解析成帶時區的 datetime；失敗回傳 None。"""
    if not 值:
        return None
    try:
        解析 = datetime.fromisoformat(str(值))
    except (TypeError, ValueError):
        return None
    if 解析.tzinfo is None:
        解析 = 解析.replace(tzinfo=timezone.utc)
    return 解析


def 封存根目錄() -> Path:
    """封存倉庫目錄（user_skill/.archive）。"""
    return 基本工具.使用者技能根目錄() / ".archive"


# ---------------------------------------------------------------------------
# 階段一：彙總事件 → 覆寫表三
# ---------------------------------------------------------------------------

def 彙總事件到使用量() -> int:
    """把事件表彙總結果覆寫進使用量表三；只更新目前仍存在於 user_skill 的技能。

    回傳更新的技能數。已刪除/封存的技能（不在 user_skill）不會被重建成殭屍記錄。
    """
    存在ids = {身分.get("skill_id") for 身分 in 基本工具.列出使用者技能身分() if 身分.get("skill_id")}
    更新數 = 0
    for 列 in 技能使用事件.彙總():
        skill_id = 列.get("skill_id")
        if skill_id not in 存在ids:
            continue
        技能使用量.設定彙總(skill_id, 列.get("use_count") or 0, 列.get("last_used_at"), user_id=列.get("user_id"))
        更新數 += 1
    return 更新數


# ---------------------------------------------------------------------------
# 階段二：生命週期轉移
# ---------------------------------------------------------------------------

def 封存技能(skill_id: str, 名稱: str) -> bool:
    """把技能目錄搬到 .archive/，並把 state 設為 archived。回傳是否成功。"""
    技能目錄: Path | None = None
    for 身分 in 基本工具.列出使用者技能身分():
        if 身分.get("skill_id") == skill_id:
            技能目錄 = Path(身分["path"]).parent
            break
    if 技能目錄 is None or not 技能目錄.exists():
        return False
    封存根 = 封存根目錄()
    封存根.mkdir(parents=True, exist_ok=True)
    目標 = 封存根 / 名稱
    if 目標.exists():
        目標 = 封存根 / f"{名稱}-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}"
    try:
        shutil.move(str(技能目錄), str(目標))
    except OSError as 錯誤:
        logger.debug("封存技能 %s 失敗：%s", 名稱, 錯誤, exc_info=True)
        return False
    技能使用量.設定狀態(skill_id, 技能使用量.狀態_封存)
    # 清掉空的 category 目錄
    父目錄 = 技能目錄.parent
    根目錄 = 基本工具.使用者技能根目錄()
    if 父目錄 != 根目錄 and 父目錄.exists() and not any(父目錄.iterdir()):
        父目錄.rmdir()
    return True


def 套用生命週期轉移(now: datetime | None = None) -> dict[str, int]:
    """依 last_used_at 對每個（未 pin 的）使用者技能做 active/stale/archived 轉移。

    錨點 = last_used_at，沒有就退回 created_at，再沒有就用 now（避免剛建就被判閒置）。
    回傳計數 dict。
    """
    if now is None:
        now = datetime.now(timezone.utc)
    閒置界線 = now - timedelta(days=閒置門檻天數())
    封存界線 = now - timedelta(days=封存門檻天數())
    計數 = {"checked": 0, "marked_stale": 0, "archived": 0, "reactivated": 0, "skipped_pinned": 0}

    for 列 in 技能使用量.使用量報告():
        計數["checked"] += 1
        if 列.get("pinned"):
            計數["skipped_pinned"] += 1
            continue
        skill_id = 列["skill_id"]
        名稱 = 列.get("name") or ""
        錨點 = _解析ISO(列.get("last_used_at")) or _解析ISO(列.get("created_at")) or now
        目前狀態 = 列.get("state", 技能使用量.狀態_使用中)

        if 錨點 <= 封存界線 and 目前狀態 != 技能使用量.狀態_封存:
            if 封存技能(skill_id, 名稱):
                計數["archived"] += 1
        elif 錨點 <= 閒置界線 and 目前狀態 == 技能使用量.狀態_使用中:
            技能使用量.設定狀態(skill_id, 技能使用量.狀態_閒置)
            計數["marked_stale"] += 1
        elif 錨點 > 閒置界線 and 目前狀態 == 技能使用量.狀態_閒置:
            技能使用量.設定狀態(skill_id, 技能使用量.狀態_使用中)
            計數["reactivated"] += 1

    return 計數


def 執行策展(now: datetime | None = None) -> dict[str, Any]:
    """Curator 主入口：先彙總事件到表三，再套用生命週期轉移。回傳統計。"""
    彙總數 = 彙總事件到使用量()
    轉移統計 = 套用生命週期轉移(now=now)
    return {"aggregated": 彙總數, **轉移統計}
