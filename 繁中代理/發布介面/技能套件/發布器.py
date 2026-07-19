"""以同層暫存、同步與原子不可覆寫改名發布不可變技能套件。

參數：不適用；模組公開發布器、收據與固定錯誤型別。回傳：不適用。
例外：匯入時若平台基礎模組不可用，傳出標準匯入例外。
副作用：匯入只建立常數、型別與函式，不讀寫技能來源或發布目錄。
"""

from __future__ import annotations

import ctypes
from dataclasses import dataclass
import errno
import hashlib
import json
import math
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import sys
import tempfile
from typing import Any, Callable, Mapping

from .安全複製 import 掃描技能, 重驗檔案, 限制
from .清單 import 技能掃描, 建立清單, 正規JSON, 計算套件雜湊


class 套件發布錯誤(RuntimeError):
    """表示技能套件發布失敗或不可變目標發生碰撞。

    參數：沿用 ``RuntimeError`` 的訊息參數。回傳：建立可辨識發布錯誤。
    例外：建構本身只可能傳出基底例外的標準錯誤。
    副作用：建立錯誤物件不會存取檔案系統。
    """


@dataclass(frozen=True, slots=True)
class 套件發布收據:
    """保存已耐久發布技能套件的公開參照。

    參數：``套件識別碼`` 是不可變目錄名稱；``清單參照`` 是相對清單路徑；
    ``清單摘要`` 與 ``套件雜湊`` 分別識別完整清單及內容集合；``總位元組數`` 是
    已複製內容大小；``路徑`` 是最終套件目錄。回傳：建立不可變公開收據。
    例外：欄位建構不主動拋出例外。副作用：只保存不可變資料。
    """

    套件識別碼: str
    清單參照: str
    清單摘要: str
    套件雜湊: str
    總位元組數: int
    路徑: Path


class 套件耐久性未知(套件發布錯誤):
    """表示最終目錄已可見但父目錄同步結果未知。

    參數：``收據`` 描述已改名的成果。回傳：建立帶 authoritative 收據的專用錯誤。
    例外：建構本身只可能傳出基底例外。副作用：只保存收據，不存取檔案系統。
    """

    def __init__(self, 收據: 套件發布收據) -> None:
        """保存已可見成果的收據並建立固定錯誤訊息。

        參數：``收據`` 是已完成原子改名的成果。回傳：無。
        例外：基底錯誤建構失敗時原樣傳出。副作用：只設定實例欄位。
        """
        super().__init__("套件耐久性未知")
        self.收據 = 收據


_識別碼格式 = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}\Z")
_不可跟隨 = getattr(os, "O_NOFOLLOW", 0)
_僅目錄 = getattr(os, "O_DIRECTORY", 0)
_清單鍵 = {
    "manifest_version", "bundle_id", "endpoint_id", "endpoint_version_id", "version_number",
    "created_at", "created_by_user_id", "source_skills", "copied_files", "copied_file_hashes",
    "excluded_files", "warnings", "total_bytes", "bundle_hash",
}
_身分鍵 = ("bundle_id", "endpoint_id", "endpoint_version_id", "version_number")
_來源技能鍵 = {"name", "source_path", "source_hash"}
_檔案鍵 = {"path", "size_bytes", "sha256"}
_排除鍵 = {"path", "reason"}
_允許排除原因 = {"fixed_excluded_directory", "fixed_excluded_file", "symlink_not_copied"}


def _合法清單路徑(值: object) -> bool:
    """判斷值是否為長度受限的正規相對 POSIX 路徑。

    參數：``值`` 是 hostile manifest 提供的待驗證物件。回傳：僅 exact 字串、
    非絕對且不含空白、目前或父層元件時為真。例外：編碼與解析錯誤關閉為假。
    副作用：只配置短暫路徑物件，不存取檔案系統。
    """
    try:
        if type(值) is not str or not 值 or "\\" in 值:
            return False
        路徑 = PurePosixPath(值)
        return (
            len(值.encode("utf-8")) <= 限制().最大路徑位元組數
            and not 路徑.is_absolute()
            and str(路徑) == 值
            and all(部件 not in {"", ".", ".."} for 部件 in 路徑.parts)
        )
    except (UnicodeError, ValueError):
        return False


def _不可覆寫改名(來源: Path, 目標: Path) -> None:
    """使用目前平台的原子 no-replace primitive 改名目錄。

    參數：``來源`` 是同層暫存目錄；``目標`` 是不可覆寫最終路徑。回傳：無。
    例外：碰撞時拋出 ``FileExistsError``；不支援或系統失敗時拋出 ``OSError``。
    副作用：成功時原子移動來源；絕不以一般 ``rename`` 降級。
    """
    函式庫 = ctypes.CDLL(None, use_errno=True)
    來源位元組 = os.fsencode(來源)
    目標位元組 = os.fsencode(目標)
    if sys.platform == "darwin" and hasattr(函式庫, "renamex_np"):
        改名函式 = 函式庫.renamex_np
        改名函式.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_uint]
        改名函式.restype = ctypes.c_int
        結果 = 改名函式(來源位元組, 目標位元組, 0x00000004)
    elif sys.platform.startswith("linux") and hasattr(函式庫, "renameat2"):
        改名函式 = 函式庫.renameat2
        改名函式.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
        改名函式.restype = ctypes.c_int
        結果 = 改名函式(-100, 來源位元組, -100, 目標位元組, 1)
    else:
        raise OSError(errno.ENOTSUP, "atomic no-replace rename unsupported")
    if 結果 == 0:
        return
    錯誤碼 = ctypes.get_errno()
    if 錯誤碼 in (errno.EEXIST, errno.ENOTEMPTY):
        raise FileExistsError(錯誤碼, os.strerror(錯誤碼), 目標)
    raise OSError(錯誤碼, os.strerror(錯誤碼), 目標)


def _完整寫入(描述元: int, 資料: bytes) -> None:
    """將所有位元組寫入已開啟描述元。

    參數：``描述元`` 是呼叫端擁有的可寫檔案；``資料`` 是完整內容。回傳：無。
    例外：零進度或系統寫入失敗時拋出 ``OSError``。
    副作用：推進描述元游標並寫入檔案，但不關閉或同步描述元。
    """
    檢視 = memoryview(資料)
    while 檢視:
        已寫入 = os.write(描述元, 檢視)
        if 已寫入 <= 0:
            raise OSError
        檢視 = 檢視[已寫入:]


def _讀取有界檔案(目錄描述元: int, 名稱: str, 最大位元組數: int) -> tuple[bytes, os.stat_result]:
    """以 no-follow 描述元讀取有界的一般檔案。

    參數：``目錄描述元`` 與 ``名稱`` 定位檔案；``最大位元組數`` 限制配置。回傳：內容與穩定 metadata。
    例外：種類錯誤、競態、超限或系統失敗時拋出 ``OSError``。副作用：短暫開啟並讀取檔案。
    """
    開啟前 = os.stat(名稱, dir_fd=目錄描述元, follow_symlinks=False)
    if not stat.S_ISREG(開啟前.st_mode) or 開啟前.st_size > 最大位元組數:
        raise OSError
    描述元 = os.open(名稱, os.O_RDONLY | _不可跟隨, dir_fd=目錄描述元)
    try:
        區塊列: list[bytes] = []
        總數 = 0
        while True:
            區塊 = os.read(描述元, min(65536, 最大位元組數 + 1 - 總數))
            if not 區塊:
                break
            區塊列.append(區塊)
            總數 += len(區塊)
            if 總數 > 最大位元組數:
                raise OSError
        讀取後 = os.fstat(描述元)
    finally:
        os.close(描述元)
    開啟身分 = (開啟前.st_dev, 開啟前.st_ino, 開啟前.st_size, 開啟前.st_mtime_ns)
    讀取身分 = (讀取後.st_dev, 讀取後.st_ino, 讀取後.st_size, 讀取後.st_mtime_ns)
    if 開啟身分 != 讀取身分 or not stat.S_ISREG(讀取後.st_mode):
        raise OSError
    return b"".join(區塊列), 讀取後


def _同步目錄(路徑: Path) -> None:
    """同步單一目錄的 metadata。

    參數：``路徑`` 是待同步目錄。回傳：無。例外：開啟或同步錯誤原樣傳出。
    副作用：短暫開啟目錄描述元並呼叫 ``fsync``，最後一律關閉描述元。
    """
    描述元 = os.open(路徑, os.O_RDONLY | _僅目錄)
    try:
        os.fsync(描述元)
    finally:
        os.close(描述元)


def _封存不可變並同步(根目錄: Path) -> None:
    """將暫存樹轉成固定唯讀模式並同步每個 inode。

    參數：``根目錄`` 是已完成內容寫入的同層暫存樹。回傳：無。
    例外：列舉、chmod、開啟或同步錯誤原樣傳出。
    副作用：一般檔改為 0444、目錄改為 0555，並逐一 fsync。
    """
    目錄列 = [根目錄]
    檔案列: list[Path] = []
    for 目前根, 子目錄列, 子檔案列 in os.walk(根目錄, followlinks=False):
        目前 = Path(目前根)
        目錄列.extend(目前 / 名稱 for 名稱 in 子目錄列)
        檔案列.extend(目前 / 名稱 for 名稱 in 子檔案列)
    for 路徑 in 檔案列:
        資訊 = 路徑.lstat()
        if not stat.S_ISREG(資訊.st_mode):
            raise OSError
        os.chmod(路徑, 0o444, follow_symlinks=False)
        描述元 = os.open(路徑, os.O_RDONLY | _不可跟隨)
        try:
            os.fsync(描述元)
        finally:
            os.close(描述元)
    for 路徑 in sorted(目錄列, key=lambda 項目: len(項目.parts), reverse=True):
        if not stat.S_ISDIR(路徑.lstat().st_mode):
            raise OSError
        os.chmod(路徑, 0o555, follow_symlinks=False)
        _同步目錄(路徑)


def _安全清除(路徑: Path | None) -> None:
    """盡力清除可能已轉為唯讀的暫存目錄且不傳出例外。

    參數：``路徑`` 是待清除暫存目錄或 ``None``。回傳：無。例外：所有清理錯誤皆被抑制。
    副作用：若路徑仍存在，放寬其目錄權限並遞迴刪除。
    """
    if 路徑 is None:
        return
    def 修復權限(_函式: Callable[..., Any], 失敗路徑: str, _資訊: Any) -> None:
        """放寬清理期間遇到的唯讀路徑後重試刪除。

        參數：``失敗路徑`` 是 shutil 回報位置，其餘參數只符合 callback。回傳：無。
        例外：錯誤由外層清理抑制。副作用：chmod 後刪除該位置。
        """
        os.chmod(失敗路徑, 0o700)
        if os.path.isdir(失敗路徑) and not os.path.islink(失敗路徑):
            shutil.rmtree(失敗路徑, onerror=修復權限)
        else:
            os.unlink(失敗路徑)
    try:
        shutil.rmtree(路徑, onerror=修復權限)
    except BaseException:
        pass


def _驗證清單結構(清單: Any) -> dict[str, dict[str, Any]]:
    """驗證既有清單的固定結構並建立預期檔案索引。

    參數：``清單`` 是 JSON 解碼值。回傳：由相對路徑映射至檔案項目的索引。
    例外：任何結構、型別、額度或摘要不合契約時拋出 ``ValueError``。
    副作用：只配置有界容器，不存取檔案系統。
    """
    if type(清單) is not dict or set(清單) != _清單鍵:
        raise ValueError
    if type(清單["manifest_version"]) is not int or 清單["manifest_version"] != 1:
        raise ValueError
    if any(
        type(清單[鍵]) is not str or _識別碼格式.fullmatch(清單[鍵]) is None
        for 鍵 in ("bundle_id", "endpoint_id", "endpoint_version_id", "created_by_user_id")
    ):
        raise ValueError
    if type(清單["version_number"]) is not int or 清單["version_number"] <= 0:
        raise ValueError
    建立時間 = 清單["created_at"]
    if type(建立時間) not in (int, float) or not math.isfinite(建立時間) or 建立時間 < 0:
        raise ValueError
    來源列 = 清單["source_skills"]
    if type(來源列) is not list or not 1 <= len(來源列) <= 32:
        raise ValueError
    來源索引: dict[str, dict[str, Any]] = {}
    for 來源 in 來源列:
        if type(來源) is not dict or set(來源) != _來源技能鍵:
            raise ValueError
        名稱, 來源路徑, 來源雜湊 = 來源["name"], 來源["source_path"], 來源["source_hash"]
        if (
            type(名稱) is not str or _識別碼格式.fullmatch(名稱) is None
            or type(來源路徑) is not str or not os.path.isabs(來源路徑)
            or not 來源路徑 or len(來源路徑.encode("utf-8")) > 限制().最大路徑位元組數
            or type(來源雜湊) is not str or re.fullmatch(r"[0-9a-f]{64}", 來源雜湊) is None
            or 名稱 in 來源索引
        ):
            raise ValueError
        來源索引[名稱] = 來源
    if [來源["name"] for 來源 in 來源列] != sorted(來源索引, key=lambda 值: 值.encode("utf-8")):
        raise ValueError
    項目列 = 清單.get("copied_files")
    if type(項目列) is not list or not 1 <= len(項目列) <= 限制().最大檔案數:
        raise ValueError
    索引: dict[str, dict[str, Any]] = {}
    總數 = 0
    for 項目 in 項目列:
        if type(項目) is not dict or set(項目) != _檔案鍵:
            raise ValueError
        路徑, 大小, 雜湊 = 項目["path"], 項目["size_bytes"], 項目["sha256"]
        if type(路徑) is not str or type(大小) is not int or type(雜湊) is not str:
            raise ValueError
        部件 = PurePosixPath(路徑).parts
        if not _合法清單路徑(路徑) or len(部件) < 2 or 部件[0] not in 來源索引:
            raise ValueError
        if len(路徑.encode("utf-8")) > 限制().最大路徑位元組數 or not 0 <= 大小 <= 限制().最大檔案位元組數:
            raise ValueError
        if re.fullmatch(r"[0-9a-f]{64}", 雜湊) is None or 路徑 in 索引:
            raise ValueError
        索引[路徑] = 項目
        總數 += 大小
    if [項目["path"] for 項目 in 項目列] != sorted(索引, key=lambda 值: 值.encode("utf-8")):
        raise ValueError
    for 名稱, 來源 in 來源索引.items():
        技能項目 = [
            ["/".join(PurePosixPath(路徑).parts[1:]), 項目["size_bytes"], 項目["sha256"]]
            for 路徑, 項目 in 索引.items() if PurePosixPath(路徑).parts[0] == 名稱
        ]
        if not 技能項目 or not any(項目[0] == "SKILL.md" for 項目 in 技能項目):
            raise ValueError
        if hashlib.sha256(正規JSON(技能項目)).hexdigest() != 來源["source_hash"]:
            raise ValueError
    排除列 = 清單["excluded_files"]
    if type(排除列) is not list or len(排除列) > 限制().最大檔案數:
        raise ValueError
    排除路徑列: list[str] = []
    for 排除 in 排除列:
        if type(排除) is not dict or set(排除) != _排除鍵:
            raise ValueError
        路徑, 原因 = 排除["path"], 排除["reason"]
        部件 = PurePosixPath(路徑).parts if type(路徑) is str else ()
        if (
            not _合法清單路徑(路徑) or len(部件) < 2 or 部件[0] not in 來源索引
            or type(原因) is not str or 原因 not in _允許排除原因 or 路徑 in 索引
        ):
            raise ValueError
        排除路徑列.append(路徑)
    if 排除路徑列 != sorted(set(排除路徑列), key=lambda 值: 值.encode("utf-8")):
        raise ValueError
    if type(清單["warnings"]) is not list or 清單["warnings"]:
        raise ValueError
    if type(清單["total_bytes"]) is not int or 總數 > 限制().最大總位元組數 or 清單["total_bytes"] != 總數:
        raise ValueError
    if type(清單["copied_file_hashes"]) is not dict or 清單["copied_file_hashes"] != {路徑: 項目["sha256"] for 路徑, 項目 in 索引.items()}:
        raise ValueError
    if type(清單["bundle_hash"]) is not str or re.fullmatch(r"[0-9a-f]{64}", 清單["bundle_hash"]) is None:
        raise ValueError
    if 清單["bundle_hash"] != 計算套件雜湊(項目列):
        raise ValueError
    return 索引


def _重驗最終內容(
    最終目錄: Path, 清單摘要: str, 索引: dict[str, dict[str, Any]]
) -> None:
    """以目錄描述元重驗最終樹的種類、模式、大小、摘要及完整集合。

    參數：``最終目錄`` 是既有成果；``清單摘要`` 與 ``索引`` 描述唯一允許內容。回傳：無。
    例外：碰撞、競態、額外項目或內容不符時拋出 ``OSError``。副作用：只讀取成果樹。
    """
    根開啟前 = 最終目錄.lstat()
    if not stat.S_ISDIR(根開啟前.st_mode) or stat.S_IMODE(根開啟前.st_mode) != 0o555:
        raise OSError
    根描述元 = os.open(最終目錄, os.O_RDONLY | _僅目錄 | _不可跟隨)
    實際檔案: set[str] = set()
    實際目錄: set[str] = {""}
    預期目錄: set[str] = {""}
    已驗清單 = False
    for 路徑 in 索引:
        部件 = PurePosixPath(路徑).parts
        預期目錄.update("/".join(部件[:索引值]) for 索引值 in range(1, len(部件)))

    def 走訪(目錄描述元: int, 前綴: str) -> None:
        """遞迴重驗一個已釘選最終目錄。

        參數：``目錄描述元`` 由外層持有；``前綴`` 是相對路徑。回傳：無。
        例外：種類、模式、內容或競態不符時拋出 ``OSError``。副作用：讀取並關閉自行開啟的描述元。
        """
        nonlocal 已驗清單
        if stat.S_IMODE(os.fstat(目錄描述元).st_mode) != 0o555:
            raise OSError
        for 名稱 in os.listdir(目錄描述元):
            相對路徑 = 名稱 if not 前綴 else f"{前綴}/{名稱}"
            資訊 = os.stat(名稱, dir_fd=目錄描述元, follow_symlinks=False)
            if stat.S_ISDIR(資訊.st_mode):
                子描述元 = os.open(名稱, os.O_RDONLY | _僅目錄 | _不可跟隨, dir_fd=目錄描述元)
                try:
                    釘選 = os.fstat(子描述元)
                    if (資訊.st_dev, 資訊.st_ino) != (釘選.st_dev, 釘選.st_ino):
                        raise OSError
                    實際目錄.add(相對路徑)
                    走訪(子描述元, 相對路徑)
                finally:
                    os.close(子描述元)
            elif stat.S_ISREG(資訊.st_mode):
                if 相對路徑 == "manifest.json":
                    if 前綴 or stat.S_IMODE(資訊.st_mode) != 0o444:
                        raise OSError
                    資料, _穩定資訊 = _讀取有界檔案(
                        目錄描述元, 名稱, 限制().最大總位元組數
                    )
                    if hashlib.sha256(資料).hexdigest() != 清單摘要:
                        raise OSError
                    已驗清單 = True
                    continue
                項目 = 索引.get(相對路徑)
                if 項目 is None or stat.S_IMODE(資訊.st_mode) != 0o444:
                    raise OSError
                資料, 穩定資訊 = _讀取有界檔案(目錄描述元, 名稱, 項目["size_bytes"])
                if 穩定資訊.st_size != 項目["size_bytes"] or hashlib.sha256(資料).hexdigest() != 項目["sha256"]:
                    raise OSError
                實際檔案.add(相對路徑)
            else:
                raise OSError
    try:
        根開啟後 = os.fstat(根描述元)
        if (根開啟前.st_dev, 根開啟前.st_ino) != (根開啟後.st_dev, 根開啟後.st_ino):
            raise OSError
        走訪(根描述元, "")
    finally:
        os.close(根描述元)
    if not 已驗清單 or 實際檔案 != set(索引) or 實際目錄 != 預期目錄:
        raise OSError


