"""技能管理工具（skill_manage）。

功能：
    代理透過本工具建立/修改/刪除使用者技能，把成功做法沉澱成可重用的流程知識。
    技能寫入使用者可寫技能根目錄（見 `基本工具.使用者技能根目錄`），本地測試用；
    日後可改接 BigQuery。

Actions:
    create      -- 建立新技能（SKILL.md + 目錄）
    edit        -- 完整重寫技能的 SKILL.md
    patch       -- 對 SKILL.md 或支援檔做定向查找替換
    delete      -- 刪除整個技能
    write_file  -- 新增/覆寫 references/templates/scripts/assets 下的支援檔
    remove_file -- 移除支援檔

    權限由工具登錄器在呼叫前檢查（工具是否允許 skill_manage）；內建技能為唯讀，
    只有使用者技能根目錄下的技能可被修改。
"""

from __future__ import annotations

import os
import re
import shutil
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..基本工具 import 內建技能根目錄, 使用者技能根目錄, 讀取技能識別碼
from . import 技能使用量


def _取得雲端技能庫():
    """取得目前後端對應的雲端技能庫實例（維持單一分流來源）。

    沿用 `技能使用量._取得BigQuery技能庫()` 的判斷：`STORAGE_BACKEND=bigquery`
    時回傳 BigQuery 技能庫，否則回傳 None（表示改走本機硬碟）。

    參數：無。
    返回值：BigQuery技能庫 實例；sqlite 模式時為 None。
    """
    return 技能使用量._取得BigQuery技能庫()


def 產生目前時間字串() -> str:
    """產生目前 UTC 時間的 ISO 8601 字串，供技能建立/更新時間欄位使用。

    參數：
        無。

    返回：
        str，ISO 8601 時間字串，例如 `2026-07-13T08:30:00+00:00`。
    """
    return datetime.now(timezone.utc).isoformat()


def _包裝雲端技能錯誤(動作: str, 錯誤: Exception) -> dict[str, Any]:
    """把 BigQuery 技能操作例外轉成 skill_manage 結構化錯誤回應。

    參數：
        動作: 操作描述（例如「建立技能」），會出現在錯誤訊息中。
        錯誤: 雲端儲存層拋出的例外。

    返回：
        dict，含 success=False 與 error 欄位。
    """
    return {"success": False, "error": f"{動作}失敗：{錯誤}"}

技能名稱規則 = re.compile(r"^[a-z0-9][a-z0-9._-]*$")
最大技能名稱長度 = 64
最大描述長度 = 1024
最大技能內容字元數 = 100_000
最大支援檔位元組 = 1_048_576  # 1 MiB
允許技能子目錄 = {"references", "templates", "scripts", "assets"}


def _驗證技能名稱(名稱: str) -> str | None:
    """驗證技能名稱；合法回傳 None，否則回傳錯誤訊息。"""
    if not 名稱:
        return "技能名稱為必填。"
    if len(名稱) > 最大技能名稱長度:
        return f"技能名稱超過 {最大技能名稱長度} 字元。"
    if not 技能名稱規則.match(名稱):
        return f"技能名稱 '{名稱}' 不合法。只能用小寫字母、數字、-、_、.，且需以字母或數字開頭。"
    return None


def _驗證技能分類(分類: Any) -> str | None:
    """驗證可選的分類（單層目錄名）；合法回傳 None。

    分類來自模型 JSON 參數（未受型別約束），因此保留 isinstance 防禦。
    """
    if 分類 is None:
        return None
    if not isinstance(分類, str):
        return "分類必須是字串。"
    分類 = 分類.strip()
    if not 分類:
        return None
    if "/" in 分類 or "\\" in 分類:
        return f"分類 '{分類}' 不合法：必須是單層目錄名。"
    if len(分類) > 最大技能名稱長度:
        return f"分類超過 {最大技能名稱長度} 字元。"
    if not 技能名稱規則.match(分類):
        return f"分類 '{分類}' 不合法。只能用小寫字母、數字、-、_、.。"
    return None


def _驗證技能frontmatter(內容: str) -> str | None:
    """驗證 SKILL.md 具備 frontmatter 與必要欄位；合法回傳 None。

    採輕量檢查（不依賴 pyyaml）：確認 frontmatter 起訖、含 name/description、
    且 frontmatter 之後有正文。
    """
    if not 內容.strip():
        return "內容不可為空。"
    if not 內容.startswith("---"):
        return "SKILL.md 必須以 YAML frontmatter（---）開頭。"
    結束 = re.search(r"\n---\s*\n", 內容[3:])
    if not 結束:
        return "SKILL.md frontmatter 未關閉；請補上結尾的 '---' 行。"
    frontmatter區 = 內容[3:結束.start() + 3]
    if not re.search(r"(?m)^\s*name\s*:", frontmatter區):
        return "frontmatter 必須包含 'name' 欄位。"
    描述匹配 = re.search(r"(?m)^\s*description\s*:\s*(.*)$", frontmatter區)
    if not 描述匹配:
        return "frontmatter 必須包含 'description' 欄位。"
    if len(描述匹配.group(1).strip()) > 最大描述長度:
        return f"description 超過 {最大描述長度} 字元。"
    正文 = 內容[結束.end() + 3:].strip()
    if not 正文:
        return "SKILL.md 在 frontmatter 之後必須有正文（指引、步驟等）。"
    return None


def _驗證技能內容大小(內容: str, 標籤: str = "SKILL.md") -> str | None:
    """檢查內容未超過字元上限；合法回傳 None。"""
    if len(內容) > 最大技能內容字元數:
        return (
            f"{標籤} 內容為 {len(內容):,} 字元（上限 {最大技能內容字元數:,}）。"
            f"請考慮拆成較小的 SKILL.md，並把細節放到 references/ 或 templates/。"
        )
    return None


def _尋找使用者技能(名稱: str) -> Path | None:
    """在使用者技能根目錄下依名稱尋找技能；回傳技能目錄或 None。

    只搜尋可寫的使用者技能；內建技能為唯讀，不可被 skill_manage 修改。
    """
    根目錄 = 使用者技能根目錄()
    if not 根目錄.exists():
        return None
    for skill_md in 根目錄.rglob("SKILL.md"):
        if skill_md.parent.name == 名稱:
            return skill_md.parent
    return None


def _檢查內建技能是否存在(名稱: str) -> bool:
    """檢查唯讀內建技能是否已有同名技能，避免列表與檢視時名稱衝突。"""
    根目錄 = 內建技能根目錄()
    if not 根目錄.exists():
        return False
    return any(skill_md.parent.name == 名稱 for skill_md in 根目錄.rglob("SKILL.md"))


def _驗證支援檔路徑(檔案路徑: str) -> str | None:
    """驗證 write_file/remove_file 的檔案路徑；合法回傳 None。"""
    if not 檔案路徑:
        return "file_path 為必填。"
    正規化 = Path(檔案路徑)
    if ".." in 正規化.parts:
        return "不允許路徑穿越（'..'）。"
    if not 正規化.parts or 正規化.parts[0] not in 允許技能子目錄:
        return f"檔案必須位於下列子目錄之一：{', '.join(sorted(允許技能子目錄))}。收到：'{檔案路徑}'"
    if len(正規化.parts) < 2:
        return f"請提供檔案路徑而非僅目錄。例如：'{正規化.parts[0]}/example.md'"
    return None


def _解析支援檔目標(技能目錄: Path, 檔案路徑: str) -> tuple[Path | None, str | None]:
    """解析支援檔完整路徑並確保未逃出技能目錄。"""
    目標 = 技能目錄 / 檔案路徑
    try:
        目標.resolve().relative_to(技能目錄.resolve())
    except ValueError:
        return None, "檔案路徑逃出技能目錄。"
    return 目標, None


def _原子寫入技能檔(檔案路徑: Path, 內容: str) -> None:
    """原子寫入文字檔，避免中斷造成半寫入狀態。"""
    檔案路徑.parent.mkdir(parents=True, exist_ok=True)
    fd, 暫存 = tempfile.mkstemp(dir=str(檔案路徑.parent), prefix=f".{檔案路徑.name}.tmp.")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(內容)
        os.replace(暫存, 檔案路徑)
    except Exception:
        try:
            os.unlink(暫存)
        except OSError:
            pass
        raise


工具規格標記 = re.compile(r"(?im)^\s*(implementation|parameters|output_description)\s*:")


def _驗證非工具規格(內容: str) -> str | None:
    """擋掉把 skill 寫成工具/函式規格的內容（code、parameters、implementation）。

    skill 是給 LLM 讀的流程說明，不是會被執行的 code。以行首 YAML 鍵為訊號
    （`implementation:` / `parameters:` / `output_description:`）判斷，回傳錯誤或 None。
    """
    匹配 = 工具規格標記.search(內容)
    if 匹配:
        欄位 = 匹配.group(1)
        return (
            f"偵測到 '{欄位}:' —— skill 是給 LLM 讀的『流程說明』，不是工具/函式定義，"
            f"不能包含 parameters / implementation / 函式 / code。請改寫成 markdown 步驟"
            f"（## 何時用 / ## 步驟 / ## 注意）；可先 skill_view('skill-authoring') 看格式。"
        )
    return None


def _設定frontmatter欄位(內容: str, 鍵: str, 值: str) -> str:
    """在 frontmatter 設定/覆寫某欄位（移除既有同名欄位，插到 frontmatter 最前）。

    內容需以 '---' 開頭且有結尾 '---'；否則原樣回傳（交給 frontmatter 驗證擋）。
    """
    if not 內容.startswith("---"):
        return 內容
    行清單 = 內容.split("\n")
    結束索引 = next((i for i in range(1, len(行清單)) if 行清單[i].strip() == "---"), None)
    if 結束索引 is None:
        return 內容
    模式 = re.compile(rf"^\s*{re.escape(鍵)}\s*:")
    frontmatter行 = [行 for 行 in 行清單[1:結束索引] if not 模式.match(行)]
    return "\n".join([行清單[0], f"{鍵}: {值}", *frontmatter行, *行清單[結束索引:]])


def _寫入技能識別碼(內容: str, skill_id: str) -> str:
    """把 skill_id 寫進 frontmatter 的 `id:` 欄位；id 是穩定主鍵，改名/改寫都不變。"""
    return _設定frontmatter欄位(內容, "id", skill_id)


def _建立技能(名稱: str, 內容: str, 分類: str | None = None, user_id: str | None = None) -> dict[str, Any]:
    """建立新的使用者技能（SKILL.md），並生成穩定 skill_id（UUID）。"""
    for 錯誤 in (_驗證技能名稱(名稱), _驗證技能分類(分類), _驗證技能內容大小(內容)):
        if 錯誤:
            return {"success": False, "error": 錯誤}
    # 用必填參數 name 補/校正 frontmatter 的 name 欄位（模型常寫成 title 或漏寫），
    # 再驗 frontmatter：這樣即使欄位名寫錯，技能一樣建得成。
    內容 = _設定frontmatter欄位(內容, "name", 名稱)
    for 錯誤 in (_驗證技能frontmatter(內容), _驗證非工具規格(內容)):
        if 錯誤:
            return {"success": False, "error": 錯誤}
    庫 = _取得雲端技能庫()
    # 重名檢查：bigquery 查表，sqlite 掃硬碟
    if 庫 is not None:
        if 庫.讀取技能內容(名稱, user_id=user_id, 限定使用者=True) is not None:
            return {"success": False, "error": f"已存在名為 '{名稱}' 的使用者技能。"}
    elif _尋找使用者技能(名稱):
        return {"success": False, "error": f"已存在名為 '{名稱}' 的使用者技能。"}
    if _檢查內建技能是否存在(名稱):
        return {
            "success": False,
            "error": (
                f"名稱 '{名稱}' 與內建技能衝突。"
                f"請改用其他名稱（例如 'my-{名稱}' 或 '{名稱}-custom'）。"
            ),
        }
    skill_id = str(uuid.uuid4())
    內容 = _寫入技能識別碼(內容, skill_id)
    技能檔路徑: str | None = None
    if 庫 is not None:
        try:
            庫.建立技能(skill_id, 名稱, 內容, 分類, user_id=user_id, 建立時間=產生目前時間字串())
        except Exception as 錯誤:
            return _包裝雲端技能錯誤("建立技能", 錯誤)
    else:
        技能目錄 = (使用者技能根目錄() / 分類 / 名稱) if 分類 else (使用者技能根目錄() / 名稱)
        技能目錄.mkdir(parents=True, exist_ok=True)
        skill_md = 技能目錄 / "SKILL.md"
        _原子寫入技能檔(skill_md, 內容)
        技能檔路徑 = str(skill_md)
    技能使用量.初始化記錄(skill_id, user_id=user_id)
    結果: dict[str, Any] = {"success": True, "message": f"技能 '{名稱}' 已建立。", "path": 技能檔路徑, "skill_id": skill_id}
    if 分類:
        結果["category"] = 分類
    return 結果


def _編輯技能(名稱: str, 內容: str, user_id: str | None = None) -> dict[str, Any]:
    """完整重寫既有使用者技能的 SKILL.md；保留原 skill_id 不變、frontmatter name 對齊目錄。"""
    內容 = _設定frontmatter欄位(內容, "name", 名稱)
    for 錯誤 in (_驗證技能frontmatter(內容), _驗證技能內容大小(內容), _驗證非工具規格(內容)):
        if 錯誤:
            return {"success": False, "error": 錯誤}
    庫 = _取得雲端技能庫()
    if 庫 is not None:
        列 = 庫.讀取技能內容(名稱, user_id=user_id, 限定使用者=True)
        if not 列:
            return {"success": False, "error": f"找不到使用者技能 '{名稱}'。請先用 skills_list() 查看。"}
        現有識別碼 = 列.get("skill_id")
        if 現有識別碼:
            內容 = _寫入技能識別碼(內容, 現有識別碼)  # 全量改寫不能弄丟身分
        try:
            庫.更新技能內容(現有識別碼, 內容, 產生目前時間字串())
        except Exception as 錯誤:
            return _包裝雲端技能錯誤("更新技能", 錯誤)
        return {"success": True, "message": f"技能 '{名稱}' 已更新。", "path": None}
    技能目錄 = _尋找使用者技能(名稱)
    if not 技能目錄:
        return {"success": False, "error": f"找不到使用者技能 '{名稱}'。請先用 skills_list() 查看。"}
    skill_md = 技能目錄 / "SKILL.md"
    現有識別碼 = 讀取技能識別碼(skill_md)
    if 現有識別碼:
        內容 = _寫入技能識別碼(內容, 現有識別碼)  # 全量改寫不能弄丟身分
    _原子寫入技能檔(skill_md, 內容)
    return {"success": True, "message": f"技能 '{名稱}' 已更新。", "path": str(技能目錄)}


def _修補技能(名稱: str, 舊文字: str, 新文字: str | None, 檔案路徑: str | None = None, 全部替換: bool = False, user_id: str | None = None) -> dict[str, Any]:
    """對技能檔做定向查找替換；預設針對 SKILL.md，可用 file_path 指定支援檔。"""
    if not 舊文字:
        return {"success": False, "error": "patch 需要 old_string。"}
    if 新文字 is None:
        return {"success": False, "error": "patch 需要 new_string；要刪除時傳空字串。"}
    庫 = _取得雲端技能庫()
    if 庫 is not None:
        if 檔案路徑:
            return {"success": False, "error": "BigQuery 模式尚未支援技能支援檔（references/scripts 等）的修補；支援檔尚未搬到 BQ。"}
        列 = 庫.讀取技能內容(名稱, user_id=user_id, 限定使用者=True)
        if not 列:
            return {"success": False, "error": f"找不到使用者技能 '{名稱}'。"}
        原文 = str(列.get("content") or "")
        次數 = 原文.count(舊文字)
        if 次數 == 0:
            return {"success": False, "error": "找不到 old_string。", "file_preview": 原文[:500]}
        if 次數 > 1 and not 全部替換:
            return {"success": False, "error": "old_string 不唯一；請加大上下文或設定 replace_all=true。"}
        替換後 = 原文.replace(舊文字, 新文字) if 全部替換 else 原文.replace(舊文字, 新文字, 1)
        錯誤 = _驗證技能內容大小(替換後)
        if 錯誤:
            return {"success": False, "error": 錯誤}
        錯誤 = _驗證技能frontmatter(替換後)
        if 錯誤:
            return {"success": False, "error": f"修補會破壞 SKILL.md 結構：{錯誤}"}
        現有識別碼 = 列.get("skill_id")
        if 現有識別碼:
            替換後 = _寫入技能識別碼(替換後, 現有識別碼)  # 修補不能弄丟身分
        try:
            庫.更新技能內容(現有識別碼, 替換後, 產生目前時間字串())
        except Exception as 錯誤:
            return _包裝雲端技能錯誤("修補技能", 錯誤)
        實際次數 = 次數 if 全部替換 else 1
        return {"success": True, "message": f"已修補技能 '{名稱}' 的 SKILL.md（{實際次數} 處替換）。"}
    技能目錄 = _尋找使用者技能(名稱)
    if not 技能目錄:
        return {"success": False, "error": f"找不到使用者技能 '{名稱}'。"}
    if 檔案路徑:
        錯誤 = _驗證支援檔路徑(檔案路徑)
        if 錯誤:
            return {"success": False, "error": 錯誤}
        目標, 錯誤 = _解析支援檔目標(技能目錄, 檔案路徑)
        if 錯誤:
            return {"success": False, "error": 錯誤}
    else:
        目標 = 技能目錄 / "SKILL.md"
    if not 目標.exists():
        return {"success": False, "error": f"找不到檔案：{檔案路徑 or 'SKILL.md'}"}
    原文 = 目標.read_text(encoding="utf-8")
    次數 = 原文.count(舊文字)
    if 次數 == 0:
        return {"success": False, "error": "找不到 old_string。", "file_preview": 原文[:500]}
    if 次數 > 1 and not 全部替換:
        return {"success": False, "error": "old_string 不唯一；請加大上下文或設定 replace_all=true。"}
    替換後 = 原文.replace(舊文字, 新文字) if 全部替換 else 原文.replace(舊文字, 新文字, 1)
    標籤 = "SKILL.md" if not 檔案路徑 else 檔案路徑
    錯誤 = _驗證技能內容大小(替換後, 標籤=標籤)
    if 錯誤:
        return {"success": False, "error": 錯誤}
    if not 檔案路徑:
        錯誤 = _驗證技能frontmatter(替換後)
        if 錯誤:
            return {"success": False, "error": f"修補會破壞 SKILL.md 結構：{錯誤}"}
    _原子寫入技能檔(目標, 替換後)
    實際次數 = 次數 if 全部替換 else 1
    return {"success": True, "message": f"已修補技能 '{名稱}' 的 {標籤}（{實際次數} 處替換）。"}


def _刪除技能(名稱: str, user_id: str | None = None) -> dict[str, Any]:
    """完整刪除使用者技能；被 pin 的技能拒絕刪除。pin/usage 以 skill_id 為 key。"""
    庫 = _取得雲端技能庫()
    if 庫 is not None:
        列 = 庫.讀取技能內容(名稱, user_id=user_id, 限定使用者=True)
        if not 列:
            return {"success": False, "error": f"找不到使用者技能 '{名稱}'。"}
        skill_id = 列.get("skill_id")
        if skill_id and 技能使用量.是否pin(skill_id):
            return {"success": False, "error": f"技能 '{名稱}' 已被 pin，無法刪除。請先解除 pin（unpin）再刪除。"}
        try:
            庫.刪除技能(skill_id)
        except Exception as 錯誤:
            return _包裝雲端技能錯誤("刪除技能", 錯誤)
        if skill_id:
            技能使用量.遺忘(skill_id)
        return {"success": True, "message": f"技能 '{名稱}' 已刪除。"}
    技能目錄 = _尋找使用者技能(名稱)
    if not 技能目錄:
        return {"success": False, "error": f"找不到使用者技能 '{名稱}'。"}
    skill_id = 讀取技能識別碼(技能目錄 / "SKILL.md")
    if skill_id and 技能使用量.是否pin(skill_id):
        return {"success": False, "error": f"技能 '{名稱}' 已被 pin，無法刪除。請先解除 pin（unpin）再刪除。"}
    根目錄 = 使用者技能根目錄()
    shutil.rmtree(技能目錄)
    if skill_id:
        技能使用量.遺忘(skill_id)
    父目錄 = 技能目錄.parent
    if 父目錄 != 根目錄 and 父目錄.exists() and not any(父目錄.iterdir()):
        父目錄.rmdir()
    return {"success": True, "message": f"技能 '{名稱}' 已刪除。"}


def _寫入技能支援檔(名稱: str, 檔案路徑: str, 檔案內容: str | None) -> dict[str, Any]:
    """新增或覆寫技能目錄下的支援檔。"""
    if _取得雲端技能庫() is not None:
        return {"success": False, "error": "BigQuery 模式尚未支援技能支援檔（references/scripts 等）；目前只把 SKILL.md 內容存進 BQ。"}
    錯誤 = _驗證支援檔路徑(檔案路徑)
    if 錯誤:
        return {"success": False, "error": 錯誤}
    if 檔案內容 is None:
        return {"success": False, "error": "file_content 為必填。"}
    位元組 = len(檔案內容.encode("utf-8"))
    if 位元組 > 最大支援檔位元組:
        return {"success": False, "error": f"檔案內容 {位元組:,} bytes（上限 {最大支援檔位元組:,} / 1 MiB）。"}
    錯誤 = _驗證技能內容大小(檔案內容, 標籤=檔案路徑)
    if 錯誤:
        return {"success": False, "error": 錯誤}
    技能目錄 = _尋找使用者技能(名稱)
    if not 技能目錄:
        return {"success": False, "error": f"找不到使用者技能 '{名稱}'。請先用 action='create' 建立。"}
    目標, 錯誤 = _解析支援檔目標(技能目錄, 檔案路徑)
    if 錯誤:
        return {"success": False, "error": 錯誤}
    _原子寫入技能檔(目標, 檔案內容)
    return {"success": True, "message": f"已寫入支援檔 '{檔案路徑}' 到技能 '{名稱}'。", "path": str(目標)}


def _移除技能支援檔(名稱: str, 檔案路徑: str) -> dict[str, Any]:
    """移除技能目錄下的支援檔。"""
    if _取得雲端技能庫() is not None:
        return {"success": False, "error": "BigQuery 模式尚未支援技能支援檔（references/scripts 等）；目前只把 SKILL.md 內容存進 BQ。"}
    錯誤 = _驗證支援檔路徑(檔案路徑)
    if 錯誤:
        return {"success": False, "error": 錯誤}
    技能目錄 = _尋找使用者技能(名稱)
    if not 技能目錄:
        return {"success": False, "error": f"找不到使用者技能 '{名稱}'。"}
    目標, 錯誤 = _解析支援檔目標(技能目錄, 檔案路徑)
    if 錯誤:
        return {"success": False, "error": 錯誤}
    if not 目標.exists():
        return {"success": False, "error": f"技能 '{名稱}' 內找不到檔案 '{檔案路徑}'。"}
    目標.unlink()
    父目錄 = 目標.parent
    if 父目錄 != 技能目錄 and 父目錄.exists() and not any(父目錄.iterdir()):
        父目錄.rmdir()
    return {"success": True, "message": f"已從技能 '{名稱}' 移除檔案 '{檔案路徑}'。"}


def 管理技能(參數: dict[str, Any]) -> dict[str, Any]:
    """skill_manage 主入口：建立/編輯/修補/刪除使用者技能與支援檔。

    參數：
        參數: 包含 action 與對應欄位（name、content、category、file_path、
            file_content、old_string、new_string、replace_all）。權限已由
            工具登錄器在呼叫前檢查（工具是否允許 skill_manage）。

    返回值：
        各 action 的結果 dict（含 success 與 message/error）。
    """
    動作 = str(參數.get("action", ""))
    名稱 = str(參數.get("name", ""))

    if 動作 == "create":
        內容 = 參數.get("content")
        if not 內容:
            return {"success": False, "error": "create 需要 content（完整 SKILL.md：frontmatter + 正文）。"}
        return _建立技能(名稱, str(內容), 參數.get("category"), user_id=參數.get("_current_user_id"))
    if 動作 == "edit":
        內容 = 參數.get("content")
        if not 內容:
            return {"success": False, "error": "edit 需要 content（完整更新後的 SKILL.md）。"}
        return _編輯技能(名稱, str(內容), user_id=參數.get("_current_user_id"))
    if 動作 == "patch":
        return _修補技能(
            名稱,
            str(參數.get("old_string") or ""),
            參數.get("new_string"),
            參數.get("file_path"),
            bool(參數.get("replace_all", False)),
            user_id=參數.get("_current_user_id"),
        )
    if 動作 == "delete":
        return _刪除技能(名稱, user_id=參數.get("_current_user_id"))
    if 動作 == "write_file":
        return _寫入技能支援檔(名稱, str(參數.get("file_path") or ""), 參數.get("file_content"))
    if 動作 == "remove_file":
        return _移除技能支援檔(名稱, str(參數.get("file_path") or ""))
    return {"success": False, "error": f"未知 action '{動作}'。可用：create, edit, patch, delete, write_file, remove_file"}
