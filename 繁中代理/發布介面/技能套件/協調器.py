"""以描述元安全隔離孤兒技能套件並執行有界啟動協調。

參數：不適用；公開類別另接受套件根、保存期限、時鐘、收據及資料庫連線。
回傳：不適用；公開方法回傳隔離路徑或不可變協調結果。
例外：不安全輸入或資源失敗映射為固定協調錯誤，控制例外保持原物件傳出。
副作用：可掃描、隔離、刪除套件，並在呼叫端 SQLite 交易內補寫收據。
"""

from __future__ import annotations

import ctypes
from dataclasses import dataclass
import errno
import hashlib
import math
import os
from os import close as _系統關閉
from pathlib import Path, PurePosixPath
import re
import sqlite3
import stat
import sys
import time
import traceback
from typing import Callable, NoReturn
import uuid

from .安全複製 import 技能套件最大總位元組數, 限制
from .發布器 import 套件發布收據, 已驗證技能套件清單, 驗證已發布技能套件清單, _讀取有界檔案
from .儲存庫 import 套件收據儲存庫


class 技能套件協調錯誤(RuntimeError):
    """表示隔離或啟動協調無法安全完成。

    參數：沿用 ``RuntimeError`` 的固定公開訊息參數。
    回傳：建立可辨識技能套件協調失敗的例外物件。
    例外：建構本身只可能傳出基底例外的標準錯誤。
    副作用：建立例外物件不存取檔案系統或資料庫。
    """


@dataclass(frozen=True, slots=True)
class 啟動協調結果:
    """保存本輪新增收據、隔離及刪除的套件識別碼。

    參數：三個識別碼元組分別描述補據、隔離與刪除結果。
    回傳：建立不可變且可脫離資源生命週期保存的協調結果。
    例外：資料類別建構不主動拋出業務例外。
    副作用：只保存不可變純量，不修改檔案系統或資料庫。
    """

    已補收據: tuple[str, ...]
    已隔離: tuple[str, ...]
    已刪除: tuple[str, ...]


@dataclass(slots=True)
class _協調預算:
    """保存單次協調 authority 的共享列舉項目及完整讀取額度。

    參數：可使用預設共享位元組額度及零起始列舉數建構。
    回傳：建構後提供有界列舉及讀取方法。
    例外：額度不合法或超限時由方法傳出一般作業系統錯誤。
    副作用：方法會遞增物件內的共享計數並扣除剩餘位元組數。
    """

    剩餘位元組數: int = 技能套件最大總位元組數
    已列舉項目數: int = 0

    def 列舉(self, 目錄描述元: int) -> tuple[str, ...]:
        """以 descriptor iterator 收集至多 256 個名稱；第 257 項立即關閉失敗。

        參數：目錄描述元是呼叫端持有且已釘選的目錄 fd。
        回傳：共享上限內的 detached 名稱 tuple。
        例外：第 257 個共享項目或掃描、關閉失敗時傳出一般錯誤。
        副作用：消耗共享項目額度，且一律關閉本方法建立的掃描 iterator。
        """
        名稱列: list[str] = []
        迭代器 = os.scandir(目錄描述元)
        try:
            for 項目 in 迭代器:
                self.已列舉項目數 += 1
                if self.已列舉項目數 > 限制().最大檔案數:
                    raise OSError
                名稱列.append(項目.name)
        finally:
            _執行清理(迭代器.close)
        return tuple(名稱列)

    def 讀取(self, 目錄描述元: int, 名稱: str, 單檔上限: int) -> tuple[bytes, os.stat_result]:
        """由同一剩餘額度完整讀取一檔；不足時在配置內容前關閉失敗。

        參數：目錄描述元與名稱定位檔案；單檔上限限制本次讀取配置。
        回傳：檔案內容位元組與讀取時釘選的 ``stat`` 結果。
        例外：型別、額度、檔案身分或讀取失效時傳出一般作業系統錯誤。
        副作用：開啟並關閉檔案描述元，扣除共享剩餘位元組額度。
        """
        if type(單檔上限) is not int or 單檔上限 < 0 or self.剩餘位元組數 < 0:
            raise OSError
        資料, 資訊 = _讀取有界檔案(
            目錄描述元, 名稱, min(單檔上限, self.剩餘位元組數),
        )
        self.剩餘位元組數 -= len(資料)
        return 資料, 資訊


@dataclass(frozen=True, slots=True)
class _樹投影項目:
    """保存一個已釘選檔案系統項目的完整 detached 身分。

    參數：相對部件、種類、模式、裝置、inode、大小、修改奈秒與必要雜湊。
    回傳：建構後提供不可變的 exact revalidation 欄位。
    例外：本資料類別不主動產生例外。
    副作用：無；不保留路徑物件或開啟的描述元。
    """

    相對部件: tuple[str, ...]
    種類: str
    模式: int
    裝置: int
    節點: int
    大小: int
    修改奈秒: int
    雜湊: str | None


@dataclass(frozen=True, slots=True)
class _已重驗套件:
    """保存一次 descriptor 驗證所得的 detached receipt 與 authoritative projection。

    參數：收據、清單、完整樹投影、根身分及修改時間皆來自同次釘選驗證。
    回傳：建構後提供後續決策與 mutation 使用的不可變資料。
    例外：本資料類別不主動產生例外。
    副作用：無；不持有任何檔案描述元。
    """

    收據: 套件發布收據
    投影: 已驗證技能套件清單
    樹投影: tuple[_樹投影項目, ...]
    根身分: tuple[int, int]
    修改奈秒: int


_識別碼 = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}\Z")
_控制例外 = (KeyboardInterrupt, SystemExit, GeneratorExit)
_不可跟隨 = getattr(os, "O_NOFOLLOW", 0)
_僅目錄 = getattr(os, "O_DIRECTORY", 0)
_固定錯誤 = "技能套件協調錯誤"


def _拒絕() -> NoReturn:
    """以不帶錯誤鏈的固定公開錯誤關閉失敗。

    參數：無。
    回傳：永不正常回傳。
    例外：固定拋出 ``技能套件協調錯誤``，且不保留原始例外鏈。
    副作用：只建立並拋出公開例外，不修改外部資源。
    """
    raise 技能套件協調錯誤(_固定錯誤) from None


def _清框架(錯誤: BaseException) -> None:
    """盡力清除內部失敗框架，且不覆蓋原結果。

    參數：錯誤是待清除 traceback frame locals 的例外物件。
    回傳：清理完成或清理失敗被抑制後回傳 ``None``。
    例外：不向呼叫端傳出清理階段例外。
    副作用：可能清空既有 traceback frames 對區域物件的參照。
    """
    try:
        traceback.clear_frames(錯誤.__traceback__)
    except BaseException:
        pass


def _執行清理(動作: Callable[[], object]) -> None:
    """執行關閉動作；若已有失敗，清理錯誤不得覆蓋其 identity 或 args。

    參數：動作是不需參數的清理 callable。
    回傳：清理成功或已有原失敗時回傳 ``None``。
    例外：沒有原失敗時傳出清理錯誤；否則保留原失敗。
    副作用：執行指定清理，並盡力清除被抑制錯誤的框架。
    """
    原錯誤 = sys.exception()
    try:
        動作()
    except BaseException as 清理錯誤:
        _清框架(清理錯誤)
        if 原錯誤 is None:
            raise


def _關閉描述元(描述元: int) -> None:
    """關閉 fd，並在例外展開期間保留第一個失敗。

    參數：描述元是本模組擁有且待關閉的 fd。
    回傳：成功後回傳 ``None``。
    例外：正常路徑傳出關閉錯誤；展開既有例外時不覆蓋原錯誤。
    副作用：釋放一個作業系統描述元。
    """
    _執行清理(lambda: _系統關閉(描述元))


def _回復儲存點(資料庫: sqlite3.Connection, 名稱: str, *, 回復外層: bool) -> None:
    """盡力回復並釋放協調器儲存點；清理不得遮蔽進入時的失敗。

    參數：資料庫與名稱定位自有儲存點；回復外層表示協調器曾建立外層交易。
    回傳：清理完成後回傳 ``None``。
    例外：保留呼叫本函式前正在處理的第一個失敗。
    副作用：執行回復、釋放，並視需要回滾協調器自建外層交易。
    """
    _執行清理(lambda: 資料庫.execute(f'ROLLBACK TO SAVEPOINT "{名稱}"'))
    _執行清理(lambda: 資料庫.execute(f'RELEASE SAVEPOINT "{名稱}"'))
    if 回復外層:
        _執行清理(lambda: 資料庫.execute("ROLLBACK"))


def _開安全絕對目錄(路徑: Path) -> int:
    """由檔案系統根逐層不跟隨連結地開啟目錄。

    參數：路徑是待釘選的詞彙絕對或相對目錄路徑。
    回傳：呼叫端取得所有權的最終目錄描述元。
    例外：非法部件、連結、開啟或中途關閉失敗皆直接傳出。
    副作用：逐層開啟並關閉描述元；close 結果不明的舊 fd 不會被猜測重試。
    """
    絕對 = Path(os.path.abspath(os.fspath(路徑)))
    描述元 = os.open("/", os.O_RDONLY | _僅目錄)
    try:
        for 部件 in 絕對.parts[1:]:
            if 部件 in {"", ".", ".."}:
                raise OSError
            下一個 = os.open(部件, os.O_RDONLY | _僅目錄 | _不可跟隨, dir_fd=描述元)
            # open 成功後立即把唯一 owned slot 轉交 next；因此 close 舊 fd 前後的
            # 任意非同步控制都只會由外層關閉 next，絕不重關結果不明的 stale old。
            舊描述元, 描述元 = 描述元, 下一個
            _關閉描述元(舊描述元)
        return 描述元
    except BaseException:
        _關閉描述元(描述元)
        raise


def _不可覆寫改名at(來源父: int, 來源: str, 目標父: int, 目標: str) -> None:
    """只使用描述元相對的原子不可覆寫原語移動目錄。

    參數：來源與目標父 fd 搭配各自單一目錄名稱。
    回傳：移動成功後回傳 ``None``。
    例外：平台不支援、目的碰撞、權限或系統呼叫失敗時傳出一般錯誤。
    副作用：原子移動目錄；Darwin 上短暫放寬並恢復來源目錄模式。
    """
    函式庫 = ctypes.CDLL(None, use_errno=True)
    if sys.platform == "darwin" and hasattr(函式庫, "renameatx_np"):
        函式 = 函式庫.renameatx_np
        函式.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
        函式.restype = ctypes.c_int
        # Darwin 會拒絕移動模式 0555 且非空的目錄；以釘選 fd 暫時只放寬
        # 目錄 inode，rename 後立即恢復，內容檔仍維持 0444。
        來源描述元 = os.open(來源, os.O_RDONLY | _僅目錄 | _不可跟隨, dir_fd=來源父)
        try:
            if stat.S_IMODE(os.fstat(來源描述元).st_mode) != 0o555:
                raise OSError
            os.fchmod(來源描述元, 0o755)
            try:
                結果 = 函式(來源父, os.fsencode(來源), 目標父, os.fsencode(目標), 0x00000004)
            finally:
                _執行清理(lambda: os.fchmod(來源描述元, 0o555))
        finally:
            _關閉描述元(來源描述元)
    elif sys.platform.startswith("linux") and hasattr(函式庫, "renameat2"):
        函式 = 函式庫.renameat2
        函式.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
        函式.restype = ctypes.c_int
        結果 = 函式(來源父, os.fsencode(來源), 目標父, os.fsencode(目標), 1)
    else:
        raise OSError(errno.ENOTSUP, "atomic no-replace rename unsupported")
    if 結果:
        錯誤碼 = ctypes.get_errno()
        raise OSError(錯誤碼, os.strerror(錯誤碼))


def _投影索引(投影: 已驗證技能套件清單) -> tuple[dict[str, object], set[str]]:
    """建立 copied-file 索引及其唯一允許目錄集合。

    參數：投影是已通過發布清單驗證的 detached 資料。
    回傳：以相對路徑為鍵的檔案索引，以及包含根的允許目錄集合。
    例外：重複路徑或無效投影由上游驗證拒絕，本函式不另轉譯。
    副作用：只配置新的索引與集合，不讀寫檔案系統。
    """
    索引 = {項目.path: 項目 for 項目 in 投影.copied_files}
    目錄 = {""}
    for 路徑 in 索引:
        部件 = PurePosixPath(路徑).parts
        目錄.update("/".join(部件[:位置]) for 位置 in range(1, len(部件)))
    return 索引, 目錄


def _建立樹投影項目(
    相對部件: tuple[str, ...], 種類: str, 資訊: os.stat_result, 雜湊: str | None,
) -> _樹投影項目:
    """把一次穩定 stat 與必要內容雜湊複製成 detached tree projection。

    參數：相對部件、項目種類、穩定 stat 與一般檔案的 SHA-256 雜湊。
    回傳：不持有 descriptor 或 DirEntry 的不可變樹投影項目。
    例外：欄位無法轉成預期純量時直接傳出原例外。
    副作用：無。
    """
    return _樹投影項目(
        相對部件, 種類, stat.S_IMODE(資訊.st_mode), 資訊.st_dev, 資訊.st_ino,
        資訊.st_size, 資訊.st_mtime_ns, 雜湊,
    )


def _資訊符合投影(資訊: os.stat_result, 項目: _樹投影項目) -> bool:
    """精確比較可見 stat 與 preflight detached projection 欄位。

    參數：資訊是 no-follow 或釘選 fd 的 stat；項目是預檢建立的投影。
    回傳：種類、模式、dev、ino、size 與 mtime 全部相同時為真。
    例外：不主動產生例外。
    副作用：無。
    """
    是預期種類 = stat.S_ISDIR(資訊.st_mode) if 項目.種類 == "目錄" else stat.S_ISREG(資訊.st_mode)
    return 是預期種類 and (
        stat.S_IMODE(資訊.st_mode), 資訊.st_dev, 資訊.st_ino, 資訊.st_size, 資訊.st_mtime_ns,
    ) == (項目.模式, 項目.裝置, 項目.節點, 項目.大小, 項目.修改奈秒)


def _重驗套件(
    父描述元: int, 名稱: str, 父路徑: Path, 預算: _協調預算 | None = None,
) -> _已重驗套件:
    """在單一釘選父 fd 下以共享預算重驗套件。

    參數：父描述元、名稱與父路徑定位套件；預算可共享或由本函式建立。
    回傳：分離的收據、清單投影、根身分及修改時間。
    例外：結構、模式、摘要、身分、額度或描述元操作不符時傳出一般錯誤。
    副作用：有界讀取完整套件樹並消耗共享額度；不修改檔案系統。
    """
    if 預算 is None:
        預算 = _協調預算()
    前資訊 = os.stat(名稱, dir_fd=父描述元, follow_symlinks=False)
    if not stat.S_ISDIR(前資訊.st_mode) or stat.S_IMODE(前資訊.st_mode) != 0o555:
        raise OSError
    根 = os.open(名稱, os.O_RDONLY | _僅目錄 | _不可跟隨, dir_fd=父描述元)
    try:
        根資訊 = os.fstat(根)
        if (前資訊.st_dev, 前資訊.st_ino) != (根資訊.st_dev, 根資訊.st_ino):
            raise OSError
        原文, 清單資訊 = 預算.讀取(根, "manifest.json", 限制().最大總位元組數)
        if 清單資訊.st_nlink != 1 or stat.S_IMODE(清單資訊.st_mode) != 0o444:
            raise OSError
        投影 = 驗證已發布技能套件清單(原文)
        if 投影.bundle_id != 名稱:
            raise OSError
        索引, 預期目錄 = _投影索引(投影)
        已見: set[str] = set()
        位元組 = 0
        樹投影: list[_樹投影項目] = [_建立樹投影項目((), "目錄", 根資訊, None)]

        def 走訪(目錄描述元: int, 前綴: str, 部件前綴: tuple[str, ...]) -> None:
            """只進入清單衍生的預期目錄並驗證每個一般檔。

            參數：目錄描述元定位目前層；前綴是其套件相對路徑。
            回傳：完成目前子樹驗證後回傳 ``None``。
            例外：未知項目、模式、身分、摘要或額度不符時傳出一般錯誤。
            副作用：遞迴開啟並關閉子目錄，更新外層已見集合與位元組計數。
            """
            nonlocal 位元組
            for 子名 in sorted(預算.列舉(目錄描述元)):
                相對 = 子名 if not 前綴 else f"{前綴}/{子名}"
                相對部件 = (*部件前綴, 子名)
                資訊 = os.stat(子名, dir_fd=目錄描述元, follow_symlinks=False)
                if stat.S_ISDIR(資訊.st_mode) and 相對 in 預期目錄:
                    if stat.S_IMODE(資訊.st_mode) != 0o555:
                        raise OSError
                    子 = os.open(子名, os.O_RDONLY | _僅目錄 | _不可跟隨, dir_fd=目錄描述元)
                    try:
                        釘選 = os.fstat(子)
                        if (資訊.st_dev, 資訊.st_ino) != (釘選.st_dev, 釘選.st_ino):
                            raise OSError
                        投影位置 = len(樹投影)
                        樹投影.append(_建立樹投影項目(相對部件, "目錄", 釘選, None))
                        走訪(子, 相對, 相對部件)
                        完成 = os.fstat(子)
                        if not _資訊符合投影(完成, 樹投影[投影位置]):
                            raise OSError
                        樹投影[投影位置] = _建立樹投影項目(相對部件, "目錄", 完成, None)
                    finally:
                        _關閉描述元(子)
                elif stat.S_ISREG(資訊.st_mode) and 相對 == "manifest.json" and not 前綴:
                    if not (
                        資訊.st_dev, 資訊.st_ino, 資訊.st_size, 資訊.st_mtime_ns,
                    ) == (
                        清單資訊.st_dev, 清單資訊.st_ino, 清單資訊.st_size, 清單資訊.st_mtime_ns,
                    ):
                        raise OSError
                    樹投影.append(_建立樹投影項目(
                        相對部件, "檔案", 清單資訊, hashlib.sha256(原文).hexdigest(),
                    ))
                    continue
                elif stat.S_ISREG(資訊.st_mode) and 相對 in 索引:
                    項目 = 索引[相對]
                    if 資訊.st_nlink != 1 or stat.S_IMODE(資訊.st_mode) != 0o444:
                        raise OSError
                    資料, 穩定 = 預算.讀取(目錄描述元, 子名, 項目.size_bytes)  # type: ignore[attr-defined]
                    摘要 = hashlib.sha256(資料).hexdigest()
                    if (
                        穩定.st_nlink != 1
                        or (資訊.st_dev, 資訊.st_ino) != (穩定.st_dev, 穩定.st_ino)
                        or 摘要 != 項目.sha256  # type: ignore[attr-defined]
                    ):
                        raise OSError
                    位元組 += len(資料)
                    已見.add(相對)
                    樹投影.append(_建立樹投影項目(相對部件, "檔案", 穩定, 摘要))
                else:
                    raise OSError
        走訪(根, "", ())
        後資訊 = os.stat(名稱, dir_fd=父描述元, follow_symlinks=False)
        if ((後資訊.st_dev, 後資訊.st_ino) != (根資訊.st_dev, 根資訊.st_ino)
                or not _資訊符合投影(後資訊, 樹投影[0])
                or 已見 != set(索引) or 位元組 != 投影.total_bytes):
            raise OSError
        樹投影[0] = _建立樹投影項目((), "目錄", 後資訊, None)
        收據 = 套件發布收據(
            投影.bundle_id, f"{投影.bundle_id}/manifest.json", 投影.manifest_digest,
            投影.bundle_hash, 投影.total_bytes, Path(父路徑) / 投影.bundle_id,
        )
        return _已重驗套件(
            收據, 投影, tuple(樹投影), (根資訊.st_dev, 根資訊.st_ino), 後資訊.st_mtime_ns,
        )
    finally:
        _關閉描述元(根)


def _安全刪樹(父: int, 名稱: str, 投影: tuple[_樹投影項目, ...]) -> None:
    """完整重驗 detached projection 後依反向順序刪除樹。

    參數：父 fd 與名稱定位樹根；投影列出 preflight 釘選的每個目錄及檔案。
    回傳：投影全部相符且完整刪除後回傳 ``None``。
    例外：缺項、多項、種類、身分、內容、模式或刪除操作不符時傳出一般錯誤。
    副作用：重驗階段只做有界列舉及讀取；通過後才調整模式並 unlink/rmdir。
    """
    if not 投影 or 投影[0].相對部件 != () or 投影[0].種類 != "目錄":
        raise OSError
    項目索引 = {項目.相對部件: 項目 for 項目 in 投影}
    if len(項目索引) != len(投影):
        raise OSError
    子項: dict[tuple[str, ...], dict[str, _樹投影項目]] = {}
    for 項目 in 投影[1:]:
        父項目 = 項目索引.get(項目.相對部件[:-1])
        if not 項目.相對部件 or 父項目 is None or 父項目.種類 != "目錄":
            raise OSError
        同層 = 子項.setdefault(項目.相對部件[:-1], {})
        if 項目.相對部件[-1] in 同層:
            raise OSError
        同層[項目.相對部件[-1]] = 項目
    目錄描述元: dict[tuple[str, ...], int] = {}
    已關閉: set[tuple[str, ...]] = set()
    已開始刪除 = False
    根 = os.open(名稱, os.O_RDONLY | _僅目錄 | _不可跟隨, dir_fd=父)
    目錄描述元[()] = 根

    def 驗證目錄(部件: tuple[str, ...]) -> None:
        """以投影子項數加一為硬界線驗證目前釘選目錄。

        參數：部件定位已開啟目錄描述元及其 exact expected children。
        回傳：目前子樹的 metadata 與檔案雜湊全部相符後回傳 ``None``。
        例外：第 expected+1 項即關閉拒絕，任何 projection 差異亦直接拒絕。
        副作用：只讀取並保留預期目錄 fd；不變更檔案系統。
        """
        目前 = 目錄描述元[部件]
        目前項目 = 項目索引[部件]
        if not _資訊符合投影(os.fstat(目前), 目前項目):
            raise OSError
        預期 = 子項.get(部件, {})
        名稱列: list[str] = []
        迭代器 = os.scandir(目前)
        try:
            for 掃描項目 in 迭代器:
                名稱列.append(掃描項目.name)
                if len(名稱列) > len(預期):
                    raise OSError
        finally:
            _執行清理(迭代器.close)
        if len(名稱列) != len(預期) or set(名稱列) != set(預期):
            raise OSError
        for 子名 in sorted(名稱列):
            子投影 = 預期[子名]
            可見 = os.stat(子名, dir_fd=目前, follow_symlinks=False)
            if not _資訊符合投影(可見, 子投影):
                raise OSError
            if 子投影.種類 == "目錄":
                子描述元 = os.open(子名, os.O_RDONLY | _僅目錄 | _不可跟隨, dir_fd=目前)
                目錄描述元[子投影.相對部件] = 子描述元
                if not _資訊符合投影(os.fstat(子描述元), 子投影):
                    raise OSError
                驗證目錄(子投影.相對部件)
            elif 子投影.種類 == "檔案":
                資料, 穩定 = _讀取有界檔案(目前, 子名, 子投影.大小)
                if (
                    穩定.st_nlink != 1
                    or not _資訊符合投影(穩定, 子投影)
                    or hashlib.sha256(資料).hexdigest() != 子投影.雜湊
                ):
                    raise OSError
            else:
                raise OSError

    try:
        驗證目錄(())
        # 所有 projected entries 都已在不修改檔案系統的階段精確重驗；以下才開始 mutation。
        for 部件, 描述元 in 目錄描述元.items():
            os.fchmod(描述元, 0o700)
        for 項目 in reversed(投影):
            if 項目.種類 == "檔案":
                os.unlink(項目.相對部件[-1], dir_fd=目錄描述元[項目.相對部件[:-1]])
                已開始刪除 = True
            elif 項目.相對部件:
                已關閉.add(項目.相對部件)
                _關閉描述元(目錄描述元[項目.相對部件])
                os.rmdir(項目.相對部件[-1], dir_fd=目錄描述元[項目.相對部件[:-1]])
        已關閉.add(())
        _關閉描述元(根)
        os.rmdir(名稱, dir_fd=父)
    finally:
        if not 已開始刪除:
            for 部件, 描述元 in 目錄描述元.items():
                if 部件 not in 已關閉:
                    _執行清理(lambda 描述元=描述元: os.fchmod(描述元, 0o555))
        for 部件, 描述元 in reversed(tuple(目錄描述元.items())):
            if 部件 not in 已關閉:
                _關閉描述元(描述元)


class 技能套件協調器:
    """以固定根、明確 retention 與可注入時鐘協調技能套件。

    參數：建構時接收技能套件根、孤兒保存期限與可替換時鐘。
    回傳：建立可執行隔離與有界啟動協調的服務物件。
    例外：建構輸入不符固定契約時拋出 ``技能套件協調錯誤``。
    副作用：建構只保存設定；公開操作才會存取檔案系統與SQLite。
    """

    def __init__(self, 根目錄: str | Path, *, 孤兒保留秒數: float, 時鐘=time.time) -> None:
        """保存 lexical absolute 根與已驗證 retention；不建立目錄。

        參數：根目錄定位發布套件；孤兒保留秒數控制清理期限；時鐘提供目前時間。
        回傳：無回傳值。
        例外：保存期限或路徑契約無效時拋出 ``技能套件協調錯誤``。
        副作用：只保存正規路徑與純量設定，不開啟目錄或資料庫。
        """
        if type(孤兒保留秒數) not in (int, float) or not math.isfinite(孤兒保留秒數) or 孤兒保留秒數 < 0:
            _拒絕()
        self.根目錄 = Path(os.path.abspath(os.fspath(根目錄)))
        self.孤兒保留秒數 = float(孤兒保留秒數)
        self._時鐘 = 時鐘

    def 標記孤兒(self, 收據: 套件發布收據) -> Path:
        """重驗 exact receipt 後原子不可覆寫移入同根 ``.orphaned``。

        參數：收據必須精確描述本協調器根下的一個已發布套件。
        回傳：成功隔離後的 ``.orphaned/<bundle-id>`` 絕對路徑。
        例外：普通失敗映射為固定協調錯誤；控制例外保持 identity 傳出。
        副作用：可建立隔離根、原子改名、更新孤兒時間並同步兩個父目錄。
        """
        失敗 = False
        try:
            if type(收據) is not 套件發布收據 or 收據.路徑 != self.根目錄 / 收據.套件識別碼:
                raise ValueError
            根 = _開安全絕對目錄(self.根目錄)
            try:
                實際 = _重驗套件(根, 收據.套件識別碼, self.根目錄)
                if 實際.收據 != 收據:
                    raise ValueError
                try:
                    os.mkdir(".orphaned", 0o700, dir_fd=根)
                except FileExistsError:
                    pass
                孤兒根 = os.open(".orphaned", os.O_RDONLY | _僅目錄 | _不可跟隨, dir_fd=根)
                try:
                    if stat.S_IMODE(os.fstat(孤兒根).st_mode) != 0o700:
                        raise OSError
                    _不可覆寫改名at(根, 收據.套件識別碼, 孤兒根, 收據.套件識別碼)
                    os.utime(收據.套件識別碼, (self._時鐘(), self._時鐘()), dir_fd=孤兒根, follow_symlinks=False)
                    os.fsync(孤兒根)
                    os.fsync(根)
                finally:
                    _關閉描述元(孤兒根)
            finally:
                _關閉描述元(根)
            return self.根目錄 / ".orphaned" / 收據.套件識別碼
        except _控制例外 as 錯誤:
            _清框架(錯誤)
            raise
        except 技能套件協調錯誤:
            raise
        except BaseException as 錯誤:
            _清框架(錯誤)
            失敗 = True
        if 失敗:
            _拒絕()
        raise AssertionError

    def _預檢啟動(
        self,
    ) -> tuple[tuple[_已重驗套件, ...], tuple[_已重驗套件, ...]]:
        """在任何 mutation 前以單一 authority 建立完整 detached projections。

        參數：使用建構時保存的套件根，不另接受參數。
        回傳：已完整重驗的 active 與 orphan 套件 tuple。
        例外：列舉、讀取、身分、內容或共享額度不符時直接傳出一般錯誤。
        副作用：只做有界列舉及讀取並關閉自有 fd，不修改資料庫或檔案系統。
        """
        根 = _開安全絕對目錄(self.根目錄)
        孤兒根: int | None = None
        預算 = _協調預算()
        try:
            根名稱 = sorted(預算.列舉(根))
            active名稱 = [名稱 for 名稱 in 根名稱 if 名稱 != ".orphaned"]
            if any(_識別碼.fullmatch(名稱) is None for 名稱 in active名稱):
                raise OSError
            if ".orphaned" in 根名稱:
                孤兒根 = os.open(".orphaned", os.O_RDONLY | _僅目錄 | _不可跟隨, dir_fd=根)
                if stat.S_IMODE(os.fstat(孤兒根).st_mode) != 0o700:
                    raise OSError
                孤兒名稱 = sorted(預算.列舉(孤兒根))
            else:
                孤兒名稱 = []
            if (
                any(_識別碼.fullmatch(名稱) is None for 名稱 in 孤兒名稱)
                or not set(active名稱).isdisjoint(孤兒名稱)
            ):
                raise OSError
            作用中 = tuple(
                _重驗套件(根, 名稱, self.根目錄, 預算) for 名稱 in active名稱
            )
            孤兒 = () if 孤兒根 is None else tuple(
                _重驗套件(孤兒根, 名稱, self.根目錄 / ".orphaned", 預算)
                for 名稱 in 孤兒名稱
            )
            return 作用中, 孤兒
        finally:
            try:
                if 孤兒根 is not None:
                    _關閉描述元(孤兒根)
            finally:
                _關閉描述元(根)

    def _隔離已重驗套件(self, 套件: _已重驗套件) -> None:
        """只依釘選 projection 名稱及 root identity 移動 preflight 成果。

        參數：套件是本輪 preflight 產生的 detached authoritative projection。
        回傳：原子隔離並同步完成後回傳 ``None``。
        例外：路徑、身分、模式、碰撞、改名或同步失敗時直接傳出一般錯誤。
        副作用：可建立孤兒根、改名套件、設定修改時間並同步目錄。
        """
        名稱 = 套件.投影.bundle_id
        if 套件.收據.路徑 != self.根目錄 / 名稱 or 套件.收據.套件識別碼 != 名稱:
            raise OSError
        根 = _開安全絕對目錄(self.根目錄)
        try:
            可見 = os.stat(名稱, dir_fd=根, follow_symlinks=False)
            if (可見.st_dev, 可見.st_ino) != 套件.根身分:
                raise OSError
            try:
                os.mkdir(".orphaned", 0o700, dir_fd=根)
            except FileExistsError:
                pass
            孤兒根 = os.open(".orphaned", os.O_RDONLY | _僅目錄 | _不可跟隨, dir_fd=根)
            try:
                if stat.S_IMODE(os.fstat(孤兒根).st_mode) != 0o700:
                    raise OSError
                _不可覆寫改名at(根, 名稱, 孤兒根, 名稱)
                時間 = self._時鐘()
                os.utime(名稱, (時間, 時間), dir_fd=孤兒根, follow_symlinks=False)
                os.fsync(孤兒根)
                os.fsync(根)
            finally:
                _關閉描述元(孤兒根)
        finally:
            _關閉描述元(根)

    def _刪除已重驗孤兒(self, 套件: _已重驗套件) -> None:
        """不再消耗共享 authority 地重驗並刪除一個到期孤兒。

        參數：套件包含 preflight 的清單及完整 detached tree projection。
        回傳：投影相符並刪除、同步孤兒根後回傳 ``None``。
        例外：根身分、投影、開啟、刪除或同步不符時直接傳出一般錯誤。
        副作用：重驗成功後按投影反向 unlink/rmdir，最後同步孤兒根。
        """
        名稱 = 套件.投影.bundle_id
        根 = _開安全絕對目錄(self.根目錄)
        try:
            孤兒根 = os.open(".orphaned", os.O_RDONLY | _僅目錄 | _不可跟隨, dir_fd=根)
            try:
                可見 = os.stat(名稱, dir_fd=孤兒根, follow_symlinks=False)
                if (可見.st_dev, 可見.st_ino) != 套件.根身分:
                    raise OSError
                _安全刪樹(孤兒根, 名稱, 套件.樹投影)
                os.fsync(孤兒根)
            finally:
                _關閉描述元(孤兒根)
        finally:
            _關閉描述元(根)

    def 啟動協調(self, 現在: float, 資料庫: sqlite3.Connection) -> 啟動協調結果:
        """有界掃描 active/orphan；補收據、隔離或依 retention 刪除。

        參數：現在是有限非負 epoch 秒；資料庫是呼叫端持有的 SQLite 連線。
        回傳：本輪已補收據、已隔離與已刪除識別碼的不可變結果。
        例外：普通失敗映射為固定協調錯誤；控制例外保持 identity 傳出。
        副作用：讀取套件及資料庫，並可在自有 savepoint 內寫收據與變更套件樹。
        """
        失敗 = False
        try:
            if (type(現在) not in (int, float) or not math.isfinite(現在) or 現在 < 0
                    or type(資料庫) is not sqlite3.Connection):
                raise ValueError
            呼叫端原有交易 = 資料庫.in_transaction
            作用中, 孤兒 = self._預檢啟動()
            已補: list[str] = []
            已隔離: list[str] = []
            儲存庫 = 套件收據儲存庫(資料庫)
            決策: list[tuple[_已重驗套件, str]] = []
            for 套件 in 作用中:
                收據 = 套件.收據
                投影 = 套件.投影
                既有 = 儲存庫.依版本查詢(投影.endpoint_version_id)
                if 既有 is not None:
                    if (
                        既有.套件識別碼, 既有.清單參照, 既有.清單摘要,
                        既有.套件雜湊, 既有.總位元組數,
                    ) != (
                        投影.bundle_id, 收據.清單參照, 投影.manifest_digest,
                        投影.bundle_hash, 投影.total_bytes,
                    ):
                        raise OSError
                    決策.append((套件, "existing"))
                    continue
                版本 = 資料庫.execute(
                    "SELECT endpoint_id,version_number FROM published_endpoint_versions WHERE id=?",
                    (投影.endpoint_version_id,),
                ).fetchone()
                if 版本 == (投影.endpoint_id, 投影.version_number):
                    決策.append((套件, "reconcile"))
                elif 版本 is None:
                    決策.append((套件, "orphan"))
                else:
                    raise OSError
            已刪除: list[str] = []
            自建外層 = False
            儲存點已建立 = False
            儲存點 = ""
            try:
                if not 呼叫端原有交易:
                    資料庫.execute("BEGIN")
                    自建外層 = True
                儲存點 = f"bundle_coordinator_{uuid.uuid4().hex}"
                資料庫.execute(f'SAVEPOINT "{儲存點}"')
                儲存點已建立 = True
                for 套件, 動作 in 決策:
                    if 動作 == "reconcile":
                        儲存庫.新增(
                            版本識別碼=套件.投影.endpoint_version_id, 收據=套件.收據,
                            發布時間=現在, 狀態="reconciled", 協調時間=現在,
                        )
                        已補.append(套件.投影.bundle_id)
                    elif 動作 == "orphan":
                        self._隔離已重驗套件(套件)
                        已隔離.append(套件.投影.bundle_id)
                for 套件 in 孤兒:
                    if 現在 - (套件.修改奈秒 / 1_000_000_000) > self.孤兒保留秒數:
                        self._刪除已重驗孤兒(套件)
                        已刪除.append(套件.投影.bundle_id)
                資料庫.execute(f'RELEASE SAVEPOINT "{儲存點}"')
                儲存點已建立 = False
            except BaseException:
                if 儲存點已建立:
                    _回復儲存點(資料庫, 儲存點, 回復外層=自建外層)
                elif not 呼叫端原有交易 and 資料庫.in_transaction:
                    _執行清理(lambda: 資料庫.execute("ROLLBACK"))
                raise
            return 啟動協調結果(tuple(已補), tuple(已隔離), tuple(已刪除))
        except _控制例外 as 錯誤:
            _清框架(錯誤)
            raise
        except 技能套件協調錯誤:
            raise
        except BaseException as 錯誤:
            _清框架(錯誤)
            失敗 = True
        if 失敗:
            _拒絕()
        raise AssertionError
