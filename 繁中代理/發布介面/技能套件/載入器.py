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
