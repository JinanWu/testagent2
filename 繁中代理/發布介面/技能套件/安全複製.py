"""以釘選目錄描述元安全掃描並重驗技能來源檔案。

參數／欄位：不適用；本模組定義掃描限制、安全錯誤與來源重驗操作。
回傳：不適用；各公開操作的回傳契約由其文件字串分別說明。
例外：匯入相依模組失敗時原樣傳出匯入例外。
副作用：匯入時只定義型別、常數與函式，不掃描或修改檔案系統。
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import os
from pathlib import Path, PurePosixPath
import stat



class 技能套件安全錯誤(RuntimeError):
    """表示來源不安全、超限，或在掃描與重驗之間改變。

    參數：沿用 ``RuntimeError`` 的訊息參數。
    回傳：不適用；本類別用作技能來源關閉失敗訊號。
    例外：建構本身只可能傳出基底例外的標準錯誤。
    副作用：建立錯誤物件不會存取檔案系統。
    """


@dataclass(frozen=True, slots=True)
class 限制:
    """定義來源掃描及整個套件共享的安全資源上限。

    參數：依序限制一般檔案數、全部內容位元組數、單檔位元組數、目錄深度與
    UTF-8 相對路徑位元組數。回傳：建立不可變限制值。
    例外：欄位建構不主動拋出例外。副作用：只保存整數，沒有外部副作用。
    """

    最大檔案數: int = 256
    最大總位元組數: int = 4 * 1024 * 1024
    最大檔案位元組數: int = 1024 * 1024
    最大深度: int = 32
    最大路徑位元組數: int = 1024


_排除目錄 = {".git", "node_modules", "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache"}
_排除檔名 = {".DS_Store"}
_排除尾碼 = (".pyc", ".pyo", ".swp", ".swo", ".tmp", ".temp", ".bak", "~")
_不可跟隨 = getattr(os, "O_NOFOLLOW", 0)
_僅目錄 = getattr(os, "O_DIRECTORY", 0)


def _判斷排除原因(名稱: str, 是目錄: bool) -> str | None:
    """判斷來源項目是否符合固定排除規則。

    參數：``名稱`` 是單一目錄項目名稱；``是目錄`` 表示項目種類。
    回傳：固定清單原因碼，或不應排除時的 ``None``。
    例外：沒有預期例外。
    副作用：只查詢模組內常數，不修改輸入或檔案系統。
    """
    if 是目錄 and 名稱 in _排除目錄:
        return "fixed_excluded_directory"
    if not 是目錄 and (名稱 in _排除檔名 or 名稱.endswith(_排除尾碼)):
        return "fixed_excluded_file"
    return None


def _安全絕對路徑(路徑: Path) -> Path:
    """取得不含任何符號連結元件的詞法絕對路徑。

    參數：``路徑`` 是待檢查的來源根。回傳：未解析符號連結的絕對路徑。
    例外：任一元件是符號連結、缺失或查詢失敗時拋出 ``技能套件安全錯誤``。
    副作用：逐層查詢路徑 metadata，不修改檔案系統。
    """
    絕對路徑 = Path(os.path.abspath(os.fspath(路徑)))
    目前路徑 = Path(絕對路徑.anchor)
    try:
        for 部件 in 絕對路徑.parts[1:]:
            目前路徑 /= 部件
            if stat.S_ISLNK(目前路徑.lstat().st_mode):
                raise OSError
    except (OSError, ValueError):
        raise 技能套件安全錯誤("技能來源不安全") from None
    return 絕對路徑


def _開啟根目錄(路徑: Path) -> tuple[int, Path]:
    """開啟並釘選非符號連結的來源根目錄。

    參數：``路徑`` 是待開啟的技能根目錄。
    回傳：由呼叫端負責關閉的唯讀目錄描述元及安全絕對路徑。
    例外：路徑不是安全目錄或開啟失敗時拋出 ``技能套件安全錯誤``。
    副作用：短暫查詢路徑並開啟描述元；失敗時會關閉已開啟描述元。
    """
    目前描述元: int | None = None
    try:
        安全路徑 = _安全絕對路徑(路徑)
        目前描述元 = os.open(安全路徑.anchor, os.O_RDONLY | _僅目錄 | _不可跟隨)
        for 部件 in 安全路徑.parts[1:]:
            子描述元 = os.open(
                部件, os.O_RDONLY | _僅目錄 | _不可跟隨, dir_fd=目前描述元
            )
            os.close(目前描述元)
            目前描述元 = 子描述元
        開啟前 = 安全路徑.lstat()
        開啟後 = os.fstat(目前描述元)
        if (開啟前.st_dev, 開啟前.st_ino) != (開啟後.st_dev, 開啟後.st_ino):
            raise OSError
        描述元 = 目前描述元
        目前描述元 = None
        return 描述元, 安全路徑
    except 技能套件安全錯誤:
        raise
    except (OSError, ValueError):
        raise 技能套件安全錯誤("技能來源不安全") from None
    finally:
        if 目前描述元 is not None:
            os.close(目前描述元)


def _是外部符號連結(根目錄: Path, 相對路徑: str) -> bool:
    """判斷符號連結的解析位置是否逃出技能根目錄。

    參數：``根目錄`` 是來源根；``相對路徑`` 是待檢查連結。
    回傳：逃出根目錄或無法安全解析時為真，否則為假。
    例外：解析錯誤會關閉成真，不向外傳出。
    副作用：查詢檔案系統路徑，不修改任何檔案。
    """
    try:
        解析位置 = (根目錄 / 相對路徑).resolve(strict=False)
        解析位置.relative_to(根目錄.resolve(strict=True))
        return False
    except (OSError, ValueError):
        return True


def _讀取全部(描述元: int, 最大位元組數: int) -> bytes:
    """在明確上限內讀完已開啟檔案。

    參數：``描述元`` 是呼叫端擁有的可讀檔案；``最大位元組數`` 是內容上限。
    回傳：依序串接的全部檔案內容。
    例外：超限時拋出 ``技能套件安全錯誤``；系統讀取錯誤原樣傳出。
    副作用：推進描述元游標，但不關閉描述元，也不修改檔案。
    """
    區塊列: list[bytes] = []
    總數 = 0
    while True:
        區塊 = os.read(描述元, min(65536, 最大位元組數 + 1 - 總數))
        if not 區塊:
            return b"".join(區塊列)
        區塊列.append(區塊)
        總數 += len(區塊)
        if 總數 > 最大位元組數:
            raise 技能套件安全錯誤("技能來源超過限制")

