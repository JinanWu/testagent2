"""工具登錄與本機工具實作。

功能：
    提供一個小型 Hermes-style tool registry。工具會以 OpenAI tool schema
    傳給 provider；模型回傳 tool_calls 後，runtime 依工具名稱呼叫 handler，
    再把 tool result 以 canonical `role=tool` 訊息放回 working messages。

工具範圍：
    此檔只保留工具系統的基礎設施。內建工具實作放在 `基本工具.py`，
    預設工具註冊流程放在 `工具註冊.py`。
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable


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

    def __init__(self, 工作目錄: str | Path | None = None) -> None:
        """初始化空工具表。

        參數：
            工作目錄: Runtime 工作目錄；本機檔案與 terminal 工具的相對路徑會以此為基準。

        返回值：None。
        """
        self.工作目錄 = str(Path(工作目錄 or os.getcwd()).expanduser().resolve())
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
            工具參數 = dict(參數)
            工具參數.setdefault("_runtime_workdir", self.工作目錄)
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
