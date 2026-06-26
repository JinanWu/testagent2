"""Agent runtime 與 tool loop。

功能：
    實作 Hermes-style 單 turn 執行流程：讀取/建立 SQLite session、建立或復用
    system prompt、早期持久化 user turn、preflight context compression、呼叫模型、
    執行 tool_calls、把 assistant tool_call 與 tool result 放回 working messages、
    在持久化點 flush，直到模型產生最終答案或達到最大迭代次數。

訊息格式：
    runtime 內部一律使用 OpenAI-compatible canonical shape；provider adapter 才負責
    轉換為 Gemini 或其他 SDK 的格式。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .上下文壓縮器 import 上下文壓縮器, 粗估訊息Token數
from .工作階段庫 import 工作階段庫
from .工具 import 工具登錄器, 建立預設工具登錄器
from .技能索引器 import 建立技能摘要 as 建立技能索引摘要
from .提示詞組裝器 import 提示詞設定, 提示詞組裝器
from .模型供應商 import 模型供應商


@dataclass
class 執行結果:
    """描述單次 agent turn 的結果。

    參數：
        最終回答: assistant 最終文字。
        工作階段識別碼: session id。
        訊息清單: 完整 canonical messages。
        模型呼叫次數: provider 呼叫次數。
        工具呼叫次數: tool call 次數。
        是否已壓縮: 本 turn 是否觸發 context compression。

    返回值：
        dataclass 實例。
    """

    最終回答: str
    工作階段識別碼: str
    訊息清單: list[dict[str, Any]]
    模型呼叫次數: int
    工具呼叫次數: int
    是否已壓縮: bool


class 代理執行階段:
    """Hermes-style CLI AgentRuntime。

    參數：
        工作階段庫物件: SQLite session store。
        模型供應商物件: provider adapter。
        模型名稱: 模型名稱。
        供應商名稱: provider 名稱。
        平台名稱: gateway/platform 名稱；尚未接 gateway 時預設為 api_server。
        工具登錄器物件: 可選工具登錄器；預設建立 MVP 工具。
        工作目錄: prompt context 與 terminal 工具的預設工作目錄。
        最大迭代次數: tool loop 最大模型呼叫次數。

    返回值：
        可執行使用者訊息的 runtime。
    """

    def __init__(
        self,
        工作階段庫物件: 工作階段庫,
        模型供應商物件: 模型供應商,
        模型名稱: str,
        供應商名稱: str = "gemini-adc",
        平台名稱: str = "api_server",
        工具登錄器物件: 工具登錄器 | None = None,
        工作目錄: str = ".",
        最大迭代次數: int = 8,
        上下文長度: int = 32768,
    ) -> None:
        """初始化 runtime。

        參數：見類別文檔。
        返回值：None。
        """
        self.工作階段庫物件 = 工作階段庫物件
        self.模型供應商物件 = 模型供應商物件
        self.模型名稱 = 模型名稱
        self.供應商名稱 = 供應商名稱
        self.平台名稱 = 平台名稱
        self.工具登錄器物件 = 工具登錄器物件 or 建立預設工具登錄器()
        self.工作目錄 = str(Path(工作目錄).expanduser().resolve())
        self.最大迭代次數 = 最大迭代次數
        self.上下文壓縮器物件 = 上下文壓縮器(上下文長度=上下文長度)

    def 執行使用者訊息(self, 使用者訊息: str, 工作階段識別碼: str | None = None, 額外系統訊息: str | None = None) -> 執行結果:
        """執行單次使用者 turn。

        參數：
            使用者訊息: 使用者輸入。
            工作階段識別碼: 可選 session id；若不存在會建立。
            額外系統訊息: 可選 context tier system message。

        返回值：
            執行結果。
        """
        工作階段識別碼 = self.工作階段庫物件.建立或讀取工作階段(工作階段識別碼)
        歷史訊息 = self.工作階段庫物件.讀取訊息(工作階段識別碼)
        工作階段資料 = self.工作階段庫物件.讀取工作階段(工作階段識別碼) or {}
        工具結構清單 = self.工具登錄器物件.列出工具結構()
        系統提示詞 = 工作階段資料.get("system_prompt") or self.建立系統提示詞(工作階段識別碼, 額外系統訊息)
        if not 工作階段資料.get("system_prompt"):
            self.工作階段庫物件.更新系統提示詞(工作階段識別碼, 系統提示詞)

        訊息清單 = list(歷史訊息)
        if not 訊息清單 or 訊息清單[0].get("role") != "system":
            訊息清單.insert(0, {"role": "system", "content": 系統提示詞})
        訊息清單.append({"role": "user", "content": 使用者訊息})

        # Hermes-style crash-resilience：user turn 進入後先持久化。
        self.工作階段庫物件.寫入訊息清單(工作階段識別碼, 訊息清單)

        壓縮結果 = self.上下文壓縮器物件.壓縮訊息(訊息清單, 系統提示詞, 工具結構清單)
        是否已壓縮 = 壓縮結果.是否已壓縮
        訊息清單 = 壓縮結果.訊息清單
        if 是否已壓縮:
            self.工作階段庫物件.寫入訊息清單(工作階段識別碼, 訊息清單)

        模型呼叫次數 = 0
        工具呼叫次數 = 0
        最終回答 = ""
        for _ in range(self.最大迭代次數):
            模型呼叫次數 += 1
            模型回應 = self.模型供應商物件.產生回應(訊息清單, 工具結構清單)
            if 模型回應.使用量.get("prompt_token_count"):
                壓縮結果 = self.上下文壓縮器物件.壓縮訊息(訊息清單, 系統提示詞, 工具結構清單)
                if 壓縮結果.是否已壓縮:
                    是否已壓縮 = True
                    訊息清單 = 壓縮結果.訊息清單
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
                # Hermes 不要求每個 tool call 前一定立即寫 DB；此處在 tool result 完成後 flush。
                self.工作階段庫物件.寫入訊息清單(工作階段識別碼, 訊息清單)
                壓縮結果 = self.上下文壓縮器物件.壓縮訊息(訊息清單, 系統提示詞, 工具結構清單)
                if 壓縮結果.是否已壓縮:
                    是否已壓縮 = True
                    訊息清單 = 壓縮結果.訊息清單
                    self.工作階段庫物件.寫入訊息清單(工作階段識別碼, 訊息清單)
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

    def 建立系統提示詞(self, 工作階段識別碼: str, 額外系統訊息: str | None = None) -> str:
        """建立並回傳本 session 的 system prompt。

        參數：
            工作階段識別碼: session id。
            額外系統訊息: context tier 額外訊息。

        返回值：
            完整 system prompt。
        """
        技能摘要 = self.建立技能摘要()
        設定 = 提示詞設定(
            模型名稱=self.模型名稱,
            供應商名稱=self.供應商名稱,
            工作階段識別碼=工作階段識別碼,
            平台名稱=self.平台名稱,
            工具名稱清單=list(self.工具登錄器物件.工具表.keys()),
            技能摘要=技能摘要,
            工作目錄=self.工作目錄,
        )
        return 提示詞組裝器(設定).組裝系統提示詞(額外系統訊息)

    def 建立技能摘要(self) -> str:
        """建立可放入 system prompt 的技能索引摘要。

        參數：無。
        返回值：技能摘要文字。實際掃描、過濾、快取與組裝邏輯由
            `技能索引器` 負責；runtime 只提供技能根目錄與目前工具名稱。
        """
        技能根目錄 = Path(__file__).resolve().parents[1] / "assets" / "hermes_skills"
        工具名稱集合 = set(self.工具登錄器物件.工具表.keys())
        return 建立技能索引摘要(技能根目錄, 工具名稱集合)
