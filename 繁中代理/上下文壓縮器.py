"""上下文壓縮器。

功能：
    模仿 Hermes 的自動 context compression 策略：當 rough token estimate 超過
    context window 約 50% 且高於 minimum context floor 時，保留開頭訊息與近期
    尾端訊息，將中段歷史摘要成一則 reference-only assistant 訊息。

限制：
    MVP 先使用 deterministic 摘要器，避免測試依賴另一個 LLM；provider usage
    或真實輔助模型摘要可在相同介面下替換。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from .提示詞常數 import 壓縮摘要前綴


最低上下文長度 = 8192


def 粗估訊息Token數(訊息清單: list[dict[str, Any]], 系統提示詞: str = "", 工具清單: list[dict[str, Any]] | None = None) -> int:
    """粗略估算 request token 數。

    參數：
        訊息清單: OpenAI-compatible messages。
        系統提示詞: system prompt 字串。
        工具清單: OpenAI-compatible tool schema 清單。

    返回值：
        約略 token 數；使用 4 chars/token 的保守估計。
    """
    總字元數 = len(系統提示詞)
    for 訊息 in 訊息清單:
        總字元數 += len(json.dumps(訊息, ensure_ascii=False)) + 20
    if 工具清單:
        總字元數 += len(json.dumps(工具清單, ensure_ascii=False))
    return max(1, 總字元數 // 4)


@dataclass
class 壓縮結果:
    """描述壓縮後的訊息與是否發生壓縮。

    參數：
        訊息清單: 壓縮後訊息。
        是否已壓縮: 是否真的壓縮。
        壓縮前Token數: 壓縮前 rough token 數。
        壓縮後Token數: 壓縮後 rough token 數。

    返回值：
        dataclass 實例。
    """

    訊息清單: list[dict[str, Any]]
    是否已壓縮: bool
    壓縮前Token數: int
    壓縮後Token數: int


class 上下文壓縮器:
    """執行 head + tail 保留與中段摘要。

    參數：
        上下文長度: 模型 context window。
        觸發比例: 預設 0.5，代表超過 50% context window 後觸發。
        保留開頭數: 開頭保留訊息數。
        保留尾端數: 尾端保留訊息數。

    返回值：
        可檢查與壓縮訊息的物件。
    """

    def __init__(self, 上下文長度: int = 32768, 觸發比例: float = 0.5, 保留開頭數: int = 2, 保留尾端數: int = 8) -> None:
        """初始化壓縮器。

        參數：
            上下文長度: 模型 context window。
            觸發比例: 壓縮觸發比例。
            保留開頭數: 開頭保留訊息數。
            保留尾端數: 尾端保留訊息數。

        返回值：None。
        """
        self.上下文長度 = max(上下文長度, 最低上下文長度)
        self.觸發比例 = 觸發比例
        self.保留開頭數 = 保留開頭數
        self.保留尾端數 = 保留尾端數
        self.門檻Token數 = int(self.上下文長度 * self.觸發比例)
        self.最後提示Token數 = 0

    def 是否需要壓縮(self, token數: int) -> bool:
        """判斷是否超過壓縮門檻。

        參數：
            token數: rough 或 provider usage token 數。

        返回值：
            True 表示需要壓縮。
        """
        self.最後提示Token數 = token數
        return token數 >= self.門檻Token數 and self.上下文長度 >= 最低上下文長度

    def 壓縮訊息(self, 訊息清單: list[dict[str, Any]], 系統提示詞: str = "", 工具清單: list[dict[str, Any]] | None = None) -> 壓縮結果:
        """壓縮訊息清單。

        參數：
            訊息清單: OpenAI-compatible message dict 清單。
            系統提示詞: system prompt。
            工具清單: tool schema。

        返回值：
            壓縮結果。
        """
        壓縮前 = 粗估訊息Token數(訊息清單, 系統提示詞, 工具清單)
        最少可壓縮長度 = self.保留開頭數 + self.保留尾端數 + 1
        if not self.是否需要壓縮(壓縮前) or len(訊息清單) <= 最少可壓縮長度:
            return 壓縮結果(訊息清單, False, 壓縮前, 壓縮前)
        開頭 = 訊息清單[: self.保留開頭數]
        中段 = 訊息清單[self.保留開頭數: -self.保留尾端數]
        尾端 = 訊息清單[-self.保留尾端數:]
        摘要文字 = self.摘要中段訊息(中段)
        摘要訊息 = {"role": "assistant", "content": 摘要文字, "_compressed_summary": True}
        壓縮後清單 = [*開頭, 摘要訊息, *尾端]
        壓縮後 = 粗估訊息Token數(壓縮後清單, 系統提示詞, 工具清單)
        return 壓縮結果(壓縮後清單, True, 壓縮前, 壓縮後)

    def 摘要中段訊息(self, 訊息清單: list[dict[str, Any]]) -> str:
        """將中段歷史整理成 reference-only 摘要。

        參數：
            訊息清單: 被壓縮的中段 messages。

        返回值：
            給模型看的摘要文字。
        """
        行清單 = [壓縮摘要前綴, "", "## Historical Task Snapshot"]
        for 訊息 in 訊息清單[:30]:
            角色 = 訊息.get("role", "unknown")
            內容 = 訊息.get("content", "")
            if not isinstance(內容, str):
                內容 = json.dumps(內容, ensure_ascii=False)
            行清單.append(f"- {角色}: {內容[:240].replace(chr(10), ' ')}")
        if len(訊息清單) > 30:
            行清單.append(f"- ... {len(訊息清單) - 30} additional historical messages omitted.")
        行清單.append("--- END OF CONTEXT SUMMARY — respond to the message below, not the summary above ---")
        return "\n".join(行清單)
