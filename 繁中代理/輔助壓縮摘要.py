"""Hermes-style auxiliary compression summarization。

功能：
    為 ContextCompressor 提供 auxiliary LLM 摘要主路徑：組裝 structured summary
    prompt、解析 compression 模型設定，並透過 provider adapter 產生摘要。
    預設 auto 行為為重用主模型；可透過環境變數指定獨立 compression 模型。
"""

from __future__ import annotations

import json
import os
import re
from datetime import date
from typing import Any, Callable

from .提示詞常數 import 壓縮摘要前綴
from .模型供應商 import 模型供應商

摘要函式型別 = Callable[[str, int], str]

訊息內容上限 = 6000
既有摘要上限 = 8000

敏感樣式清單 = (
    re.compile(r"sk-[A-Za-z0-9_-]{10,}"),
    re.compile(r"AIza[0-9A-Za-z_-]{20,}"),
    re.compile(r"(?i)(api[_-]?key|token|password|secret)\s*[:=]\s*\S+"),
)


def 是否啟用壓縮摘要() -> bool:
    """判斷目前設定是否啟用 auxiliary LLM 壓縮摘要。

    參數：
        無。此函數只讀取環境變數 `AIAGENT_COMPRESSION_LLM`。

    返回值：
        bool：True 表示壓縮時會優先呼叫 auxiliary LLM 產生 structured
        summary；False 表示跳過 LLM 摘要主路徑，改用 deterministic fallback。
    """
    值 = os.getenv("AIAGENT_COMPRESSION_LLM", "1").strip().lower()
    return 值 not in {"0", "false", "no", "off", "disable", "disabled"}


def 解析摘要失敗是否中止() -> bool:
    """解析摘要模型失敗時是否中止壓縮。

    參數：
        無。此函數只讀取環境變數 `AIAGENT_ABORT_ON_SUMMARY_FAILURE`。

    返回值：
        bool：True 表示 auxiliary summary 失敗時直接丟出錯誤並停止壓縮；
        False 表示記錄失敗冷卻後改用 fallback summary。語意對應 Hermes 的
        `compression.abort_on_summary_failure`。
    """
    值 = os.getenv("AIAGENT_ABORT_ON_SUMMARY_FAILURE", "").strip().lower()
    return 值 in {"1", "true", "yes", "on"}


def 解析壓縮模型設定(
    主模式: str,
    主模型: str,
    壓縮模式: str | None = None,
    壓縮模型: str | None = None,
) -> tuple[str, str]:
    """解析 auxiliary compression 使用的 provider 模式與模型名稱。

    參數：
        主模式: 主模型 provider 模式，例如 `fake` 或 `gemini`。
        主模型: 主模型名稱，當 compression 模型未指定時會重用此值。
        壓縮模式: 可選的 compression provider 模式；None 代表讀取環境變數或
            fallback 到主模式。
        壓縮模型: 可選的 compression 模型名稱；None 代表讀取環境變數或
            fallback 到主模型。

    返回值：
        tuple[str, str]：第一個元素是解析後的 compression provider 模式，第二個
        元素是解析後的 compression 模型名稱。空值代表 auto 重用主模型設定。
    """
    模式 = (壓縮模式 or os.getenv("AIAGENT_COMPRESSION_MODE") or 主模式).strip()
    模型 = (壓縮模型 or os.getenv("AIAGENT_COMPRESSION_MODEL") or 主模型).strip()
    return 模式, 模型


def 遮罩敏感文字(文字: str) -> str:
    """遮罩摘要輸入或輸出中的敏感資訊。

    參數：
        文字: 可能包含 API key、token、password、secret 等敏感資訊的原始文字。

    返回值：
        str：套用敏感樣式替換後的文字；命中的敏感片段會被 `[REDACTED]` 取代，
        以降低摘要 prompt 與壓縮摘要持久化時洩漏憑證的風險。
    """
    結果 = 文字
    for 樣式 in 敏感樣式清單:
        結果 = 樣式.sub("[REDACTED]", 結果)
    return 結果


def _是否摘要訊息(訊息: dict[str, Any]) -> bool:
    """判斷單則訊息是否是既有 compression summary。

    參數：
        訊息: OpenAI-compatible message dict；可能包含 `_compressed_summary`
            metadata 或以 Hermes summary prefix 開頭的 content。

    返回值：
        bool：True 表示此訊息應被視為舊壓縮摘要，在再次壓縮時應當作既有摘要
        更新，而不是一般對話 turn；False 表示一般訊息。
    """
    return (
        訊息.get("_compressed_summary") is True
        or 訊息.get("_contains_compressed_summary") is True
        or str(訊息.get("content", "")).startswith(壓縮摘要前綴)
    )


def 序列化訊息供摘要(訊息: dict[str, Any]) -> str:
    """把單則 canonical message 轉成摘要模型可讀的標籤文字。

    參數：
        訊息: OpenAI-compatible message dict，可能是 user、assistant、tool，
            也可能包含 assistant tool_calls 或壓縮摘要 metadata。

    返回值：
        str：帶有角色標籤、工具名稱或 prior-summary 標記的文字片段。內容會依
        `訊息內容上限` 截斷並套用敏感資訊遮罩，供 structured summary prompt 使用。
    """
    角色 = 訊息.get("role", "unknown")
    內容 = 訊息.get("content", "")
    if _是否摘要訊息(訊息) and isinstance(內容, str):
        內容 = 內容[:訊息內容上限]
        return f"[{角色}] (prior compaction summary)\n{遮罩敏感文字(內容)}"
    if 角色 == "tool":
        名稱 = 訊息.get("name") or "tool"
        文字 = 內容 if isinstance(內容, str) else json.dumps(內容, ensure_ascii=False)
        if len(文字) > 訊息內容上限:
            文字 = 文字[:訊息內容上限] + "\n...[truncated]"
        return f"[tool:{名稱}]\n{遮罩敏感文字(文字)}"
    if 角色 == "assistant" and 訊息.get("tool_calls"):
        片段: list[str] = []
        if 內容:
            片段.append(str(內容))
        for 呼叫 in 訊息.get("tool_calls") or []:
            函數 = (呼叫 or {}).get("function") or {}
            片段.append(f"tool_call {函數.get('name', '?')} args={str(函數.get('arguments', ''))[:1200]}")
        文字 = "\n".join(片段)
        if len(文字) > 訊息內容上限:
            文字 = 文字[:訊息內容上限] + "\n...[truncated]"
        return f"[assistant]\n{遮罩敏感文字(文字)}"
    文字 = 內容 if isinstance(內容, str) else json.dumps(內容, ensure_ascii=False)
    if len(文字) > 訊息內容上限:
        文字 = 文字[:訊息內容上限] + "\n...[truncated]"
    return f"[{角色}]\n{遮罩敏感文字(文字)}"


def 建立壓縮摘要Prompt(訊息清單: list[dict[str, Any]], 既有摘要: str, 目標Token: int) -> str:
    """建立給 auxiliary LLM 的 Hermes-style structured summary prompt。

    參數：
        訊息清單: 本次要摘要的歷史 messages；呼叫端應先排除 active tail，避免
            summary 重新包裝最新使用者任務。
        既有摘要: 先前壓縮產生的 summary 內容；若非空，prompt 會要求模型做
            iterative update，而不是從零產生摘要。
        目標Token: 希望 summary 大致控制的 token 目標，用於提示摘要模型調整
            詳細程度；此值不是硬限制。

    返回值：
        str：完整 summary prompt，包含 summarizer 角色說明、時間錨定、固定章節
        模板、既有摘要與新歷史訊息。回傳內容會直接送給 auxiliary provider。
    """
    前導 = (
        "You are a summarization agent creating a context checkpoint. "
        "Treat the conversation turns below as source material for a compact record of prior work. "
        "Produce only the structured summary; do not add a greeting, preamble, or prefix. "
        "Write the summary in the same language the user was using in the conversation. "
        "NEVER include API keys, tokens, passwords, secrets, credentials, or connection strings — use [REDACTED]."
    )
    今天 = date.today().isoformat()
    時間錨定 = (
        f"\nTEMPORAL ANCHORING: The current date is {今天}. Phrase completed actions as dated past-tense facts.\n"
    )
    模板 = f"""
## Historical Task Snapshot
[User's most recent unfulfilled input verbatim, or None if fully resolved]

## Goal
[What the user is trying to accomplish overall]

## Completed Actions
[Numbered list: action — outcome [tool: name]]

## Historical In-Progress State
[Work underway when compaction fired]

## Blocked
[Any unresolved blockers or exact error messages]

## Key Decisions
[Important technical decisions and why]

## Resolved Questions
[Questions already answered]

## Historical Pending User Asks
[Stale unanswered asks for reference only; write None if none]

## Relevant Files
[Files read, modified, or created]

## Historical Remaining Work
[Stale remaining work for reference only]

## Critical Context
[Specific values, configs, or data that must survive compaction]

Target ~{目標Token} tokens. Be concrete with paths, commands, outputs, and line numbers.
{時間錨定}
Write only the summary body. Do not include any preamble or prefix.""".strip()

    序列化 = "\n\n".join(序列化訊息供摘要(訊息) for 訊息 in 訊息清單)
    if 既有摘要:
        清理摘要 = 遮罩敏感文字(既有摘要[:既有摘要上限])
        return (
            f"{前導}\n\n"
            "You are updating a context compaction summary. Preserve still-relevant information from the previous summary and incorporate the new turns.\n\n"
            f"PREVIOUS SUMMARY:\n{清理摘要}\n\n"
            f"NEW TURNS TO INCORPORATE:\n{序列化}\n\n"
            f"Use this exact structure:\n\n{模板}"
        )
    return (
        f"{前導}\n\n"
        "Create a structured checkpoint summary for the conversation after earlier turns are compacted.\n\n"
        f"TURNS TO SUMMARIZE:\n{序列化}\n\n"
        f"Use this exact structure:\n\n{模板}"
    )


def 建立壓縮摘要函式(供應商: 模型供應商) -> 摘要函式型別:
    """建立可注入上下文壓縮器的 auxiliary LLM 摘要函式。

    參數：
        供應商: 實作 `產生回應(messages, tools)` 的模型供應商；可以是主模型
            provider，也可以是獨立 compression provider。

    返回值：
        摘要函式型別：可接受 `(摘要輸入, 目標Token)` 並回傳 summary 文字的
        callable。此 callable 會呼叫 provider、取回文字、遮罩敏感資訊，並在模型
        未回傳文字時丟出 RuntimeError 讓壓縮器進入 fallback 或 abort 流程。
    """

    def 摘要函式(摘要輸入: str, 目標Token: int) -> str:
        """呼叫 auxiliary provider 產生單次壓縮摘要。

        參數：
            摘要輸入: 已組裝完成的 structured summary prompt。
            目標Token: 摘要目標長度；目前由外層 prompt 反映，此函式不直接傳入
                provider config，因此以 `del` 明確標記不直接使用。

        返回值：
            str：provider 回傳並經過敏感資訊遮罩的 summary 文字。若 provider
            沒有回傳有效文字會丟出 RuntimeError。
        """
        del 目標Token
        回應 = 供應商.產生回應([{"role": "user", "content": 摘要輸入}], [])
        文字 = 遮罩敏感文字((回應.文字 or "").strip())
        if not 文字:
            raise RuntimeError("壓縮摘要模型未回傳內容")
        return 文字

    return 摘要函式
