"""以描述元安全地載入 exact endpoint version 的不可變技能套件。

參數／欄位：不適用；本模組公開定位契約、固定錯誤與已發布套件載入器。
回傳：不適用；公開方法回傳經完整驗證的 ``技能套件快照``。
例外：載入邊界失敗時只拋出固定 ``技能套件載入錯誤``；控制流程例外原樣傳出。
副作用：匯入只定義型別與函式，不讀取檔案系統或呼叫定位提供者。
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import hmac
import os
from pathlib import Path, PurePosixPath
import re
import stat
import traceback
from typing import Protocol
import unicodedata

from .安全複製 import 技能套件最大總位元組數, 限制
from .發布器 import 已驗證技能套件清單, 驗證已發布技能套件清單
from .清單 import 是合法技能套件清單參照
from ..執行期.執行器 import 技能套件快照, 技能套件檔案


_固定訊息 = "技能套件載入失敗。"
_唯一來源 = "endpoint_version_snapshot"
_識別碼格式 = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}\Z")
_摘要格式 = re.compile(r"[0-9a-f]{64}\Z")
_不可跟隨 = getattr(os, "O_NOFOLLOW", 0)
_僅目錄 = getattr(os, "O_DIRECTORY", 0)
_控制流程 = (KeyboardInterrupt, SystemExit, GeneratorExit)



class 技能套件載入錯誤(RuntimeError):
    """表示 exact 已發布技能套件無法安全載入。

    參數：不接受參數，訊息固定為「技能套件載入失敗。」。
    回傳：建立固定且不含敏感內容的錯誤。
    例外：基底例外初始化失敗時原樣傳出。
    副作用：只建立例外物件，不存取定位提供者或檔案系統。
    """

    def __init__(self) -> None:
        """建立固定訊息的載入錯誤。

        參數：無。回傳：無。例外：基底初始化錯誤原樣傳出。
        副作用：只初始化目前例外實例。
        """
        super().__init__(_固定訊息)


@dataclass(frozen=True, slots=True, init=False)
class 技能套件定位:
    """保存單一版本之不可變技能套件 authoritative 定位。

    欄位：版本與套件識別碼、canonical 清單參照、分離的清單與內容摘要，以及內容總量。
    回傳：建立 exact scalar 的不可變定位。例外：任一欄位不合契約時拋出固定載入錯誤。
    副作用：只重建 scalar，不呼叫提供者或存取檔案系統。
    """

    version_id: str
    bundle_id: str
    manifest_reference: str
    manifest_digest: str
    bundle_hash: str
    total_bytes: int

    def __init__(
        self, *, version_id: str, bundle_id: str, manifest_reference: str,
        manifest_digest: str, bundle_hash: str, total_bytes: int,
    ) -> None:
        """驗證並重建 authoritative 定位。

        參數：各 keyword-only 參數對應同名欄位。回傳：無。
        例外：型別、格式、參照關係或總量不合契約時拋出固定載入錯誤。
        副作用：只設定 frozen instance 欄位。
        """
        try:
            if not _是識別碼(version_id) or not _是識別碼(bundle_id):
                raise ValueError
            if manifest_reference != f"{bundle_id}/manifest.json":
                raise ValueError
            if not _是摘要(manifest_digest) or not _是摘要(bundle_hash):
                raise ValueError
            if type(total_bytes) is not int or not 0 < total_bytes <= 技能套件最大總位元組數:
                raise ValueError
            object.__setattr__(self, "version_id", version_id)
            object.__setattr__(self, "bundle_id", bundle_id)
            object.__setattr__(self, "manifest_reference", manifest_reference)
            object.__setattr__(self, "manifest_digest", manifest_digest)
            object.__setattr__(self, "bundle_hash", bundle_hash)
            object.__setattr__(self, "total_bytes", total_bytes)
        except _控制流程:
            raise
        except BaseException:
            raise 技能套件載入錯誤() from None


class 技能套件定位提供者(Protocol):
    """定義只依 exact endpoint version 取得技能套件定位的提供者。

    參數／欄位：實作者不得使用 current、latest 或 slug fallback。
    回傳：不適用；方法回傳可 exact-rebuild 的定位物件。
    例外：由實作者定義，載入器會固定化普通失敗。副作用：由實作者定義。
    """

    def 取得技能套件定位(self, endpoint_version_id: str) -> object:
        """取得 exact version 的 authoritative 定位。

        參數：``endpoint_version_id`` 是已預檢的 exact 識別碼。回傳：定位物件。
        例外：由實作者定義。副作用：可執行一次 authoritative lookup。
        """
        ...


def _是識別碼(值: object) -> bool:
    """判斷值是否為 exact、NFC 且受限的識別碼。

    參數：``值`` 是待檢查 hostile 值。回傳：完全符合時為真，否則為假。
    例外：Unicode 正規化錯誤關閉為假。副作用：只執行字串檢查。
    """
    try:
        return (
            type(值) is str and unicodedata.normalize("NFC", 值) == 值
            and _識別碼格式.fullmatch(值) is not None
        )
    except (TypeError, ValueError):
        return False


def _是摘要(值: object) -> bool:
    """判斷值是否為 exact lowercase SHA-256 字串。

    參數：``值`` 是待檢查值。回傳：符合時為真。例外：無預期例外。
    副作用：只執行正規表示式比對。
    """
    return type(值) is str and _摘要格式.fullmatch(值) is not None


_是清單參照 = 是合法技能套件清單參照


def _重建定位(值: object) -> 技能套件定位:
    """從 provider 回傳值立即建立 exact immutable 定位。

    參數：``值`` 是 provider 的 object。回傳：新 ``技能套件定位``。
    例外：欄位讀取或驗證失敗時拋出固定載入錯誤。副作用：讀取六個具名屬性一次。
    """
    try:
        return 技能套件定位(
            version_id=getattr(值, "version_id"), bundle_id=getattr(值, "bundle_id"),
            manifest_reference=getattr(值, "manifest_reference"),
            manifest_digest=getattr(值, "manifest_digest"),
            bundle_hash=getattr(值, "bundle_hash"), total_bytes=getattr(值, "total_bytes"),
        )
    except _控制流程:
        raise
    except BaseException:
        raise 技能套件載入錯誤() from None


@dataclass(frozen=True, slots=True)
class _穩定檔案:
    """保存一次 descriptor-safe 讀取所釘選的一般檔案。

    欄位：``資料`` 是內容；其餘欄位保存 device、inode、size 與 mtime identity。
    回傳：建立 immutable internal observation。例外：建構不主動驗證。
    副作用：只保存值，不讀寫檔案系統。
    """

    資料: bytes
    裝置: int
    索引節點: int
    位元組數: int
    修改奈秒: int


def _身分(資訊: os.stat_result) -> tuple[int, int, int, int]:
    """投影競態重驗所需的檔案身分。

    參數：``資訊`` 是 stat 結果。回傳：device、inode、size、mtime_ns 四元組。
    例外：非 stat-shaped hostile 值可能傳出 ``AttributeError``。副作用：只讀欄位。
    """
    return (資訊.st_dev, 資訊.st_ino, 資訊.st_size, 資訊.st_mtime_ns)


def _開啟發布根(根字串: str) -> int:
    """逐層 no-follow 開啟 absolute 發布根並比對 visible identity。

    參數：``根字串`` 是建構時保存的 canonical absolute path。回傳：呼叫端負責關閉的
    root descriptor。例外：缺失、symlink、special 或競態時傳出 ``OSError``。
    副作用：逐層開啟並關閉目錄描述元，成功時保留最終描述元。
    """
    根路徑 = Path(根字串)
    目前 = os.open(根路徑.anchor, os.O_RDONLY | _僅目錄 | _不可跟隨)
    try:
        for 部件 in 根路徑.parts[1:]:
            可見 = os.stat(部件, dir_fd=目前, follow_symlinks=False)
            if not stat.S_ISDIR(可見.st_mode):
                raise OSError
            下一個 = os.open(部件, os.O_RDONLY | _僅目錄 | _不可跟隨, dir_fd=目前)
            try:
                已開啟 = os.fstat(下一個)
                if (可見.st_dev, 可見.st_ino) != (已開啟.st_dev, 已開啟.st_ino):
                    raise OSError
            except BaseException:
                os.close(下一個)
                raise
            os.close(目前)
            目前 = 下一個
        最終可見 = os.stat(根字串, follow_symlinks=False)
        最終釘選 = os.fstat(目前)
        if not stat.S_ISDIR(最終可見.st_mode) or (最終可見.st_dev, 最終可見.st_ino) != (最終釘選.st_dev, 最終釘選.st_ino):
            raise OSError
        結果 = 目前
        目前 = -1
        return 結果
    finally:
        if 目前 >= 0:
            os.close(目前)


def _開啟套件根(根描述元: int, bundle_id: str) -> int:
    """由發布根 descriptor-relative 開啟唯一 bundle root。

    參數：發布根描述元與已驗證 ``bundle_id``。回傳：呼叫端負責關閉的 bundle descriptor。
    例外：非目錄、symlink、模式非 0555 或 identity 競態時傳出 ``OSError``。
    副作用：查詢並開啟 bundle 目錄。
    """
    可見 = os.stat(bundle_id, dir_fd=根描述元, follow_symlinks=False)
    if not stat.S_ISDIR(可見.st_mode) or stat.S_IMODE(可見.st_mode) != 0o555:
        raise OSError
    描述元 = os.open(bundle_id, os.O_RDONLY | _僅目錄 | _不可跟隨, dir_fd=根描述元)
    try:
        釘選 = os.fstat(描述元)
        if (可見.st_dev, 可見.st_ino) != (釘選.st_dev, 釘選.st_ino):
            raise OSError
        return 描述元
    except BaseException:
        os.close(描述元)
        raise


def _開啟父目錄(
    根描述元: int, 相對路徑: str, 目錄身分: dict[str, tuple[int, int]],
) -> tuple[int, list[int]]:
    """沿 canonical manifest path 開啟並釘選全部 intermediate directories。

    參數：bundle descriptor、已由 public validator 核准的路徑及跨讀取 identity map。
    回傳：parent descriptor 與由呼叫端反向關閉的 descriptor list。
    例外：目錄種類、模式、symlink 或 identity 不一致時傳出 ``OSError``。
    副作用：開啟各 intermediate directory 並更新 identity map。
    """
    目前 = 根描述元
    已開啟: list[int] = []
    前綴: list[str] = []
    try:
        for 名稱 in PurePosixPath(相對路徑).parts[:-1]:
            可見 = os.stat(名稱, dir_fd=目前, follow_symlinks=False)
            if not stat.S_ISDIR(可見.st_mode) or stat.S_IMODE(可見.st_mode) != 0o555:
                raise OSError
            子描述元 = os.open(名稱, os.O_RDONLY | _僅目錄 | _不可跟隨, dir_fd=目前)
            釘選 = os.fstat(子描述元)
            if (可見.st_dev, 可見.st_ino) != (釘選.st_dev, 釘選.st_ino):
                os.close(子描述元)
                raise OSError
            前綴.append(名稱)
            路徑鍵 = "/".join(前綴)
            現有 = 目錄身分.setdefault(路徑鍵, (釘選.st_dev, 釘選.st_ino))
            if 現有 != (釘選.st_dev, 釘選.st_ino):
                os.close(子描述元)
                raise OSError
            已開啟.append(子描述元)
            目前 = 子描述元
        return 目前, 已開啟
    except BaseException:
        for 描述元 in reversed(已開啟):
            os.close(描述元)
        raise


def _讀取穩定檔案(
    目錄描述元: int, 名稱: str, *, 預期大小: int | None, 上限: int,
) -> _穩定檔案:
    """先 gate metadata，再以 O_NOFOLLOW 讀取並 before/after 重驗一般檔案。

    參數：parent descriptor、單一檔名、可選 exact size 與配置上限。回傳：內容及穩定身分。
    例外：special、symlink、模式非 0444、超限、短讀或競態時傳出 ``OSError``。
    副作用：短暫開啟與讀取檔案描述元，最後一律關閉。
    """
    可見 = os.stat(名稱, dir_fd=目錄描述元, follow_symlinks=False)
    if (
        not stat.S_ISREG(可見.st_mode) or stat.S_IMODE(可見.st_mode) != 0o444
        or 可見.st_size > 上限 or (預期大小 is not None and 可見.st_size != 預期大小)
    ):
        raise OSError
    描述元 = os.open(名稱, os.O_RDONLY | _不可跟隨, dir_fd=目錄描述元)
    try:
        讀取前 = os.fstat(描述元)
        if _身分(可見) != _身分(讀取前) or not stat.S_ISREG(讀取前.st_mode):
            raise OSError
        區塊列: list[bytes] = []
        總數 = 0
        while True:
            區塊 = os.read(描述元, min(65536, 上限 + 1 - 總數))
            if not 區塊:
                break
            區塊列.append(區塊)
            總數 += len(區塊)
            if 總數 > 上限:
                raise OSError
        讀取後 = os.fstat(描述元)
        if _身分(讀取前) != _身分(讀取後) or 總數 != 讀取前.st_size:
            raise OSError
        資料 = b"".join(區塊列)
        return _穩定檔案(資料, 讀取後.st_dev, 讀取後.st_ino, 讀取後.st_size, 讀取後.st_mtime_ns)
    finally:
        os.close(描述元)


def _讀取相對檔案(
    根描述元: int, 路徑: str, 目錄身分: dict[str, tuple[int, int]], *,
    預期大小: int | None, 上限: int,
) -> _穩定檔案:
    """descriptor-relative 讀取 canonical bundle path 並關閉 intermediate descriptors。

    參數：bundle descriptor、canonical 路徑、目錄 identity map、size 與上限。
    回傳：穩定檔案 observation。例外：路徑任一層不安全時傳出 ``OSError``。
    副作用：短暫開啟路徑中的目錄與檔案，最後關閉所有新增描述元。
    """
    父描述元, 已開啟 = _開啟父目錄(根描述元, 路徑, 目錄身分)
    try:
        return _讀取穩定檔案(
            父描述元, PurePosixPath(路徑).parts[-1], 預期大小=預期大小, 上限=上限,
        )
    finally:
        for 描述元 in reversed(已開啟):
            os.close(描述元)


def _安全關閉(描述元: int | None) -> None:
    """盡力關閉 loader 擁有的 descriptor。

    參數：``描述元`` 是可選檔案描述元。回傳：無。例外：所有關閉錯誤皆被抑制。
    副作用：若值有效則嘗試關閉一次。
    """
    if 描述元 is not None:
        try:
            os.close(描述元)
        except BaseException:
            pass


def _清除框架(錯誤: BaseException) -> None:
    """盡力清空已停止的 hostile callback traceback frames。

    參數：``錯誤`` 是即將離開公開邊界的例外。回傳：無。例外：清理錯誤皆被抑制。
    副作用：清除 traceback 中已停止執行 frame 的 locals。
    """
    try:
        traceback.clear_frames(錯誤.__traceback__)
    except BaseException:
        pass


def _驗證完整樹(
    根描述元: int, 根身分: tuple[int, int], 清單檔案: _穩定檔案,
    檔案身分: dict[str, tuple[int, int, int, int]],
    目錄身分: dict[str, tuple[int, int]],
) -> None:
    """descriptor-relative 列舉 final tree 並拒絕任何差集、種類、模式或競態。

    參數：bundle descriptor 與先前讀取時釘選的 root、manifest、files、directories identity。
    回傳：無。例外：額外、缺失、special、symlink、模式或 identity 不符時傳出 ``OSError``。
    副作用：遞迴開啟並列舉目錄；只讀且關閉所有自行開啟的 descriptors。
    """
    預期檔案 = set(檔案身分)
    預期檔案.add("manifest.json")
    預期目錄 = set(目錄身分)
    實際檔案: set[str] = set()
    實際目錄: set[str] = set()

    def 走訪(目錄描述元: int, 前綴: str) -> None:
        """走訪一個已釘選 final directory。

        參數：目前 descriptor 與 bundle-relative 前綴。回傳：無。
        例外：tree contract 不符時傳出 ``OSError``。副作用：列舉並短暫開啟子目錄。
        """
        for 名稱 in os.listdir(目錄描述元):
            相對路徑 = 名稱 if not 前綴 else f"{前綴}/{名稱}"
            可見 = os.stat(名稱, dir_fd=目錄描述元, follow_symlinks=False)
            if stat.S_ISDIR(可見.st_mode):
                if 相對路徑 not in 預期目錄 or stat.S_IMODE(可見.st_mode) != 0o555:
                    raise OSError
                子描述元 = os.open(名稱, os.O_RDONLY | _僅目錄 | _不可跟隨, dir_fd=目錄描述元)
                try:
                    釘選 = os.fstat(子描述元)
                    身分 = (釘選.st_dev, 釘選.st_ino)
                    if 身分 != (可見.st_dev, 可見.st_ino) or 身分 != 目錄身分[相對路徑]:
                        raise OSError
                    實際目錄.add(相對路徑)
                    走訪(子描述元, 相對路徑)
                finally:
                    os.close(子描述元)
            elif stat.S_ISREG(可見.st_mode):
                if 相對路徑 not in 預期檔案 or stat.S_IMODE(可見.st_mode) != 0o444:
                    raise OSError
                預期身分 = (
                    (清單檔案.裝置, 清單檔案.索引節點, 清單檔案.位元組數, 清單檔案.修改奈秒)
                    if 相對路徑 == "manifest.json" else 檔案身分[相對路徑]
                )
                if _身分(可見) != 預期身分:
                    raise OSError
                實際檔案.add(相對路徑)
            else:
                raise OSError

    走訪(根描述元, "")
    結束根 = os.fstat(根描述元)
    if (
        (結束根.st_dev, 結束根.st_ino) != 根身分
        or 實際檔案 != 預期檔案 or 實際目錄 != 預期目錄
    ):
        raise OSError


class 已發布技能套件載入器:
    """以釘選描述元載入且完整重驗 exact 已發布技能套件。

    欄位：保存 lexical absolute 發布根與定位提供者。回傳：不適用。
    例外：建構或載入失敗時拋出固定載入錯誤。副作用：建構不存取檔案系統。
    """

    def __init__(self, 發布根目錄: str | Path, 定位提供者: 技能套件定位提供者) -> None:
        """保存無 cwd/home fallback 的 absolute 發布根與 provider。

        參數：``發布根目錄`` 必須是 exact str 或平台 Path；``定位提供者`` 提供 exact lookup。
        回傳：無。例外：根路徑不合契約時拋出固定載入錯誤。副作用：不執行 filesystem I/O。
        """
        try:
            if type(發布根目錄) not in (str, type(Path())):
                raise ValueError
            根字串 = os.fspath(發布根目錄)
            if not os.path.isabs(根字串) or "\x00" in 根字串 or unicodedata.normalize("NFC", 根字串) != 根字串:
                raise ValueError
            正規根 = os.path.normpath(根字串)
            if 正規根 != 根字串.rstrip(os.sep) and not (根字串 == os.sep == 正規根):
                raise ValueError
            self._發布根目錄 = 正規根
            self._定位提供者 = 定位提供者
        except _控制流程:
            raise
        except BaseException:
            raise 技能套件載入錯誤() from None

    def 載入技能套件快照(
        self, endpoint_version_id: str, skill_bundle_hash: str,
        manifest_reference: str, source: str,
    ) -> 技能套件快照:
        """載入 exact version、receipt 與 canonical manifest 所釘選的內容。

        參數：四個 scalar 必須分別符合版本、內容摘要、參照及固定來源契約。
        回傳：含 canonical manifest bytes 與分離摘要的完整 immutable ``技能套件快照``。
        例外：普通失敗固定且無鏈；控制流程例外保持 identity 與 args。
        副作用：預檢後呼叫 provider 一次，descriptor-relative 讀取並關閉完整 bundle。
        """
        根描述元 = 套件描述元 = None
        取得定位 = 定位值 = 定位 = 清單檔案 = 清單 = 結果 = None
        檔案列: list[技能套件檔案] | None = None
        try:
            if (
                not _是識別碼(endpoint_version_id) or not _是摘要(skill_bundle_hash)
                or not _是清單參照(manifest_reference)
                or type(source) is not str or source != _唯一來源
            ):
                raise ValueError
            取得定位 = getattr(self._定位提供者, "取得技能套件定位")
            定位值 = 取得定位(endpoint_version_id)
            定位 = _重建定位(定位值)
            if (
                定位.version_id != endpoint_version_id
                or not hmac.compare_digest(定位.bundle_hash, skill_bundle_hash)
                or 定位.manifest_reference != manifest_reference
            ):
                raise ValueError
            根描述元 = _開啟發布根(self._發布根目錄)
            套件描述元 = _開啟套件根(根描述元, 定位.bundle_id)
            根資訊 = os.fstat(套件描述元)
            根身分 = (根資訊.st_dev, 根資訊.st_ino)
            目錄身分: dict[str, tuple[int, int]] = {}
            清單檔案 = _讀取相對檔案(
                套件描述元, "manifest.json", 目錄身分,
                預期大小=None, 上限=技能套件最大總位元組數,
            )
            if not hmac.compare_digest(hashlib.sha256(清單檔案.資料).hexdigest(), 定位.manifest_digest):
                raise ValueError
            清單 = 驗證已發布技能套件清單(清單檔案.資料)
            if (
                清單.manifest_digest != 定位.manifest_digest
                or 清單.bundle_id != 定位.bundle_id
                or 清單.endpoint_version_id != 定位.version_id
                or 清單.bundle_hash != 定位.bundle_hash
                or 清單.total_bytes != 定位.total_bytes
            ):
                raise ValueError
            檔案列 = []
            檔案身分: dict[str, tuple[int, int, int, int]] = {}
            實讀總數 = 0
            for 項目 in 清單.copied_files:
                檔案 = _讀取相對檔案(
                    套件描述元, 項目.path, 目錄身分,
                    預期大小=項目.size_bytes, 上限=限制().最大檔案位元組數,
                )
                if not hmac.compare_digest(hashlib.sha256(檔案.資料).hexdigest(), 項目.sha256):
                    raise ValueError
                實讀總數 += len(檔案.資料)
                if 實讀總數 > 定位.total_bytes:
                    raise ValueError
                檔案身分[項目.path] = (
                    檔案.裝置, 檔案.索引節點, 檔案.位元組數, 檔案.修改奈秒,
                )
                檔案列.append(技能套件檔案(path=項目.path, sha256=項目.sha256, content=檔案.資料))
            if 實讀總數 != 清單.total_bytes or 實讀總數 != 定位.total_bytes:
                raise ValueError
            _驗證完整樹(套件描述元, 根身分, 清單檔案, 檔案身分, 目錄身分)
            結果 = 技能套件快照(
                endpoint_version_id=定位.version_id, skill_bundle_hash=定位.bundle_hash,
                manifest_digest=定位.manifest_digest, 清單原始資料=清單檔案.資料,
                files=tuple(檔案列),
            )
            return 結果
        except _控制流程 as 錯誤:
            _清除框架(錯誤)
            endpoint_version_id = skill_bundle_hash = manifest_reference = source = ""
            取得定位 = 定位值 = 定位 = 清單檔案 = 清單 = 結果 = None
            檔案列 = None
            raise
        except BaseException as 錯誤:
            _清除框架(錯誤)
            endpoint_version_id = skill_bundle_hash = manifest_reference = source = ""
            取得定位 = 定位值 = 定位 = 清單檔案 = 清單 = 結果 = None
            檔案列 = None
            raise 技能套件載入錯誤() from None
        finally:
            _安全關閉(套件描述元)
            _安全關閉(根描述元)
