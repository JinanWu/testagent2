"""技能索引組裝器。

功能：
    掃描 skills 根目錄下的分類 `DESCRIPTION.md` 與技能 `SKILL.md`，解析簡化
    frontmatter，依平台、停用清單、工具與 toolset 條件過濾技能，最後組裝可放入
    system prompt 的 `<available_skills>` 摘要。此模組同時提供 process 內記憶體
    快取與磁碟 snapshot，避免每次建立新 session 都重新掃描整個 skills 目錄。

參數與返回值：
    對外主要使用 `建立技能摘要()`，傳入 skills 根目錄與目前可用工具名稱集合，
    回傳已整理、已過濾、已快取的技能摘要文字。
"""

from __future__ import annotations

import json
import os
import platform
import threading
from pathlib import Path
from typing import Any


技能摘要快取版本 = 1
技能摘要最大描述字數 = 280
技能摘要記憶體快取: dict[tuple[str, str], str] = {}
技能摘要快取鎖 = threading.Lock()

# 這段會放在 skills index 前方，提醒模型在回答前先檢查可用技能；
# 只要技能與任務相關，就必須用 skill_view 載入完整說明，而不是只靠通用推理完成。
技能強制載入指引 = (
    "## Skills (mandatory)\n"
    "Before replying, scan the skills below. If a skill matches or is even partially relevant "
    "to your task, you MUST load it with skill_view(name) and follow its instructions. "
    "Err on the side of loading — it is always better to have context you don't need "
    "than to miss critical steps, pitfalls, or established workflows. "
    "Skills contain specialized knowledge — API endpoints, tool-specific commands, "
    "and proven workflows that outperform general-purpose approaches. Load the skill "
    "even if you think you could handle the task with basic tools like web_search or terminal. "
    "Skills also encode the user's preferred approach, conventions, and quality standards "
    "for tasks like code review, planning, and testing — load them even for tasks you "
    "already know how to do, because the skill defines how it should be done here.\n"
    "Whenever the user asks you to configure, set up, install, enable, disable, modify, "
    "or troubleshoot Hermes Agent itself — its CLI, config, models, providers, tools, "
    "skills, voice, gateway, plugins, or any feature — load the `hermes-agent` skill "
    "first. It has the actual commands (e.g. `hermes config set ...`, `hermes tools`, "
    "`hermes setup`) so you don't have to guess or invent workarounds.\n"
    "If a skill has issues, fix it with skill_manage(action='patch').\n"
    "After difficult/iterative tasks, offer to save as a skill. "
    "If a skill you loaded was missing steps, had wrong commands, or needed "
    "pitfalls you discovered, update it before finishing."
)
技能無相關項目指引 = "Only proceed without loading a skill if genuinely none are relevant to the task."


def 截斷摘要文字(文字: str, 最大字數: int = 技能摘要最大描述字數) -> str:
    """將技能索引用描述限制在固定長度內。

    參數：
        文字: 原始描述文字。
        最大字數: 允許保留的最大字元數。

    返回值：
        截斷後的描述文字；若原文未超過限制則原樣回傳。
    """
    整理後文字 = " ".join(文字.split())
    if len(整理後文字) <= 最大字數:
        return 整理後文字
    return 整理後文字[: max(最大字數 - 1, 0)].rstrip() + "…"


def 解析前置資料值(值: str) -> Any:
    """解析 frontmatter 的簡化值格式。

    參數：
        值: 冒號右側的原始字串。

    返回值：
        字串或字串清單。目前支援 `"文字"`、`'文字'` 與 `[a, b, c]`。
    """
    去空白值 = 值.strip().strip("'\"")
    if 去空白值.startswith("[") and 去空白值.endswith("]"):
        清單內容 = 去空白值[1:-1].strip()
        if not 清單內容:
            return []
        return [項目.strip().strip("'\"") for 項目 in 清單內容.split(",") if 項目.strip()]
    return 去空白值


def 解析Markdown前置資料(內容: str) -> dict[str, Any]:
    """解析 Markdown 檔案開頭的簡化 YAML frontmatter。

    參數：
        內容: Markdown 文字內容。

    返回值：
        frontmatter 字典。此 MVP 解析常見的 `key: value`、inline list 與簡單
        縮排欄位，足以取得 `name`、`description`、`platforms` 與 Hermes 條件欄位。
    """
    if not 內容.startswith("---"):
        return {}
    結束位置 = 內容.find("\n---", 3)
    if 結束位置 == -1:
        return {}
    前置資料文字 = 內容[3:結束位置]
    前置資料: dict[str, Any] = {}
    原始行清單 = 前置資料文字.splitlines()
    索引 = 0
    while 索引 < len(原始行清單):
        原始行 = 原始行清單[索引]
        行 = 原始行.strip()
        if not 行 or 行.startswith("#") or ":" not in 行:
            索引 += 1
            continue
        鍵, _, 值 = 行.partition(":")
        鍵 = 鍵.strip()
        值 = 值.strip()
        if 值 in {"|", ">"}:
            區塊行清單: list[str] = []
            索引 += 1
            while 索引 < len(原始行清單):
                後續原始行 = 原始行清單[索引]
                if 後續原始行.strip() and not 後續原始行.startswith((" ", "\t")):
                    break
                if 後續原始行.strip():
                    區塊行清單.append(後續原始行.strip())
                索引 += 1
            if 鍵 and 區塊行清單:
                前置資料[鍵] = " ".join(區塊行清單)
            continue
        if 鍵 and 值 and not 值.startswith("[") and not 值.startswith("{"):
            前置資料[鍵] = 解析前置資料值(值)
        elif 鍵 and 值.startswith("["):
            前置資料[鍵] = 解析前置資料值(值)
        索引 += 1
    return 前置資料


def 讀取Markdown描述欄位(路徑: Path) -> str:
    """讀取 Markdown frontmatter 中的 description 欄位。

    參數：
        路徑: Markdown 檔案路徑。

    返回值：
        description 文字；若檔案不存在、讀取失敗或沒有欄位，回傳空字串。
    """
    try:
        內容 = 路徑.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    return 截斷摘要文字(解析Markdown前置資料(內容).get("description", ""))


def 轉成字串清單(值: Any) -> list[str]:
    """將 frontmatter 欄位轉成字串清單。

    參數：
        值: frontmatter 欄位值，可能是字串、清單或 None。

    返回值：
        去除空白後的字串清單。
    """
    if 值 is None:
        return []
    if isinstance(值, list):
        return [str(項目).strip() for 項目 in 值 if str(項目).strip()]
    if isinstance(值, str):
        return [項目.strip() for 項目 in 值.split(",") if 項目.strip()]
    return [str(值).strip()] if str(值).strip() else []


def 取得目前平台名稱() -> str:
    """取得目前執行平台名稱。

    參數：無。
    返回值：`linux`、`macos`、`windows` 或 Python platform.system() 的小寫值。
    """
    系統名稱 = platform.system().lower()
    對照表 = {"darwin": "macos", "windows": "windows", "linux": "linux"}
    return 對照表.get(系統名稱, 系統名稱)


def 讀取停用技能名稱集合() -> set[str]:
    """讀取停用技能名稱集合。

    參數：無。
    返回值：停用技能名稱集合。此 MVP 使用環境變數
        `AIAGENT_DISABLED_SKILLS`，以逗號分隔技能名稱。
    """
    原始值 = os.getenv("AIAGENT_DISABLED_SKILLS", "")
    return {項目.strip() for 項目 in 原始值.split(",") if 項目.strip()}


def 建立可用工具集名稱集合(工具名稱集合: set[str]) -> set[str]:
    """依工具名稱推導粗略 toolset 名稱。

    參數：
        工具名稱集合: 目前可用工具名稱集合。

    返回值：
        toolset 名稱集合。此 MVP 只推導常見的 `terminal`、`files`、`file`、
        `web`、`skills`。
    """
    工具集名稱集合: set[str] = set()
    if "terminal" in 工具名稱集合:
        工具集名稱集合.add("terminal")
    if {"read_file", "write_file", "patch", "search_files"}.intersection(工具名稱集合):
        工具集名稱集合.update({"file", "files"})
    if {"web_search", "web_extract", "browser_navigate"}.intersection(工具名稱集合):
        工具集名稱集合.add("web")
    if {"skills_list", "skill_view", "skill_manage"}.intersection(工具名稱集合):
        工具集名稱集合.add("skills")
    return 工具集名稱集合


def 技能是否符合平台(前置資料: dict[str, Any], 平台名稱: str) -> bool:
    """判斷技能是否符合目前平台。

    參數：
        前置資料: 技能 SKILL.md frontmatter。
        平台名稱: 目前平台名稱。

    返回值：
        True 表示技能可在目前平台顯示。
    """
    平台清單 = [項目.lower() for 項目 in 轉成字串清單(前置資料.get("platforms"))]
    return not 平台清單 or "any" in 平台清單 or 平台名稱.lower() in 平台清單


def 技能是否符合工具條件(前置資料: dict[str, Any], 工具名稱集合: set[str], 工具集名稱集合: set[str]) -> bool:
    """判斷技能的工具條件是否符合目前工具能力。

    參數：
        前置資料: 技能 SKILL.md frontmatter。
        工具名稱集合: 目前可用工具名稱集合。
        工具集名稱集合: 目前可用 toolset 名稱集合。

    返回值：
        True 表示技能可顯示；False 表示缺少必要工具或已有主要工具所以 fallback 應隱藏。
    """
    for 工具集名稱 in 轉成字串清單(前置資料.get("requires_toolsets")):
        if 工具集名稱 not in 工具集名稱集合:
            return False
    for 工具名稱 in 轉成字串清單(前置資料.get("requires_tools")):
        if 工具名稱 not in 工具名稱集合:
            return False
    for 工具集名稱 in 轉成字串清單(前置資料.get("fallback_for_toolsets")):
        if 工具集名稱 in 工具集名稱集合:
            return False
    for 工具名稱 in 轉成字串清單(前置資料.get("fallback_for_tools")):
        if 工具名稱 in 工具名稱集合:
            return False
    return True


def 走訪技能索引檔案(技能根目錄: Path, 檔名: str) -> list[Path]:
    """列出技能根目錄下指定名稱的索引檔案。

    參數：
        技能根目錄: skills 根目錄。
        檔名: 要尋找的檔名，例如 `SKILL.md` 或 `DESCRIPTION.md`。

    返回值：
        已排序的檔案路徑清單；隱藏分類或隱藏目錄會被排除。
    """
    檔案清單: list[Path] = []
    for 路徑 in 技能根目錄.rglob(檔名):
        try:
            相對路徑 = 路徑.relative_to(技能根目錄)
        except ValueError:
            continue
        if any(片段.startswith(".") for 片段 in 相對路徑.parts):
            continue
        檔案清單.append(路徑)
    return sorted(檔案清單)


def 建立技能摘要Manifest(技能根目錄: Path) -> dict[str, list[int]]:
    """建立技能索引檔案的 manifest。

    參數：
        技能根目錄: skills 根目錄。

    返回值：
        dict；key 是相對路徑，value 是 `[mtime_ns, size]`。用於判斷磁碟
        snapshot 是否仍符合目前的 `SKILL.md` 與 `DESCRIPTION.md`。
    """
    清單指紋: dict[str, list[int]] = {}
    for 檔名 in ("SKILL.md", "DESCRIPTION.md"):
        for 路徑 in 走訪技能索引檔案(技能根目錄, 檔名):
            try:
                檔案狀態 = 路徑.stat()
                相對路徑 = str(路徑.relative_to(技能根目錄))
            except OSError:
                continue
            清單指紋[相對路徑] = [檔案狀態.st_mtime_ns, 檔案狀態.st_size]
    return 清單指紋


def 建立技能摘要快取鍵(技能根目錄: Path, 清單指紋: dict[str, list[int]], 快取條件: dict[str, Any]) -> tuple[str, str]:
    """建立記憶體快取使用的 key。

    參數：
        技能根目錄: skills 根目錄。
        清單指紋: 技能索引檔案 manifest。
        快取條件: 影響技能顯示結果的條件，例如平台、停用清單、工具名稱。

    返回值：
        `(技能根目錄, 快取內容_json)` tuple。
    """
    快取內容 = {"manifest": 清單指紋, "conditions": 快取條件}
    快取內容文字 = json.dumps(快取內容, ensure_ascii=False, sort_keys=True)
    return (str(技能根目錄.resolve()), 快取內容文字)


def 取得技能摘要快取路徑() -> Path:
    """取得技能摘要磁碟 snapshot 路徑。

    參數：無。
    返回值：snapshot JSON 路徑。測試或部署可用
        `AIAGENT_SKILL_SNAPSHOT_PATH` 覆寫；預設放在專案 `.hermes` 目錄。
    """
    環境路徑 = os.getenv("AIAGENT_SKILL_SNAPSHOT_PATH")
    if 環境路徑:
        return Path(環境路徑).expanduser().resolve()
    return Path(__file__).resolve().parents[1] / ".hermes" / ".skills_prompt_snapshot.json"


def 清除技能摘要記憶體快取() -> None:
    """清除 process 內的技能摘要記憶體快取。

    參數：無。
    返回值：None。主要供測試或未來 reload-skills 類功能使用。
    """
    with 技能摘要快取鎖:
        技能摘要記憶體快取.clear()


def 讀取技能摘要快取(技能根目錄: Path, 清單指紋: dict[str, list[int]], 快取條件: dict[str, Any]) -> str | None:
    """讀取技能摘要快取。

    參數：
        技能根目錄: skills 根目錄。
        清單指紋: 目前技能索引檔案 manifest。
        快取條件: 影響技能顯示結果的條件。

    返回值：
        快取命中時回傳技能摘要文字；記憶體與磁碟 snapshot 都失效時回傳 None。
    """
    快取鍵 = 建立技能摘要快取鍵(技能根目錄, 清單指紋, 快取條件)
    with 技能摘要快取鎖:
        記憶體摘要 = 技能摘要記憶體快取.get(快取鍵)
    if 記憶體摘要 is not None:
        return 記憶體摘要

    快取路徑 = 取得技能摘要快取路徑()
    if not 快取路徑.is_file():
        return None
    try:
        快取資料 = json.loads(快取路徑.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(快取資料, dict):
        return None
    if 快取資料.get("version") != 技能摘要快取版本:
        return None
    if 快取資料.get("skills_root") != str(技能根目錄.resolve()):
        return None
    if 快取資料.get("manifest") != 清單指紋:
        return None
    if 快取資料.get("conditions") != 快取條件:
        return None
    技能摘要 = 快取資料.get("summary")
    if not isinstance(技能摘要, str):
        return None
    with 技能摘要快取鎖:
        技能摘要記憶體快取[快取鍵] = 技能摘要
    return 技能摘要


def 寫入技能摘要快取(技能根目錄: Path, 清單指紋: dict[str, list[int]], 快取條件: dict[str, Any], 技能摘要: str) -> None:
    """寫入技能摘要快取。

    參數：
        技能根目錄: skills 根目錄。
        清單指紋: 建立技能摘要時使用的 manifest。
        快取條件: 建立技能摘要時使用的顯示條件。
        技能摘要: 已組裝完成的 `<available_skills>` 文字。

    返回值：
        None。會同步更新記憶體快取與磁碟 snapshot；磁碟寫入失敗時不影響主流程。
    """
    快取鍵 = 建立技能摘要快取鍵(技能根目錄, 清單指紋, 快取條件)
    with 技能摘要快取鎖:
        技能摘要記憶體快取[快取鍵] = 技能摘要

    快取路徑 = 取得技能摘要快取路徑()
    快取資料 = {
        "version": 技能摘要快取版本,
        "skills_root": str(技能根目錄.resolve()),
        "manifest": 清單指紋,
        "conditions": 快取條件,
        "summary": 技能摘要,
    }
    try:
        快取路徑.parent.mkdir(parents=True, exist_ok=True)
        暫存路徑 = 快取路徑.with_suffix(快取路徑.suffix + ".tmp")
        暫存路徑.write_text(json.dumps(快取資料, ensure_ascii=False, indent=2), encoding="utf-8")
        暫存路徑.replace(快取路徑)
    except OSError:
        return


def 建立技能摘要(技能根目錄: Path, 工具名稱集合: set[str]) -> str:
    """建立可放入 system prompt 的技能索引摘要。

    參數：
        技能根目錄: skills 根目錄。
        工具名稱集合: 目前可用工具名稱集合，用於 tool/toolset 條件過濾。

    返回值：
        技能摘要文字。摘要會依分類列出技能，分類描述來自 `DESCRIPTION.md`，
        技能名稱與一句話說明來自 `SKILL.md` frontmatter。
    """
    if not 技能根目錄.exists():
        return 技能強制載入指引 + "\n\n<available_skills>\n  (skills not copied yet)\n</available_skills>\n\n" + 技能無相關項目指引
    清單指紋 = 建立技能摘要Manifest(技能根目錄)
    工具集名稱集合 = 建立可用工具集名稱集合(工具名稱集合)
    停用技能名稱集合 = 讀取停用技能名稱集合()
    平台名稱 = 取得目前平台名稱()
    快取條件 = {
        "platform": 平台名稱,
        "disabled_skills": sorted(停用技能名稱集合),
        "tools": sorted(工具名稱集合),
        "toolsets": sorted(工具集名稱集合),
    }
    快取摘要 = 讀取技能摘要快取(技能根目錄, 清單指紋, 快取條件)
    if 快取摘要 is not None:
        return 快取摘要

    分類描述表 = 建立技能分類描述表(技能根目錄)
    技能項目清單 = 建立技能索引項目清單(技能根目錄, 平台名稱, 停用技能名稱集合, 工具名稱集合, 工具集名稱集合)
    if not 技能項目清單:
        return 技能強制載入指引 + "\n\n<available_skills>\n  (no skills found)\n</available_skills>\n\n" + 技能無相關項目指引

    分類項目表: dict[str, list[dict[str, str]]] = {}
    for 技能項目 in 技能項目清單[:300]:
        分類項目表.setdefault(技能項目["category"], []).append(技能項目)

    行清單 = [技能強制載入指引, "", "<available_skills>"]
    for 分類 in sorted(分類項目表):
        分類描述 = 分類描述表.get(分類, "")
        行清單.append(f"  {分類}: {分類描述}" if 分類描述 else f"  {分類}:")
        已出現技能名稱: set[str] = set()
        for 技能項目 in sorted(分類項目表[分類], key=lambda 項目: 項目["skill_name"]):
            技能名稱 = 技能項目["skill_name"]
            if 技能名稱 in 已出現技能名稱:
                continue
            已出現技能名稱.add(技能名稱)
            技能說明 = 技能項目.get("description", "")
            行清單.append(f"    - {技能名稱}: {技能說明}" if 技能說明 else f"    - {技能名稱}")
    行清單.append("</available_skills>")
    行清單.extend(["", 技能無相關項目指引])
    技能摘要 = "\n".join(行清單)
    寫入技能摘要快取(技能根目錄, 清單指紋, 快取條件, 技能摘要)
    return 技能摘要


def 建立技能分類描述表(技能根目錄: Path) -> dict[str, str]:
    """建立分類名稱到分類描述的對照表。

    參數：
        技能根目錄: skills 根目錄，例如 `assets/hermes_skills`。

    返回值：
        dict；key 是分類路徑名稱，value 是 `DESCRIPTION.md` 的 description。
    """
    分類描述表: dict[str, str] = {}
    for 描述路徑 in 走訪技能索引檔案(技能根目錄, "DESCRIPTION.md"):
        try:
            相對路徑 = 描述路徑.relative_to(技能根目錄)
        except ValueError:
            continue
        分類 = "/".join(相對路徑.parts[:-1]) if len(相對路徑.parts) > 1 else "general"
        描述 = 讀取Markdown描述欄位(描述路徑)
        if 描述:
            分類描述表[分類] = 描述
    return 分類描述表


def 建立技能索引項目清單(
    技能根目錄: Path,
    平台名稱: str | None = None,
    停用技能名稱集合: set[str] | None = None,
    工具名稱集合: set[str] | None = None,
    工具集名稱集合: set[str] | None = None,
) -> list[dict[str, str]]:
    """從 `SKILL.md` 路徑建立結構化技能索引項目。

    參數：
        技能根目錄: skills 根目錄，例如 `assets/hermes_skills`。
        平台名稱: 目前平台名稱；未提供時自動偵測。
        停用技能名稱集合: 停用技能名稱；未提供時讀取環境設定。
        工具名稱集合: 可用工具名稱；未提供時使用空集合。
        工具集名稱集合: 可用 toolset 名稱；未提供時依工具名稱推導。

    返回值：
        技能項目清單。每個項目包含 `skill_name`、`category`、`description`
        與 `path`，供 `建立技能摘要()` 組裝 prompt 索引。
    """
    平台名稱 = 平台名稱 or 取得目前平台名稱()
    停用技能名稱集合 = 停用技能名稱集合 if 停用技能名稱集合 is not None else 讀取停用技能名稱集合()
    工具名稱集合 = 工具名稱集合 or set()
    工具集名稱集合 = 工具集名稱集合 if 工具集名稱集合 is not None else 建立可用工具集名稱集合(工具名稱集合)
    技能項目清單: list[dict[str, str]] = []
    for 技能路徑 in 走訪技能索引檔案(技能根目錄, "SKILL.md"):
        技能項目 = 建立技能索引項目(技能路徑, 技能根目錄, 平台名稱, 停用技能名稱集合, 工具名稱集合, 工具集名稱集合)
        if 技能項目:
            技能項目清單.append(技能項目)
    return 技能項目清單


def 建立技能索引項目(
    技能路徑: Path,
    技能根目錄: Path,
    平台名稱: str | None = None,
    停用技能名稱集合: set[str] | None = None,
    工具名稱集合: set[str] | None = None,
    工具集名稱集合: set[str] | None = None,
) -> dict[str, str] | None:
    """建立單一技能的索引資料。

    參數：
        技能路徑: `SKILL.md` 檔案路徑。
        技能根目錄: skills 根目錄，用來計算分類與相對路徑。
        平台名稱: 目前平台名稱；未提供時自動偵測。
        停用技能名稱集合: 停用技能名稱集合。
        工具名稱集合: 目前可用工具名稱集合。
        工具集名稱集合: 目前可用 toolset 名稱集合。

    返回值：
        技能索引 dict；若路徑無法相對於根目錄、位於隱藏分類，或被過濾條件排除，
        回傳 None。
    """
    try:
        相對路徑 = 技能路徑.relative_to(技能根目錄)
    except ValueError:
        return None
    if len(相對路徑.parts) < 2 or any(片段.startswith(".") for 片段 in 相對路徑.parts):
        return None
    內文 = 技能路徑.read_text(encoding="utf-8", errors="replace")
    前置資料 = 解析Markdown前置資料(內文)
    技能名稱 = str(前置資料.get("name") or 相對路徑.parts[-2])
    平台名稱 = 平台名稱 or 取得目前平台名稱()
    停用技能名稱集合 = 停用技能名稱集合 or set()
    工具名稱集合 = 工具名稱集合 or set()
    工具集名稱集合 = 工具集名稱集合 if 工具集名稱集合 is not None else 建立可用工具集名稱集合(工具名稱集合)
    if 技能名稱 in 停用技能名稱集合 or 相對路徑.parts[-2] in 停用技能名稱集合:
        return None
    if not 技能是否符合平台(前置資料, 平台名稱):
        return None
    if not 技能是否符合工具條件(前置資料, 工具名稱集合, 工具集名稱集合):
        return None
    分類 = "/".join(相對路徑.parts[:-2]) if len(相對路徑.parts) > 2 else "general"
    return {
        "skill_name": 技能名稱,
        "category": 分類,
        "description": 截斷摘要文字(前置資料.get("description", "")),
        "path": str(技能路徑),
    }
