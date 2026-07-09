"""Agent runtime 與 tool loop。

功能：
    實作 Hermes-style 單 turn 執行流程：system prompt 與 persisted transcript 分離、
    user turn 早期持久化、preflight/provider-usage/tool-loop 後壓縮、壓縮成功後做
    Session Split，並在 context overflow 類錯誤發生時壓縮後 retry。
"""

from __future__ import annotations

import json
import logging
from contextlib import nullcontext
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .上下文壓縮器 import 上下文壓縮器
from .工作階段上下文 import 設定目前工作階段識別碼, 設定目前工作階段資料庫路徑, 設定目前使用者
from .工作階段庫 import 工作階段庫
from .工具 import 工具登錄器
from .工具註冊 import 建立預設工具登錄器
from .基本工具 import 取得技能根目錄清單
from .技能索引器 import 建立技能摘要 as 建立技能索引摘要, 技能強制載入指引, 技能無相關項目指引
from .提示詞組裝器 import 提示詞設定, 提示詞組裝器
from .模型供應商 import 建立模型供應商, 模型供應商
from .使用者 import 使用者上下文, 使用者庫, 取得預設記憶根目錄, 建立預設使用者上下文
from .輔助壓縮摘要 import 建立壓縮摘要函式, 是否啟用壓縮摘要, 解析壓縮模型設定, 解析摘要失敗是否中止

_logger = logging.getLogger(__name__)


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
        模型模式: str = "gemini",
        啟用壓縮摘要: bool | None = None,
        摘要失敗是否中止: bool | None = None,
        壓縮模式: str | None = None,
        壓縮模型: str | None = None,
        user_id: str | None = None,
        使用者上下文物件: 使用者上下文 | None = None,
        source: str = "cli",
        model_config: dict[str, Any] | None = None,
        事件回呼: Any | None = None,
        記憶管理器: Any | None = None,
        上下文引擎: Any | None = None,
    ) -> None:
        """初始化 Hermes-style CLI runtime 與 context compression 設定。

        參數：
            工作階段庫物件: SQLite session store，負責保存 sessions、messages 與
                compression locks。
            模型供應商物件: 主模型 provider adapter，負責產生 assistant 回應與
                tool_calls。
            模型名稱: 主模型名稱，會寫入 system prompt 與 compression auto 設定。
            供應商名稱: 顯示在 prompt 中的 provider 名稱，例如 `fake` 或
                `gemini-adc`。
            工具登錄器物件: 可選的工具登錄器；None 時會建立預設 Hermes-like 工具。
            工作目錄: runtime 與工具預設工作目錄。
            最大迭代次數: 單次使用者 turn 允許的最大模型/tool loop 次數。
            上下文長度: 模型 context window 長度，用於 compression threshold。
            模型模式: 主模型 provider 模式，供 auxiliary compression auto 設定重用。
            啟用壓縮摘要: 是否啟用 auxiliary LLM summary；None 時讀取環境設定。
            摘要失敗是否中止: summary 失敗時是否中止壓縮；None 時讀取環境設定。
            壓縮模式: 可選 compression provider 模式；None 時 auto 重用主模式或環境值。
            壓縮模型: 可選 compression 模型名稱；None 時 auto 重用主模型或環境值。
            user_id: 使用者識別碼，CLI/Gateway 可用於 session 篩選與審計。
            source: 來源平台，例如 cli、api、telegram 或 discord。
            model_config: 模型設定快照，會寫入 sessions.model_config。

        返回值：
            None。初始化後會建立工具登錄器、工作目錄、最大迭代限制與
            `上下文壓縮器物件`，後者可能包含 auxiliary LLM summary 函式。
        """
        self.工作階段庫物件 = 工作階段庫物件
        設定目前工作階段資料庫路徑(str(工作階段庫物件.資料庫路徑))
        self.模型供應商物件 = 模型供應商物件
        self.模型名稱 = 模型名稱
        self.供應商名稱 = 供應商名稱
        self.平台名稱 = 平台名稱
        self.使用者上下文物件 = self.解析使用者上下文(user_id, 使用者上下文物件, 工作目錄)
        self.user_id = self.使用者上下文物件.user_id
        設定目前使用者(self.user_id, self.使用者上下文物件)
        self.source = source
        self.model_config = model_config or {"mode": 模型模式}
        self.工作目錄 = str(Path(工作目錄).expanduser().resolve())
        self.工具登錄器物件 = 工具登錄器物件 or 建立預設工具登錄器(self.工作目錄, self.使用者上下文物件)
        self.最大迭代次數 = 最大迭代次數
        self.事件回呼 = 事件回呼
        self.記憶管理器 = 記憶管理器
        self.上下文引擎 = 上下文引擎
        self.壓縮讓路警告集合: set[str] = set()
        self.壓縮鎖錯誤集合: set[str] = set()
        摘要函式 = self.建立壓縮摘要函式(模型模式, 啟用壓縮摘要, 壓縮模式, 壓縮模型)
        self.上下文壓縮器物件 = 上下文壓縮器(
            上下文長度=上下文長度,
            摘要函式=摘要函式,
            摘要失敗是否中止=解析摘要失敗是否中止() if 摘要失敗是否中止 is None else 摘要失敗是否中止,
        )

    def 解析使用者上下文(self, user_id: str | None, 使用者上下文物件: 使用者上下文 | None, 工作目錄: str) -> 使用者上下文:
        """解析 runtime 使用者上下文，避免 session owner 與 runtime 權限脫鉤。

        參數：
            user_id: 呼叫端指定的使用者識別碼；僅作為完整 context 載入或 local fallback。
            使用者上下文物件: 呼叫端已解析的完整使用者上下文。
            工作目錄: fallback allowed_workdirs 使用的工作目錄。

        返回值：
            完整且不會就地修改呼叫端物件的使用者上下文。
        """
        if 使用者上下文物件 is not None:
            if user_id and user_id != 使用者上下文物件.user_id:
                if 使用者上下文物件.user_id != "local":
                    raise ValueError(f"user_id 與使用者上下文不一致：{user_id} != {使用者上下文物件.user_id}")
                return self.依user_id建立使用者上下文(user_id, 工作目錄)
            return 使用者上下文物件
        if user_id:
            return self.依user_id建立使用者上下文(user_id, 工作目錄)
        return 建立預設使用者上下文(工作目錄)

    def 依user_id建立使用者上下文(self, user_id: str, 工作目錄: str) -> 使用者上下文:
        """依 user_id 從 DB 載入完整權限，未知使用者則建立受限 fallback。

        參數：
            user_id: 使用者識別碼。
            工作目錄: fallback allowed_workdirs 使用的工作目錄。

        返回值：
            完整使用者上下文；不會沿用 local/admin 預設權限。
        """
        try:
            return 使用者庫(self.工作階段庫物件.資料庫路徑).建立使用者上下文(user_id=user_id, 工作目錄=工作目錄)
        except ValueError:
            允許目錄 = [Path(工作目錄).expanduser().resolve()]
            return 使用者上下文(
                user_id=user_id,
                username=user_id,
                display_name=user_id,
                roles=["user"],
                enabled_tools=set(),
                enabled_skills=set(),
                skill_roots=[],
                allowed_workdirs=允許目錄,
                memory_home=取得預設記憶根目錄(user_id),
                is_admin=False,
            )

    def 建立壓縮摘要函式(
        self,
        模型模式: str,
        啟用壓縮摘要: bool | None,
        壓縮模式: str | None,
        壓縮模型: str | None,
    ):
        """解析 auxiliary compression 設定並建立 summary callable。

        參數：
            模型模式: 主模型 provider 模式，例如 `fake` 或 `gemini`。
            啟用壓縮摘要: 是否啟用 LLM summary；None 表示依環境變數決定。
            壓縮模式: 指定 compression provider 模式；None 表示 auto。
            壓縮模型: 指定 compression 模型名稱；None 表示 auto。

        返回值：
            Callable | None：啟用時回傳可注入上下文壓縮器的 summary 函式；停用、
            模式為 `off` 或設定不需要 LLM summary 時回傳 None。若 compression 模型
            與主模型相同，會重用主 provider；否則建立獨立 provider。
        """
        應啟用 = 是否啟用壓縮摘要() if 啟用壓縮摘要 is None else 啟用壓縮摘要
        if not 應啟用:
            return None
        解析模式, 解析模型 = 解析壓縮模型設定(模型模式, self.模型名稱, 壓縮模式, 壓縮模型)
        if 解析模式 == "off":
            return None
        if 解析模式 == 模型模式 and 解析模型 == self.模型名稱:
            壓縮供應商 = self.模型供應商物件
        else:
            壓縮供應商 = 建立模型供應商(解析模式, 解析模型)
        return 建立壓縮摘要函式(壓縮供應商)

    def 執行使用者訊息(self, 使用者訊息: str, 工作階段識別碼: str | None = None, 額外系統訊息: str | None = None) -> 執行結果:
        """執行單次使用者 turn 並完成模型/tool/compression loop。

        參數：
            使用者訊息: 使用者本次輸入的文字。
            工作階段識別碼: 可選 session id；提供時會讀取既有 session，未提供時
                建立新 session。
            額外系統訊息: 可選的 context-tier system message，僅在首次建立
                session system prompt 時納入。

        返回值：
            執行結果：包含最終回答、目前 active session id、壓縮後或完整訊息清單、
            模型呼叫次數、工具呼叫次數，以及本 turn 是否發生 compression split。
        """
        原始工作階段識別碼 = 工作階段識別碼
        設定目前使用者(self.user_id, self.使用者上下文物件)
        工作階段識別碼 = self.工作階段庫物件.建立或讀取工作階段(
            工作階段識別碼,
            source=self.source,
            user_id=self.user_id,
            model=self.模型名稱,
            model_config=self.model_config,
            cwd=self.工作目錄,
        )
        if 原始工作階段識別碼:
            工作階段識別碼 = self.工作階段庫物件.解析Resume工作階段(工作階段識別碼, user_id=self.user_id, source=self.source)
        設定目前工作階段識別碼(工作階段識別碼)
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

            self.工作階段庫物件.更新模型使用量(工作階段識別碼, 模型回應.使用量, api呼叫增量=1, billing_provider=self.供應商名稱)
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
                assistant訊息 = {"role": "assistant", "content": 模型回應.文字 or "", "tool_calls": 模型回應.工具呼叫清單, "finish_reason": 模型回應.完成原因}
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
            訊息清單.append({"role": "assistant", "content": 最終回答, "finish_reason": 模型回應.完成原因})
            self.工作階段庫物件.寫入訊息清單(工作階段識別碼, 訊息清單)
            break
        else:
            最終回答 = "已達最大迭代次數，仍未取得最終回答。"
            訊息清單.append({"role": "assistant", "content": 最終回答, "finish_reason": "max_iterations"})
            self.工作階段庫物件.寫入訊息清單(工作階段識別碼, 訊息清單)

        return 執行結果(最終回答, 工作階段識別碼, 訊息清單, 模型呼叫次數, 工具呼叫次數, 是否已壓縮)

    def rewind到訊息(self, 工作階段識別碼: str, 目標訊息id: int) -> dict[str, Any]:
        """從 runtime 入口執行 soft-delete rewind。

        參數：
            工作階段識別碼: 要 rewind 的 session id；會先導向 compression tip。
            目標訊息id: 目標 message row id。

        返回值：dict[str, Any]。工作階段庫回傳的 rewind 結果，並包含 active session id。
        """
        設定目前使用者(self.user_id, self.使用者上下文物件)
        作用中工作階段識別碼 = self.工作階段庫物件.解析Resume工作階段(工作階段識別碼, user_id=self.user_id, source=self.source)
        設定目前工作階段識別碼(作用中工作階段識別碼)
        結果 = self.工作階段庫物件.rewind到訊息(作用中工作階段識別碼, 目標訊息id, user_id=self.user_id)
        結果["session_id"] = 作用中工作階段識別碼
        return 結果

    def 建立Request訊息(self, 系統提示詞: str, 訊息清單: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """在 provider request 邊界組裝 system prompt 與 transcript。

        參數：
            系統提示詞: sessions.system_prompt 保存的穩定 prompt 快照。
            訊息清單: persisted transcript messages，不包含 role=system。

        返回值：
            list[dict[str, Any]]：第一則為 system message、後面接 transcript 的
            request messages。此函數不修改資料庫，避免 system prompt 被重複持久化
            與重複計入 compression head/tail。
        """
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
        """取得 compression lock 後嘗試壓縮並建立 session split。

        參數：
            工作階段識別碼: 目前 active session id。
            訊息清單: 目前 working messages。
            系統提示詞: session 的 stable system prompt；會複製到新 child session。
            工具結構清單: 本次 request 的 tool schemas，用於 token 估算。
            provider提示Token數: 可選 provider 真實 prompt token 數，用於
                post-response compression 判斷。
            強制: True 時強制壓縮，通常由 context overflow recovery 使用。

        返回值：
            tuple[str, list[dict[str, Any]], bool]：新的 active session id、對應訊息
            清單、是否真的發生壓縮。若未取得鎖或不需壓縮，會回傳原 session 與
            原訊息清單。
        """
        try:
            鎖Context = self.工作階段庫物件.壓縮鎖(工作階段識別碼)
        except Exception:
            鎖Context = None
        if 鎖Context is None:
            鎖Context = nullcontext(True)
        with 鎖Context as 是否取得鎖:
            if not 是否取得鎖:
                鎖定持有者 = self.工作階段庫物件.讀取壓縮鎖Holder(工作階段識別碼)
                if 工作階段識別碼 not in self.壓縮讓路警告集合:
                    self.壓縮讓路警告集合.add(工作階段識別碼)
                    print(f"⚠ 跳過並發壓縮：session {工作階段識別碼} 正由 {鎖定持有者 or 'unknown'} 壓縮，稍後會再試。")
                return 工作階段識別碼, 訊息清單, False
            self.工作階段庫物件.寫入訊息清單(工作階段識別碼, 訊息清單)
            if self.記憶管理器 and hasattr(self.記憶管理器, "on_pre_compress"):
                try:
                    self.記憶管理器.on_pre_compress(訊息清單)
                except Exception:
                    pass
            壓縮結果 = self.上下文壓縮器物件.壓縮訊息(
                訊息清單,
                系統提示詞,
                工具結構清單,
                provider提示Token數=provider提示Token數,
                強制=強制,
            )
            if not 壓縮結果.是否已壓縮:
                return 工作階段識別碼, 訊息清單, False
            舊工作階段識別碼 = 工作階段識別碼
            新工作階段識別碼 = self.工作階段庫物件.建立壓縮後工作階段(舊工作階段識別碼, 壓縮結果.訊息清單, 系統提示詞)
            設定目前工作階段識別碼(新工作階段識別碼)
            self.通知Session切換(新工作階段識別碼, 舊工作階段識別碼)
            return 新工作階段識別碼, 壓縮結果.訊息清單, True

    def 通知Session切換(self, 新工作階段識別碼: str, 舊工作階段識別碼: str) -> None:
        """通知事件、記憶與上下文引擎 session id 因壓縮而切換。

        參數：
            新工作階段識別碼: compression child session id。
            舊工作階段識別碼: compression parent session id。

        返回值：None。hook 失敗不會中斷主流程，避免壓縮成功後因外掛錯誤回滾。
        """
        if self.上下文引擎 and hasattr(self.上下文引擎, "on_session_start"):
            try:
                self.上下文引擎.on_session_start(
                    新工作階段識別碼,
                    boundary_reason="compression",
                    old_session_id=舊工作階段識別碼,
                )
            except Exception:
                pass
        if self.記憶管理器 and hasattr(self.記憶管理器, "on_session_switch"):
            try:
                self.記憶管理器.on_session_switch(
                    新工作階段識別碼,
                    parent_session_id=舊工作階段識別碼,
                    reset=False,
                    reason="compression",
                )
            except Exception:
                pass
        if self.事件回呼:
            try:
                self.事件回呼("session:compress", {
                    "session_id": 新工作階段識別碼,
                    "old_session_id": 舊工作階段識別碼,
                    "compression_count": self.上下文壓縮器物件.壓縮次數 if hasattr(self.上下文壓縮器物件, "壓縮次數") else None,
                })
            except Exception:
                pass

    def 是否ContextOverflow錯誤(self, 錯誤: Exception) -> bool:
        """判斷例外是否屬於可用壓縮重試的 context overflow 類錯誤。

        參數：
            錯誤: provider 呼叫時丟出的例外。

        返回值：
            bool：True 表示錯誤訊息包含 413、payload too large、context、token、
            too long、long context 或 image too large 等關鍵字，可嘗試強制壓縮後
            retry；False 表示非壓縮可恢復錯誤，應重新丟出。
        """
        文字 = str(錯誤).lower()
        關鍵字清單 = ["413", "payload too large", "context", "token", "too long", "long context", "image too large"]
        return any(關鍵字 in 文字 for 關鍵字 in 關鍵字清單)

    def 建立系統提示詞(self, 工作階段識別碼: str, 額外系統訊息: str | None = None) -> str:
        """建立目前 session 的穩定 system prompt。

        參數：
            工作階段識別碼: 目前 session id，會寫入 prompt metadata。
            額外系統訊息: 可選的額外 context-tier system message；通常用於首次建立
                session 時加入當前任務或工作區上下文。

        返回值：
            str：由提示詞組裝器產生的完整 system prompt，應保存到 sessions row，
            並只在 provider request 邊界 prepend，不應寫入 messages transcript。
        """
        技能摘要 = self.建立技能摘要()
        設定 = 提示詞設定(
            模型名稱=self.模型名稱,
            供應商名稱=self.供應商名稱,
            工作階段識別碼=工作階段識別碼,
            平台名稱=self.平台名稱,
            工具名稱清單=list(self.工具登錄器物件.工具表.keys()),
            技能摘要=技能摘要,
            記憶文字=self.建立記憶提示區塊("memory"),
            使用者資料文字=self.建立記憶提示區塊("user"),
            工作目錄=self.工作目錄,
        )
        return 提示詞組裝器(設定).組裝系統提示詞(額外系統訊息)

    def 建立記憶提示區塊(self, 目標: str) -> str:
        """載入內建記憶 snapshot 給 system prompt。

        參數：
            目標: memory 或 user。

        返回值：
            frozen snapshot 區塊；沒有記憶時回傳空字串。
        """
        hermes家目錄: Path | None = None
        try:
            from .記憶存放 import 記憶存放

            暫存設定 = 提示詞設定(工作目錄=self.工作目錄, Hermes家目錄=str(self.使用者上下文物件.memory_home) if self.使用者上下文物件.memory_home else None)
            hermes家目錄 = 提示詞組裝器(暫存設定).取得Hermes家目錄()
            存放 = 記憶存放(hermes家目錄)
            存放.載入()
            return 存放.格式化給系統提示(目標)
        except Exception:
            _logger.warning(
                "載入 %s 記憶 snapshot 失敗（hermes home=%s）",
                目標,
                hermes家目錄,
                exc_info=True,
            )
            return ""

    def 建立技能摘要(self) -> str:
        """依使用者可用技能根目錄建立 prompt 用摘要。

        參數：
            無。函數會依目前 UserContext 的 skill_roots 掃描；None 代表使用
            專案內建技能根目錄（hermes_skills + user_skill），與 skills_list /
            skill_view 的預設掃描範圍一致。空清單代表不注入任何技能摘要。

        返回值：
            str：依 enabled_skills 與可用工具過濾後的 `<available_skills>` 區塊。
            多個根目錄會合併成單一區塊，避免重複技能載入指引。
        """
        if self.使用者上下文物件.skill_roots is None:
            根目錄清單 = 取得技能根目錄清單({"_skill_roots": None})
        else:
            根目錄清單 = self.使用者上下文物件.skill_roots
        if not 根目錄清單:
            return ""
        工具名稱集合 = set(self.工具登錄器物件.工具表.keys())
        摘要清單 = [建立技能索引摘要(根目錄, 工具名稱集合, self.使用者上下文物件.enabled_skills) for 根目錄 in 根目錄清單]
        if len(摘要清單) == 1:
            return 摘要清單[0]
        技能行清單: list[str] = []
        已出現技能名稱: set[str] = set()
        已出現行: set[str] = set()
        for 摘要 in 摘要清單:
            區塊內 = False
            for 行 in 摘要.splitlines():
                if 行.strip() == "<available_skills>":
                    區塊內 = True
                    continue
                if 行.strip() == "</available_skills>":
                    區塊內 = False
                    continue
                if not 區塊內 or "(no skills found)" in 行:
                    continue
                指紋 = 行.strip()
                技能名稱 = ""
                if 指紋.startswith("- "):
                    技能名稱 = 指紋[2:].split(":", 1)[0].strip()
                if 技能名稱 and 技能名稱 in 已出現技能名稱:
                    continue
                if 指紋 in 已出現行:
                    continue
                if 技能名稱:
                    已出現技能名稱.add(技能名稱)
                已出現行.add(指紋)
                技能行清單.append(行)
        if not 技能行清單:
            return 技能強制載入指引 + "\n\n<available_skills>\n  (no skills found)\n</available_skills>\n\n" + 技能無相關項目指引
        return "\n".join([技能強制載入指引, "", "<available_skills>", *技能行清單, "</available_skills>", "", 技能無相關項目指引])
