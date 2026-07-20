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
import traceback
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
_來源技能鍵 = {"name", "source_path", "source_hash"}
_檔案鍵 = {"path", "size_bytes", "sha256"}
_排除鍵 = {"path", "reason"}
_允許排除原因 = {"fixed_excluded_directory", "fixed_excluded_file", "symlink_not_copied"}


@dataclass(frozen=True, slots=True)
class 已驗證清單檔案:
    """保存 public validator 核准的一個 copied file projection。

    欄位：``path`` 是 canonical bundle 路徑；``size_bytes`` 是內容長度；``sha256``
    是內容摘要。回傳：建立不可變 projection。例外：欄位建構不主動拋出例外。
    副作用：只保存 immutable scalar，不存取檔案系統。
    """

    path: str
    size_bytes: int
    sha256: str


@dataclass(frozen=True, slots=True)
class 已驗證技能套件清單:
    """保存 canonical manifest 的 immutable authoritative projection。

    欄位：``manifest_digest`` 識別完整 canonical bytes；其餘識別欄位、總量、
    ``bundle_hash`` 與 ordered ``copied_files`` 來自同一已驗證 manifest。
    回傳：建立不可變 projection。例外：欄位建構不主動拋出例外。
    副作用：只保存 immutable scalar 與 tuple。
    """

    manifest_digest: str
    bundle_id: str
    endpoint_id: str
    endpoint_version_id: str
    version_number: int
    total_bytes: int
    bundle_hash: str
    copied_files: tuple[已驗證清單檔案, ...]


def _拒絕清單常數(_值: str) -> object:
    """拒絕 JSON nonfinite token。

    參數：``_值`` 是 decoder 提供的 token。回傳：不適用。
    例外：固定拋出 ``ValueError``。副作用：無。
    """
    raise ValueError


def _建立無重複清單物件(項目們: list[tuple[str, Any]]) -> dict[str, Any]:
    """由 decoder pairs 建立 exact dict 並拒絕 duplicate key。

    參數：``項目們`` 是同一 JSON object 的 ordered pairs。回傳：新 exact dict。
    例外：鍵重複時拋出 ``ValueError``。副作用：只配置 bounded mapping。
    """
    結果: dict[str, Any] = {}
    for 鍵, 值 in 項目們:
        if 鍵 in 結果:
            raise ValueError
        結果[鍵] = 值
    return 結果


def _清除例外框架(錯誤: BaseException) -> None:
    """盡力清空已離開的敵對驗證框架。

    參數：``錯誤`` 是正要離開公開邊界的原例外。回傳：無。
    例外：框架清理錯誤一律抑制。副作用：清空 traceback 中已停止執行框架的 locals。
    """
    try:
        traceback.clear_frames(錯誤.__traceback__)
    except BaseException:
        pass


def 驗證已發布技能套件清單(原始資料: bytes) -> 已驗證技能套件清單:
    """驗證 canonical manifest bytes 並回傳唯一 immutable projection。

    參數：``原始資料`` 是待驗證的 exact bytes。回傳：包含 manifest digest、身分、
    bundle hash、總量及 ordered files 的 ``已驗證技能套件清單``。
    例外：非 bytes、超限、非嚴格 UTF-8、duplicate key、nonfinite、非 canonical bytes、
    schema/type/bounds/relations/digest 不符時拋出 ``ValueError``。
    副作用：只解碼與配置 immutable projection，不存取檔案系統。
    """
    原文 = 清單 = 檔案們 = 項目 = 結果 = None
    失敗 = False
    try:
        if type(原始資料) is not bytes or not 原始資料 or len(原始資料) > 限制().最大總位元組數:
            raise ValueError
        原文 = 原始資料.decode("utf-8", errors="strict")
        清單 = json.loads(
            原文,
            parse_constant=_拒絕清單常數,
            object_pairs_hook=_建立無重複清單物件,
        )
        if 正規JSON(清單) != 原始資料:
            raise ValueError
        _驗證清單結構(清單)
        檔案們 = tuple(
            已驗證清單檔案(項目["path"], 項目["size_bytes"], 項目["sha256"])
            for 項目 in 清單["copied_files"]
        )
        結果 = 已驗證技能套件清單(
            hashlib.sha256(原始資料).hexdigest(),
            清單["bundle_id"], 清單["endpoint_id"], 清單["endpoint_version_id"],
            清單["version_number"], 清單["total_bytes"], 清單["bundle_hash"], 檔案們,
        )
        return 結果
    except (KeyboardInterrupt, SystemExit, GeneratorExit) as 錯誤:
        try:
            _清除例外框架(錯誤)
        except BaseException:
            pass
        原始資料 = b""
        原文 = 清單 = 檔案們 = 項目 = 結果 = None
        raise
    except BaseException as 錯誤:
        try:
            _清除例外框架(錯誤)
        except BaseException:
            pass
        原始資料 = b""
        原文 = 清單 = 檔案們 = 項目 = 結果 = None
        失敗 = True
    if 失敗:
        raise ValueError from None
    raise AssertionError


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


class 技能套件發布器:
    """協調技能掃描、暫存寫入與不可變發布。

    參數：由建構器保存發布根與可選失敗點。回傳：建立可重複使用的發布協調器。
    例外：建構錯誤依建構器契約。副作用：方法執行前不碰觸檔案系統。
    """

    def __init__(self, 根目錄: str | Path, *, 失敗點: Callable[[str], None] | None = None) -> None:
        """建立技能套件發布器。

        參數：``根目錄`` 是套件共同父目錄；``失敗點`` 可在耐久步驟注入失敗。回傳：無。
        例外：路徑轉換失敗時傳出標準例外。副作用：只保存設定，不建立目錄。
        """
        self.根目錄 = Path(根目錄)
        self._失敗點 = 失敗點 or (lambda _名稱: None)

    def _掃描(self, 技能表: Mapping[str, str | Path]) -> tuple[技能掃描, ...]:
        """掃描排序來源並套用整個 bundle 共享額度。

        參數：``技能表`` 將技能名稱映射至來源根。回傳：已排序釘選的掃描 tuple。
        例外：名稱、數量、來源或合計額度不合時拋出固定錯誤。副作用：只讀取來源。
        """
        if not 技能表 or len(技能表) > 32:
            raise 套件發布錯誤("技能套件發布失敗")
        掃描列: list[技能掃描] = []
        for 名稱 in sorted(技能表, key=lambda 項目: 項目.encode("utf-8")):
            if _識別碼格式.fullmatch(名稱) is None:
                raise 套件發布錯誤("技能套件發布失敗")
            掃描列.append(掃描技能(名稱, 技能表[名稱]))
        檔案總數 = sum(len(掃描.檔案) for 掃描 in 掃描列)
        位元組總數 = sum(檔案.位元組數 for 掃描 in 掃描列 for 檔案 in 掃描.檔案)
        if 檔案總數 > 限制().最大檔案數 or 位元組總數 > 限制().最大總位元組數:
            raise 套件發布錯誤("技能來源超過限制")
        return tuple(掃描列)

    def 發布(self, *, 套件識別碼: str, 端點識別碼: str, 端點版本識別碼: str,
           版本號碼: int, 建立時間: float, 建立者識別碼: str,
           技能表: Mapping[str, str | Path]) -> 套件發布收據:
        """建立並耐久發布一個不可變技能套件。

        參數：識別欄位與版本資料描述目標；``技能表`` 提供名稱至來源映射。
        回傳：新發布或同內容雜湊且身分一致之既有套件收據。
        例外：一般失敗拋出 ``套件發布錯誤``；父同步失敗拋出 ``套件耐久性未知``；控制例外原樣傳出。
        副作用：掃描來源、建立同層暫存、唯讀同步，並以 no-replace 原子改名發布。
        """
        暫存目錄: Path | None = None
        try:
            識別碼列 = (套件識別碼, 端點識別碼, 端點版本識別碼, 建立者識別碼)
            if any(type(值) is not str or _識別碼格式.fullmatch(值) is None for 值 in 識別碼列):
                raise ValueError
            if type(版本號碼) is not int or 版本號碼 <= 0:
                raise ValueError
            掃描列 = self._掃描(技能表)
            清單, 原始資料, 摘要 = 建立清單(
                套件識別碼=套件識別碼, 端點識別碼=端點識別碼,
                端點版本識別碼=端點版本識別碼, 版本號碼=版本號碼,
                建立時間=建立時間, 建立者識別碼=建立者識別碼, 掃描列=掃描列,
            )
            驗證已發布技能套件清單(原始資料)
            self.根目錄.mkdir(parents=True, exist_ok=True)
            最終目錄 = self.根目錄 / 套件識別碼
            try:
                最終目錄.lstat()
            except FileNotFoundError:
                pass
            else:
                return self._讀取既有收據(最終目錄, 清單)
            暫存目錄 = Path(tempfile.mkdtemp(prefix=".stage-", dir=self.根目錄))
            for 掃描 in 掃描列:
                for 檔案 in 掃描.檔案:
                    目標 = 暫存目錄 / 掃描.名稱 / 檔案.相對路徑
                    目標.parent.mkdir(parents=True, exist_ok=True)
                    資料 = 重驗檔案(掃描, 檔案)
                    描述元 = os.open(目標, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                    try:
                        _完整寫入(描述元, 資料)
                        self._失敗點("file_fsync")
                        os.fsync(描述元)
                    finally:
                        os.close(描述元)
            清單路徑 = 暫存目錄 / "manifest.json"
            描述元 = os.open(清單路徑, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            try:
                _完整寫入(描述元, 原始資料)
                self._失敗點("manifest_fsync")
                os.fsync(描述元)
            finally:
                os.close(描述元)
            _封存不可變並同步(暫存目錄)
            self._失敗點("stage_fsync")
            _同步目錄(暫存目錄)
            self._失敗點("rename")
            try:
                _不可覆寫改名(暫存目錄, 最終目錄)
            except FileExistsError:
                _安全清除(暫存目錄)
                暫存目錄 = None
                return self._讀取既有收據(最終目錄, 清單)
            暫存目錄 = None
            收據 = 套件發布收據(
                套件識別碼, f"{套件識別碼}/manifest.json", 摘要,
                清單["bundle_hash"], 清單["total_bytes"], 最終目錄,
            )
            try:
                self._失敗點("parent_fsync")
                _同步目錄(self.根目錄)
            except Exception:
                raise 套件耐久性未知(收據) from None
            return 收據
        except 套件耐久性未知:
            raise
        except (KeyboardInterrupt, SystemExit, GeneratorExit):
            _安全清除(暫存目錄)
            raise
        except BaseException:
            _安全清除(暫存目錄)
            raise 套件發布錯誤("技能套件發布失敗") from None

    def _讀取既有收據(self, 最終目錄: Path, 預期清單: dict[str, Any]) -> 套件發布收據:
        """重驗同識別碼既有成果並依內容雜湊建立冪等收據。

        參數：``最終目錄`` 是碰撞目標；``預期清單`` 來自本次掃描。回傳：既有 authoritative 收據。
        例外：身分、內容、種類、模式、摘要或完整集合不同時拋出碰撞錯誤。副作用：只讀取成果。
        """
        try:
            根描述元 = os.open(最終目錄, os.O_RDONLY | _僅目錄 | _不可跟隨)
            try:
                原始資料, 清單資訊 = _讀取有界檔案(根描述元, "manifest.json", 限制().最大總位元組數)
            finally:
                os.close(根描述元)
            if stat.S_IMODE(清單資訊.st_mode) != 0o444:
                raise ValueError
            既有清單 = 驗證已發布技能套件清單(原始資料)
            預期投影 = 驗證已發布技能套件清單(正規JSON(預期清單))
            if (
                既有清單.bundle_id != 預期投影.bundle_id
                or 既有清單.endpoint_id != 預期投影.endpoint_id
                or 既有清單.endpoint_version_id != 預期投影.endpoint_version_id
                or 既有清單.version_number != 預期投影.version_number
            ):
                raise ValueError
            if 既有清單.bundle_hash != 預期投影.bundle_hash:
                raise ValueError
            索引 = {
                項目.path: {
                    "path": 項目.path, "size_bytes": 項目.size_bytes, "sha256": 項目.sha256,
                }
                for 項目 in 既有清單.copied_files
            }
            _重驗最終內容(最終目錄, 既有清單.manifest_digest, 索引)
            套件識別碼 = 既有清單.bundle_id
            return 套件發布收據(
                套件識別碼, f"{套件識別碼}/manifest.json",
                既有清單.manifest_digest, 既有清單.bundle_hash,
                既有清單.total_bytes, 最終目錄,
            )
        except (KeyboardInterrupt, SystemExit, GeneratorExit):
            raise
        except BaseException:
            raise 套件發布錯誤("技能套件碰撞") from None
