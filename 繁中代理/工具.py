"""工具登錄基礎設施。

功能：
    提供小型 Hermes-style tool registry。工具會以 OpenAI tool schema 傳給 provider；
    模型回傳 tool_calls 後，runtime 依工具名稱呼叫 handler，再把 tool result 以
    canonical `role=tool` 訊息放回 working messages。

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

from .使用者 import 使用者上下文


# 有些模型會用很多不同工具名稱（像是 memory_add、delete_memory 之類），
# 但我們其實只註冊一個 `memory` 工具，用 action 參數來分辨要做什麼。
# 將常見的寫入別名對應回來（變成統一名稱和 action），不管模型用哪種叫法都能命中，
# 避免「未知工具」而失敗。讀取不提供別名：記憶已隨系統提示注入，模型應直接讀上下文。
記憶工具別名對照: dict[str, tuple[str, str]] = {
    "memory_add": ("memory", "add"),
    "memory_create": ("memory", "add"),
    "add_memory": ("memory", "add"),
    "memory_replace": ("memory", "replace"),
    "memory_update": ("memory", "replace"),
    "update_memory": ("memory", "replace"),
    "memory_remove": ("memory", "remove"),
    "memory_delete": ("memory", "remove"),
    "delete_memory": ("memory", "remove"),
}


def 正規化工具別名(名稱: str, 參數: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    """把模型慣用的工具別名轉回本專案 canonical 工具與 action。

    參數：
        名稱: 模型呼叫的工具名稱。
        參數: 模型提供的工具參數。

    返回值：
        (canonical 工具名稱, 已補上 action 的參數)。非別名時原樣回傳。
    """
    對應 = 記憶工具別名對照.get(名稱)
    if not 對應:
        return 名稱, 參數
    canonical名稱, 動作 = 對應
    正規化參數 = dict(參數)
    正規化參數["action"] = 動作
    return canonical名稱, 正規化參數


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
        """轉成 OpenAI-compatible tool schema。"""
        return {
            "type": "function",
            "function": {
                "name": self.名稱,
                "description": self.說明,
                "parameters": self.參數結構,
            },
        }


class 工具登錄器:
    """保存工具 schema 與 handler 的登錄器。"""

    def __init__(self, 工作目錄: str | Path | None = None, 使用者上下文物件: 使用者上下文 | None = None, 已知工具名稱集合: set[str] | None = None) -> None:
        """初始化空工具表。

        參數：
            工作目錄: Runtime 工作目錄；本機檔案與 terminal 工具的相對路徑會以此為基準。
            使用者上下文物件: 目前使用者權限；用於二次執行檢查與參數注入。
            已知工具名稱集合: schema 檔內所有已知工具，用於區分未知與未授權。
        """
        self.工作目錄 = str(Path(工作目錄 or os.getcwd()).expanduser().resolve())
        self.使用者上下文物件 = 使用者上下文物件
        self.已知工具名稱集合 = 已知工具名稱集合 or set()
        self.工具表: dict[str, 工具定義] = {}

    def 登錄工具(self, 工具: 工具定義) -> None:
        """登錄單一工具。"""
        self.工具表[工具.名稱] = 工具

    def 列出工具結構(self) -> list[dict[str, Any]]:
        """列出所有 OpenAI-compatible tool schema。"""
        return [工具.轉成OpenAI工具() for 工具 in self.工具表.values()]

    def 呼叫工具(self, 名稱: str, 參數: dict[str, Any]) -> str:
        """呼叫工具並回傳 JSON 字串。"""
        名稱, 參數 = 正規化工具別名(名稱, 參數)
        工具 = self.工具表.get(名稱)
        if not 工具:
            if 名稱 in self.已知工具名稱集合:
                return json.dumps({"success": False, "error": f"使用者無權使用工具：{名稱}", "permission_denied": True}, ensure_ascii=False)
            return json.dumps({"success": False, "error": f"未知工具：{名稱}"}, ensure_ascii=False)
        if self.使用者上下文物件 and not self.使用者上下文物件.工具是否允許(名稱):
            return json.dumps({"success": False, "error": f"使用者無權使用工具：{名稱}", "permission_denied": True}, ensure_ascii=False)
        try:
            工具參數 = dict(參數)
            工具參數["_runtime_workdir"] = self.工作目錄
            if self.使用者上下文物件:
                工具參數["_current_user_id"] = self.使用者上下文物件.user_id
                工具參數["_enabled_skills"] = sorted(self.使用者上下文物件.enabled_skills) if self.使用者上下文物件.enabled_skills is not None else None
                工具參數["_skill_roots"] = [str(路徑) for 路徑 in self.使用者上下文物件.skill_roots] if self.使用者上下文物件.skill_roots is not None else None
                工具參數["_allowed_workdirs"] = [str(路徑) for 路徑 in self.使用者上下文物件.allowed_workdirs] if self.使用者上下文物件.allowed_workdirs is not None else None
                工具參數["_memory_home"] = str(self.使用者上下文物件.memory_home) if self.使用者上下文物件.memory_home else None
            結果 = 工具.處理函數(工具參數)
            return json.dumps({"success": True, "result": 結果}, ensure_ascii=False)
        except PermissionError as 錯誤:
            return json.dumps({"success": False, "error": str(錯誤), "permission_denied": True}, ensure_ascii=False)
        except Exception as 錯誤:
            return json.dumps({"success": False, "error": str(錯誤)}, ensure_ascii=False)


def 回報工具未啟用(名稱: str) -> Callable[[dict[str, Any]], dict[str, Any]]:
    """建立未啟用工具的明確回報 handler。"""

    def 處理未啟用工具(參數: dict[str, Any]) -> dict[str, Any]:
        """回報工具 schema 已複製但本機外部整合尚未啟用。"""
        return {
            "success": False,
            "tool": 名稱,
            "error": "此 Hermes core tool 的 schema 已在本專案中複製；MVP 尚未啟用對應外部服務或完整 handler。",
            "received_args": 參數,
        }

    return 處理未啟用工具
