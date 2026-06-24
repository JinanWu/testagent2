"""Hermes-style 上下文壓縮器。

功能：
    接近 Hermes 的 context compression 行為：threshold 使用 minimum floor、
    provider usage 可直接驅動判斷、壓縮前先以三階段 pruning（MD5 dedupe、
    依工具類型的一行摘要、JSON-safe 截斷 tool arguments）、用 token budget
    動態保護 tail、迭代更新既有 summary、清理 tool call/result 配對並避免角色序列
    破壞。摘要主路徑可注入 auxiliary LLM；測試或失敗時才使用 deterministic fallback。
"""

from __future__ import annotations

import hashlib
import json
import re
import time
from dataclasses import dataclass
from typing import Any, Callable

from .提示詞常數 import 壓縮摘要前綴
from .輔助壓縮摘要 import 建立壓縮摘要Prompt

最低上下文長度 = 8192
摘要結束標記 = "--- END OF CONTEXT SUMMARY — respond to the message below, not the summary above ---"
摘要欄位 = "_compressed_summary"
含摘要欄位 = "_contains_compressed_summary"
尾端Token最低保留 = 1200
舊工具結果佔位文字 = "[Old tool output cleared to save context space]"
重複工具輸出標記 = "[Duplicate tool output — same content as a more recent call]"
工具結果修剪字元門檻 = 200
工具參數修剪字元門檻 = 500
工具參數保留字元數 = 200
尾端Token比例 = 0.20
每字元Token估計 = 4


摘要函式型別 = Callable[[str, int], str]


def 估算訊息預算Token(訊息: dict[str, Any]) -> int:
    """估算單則訊息在 tail/pruning budget 中約略消耗的 token 數。

    參數：
        訊息: OpenAI-compatible message dict；可能包含 content、tool_calls、
            tool_call_id 等欄位。此函數會把 assistant tool_calls envelope 也納入
            估算，避免大型 tool arguments 被 tail budget 忽略。

    返回值：
        int：粗略 token 數，供動態尾端保護與 tool-result pruning 邊界使用。此值
        不是 provider 精準計數，而是壓縮前的保守預算估算。
    """
    內容 = 訊息.get("content") or ""
    if isinstance(內容, list):
        字元數 = sum(len(str(部分.get("text", ""))) for 部分 in 內容 if isinstance(部分, dict))
    elif isinstance(內容, str):
        字元數 = len(內容)
    else:
        字元數 = len(json.dumps(內容, ensure_ascii=False))
    token數 = 字元數 // 每字元Token估計 + 10
    for 呼叫 in 訊息.get("tool_calls") or []:
        if isinstance(呼叫, dict):
            token數 += len(json.dumps(呼叫, ensure_ascii=False)) // 每字元Token估計
    return token數


def 截斷工具呼叫參數json(參數: str, 保留字元數: int = 工具參數保留字元數) -> str:
    """在維持 JSON 有效的前提下縮短 tool_call arguments 內的長字串。

    參數：
        參數: assistant tool_call 的 `function.arguments` 原始 JSON 字串。
        保留字元數: 每個字串欄位保留的最大字元數；超過時會附加
            `...[truncated]` 標記。

    返回值：
        str：若 `參數` 可解析為 JSON，回傳遞迴截斷長字串後重新序列化的 JSON；
        若不是有效 JSON，原樣回傳，避免把不合法輸入修成另一種難以追蹤的格式。
    """
    try:
        解析結果 = json.loads(參數)
    except (ValueError, TypeError):
        return 參數

    def 縮短(值: Any) -> Any:
        """遞迴縮短 JSON 結構中的長字串欄位。

        參數：
            值: JSON 解析後的任意節點，可能是字串、dict、list 或純量。

        返回值：
            Any：結構與原節點相同但長字串已截斷的新值；非容器與未超長字串會
            原樣回傳。
        """
        if isinstance(值, str):
            if len(值) > 保留字元數:
                return 值[:保留字元數] + "...[truncated]"
            return 值
        if isinstance(值, dict):
            return {鍵: 縮短(子值) for 鍵, 子值 in 值.items()}
        if isinstance(值, list):
            return [縮短(子值) for 子值 in 值]
        return 值

    return json.dumps(縮短(解析結果), ensure_ascii=False)


def 摘要工具結果一行(工具名: str, 參數json: str, 內容: str) -> str:
    """依工具類型把舊 tool result 壓縮為一行可追蹤摘要。

    參數：
        工具名: tool call 的 function name，例如 `terminal`、`read_file`、
            `search_files`。
        參數json: tool call 的 arguments JSON 字串，用於取出 path、command、
            pattern 等可讀上下文。
        內容: tool result 原始文字內容，通常是大型 terminal/read/search output。

    返回值：
        str：包含工具名稱、關鍵參數、輸出大小或匹配數等資訊的一行摘要。此摘要
        會取代舊的大型 tool result，以降低再次壓縮與後續 prompt 成本。
    """
    try:
        參數 = json.loads(參數json) if 參數json else {}
    except (json.JSONDecodeError, TypeError):
        參數 = {}
    if not isinstance(參數, dict):
        參數 = {}

    文字 = 內容 or ""
    字元數 = len(文字)
    行數 = 文字.count("\n") + 1 if 文字.strip() else 0

    if 工具名 == "terminal":
        指令 = str(參數.get("command", ""))
        if len(指令) > 80:
            指令 = 指令[:77] + "..."
        離開碼匹配 = re.search(r'"exit_code"\s*:\s*(-?\d+)', 文字)
        離開碼 = 離開碼匹配.group(1) if 離開碼匹配 else "?"
        return f"[terminal] ran `{指令}` -> exit {離開碼}, {行數} lines output"

    if 工具名 == "read_file":
        路徑 = 參數.get("path", "?")
        偏移 = 參數.get("offset", 1)
        return f"[read_file] read {路徑} from line {偏移} ({字元數:,} chars)"

    if 工具名 == "write_file":
        路徑 = 參數.get("path", "?")
        寫入行數 = str(參數.get("content", "")).count("\n") + 1 if 參數.get("content") else "?"
        return f"[write_file] wrote to {路徑} ({寫入行數} lines)"

    if 工具名 == "search_files":
        樣式 = 參數.get("pattern", "?")
        路徑 = 參數.get("path", ".")
        目標 = 參數.get("target", "content")
        匹配 = re.search(r'"total_count"\s*:\s*(\d+)', 文字)
        數量 = 匹配.group(1) if 匹配 else "?"
        return f"[search_files] {目標} search for '{樣式}' in {路徑} -> {數量} matches"

    if 工具名 == "patch":
        路徑 = 參數.get("path", "?")
        模式 = 參數.get("mode", "replace")
        return f"[patch] {模式} in {路徑} ({字元數:,} chars result)"

    if 工具名 == "web_search":
        查詢 = 參數.get("query", "?")
        return f"[web_search] query='{查詢}' ({字元數:,} chars result)"

    if 工具名 in {"skill_view", "skills_list", "skill_manage"}:
        名稱 = 參數.get("name", "?")
        return f"[{工具名}] name={名稱} ({字元數:,} chars)"

    if 工具名 == "memory":
        動作 = 參數.get("action", "?")
        目標 = 參數.get("target", "?")
        return f"[memory] {動作} on {目標}"

    首參數 = ""
    for 鍵, 值 in list(參數.items())[:2]:
        首參數 += f" {鍵}={str(值)[:40]}"
    return f"[{工具名}]{首參數} ({字元數:,} chars result)"


def 建立工具呼叫索引(訊息清單: list[dict[str, Any]]) -> dict[str, tuple[str, str]]:
    """建立 tool_call_id 到工具名稱與參數 JSON 的查詢表。

    參數：
        訊息清單: OpenAI-compatible messages；此函數會掃描其中 assistant 訊息的
            `tool_calls` 欄位。

    返回值：
        dict[str, tuple[str, str]]：key 是 tool_call_id，value 是
        `(tool_name, arguments_json)`。後續修剪 tool result 時會用此索引產生
        更有資訊量的一行摘要。
    """
    索引: dict[str, tuple[str, str]] = {}
    for 訊息 in 訊息清單:
        if 訊息.get("role") != "assistant":
            continue
        for 呼叫 in 訊息.get("tool_calls") or []:
            if not isinstance(呼叫, dict):
                continue
            識別碼 = str(呼叫.get("id") or "")
            函數 = 呼叫.get("function") or {}
            索引[識別碼] = (str(函數.get("name") or "unknown"), str(函數.get("arguments") or ""))
    return 索引


def 計算修剪邊界(訊息清單: list[dict[str, Any]], 保留尾端數: int, 保留尾端Token: int) -> int:
    """決定壓縮前 tool pruning 可以處理到哪個歷史索引。

    參數：
        訊息清單: 尚未壓縮的 messages。
        保留尾端數: 至少保留的近期訊息數，避免最新 tool result 被過早摘要化。
        保留尾端Token: 近期訊息的 token budget；由尾端往前累計，超出 budget
            且已滿足最少訊息數後，該索引之前的訊息可視為舊歷史。

    返回值：
        int：可修剪區間的右邊界索引。呼叫端通常會對 `range(邊界)` 內的舊
        tool result 與 tool arguments 執行 pruning。
    """
    if not 訊息清單:
        return 0
    if 保留尾端Token <= 0:
        return max(0, len(訊息清單) - 保留尾端數)

    累計 = 0
    邊界 = len(訊息清單)
    最少保留 = min(保留尾端數, len(訊息清單))
    for 索引 in range(len(訊息清單) - 1, -1, -1):
        訊息token = 估算訊息預算Token(訊息清單[索引])
        if 累計 + 訊息token > 保留尾端Token and (len(訊息清單) - 索引) >= 最少保留:
            邊界 = 索引
            break
        累計 += 訊息token
        邊界 = 索引
    預算保留數 = len(訊息清單) - 邊界
    實際保留數 = max(預算保留數, 最少保留)
    return max(0, len(訊息清單) - 實際保留數)


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
    """判斷訊息是否為壓縮摘要訊息。

    參數：
        訊息: OpenAI-compatible message dict；可能含 `_compressed_summary` metadata
            或以 Hermes summary prefix 開頭的 content。

    返回值：
        bool：True 表示此訊息是先前 context compression 產生的摘要；False 表示
        一般對話、assistant 或 tool 訊息。
    """
    return (
        訊息.get(摘要欄位) is True
        or 訊息.get(含摘要欄位) is True
        or str(訊息.get("content", "")).startswith(壓縮摘要前綴)
    )


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
        """初始化 context compressor 的門檻、保護區與摘要策略。

        參數：
            上下文長度: 模型 context window 長度；門檻會用此值乘上觸發比例後再
                套用最低上下文長度 floor。
            觸發比例: 壓縮觸發比例，預設 0.5 代表使用約一半 context window 後
                開始考慮壓縮。
            保留開頭數: 壓縮時保護的最前方訊息數，用於保留初始任務或重要設定。
            保留尾端數: 壓縮時保護的近期訊息下限；實際 tail 仍會受 token budget
                控制。
            摘要函式: 可選的 auxiliary LLM summary callable；未提供時使用
                deterministic fallback summary。
            摘要失敗是否中止: True 表示 summary callable 失敗時直接丟出錯誤；
                False 表示進入 summary failure cooldown 並改用 fallback。

        返回值：
            None。初始化後的物件會保存門檻 token、tail budget、無效壓縮次數、
            summary cooldown 與 provider usage 狀態。
        """
        self.上下文長度 = 上下文長度
        self.觸發比例 = 觸發比例
        self.保留開頭數 = 保留開頭數
        self.保留尾端數 = 保留尾端數
        self.門檻Token數 = max(int(self.上下文長度 * self.觸發比例), 最低上下文長度)
        self.尾端Token預算 = max(尾端Token最低保留, int(self.門檻Token數 * 尾端Token比例))
        self.最後提示Token數 = 0
        self.最後回應提示Token數: int | None = None
        self.摘要函式 = 摘要函式
        self.摘要失敗是否中止 = 摘要失敗是否中止
        self.無效壓縮次數 = 0
        self.壓縮次數 = 0
        self.摘要失敗冷卻到 = 0.0
        self.是否停用壓縮 = False

    def 從回應使用量更新(self, 使用量: dict[str, Any]) -> int | None:
        """從 provider response usage 正規化 prompt token 數。

        參數：
            使用量: provider adapter 回傳的 usage dict；支援
                `prompt_token_count`、`input_tokens`、`prompt_tokens` 等常見欄位。

        返回值：
            int | None：成功解析時回傳真實 prompt token 數，並更新
            `最後回應提示Token數` 與 `最後提示Token數`；缺欄位或無法轉成 int 時
            回傳 None，呼叫端應改用 rough estimate preflight/fallback。
        """
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
        """判斷目前 token 數是否已達壓縮門檻。

        參數：
            token數: 用於判斷的 prompt token 數；post-response 路徑應傳入 provider
                真實 usage，preflight 路徑則可傳入 rough estimate。

        返回值：
            bool：True 表示壓縮未被停用且 token 數大於等於門檻；False 表示尚未
            達門檻或 anti-thrashing 已停用此 compressor instance。
        """
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
        """壓縮 canonical messages 並產生 head + summary + tail 結果。

        參數：
            訊息清單: 待壓縮的 OpenAI-compatible messages；不應包含已分離的
                stable system prompt。
            系統提示詞: request 邊界才 prepend 的 system prompt；只用於 before/after
                rough token 估算，不會寫入壓縮後 transcript。
            工具清單: 本次 request 可用 tool schemas；用於估算 request token 成本。
            provider提示Token數: provider 回傳的真實 prompt token 數；若提供，會
                優先用它判斷是否達門檻。
            強制: True 時忽略門檻與 cooldown，通常用於 context overflow recovery。

        返回值：
            壓縮結果：包含壓縮後訊息、是否真的壓縮、壓縮前後 rough token 估算、
            原因與 anti-thrashing 是否停用。壓縮成功時結果已經過 summary
            normalization、tool-pair sanitize 與相鄰角色處理。
        """
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
            既有摘要 = "\n\n".join(self.清理摘要前綴(str(訊息.get("content", ""))) for 訊息 in [*開頭, *中段] if 是否摘要訊息(訊息))
            摘要文字 = self.建立Fallback摘要(中段, 既有摘要)

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
        """依 token budget 與硬性近期訊息下限決定 tail 起點。

        參數：
            訊息清單: 已先做 tool-result pruning 的 messages。
            開頭結束: head 保護區的結束索引；tail 起點不會早於此索引。

        返回值：
            int：tail 第一則訊息的索引。此索引之後的訊息會原文保留，索引之前且
            不在 head 的訊息會被送入 summary。
        """
        可用尾端Token = self.尾端Token預算
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
        """在摘要前修剪舊 tool material 以降低壓縮成本。

        參數：
            訊息清單: 尚未壓縮的完整 messages；可包含 assistant tool_calls 與 tool
                results。

        返回值：
            list[dict[str, Any]]：複製並修剪後的 messages。修剪規則包含：由新到舊
            dedupe 重複大型 tool output、把舊大型 tool result 改為工具類型一行摘要、
            以及在 JSON 有效前提下截斷舊 assistant tool_call arguments。
        """
        if not 訊息清單:
            return 訊息清單

        結果 = [dict(訊息) for 訊息 in 訊息清單]
        呼叫索引 = 建立工具呼叫索引(結果)
        修剪邊界 = 計算修剪邊界(結果, self.保留尾端數, self.尾端Token預算)

        # Pass 1: 由新到舊 dedupe 相同內容，保留最新完整版。
        內容雜湊: set[str] = set()
        for 索引 in range(len(結果) - 1, -1, -1):
            訊息 = 結果[索引]
            if 訊息.get("role") != "tool":
                continue
            內容 = 訊息.get("content")
            if not isinstance(內容, str) or len(內容) < 工具結果修剪字元門檻:
                continue
            雜湊 = hashlib.md5(內容.encode("utf-8", errors="replace")).hexdigest()[:12]
            if 雜湊 in 內容雜湊:
                結果[索引] = {**訊息, "content": 重複工具輸出標記}
            else:
                內容雜湊.add(雜湊)

        # Pass 2: 修剪邊界外的舊 tool result，改為依工具類型的一行摘要。
        for 索引 in range(修剪邊界):
            訊息 = 結果[索引]
            if 訊息.get("role") != "tool":
                continue
            內容 = 訊息.get("content")
            if not isinstance(內容, str):
                continue
            if not 內容 or 內容 == 舊工具結果佔位文字 or 內容.startswith("[Duplicate tool output"):
                continue
            if len(內容) <= 工具結果修剪字元門檻:
                continue
            識別碼 = str(訊息.get("tool_call_id") or "")
            工具名, 參數json = 呼叫索引.get(識別碼, (str(訊息.get("name") or "unknown"), ""))
            結果[索引] = {**訊息, "content": 摘要工具結果一行(工具名, 參數json, 內容)}

        # Pass 3: 修剪邊界外的 assistant tool_call arguments（JSON-safe）。
        for 索引 in range(修剪邊界):
            訊息 = 結果[索引]
            if 訊息.get("role") != "assistant" or not 訊息.get("tool_calls"):
                continue
            新呼叫清單: list[dict[str, Any]] = []
            有修改 = False
            for 呼叫 in 訊息.get("tool_calls") or []:
                if not isinstance(呼叫, dict):
                    新呼叫清單.append(呼叫)
                    continue
                新呼叫 = dict(呼叫)
                函數 = dict(新呼叫.get("function") or {})
                參數 = str(函數.get("arguments") or "")
                if len(參數) > 工具參數修剪字元門檻:
                    新參數 = 截斷工具呼叫參數json(參數)
                    if 新參數 != 參數:
                        函數["arguments"] = 新參數
                        新呼叫["function"] = 函數
                        有修改 = True
                新呼叫清單.append(新呼叫)
            if 有修改:
                結果[索引] = {**訊息, "tool_calls": 新呼叫清單}

        return 結果

    def 建立摘要(self, 開頭: list[dict[str, Any]], 中段: list[dict[str, Any]]) -> str:
        """建立 reference-only context compaction summary。

        參數：
            開頭: 壓縮後仍會保留的 head messages；此處只用來尋找既有 summary。
            中段: 本次要摘要的 historical messages，可能包含既有 summary 與新舊
                歷史 turns。

        返回值：
            str：已套用目前 Hermes-style summary prefix 與結束標記的摘要文字。
            若設定 auxiliary LLM，會先呼叫 `摘要函式`；未設定時使用 fallback。
        """
        既有摘要 = "\n\n".join(self.清理摘要前綴(str(訊息.get("content", ""))) for 訊息 in [*開頭, *中段] if 是否摘要訊息(訊息))
        目標Token = min(12000, max(2000, 粗估訊息Token數(中段) // 5))
        摘要輸入 = self.建立摘要輸入(中段, 既有摘要, 目標Token)
        if self.摘要函式:
            文字 = self.摘要函式(摘要輸入, 目標Token).strip()
        else:
            文字 = self.建立Fallback摘要(中段, 既有摘要)
        return self.正規化摘要文字(文字)

    def 建立摘要輸入(self, 訊息清單: list[dict[str, Any]], 既有摘要: str = "", 目標Token: int = 2000) -> str:
        """建立 auxiliary LLM summary prompt。

        參數：
            訊息清單: 新增的 historical messages，會被序列化為 summary source。
            既有摘要: 先前 summary 的本文；非空時 prompt 會要求 iterative update。
            目標Token: summary 目標 token 數，用於提示模型控制摘要長度。

        返回值：
            str：完整 structured summary prompt；由 `輔助壓縮摘要.建立壓縮摘要Prompt`
            產生，包含固定章節、時間錨定與敏感資訊遮罩後的訊息材料。
        """
        return 建立壓縮摘要Prompt(訊息清單, 既有摘要, 目標Token)

    def 建立Fallback摘要(self, 訊息清單: list[dict[str, Any]], 既有摘要: str = "") -> str:
        """建立 summary model 不可用時的 deterministic structured fallback。

        參數：
            訊息清單: 要壓縮的 historical messages；函數會取近期片段並萃取檔案、
                決策、待辦等線索。
            既有摘要: 先前 summary 本文；若提供會以「Previous summary retained」
                形式納入，避免多次壓縮遺失歷史狀態。

        返回值：
            str：未加 prefix 的 structured summary body。呼叫端會再透過
            `正規化摘要文字` 補上 reference-only prefix 與結束標記。
        """
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
        """正規化 summary 文字並補上目前 reference-only 邊界。

        參數：
            摘要文字: auxiliary LLM 或 fallback summarizer 產生的 summary body；
                可能已經包含舊版 summary prefix 或結束標記。

        返回值：
            str：包含目前 `壓縮摘要前綴`、必要 Historical Task Snapshot 標題與
            `摘要結束標記` 的完整 summary 文字，可直接放入 summary message。
        """
        內容 = self.清理摘要前綴(摘要文字).strip()
        if "## Historical Task Snapshot" not in 內容:
            內容 = "## Historical Task Snapshot\n" + 內容
        return f"{壓縮摘要前綴}\n\n{內容}\n{摘要結束標記}"

    def 清理摘要前綴(self, 文字: str) -> str:
        """移除既有 summary prefix 與結束標記。

        參數：
            文字: 可能來自舊 summary message 的原始文字。

        返回值：
            str：去除目前 summary prefix 與結束標記後的 summary body。再次壓縮時
            用此值避免 prefix 反覆疊加與 stale directive 殘留。
        """
        結果 = 文字.replace(壓縮摘要前綴, "").replace(摘要結束標記, "")
        return 結果.strip()

    def 建立摘要訊息(self, 開頭: list[dict[str, Any]], 尾端: list[dict[str, Any]], 摘要文字: str) -> dict[str, Any]:
        """依 head/tail 鄰近角色建立 provider-safe summary message。

        參數：
            開頭: 壓縮後會放在 summary 前方的 messages；用最後一則角色判斷是否
                會造成相鄰同角色。
            尾端: 壓縮後會放在 summary 後方的 messages；用第一則角色判斷是否
                需要把 summary 設成 user role。
            摘要文字: 已正規化的 reference-only summary 文字。

        返回值：
            dict[str, Any]：包含 `role`、`content` 與 `_compressed_summary=True` 的
            summary message。role 會在 user/assistant 之間選擇以降低嚴格 provider
            拒絕相鄰同角色序列的機率。
        """
        前角色 = 開頭[-1].get("role") if 開頭 else None
        後角色 = 尾端[0].get("role") if 尾端 else None
        角色 = "assistant"
        if 前角色 == "assistant" and 後角色 != "user":
            角色 = "user"
        elif 後角色 == "assistant" and 前角色 != "user":
            角色 = "user"
        return {"role": 角色, "content": 摘要文字, 摘要欄位: True}

    def 合併相鄰同角色摘要(self, 訊息清單: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """合併 summary 造成的相鄰同角色訊息。

        參數：
            訊息清單: 初步組成的 head + summary + tail messages，可能存在 summary
                與前後 user/assistant 同 role 的情況。

        返回值：
            list[dict[str, Any]]：合併後的 messages。若相鄰兩則都是 summary，會合併
            成一則；若 summary 與 tail 同 role，會把 summary 併入 tail 並標記
            `_contains_compressed_summary`，以避免 UI 誤把真實 tail 當成可隱藏摘要。
        """
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
                合併.pop(摘要欄位, None)
                合併[含摘要欄位] = True
                結果[-1] = 合併
                continue
            結果.append(訊息)
        return 結果

    def 向後對齊邊界(self, 訊息清單: list[dict[str, Any]], 邊界: int) -> int:
        """把 head 邊界往後移動到不切斷 tool pair 的位置。

        參數：
            訊息清單: 待切分的 messages。
            邊界: 初步 head 結束索引。

        返回值：
            int：對齊後的 head 結束索引。若原邊界落在 assistant tool_calls 後或
            tool result 前，會往後移動，避免 head 保留 call 但中段吃掉 result。
        """
        while 邊界 < len(訊息清單) and 邊界 > 0:
            前訊息 = 訊息清單[邊界 - 1]
            目前 = 訊息清單[邊界]
            if 前訊息.get("tool_calls") or 目前.get("role") == "tool":
                邊界 += 1
                continue
            break
        return min(邊界, len(訊息清單))

    def 向前對齊邊界(self, 訊息清單: list[dict[str, Any]], 邊界: int) -> int:
        """把 tail 邊界往前移動到不產生 orphan tool result 的位置。

        參數：
            訊息清單: 待切分的 messages。
            邊界: 初步 tail 起點索引。

        返回值：
            int：對齊後的 tail 起點索引。若 tail 會從 tool result 開始，或會把
            assistant tool_calls 與其 result 分到不同區塊，會往前移動邊界。
        """
        while 邊界 < len(訊息清單) and 邊界 > 0:
            目前 = 訊息清單[邊界]
            前訊息 = 訊息清單[邊界 - 1]
            if 目前.get("role") == "tool" or 前訊息.get("tool_calls"):
                邊界 -= 1
                continue
            break
        return max(0, 邊界)

    def 清理工具配對(self, 訊息清單: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """移除 orphan 或順序不合法的 tool call/result 配對。

        參數：
            訊息清單: 壓縮後準備送往 provider 的 messages；可能因 head/middle/tail
                切分而遺失部分 tool call 或 tool result，或中間插入 user/assistant。

        返回值：
            list[dict[str, Any]]：只保留 `assistant tool_calls` 後緊接對應
            `tool result` 的合法配對；若中間被其他訊息隔開，整組 broken pair
            會一併移除，只保留非 tool 訊息。
        """
        有效識別碼: set[str] = set()
        索引 = 0
        while 索引 < len(訊息清單):
            訊息 = 訊息清單[索引]
            呼叫清單 = 訊息.get("tool_calls") or []
            if not 呼叫清單:
                索引 += 1
                continue
            預期 = {str(呼叫.get("id")) for 呼叫 in 呼叫清單 if isinstance(呼叫, dict) and 呼叫.get("id")}
            if not 預期:
                索引 += 1
                continue
            游標 = 索引 + 1
            收到: set[str] = set()
            while 游標 < len(訊息清單) and 訊息清單[游標].get("role") == "tool":
                識別碼 = str(訊息清單[游標].get("tool_call_id") or "")
                if 識別碼 not in 預期 or 識別碼 in 收到:
                    break
                收到.add(識別碼)
                游標 += 1
            if 收到 == 預期:
                有效識別碼.update(收到)
            索引 += 1

        清單: list[dict[str, Any]] = []
        for 訊息 in 訊息清單:
            if 訊息.get("role") == "tool":
                if str(訊息.get("tool_call_id") or "") not in 有效識別碼:
                    continue
                清單.append(訊息)
                continue
            if 訊息.get("tool_calls"):
                新訊息 = dict(訊息)
                新呼叫 = [呼叫 for 呼叫 in 訊息.get("tool_calls", []) if str(呼叫.get("id")) in 有效識別碼]
                if 新呼叫:
                    新訊息["tool_calls"] = 新呼叫
                    清單.append(新訊息)
                elif 新訊息.get("content"):
                    新訊息.pop("tool_calls", None)
                    清單.append(新訊息)
                continue
            清單.append(訊息)
        return 清單
