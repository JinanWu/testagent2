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

    def __init__(self) -> None:
        """初始化空工具表。

        參數：無。
        返回值：None。
        """
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
            return json.dumps({"success": False, "error": f"未知工具：{名稱}"}, ensure_ascii=False)
        try:
            結果 = 工具.處理函數(參數)
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
    路徑 = Path(str(參數.get("path", ""))).expanduser()
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
    路徑 = Path(str(參數.get("path", ""))).expanduser()
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
    路徑 = Path(str(參數.get("path", ""))).expanduser()
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
    根目錄 = Path(str(參數.get("path", "."))).expanduser()
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
    工作目錄 = str(參數.get("workdir") or os.getcwd())
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


def 列出技能(參數: dict[str, Any]) -> dict[str, Any]:
    """列出專案內複製的 Hermes skills。

    參數：
        參數: 可包含 skills_root；預設為 assets/hermes_skills。

    返回值：
        技能名稱與路徑清單。
    """
    根目錄 = Path(str(參數.get("skills_root") or Path(__file__).resolve().parents[1] / "assets" / "hermes_skills"))
    技能清單 = []
    if 根目錄.exists():
        for 路徑 in 根目錄.rglob("SKILL.md"):
            技能清單.append({"name": 路徑.parent.name, "path": str(路徑)})
    return {"skills": 技能清單[:200], "total_count": len(技能清單)}


def 讀取技能(參數: dict[str, Any]) -> dict[str, Any]:
    """依技能名稱讀取 SKILL.md 內容。

    參數：
        參數: 包含 name 與可選 skills_root。

    返回值：
        包含 name、path、content 的 dict。
    """
    名稱 = str(參數.get("name", ""))
    根目錄 = Path(str(參數.get("skills_root") or Path(__file__).resolve().parents[1] / "assets" / "hermes_skills"))
    for 路徑 in 根目錄.rglob("SKILL.md"):
        if 路徑.parent.name == 名稱 or 名稱 in str(路徑.parent):
            return {"name": 名稱, "path": str(路徑), "content": 路徑.read_text(encoding="utf-8", errors="replace")[:50000]}
    raise FileNotFoundError(f"找不到技能：{名稱}")


def 建立預設工具登錄器() -> 工具登錄器:
    """建立含 Hermes core schema 的工具登錄器。

    參數：無。
    返回值：工具登錄器；會載入 `assets/hermes_core_tool_schemas.json` 中從 Hermes
        擷取的 48 個 core tool schema。MVP 已實作本機檔案、終端與技能讀取工具；
        其他需外部服務的工具會用明確未啟用 handler 回報。
    """
    登錄器 = 工具登錄器()
    已實作處理器: dict[str, Callable[[dict[str, Any]], Any]] = {
        "read_file": 讀取檔案內容,
        "write_file": 寫入檔案內容,
        "patch": 套用文字修補,
        "search_files": 搜尋檔案,
        "terminal": 執行終端指令,
        "skills_list": 列出技能,
        "skill_view": 讀取技能,
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
