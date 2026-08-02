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


class Web代理服務:
    """協調既有 runtime/repositories，並在 HTTP 前建立安全 DTO。"""

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
            if type(回覆) is not str:
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
                角色 = 訊息.get("role")
                if type(內容) is not str or len(內容.encode("utf-8")) > 1024 * 1024:
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

        參數為 current-session user ID 與 skill ID；返回完整內容安全 DTO。缺少、
        未授權、重複、路徑逃逸、symlink、非 regular 或超過 256 KiB 均統一為
        Web資源不存在；其他 I/O 失敗拋 Web服務不可用。
        """
        _驗證識別碼(技能識別碼)
        try:
            索引, 根目錄清單 = self._建立可見技能索引(使用者識別碼, 重複視為不存在=True)
            原始項目 = 索引.get(技能識別碼)
            if 原始項目 is None:
                raise Web資源不存在
            內容 = _安全讀取技能(Path(原始項目["path"]), 根目錄清單)
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
        """載入完整 user context 並以既有 indexer 建立唯一技能 ID map。"""
        _驗證識別碼(使用者識別碼)
        使用者 = self._使用者庫.建立使用者上下文(user_id=使用者識別碼)
        if type(使用者) is not 使用者上下文 or 使用者.user_id != 使用者識別碼:
            raise ValueError
        根目錄清單 = (
            取得技能根目錄清單({"_skill_roots": None})
            if 使用者.skill_roots is None else list(使用者.skill_roots)
        )
        索引: dict[str, dict[str, str]] = {}
        候選識別碼: set[str] = set()
        索引總位元組 = 0
        索引項目數量 = 0
        走訪預算 = _技能走訪預算(_最大技能走訪項目數量)
        for 根目錄 in 根目錄清單:
            根路徑 = Path(根目錄)
            剩餘候選上限 = _最大技能索引項目數量 + 1 - 索引項目數量
            for 技能路徑 in _走訪有界技能索引檔案(
                根路徑, "SKILL.md", 剩餘候選上限, 走訪預算,
            ):
                索引項目數量 += 1
                if 索引項目數量 > _最大技能索引項目數量:
                    raise ValueError
                if 使用者.enabled_skills is not None and 技能路徑.parent.name not in 使用者.enabled_skills:
                    continue
                候選ID = 技能路徑.parent.name
                if 候選ID in 候選識別碼:
                    if 重複視為不存在:
                        raise Web資源不存在
                    raise ValueError
                候選識別碼.add(候選ID)
                try:
                    內容 = _安全讀取技能(技能路徑, [根路徑])
                except Web資源不存在:
                    continue
                索引總位元組 += len(內容.encode("utf-8"))
                if 索引總位元組 > _最大技能索引總位元組:
                    raise ValueError
                項目 = _建立安全技能索引項目(技能路徑, 根路徑, 內容)
                if 項目 is None:
                    continue
                識別碼 = 項目.get("skill_name")
                _驗證識別碼(識別碼)
                if 識別碼 in 索引:
                    if 重複視為不存在:
                        raise Web資源不存在
                    raise ValueError
                索引[識別碼] = 項目
        return 索引, [Path(根目錄) for 根目錄 in 根目錄清單]


@dataclass(slots=True)
class _技能走訪預算:
    """跨所有授權 root 共享、會隨每個 directory entry 遞減的預算。"""

    剩餘項目數量: int


def _走訪有界技能索引檔案(
    技能根目錄: Path,
    檔名: str,
    候選上限: int,
    走訪預算: _技能走訪預算 | None = None,
) -> Iterator[Path]:
    """有界掃描每個 entry；僅完整讀完的 bounded directory batch 才排序。"""
    if 候選上限 <= 0:
        return
    if 走訪預算 is None:
        走訪預算 = _技能走訪預算(_最大技能走訪項目數量)
    已產出 = 0

    def 走訪目錄(目錄: Path) -> Iterator[Path]:
        """依名稱走訪完整小批次，目錄、候選與其他 entry 均消耗共享預算。"""
        nonlocal 已產出
        if 已產出 >= 候選上限:
            return
        try:
            with os.scandir(目錄) as 掃描器:
                項目清單 = []
                for 項目 in 掃描器:
                    項目清單.append(項目)
                    if len(項目清單) > 走訪預算.剩餘項目數量:
                        走訪預算.剩餘項目數量 = 0
                        raise ValueError("技能目錄走訪超過上限")
        except FileNotFoundError:
            return
        走訪預算.剩餘項目數量 -= len(項目清單)
        項目清單.sort(key=lambda 項目: 項目.name)
        for 項目 in 項目清單:
            if 已產出 >= 候選上限:
                return
            if 項目.name.startswith("."):
                continue
            路徑 = 目錄 / 項目.name
            if 項目.name == 檔名:
                已產出 += 1
                yield 路徑
                continue
            if 項目.is_dir(follow_symlinks=False):
                yield from 走訪目錄(路徑)

    yield from 走訪目錄(技能根目錄)


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


def _建立安全技能索引項目(技能路徑: Path, 根目錄: Path, 內容: str) -> dict[str, str] | None:
    """只從已安全 bounded 讀取的內容解析既有索引 metadata。"""
    相對路徑 = 技能路徑.relative_to(根目錄)
    if len(相對路徑.parts) < 2 or any(片段.startswith(".") for 片段 in 相對路徑.parts):
        return None
    前置資料 = 解析Markdown前置資料(內容)
    技能名稱 = str(前置資料.get("name") or 相對路徑.parts[-2])
    停用技能 = 讀取停用技能名稱集合()
    if 技能名稱 in 停用技能 or 相對路徑.parts[-2] in 停用技能:
        return None
    工具名稱: set[str] = set()
    if not 技能是否符合平台(前置資料, 取得目前平台名稱()) or not 技能是否符合工具條件(
        前置資料, 工具名稱, 建立可用工具集名稱集合(工具名稱)
    ):
        return None
    分類 = "/".join(相對路徑.parts[:-2]) if len(相對路徑.parts) > 2 else "general"
    return {
        "skill_name": 技能名稱,
        "category": 分類,
        "description": 截斷摘要文字(前置資料.get("description", "")),
        "path": str(技能路徑),
    }


def _安全讀取技能(來源路徑: Path, 根目錄清單: list[Path]) -> str:
    """逐 component descriptor-relative 開啟，並限制 regular file 與大小。"""
    解析來源 = 來源路徑.resolve(strict=True)
    解析根清單 = [根.resolve(strict=True) for 根 in 根目錄清單]
    符合根 = next((根 for 根 in 解析根清單 if 解析來源.is_relative_to(根)), None)
    if 符合根 is None:
        raise Web資源不存在
    初始狀態 = 來源路徑.lstat()
    if stat.S_ISLNK(初始狀態.st_mode) or not stat.S_ISREG(初始狀態.st_mode):
        raise Web資源不存在
    if 初始狀態.st_size > _最大技能檔案位元組:
        raise Web資源不存在
    相對片段 = 來源路徑.absolute().relative_to(符合根).parts
    目錄flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    目錄描述符 = os.open(符合根, 目錄flags)
    目前路徑 = 符合根
    try:
        for 片段 in 相對片段[:-1]:
            下一描述符 = os.open(片段, 目錄flags, dir_fd=目錄描述符)
            os.close(目錄描述符)
            目錄描述符 = 下一描述符
            目前路徑 /= 片段
        描述符 = os.open(
            相對片段[-1], os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0), dir_fd=目錄描述符,
        )
        try:
            開啟狀態 = os.fstat(描述符)
            目前目錄狀態 = os.stat(目前路徑, follow_symlinks=False)
            if (
                not stat.S_ISREG(開啟狀態.st_mode) or 開啟狀態.st_size > _最大技能檔案位元組
                or (初始狀態.st_dev, 初始狀態.st_ino) != (開啟狀態.st_dev, 開啟狀態.st_ino)
                or (目前目錄狀態.st_dev, 目前目錄狀態.st_ino)
                != (os.fstat(目錄描述符).st_dev, os.fstat(目錄描述符).st_ino)
            ):
                raise Web資源不存在
            with os.fdopen(描述符, "rb", closefd=False) as 檔案:
                原始內容 = 檔案.read(_最大技能檔案位元組 + 1)
        finally:
            os.close(描述符)
    finally:
        os.close(目錄描述符)
    if len(原始內容) > _最大技能檔案位元組:
        raise Web資源不存在
    return 原始內容.decode("utf-8")


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
