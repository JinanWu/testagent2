"""Published Runtime 的 immutable 模型快照套用與 typed timeout adapter。"""

from __future__ import annotations

import threading
from typing import Any, Protocol
import weakref

from .模型契約 import (
    供應商逾時,
    控制流程,
    模型回應快照,
    模型設定快照,
    模型設定錯誤,
    模型轉接錯誤,
    模型轉接請求,
    模型逾時錯誤,
    設定訊息,
    轉接訊息,
    複製JSON,
    重建設定,
)


class 發布模型供應商(Protocol):
    """由 provider 自行執行 timeout 的 published-runtime boundary。"""

    def 產生發布回應(self, **參數: Any) -> object:
        """接收所有釘選參數；schema retry 僅為政策 metadata。"""


class 模型轉接器:
    """只可由建立模型轉接器 factory 取得的 sealed adapter。"""

    __slots__ = ()

    def __new__(cls, *參數: object, **命名參數: object) -> 模型轉接器:
        """固定拒絕所有 caller 直接建構，不檢視 caller 提供的物件。"""
        cls = 參數 = 命名參數 = None
        raise 模型設定錯誤(設定訊息) from None

    def 產生回應(self, 請求: 模型轉接請求) -> 模型回應快照:
        """傳入精確 timeout/config；只翻譯明確逾時，不建立背景 thread。"""
        狀態 = 封印 = 供應商 = 產生方法 = 設定 = 訊息原值 = 工具原值 = 結構原值 = None
        訊息 = 工具 = 結構 = 不可信回應 = 結果 = 錯誤 = None
        失敗 = 逾時 = False
        try:
            if type(self) is not _模型轉接器實作:
                raise ValueError
            with _轉接器狀態鎖:
                狀態 = _轉接器狀態.get(self)
            if type(狀態) is not tuple or len(狀態) != 4:
                raise ValueError
            封印, 設定, 供應商, 產生方法 = 狀態
            if 封印 is not _轉接器封印 or type(設定) is not 模型設定快照:
                raise ValueError
            if type(請求) is not 模型轉接請求:
                raise ValueError
            訊息原值 = object.__getattribute__(請求, "messages")
            工具原值 = object.__getattribute__(請求, "tools")
            結構原值 = object.__getattribute__(請求, "response_schema")
            訊息 = 複製JSON(訊息原值, 1_000_000)
            工具 = 複製JSON(工具原值, 1_000_000)
            if 設定.structured_output:
                if type(結構原值) is not dict:
                    raise ValueError
                結構 = 複製JSON(結構原值, 500_000)
            不可信回應 = 產生方法(
                model=設定.model,
                temperature=設定.temperature,
                max_tokens=設定.max_tokens,
                timeout_seconds=設定.timeout_seconds,
                structured_output=設定.structured_output,
                schema_retry_count=設定.schema_retry_count,
                messages=訊息,
                tools=工具,
                response_schema=結構,
            )
            結果 = _重建回應(不可信回應)
            return 結果
        except 控制流程:
            self = 請求 = 狀態 = 封印 = 供應商 = 產生方法 = 設定 = 訊息原值 = 工具原值 = 結構原值 = None
            訊息 = 工具 = 結構 = 不可信回應 = 結果 = 錯誤 = None
            raise
        except BaseException as 錯誤:
            逾時 = type(錯誤) is 供應商逾時
            失敗 = True
        if 失敗:
            self = 請求 = 狀態 = 封印 = 供應商 = 產生方法 = 設定 = 訊息原值 = 工具原值 = 結構原值 = None
            訊息 = 工具 = 結構 = 不可信回應 = 結果 = 錯誤 = None
            if 逾時:
                raise 模型逾時錯誤("模型供應商逾時") from None
            raise 模型轉接錯誤(轉接訊息) from None
        raise AssertionError


class _模型轉接器實作(模型轉接器):
    """factory 以 object allocation 建立的 module-private sealed implementation。"""

    __slots__ = ("__weakref__",)


_轉接器封印 = object()
_轉接器狀態鎖 = threading.Lock()
_轉接器狀態: weakref.WeakKeyDictionary[
    模型轉接器, tuple[object, 模型設定快照, object, Any]
] = weakref.WeakKeyDictionary()


def 建立模型轉接器(
    註冊表: dict[str, 發布模型供應商],
    快照: 模型設定快照 | dict[str, Any],
) -> 模型轉接器:
    """完整預檢快照與 registry descriptor 後才捕捉 provider method。"""
    設定 = 供應商 = 描述 = 鍵 = 值 = 現鍵 = 現值 = 方法 = 轉接器 = 錯誤 = None
    try:
        設定 = 重建設定(快照)
        if type(註冊表) is not dict:
            raise ValueError
        描述 = []
        for 鍵, 值 in dict.items(註冊表):
            if type(鍵) is not str:
                raise ValueError
            描述.append((鍵, 值))
        if len(描述) != len(註冊表):
            raise ValueError
        for 鍵, 值 in 描述:
            if 鍵 == 設定.provider:
                供應商 = 值
        if 供應商 is None:
            raise ValueError
        if len(註冊表) != len(描述):
            raise ValueError
        索引 = 0
        for 現鍵, 現值 in dict.items(註冊表):
            if 索引 >= len(描述):
                raise ValueError
            鍵, 值 = 描述[索引]
            if type(現鍵) is not str or 現鍵 != 鍵 or 現值 is not 值:
                raise ValueError
            索引 += 1
        if 索引 != len(描述):
            raise ValueError
        方法 = getattr(供應商, "產生發布回應", None)
        if not callable(方法):
            raise ValueError
        轉接器 = object.__new__(_模型轉接器實作)
        with _轉接器狀態鎖:
            _轉接器狀態[轉接器] = (_轉接器封印, 設定, 供應商, 方法)
        return 轉接器
    except 控制流程:
        註冊表 = 快照 = 設定 = 供應商 = 描述 = 鍵 = 值 = 現鍵 = 現值 = 方法 = 轉接器 = 錯誤 = None
        索引 = None
        raise
    except BaseException:
        註冊表 = 快照 = 設定 = 供應商 = 描述 = 鍵 = 值 = 現鍵 = 現值 = 方法 = 轉接器 = 錯誤 = None
        索引 = None
        raise 模型設定錯誤(設定訊息) from None


def _重建回應(值: object) -> 模型回應快照:
    """先捕捉全部 provider result slots，再建立 detached exact response。"""
    文字 = 原因 = 使用量原值 = 呼叫原值 = 使用量 = 呼叫 = 結果 = None
    try:
        if type(值) is not 模型回應快照:
            raise ValueError
        文字 = object.__getattribute__(值, "text")
        原因 = object.__getattribute__(值, "finish_reason")
        使用量原值 = object.__getattribute__(值, "usage")
        呼叫原值 = object.__getattribute__(值, "tool_calls")
        if type(文字) is not str or len(文字) > 1_000_000:
            raise ValueError
        if type(原因) is not str or len(原因) > 128:
            raise ValueError
        使用量 = 複製JSON(使用量原值, 500_000)
        呼叫 = 複製JSON(呼叫原值, 1_000_000)
        if type(使用量) is not dict or type(呼叫) is not list:
            raise ValueError
        結果 = 模型回應快照(文字, 原因, 使用量, 呼叫)
        return 結果
    except BaseException:
        值 = 文字 = 原因 = 使用量原值 = 呼叫原值 = 使用量 = 呼叫 = 結果 = None
        raise
