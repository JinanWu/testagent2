"""Hermes-style 上下文壓縮器。

功能：
    接近 Hermes 的 context compression 行為：threshold 使用 minimum floor、
    provider usage 可直接驅動判斷、壓縮前先修剪舊 tool result、用 token budget
    動態保護 tail、迭代更新既有 summary、清理 tool call/result 配對並避免角色序列
    破壞。摘要主路徑可注入 auxiliary LLM；測試或失敗時才使用 deterministic fallback。
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Any, Callable

from .提示詞常數 import 壓縮摘要前綴

最低上下文長度 = 8192
摘要結束標記 = "--- END OF CONTEXT SUMMARY — respond to the message below, not the summary above ---"
摘要欄位 = "_compressed_summary"
舊工具結果佔位文字 = "[Old tool output cleared to save context space]"


摘要函式型別 = Callable[[str, int], str]


def 粗估訊息Token數(訊息清單: list[dict[str, Any]], 系統提示詞: str = "", 工具清單: list[dict[str, Any]] | None = None) -> int:
    """粗略估算 request token 數。

    參數：
        訊息清單: OpenAI-compatible messages；不應包含已分離的 stable system prompt。
        系統提示詞: 只在組裝實際 request 時額外計入的 system prompt 字串。
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


def 是否摘要訊息(訊息: dict[str, Any]) -> bool:
    """判斷訊息是否為壓縮摘要。"""
    return 訊息.get(摘要欄位) is True or str(訊息.get("content", "")).startswith(壓縮摘要前綴)


@dataclass
class 壓縮結果:
    """描述壓縮後的訊息與是否發生壓縮。"""

    訊息清單: list[dict[str, Any]]
    是否已壓縮: bool
    壓縮前Token數: int
    壓縮後Token數: int
    原因: str = ""
    是否應停止壓縮: bool = False


class 上下文壓縮器:
    """執行 Hermes-style context compression。"""

    def __init__(
        self,
        上下文長度: int = 32768,
        觸發比例: float = 0.5,
        保留開頭數: int = 3,
        保留尾端數: int = 20,
        摘要函式: 摘要函式型別 | None = None,
        摘要失敗是否中止: bool = False,
    ) -> None:
        """初始化壓縮器。"""
        self.上下文長度 = 上下文長度
        self.觸發比例 = 觸發比例
        self.保留開頭數 = 保留開頭數
        self.保留尾端數 = 保留尾端數
        self.門檻Token數 = max(int(self.上下文長度 * self.觸發比例), 最低上下文長度)
        self.最後提示Token數 = 0
        self.最後回應提示Token數: int | None = None
        self.摘要函式 = 摘要函式
        self.摘要失敗是否中止 = 摘要失敗是否中止
        self.無效壓縮次數 = 0
        self.壓縮次數 = 0
        self.摘要失敗冷卻到 = 0.0
        self.是否停用壓縮 = False

    def 從回應使用量更新(self, 使用量: dict[str, Any]) -> int | None:
        """使用 provider 回傳的 prompt tokens 更新壓縮判斷依據。"""
        token數 = 使用量.get("prompt_token_count") or 使用量.get("input_tokens") or 使用量.get("prompt_tokens")
        if token數 is None:
            return None
        try:
            self.最後回應提示Token數 = int(token數)
        except (TypeError, ValueError):
            return None
        self.最後提示Token數 = self.最後回應提示Token數
        return self.最後回應提示Token數

    def 是否需要壓縮(self, token數: int) -> bool:
        """判斷是否超過壓縮門檻。"""
        self.最後提示Token數 = token數
        return not self.是否停用壓縮 and token數 >= self.門檻Token數

    def 壓縮訊息(
        self,
        訊息清單: list[dict[str, Any]],
        系統提示詞: str = "",
        工具清單: list[dict[str, Any]] | None = None,
        provider提示Token數: int | None = None,
        強制: bool = False,
    ) -> 壓縮結果:
        """壓縮訊息清單；provider提示Token數 存在時優先使用真實 usage 判斷。"""
        壓縮前估算 = 粗估訊息Token數(訊息清單, 系統提示詞, 工具清單)
        判斷Token數 = provider提示Token數 if provider提示Token數 is not None else 壓縮前估算
        最少可壓縮長度 = self.保留開頭數 + 2
        if not 強制 and (not self.是否需要壓縮(判斷Token數) or len(訊息清單) <= 最少可壓縮長度):
            return 壓縮結果(訊息清單, False, 判斷Token數, 判斷Token數, "低於門檻或訊息不足")
        if self.摘要失敗冷卻到 > time.time() and not 強制:
            return 壓縮結果(訊息清單, False, 判斷Token數, 判斷Token數, "摘要失敗冷卻中")

        修剪後訊息 = self.修剪舊工具結果(訊息清單)
        開頭結束 = min(self.保留開頭數, len(修剪後訊息))
        尾端開始 = self.計算尾端起點(修剪後訊息, 開頭結束)
        開頭結束 = self.向後對齊邊界(修剪後訊息, 開頭結束)
        尾端開始 = max(開頭結束, self.向前對齊邊界(修剪後訊息, 尾端開始))

        原始開頭 = 修剪後訊息[:開頭結束]
        原始中段 = 修剪後訊息[開頭結束:尾端開始]
        原始尾端 = 修剪後訊息[尾端開始:]
        舊摘要清單 = [訊息 for 訊息 in [*原始開頭, *原始中段, *原始尾端] if 是否摘要訊息(訊息)]
        開頭 = [訊息 for 訊息 in 原始開頭 if not 是否摘要訊息(訊息)]
        中段 = [*舊摘要清單, *[訊息 for 訊息 in 原始中段 if not 是否摘要訊息(訊息)]]
        尾端 = [訊息 for 訊息 in 原始尾端 if not 是否摘要訊息(訊息)]
        if not 中段:
            return 壓縮結果(訊息清單, False, 判斷Token數, 判斷Token數, "沒有可壓縮中段")

        try:
            摘要文字 = self.建立摘要(開頭, 中段)
        except Exception:
            self.摘要失敗冷卻到 = time.time() + 600
            if self.摘要失敗是否中止:
                raise
            摘要文字 = self.建立Fallback摘要(中段)

        摘要訊息 = self.建立摘要訊息(開頭, 尾端, 摘要文字)
        壓縮後清單 = [*開頭, 摘要訊息, *尾端]
        壓縮後清單 = self.清理工具配對(self.合併相鄰同角色摘要(壓縮後清單))
        壓縮後估算 = 粗估訊息Token數(壓縮後清單, 系統提示詞, 工具清單)
        節省率 = 1 - (壓縮後估算 / max(壓縮前估算, 1))
        if 節省率 < 0.10:
            self.無效壓縮次數 += 1
            if self.無效壓縮次數 >= 3:
                self.是否停用壓縮 = True
                return 壓縮結果(訊息清單, False, 壓縮前估算, 壓縮後估算, "壓縮節省率過低，已停用", True)
        else:
            self.無效壓縮次數 = 0
        self.壓縮次數 += 1
        return 壓縮結果(壓縮後清單, True, 壓縮前估算, 壓縮後估算, "已壓縮")

    def 計算尾端起點(self, 訊息清單: list[dict[str, Any]], 開頭結束: int) -> int:
        """依 token budget 動態決定 tail 起點。"""
        可用尾端Token = max(1200, int(self.門檻Token數 * 0.30))
        可用字元 = 可用尾端Token * 4
        總字元 = 0
        已保留 = 0
        起點 = len(訊息清單)
        最大硬保留 = min(self.保留尾端數, len(訊息清單) - 開頭結束)
        for 索引 in range(len(訊息清單) - 1, 開頭結束 - 1, -1):
            訊息字元 = len(json.dumps(訊息清單[索引], ensure_ascii=False)) + 20
            if 已保留 >= min(8, 最大硬保留) and (已保留 >= self.保留尾端數 or 總字元 + 訊息字元 > 可用字元):
                break
            總字元 += 訊息字元
            已保留 += 1
            起點 = 索引
        return 起點

    def 修剪舊工具結果(self, 訊息清單: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """壓縮前先修剪舊 tool result 與過長 tool arguments。"""
        結果: list[dict[str, Any]] = []
        已見工具結果雜湊: set[str] = set()
        最後保留工具結果起點 = max(0, len(訊息清單) - max(8, self.保留尾端數))
        for 索引, 訊息 in enumerate(訊息清單):
            新訊息 = dict(訊息)
            if 新訊息.get("tool_calls"):
                新呼叫清單 = []
                for 呼叫 in 新訊息.get("tool_calls", []):
                    新呼叫 = dict(呼叫)
                    函數 = dict(新呼叫.get("function") or {})
                    參數 = str(函數.get("arguments") or "")
                    if len(參數) > 1200:
                        函數["arguments"] = 參數[:1200] + "…[arguments truncated for context compression]"
                    新呼叫["function"] = 函數
                    新呼叫清單.append(新呼叫)
                新訊息["tool_calls"] = 新呼叫清單
            if 新訊息.get("role") == "tool" and 索引 < 最後保留工具結果起點:
                內容 = str(新訊息.get("content", ""))
                指紋 = str(hash(內容))
                if 指紋 in 已見工具結果雜湊 or len(內容) > 800:
                    新訊息["content"] = self.摘要工具結果(新訊息, 是否重複=指紋 in 已見工具結果雜湊)
                已見工具結果雜湊.add(指紋)
            結果.append(新訊息)
        return 結果

    def 摘要工具結果(self, 訊息: dict[str, Any], 是否重複: bool = False) -> str:
        """把舊 tool result 壓縮成短摘要。"""
        內容 = str(訊息.get("content", ""))
        名稱 = 訊息.get("name") or "tool"
        if 是否重複:
            return f"[Deduplicated old {名稱} tool result; repeated output omitted]"
        return f"[Pruned old {名稱} tool result; original length={len(內容)} chars] {內容[:500]}"

    def 建立摘要(self, 開頭: list[dict[str, Any]], 中段: list[dict[str, Any]]) -> str:
        """使用 auxiliary LLM 摘要；未設定時使用 deterministic fallback。"""
        既有摘要 = "\n\n".join(self.清理摘要前綴(str(訊息.get("content", ""))) for 訊息 in [*開頭, *中段] if 是否摘要訊息(訊息))
        摘要輸入 = self.建立摘要輸入(中段, 既有摘要)
        目標Token = min(12000, max(2000, 粗估訊息Token數(中段) // 5))
        if self.摘要函式:
            文字 = self.摘要函式(摘要輸入, 目標Token).strip()
        else:
            文字 = self.建立Fallback摘要(中段, 既有摘要)
        return self.正規化摘要文字(文字)

    def 建立摘要輸入(self, 訊息清單: list[dict[str, Any]], 既有摘要: str = "") -> str:
        """建立給 auxiliary LLM 的 structured summary source。"""
        行清單 = [
            "Summarize the historical transcript as reference only.",
            "Required sections: Historical Task Snapshot, Historical In-Progress State, Historical Pending User Asks, Historical Remaining Work, Relevant Files, Resolved Questions, Key Decisions, Blocked Issues.",
        ]
        if 既有摘要:
            行清單.extend(["\nExisting summary to update:", 既有摘要[:8000]])
        行清單.append("\nNew historical messages:")
        for 訊息 in 訊息清單:
            行清單.append(json.dumps(訊息, ensure_ascii=False)[:4000])
        return "\n".join(行清單)

    def 建立Fallback摘要(self, 訊息清單: list[dict[str, Any]], 既有摘要: str = "") -> str:
        """摘要模型不可用時的 deterministic structured fallback。"""
        相關檔案: list[str] = []
        決策: list[str] = []
        待辦: list[str] = []
        行清單 = ["## Historical Task Snapshot"]
        if 既有摘要:
            行清單.append("Previous summary retained and updated:")
            行清單.append(self.清理摘要前綴(既有摘要)[:1200])
        for 訊息 in 訊息清單[-40:]:
            角色 = 訊息.get("role", "unknown")
            內容 = 訊息.get("content", "")
            if 是否摘要訊息(訊息):
                內容 = self.清理摘要前綴(str(內容))
            if not isinstance(內容, str):
                內容 = json.dumps(內容, ensure_ascii=False)
            短內容 = 內容[:360].replace("\n", " ")
            if any(標記 in 內容 for 標記 in ["/Users/", ".py", ".md"]):
                相關檔案.append(短內容[:220])
            if any(標記 in 內容 for 標記 in ["決定", "decision", "改為", "採用"]):
                決策.append(短內容)
            if any(標記 in 內容 for 標記 in ["TODO", "待辦", "還要", "remaining"]):
                待辦.append(短內容)
            行清單.append(f"- {角色}: {短內容}")
        行清單.extend([
            "\n## Historical In-Progress State",
            "- Preserve recent tail messages as the active continuation source.",
            "\n## Historical Pending User Asks",
            *(f"- {項目}" for 項目 in 待辦[:8]),
            "\n## Historical Remaining Work",
            "- Use only if the latest user message explicitly resumes it.",
            "\n## Relevant Files",
            *(f"- {項目}" for 項目 in 相關檔案[:10]),
            "\n## Resolved Questions",
            "- See compacted transcript bullets above.",
            "\n## Key Decisions",
            *(f"- {項目}" for 項目 in 決策[:10]),
            "\n## Blocked Issues",
            "- None captured by fallback summarizer.",
        ])
        return "\n".join(行清單)

    def 正規化摘要文字(self, 摘要文字: str) -> str:
        """清理舊 prefix，補上 Hermes reference-only 邊界。"""
        內容 = self.清理摘要前綴(摘要文字).strip()
        if "## Historical Task Snapshot" not in 內容:
            內容 = "## Historical Task Snapshot\n" + 內容
        return f"{壓縮摘要前綴}\n\n{內容}\n{摘要結束標記}"

    def 清理摘要前綴(self, 文字: str) -> str:
        """移除既有 summary prefix 與結束標記，避免多次壓縮越疊越厚。"""
        結果 = 文字.replace(壓縮摘要前綴, "").replace(摘要結束標記, "")
        return 結果.strip()

    def 建立摘要訊息(self, 開頭: list[dict[str, Any]], 尾端: list[dict[str, Any]], 摘要文字: str) -> dict[str, Any]:
        """依前後角色動態選擇 summary role。"""
        前角色 = 開頭[-1].get("role") if 開頭 else None
        後角色 = 尾端[0].get("role") if 尾端 else None
        角色 = "assistant"
        if 前角色 == "assistant" and 後角色 != "user":
            角色 = "user"
        elif 後角色 == "assistant" and 前角色 != "user":
            角色 = "user"
        return {"role": 角色, "content": 摘要文字, 摘要欄位: True}

    def 合併相鄰同角色摘要(self, 訊息清單: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """必要時把 summary merge 到 tail message，避免相鄰同角色造成嚴格 provider 拒絕。"""
        結果: list[dict[str, Any]] = []
        for 訊息 in 訊息清單:
            if 結果 and 是否摘要訊息(訊息) and 結果[-1].get("role") == 訊息.get("role"):
                結果[-1] = dict(結果[-1])
                結果[-1]["content"] = f"{結果[-1].get('content', '')}\n\n{訊息.get('content', '')}"
                結果[-1][摘要欄位] = True
                continue
            if 結果 and 是否摘要訊息(結果[-1]) and 結果[-1].get("role") == 訊息.get("role") and 訊息.get("role") in {"user", "assistant"}:
                合併 = dict(訊息)
                合併["content"] = f"{結果[-1].get('content', '')}\n\n[Most recent preserved message]\n{合併.get('content', '')}"
                合併[摘要欄位] = True
                結果[-1] = 合併
                continue
            結果.append(訊息)
        return 結果

    def 向後對齊邊界(self, 訊息清單: list[dict[str, Any]], 邊界: int) -> int:
        """避免 head 邊界切斷 assistant tool_call 與 tool result。"""
        while 邊界 < len(訊息清單) and 邊界 > 0:
            前訊息 = 訊息清單[邊界 - 1]
            目前 = 訊息清單[邊界]
            if 前訊息.get("tool_calls") or 目前.get("role") == "tool":
                邊界 += 1
                continue
            break
        return min(邊界, len(訊息清單))

    def 向前對齊邊界(self, 訊息清單: list[dict[str, Any]], 邊界: int) -> int:
        """避免 tail 邊界從 orphan tool result 開始。"""
        while 邊界 < len(訊息清單) and 邊界 > 0:
            目前 = 訊息清單[邊界]
            前訊息 = 訊息清單[邊界 - 1]
            if 目前.get("role") == "tool" or 前訊息.get("tool_calls"):
                邊界 -= 1
                continue
            break
        return max(0, 邊界)

    def 清理工具配對(self, 訊息清單: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """確保 tool result 一定有 call，tool_call 一定有 result。"""
        呼叫位置: dict[str, int] = {}
        結果識別碼: set[str] = set()
        for 索引, 訊息 in enumerate(訊息清單):
            for 呼叫 in 訊息.get("tool_calls", []) or []:
                if 呼叫.get("id"):
                    呼叫位置[str(呼叫.get("id"))] = 索引
            if 訊息.get("role") == "tool" and 訊息.get("tool_call_id"):
                結果識別碼.add(str(訊息.get("tool_call_id")))
        清單: list[dict[str, Any]] = []
        for 訊息 in 訊息清單:
            if 訊息.get("role") == "tool" and str(訊息.get("tool_call_id")) not in 呼叫位置:
                continue
            if 訊息.get("tool_calls"):
                新訊息 = dict(訊息)
                新呼叫 = [呼叫 for 呼叫 in 訊息.get("tool_calls", []) if str(呼叫.get("id")) in 結果識別碼]
                if 新呼叫:
                    新訊息["tool_calls"] = 新呼叫
                    清單.append(新訊息)
                elif 新訊息.get("content"):
                    新訊息.pop("tool_calls", None)
                    清單.append(新訊息)
                continue
            清單.append(訊息)
        return 清單
