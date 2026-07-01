"""工具登錄與本機工具實作。

功能：
    提供一個小型 Hermes-style tool registry。工具會以 OpenAI tool schema
    傳給 provider；模型回傳 tool_calls 後，runtime 依工具名稱呼叫 handler，
    再把 tool result 以 canonical `role=tool` 訊息放回 working messages。

工具範圍：
    - read_file: 讀取文字檔案。
    - search_files: 依檔名或內容搜尋。
    - terminal: 執行非互動 shell 指令。
    - skills_list / skill_view: 讀取專案內複製的 Hermes skills。
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from .使用者 import 使用者上下文


def 解析工具路徑(路徑值: Any, 工作目錄: str | Path | None = None, 預設: str = ".") -> Path:
    """把工具收到的路徑解析成絕對路徑；相對路徑以 runtime 工作目錄為基準。

    參數：
        路徑值: 工具參數中的 path/workdir。
        工作目錄: AgentRuntime 注入的工作目錄；None 時退回目前 process cwd。
        預設: 路徑值空白時使用的預設路徑。

    返回值：Path。絕對路徑會原樣展開；相對路徑會接在工作目錄後。
    """
    原始 = str(路徑值 or 預設)
    路徑 = Path(原始).expanduser()
    if 路徑.is_absolute():
        return 路徑
    基準 = Path(工作目錄 or os.getcwd()).expanduser()
    return (基準 / 路徑).resolve()


def 路徑是否在允許範圍(路徑: Path, 允許目錄清單: list[Path] | None) -> bool:
    """判斷路徑是否落在允許的工作目錄內。

    參數：
        路徑: 已解析的目標路徑。
        允許目錄清單: 允許根目錄；None 表示不限制。

    返回值：
        True 表示允許存取。
    """
    if 允許目錄清單 is None:
        return True
    解析路徑 = 路徑.expanduser().resolve()
    for 允許目錄 in 允許目錄清單:
        根目錄 = 允許目錄.expanduser().resolve()
        if 解析路徑 == 根目錄 or 根目錄 in 解析路徑.parents:
            return True
    return False


def 確認路徑允許(路徑: Path, 參數: dict[str, Any]) -> None:
    """檢查工具路徑是否符合目前使用者 allowed_workdirs。

    參數：
        路徑: 目標路徑。
        參數: 工具呼叫參數，應含 `_allowed_workdirs`。

    返回值：None。若不允許會丟出 PermissionError。
    """
    允許目錄清單 = 參數.get("_allowed_workdirs")
    if 允許目錄清單 is None:
        return
    if not 路徑是否在允許範圍(路徑, [Path(str(項目)) for 項目 in 允許目錄清單]):
        raise PermissionError(f"路徑超出使用者允許範圍：{路徑}")


@dataclass(frozen=True)
class 工具定義:
    """描述一個可供 LLM 呼叫的工具。

    參數：
        名稱: 工具名稱，必須對應 tool_call function.name。
        說明: 給模型看的工具用途。
        參數結構: JSON schema parameters。
        處理函數: 接收 dict 並回傳可 JSON 序列化資料的函數。

    返回值：
        dataclass 實例。
    """

    名稱: str
    說明: str
    參數結構: dict[str, Any]
    處理函數: Callable[[dict[str, Any]], Any]

    def 轉成OpenAI工具(self) -> dict[str, Any]:
        """轉成 OpenAI-compatible tool schema。

        參數：
            無。

        返回值：
            `{"type":"function","function":...}` dict。
        """
        return {
            "type": "function",
            "function": {
                "name": self.名稱,
                "description": self.說明,
                "parameters": self.參數結構,
            },
        }


class 工具登錄器:
    """保存工具 schema 與 handler 的登錄器。

    參數：
        無。

    返回值：
        可登錄與呼叫工具的物件。
    """

    def __init__(self, 工作目錄: str | Path | None = None, 使用者上下文物件: 使用者上下文 | None = None, 已知工具名稱集合: set[str] | None = None) -> None:
        """初始化空工具表。

        參數：
            工作目錄: Runtime 工作目錄；本機檔案與 terminal 工具的相對路徑會以此為基準。
            使用者上下文物件: 目前使用者權限；用於二次執行檢查與參數注入。
            已知工具名稱集合: schema 檔內所有已知工具，用於區分未知與未授權。

        返回值：None。
        """
        self.工作目錄 = str(Path(工作目錄 or os.getcwd()).expanduser().resolve())
        self.使用者上下文物件 = 使用者上下文物件
        self.已知工具名稱集合 = 已知工具名稱集合 or set()
        self.工具表: dict[str, 工具定義] = {}

    def 登錄工具(self, 工具: 工具定義) -> None:
        """登錄單一工具。

        參數：
            工具: 工具定義。

        返回值：
            None。
        """
        self.工具表[工具.名稱] = 工具

    def 列出工具結構(self) -> list[dict[str, Any]]:
        """列出所有 OpenAI-compatible tool schema。

        參數：無。
        返回值：tool schema 清單。
        """
        return [工具.轉成OpenAI工具() for 工具 in self.工具表.values()]

    def 呼叫工具(self, 名稱: str, 參數: dict[str, Any]) -> str:
        """呼叫工具並回傳 JSON 字串。

        參數：
            名稱: tool_call function.name。
            參數: tool_call function.arguments 解析後的 dict。

        返回值：
            JSON 字串；若工具不存在或執行失敗會包含 success=false。
        """
        工具 = self.工具表.get(名稱)
        if not 工具:
            if 名稱 in self.已知工具名稱集合:
                return json.dumps({"success": False, "error": f"使用者無權使用工具：{名稱}", "permission_denied": True}, ensure_ascii=False)
            return json.dumps({"success": False, "error": f"未知工具：{名稱}"}, ensure_ascii=False)
        if self.使用者上下文物件 and not self.使用者上下文物件.工具是否允許(名稱):
            return json.dumps({"success": False, "error": f"使用者無權使用工具：{名稱}", "permission_denied": True}, ensure_ascii=False)
        try:
            工具參數 = dict(參數)
            工具參數.setdefault("_runtime_workdir", self.工作目錄)
            if self.使用者上下文物件:
                工具參數.setdefault("_current_user_id", self.使用者上下文物件.user_id)
                工具參數.setdefault("_enabled_skills", sorted(self.使用者上下文物件.enabled_skills) if self.使用者上下文物件.enabled_skills is not None else None)
                工具參數.setdefault("_skill_roots", [str(路徑) for 路徑 in self.使用者上下文物件.skill_roots])
                工具參數.setdefault("_allowed_workdirs", [str(路徑) for 路徑 in self.使用者上下文物件.allowed_workdirs] if self.使用者上下文物件.allowed_workdirs is not None else None)
                工具參數.setdefault("_memory_home", str(self.使用者上下文物件.memory_home) if self.使用者上下文物件.memory_home else None)
            結果 = 工具.處理函數(工具參數)
            return json.dumps({"success": True, "result": 結果}, ensure_ascii=False)
        except Exception as 錯誤:
            return json.dumps({"success": False, "error": str(錯誤)}, ensure_ascii=False)


def 寫入檔案內容(參數: dict[str, Any]) -> dict[str, Any]:
    """完整覆寫文字檔案內容。

    參數：
        參數: 包含 path 與 content。

    返回值：
        包含 path 與 bytes_written 的 dict。
    """
    路徑 = 解析工具路徑(參數.get("path", ""), 參數.get("_runtime_workdir"))
    確認路徑允許(路徑, 參數)
    內容 = str(參數.get("content", ""))
    路徑.parent.mkdir(parents=True, exist_ok=True)
    路徑.write_text(內容, encoding="utf-8")
    return {"path": str(路徑), "bytes_written": len(內容.encode("utf-8"))}


def 套用文字修補(參數: dict[str, Any]) -> dict[str, Any]:
    """執行簡化版文字替換修補。

    參數：
        參數: 包含 path、old_string、new_string 與 replace_all。

    返回值：
        包含 path 與 replacements 的 dict。
    """
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


def 回報工具未啟用(名稱: str) -> Callable[[dict[str, Any]], dict[str, Any]]:
    """建立未啟用工具的明確回報 handler。

    參數：
        名稱: Hermes core tool 名稱。

    返回值：
        handler 函數；呼叫時回傳 success=false 與原因。
    """

    def 處理未啟用工具(參數: dict[str, Any]) -> dict[str, Any]:
        """回報工具 schema 已複製但本機外部整合尚未啟用。

        參數：
            參數: 模型提供的工具參數。

        返回值：
            說明未啟用原因的 dict。
        """
        return {
            "success": False,
            "tool": 名稱,
            "error": "此 Hermes core tool 的 schema 已在本專案中複製；MVP 尚未啟用對應外部服務或完整 handler。",
            "received_args": 參數,
        }

    return 處理未啟用工具


def 讀取檔案內容(參數: dict[str, Any]) -> dict[str, Any]:
    """讀取文字檔案的一段內容。

    參數：
        參數: 包含 path、offset、limit。offset 為 1-indexed 行號。

    返回值：
        包含 path、content、total_lines 的 dict。
    """
    路徑 = 解析工具路徑(參數.get("path", ""), 參數.get("_runtime_workdir"))
    確認路徑允許(路徑, 參數)
    起始行 = int(參數.get("offset", 1) or 1)
    最大行數 = int(參數.get("limit", 200) or 200)
    文字 = 路徑.read_text(encoding="utf-8", errors="replace")
    行清單 = 文字.splitlines()
    片段 = 行清單[max(起始行 - 1, 0): max(起始行 - 1, 0) + 最大行數]
    return {"path": str(路徑), "content": "\n".join(片段), "total_lines": len(行清單)}


def 搜尋檔案(參數: dict[str, Any]) -> dict[str, Any]:
    """搜尋檔名或檔案內容。

    參數：
        參數: 包含 pattern、path、target；target 可為 files 或 content。

    返回值：
        dict；files 模式回傳檔案路徑，content 模式回傳匹配行。
    """
    根目錄 = 解析工具路徑(參數.get("path", "."), 參數.get("_runtime_workdir"), 預設=".")
    確認路徑允許(根目錄, 參數)
    樣式 = str(參數.get("pattern", "*"))
    目標 = str(參數.get("target", "content"))
    限制 = int(參數.get("limit", 50) or 50)
    結果清單: list[Any] = []
    if 目標 == "files":
        for 路徑 in 根目錄.rglob(樣式):
            if 路徑.is_file():
                結果清單.append(str(路徑))
                if len(結果清單) >= 限制:
                    break
        return {"matches": 結果清單, "total_count": len(結果清單)}
    正規式 = re.compile(樣式)
    for 路徑 in 根目錄.rglob("*"):
        if not 路徑.is_file() or 路徑.name.startswith("."):
            continue
        try:
            for 行號, 行 in enumerate(路徑.read_text(encoding="utf-8", errors="replace").splitlines(), start=1):
                if 正規式.search(行):
                    結果清單.append({"path": str(路徑), "line": 行號, "content": 行[:500]})
                    if len(結果清單) >= 限制:
                        return {"matches": 結果清單, "total_count": len(結果清單)}
        except Exception:
            continue
    return {"matches": 結果清單, "total_count": len(結果清單)}


def 執行終端指令(參數: dict[str, Any]) -> dict[str, Any]:
    """執行短時間非互動 shell 指令。

    參數：
        參數: 包含 command、workdir、timeout。

    返回值：
        包含 output、exit_code 的 dict。
    """
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


def 取得技能根目錄清單(參數: dict[str, Any]) -> list[Path]:
    """取得目前使用者允許的技能根目錄。

    參數：
        參數: 工具呼叫參數，可能含 `_skill_roots`。

    返回值：
        技能根目錄清單；未設定時使用專案內建 skills。
    """
    根目錄清單 = [Path(str(路徑)).expanduser().resolve() for 路徑 in (參數.get("_skill_roots") or [])]
    if 根目錄清單:
        return 根目錄清單
    return [Path(__file__).resolve().parents[1] / "assets" / "hermes_skills"]


def 取得允許技能集合(參數: dict[str, Any]) -> set[str] | None:
    """取得目前使用者允許技能集合。

    參數：
        參數: 工具呼叫參數，可能含 `_enabled_skills`。

    返回值：
        None 表示允許全部；否則回傳技能名稱集合。
    """
    原始值 = 參數.get("_enabled_skills")
    if 原始值 is None:
        return None
    return {str(項目) for 項目 in 原始值}


def 列出技能(參數: dict[str, Any]) -> dict[str, Any]:
    """列出專案內複製的 Hermes skills。

    參數：
        參數: 可包含 skills_root；預設為 assets/hermes_skills。

    返回值：
        技能名稱與路徑清單。
    """
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


def 讀取技能(參數: dict[str, Any]) -> dict[str, Any]:
    """依技能名稱讀取 SKILL.md 內容。

    參數：
        參數: 包含 name 與可選 skills_root。

    返回值：
        包含 name、path、content 的 dict。
    """
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
                return {"name": 名稱, "path": str(路徑), "content": 路徑.read_text(encoding="utf-8", errors="replace")[:50000]}
    raise FileNotFoundError(f"找不到技能：{名稱}")



def 搜尋工作階段工具(參數: dict[str, Any]) -> dict[str, Any]:
    """搜尋 SQLite session history，提供 Hermes-like session_search 四種形狀。

    參數：
        參數: 可包含 query、session_id、around_message_id、limit、window、db_path。

    返回值：
        dict：依 discovery、scroll、read、browse 形狀回傳 session history。
    """
    from .工作階段上下文 import 讀取目前工作階段資料庫路徑, 讀取目前使用者識別碼
    from .工作階段庫 import 工作階段庫

    限制 = int(參數.get("limit", 3) or 3)
    視窗 = int(參數.get("window", 5) or 5)
    預設DB = 讀取目前工作階段資料庫路徑() or os.getenv("TESTAGENT2_SESSION_DB") or str(Path.home() / ".testagent2" / "sessions.sqlite3")
    資料庫路徑文字 = Path(str(參數.get("db_path") or 預設DB)).expanduser()
    if not 資料庫路徑文字.exists():
        return {"matches": [], "total_count": 0, "db_path": str(資料庫路徑文字), "error": "session database 不存在"}
    庫 = 工作階段庫(資料庫路徑文字)
    工作階段識別碼 = str(參數.get("session_id") or "").strip()
    錨點訊息識別碼 = 參數.get("around_message_id")
    查詢 = str(參數.get("query") or 參數.get("q") or "").strip()
    包含封存 = bool(參數.get("include_archived", False))
    來源 = 參數.get("source")
    使用者識別碼 = 參數.get("_current_user_id")
    if 使用者識別碼 is None:
        使用者識別碼 = 參數.get("user_id")
    if 工作階段識別碼 and 錨點訊息識別碼 is not None:
        return 庫.捲動工作階段訊息(工作階段識別碼, int(錨點訊息識別碼), window=視窗, user_id=使用者識別碼) | {"db_path": str(資料庫路徑文字)}
    if 工作階段識別碼:
        return 庫.讀取工作階段全文(工作階段識別碼) | {"db_path": str(資料庫路徑文字)}
    if 查詢:
        符合清單 = 庫.搜尋工作階段(查詢, limit=限制, window=視窗, include_archived=包含封存, source=來源, user_id=使用者識別碼)
        return {"matches": 符合清單, "total_count": len(符合清單), "db_path": str(資料庫路徑文字)}
    瀏覽結果 = 庫.瀏覽近期工作階段(limit=限制, include_archived=包含封存, source=來源, user_id=使用者識別碼)
    瀏覽結果["db_path"] = str(資料庫路徑文字)
    return 瀏覽結果

def 記憶工具(參數: dict[str, Any]) -> dict[str, Any]:
    """寫入或修改 Hermes-like 內建記憶。

    參數：
        參數: 包含 action、target、content、old_text。

    返回值：
        記憶存放操作結果 dict。
    """
    from .提示詞組裝器 import 提示詞設定, 提示詞組裝器
    from .記憶存放 import 記憶存放

    hermes家目錄 = 提示詞組裝器(提示詞設定(工作目錄=os.getcwd())).取得Hermes家目錄()
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

def 建立預設工具登錄器(工作目錄: str | Path | None = None) -> 工具登錄器:
    """建立含 Hermes core schema 的工具登錄器。

    參數：
        工作目錄: Runtime 工作目錄；工具相對路徑會以此為基準。

    返回值：工具登錄器；會載入 `assets/hermes_core_tool_schemas.json` 中從 Hermes
        擷取的 48 個 core tool schema。MVP 已實作本機檔案、終端與技能讀取工具；
        其他需外部服務的工具會用明確未啟用 handler 回報。
    """
    登錄器 = 工具登錄器(工作目錄)
    已實作處理器: dict[str, Callable[[dict[str, Any]], Any]] = {
        "read_file": 讀取檔案內容,
        "write_file": 寫入檔案內容,
        "patch": 套用文字修補,
        "search_files": 搜尋檔案,
        "terminal": 執行終端指令,
        "skills_list": 列出技能,
        "skill_view": 讀取技能,
        "session_search": 搜尋工作階段工具,
        "memory": 記憶工具,
    }
    結構路徑 = Path(__file__).resolve().parents[1] / "assets" / "hermes_core_tool_schemas.json"
    if 結構路徑.exists():
        結構清單 = json.loads(結構路徑.read_text(encoding="utf-8"))
        for 項目 in 結構清單:
            結構 = 項目["schema"]
            名稱 = 結構["name"]
            登錄器.登錄工具(工具定義(
                名稱=名稱,
                說明=結構.get("description", ""),
                參數結構=結構.get("parameters", {"type": "object", "properties": {}}),
                處理函數=已實作處理器.get(名稱, 回報工具未啟用(名稱)),
            ))
        return 登錄器

    for 名稱, 處理器 in 已實作處理器.items():
        登錄器.登錄工具(工具定義(名稱, 名稱, {"type": "object", "properties": {}}, 處理器))
    return 登錄器
