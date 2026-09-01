"""釐清詢問工具（clarify）。

功能：
    提供 `clarify` 的 handler。模型遇到有意義的分歧、需要使用者拍板時呼叫它：
    工具會當場把問題送到使用者面前、**阻塞等待**回答，拿到答案後以 tool result
    回給模型，讓模型在同一個 turn 內接著做事，而不是把問題當最終回答結案。

策略分離：
    這個模組只負責「發問」與「把結果整理成模型看得懂的形狀」；至於**怎麼問、
    等多久**，完全由執行平台注入的詢問回呼決定（Strategy Pattern）。

        CLI 互動 REPL   → 人就在鍵盤前，無限等待，使用者可自行略過
        一次性 / API    → 沒有回呼可注入，立即回「沒人可問」指引，不空等
        之後的 gateway  → 送出按鈕後等 Event，逾時（例如 10 分鐘）避免死鎖

    因此新增平台時只要實作回呼，本檔不需要修改。

回呼協定：
    `回呼(問題: str, 選項清單: list[str] | None) -> str`

    回傳字串即為使用者的回答。平台若已詢問但沒等到回答，應丟出 `詢問未回應`
    並標明原因（逾時或使用者略過）；本檔會轉成對應的指引字串。

沒有回答時的處理：
    一律回「成功」的 tool result，只是把 answer 換成一句指引，要模型自行判斷並
    繼續。刻意不丟錯：`工具結果` 契約層會把 handler 的錯誤訊息換成固定文字，丟
    錯的話模型只會收到不帶原因的「工具執行失敗」，反而只能瞎猜。
"""

from __future__ import annotations

import logging
from typing import Any, Callable

logger = logging.getLogger(__name__)

詢問回呼型別 = Callable[[str, "list[str] | None"], str]

最大選項數 = 4
最大問題長度 = 2000
最大選項長度 = 200

逾時原因 = "timeout"
略過原因 = "skipped"
無互動通道原因 = "no_interactive_channel"
缺少問題原因 = "missing_question"
回呼失敗原因 = "callback_failed"
可用原因 = (逾時原因, 略過原因, 無互動通道原因)

無互動通道指引 = (
    "[oneshot mode: no user available. Make the most reasonable assumption "
    "you can and continue.]"
)
無互動通道選項指引 = (
    "[oneshot mode: no user available. Pick the best option from {選項} "
    "using your own judgment and continue.]"
)
逾時指引 = (
    "The user did not provide a response within the time limit. "
    "Use your best judgement to make the choice and proceed."
)
略過指引 = (
    "The user skipped this question. "
    "Use your best judgement to make the choice and proceed."
)
缺少問題指引 = (
    "clarify requires a non-empty `question`. Re-issue the call with the "
    "question you want to ask, or proceed on your own judgement."
)
回呼失敗指引 = (
    "The question could not be delivered to the user because the interface "
    "failed. Use your best judgement to make the choice and proceed."
)


class 詢問未回應(Exception):
    """平台已把問題送出，但沒有取得使用者回答。

    參數：
        原因: 逾時原因、略過原因 或 無互動通道原因。
    """

    def __init__(self, 原因: str = 逾時原因) -> None:
        """記錄未回應原因，供 handler 選出對應指引。"""
        super().__init__(原因)
        self.原因 = 原因 if 原因 in 可用原因 else 逾時原因


def 一次性詢問策略(問題: str, 選項清單: list[str] | None) -> str:
    """一次性執行（oneshot）的詢問策略：確定沒有使用者，直接放棄等待。

    這是平台策略之一，與 CLI 的「無限等待」、gateway 的「長逾時」平行。明確注入
    它比「不注入回呼」更好：新平台若忘了接上互動介面，能從沒有策略這件事看出是
    設定漏了，而不是被誤當成 oneshot。

    參數：
        問題: 本來要問使用者的問題；此策略不使用。
        選項清單: 本來要提供的選項；此策略不使用。
    返回值：不會正常返回，一律丟出 `詢問未回應(無互動通道原因)`。
    """
    del 問題, 選項清單
    raise 詢問未回應(無互動通道原因)


def 正規化問題(參數: dict[str, Any]) -> str:
    """取出並修剪要問使用者的問題。

    參數：
        參數: 工具呼叫參數。
    返回值：str，修剪後的問題；過長時截斷。問題為空時回傳空字串，由呼叫端回報
        —— 刻意不丟錯，因為 `工具結果` 契約層會把錯誤訊息換成固定文字，模型只
        會收到不帶原因的失敗，無從知道自己漏了 question。
    """
    return str(參數.get("question") or "").strip()[:最大問題長度]


def 正規化選項(原始選項: Any, 警告清單: list[str]) -> list[str] | None:
    """把模型提供的 choices 整理成可顯示的選項清單。

    採「修正並回報」而非拒絕：選項只是輔助，格式有瑕疵時修掉並記警告，比讓整
    次詢問失敗實用。

    參數：
        原始選項: 工具參數中的 choices；None 代表開放式問答。
        警告清單: 就地累積的警告。
    返回值：選項清單；無可用選項時回傳 None，代表改成開放式問答。
    """
    if 原始選項 is None:
        return None
    if type(原始選項) is not list:
        警告清單.append("choices 不是陣列，已當作開放式問題處理")
        return None
    選項清單: list[str] = []
    for 原始項目 in 原始選項:
        選項 = str(原始項目 or "").strip()
        if not 選項:
            continue
        if len(選項) > 最大選項長度:
            警告清單.append(f"選項「{選項[:20]}…」超過 {最大選項長度} 字元，已截斷")
            選項 = 選項[:最大選項長度]
        選項清單.append(選項)
    if not 選項清單:
        警告清單.append("choices 沒有可用選項，已當作開放式問題處理")
        return None
    if len(選項清單) > 最大選項數:
        警告清單.append(f"choices 最多 {最大選項數} 個，只保留前 {最大選項數} 個")
        選項清單 = 選項清單[:最大選項數]
    return 選項清單


未詢問原因 = (無互動通道原因, 缺少問題原因)
無回應指引對照 = {
    逾時原因: 逾時指引,
    略過原因: 略過指引,
    缺少問題原因: 缺少問題指引,
    回呼失敗原因: 回呼失敗指引,
}


def 建立無回應回傳(
    原因: str,
    問題: str,
    選項清單: list[str] | None,
    警告清單: list[str],
) -> dict[str, Any]:
    """組出「沒拿到回答」時的結果。

    參數：
        原因: 無互動通道原因、缺少問題原因、逾時原因、略過原因或回呼失敗原因。
        問題: 本次要問的問題，原樣帶回讓模型知道自己問了什麼。
        選項清單: 已正規化的選項，用於組出帶選項的指引。
        警告清單: 本次自動修正的說明。
    返回值：dict。answer 放的是要模型自行判斷的指引，形狀與有回答時一致。
    """
    if 原因 == 無互動通道原因:
        指引 = 無互動通道選項指引.format(選項=選項清單) if 選項清單 else 無互動通道指引
    else:
        指引 = 無回應指引對照.get(原因, 逾時指引)
    結果: dict[str, Any] = {
        "asked": 原因 not in 未詢問原因,
        "answered": False,
        "reason": 原因,
        "question": 問題,
        "choices_offered": 選項清單,
        "answer": 指引,
        "choice_index": None,
    }
    if 警告清單:
        結果["warnings"] = 警告清單
    return 結果


def 建立回答回傳(
    回答: str,
    問題: str,
    選項清單: list[str] | None,
    警告清單: list[str],
) -> dict[str, Any]:
    """組出使用者實際回答時的結果。

    參數：
        回答: 平台回呼取得的回答。
        問題: 本次問出去的問題，原樣帶回；壓縮後可能只剩 tool result，帶著才知
            道這個答案是在回什麼。
        選項清單: 已正規化的選項，用於回推使用者選了第幾個。
        警告清單: 本次自動修正的說明。
    返回值：dict。回答命中某個選項時附上 choice_index；自由輸入時為 None。
    """
    答案 = str(回答 or "").strip()
    位置 = 選項清單.index(答案) if 選項清單 and 答案 in 選項清單 else None
    結果: dict[str, Any] = {
        "asked": True,
        "answered": True,
        "question": 問題,
        "choices_offered": 選項清單,
        "answer": 答案,
        "choice_index": 位置,
    }
    if 警告清單:
        結果["warnings"] = 警告清單
    return 結果


def 建立釐清處理器(詢問回呼: 詢問回呼型別 | None = None) -> Callable[[dict[str, Any]], dict[str, Any]]:
    """把平台的詢問策略綁進 `clarify` handler。

    回呼以 closure 綁定而非放進工具參數：`工具登錄器.呼叫工具` 會對參數做 JSON
    深拷貝，函式物件過不去。

    參數：
        詢問回呼: 平台注入的詢問策略。平台應一律明確注入，一次性執行請注入
            `一次性詢問策略`。None 只是安全網：會記一筆 warning 並比照一次性處
            理，讓漏接互動介面的平台不至於整個 clarify 壞掉。
    返回值：可登錄的 handler。
    """

    def 釐清詢問(參數: dict[str, Any]) -> dict[str, Any]:
        """執行 `clarify`：把問題送給使用者並等待回答。

        參數：
            參數: 工具呼叫參數。question 必填；choices 最多四個，省略即為開放式
                問答。
        返回值：dict，永遠包含 answer 欄位——可能是使用者的回答，也可能是要模型
            自行判斷的指引。一律不丟錯：`工具結果` 契約層會把 handler 的錯誤訊息
            換成固定文字，丟錯只會讓模型收到不帶原因的失敗。
        """
        警告清單: list[str] = []
        問題 = 正規化問題(參數)
        選項清單 = 正規化選項(參數.get("choices"), 警告清單)
        if not 問題:
            return 建立無回應回傳(缺少問題原因, 問題, 選項清單, 警告清單)
        if 詢問回呼 is None:
            logger.warning("clarify 沒有注入詢問策略，比照一次性模式處理；平台應明確注入回呼。")
            return 建立無回應回傳(無互動通道原因, 問題, 選項清單, 警告清單)
        try:
            回答 = 詢問回呼(問題, 選項清單)
        except 詢問未回應 as 未回應:
            return 建立無回應回傳(未回應.原因, 問題, 選項清單, 警告清單)
        except Exception as 錯誤:
            # 平台實作出包不該讓模型收到不透明失敗；記下細節，回可讀指引讓它繼續。
            logger.warning("clarify 詢問策略執行失敗：%s", 錯誤, exc_info=True)
            return 建立無回應回傳(回呼失敗原因, 問題, 選項清單, 警告清單)
        return 建立回答回傳(回答, 問題, 選項清單, 警告清單)

    return 釐清詢問
