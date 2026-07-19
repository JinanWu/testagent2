"""CP3 Web Chat、工作階段與技能的 transport-neutral 安全服務。

本模組只重用既有使用者庫、工作階段庫、代理執行階段與技能索引器；不解析 HTTP，
也不直接執行 SQL。所有回應都先重建為本模組的固定 allowlist DTO。
"""

from __future__ import annotations

from dataclasses import dataclass
import math
import os
from pathlib import Path
import stat
from typing import Any, Iterator, Protocol

from 繁中代理.基本工具 import 取得技能根目錄清單
from 繁中代理.使用者 import 使用者上下文
from 繁中代理.技能索引器 import (
    取得目前平台名稱,
    建立可用工具集名稱集合,
    截斷摘要文字,
    技能是否符合平台,
    技能是否符合工具條件,
    解析Markdown前置資料,
    讀取停用技能名稱集合,
)

_WEB來源 = "web"
_最大訊息位元組 = 16_384
_最大識別碼字元 = 128
_最大技能檔案位元組 = 256 * 1024
_最大工作階段訊息數量 = 10_000
_最大工作階段總位元組 = 16 * 1024 * 1024
_最大技能索引項目數量 = 1_000
_最大技能索引總位元組 = 16 * 1024 * 1024
_最大技能走訪項目數量 = 4_000


class Web資源不存在(RuntimeError):
    """代表資源不存在或不屬於目前 owner/source；不得對外區分原因。"""


class Web請求無效(ValueError):
    """代表 transport-neutral 輸入不符合固定界線。"""


class Web服務不可用(RuntimeError):
    """代表後端依賴失敗；例外文字不得傳到 HTTP 回應。"""


class 使用者上下文供應器(Protocol):
    """重用既有使用者庫所需的最小介面。"""

    def 建立使用者上下文(self, user_id: str | None = None) -> 使用者上下文:
        """依權威使用者識別碼載入完整權限上下文。"""
        ...


class Web執行階段(Protocol):
    """重用既有代理執行階段所需的單 turn 介面。"""

    def 執行使用者訊息(self, 使用者訊息: str, 工作階段識別碼: str | None = None):
        """執行一則使用者訊息並回傳既有執行結果。"""
        ...


class Web執行階段工廠(Protocol):
    """以完整使用者上下文與固定來源建立 request-local runtime。"""

    def __call__(self, *, 使用者上下文物件: 使用者上下文, source: str) -> Web執行階段:
        """建立不共享 mutable runtime 的執行階段。"""
        ...


class Web工作階段庫(Protocol):
    """Web 服務重用既有工作階段庫的最小查詢介面。"""

    def 檢查工作階段存取(self, 工作階段識別碼: str, user_id: str | None = None, source: str | None = None):
        """檢查 owner/source；不存在回傳 None，不符時可拋 PermissionError。"""
        ...

    def 取得工作階段譜系(self, 工作階段識別碼: str) -> list[str]:
        """回傳包含 logical root 的 root-to-tip 譜系。"""
        ...

    def 列出工作階段(self, **條件: Any) -> list[dict[str, Any]]:
        """使用既有 owner/source/active/lineage 篩選列出工作階段。"""
        ...

    def 解析Resume工作階段(self, 工作階段識別碼: str, **條件: Any) -> str:
        """將 logical root 解析到目前 compression tip。"""
        ...

    def 讀取工作階段(self, 工作階段識別碼: str) -> dict[str, Any] | None:
        """讀取既有工作階段 metadata。"""
        ...

    def 讀取訊息(self, 工作階段識別碼: str, **條件: Any) -> list[dict[str, Any]]:
        """透過既有 repository 讀取 lineage transcript。"""
        ...


@dataclass(frozen=True, slots=True)
class 聊天回應:
    """Chat 成功回應的固定安全欄位。"""

    工作階段識別碼: str
    回覆內容: str


@dataclass(frozen=True, slots=True)
class 工作階段列表項目:
    """工作階段列表允許對外顯示的四個欄位。"""

    識別碼: str
    標題: str
    更新時間: float
    訊息數量: int


@dataclass(frozen=True, slots=True)
class 工作階段詳情:
    """工作階段 metadata 與已移除內部欄位的文字 transcript。"""

    識別碼: str
    標題: str
    更新時間: float
    訊息清單: tuple[tuple[str, str], ...]


@dataclass(frozen=True, slots=True)
class 技能項目:
    """技能列表的固定公開 metadata，不保存來源路徑。"""

    識別碼: str
    名稱: str
    分類: str
    描述: str


@dataclass(frozen=True, slots=True)
class 技能詳情:
    """技能固定公開 metadata 與經安全讀取的完整內容。"""

    項目: 技能項目
    內容: str


def 序列化聊天回應(回應: 聊天回應) -> dict[str, object]:
    """將自有 DTO 序列化為 frozen Chat JSON allowlist；不讀取其他屬性。"""
    if type(回應) is not 聊天回應:
        raise Web服務不可用
    return {
        "session_id": 回應.工作階段識別碼,
        "reply": {"role": "assistant", "content": 回應.回覆內容},
    }


def 序列化工作階段列表(項目清單: tuple[工作階段列表項目, ...]) -> dict[str, object]:
    """將固定列表 DTO 轉為 exact sessions envelope，不反射原始 repository rows。"""
    if type(項目清單) is not tuple or any(type(項目) is not 工作階段列表項目 for 項目 in 項目清單):
        raise Web服務不可用
    return {"sessions": [{
        "id": 項目.識別碼, "title": 項目.標題,
        "updated_at": 項目.更新時間, "message_count": 項目.訊息數量,
    } for 項目 in 項目清單]}


def 序列化工作階段詳情(詳情: 工作階段詳情) -> dict[str, object]:
    """序列化 exact session/messages allowlist，永不輸出 lineage、reasoning 或工具欄位。"""
    if type(詳情) is not 工作階段詳情:
        raise Web服務不可用
    return {
        "session": {"id": 詳情.識別碼, "title": 詳情.標題, "updated_at": 詳情.更新時間},
        "messages": [{"role": 角色, "content": 內容} for 角色, 內容 in 詳情.訊息清單],
    }


def 序列化技能列表(項目清單: tuple[技能項目, ...]) -> dict[str, object]:
    """序列化不含 path 的 exact skills envelope。"""
    if type(項目清單) is not tuple or any(type(項目) is not 技能項目 for 項目 in 項目清單):
        raise Web服務不可用
    return {"skills": [_序列化技能項目(項目) for 項目 in 項目清單]}


def 序列化技能詳情(詳情: 技能詳情) -> dict[str, object]:
    """序列化技能 metadata allowlist 與完整 SKILL.md content。"""
    if type(詳情) is not 技能詳情 or type(詳情.項目) is not 技能項目:
        raise Web服務不可用
    return {**_序列化技能項目(詳情.項目), "content": 詳情.內容}


def _序列化技能項目(項目: 技能項目) -> dict[str, str]:
    """建立單一技能的四欄外部 DTO。"""
    return {"id": 項目.識別碼, "name": 項目.名稱, "category": 項目.分類, "description": 項目.描述}
def _確認工作階段資料(資料: object, 識別碼: str, 使用者識別碼: str) -> None:
    """要求 repository row 明確符合 exact owner 與 Web source。"""
    if (
        type(資料) is not dict or 資料.get("user_id") != 使用者識別碼
        or 資料.get("source") != _WEB來源
    ):
        raise Web資源不存在


def _取得譜系根(譜系: object) -> str:
    """驗證非空 root-to-tip 譜系並回傳 logical root。"""
    if type(譜系) is not list or not 譜系:
        raise ValueError
    根識別碼 = 譜系[0]
    _驗證識別碼(根識別碼)
    return 根識別碼


def _驗證文字(值: object, 最大字元: int) -> None:
    """要求 exact、非空且有界文字；用於 title 等輸出純量。"""
    if type(值) is not str or not 1 <= len(值) <= 最大字元:
        raise ValueError


def _驗證時間(值: object) -> None:
    """要求 finite、非負 exact int/float，避免 NaN 污染 JSON。"""
    if type(值) not in (int, float) or not math.isfinite(值) or 值 < 0:
        raise ValueError


def _驗證識別碼(值: object) -> None:
    """要求 exact、非空、去邊界空白且 128 字元內的識別碼。"""
    if type(值) is not str or not 1 <= len(值) <= _最大識別碼字元 or 值.strip() != 值:
        raise Web請求無效


def _驗證訊息(值: object) -> None:
    """要求 trim 後非空且 UTF-8 大小不超過 16 KiB 的 exact string。"""
    if type(值) is not str:
        raise Web請求無效
    整理值 = 值.strip()
    if not 整理值 or len(整理值.encode("utf-8")) > _最大訊息位元組:
        raise Web請求無效
