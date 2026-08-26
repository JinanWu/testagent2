"""CP3 Web Chat、工作階段與技能的 transport-neutral 安全服務。

本模組只重用既有使用者庫、工作階段庫、代理執行階段與技能索引器；不解析 HTTP，
也不直接執行 SQL。所有回應都先重建為本模組的固定 allowlist DTO。

參數：服務入口接受已驗證的使用者識別、查詢條件與明確依賴。
回傳：回傳固定允許欄位的聊天、工作階段與技能資料物件。
例外：輸入、資源與依賴失敗映射為固定 Web 服務例外。
副作用：依操作查詢權威資料、執行代理或有界唯讀掃描技能檔案。
"""

from __future__ import annotations

from dataclasses import dataclass
import math
import os
from pathlib import Path
from typing import Any, Protocol

from 繁中代理.基本工具 import 取得技能根目錄清單
from 繁中代理.提示詞常數 import 壓縮摘要前綴
from 繁中代理.使用者 import 使用者上下文
from .安全技能目錄 import (
    安全讀取技能 as _共用安全讀取技能,
    建立錨定安全技能目錄,
    技能目錄不存在,
    技能目錄限制,
    技能走訪預算 as _技能走訪預算,
    走訪有界技能檔案 as _走訪有界技能索引檔案,
)

_未命名標題 = "新對話"
_WEB來源 = "web"
_最大訊息位元組 = 16_384
_最大成功文字位元組 = 65_536
_最大識別碼字元 = 128
_最大技能檔案位元組 = 256 * 1024
_最大工作階段訊息數量 = 10_000

# 壓縮交接訊息是以 role="user" 寫進 transcript 的（模型需要在對話流裡讀到它），
# 但它不是使用者講的話，畫面上不該出現。只比對開頭哨兵，前綴內文改版仍認得舊訊息。
_壓縮摘要哨兵 = 壓縮摘要前綴.split("]", 1)[0] + "]"

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


class Web代理服務:
    """協調既有執行階段與資料庫並建立安全資料物件。

    參數：建構時接受工作階段庫、使用者庫與執行階段工廠。
    回傳：公開方法回傳固定允許欄位的 Web 資料物件。
    例外：依輸入、資源與依賴失敗拋固定 Web 服務例外。
    副作用：依操作查詢資料庫、執行代理或唯讀掃描技能檔案。
    """

    def __init__(
        self,
        工作階段庫物件: Web工作階段庫,
        使用者庫物件: 使用者上下文供應器,
        執行階段工廠: Web執行階段工廠,
    ) -> None:
        """保存明確依賴；不建立資料庫、runtime 或 module-global mutable state。"""
        if not callable(執行階段工廠):
            raise ValueError("Web代理服務設定無效")
        self._工作階段庫 = 工作階段庫物件
        self._使用者庫 = 使用者庫物件
        self._執行階段工廠 = 執行階段工廠

    def 聊天(self, 使用者識別碼: str, 訊息: str, 工作階段識別碼: str | None = None) -> 聊天回應:
        """以登入 user 的完整上下文執行 Web turn，並只回 logical root 與純文字回答。

        參數：使用者識別碼為 current-session identity；訊息為使用者文字；工作階段
        識別碼可省略以建立新對話。返回值為固定聊天 DTO。owner/source/缺少會拋
        Web資源不存在；依賴失敗會拋 Web服務不可用；本方法不回傳工具或推理內容。
        """
        _驗證識別碼(使用者識別碼)
        _驗證訊息(訊息)
        預期根識別碼 = None
        if 工作階段識別碼 is not None:
            _驗證識別碼(工作階段識別碼)
            try:
                可見工作階段 = self._工作階段庫.檢查工作階段存取(
                    工作階段識別碼, user_id=使用者識別碼, source=_WEB來源
                )
                _確認工作階段資料(可見工作階段, 工作階段識別碼, 使用者識別碼)
                預期根識別碼 = _取得譜系根(
                    self._工作階段庫.取得工作階段譜系(工作階段識別碼)
                )
            except PermissionError:
                raise Web資源不存在 from None
            except Web資源不存在:
                raise
            except Exception:
                raise Web服務不可用 from None
            if 預期根識別碼 != 工作階段識別碼:
                raise Web資源不存在
        try:
            使用者 = self._使用者庫.建立使用者上下文(user_id=使用者識別碼)
            if type(使用者) is not 使用者上下文 or 使用者.user_id != 使用者識別碼:
                raise ValueError
            執行階段 = self._執行階段工廠(使用者上下文物件=使用者, source=_WEB來源)
            結果 = 執行階段.執行使用者訊息(訊息, 工作階段識別碼)
            回覆 = object.__getattribute__(結果, "最終回答")
            作用中識別碼 = object.__getattribute__(結果, "工作階段識別碼")
            _驗證識別碼(作用中識別碼)
            if type(回覆) is not str or len(回覆.encode("utf-8")) > _最大成功文字位元組:
                raise ValueError
            結果資料 = self._工作階段庫.檢查工作階段存取(
                作用中識別碼, user_id=使用者識別碼, source=_WEB來源
            )
            _確認工作階段資料(結果資料, 作用中識別碼, 使用者識別碼)
            根識別碼 = _取得譜系根(self._工作階段庫.取得工作階段譜系(作用中識別碼))
            if 預期根識別碼 is not None and 根識別碼 != 預期根識別碼:
                raise Web資源不存在
            return 聊天回應(根識別碼, 回覆)
        except (KeyboardInterrupt, SystemExit, GeneratorExit):
            raise
        except Web資源不存在:
            raise
        except PermissionError:
            raise Web資源不存在 from None
        except Exception:
            raise Web服務不可用 from None

    def _列表標題(self, 根識別碼: str, 使用者識別碼: str) -> str:
        """title 仍等於 session id 時，改用第一則使用者訊息當顯示標題。"""
        try:
            內容 = self._工作階段庫.讀取首則使用者訊息(根識別碼, user_id=使用者識別碼)
        except Exception:
            return _未命名標題
        if type(內容) is not str or not 內容.strip():
            return _未命名標題
        摘要 = " ".join(內容.split())
        return 摘要[:40] + "…" if len(摘要) > 40 else 摘要

    def 列出工作階段(self, 使用者識別碼: str, 數量上限: int = 20) -> tuple[工作階段列表項目, ...]:
        """列出登入 user 的 active Web logical roots，並丟棄所有內部 metadata。

        參數為 current-session user ID 與 1–50 筆上限；返回固定列表 DTO tuple。
        repository 或資料形狀失敗時拋 Web服務不可用，且不包含原始例外文字。
        """
        _驗證識別碼(使用者識別碼)
        if type(數量上限) is not int or not 1 <= 數量上限 <= 50:
            raise Web請求無效
        try:
            原始清單 = self._工作階段庫.列出工作階段(
                limit=數量上限, include_children=False, include_archived=False,
                source=_WEB來源, user_id=使用者識別碼,
            )
            if type(原始清單) is not list or len(原始清單) > 數量上限:
                raise ValueError
            結果 = []
            for 原始項目 in 原始清單:
                if type(原始項目) is not dict:
                    raise ValueError
                根識別碼 = 原始項目.get("_lineage_root_id") or 原始項目.get("id")
                標題 = 原始項目.get("title")
                更新時間 = 原始項目.get("updated_at")
                訊息數量 = 原始項目.get("message_count")
                _驗證識別碼(根識別碼)
                _驗證文字(標題, 512)
                _驗證時間(更新時間)
                if type(訊息數量) is not int or 訊息數量 < 0:
                    raise ValueError
                if 標題 == 根識別碼:
                    標題 = self._列表標題(根識別碼, 使用者識別碼)
                結果.append(工作階段列表項目(根識別碼, 標題, float(更新時間), 訊息數量))
            return tuple(結果)
        except (KeyboardInterrupt, SystemExit, GeneratorExit):
            raise
        except Exception:
            raise Web服務不可用 from None

    def 讀取工作階段(self, 使用者識別碼: str, 根工作階段識別碼: str) -> 工作階段詳情:
        """驗證 root owner/source、解析 tip，並只投影 user/assistant 純文字。

        參數為 current-session user ID 與 logical root ID；返回安全詳情 DTO。
        缺少或跨範圍拋 Web資源不存在，I/O/資料形狀錯誤拋 Web服務不可用。
        """
        _驗證識別碼(使用者識別碼)
        _驗證識別碼(根工作階段識別碼)
        try:
            根資料 = self._工作階段庫.檢查工作階段存取(
                根工作階段識別碼, user_id=使用者識別碼, source=_WEB來源
            )
            _確認工作階段資料(根資料, 根工作階段識別碼, 使用者識別碼)
        except PermissionError:
            raise Web資源不存在 from None
        except Web資源不存在:
            raise
        except Exception:
            raise Web服務不可用 from None
        try:
            canonical根識別碼 = _取得譜系根(
                self._工作階段庫.取得工作階段譜系(根工作階段識別碼)
            )
            if 根工作階段識別碼 != canonical根識別碼:
                raise Web資源不存在
            tip識別碼 = self._工作階段庫.解析Resume工作階段(
                canonical根識別碼, user_id=使用者識別碼, source=_WEB來源
            )
            tip資料 = self._工作階段庫.讀取工作階段(tip識別碼)
            _確認工作階段資料(tip資料, tip識別碼, 使用者識別碼)
            if type(tip資料) is not dict:
                raise ValueError
            原始訊息 = self._工作階段庫.讀取訊息(
                tip識別碼, include_ancestors=True, user_id=使用者識別碼
            )
            if type(原始訊息) is not list or len(原始訊息) > _最大工作階段訊息數量:
                raise ValueError
            標題, 更新時間 = tip資料.get("title"), tip資料.get("updated_at")
            _驗證文字(標題, 512)
            _驗證時間(更新時間)
            投影 = []
            總位元組 = 0
            for 訊息 in 原始訊息:
                if type(訊息) is not dict:
                    raise ValueError
                內容 = 訊息.get("content")
                if type(內容) is str:
                    總位元組 += len(內容.encode("utf-8"))
                    if 總位元組 > _最大工作階段總位元組:
                        raise ValueError
                if 訊息.get("role") not in {"user", "assistant"}:
                    continue
                if type(內容) is str and 內容.startswith(_壓縮摘要哨兵):
                    continue
                角色 = 訊息.get("role")
                if type(內容) is not str or len(內容.encode("utf-8")) > _最大成功文字位元組:
                    raise ValueError
                投影.append((角色, 內容))
            return 工作階段詳情(根工作階段識別碼, 標題, float(更新時間), tuple(投影))
        except (KeyboardInterrupt, SystemExit, GeneratorExit):
            raise
        except Web資源不存在:
            raise
        except PermissionError:
            raise Web資源不存在 from None
        except Exception:
            raise Web服務不可用 from None

    def 列出技能(self, 使用者識別碼: str) -> tuple[技能項目, ...]:
        """合併登入 user 的 authorized roots 與 enabled skills，重複 ID 時拒絕。

        參數為 current-session user ID；返回不含 path 的固定技能 DTO。使用者庫、
        索引或資料形狀失敗會拋 Web服務不可用，且不洩漏 filesystem 資訊。
        """
        try:
            索引, _ = self._建立可見技能索引(使用者識別碼)
            return tuple(_建立技能項目(識別碼, 原始項目) for 識別碼, 原始項目 in sorted(索引.items()))
        except (KeyboardInterrupt, SystemExit, GeneratorExit):
            raise
        except Exception:
            raise Web服務不可用 from None

    def 讀取技能(self, 使用者識別碼: str, 技能識別碼: str) -> 技能詳情:
        """只讀目前 user 唯一可見且位於授權 root 的 bounded regular SKILL.md。

        參數：目前工作階段使用者識別碼與技能識別碼。
        回傳：回傳完整內容的安全技能詳情。
        例外：缺少、未授權、重複、連結、非一般檔案或超限皆統一為資源不存在；
        其他輸入輸出失敗拋服務不可用。
        副作用：查詢使用者權威並有界唯讀掃描技能根。
        """
        _驗證識別碼(技能識別碼)
        try:
            索引, 根目錄清單 = self._建立可見技能索引(使用者識別碼, 重複視為不存在=True)
            原始項目 = 索引.get(技能識別碼)
            if 原始項目 is None:
                raise Web資源不存在
            內容 = 原始項目["content"]
            return 技能詳情(_建立技能項目(技能識別碼, 原始項目), 內容)
        except (KeyboardInterrupt, SystemExit, GeneratorExit):
            raise
        except Web資源不存在:
            raise
        except (FileNotFoundError, PermissionError):
            raise Web資源不存在 from None
        except OSError:
            raise Web服務不可用 from None
        except Exception:
            raise Web服務不可用 from None

    def _建立可見技能索引(
        self, 使用者識別碼: str, 重複視為不存在: bool = False,
    ) -> tuple[dict[str, dict[str, str]], list[Path]]:
        """載入完整使用者上下文並建立唯一技能索引。

        參數：使用者識別碼與重複技能是否映射為不存在的政策。
        回傳：回傳技能識別到安全欄位的索引及脫離設定的根路徑清單。
        例外：使用者、設定或技能目錄資料無效時傳出對應例外。
        副作用：查詢使用者權威並有界唯讀掃描技能根。
        """
        _驗證識別碼(使用者識別碼)
        使用者 = self._使用者庫.建立使用者上下文(user_id=使用者識別碼)
        if type(使用者) is not 使用者上下文 or 使用者.user_id != 使用者識別碼:
            raise ValueError
        根目錄清單 = (
            取得技能根目錄清單({"_skill_roots": None})
            if 使用者.skill_roots is None else list(使用者.skill_roots)
        )
        根們 = tuple(Path(根目錄) for 根目錄 in 根目錄清單)
        啟用集合 = None if 使用者.enabled_skills is None else frozenset(使用者.enabled_skills)
        try:
            目錄結果 = 建立錨定安全技能目錄(
                根們, 啟用集合, 重複視為不存在=重複視為不存在,
                上限=技能目錄限制(
                    _最大技能檔案位元組, _最大技能索引項目數量,
                    _最大技能索引總位元組, _最大技能走訪項目數量,
                ),
            )
        except 技能目錄不存在:
            raise Web資源不存在 from None
        描述列 = 目錄結果.技能
        索引 = {描述.名稱: {
            "skill_name": 描述.名稱, "category": 描述.分類,
            "description": 描述.摘要, "content": 描述.內容,
        } for 描述 in 描述列}
        return 索引, list(根們)


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


def _建立技能項目(識別碼: str, 原始項目: dict[str, str]) -> 技能項目:
    """驗證既有 indexer 結果並重建不含 path 的自有 DTO。"""
    名稱, 分類, 描述 = 原始項目.get("skill_name"), 原始項目.get("category"), 原始項目.get("description")
    if 名稱 != 識別碼:
        raise ValueError
    _驗證識別碼(名稱)
    if type(分類) is not str or not 1 <= len(分類) <= 256:
        raise ValueError
    if type(描述) is not str or len(描述) > 1024:
        raise ValueError
    return 技能項目(識別碼, 名稱, 分類, 描述)


def _安全讀取技能(來源路徑: Path, 根目錄清單: list[Path]) -> str:
    """以共用描述器安全讀取器讀取 Web 技能詳情。

    參數：來源路徑與允許的技能根目錄清單。
    回傳：回傳有界且通過來源驗證的技能文字。
    例外：共用讀取器判定不存在時統一拋 Web 資源不存在。
    副作用：執行有界唯讀檔案系統操作。
    """
    try:
        return _共用安全讀取技能(
            來源路徑, tuple(根目錄清單), 最大位元組=_最大技能檔案位元組,
        )
    except 技能目錄不存在:
        raise Web資源不存在 from None


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
