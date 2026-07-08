"""內建通用工具實作。

功能：
    放置 agent 系統原本就具備、且不屬於特定業務領域的本機工具 handler。
    這些函式由 `工具註冊.py` 接到對外 tool name，例如 read_file、terminal、
    skill_view、memory 與 session_search。
"""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path
from typing import Any

from .工具 import 解析工具路徑, 確認路徑允許


def 寫入檔案內容(參數: dict[str, Any]) -> dict[str, Any]:
    """完整覆寫文字檔案內容。"""
    路徑 = 解析工具路徑(參數.get("path", ""), 參數.get("_runtime_workdir"))
    確認路徑允許(路徑, 參數)
    內容 = str(參數.get("content", ""))
    路徑.parent.mkdir(parents=True, exist_ok=True)
    路徑.write_text(內容, encoding="utf-8")
    return {"path": str(路徑), "bytes_written": len(內容.encode("utf-8"))}


def 套用文字修補(參數: dict[str, Any]) -> dict[str, Any]:
    """執行簡化版文字替換修補。"""
    路徑 = 解析工具路徑(參數.get("path", ""), 參數.get("_runtime_workdir"))
    確認路徑允許(路徑, 參數)
    舊文字 = str(參數.get("old_string", ""))
    新文字 = str(參數.get("new_string", ""))
    是否全部替換 = bool(參數.get("replace_all", False))
    原文 = 路徑.read_text(encoding="utf-8", errors="replace")
    if not 舊文字:
        raise ValueError("old_string 不可為空")
    次數 = 原文.count(舊文字)
    if 次數 == 0:
        raise ValueError("找不到 old_string")
    if 次數 > 1 and not 是否全部替換:
        raise ValueError("old_string 不唯一；請設定 replace_all=true")
    替換後 = 原文.replace(舊文字, 新文字) if 是否全部替換 else 原文.replace(舊文字, 新文字, 1)
    路徑.write_text(替換後, encoding="utf-8")
    return {"path": str(路徑), "replacements": 次數 if 是否全部替換 else 1}


def 讀取檔案內容(參數: dict[str, Any]) -> dict[str, Any]:
    """讀取文字檔案的一段內容。"""
    路徑 = 解析工具路徑(參數.get("path", ""), 參數.get("_runtime_workdir"))
    確認路徑允許(路徑, 參數)
    起始行 = int(參數.get("offset", 1) or 1)
    最大行數 = int(參數.get("limit", 200) or 200)
    文字 = 路徑.read_text(encoding="utf-8", errors="replace")
    行清單 = 文字.splitlines()
    片段 = 行清單[max(起始行 - 1, 0): max(起始行 - 1, 0) + 最大行數]
    return {"path": str(路徑), "content": "\n".join(片段), "total_lines": len(行清單)}


def 搜尋檔案(參數: dict[str, Any]) -> dict[str, Any]:
    """搜尋檔名或檔案內容。"""
    根目錄 = 解析工具路徑(參數.get("path", "."), 參數.get("_runtime_workdir"), 預設=".")
    確認路徑允許(根目錄, 參數)
    樣式 = str(參數.get("pattern", "*"))
    目標 = str(參數.get("target", "content"))
    限制 = int(參數.get("limit", 50) or 50)
    結果清單: list[Any] = []
    if 目標 == "files":
        for 路徑 in 根目錄.rglob(樣式):
            if 路徑.is_file():
                確認路徑允許(路徑, 參數)
                結果清單.append(str(路徑))
                if len(結果清單) >= 限制:
                    break
        return {"matches": 結果清單, "total_count": len(結果清單)}
    正規式 = re.compile(樣式)
    for 路徑 in 根目錄.rglob("*"):
        if not 路徑.is_file() or 路徑.name.startswith("."):
            continue
        try:
            確認路徑允許(路徑, 參數)
            for 行號, 行 in enumerate(路徑.read_text(encoding="utf-8", errors="replace").splitlines(), start=1):
                if 正規式.search(行):
                    結果清單.append({"path": str(路徑), "line": 行號, "content": 行[:500]})
                    if len(結果清單) >= 限制:
                        return {"matches": 結果清單, "total_count": len(結果清單)}
        except PermissionError:
            raise
        except OSError:
            continue
    return {"matches": 結果清單, "total_count": len(結果清單)}


def 執行終端指令(參數: dict[str, Any]) -> dict[str, Any]:
    """執行短時間非互動 shell 指令。"""
    指令 = str(參數.get("command", ""))
    工作目錄 = str(解析工具路徑(參數.get("workdir") or ".", 參數.get("_runtime_workdir"), 預設="."))
    確認路徑允許(Path(工作目錄), 參數)
    逾時秒數 = int(參數.get("timeout", 60) or 60)
    完成程序 = subprocess.run(
        指令,
        shell=True,
        cwd=工作目錄,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=逾時秒數,
    )
    return {"output": 完成程序.stdout[-12000:], "exit_code": 完成程序.returncode}


def 內建技能根目錄() -> Path:
    """回傳專案內建（唯讀）Hermes 技能根目錄。"""
    return Path(__file__).resolve().parents[1] / "assets" / "hermes_skills"


def 使用者技能根目錄() -> Path:
    """回傳使用者技能（可寫）根目錄。

    代理透過 skill_manage 建立/修改/刪除的技能都存在這裡；本地測試用，
    日後可改接 BigQuery。
    """
    return Path(__file__).resolve().parents[1] / "assets" / "user_skill"


def 取得技能根目錄清單(參數: dict[str, Any]) -> list[Path]:
    """取得目前使用者允許的技能根目錄。

    多租戶隔離：一旦使用者明確設定 skill_roots（含空清單），就只回傳使用者
    指定的根目錄，不混入全域技能。其餘情況（admin 的 `*` 語意或本地 admin
    fallback，`_skill_roots is None`）回傳內建技能 + 使用者可寫技能根目錄，
    讓 skill_manage 建立的技能能在本地被看見。
    """
    if "_skill_roots" in 參數 and 參數.get("_skill_roots") is None:
        return [內建技能根目錄(), 使用者技能根目錄()]
    return [Path(str(路徑)).expanduser().resolve() for 路徑 in (參數.get("_skill_roots") or [])]


def 取得允許技能集合(參數: dict[str, Any]) -> set[str] | None:
    """取得目前使用者允許技能集合。"""
    原始值 = 參數.get("_enabled_skills")
    if 原始值 is None:
        return None
    return {str(項目) for 項目 in 原始值}


def 列出技能(參數: dict[str, Any]) -> dict[str, Any]:
    """列出專案內複製的 Hermes skills。"""
    允許技能集合 = 取得允許技能集合(參數)
    技能清單 = []
    for 根目錄 in 取得技能根目錄清單(參數):
        if 根目錄.exists():
            for 路徑 in 根目錄.rglob("SKILL.md"):
                技能名稱 = 路徑.parent.name
                if 允許技能集合 is not None and 技能名稱 not in 允許技能集合:
                    continue
                技能清單.append({"name": 技能名稱, "path": str(路徑)})
    return {"skills": 技能清單[:200], "total_count": len(技能清單)}


def 讀取技能skill_id(skill_md路徑: Path) -> str | None:
    """讀取 SKILL.md frontmatter 的 `id` 欄位（技能的穩定 skill_id）。

    內建技能沒有 id → 回傳 None。使用者技能由 skill_manage(create) 在建立時
    注入 UUID。
    """
    try:
        文字 = skill_md路徑.read_text(encoding="utf-8", errors="replace")[:4000]
    except OSError:
        return None
    在frontmatter = False
    for 行 in 文字.split("\n"):
        剝除 = 行.strip()
        if 剝除 == "---":
            if 在frontmatter:
                break
            在frontmatter = True
            continue
        if 在frontmatter:
            匹配 = re.match(r"^\s*id\s*:\s*(.+)$", 行)
            if 匹配:
                return 匹配.group(1).strip().strip("\"'")
    return None


def 列出使用者技能身分() -> list[dict[str, Any]]:
    """列出 user_skill 底下每個技能的 {skill_id, name, path}。

    name 為目錄名（LLM 定位用），skill_id 為 frontmatter 的穩定 UUID。供 usage /
    curator 以 skill_id 為 key 運作。
    """
    根目錄 = 使用者技能根目錄()
    if not 根目錄.exists():
        return []
    清單: list[dict[str, Any]] = []
    for skill_md in 根目錄.rglob("SKILL.md"):
        相對 = skill_md.relative_to(根目錄)
        if 相對.parts and 相對.parts[0].startswith("."):
            continue
        清單.append({
            "skill_id": 讀取技能skill_id(skill_md),
            "name": skill_md.parent.name,
            "path": str(skill_md),
        })
    return 清單


def _記錄技能使用事件(skill_md路徑: Path, 參數: dict[str, Any]) -> None:
    """best-effort 記錄一筆技能使用事件（以 skill_id 為 key）；失敗不影響 skill_view。

    只有帶 skill_id 的技能（= 使用者技能）會記；內建技能沒有 id，自然略過。
    """
    try:
        skill_id = 讀取技能skill_id(skill_md路徑)
        if not skill_id:
            return
        from .工具集.技能使用事件 import 記錄事件
        記錄事件(skill_id, 參數.get("_current_user_id"))
    except Exception:
        pass


def 讀取技能(參數: dict[str, Any]) -> dict[str, Any]:
    """依技能名稱讀取 SKILL.md 內容，並記錄一筆使用事件（以 skill_id 為 key）。"""
    名稱 = str(參數.get("name", ""))
    允許技能集合 = 取得允許技能集合(參數)
    if 允許技能集合 is not None and 名稱 not in 允許技能集合:
        raise PermissionError(f"使用者無權讀取技能：{名稱}")
    for 根目錄 in 取得技能根目錄清單(參數):
        for 路徑 in 根目錄.rglob("SKILL.md"):
            if 路徑.parent.name == 名稱 or 名稱 in str(路徑.parent):
                實際名稱 = 路徑.parent.name
                if 允許技能集合 is not None and 實際名稱 not in 允許技能集合:
                    raise PermissionError(f"使用者無權讀取技能：{實際名稱}")
                內容 = 路徑.read_text(encoding="utf-8", errors="replace")[:50000]
                _記錄技能使用事件(路徑, 參數)  # best-effort，成功讀到才記
                return {"name": 名稱, "path": str(路徑), "content": 內容}
    raise FileNotFoundError(f"找不到技能：{名稱}")


def 搜尋工作階段工具(參數: dict[str, Any]) -> dict[str, Any]:
    """搜尋 SQLite session history，提供 Hermes-like session_search 四種形狀。"""
    from .工作階段上下文 import 讀取目前工作階段資料庫路徑, 讀取目前使用者識別碼
    from .儲存 import 建立工作階段庫

    限制 = int(參數.get("limit", 3) or 3)
    視窗 = int(參數.get("window", 5) or 5)
    預設DB = 讀取目前工作階段資料庫路徑() or os.getenv("TESTAGENT2_SESSION_DB") or str(Path.home() / ".testagent2" / "sessions.sqlite3")
    資料庫路徑文字 = Path(str(參數.get("db_path") or 預設DB)).expanduser()
    if not 資料庫路徑文字.exists():
        return {"matches": [], "total_count": 0, "db_path": str(資料庫路徑文字), "error": "session database 不存在"}
    庫 = 建立工作階段庫(資料庫路徑文字)
    工作階段識別碼 = str(參數.get("session_id") or "").strip()
    錨點訊息識別碼 = 參數.get("around_message_id")
    查詢 = str(參數.get("query") or 參數.get("q") or "").strip()
    包含封存 = bool(參數.get("include_archived", False))
    來源 = 參數.get("source")
    使用者識別碼 = 參數.get("_current_user_id")
    if 使用者識別碼 is None and Path(預設DB).expanduser().resolve() == 資料庫路徑文字.expanduser().resolve():
        使用者識別碼 = 讀取目前使用者識別碼()
    if 工作階段識別碼 and 錨點訊息識別碼 is not None:
        return 庫.捲動工作階段訊息(工作階段識別碼, int(錨點訊息識別碼), window=視窗, user_id=使用者識別碼) | {"db_path": str(資料庫路徑文字)}
    if 工作階段識別碼:
        return 庫.讀取工作階段全文(工作階段識別碼, user_id=使用者識別碼) | {"db_path": str(資料庫路徑文字)}
    if 查詢:
        符合清單 = 庫.搜尋工作階段(查詢, limit=限制, window=視窗, include_archived=包含封存, source=來源, user_id=使用者識別碼)
        return {"matches": 符合清單, "total_count": len(符合清單), "db_path": str(資料庫路徑文字)}
    瀏覽結果 = 庫.瀏覽近期工作階段(limit=限制, include_archived=包含封存, source=來源, user_id=使用者識別碼)
    瀏覽結果["db_path"] = str(資料庫路徑文字)
    return 瀏覽結果


def 記憶工具(參數: dict[str, Any]) -> dict[str, Any]:
    """寫入或修改 Hermes-like 內建記憶。"""
    from .提示詞組裝器 import 提示詞設定, 提示詞組裝器
    from .記憶存放 import 記憶存放

    hermes家目錄 = Path(str(參數.get("_memory_home") or 提示詞組裝器(提示詞設定(工作目錄=os.getcwd())).取得Hermes家目錄()))
    存放 = 記憶存放(hermes家目錄)
    存放.載入()
    動作 = str(參數.get("action") or "")
    目標 = str(參數.get("target") or "memory")
    if 動作 == "add":
        return 存放.新增(目標, str(參數.get("content") or ""))
    if 動作 == "replace":
        return 存放.取代(目標, str(參數.get("old_text") or ""), str(參數.get("content") or ""))
    if 動作 == "remove":
        return 存放.移除(目標, str(參數.get("old_text") or ""))
    return {"success": False, "error": f"不支援的 memory action：{動作}"}
