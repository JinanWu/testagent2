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


def 走訪有界技能檔案(
    技能根目錄: Path, 檔名: str, 候選上限: int,
    走訪預算: 技能走訪預算 | None = None,
) -> Iterator[Path]:
    """以根、檔名、候選上限及共享預算走訪。

    參數：技能根、檔名、候選上限與可選共享走訪預算。
    回傳：回傳依名稱排序的路徑迭代器。
    例外：目錄超限或輸入畸形時拋技能目錄錯誤。
    副作用：唯讀掃描目錄並遞減共享預算。
    """
    if not isinstance(技能根目錄, Path) or type(檔名) is not str or type(候選上限) is not int:
        raise 技能目錄錯誤("技能目錄不可用")
    if 候選上限 <= 0:
        return
    if 走訪預算 is None:
        走訪預算 = 技能走訪預算(最大技能走訪項目)
    已產出 = 0

    def 走訪目錄(目錄: Path) -> Iterator[Path]:
        """掃描單一路徑。

        參數：目錄為本次遞迴掃描路徑。
        回傳：回傳符合檔名的候選迭代器。
        例外：走訪項目超限時拋技能目錄錯誤。
        副作用：唯讀掃描目錄並更新外層計數。
        """
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
                        raise 技能目錄錯誤("技能目錄超過限制")
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
            elif 項目.is_dir(follow_symlinks=False):
                yield from 走訪目錄(路徑)

    yield from 走訪目錄(技能根目錄)


def 安全讀取技能(來源路徑: Path, 根目錄清單: tuple[Path, ...], *, 最大位元組: int = 最大技能檔案位元組) -> str:
    """從錨定目錄結果安全讀取指定技能。

    參數：來源路徑、技能根清單與單檔最大位元組。
    回傳：回傳符合來源的技能文字內容。
    例外：輸入、來源或內容不安全時統一拋技能目錄不存在。
    副作用：執行有界唯讀目錄與檔案輸入輸出。
    """
    try:
        if (not isinstance(來源路徑, Path) or type(根目錄清單) is not tuple
                or any(not isinstance(根, Path) for 根 in 根目錄清單)
                or type(最大位元組) is not int or 最大位元組 <= 0):
            raise 技能目錄不存在
        結果 = 建立錨定安全技能目錄(
            根目錄清單, None,
            上限=技能目錄限制(最大位元組, 最大技能索引項目,
                               max(2 * (最大位元組 + 1), 最大技能索引總位元組),
                               最大技能走訪項目),
        )
        目標 = _詞法絕對路徑(來源路徑)
        符合 = [項目 for 項目 in 結果.技能
              if _詞法絕對路徑(項目.來源目錄 / "SKILL.md") == 目標]
        if len(符合) != 1:
            raise 技能目錄不存在
        return 符合[0].內容
    except (技能目錄不存在, 技能目錄錯誤, FileNotFoundError, PermissionError, UnicodeError):
        raise 技能目錄不存在 from None


def 建立錨定安全技能目錄(
    根目錄清單: tuple[Path, ...], 啟用技能: frozenset[str] | None,
    *, 重複視為不存在: bool = False, 上限: 技能目錄限制 = 技能目錄限制(),
) -> 錨定安全技能目錄:
    """從同一錨定描述器掃描並建立安全技能目錄。

    參數：技能根、啟用技能、重複政策與資源限制。
    回傳：回傳脫離描述器生命週期的技能與根身分。
    例外：不安全來源、重複、競態或超限時拋技能目錄例外。
    副作用：讀取停用設定並執行有界唯讀檔案系統操作。
    """
    _驗證輸入(根目錄清單, 啟用技能, 重複視為不存在, 上限)
    描述表: dict[str, 安全技能描述] = {}
    根結果: list[安全技能根身分] = []
    走訪預算 = 技能走訪預算(上限.走訪項目)
    讀取預算 = _技能讀取預算(上限.索引總位元組)
    候選數 = 0
    停用 = 讀取停用技能名稱集合()

    設定根們 = tuple(sorted((_詞法絕對路徑(根) for 根 in 根目錄清單), key=str))
    已見根身分: set[tuple[int, int]] = set()
    for 設定路徑 in 設定根們:
        錨定根 = _開啟錨定技能根(設定路徑)
        根描述器 = 錨定根.描述器[-1]
        開啟前身分 = 錨定根.身分[-1]
        根錯誤: BaseException | None = None
        根雜湊項目: list[tuple[str, str, str]] = []
        try:
            已開啟 = os.fstat(根描述器)
            if 開啟前身分 != _根身分(已開啟):
                raise 技能目錄錯誤("技能目錄不可用")
            節點身分 = (已開啟.st_dev, 已開啟.st_ino)
            if 節點身分 in 已見根身分:
                raise 技能目錄錯誤("技能目錄根別名重複")
            已見根身分.add(節點身分)

            def 掃描目錄(目錄描述器: int, 相對片段: tuple[str, ...]) -> None:
                """從已持有描述器與相對片段遞迴掃描。

                參數：目錄描述器與相對於設定根的路徑片段。
                回傳：無回傳值。
                例外：競態、來源不安全或資源超限時拋技能目錄例外。
                副作用：唯讀掃描目錄、讀取技能並更新外層目錄資料。
                """
                nonlocal 候選數
                with os.scandir(目錄描述器) as 掃描器:
                    項目清單 = []
                    for 項目 in 掃描器:
                        項目清單.append(項目.name)
                        if len(項目清單) > 走訪預算.剩餘項目數量:
                            走訪預算.剩餘項目數量 = 0
                            raise 技能目錄錯誤("技能目錄超過限制")
                走訪預算.剩餘項目數量 -= len(項目清單)
                for 名 in sorted(項目清單):
                    if 名.startswith("."):
                        continue
                    try:
                        狀態 = os.stat(名, dir_fd=目錄描述器, follow_symlinks=False)
                    except FileNotFoundError:
                        continue
                    if 名 == "SKILL.md":
                        if not 相對片段:
                            continue
                        候選數 += 1
                        if 候選數 > 上限.索引項目:
                            raise 技能目錄錯誤("技能目錄超過限制")
                        if not stat.S_ISREG(狀態.st_mode) or 狀態.st_size > 上限.檔案位元組:
                            continue
                        try:
                            原始內容 = _從目錄讀取技能(目錄描述器, 名, 狀態, 上限.檔案位元組, 讀取預算)
                            內容 = 原始內容.decode("utf-8")
                        except (技能目錄不存在, UnicodeError):
                            continue
                        候選 = 相對片段[-1]
                        前置 = 解析Markdown前置資料(內容)
                        名稱 = str(前置.get("name") or 候選)
                        if 啟用技能 is not None and 名稱 not in 啟用技能:
                            continue
                        工具名稱: set[str] = set()
                        if (名稱 in 停用 or 候選 in 停用
                                or not 技能是否符合平台(前置, 取得目前平台名稱())
                                or not 技能是否符合工具條件(前置, 工具名稱, 建立可用工具集名稱集合(工具名稱))):
                            continue
                        if 名稱 in 描述表:
                            if 重複視為不存在:
                                raise 技能目錄不存在 from None
                            raise 技能目錄錯誤("技能目錄重複")
                        相對檔 = 相對片段 + (名,)
                        分類 = "/".join(相對片段[:-1]) if len(相對片段) > 1 else "general"
                        摘要 = 截斷摘要文字(前置.get("description", ""))
                        內容雜湊 = hashlib.sha256(原始內容).hexdigest()
                        來源 = 設定路徑.joinpath(*相對片段)
                        描述表[名稱] = 安全技能描述(名稱, 分類, 摘要, 內容, 內容雜湊, 來源)
                        根雜湊項目.append(("/".join(相對檔), 名稱, 內容雜湊))
                    elif stat.S_ISDIR(狀態.st_mode):
                        子描述器 = os.open(名, _目錄旗標(), dir_fd=目錄描述器)
                        子錯誤: BaseException | None = None
                        try:
                            if _根身分(狀態) != _根身分(os.fstat(子描述器)):
                                raise 技能目錄不存在
                            掃描目錄(子描述器, 相對片段 + (名,))
                        except BaseException as 錯誤:
                            子錯誤 = 錯誤
                            raise
                        finally:
                            _關閉且不覆蓋(子描述器, 子錯誤)

            掃描目錄(根描述器, ())
            _重驗錨定技能根(錨定根)
            根內容 = repr(tuple(sorted(根雜湊項目))).encode("utf-8")
            根結果.append(安全技能根身分(
                設定路徑, 已開啟.st_dev, 已開啟.st_ino, 已開啟.st_mode,
                hashlib.sha256(根內容).hexdigest(),
            ))
        except BaseException as 錯誤:
            根錯誤 = 錯誤
            raise
        finally:
            _關閉描述器們且不覆蓋(錨定根.描述器, 根錯誤)

    排序根 = tuple(sorted(根結果, key=lambda 根: str(根.路徑)))
    return 錨定安全技能目錄(tuple(描述表[名稱] for 名稱 in sorted(描述表)), 排序根)


def 建立安全技能目錄(
    根目錄清單: tuple[Path, ...], 啟用技能: frozenset[str] | None,
    *, 重複視為不存在: bool = False, 上限: 技能目錄限制 = 技能目錄限制(),
) -> tuple[安全技能描述, ...]:
    """透過錨定入口建立相容的技能描述序列。

    參數：技能根、啟用技能、重複政策與資源限制。
    回傳：回傳依名稱排序的安全技能描述。
    例外：錨定入口的技能目錄例外原樣傳出。
    副作用：執行有界唯讀檔案系統操作。
    """
    return 建立錨定安全技能目錄(
        根目錄清單, 啟用技能, 重複視為不存在=重複視為不存在, 上限=上限,
    ).技能
