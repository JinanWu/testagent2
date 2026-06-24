"""Agent runtime 與 tool loop。

功能：
    實作 Hermes-style 單 turn 執行流程：system prompt 與 persisted transcript 分離、
    user turn 早期持久化、preflight/provider-usage/tool-loop 後壓縮、壓縮成功後做
    Session Split，並在 context overflow 類錯誤發生時壓縮後 retry。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .上下文壓縮器 import 上下文壓縮器
from .工作階段庫 import 工作階段庫
from .工具 import 工具登錄器, 建立預設工具登錄器
from .提示詞組裝器 import 提示詞設定, 提示詞組裝器
from .模型供應商 import 模型供應商


@dataclass
class 執行結果:
    """描述單次 agent turn 的結果。"""

    最終回答: str
    工作階段識別碼: str
    訊息清單: list[dict[str, Any]]
    模型呼叫次數: int
    工具呼叫次數: int
    是否已壓縮: bool


class 代理執行階段:
    """Hermes-style CLI AgentRuntime。"""

    def __init__(
        self,
        工作階段庫物件: 工作階段庫,
        模型供應商物件: 模型供應商,
        模型名稱: str,
        供應商名稱: str = "gemini-adc",
        工具登錄器物件: 工具登錄器 | None = None,
        工作目錄: str = ".",
        最大迭代次數: int = 8,
        上下文長度: int = 32768,
    ) -> None:
        """初始化 runtime。"""
        self.工作階段庫物件 = 工作階段庫物件
        self.模型供應商物件 = 模型供應商物件
        self.模型名稱 = 模型名稱
        self.供應商名稱 = 供應商名稱
        self.工具登錄器物件 = 工具登錄器物件 or 建立預設工具登錄器()
        self.工作目錄 = str(Path(工作目錄).expanduser().resolve())
        self.最大迭代次數 = 最大迭代次數
        self.上下文壓縮器物件 = 上下文壓縮器(上下文長度=上下文長度)

    def 執行使用者訊息(self, 使用者訊息: str, 工作階段識別碼: str | None = None, 額外系統訊息: str | None = None) -> 執行結果:
        """執行單次使用者 turn。"""
        工作階段識別碼 = self.工作階段庫物件.建立或讀取工作階段(工作階段識別碼)
        歷史訊息 = self.工作階段庫物件.讀取訊息(工作階段識別碼)
        工作階段資料 = self.工作階段庫物件.讀取工作階段(工作階段識別碼) or {}
        工具結構清單 = self.工具登錄器物件.列出工具結構()
        系統提示詞 = 工作階段資料.get("system_prompt") or self.建立系統提示詞(工作階段識別碼, 額外系統訊息)
        if not 工作階段資料.get("system_prompt"):
            self.工作階段庫物件.更新系統提示詞(工作階段識別碼, 系統提示詞)

        訊息清單 = [訊息 for 訊息 in 歷史訊息 if 訊息.get("role") != "system"]
        訊息清單.append({"role": "user", "content": 使用者訊息})

        self.工作階段庫物件.寫入訊息清單(工作階段識別碼, 訊息清單)
        工作階段識別碼, 訊息清單, 是否已壓縮 = self.嘗試壓縮並分裂工作階段(工作階段識別碼, 訊息清單, 系統提示詞, 工具結構清單)

        模型呼叫次數 = 0
        工具呼叫次數 = 0
        最終回答 = ""
        for _ in range(self.最大迭代次數):
            模型呼叫次數 += 1
            try:
                模型回應 = self.模型供應商物件.產生回應(self.建立Request訊息(系統提示詞, 訊息清單), 工具結構清單)
            except Exception as 錯誤:
                if self.是否ContextOverflow錯誤(錯誤):
                    工作階段識別碼, 訊息清單, 壓縮發生 = self.嘗試壓縮並分裂工作階段(工作階段識別碼, 訊息清單, 系統提示詞, 工具結構清單, 強制=True)
                    是否已壓縮 = 是否已壓縮 or 壓縮發生
                    模型回應 = self.模型供應商物件.產生回應(self.建立Request訊息(系統提示詞, 訊息清單), 工具結構清單)
                else:
                    raise

            真實提示Token數 = self.上下文壓縮器物件.從回應使用量更新(模型回應.使用量)
            if 真實提示Token數 is not None:
                self.工作階段庫物件.更新提示Token數(工作階段識別碼, 真實提示Token數)
                工作階段識別碼, 訊息清單, 壓縮發生 = self.嘗試壓縮並分裂工作階段(
                    工作階段識別碼,
                    訊息清單,
                    系統提示詞,
                    工具結構清單,
                    provider提示Token數=真實提示Token數,
                )
                是否已壓縮 = 是否已壓縮 or 壓縮發生

            if 模型回應.工具呼叫清單:
                assistant訊息 = {"role": "assistant", "content": 模型回應.文字 or "", "tool_calls": 模型回應.工具呼叫清單}
                訊息清單.append(assistant訊息)
                for 工具呼叫 in 模型回應.工具呼叫清單:
                    工具呼叫次數 += 1
                    函數 = 工具呼叫.get("function", {})
                    名稱 = str(函數.get("name", ""))
                    if "." in 名稱:
                        名稱 = 名稱.rsplit(".", 1)[-1]
                    try:
                        參數 = json.loads(函數.get("arguments") or "{}")
                    except json.JSONDecodeError:
                        參數 = {}
                    工具結果 = self.工具登錄器物件.呼叫工具(名稱, 參數)
                    訊息清單.append({"role": "tool", "tool_call_id": 工具呼叫.get("id"), "name": 名稱, "content": 工具結果})
                self.工作階段庫物件.寫入訊息清單(工作階段識別碼, 訊息清單)
                工作階段識別碼, 訊息清單, 壓縮發生 = self.嘗試壓縮並分裂工作階段(工作階段識別碼, 訊息清單, 系統提示詞, 工具結構清單)
                是否已壓縮 = 是否已壓縮 or 壓縮發生
                continue
            最終回答 = 模型回應.文字 or "（模型沒有回傳文字）"
            訊息清單.append({"role": "assistant", "content": 最終回答})
            self.工作階段庫物件.寫入訊息清單(工作階段識別碼, 訊息清單)
            break
        else:
            最終回答 = "已達最大迭代次數，仍未取得最終回答。"
            訊息清單.append({"role": "assistant", "content": 最終回答})
            self.工作階段庫物件.寫入訊息清單(工作階段識別碼, 訊息清單)

        return 執行結果(最終回答, 工作階段識別碼, 訊息清單, 模型呼叫次數, 工具呼叫次數, 是否已壓縮)

    def 建立Request訊息(self, 系統提示詞: str, 訊息清單: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """在 provider 呼叫邊界 prepend system prompt，不污染持久化 transcript。"""
        return [{"role": "system", "content": 系統提示詞}, *訊息清單]

    def 嘗試壓縮並分裂工作階段(
        self,
        工作階段識別碼: str,
        訊息清單: list[dict[str, Any]],
        系統提示詞: str,
        工具結構清單: list[dict[str, Any]],
        provider提示Token數: int | None = None,
        強制: bool = False,
    ) -> tuple[str, list[dict[str, Any]], bool]:
        """取得壓縮鎖後壓縮，成功時建立 Compression Session Split。"""
        with self.工作階段庫物件.壓縮鎖(工作階段識別碼) as 是否取得鎖:
            if not 是否取得鎖:
                return 工作階段識別碼, 訊息清單, False
            壓縮結果 = self.上下文壓縮器物件.壓縮訊息(
                訊息清單,
                系統提示詞,
                工具結構清單,
                provider提示Token數=provider提示Token數,
                強制=強制,
            )
            if not 壓縮結果.是否已壓縮:
                return 工作階段識別碼, 訊息清單, False
            新工作階段識別碼 = self.工作階段庫物件.建立壓縮後工作階段(工作階段識別碼, 壓縮結果.訊息清單, 系統提示詞)
            return 新工作階段識別碼, 壓縮結果.訊息清單, True

    def 是否ContextOverflow錯誤(self, 錯誤: Exception) -> bool:
        """辨識可透過壓縮重試的 context overflow / 413 類錯誤。"""
        文字 = str(錯誤).lower()
        關鍵字清單 = ["413", "payload too large", "context", "token", "too long", "long context", "image too large"]
        return any(關鍵字 in 文字 for 關鍵字 in 關鍵字清單)

    def 建立系統提示詞(self, 工作階段識別碼: str, 額外系統訊息: str | None = None) -> str:
        """建立並回傳本 session 的 system prompt。"""
        技能摘要 = self.建立技能摘要()
        設定 = 提示詞設定(
            模型名稱=self.模型名稱,
            供應商名稱=self.供應商名稱,
            工作階段識別碼=工作階段識別碼,
            平台名稱="cli",
            工具名稱清單=list(self.工具登錄器物件.工具表.keys()),
            技能摘要=技能摘要,
            工作目錄=self.工作目錄,
        )
        return 提示詞組裝器(設定).組裝系統提示詞(額外系統訊息)

    def 建立技能摘要(self) -> str:
        """建立可放入 system prompt 的技能索引摘要。"""
        技能根目錄 = Path(__file__).resolve().parents[1] / "assets" / "hermes_skills"
        if not 技能根目錄.exists():
            return "<available_skills>\n  (skills not copied yet)\n</available_skills>"
        名稱清單 = sorted({路徑.parent.name for 路徑 in 技能根目錄.rglob("SKILL.md")})
        顯示清單 = 名稱清單[:300]
        return "<available_skills>\n" + "\n".join(f"  - {名稱}" for 名稱 in 顯示清單) + "\n</available_skills>"
