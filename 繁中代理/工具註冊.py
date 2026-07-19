"""工具註冊入口。

功能：
    建立預設工具登錄器，負責把 tool schema 與 Python handler 接起來。
    `工具.py` 保存基礎設施，`基本工具.py` 保存內建通用工具實作；本檔只做
    名稱對應與 schema 載入。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable

from .基本工具 import (
    列出技能,
    執行終端指令,
    套用文字修補,
    寫入檔案內容,
    搜尋工作階段工具,
    搜尋檔案,
    記憶工具,
    讀取技能,
    讀取檔案內容,
)
from .工具 import 回報工具未啟用, 工具定義, 工具登錄器
from .工具集.技能管理 import 管理技能
from .工具集.管理部_bigquery import 管理部文件搜尋
from .使用者 import 使用者上下文


def 載入工具結構清單(路徑: Path) -> list[dict[str, Any]]:
    """從 JSON 檔載入 Hermes-style tool schema 清單。"""
    if not 路徑.exists():
        return []
    return json.loads(路徑.read_text(encoding="utf-8"))


def 使用者可用工具(名稱: str, 使用者上下文物件: 使用者上下文 | None) -> bool:
    """判斷目前使用者是否可見指定工具。"""
    if 使用者上下文物件 is None:
        return True
    return 使用者上下文物件.工具是否允許(名稱)


def 登錄結構清單(
    登錄器: 工具登錄器,
    結構清單: list[dict[str, Any]],
    已實作處理器: dict[str, Callable[[dict[str, Any]], Any]],
    使用者上下文物件: 使用者上下文 | None = None,
) -> None:
    """把 schema 清單逐一登錄到工具登錄器。"""
    for 項目 in 結構清單:
        結構 = 項目["schema"]
        名稱 = 結構["name"]
        if not 使用者可用工具(名稱, 使用者上下文物件):
            continue
        登錄器.登錄工具(工具定義(
            名稱=名稱,
            說明=結構.get("description", ""),
            參數結構=結構.get("parameters", {"type": "object", "properties": {}}),
            處理函數=已實作處理器.get(名稱, 回報工具未啟用(名稱)),
        ))


def 建立預設工具登錄器(工作目錄: str | Path | None = None, 使用者上下文物件: 使用者上下文 | None = None) -> 工具登錄器:
    """建立含 Hermes core 與專案自訂工具的工具登錄器。

    參數：
        工作目錄: Runtime 工作目錄；工具相對路徑會以此為基準。
        使用者上下文物件: 目前使用者權限；會限制模型可見工具與執行權限。

    返回值：
        工具登錄器。預設會載入 Hermes core tool schema，再載入
        `assets/hermes_custom_tool_schemas.json` 中的專案自訂工具 schema。
    """
    已實作處理器: dict[str, Callable[[dict[str, Any]], Any]] = {
        "read_file": 讀取檔案內容,
        "write_file": 寫入檔案內容,
        "patch": 套用文字修補,
        "search_files": 搜尋檔案,
        "terminal": 執行終端指令,
        "skills_list": 列出技能,
        "skill_view": 讀取技能,
        "skill_manage": 管理技能,
        "session_search": 搜尋工作階段工具,
        "memory": 記憶工具,
        "administrative_search": 管理部文件搜尋,
    }
    資產目錄 = Path(__file__).resolve().parents[1] / "assets"
    核心結構路徑 = 資產目錄 / "hermes_core_tool_schemas.json"
    自訂結構路徑 = 資產目錄 / "hermes_custom_tool_schemas.json"

    核心結構清單 = 載入工具結構清單(核心結構路徑)
    自訂結構清單 = 載入工具結構清單(自訂結構路徑)
    已知工具名稱集合 = {項目["schema"]["name"] for 項目 in 核心結構清單 + 自訂結構清單}
    if not 已知工具名稱集合:
        已知工具名稱集合 = set(已實作處理器)

    登錄器 = 工具登錄器(工作目錄, 使用者上下文物件, 已知工具名稱集合)

    if 核心結構清單:
        登錄結構清單(登錄器, 核心結構清單, 已實作處理器, 使用者上下文物件)
    else:
        for 名稱, 處理器 in 已實作處理器.items():
            if 名稱 == "administrative_search" or not 使用者可用工具(名稱, 使用者上下文物件):
                continue
            登錄器.登錄工具(工具定義(名稱, 名稱, {"type": "object", "properties": {}}, 處理器))

    if 自訂結構清單:
        登錄結構清單(登錄器, 自訂結構清單, 已實作處理器, 使用者上下文物件)
    elif 使用者可用工具("administrative_search", 使用者上下文物件):
        登錄器.登錄工具(工具定義(
            "administrative_search",
            "administrative_search",
            {"type": "object", "properties": {}},
            已實作處理器["administrative_search"],
        ))
    return 登錄器


def 建立預設工具版本庫(修訂名稱: str):
    """U05 提供 detached capability context 前拒絕橋接 legacy 工具。"""
    from .發布介面.執行期.工具版本庫 import 工具快照錯誤

    del 修訂名稱
    raise 工具快照錯誤("發布工具快照不可用") from None
