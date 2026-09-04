"""定義技能套件清單的正規序列化與雜湊契約。

參數／欄位：不適用；本模組提供掃描資料型別與清單建構操作。
回傳：不適用；各資料型別與操作的回傳契約由其文件字串分別說明。
例外：匯入相依模組失敗時原樣傳出匯入例外。
副作用：匯入時只定義型別與函式，不讀寫來源或套件檔案。
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import PurePosixPath
import re
from typing import Any, Iterable
import unicodedata


_技能套件識別碼格式 = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}\Z")
_清單參照最大位元組數 = 142
_CloudStorage清單參照格式 = re.compile(
    r"bundles/v1/([A-Za-z0-9][A-Za-z0-9_.:-]{0,127})/manifest\.json#generation=([1-9][0-9]*)\Z"
)


def 是合法技能套件清單參照(值: object) -> bool:
    """嚴格判斷 canonical ``<bundle-id>/manifest.json`` lexical reference。

    參數：``值`` 是不可信輸入；只接受 exact ``str``、NFC、受限 bundle ID 與恰好
    兩段的相對 POSIX 路徑，拒絕反斜線、絕對路徑、空段、``.``、``..``、非 canonical
    拼法及超過 142 UTF-8 位元組的值。回傳：完全符合時為 ``True``，一般不合法輸入
    或普通 Unicode／路徑錯誤一律為 ``False``。例外：``KeyboardInterrupt``、
    ``SystemExit``、``GeneratorExit`` 等控制流程例外保持原物件傳出。
    副作用：只配置短暫字串與 lexical path，不讀寫檔案系統，也不修改輸入。
    """
    try:
        if (
            type(值) is not str or not 值 or "\\" in 值 or 值.startswith("/")
            or len(值.encode("utf-8")) > _清單參照最大位元組數
            or unicodedata.normalize("NFC", 值) != 值
        ):
            return False
        原始部件 = 值.split("/")
        if len(原始部件) != 2 or any(部件 in ("", ".", "..") for 部件 in 原始部件):
            return False
        部件 = PurePosixPath(值).parts
        return (
            len(部件) == 2 and str(PurePosixPath(值)) == 值
            and _技能套件識別碼格式.fullmatch(部件[0]) is not None
            and 部件[1] == "manifest.json"
        )
    except (TypeError, ValueError, UnicodeError):
        return False


def 是合法技能套件定位參照(值: object, 套件識別碼: object) -> bool:
    """接受exact本機清單或generation-pinned GCS清單，並核對bundle identity。

    參數：``值``是不可信locator；``套件識別碼``是同一DTO的bundle ID。
    回傳：只在本機``<bundle>/manifest.json``或GCS
    ``bundles/v1/<bundle>/manifest.json#generation=<正整數>``完全匹配時為真。
    例外：普通Unicode／型別錯誤關閉為假；控制流程例外不攔截。
    副作用：僅做bounded lexical validation，不讀取檔案或網路。
    """
    try:
        if (
            type(值) is not str or type(套件識別碼) is not str
            or _技能套件識別碼格式.fullmatch(套件識別碼) is None
            or unicodedata.normalize("NFC", 值) != 值
            or len(值.encode("utf-8")) > 200
        ):
            return False
        if 值 == f"{套件識別碼}/manifest.json":
            return 是合法技能套件清單參照(值)
        匹配 = _CloudStorage清單參照格式.fullmatch(值)
        return 匹配 is not None and 匹配.group(1) == 套件識別碼
    except (TypeError, ValueError, UnicodeError):
        return False


@dataclass(frozen=True, slots=True)
class 複製檔案:
    """記錄掃描時釘選的一個一般檔案。

    欄位：``相對路徑`` 是技能根目錄下的 POSIX 路徑；``位元組數`` 是內容長度；
    ``雜湊`` 是內容的 SHA-256 十六進位摘要；``裝置``、``索引節點`` 與
    ``修改奈秒`` 是重驗來源身分所需的檔案系統資料。
    回傳：建立保存上述欄位的不可變檔案快照。
    例外：欄位建構不主動驗證或拋出例外。
    副作用：建立實例只保存不可變資料，不讀寫檔案系統。
    """

    相對路徑: str
    位元組數: int
    雜湊: str
    裝置: int
    索引節點: int
    修改奈秒: int


@dataclass(frozen=True, slots=True)
class 釘選目錄:
    """記錄掃描時釘選的一個來源目錄。

    參數：``相對路徑`` 以空字串表示來源根，其餘使用 POSIX 路徑；``裝置`` 與
    ``索引節點`` 共同識別目錄。回傳：建立不可變目錄身分快照。
    例外：欄位建構不主動拋出例外。副作用：只保存資料，不存取檔案系統。
    """

    相對路徑: str
    裝置: int
    索引節點: int


@dataclass(frozen=True, slots=True)
class 排除項目:
    """記錄未納入套件的一個來源項目。

    欄位：``相對路徑`` 是技能根目錄下的 POSIX 路徑；``原因`` 是固定的線上契約碼。
    回傳：建立保存路徑與原因的不可變排除紀錄。
    例外：欄位建構不主動驗證或拋出例外。
    副作用：建立實例只保存不可變資料，沒有外部副作用。
    """

    相對路徑: str
    原因: str


@dataclass(frozen=True, slots=True)
class 技能掃描:
    """保存單一技能的可重播掃描快照。

    欄位：``名稱`` 是套件內技能名稱；``來源路徑`` 是絕對來源路徑；``來源雜湊``
    是此技能檔案集合摘要；``檔案`` 與 ``排除`` 分別保存納入及排除項目；``警告``
    保存要寫入清單的固定訊息；``目錄`` 保存根及納入樹中各層的釘選身分。
    參數：各欄位由安全掃描器依前述語意提供。回傳：建立不可變掃描快照。
    例外：欄位建構不主動拋出例外。副作用：只保存資料，不碰觸來源目錄。
    """

    名稱: str
    來源路徑: str
    來源雜湊: str
    檔案: tuple[複製檔案, ...]
    排除: tuple[排除項目, ...]
    警告: tuple[str, ...] = ()
    目錄: tuple[釘選目錄, ...] = ()


def 正規JSON(值: Any) -> bytes:
    """將值編碼成唯一的 UTF-8 JSON 位元組。

    參數：``值`` 是可由標準 JSON 編碼器處理的資料。
    回傳：鍵排序、無多餘空白、不跳脫非 ASCII 字元的 UTF-8 位元組。
    例外：值不可序列化或含非有限浮點數時，傳出 ``TypeError`` 或 ``ValueError``。
    副作用：只配置回傳資料，不修改輸入，也不執行外部輸入輸出。
    """
    return json.dumps(
        值,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _正規三元組(項目列: Iterable[dict[str, Any]]) -> list[list[Any]]:
    """建立依 UTF-8 路徑排序的清單雜湊輸入。

    參數：``項目列`` 內每項都須含固定線上鍵 ``path``、``size_bytes``、``sha256``。
    回傳：每項為路徑、位元組數、摘要的三元素串列。
    例外：缺少固定鍵時傳出 ``KeyError``；路徑不可編碼時傳出 ``UnicodeEncodeError``。
    副作用：會耗用一次性 iterable，但不修改其中的映射，且沒有外部副作用。
    """
    三元組 = [[項目["path"], 項目["size_bytes"], 項目["sha256"]] for 項目 in 項目列]
    return sorted(三元組, key=lambda 項目: 項目[0].encode("utf-8"))


def 計算套件雜湊(項目列: Iterable[dict[str, Any]]) -> str:
    """計算技能套件內容集合的穩定摘要。

    參數：``項目列`` 是含固定清單線上鍵的檔案項目 iterable。
    回傳：正規三元組 JSON 的 SHA-256 小寫十六進位摘要。
    例外：輸入鍵或值不合契約時傳出正規化過程的標準例外。
    副作用：會耗用一次性 iterable；不修改項目，也不讀寫外部資源。
    """
    return hashlib.sha256(正規JSON(_正規三元組(項目列))).hexdigest()


def 建立清單(
    *,
    套件識別碼: str,
    端點識別碼: str,
    端點版本識別碼: str,
    版本號碼: int,
    建立時間: float,
    建立者識別碼: str,
    掃描列: tuple[技能掃描, ...],
) -> tuple[dict[str, Any], bytes, str]:
    """建立第一版技能套件清單及兩種摘要所需資料。

    參數：識別碼、版本號碼、建立時間與建立者描述發布目標；``掃描列`` 是各技能的
    已釘選快照。回傳：依序為清單映射、正規 JSON 位元組及該位元組的 SHA-256 摘要。
    例外：欄位不能正規序列化時傳出 ``TypeError`` 或 ``ValueError``。
    副作用：只讀取掃描資料並配置新容器，不修改輸入或執行外部輸入輸出。
    """
    已複製: list[dict[str, Any]] = []
    已排除: list[dict[str, str]] = []
    來源: list[dict[str, str]] = []
    警告: list[str] = []
    for 掃描 in sorted(掃描列, key=lambda 項目: 項目.名稱.encode("utf-8")):
        技能主檔 = next((檔案 for 檔案 in 掃描.檔案 if 檔案.相對路徑 == "SKILL.md"), None)
        if 技能主檔 is None:
            raise ValueError("技能缺少 SKILL.md")
        來源.append(
            {"name": 掃描.名稱, "source_path": 掃描.來源路徑, "source_hash": 技能主檔.雜湊}
        )
        for 檔案 in 掃描.檔案:
            已複製.append(
                {
                    "path": f"{掃描.名稱}/{檔案.相對路徑}",
                    "size_bytes": 檔案.位元組數,
                    "sha256": 檔案.雜湊,
                }
            )
        for 項目 in 掃描.排除:
            已排除.append({"path": f"{掃描.名稱}/{項目.相對路徑}", "reason": 項目.原因})
        警告.extend(掃描.警告)
    已複製.sort(key=lambda 項目: 項目["path"].encode("utf-8"))
    已排除.sort(key=lambda 項目: 項目["path"].encode("utf-8"))
    雜湊表 = {項目["path"]: 項目["sha256"] for 項目 in 已複製}
    清單: dict[str, Any] = {
        "manifest_version": 1,
        "bundle_id": 套件識別碼,
        "endpoint_id": 端點識別碼,
        "endpoint_version_id": 端點版本識別碼,
        "version_number": 版本號碼,
        "created_at": 建立時間,
        "created_by_user_id": 建立者識別碼,
        "source_skills": 來源,
        "copied_files": 已複製,
        "copied_file_hashes": 雜湊表,
        "excluded_files": 已排除,
        "warnings": 警告,
        "total_bytes": sum(項目["size_bytes"] for 項目 in 已複製),
        "bundle_hash": 計算套件雜湊(已複製),
    }
    原始資料 = 正規JSON(清單)
    return 清單, 原始資料, hashlib.sha256(原始資料).hexdigest()
