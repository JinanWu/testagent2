"""技能使用量與生命週期 metadata — 供 Curator 讀取做決策。

在 `assets/skill_usage.json` 追蹤每個使用者技能的使用量與 pin 狀態，以技能
id（目前為技能目錄名）為鍵。使用量由 runtime 更新（use_count 在 session
結束時累加，而非每次呼叫技能）；Curator 讀取 last_used_at 決定要不要把閒置
技能搬去封存。pinned 技能永不被自動搬移或刪除。

Curator 只看 `assets/user_skill/`（可寫的使用者技能），因此不需要 bundled /
hub 來源判定——user_skill 底下的技能一律受管理。

未來規劃：本檔以 JSON 當儲存後端；日後改接 BigQuery `skill_usage` 表
（skill_id、use_count、last_used_at、state、pinned）時，只需替換
`讀取全部使用量` / `寫入全部使用量` 兩個 I/O 出入口。

生命週期狀態：
    active   -> 預設
    stale    -> 閒置超過門檻（Curator 標記）
    archived -> 閒置更久，Curator 搬到封存倉庫
    pinned   -> 選擇退出自動轉移（布林旗標，與 state 無關）
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from .. import 基本工具

logger = logging.getLogger(__name__)

try:
    import fcntl
except ImportError:
    fcntl = None

狀態_使用中 = "active"
狀態_閒置 = "stale"
狀態_封存 = "archived"
_有效狀態 = {狀態_使用中, 狀態_閒置, 狀態_封存}


class 技能使用量資料格式錯誤(ValueError):
    """本機 skill_usage.json 根型別或內容不符合預期。"""


def _取得BigQuery技能庫():
    """取得雲端技能庫；未啟用 BigQuery 後端時回傳 None（改走本機 JSON）。

    參數：無。
    返回值：BigQuery技能庫 實例（STORAGE_BACKEND=bigquery 時），否則 None。
    """
    from ..BigQuery技能庫 import 取得啟用中的技能庫

    return 取得啟用中的技能庫()


def 使用量檔路徑() -> Path:
    """回傳 skill_usage.json 路徑（assets 根目錄，與 user_skill 同層）。"""
    return 基本工具.使用者技能根目錄().parent / "skill_usage.json"


def 產生目前時間字串() -> str:
    """產生目前 UTC 時間的 ISO 8601 字串，供使用量的建立/最後使用時間欄位使用。

    參數：無。
    返回值：str，ISO 8601 時間字串，例如 `2026-07-13T08:30:00+00:00`。
    """
    return datetime.now(timezone.utc).isoformat()


def _空白記錄() -> dict[str, Any]:
    """一筆全新使用量記錄的預設值。以 skill_id 為 key，user_id 為擁有者欄位。"""
    return {
        "user_id": None,
        "use_count": 0,
        "last_used_at": None,
        "state": 狀態_使用中,
        "pinned": False,
        "created_at": 產生目前時間字串(),
    }


@contextmanager
def _使用量檔鎖():
    """序列化 skill_usage.json 的 read-modify-write，避免並行寫入互相覆蓋。"""
    if fcntl is None:
        yield
        return
    鎖路徑 = 使用量檔路徑().with_suffix(".json.lock")
    鎖路徑.parent.mkdir(parents=True, exist_ok=True)
    檔案 = open(鎖路徑, "a+", encoding="utf-8")
    try:
        fcntl.flock(檔案.fileno(), fcntl.LOCK_EX)
        yield
    finally:
        try:
            fcntl.flock(檔案.fileno(), fcntl.LOCK_UN)
        except OSError:
            pass
        檔案.close()


def 讀取全部使用量(*, 毀損時拋出: bool = False) -> dict[str, dict[str, Any]]:
    """讀取使用量；本機毀損時可選擇拋出，避免變更流程覆寫原檔。"""
    技能庫 = _取得BigQuery技能庫()
    if 技能庫 is not None:
        return 技能庫.讀取全部使用量()
    路徑 = 使用量檔路徑()
    if not 路徑.exists():
        return {}
    try:
        資料 = json.loads(路徑.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as 錯誤:
        logger.debug("讀取 %s 失敗：%s", 路徑, 錯誤)
        if 毀損時拋出:
            raise
        return {}
    if not isinstance(資料, dict):
        logger.debug("讀取 %s 失敗：根型別應為 dict，實際為 %s", 路徑, type(資料).__name__)
        if 毀損時拋出:
            raise 技能使用量資料格式錯誤(
                f"skill_usage.json 根型別應為 dict，實際為 {type(資料).__name__}"
            )
        return {}
    清理: dict[str, dict[str, Any]] = {}
    for 鍵, 值 in 資料.items():
        if isinstance(值, dict):
            清理[str(鍵)] = 值
    return 清理


def 寫入全部使用量(資料: dict[str, dict[str, Any]]) -> None:
    """原子寫入整份使用量；best-effort，失敗只記 log 不丟出。"""
    技能庫 = _取得BigQuery技能庫()
    if 技能庫 is not None:
        技能庫.寫入全部使用量(資料)
        return
    路徑 = 使用量檔路徑()
    try:
        路徑.parent.mkdir(parents=True, exist_ok=True)
        fd, 暫存 = tempfile.mkstemp(dir=str(路徑.parent), prefix=".skill_usage_", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(資料, f, indent=2, sort_keys=True, ensure_ascii=False)
                f.flush()
                os.fsync(f.fileno())
            os.replace(暫存, 路徑)
        except BaseException:
            try:
                os.unlink(暫存)
            except OSError:
                pass
            raise
    except Exception as 錯誤:
        logger.debug("寫入 %s 失敗：%s", 路徑, 錯誤, exc_info=True)


def 取得記錄(skill_id: str) -> dict[str, Any]:
    """回傳某技能的使用量記錄；不存在時回傳補齊預設值的新記錄。"""
    資料 = 讀取全部使用量()
    記錄 = 資料.get(skill_id)
    if not isinstance(記錄, dict):
        return _空白記錄()
    預設 = _空白記錄()
    for 鍵, 值 in 預設.items():
        記錄.setdefault(鍵, 值)
    return 記錄


def _補齊使用量記錄(記錄: dict[str, Any] | None) -> dict[str, Any]:
    """把使用量記錄補齊預設欄位。

    參數：
        記錄: 既有記錄 dict，或 None（視為新記錄）。

    返回：
        補齊預設值後的記錄 dict。
    """
    if not isinstance(記錄, dict):
        記錄 = _空白記錄()
    else:
        預設 = _空白記錄()
        for 鍵, 值 in 預設.items():
            記錄.setdefault(鍵, 值)
    return 記錄


def _變更(skill_id: str, 變更函數: Callable[[dict[str, Any]], None]) -> None:
    """讀取使用量記錄、就地套用變更函數後寫回；best-effort，失敗只記 log。

    BigQuery 模式以單列 MERGE upsert（讀取使用量列 → 覆寫使用量列），不走整表
    讀寫。本機模式在 fcntl 檔鎖內讀取整份 JSON、修改一筆後原子寫回。

    參數：
        skill_id: 技能穩定 UUID。
        變更函數: 接收記錄 dict 並就地修改的 callback。

    返回：
        無。
    """
    if not skill_id:
        return
    技能庫 = _取得BigQuery技能庫()
    if 技能庫 is not None:
        try:
            記錄 = _補齊使用量記錄(技能庫.讀取使用量列(skill_id))
            變更函數(記錄)
            技能庫.覆寫使用量列(skill_id, 記錄)
        except Exception as 錯誤:
            logger.debug("技能使用量._變更(%s) BigQuery 失敗：%s", skill_id, 錯誤, exc_info=True)
        return
    try:
        with _使用量檔鎖():
            資料 = 讀取全部使用量(毀損時拋出=True)
            記錄 = _補齊使用量記錄(資料.get(skill_id))
            變更函數(記錄)
            資料[skill_id] = 記錄
            寫入全部使用量(資料)
    except Exception as 錯誤:
        logger.debug("技能使用量._變更(%s) 失敗：%s", skill_id, 錯誤, exc_info=True)


def 初始化記錄(skill_id: str, user_id: str | None = None) -> None:
    """技能建立時呼叫，確保有一筆記錄（保留既有 created_at），並寫入擁有者 user_id。"""
    def _套用(記錄: dict[str, Any]) -> None:
        記錄.setdefault("created_at", 產生目前時間字串())
        if user_id is not None:
            記錄["user_id"] = str(user_id)
    _變更(skill_id, _套用)


def 記錄使用(skill_id: str, 次數: int = 1) -> None:
    """累加 use_count 並更新 last_used_at。

    依設計由 runtime 在 **session 結束時** 呼叫（把該 session 內用過的技能各累加
    一次或依實際使用次數），而非每次呼叫技能就加。
    """
    if 次數 <= 0:
        return
    def _套用(記錄: dict[str, Any]) -> None:
        記錄["use_count"] = int(記錄.get("use_count") or 0) + 次數
        記錄["last_used_at"] = 產生目前時間字串()
    _變更(skill_id, _套用)


def 設定彙總(skill_id: str, use_count: int, last_used_at: str | None, user_id: str | None = None) -> None:
    """由 Curator 呼叫：把事件彙總結果**覆寫**進表三（use_count / last_used_at）。

    與 `記錄使用`（累加）不同，這是全量重算後的覆寫，讓表三成為事件表的物化快照。
    """
    def _套用(記錄: dict[str, Any]) -> None:
        記錄["use_count"] = int(use_count)
        記錄["last_used_at"] = last_used_at
        if user_id is not None:
            記錄["user_id"] = str(user_id)
    _變更(skill_id, _套用)


def 補齊缺少的記錄() -> int:
    """依目前後端的技能來源，補齊缺少的使用量記錄。

    BigQuery 模式列出 BigQuery 技能並逐列 MERGE；本機模式在檔鎖內掃描
    user_skill 並原子寫回 JSON。兩種模式不混用資料來源或寫入目標。
    """
    技能庫 = _取得BigQuery技能庫()
    if 技能庫 is not None:
        try:
            既有資料 = 技能庫.讀取全部使用量()
            補齊數 = 0
            for 身分 in 技能庫.列出技能身分(是否包含封存=True):
                skill_id = 身分.get("skill_id")
                if not skill_id or skill_id in 既有資料:
                    continue
                記錄 = _空白記錄()
                if 身分.get("user_id") is not None:
                    記錄["user_id"] = str(身分["user_id"])
                技能庫.覆寫使用量列(str(skill_id), 記錄)
                補齊數 += 1
            return 補齊數
        except Exception as 錯誤:
            logger.debug("BigQuery 補齊缺少的技能使用量記錄失敗：%s", 錯誤, exc_info=True)
            return 0

    try:
        with _使用量檔鎖():
            資料 = 讀取全部使用量(毀損時拋出=True)
            補齊數 = 0
            for 身分 in 基本工具.列出使用者技能身分():
                skill_id = 身分.get("skill_id")
                if not skill_id or skill_id in 資料:
                    continue
                資料[str(skill_id)] = _空白記錄()
                補齊數 += 1
            if 補齊數:
                寫入全部使用量(資料)
            return 補齊數
    except Exception as 錯誤:
        logger.debug("補齊缺少的技能使用量記錄失敗：%s", 錯誤, exc_info=True)
        return 0


def 設定pin(skill_id: str, pinned: bool) -> None:
    """設定技能 pin 狀態；pinned 技能永不被 Curator 自動搬移或被刪除。"""
    def _套用(記錄: dict[str, Any]) -> None:
        記錄["pinned"] = bool(pinned)
    _變更(skill_id, _套用)


def 是否pin(skill_id: str) -> bool:
    """回傳技能是否被 pin。"""
    return bool(取得記錄(skill_id).get("pinned"))


def 設定狀態(skill_id: str, 狀態: str) -> None:
    """設定生命週期 state；無效值為 no-op。"""
    if 狀態 not in _有效狀態:
        logger.debug("設定狀態：無效狀態 %r（%s）", 狀態, skill_id)
        return
    def _套用(記錄: dict[str, Any]) -> None:
        記錄["state"] = 狀態
    _變更(skill_id, _套用)


def 遺忘(skill_id: str) -> None:
    """技能被刪除時呼叫，整筆移除其使用量記錄；best-effort，失敗只記 log。

    BigQuery 模式直接刪除 skill_usage 單列（刪除使用量列）。本機模式在
    fcntl 檔鎖內讀取整份 JSON、刪除對應 skill_id 後原子寫回。

    參數：
        skill_id: 技能穩定 UUID。

    返回：
        無。
    """
    if not skill_id:
        return
    技能庫 = _取得BigQuery技能庫()
    if 技能庫 is not None:
        try:
            技能庫.刪除使用量列(skill_id)
        except Exception as 錯誤:
            logger.debug("技能使用量.遺忘(%s) BigQuery 失敗：%s", skill_id, 錯誤, exc_info=True)
        return
    try:
        with _使用量檔鎖():
            資料 = 讀取全部使用量(毀損時拋出=True)
            if skill_id in 資料:
                del 資料[skill_id]
                寫入全部使用量(資料)
    except Exception as 錯誤:
        logger.debug("技能使用量.遺忘(%s) 失敗：%s", skill_id, 錯誤, exc_info=True)


def 使用量報告() -> list[dict[str, Any]]:
    """回傳每個使用者技能的 {skill_id, name, user_id, use_count, last_used_at,
    state, pinned, created_at}，供 Curator / CLI 使用。

    以 frontmatter 的 skill_id 為 key join 使用量記錄；缺記錄者補預設值。沒有
    skill_id 的技能（異常/未經 skill_manage 建立）會被略過。
    """
    資料 = 讀取全部使用量()
    列表: list[dict[str, Any]] = []
    for 身分 in 基本工具.列出使用者技能身分():
        skill_id = 身分.get("skill_id")
        if not skill_id:
            continue
        記錄 = 資料.get(skill_id)
        if not isinstance(記錄, dict):
            記錄 = _空白記錄()
        else:
            預設 = _空白記錄()
            for 鍵, 值 in 預設.items():
                記錄.setdefault(鍵, 值)
        列表.append({"skill_id": skill_id, "name": 身分.get("name"), **記錄})
    return 列表


# 相容 feature/skill-manage-usage 的既有公開名稱，避免合併後呼叫端一次性斷裂。
技能生命狀態_使用中 = 狀態_使用中
技能生命狀態_閒置 = 狀態_閒置
技能生命狀態_封存 = 狀態_封存
取得技能使用量檔路徑 = 使用量檔路徑
讀取全部技能使用量 = 讀取全部使用量
寫入全部技能使用量 = 寫入全部使用量
取得技能使用量記錄 = 取得記錄
初始化技能使用量記錄 = 初始化記錄
記錄技能使用 = 記錄使用
設定技能使用量彙總 = 設定彙總
補齊缺少的技能使用量記錄 = 補齊缺少的記錄
設定技能Pin = 設定pin
檢查技能是否Pin = 是否pin
設定技能生命狀態 = 設定狀態
移除技能使用量記錄 = 遺忘
產生技能使用量報告 = 使用量報告
