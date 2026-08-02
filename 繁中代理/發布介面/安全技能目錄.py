"""提供 Web 與發布能力共用的錨定描述器安全技能目錄。

參數：公開入口接受技能根、授權集合與資源限制。
回傳：回傳已安全讀取且脫離描述器生命週期的目錄資料。
例外：來源不安全、資料衝突或超限時拋技能目錄例外。
副作用：匯入不存取檔案系統；建立目錄時執行有界唯讀掃描。
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
import stat
from typing import Iterator

from 繁中代理.技能索引器 import (
    取得目前平台名稱, 建立可用工具集名稱集合, 截斷摘要文字,
    技能是否符合平台, 技能是否符合工具條件, 解析Markdown前置資料,
    讀取停用技能名稱集合,
)


最大技能檔案位元組 = 256 * 1024
最大技能索引項目 = 1_000
最大技能索引總位元組 = 16 * 1024 * 1024
最大技能走訪項目 = 4_000


class 技能目錄不存在(RuntimeError):
    """表示技能缺少、重複或來源不安全。

    參數：沿用執行期錯誤建構參數。
    回傳：不適用。
    例外：由呼叫端建立並拋出本例外。
    副作用：建構本身無外部副作用。
    """


class 技能目錄錯誤(RuntimeError):
    """表示目錄形狀或資源界限無效。

    參數：沿用執行期錯誤建構參數。
    回傳：不適用。
    例外：由呼叫端建立並拋出本例外。
    副作用：建構本身無外部副作用。
    """


@dataclass(frozen=True, slots=True)
class 安全技能描述:
    """保存安全技能的內容與來源投影。

    參數：欄位為名稱、分類、摘要、內容、雜湊與來源目錄。
    回傳：建構不可變技能描述。
    例外：欄位驗證例外由資料類別機制原樣傳出。
    副作用：僅配置不可變資料，不執行輸入輸出。
    """
    名稱: str
    分類: str
    摘要: str
    內容: str
    內容sha256: str
    來源目錄: Path


@dataclass(frozen=True, slots=True)
class 安全技能根身分:
    """保存技能根的權威身分投影。

    參數：欄位為路徑、裝置、節點、模式與目錄雜湊。
    回傳：建構不可變根身分。
    例外：欄位驗證例外由資料類別機制原樣傳出。
    副作用：僅配置不可變資料，不執行輸入輸出。
    """
    路徑: Path
    裝置: int
    節點: int
    模式: int
    目錄雜湊: str


@dataclass(frozen=True, slots=True)
class 錨定安全技能目錄:
    """保存同一次錨定掃描產生的技能與根身分。

    參數：欄位為技能描述與根身分序列。
    回傳：建構不可變目錄結果。
    例外：欄位驗證例外由資料類別機制原樣傳出。
    副作用：不保留描述器且不執行輸入輸出。
    """
    技能: tuple[安全技能描述, ...]
    根身分: tuple[安全技能根身分, ...]


@dataclass(slots=True)
class 技能走訪預算:
    """保存跨根共享的走訪剩餘數。

    參數：欄位為剩餘項目數量。
    回傳：建構可遞減的預算物件。
    例外：欄位驗證例外由資料類別機制原樣傳出。
    副作用：僅配置記憶體資料。
    """
    剩餘項目數量: int


@dataclass(slots=True)
class _技能讀取預算:
    """保存雙讀與各次額外探測共用的總位元組預算。

    參數：欄位為剩餘位元組。
    回傳：建構可遞減的內部預算物件。
    例外：欄位驗證例外由資料類別機制原樣傳出。
    副作用：僅配置記憶體資料。
    """
    剩餘位元組: int


@dataclass(frozen=True, slots=True)
class _錨定技能根:
    """保存詞法路徑的完整元件描述器鏈。

    參數：欄位為設定路徑、元件、描述器與身分。
    回傳：建構不可變內部錨定根。
    例外：欄位驗證例外由資料類別機制原樣傳出。
    副作用：不接管描述器；呼叫端負責關閉。
    """
    設定路徑: Path
    元件: tuple[str, ...]
    描述器: tuple[int, ...]
    身分: tuple[tuple[int, int, int], ...]


@dataclass(frozen=True, slots=True)
class 技能目錄限制:
    """保存技能目錄的各項資源上限。

    參數：欄位為檔案、候選、總讀取與走訪上限。
    回傳：建構不可變限制物件。
    例外：欄位驗證例外由資料類別機制原樣傳出。
    副作用：僅配置不可變資料。
    """
    檔案位元組: int = 最大技能檔案位元組
    索引項目: int = 最大技能索引項目
    索引總位元組: int = 最大技能索引總位元組
    走訪項目: int = 最大技能走訪項目
def _驗證輸入(根目錄清單, 啟用技能, 重複視為不存在, 上限) -> None:
    """驗證技能目錄公開輸入。

    參數：技能根、啟用技能、重複政策與資源限制。
    回傳：無回傳值。
    例外：任何輸入形狀或界限無效時拋技能目錄錯誤。
    副作用：無外部副作用。
    """
    if (type(根目錄清單) is not tuple or any(not isinstance(根, Path) for 根 in 根目錄清單)
            or (啟用技能 is not None and (type(啟用技能) is not frozenset
                or any(type(名稱) is not str for 名稱 in 啟用技能)))
            or type(重複視為不存在) is not bool or type(上限) is not 技能目錄限制
            or any(type(值) is not int or 值 <= 0 for 值 in (
                上限.檔案位元組, 上限.索引項目, 上限.索引總位元組, 上限.走訪項目))):
        raise 技能目錄錯誤("技能目錄不可用")


def _目錄旗標() -> int:
    """建立平台可用的目錄安全旗標。

    參數：無參數。
    回傳：回傳唯讀、目錄及不可跟隨連結的旗標整數。
    例外：無預期例外。
    副作用：無外部副作用。
    """
    return os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)


def _詞法絕對路徑(路徑: Path) -> Path:
    """建立不解析符號連結的詞法絕對路徑。

    參數：待正規化的路徑。
    回傳：回傳展開家目錄並消去點元件的絕對路徑。
    例外：路徑轉換的系統例外原樣傳出。
    副作用：不存取檔案系統。
    """
    return Path(os.path.abspath(os.path.expanduser(os.fspath(路徑))))


def _開啟錨定技能根(設定路徑: Path) -> _錨定技能根:
    """從檔案系統根逐元件安全開啟技能根。

    參數：已詞法正規化的設定路徑。
    回傳：回傳完整且由呼叫端持有的描述器鏈。
    例外：元件不是目錄、為連結或發生競態時拋技能目錄錯誤。
    副作用：開啟目錄描述器；失敗時關閉已取得的描述器。
    """
    元件 = tuple(設定路徑.parts[1:])
    描述器: list[int] = []
    身分: list[tuple[int, int, int]] = []
    失敗: BaseException | None = None
    try:
        根描述器 = os.open(設定路徑.anchor, _目錄旗標())
        描述器.append(根描述器)
        根狀態 = os.fstat(根描述器)
        if not stat.S_ISDIR(根狀態.st_mode):
            raise 技能目錄錯誤("技能目錄不可用")
        身分.append(_根身分(根狀態))
        for 元件名 in 元件:
            開啟前 = os.stat(元件名, dir_fd=描述器[-1], follow_symlinks=False)
            if stat.S_ISLNK(開啟前.st_mode) or not stat.S_ISDIR(開啟前.st_mode):
                raise 技能目錄錯誤("技能目錄不可用")
            子描述器 = os.open(元件名, _目錄旗標(), dir_fd=描述器[-1])
            描述器.append(子描述器)
            已開啟 = os.fstat(子描述器)
            if _根身分(開啟前) != _根身分(已開啟):
                raise 技能目錄錯誤("技能目錄不可用")
            身分.append(_根身分(已開啟))
        return _錨定技能根(設定路徑, 元件, tuple(描述器), tuple(身分))
    except BaseException as 錯誤:
        失敗 = 錯誤
        raise
    finally:
        if 失敗 is not None:
            _關閉描述器們且不覆蓋(tuple(描述器), 失敗)


def _重驗錨定技能根(錨定根: _錨定技能根) -> None:
    """依保留的父元件描述器重驗整條技能根。

    參數：包含完整描述器鏈與預期身分的錨定根。
    回傳：無回傳值。
    例外：名稱或描述器身分漂移時拋技能目錄錯誤。
    副作用：唯讀查詢描述器與目錄項目狀態。
    """
    if len(錨定根.描述器) != len(錨定根.身分) or len(錨定根.元件) + 1 != len(錨定根.描述器):
        raise 技能目錄錯誤("技能目錄不可用")
    for 描述器值, 預期 in zip(錨定根.描述器, 錨定根.身分, strict=True):
        if _根身分(os.fstat(描述器值)) != 預期:
            raise 技能目錄錯誤("技能目錄不可用")
    for 索引, 元件名 in enumerate(錨定根.元件):
        路徑狀態 = os.stat(元件名, dir_fd=錨定根.描述器[索引], follow_symlinks=False)
        if _根身分(路徑狀態) != 錨定根.身分[索引 + 1]:
            raise 技能目錄錯誤("技能目錄不可用")


def _根身分(狀態: os.stat_result) -> tuple[int, int, int]:
    """投影目錄根身分。

    參數：檔案系統狀態結果。
    回傳：回傳裝置、節點與模式三元組。
    例外：必要欄位缺少時屬性例外原樣傳出。
    副作用：無外部副作用。
    """
    return (狀態.st_dev, 狀態.st_ino, 狀態.st_mode)


def _檔案身分(狀態: os.stat_result) -> tuple[int, int, int, int, int]:
    """投影檔案讀取競態身分。

    參數：檔案系統狀態結果。
    回傳：回傳裝置、節點、模式、大小與修改時間五元組。
    例外：必要欄位缺少時屬性例外原樣傳出。
    副作用：無外部副作用。
    """
    return (狀態.st_dev, 狀態.st_ino, 狀態.st_mode, 狀態.st_size, 狀態.st_mtime_ns)


def _關閉且不覆蓋(描述器: int, 原始錯誤: BaseException | None) -> None:
    """依 POSIX 所有權政策關閉單一描述器且不覆蓋原始錯誤。

    參數：待關閉描述器與可選原始錯誤。
    回傳：無回傳值。
    例外：沒有原始錯誤時傳出關閉失敗，否則保留原始錯誤。
    副作用：恰呼叫一次 ``close``；若呼叫拋錯，其釋放結果視為不明確，絕不以
    ``fstat`` 身分或原始 ``close`` 重試，避免同 FD、同 inode 重用時誤關他人描述器。
    """
    try:
        os.close(描述器)
    except BaseException:
        if 原始錯誤 is None:
            raise


def _關閉描述器們且不覆蓋(描述器: tuple[int, ...], 原始錯誤: BaseException | None) -> None:
    """逆序關閉完整錨定描述器鏈。

    參數：描述器序列與可選原始錯誤。
    回傳：無回傳值。
    例外：沒有原始錯誤時於全數關閉後傳出第一個關閉錯誤。
    副作用：依逆序對每個確定持有的描述器各呼叫一次 ``close``；任何不明確結果
    均不重試，仍繼續處理其餘確定持有的描述器。
    """
    關閉錯誤: BaseException | None = None
    for 描述器值 in reversed(描述器):
        try:
            _關閉且不覆蓋(描述器值, 原始錯誤)
        except BaseException as 錯誤:
            if 原始錯誤 is None and 關閉錯誤 is None:
                關閉錯誤 = 錯誤
    if 關閉錯誤 is not None:
        raise 關閉錯誤


__all__ = [
    "安全技能描述", "安全技能根身分", "錨定安全技能目錄", "技能目錄不存在", "技能目錄錯誤",
    "技能目錄限制", "技能走訪預算", "走訪有界技能檔案", "安全讀取技能",
    "建立安全技能目錄", "建立錨定安全技能目錄",
]
