"""將 INV 執行嘗試接到 exact-version Published Runtime。

參數：公開 factory 接收快照、技能套件、工具發布及模型 registry 依賴。
回傳：callable bridge；每次呼叫回傳一個 ``執行嘗試結果``。
例外：控制流程例外保留 identity；其他錯誤一律轉成 INV terminal kind。
副作用：每次嘗試只做一次 exact version/release 組裝，不查 current 或 fallback。
"""
from __future__ import annotations

from typing import Any, Callable, cast

from ..呼叫.編排器 import 執行嘗試請求, 執行嘗試結果
from ..領域模型 import PublishedUsage
from .工具結果 import 工具設定錯誤, 工具逾時
from .工具發布庫 import 工具發布版
from .模型契約 import 模型回應快照, 模型逾時錯誤, 複製JSON
from .執行器 import (
    發布執行快照,
    發布執行請求,
    發布工具執行錯誤,
    工具結果觀察,
    建立發布執行器,
    _解析模型JSON,
)

_控制流程 = (KeyboardInterrupt, SystemExit, GeneratorExit)
_用量鍵 = ("total_tokens", "total_token_count", "total")


class _方法載入器:
    """保存 bridge 建立點捕捉的單一 callback。

    參數：任一 callable。回傳：依 executor 所需名稱轉送回傳值。
    例外：callback 例外原樣傳出。副作用：只執行 captured callback。
    """

    __slots__ = ("_方法",)

    def __init__(self, 方法: Callable[..., object]) -> None:
        self._方法 = 方法

    def 載入服務帳戶上下文(self, *參數: object) -> object:
        """轉送 exact service-account snapshot lookup。"""
        return self._方法(*參數)

    def 載入技能套件快照(self, *參數: object) -> object:
        """轉送 exact bundle snapshot lookup。"""
        return self._方法(*參數)


class 發布執行嘗試橋接:
    """sealed dependencies 的 single-attempt callable bridge。

    參數：只由 factory 提供四個已捕捉依賴。回傳：callable object。
    例外：直接建立不屬公開契約。副作用：建構只保存 callback 與 registry 複本。
    """

    __slots__ = ("_快照", "_帳戶", "_套件", "_發布", "_模型", "_工具呼叫紀錄器")

    def __init__(self, 快照: Callable[..., object], 帳戶: Callable[..., object],
                 套件: Callable[..., object], 發布: Callable[..., object],
                 模型: dict[str, object], 工具呼叫紀錄器: Callable[..., object] | None = None) -> None:
        self._快照, self._帳戶, self._套件, self._發布 = 快照, 帳戶, 套件, 發布
        self._模型 = 模型
        self._工具呼叫紀錄器 = 工具呼叫紀錄器

    def __call__(self, 請求: 執行嘗試請求) -> 執行嘗試結果:
        """完成一個 INV-owned attempt，schema correction 留給 INV 的第二次 attempt。

        參數：exact ``執行嘗試請求``；metadata 不會成為 system/tool role。
        回傳：detached success data/usage，或固定 terminal failure kind。
        例外：K/I/S/G 原物件穿透；普通例外不離開 bridge。
        副作用：exact version 與 release 各 lookup 一次，執行一個發布請求及有界工具回合。
        """
        呼叫識別 = 嘗試次數 = None
        工具序號 = [0]
        try:
            if type(請求) is not 執行嘗試請求:
                raise ValueError
            呼叫識別 = object.__getattribute__(請求, "invocation_id")
            嘗試次數 = object.__getattribute__(請求, "attempt")
            if self._工具呼叫紀錄器 is not None and (
                type(呼叫識別) is not str or not 呼叫識別
                or type(嘗試次數) is not int or 嘗試次數 not in (1, 2)
            ):
                raise ValueError
            版本, 輸入 = self._建立執行材料(請求)
        except _控制流程:
            raise
        except BaseException:
            return 執行嘗試結果("endpoint_misconfigured")

        def 紀錄工具(觀察: 工具結果觀察) -> None:
            """以 typed safe outcome 原子保存工具參數及成功結果或固定錯誤分類。

            參數：executor-owned ``工具結果觀察``；失敗觀察不含 provider error payload。
            返回值：無；recorder ordinary failure 原樣回到 executor，標示治理失敗來源。
            """
            紀錄器 = 識別 = 結果 = None
            try:
                紀錄器 = self._工具呼叫紀錄器
                if 紀錄器 is None:
                    return
                if type(觀察) is not 工具結果觀察:
                    raise ValueError
                工具序號[0] += 1
                識別 = f"{呼叫識別}:attempt:{嘗試次數}:tool:{工具序號[0]}"
                if 觀察.outcome == "success":
                    結果 = 觀察.result
                    if type(結果) is not dict:
                        raise ValueError
                    紀錄器(
                        呼叫識別, 識別, 觀察.tool_name, 觀察.arguments,
                        "success", result=結果,
                    )
                    return
                if (觀察.outcome != "error"
                        or 觀察.safe_error_code not in (
                            "tool_timeout", "endpoint_misconfigured", "tool_execution_failed",
                        ) or 觀察.result is not None):
                    raise ValueError
                紀錄器(
                    呼叫識別, 識別, 觀察.tool_name, 觀察.arguments,
                    "error", error={"code": 觀察.safe_error_code},
                )
            except _控制流程:
                觀察 = 紀錄器 = 識別 = 結果 = cast(Any, None)
                raise

        try:
            release = self._發布(版本.tool_handler_release)
            if type(release) is not 工具發布版 or release.handler_release != 版本.tool_handler_release:
                raise ValueError
            provider = object.__getattribute__(版本.model_config, "provider")
            if type(provider) is not str or provider not in self._模型:
                raise ValueError
            執行器 = 建立發布執行器(
                endpoint_version_id=版本.version_id,
                service_account_id=版本.service_account_id,
                發布快照提供者=版本,
                服務帳戶載入器=_方法載入器(self._帳戶),
                技能套件載入器=_方法載入器(self._套件),
                工具修訂提供者=release,
                模型供應商註冊表=dict(self._模型),
                工具呼叫觀察器=紀錄工具 if self._工具呼叫紀錄器 is not None else None,
            )
        except _控制流程:
            raise
        except BaseException:
            return 執行嘗試結果("endpoint_misconfigured")

        try:
            歷史 = object.__getattribute__(請求, "history")
            回應 = 執行器.執行單次(發布執行請求(輸入, 歷史))
            return _轉成嘗試結果(回應, 版本.response_schema is not None)
        except _控制流程:
            raise
        except 模型逾時錯誤:
            return 執行嘗試結果("model_timeout")
        except 工具逾時:
            return 執行嘗試結果("tool_timeout")
        except 工具設定錯誤:
            return 執行嘗試結果("endpoint_misconfigured")
        except 發布工具執行錯誤:
            return 執行嘗試結果("tool_execution_failed")
        except BaseException:
            return 執行嘗試結果("internal_error")

    def _建立執行材料(self, 請求: 執行嘗試請求) -> tuple[發布執行快照, Any]:
        """重建 request pin，做唯一 exact snapshot lookup 並比對三個 authority identity。

        參數：不可信 INV DTO。回傳：可信 snapshot 與 detached input。
        例外：外形、lookup 或 mismatch 傳出普通驗證例外。副作用：exact lookup 一次。
        """
        if type(請求) is not 執行嘗試請求:
            raise ValueError
        pin = object.__getattribute__(請求, "pinned_version")
        endpoint = object.__getattribute__(pin, "endpoint_id")
        account = object.__getattribute__(pin, "service_account_id")
        version = object.__getattribute__(pin, "version_id")
        for 值 in (endpoint, account, version):
            if type(值) is not str or not 值:
                raise ValueError
        snapshot = self._快照(version)
        if type(snapshot) is not 發布執行快照:
            raise ValueError
        if (snapshot.endpoint_id != endpoint or snapshot.service_account_id != account
                or snapshot.version_id != version):
            raise ValueError
        輸入 = 複製JSON(object.__getattribute__(請求, "input"), 500_000)
        return snapshot, 輸入


def _轉成嘗試結果(回應: object, 結構化: bool) -> 執行嘗試結果:
    """嚴格重建 final response；只解析 JSON，不在此 attempt 判定 schema 是否相符。

    參數：模型回應與 snapshot structured flag。回傳：bounded INV success DTO。
    例外：回應、JSON 或用量不符時傳出普通驗證例外。副作用：只配置 detached DTO。
    """
    if type(回應) is not 模型回應快照 or type(結構化) is not bool:
        raise ValueError
    文字 = object.__getattribute__(回應, "text")
    if type(文字) is not str:
        raise ValueError
    資料 = 文字
    if 結構化:
        try:
            資料 = _解析模型JSON(文字, 500_000)
        except _控制流程:
            raise
        except BaseException:
            # malformed provider text 是 schema-invalid success；INV 擁有 correction retry。
            資料 = 文字
    用量資料 = object.__getattribute__(回應, "usage")
    if type(用量資料) is not dict:
        raise ValueError
    total = None
    found = False
    for 鍵 in _用量鍵:
        if 鍵 in 用量資料:
            值 = dict.__getitem__(用量資料, 鍵)
            if found or type(值) is not int or 值 < 0:
                raise ValueError
            total, found = 值, True
    用量 = PublishedUsage(total) if found else None
    return 執行嘗試結果("success", 資料, 用量, ())


def 建立發布執行嘗試橋接(*, 發布快照儲存庫: object, 技能套件載入器: object,
                         工具發布庫: object,
                         模型供應商註冊表: dict[str, object],
                         工具呼叫紀錄器: Callable[..., object] | None = None) -> 發布執行嘗試橋接:
    """捕捉 production dependencies，建立不受後續 method/registry mutation 影響的 bridge。

    參數：snapshot repository、bundle loader、release repository 與 provider registry。
    回傳：``發布執行嘗試橋接``。例外：缺少 callback 或 registry 外形時固定 ValueError。
    副作用：不做任何 lookup、模型或工具呼叫。
    """
    try:
        快照 = getattr(發布快照儲存庫, "取得發布執行快照")
        帳戶 = getattr(發布快照儲存庫, "載入服務帳戶上下文")
        套件 = getattr(技能套件載入器, "載入技能套件快照")
        發布 = getattr(工具發布庫, "取得發布")
        if not all(callable(項) for 項 in (快照, 帳戶, 套件, 發布)):
            raise ValueError
        if (type(模型供應商註冊表) is not dict
                or (工具呼叫紀錄器 is not None and not callable(工具呼叫紀錄器))):
            raise ValueError
        模型 = dict(模型供應商註冊表)
        if any(type(鍵) is not str for 鍵 in 模型):
            raise ValueError
        return 發布執行嘗試橋接(快照, 帳戶, 套件, 發布, 模型, 工具呼叫紀錄器)
    except _控制流程:
        raise
    except BaseException:
        raise ValueError("發布執行嘗試橋接不可用") from None


建立Runtime呼叫橋接 = 建立發布執行嘗試橋接
