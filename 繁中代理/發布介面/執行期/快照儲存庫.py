"""以單一唯讀交易載入 exact EndpointVersion 的 Published Runtime 快照。

參數／欄位：模組公開固定錯誤、工具摘要協定、loader 原生定位 DTO re-export 與
SQLite 儲存庫；儲存庫建構參數是既有 SQLite 路徑、摘要計算器及可替換連線工廠。
回傳：公開查詢只接受 exact version/service-account identity，回傳 detached runtime
DTO、權限上下文或 loader 的 exact bundle 定位。
例外：任何一般例外、資料列、JSON、結構或摘要異常都映射為固定錯誤；控制流程例外
保留 identity 與 args。
副作用：每次操作只開啟 ``mode=ro`` 連線，以 ``BEGIN`` 將中央 schema 驗證與唯一一筆
JOIN 查詢固定在同一 read transaction，最後一定 rollback 並 close；匯入時不開連線。
"""
from __future__ import annotations

import hashlib
import hmac
import json
import math
import os
from pathlib import Path
import re
import sqlite3
import stat
from typing import Any, Callable, NoReturn, Protocol

from ..資料庫結構契約 import 驗證資料庫結構
from ..技能套件.安全複製 import 技能套件最大總位元組數
from ..技能套件.清單 import 是合法技能套件清單參照
from ..技能套件.載入器 import 技能套件定位
from .執行器 import 發布執行快照
from .工具版本庫 import 工具快照項目
from .模型契約 import 設定鍵, 重建設定
from .服務帳戶 import ServiceAccountContext

_識別 = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}\Z")
_雜湊 = re.compile(r"[0-9a-f]{64}\Z")
_固定錯誤 = "發布快照不可用"
_來源 = "endpoint_version_snapshot"
_控制流程 = (KeyboardInterrupt, SystemExit, GeneratorExit)
_JSON上限 = 1_000_000
_本機路徑型別 = type(Path())

# 唯一 authority query：禁止 current/latest/slug/MAX；LIMIT 2 用於偵測不可能的重複圖形。


class 發布快照儲存庫錯誤(RuntimeError):
    """資料庫發布快照無效或不可用時的固定、無鏈結錯誤。"""


class 工具摘要計算器(Protocol):
    """Controller 注入之唯一權威工具 revision 摘要 helper。"""

    def __call__(self, name: str, revision: str, description: str,
                 parameters_json: str) -> str:
        """回傳 canonical revision 的小寫 SHA-256。"""




def _驗證列(列: object, 版本: str) -> None:
    """驗證 exact 20-column authority row 的全部 runtime invariants。

    參數：``列`` 是 JOIN 結果，``版本`` 是查詢 exact key。回傳：無。
    例外：欄數、型別、狀態、canonical 參照、摘要、額度或 lifecycle 不符時拋出
    ``ValueError``。副作用：只檢查並解析記憶體內值，不存取外部資源。
    """
    if type(列) is not tuple or len(列) != 20 or 列[0] != 版本:
        raise ValueError
    for 索引 in (0, 1, 2, 8, 16):
        if not _是識別(列[索引]):
            raise ValueError
    if type(列[5]) is not str or not 列[5].strip() or len(列[5].encode("utf-8")) > 500_000:
        raise ValueError
    if (not 是合法技能套件清單參照(列[12])
            or 列[12] != f"{列[16]}/manifest.json"):
        raise ValueError
    if 列[3] != "active" or type(列[3]) is not str or 列[4] is not None:
        raise ValueError
    for 索引 in (6, 7, 9, 10, 11):
        if type(列[索引]) is not str or len(列[索引].encode("utf-8")) > _JSON上限:
            raise ValueError
    if not _是雜湊(列[13]) or not _是雜湊(列[14]):
        raise ValueError
    if type(列[15]) is not str or 列[15] not in ("published", "reconciled"):
        raise ValueError
    if (type(列[17]) is not int or not 0 < 列[17] <= 技能套件最大總位元組數
            or not _是時間(列[18])):
        raise ValueError
    if (列[15] == "published") != (列[19] is None):
        raise ValueError
    if 列[19] is not None and (not _是時間(列[19]) or float(列[19]) < float(列[18])):
        raise ValueError


def _解析允許工具(原文: object) -> tuple[str, ...]:
    """解析 canonical unique 工具名稱陣列並轉為 immutable tuple。"""
    值 = _解析正規JSON(原文)
    if type(值) is not list or len(值) > 256:
        raise ValueError
    結果 = tuple(值)
    if any(not _是識別(項) for 項 in 結果) or len(set(結果)) != len(結果):
        raise ValueError
    return 結果


def _重建工具(允許原文: object, 綱要原文: object, 計算器: 工具摘要計算器) -> tuple[工具快照項目, ...]:
    """按 allowlist/schema exact 順序，以注入 helper 重建 revision DTO。"""
    允許 = _解析允許工具(允許原文)
    綱要 = _解析正規JSON(綱要原文)
    if type(綱要) is not dict or tuple(dict.keys(綱要)) != 允許:
        raise ValueError
    結果 = []
    for 名稱 in 允許:
        項 = dict.__getitem__(綱要, 名稱)
        if type(項) is not dict or frozenset(dict.keys(項)) != frozenset({"revision", "description", "parameters"}):
            raise ValueError
        修訂, 說明, 參數 = (dict.__getitem__(項, 鍵) for 鍵 in ("revision", "description", "parameters"))
        if not _是識別(修訂) or type(說明) is not str or type(參數) is not dict:
            raise ValueError
        摘要 = 計算器(名稱, 修訂, 說明, _正規JSON(參數))
        if not _是雜湊(摘要):
            raise ValueError
        結果.append(工具快照項目(name=名稱, revision=修訂, digest=摘要))
    return tuple(結果)


def _解析清單(原文: object) -> dict[str, Any]:
    """確認版本保存的技能清單是 canonical JSON object。"""
    值 = _解析正規JSON(原文)
    if type(值) is not dict:
        raise ValueError
    return 值


def _權限摘要(列: tuple[Any, ...]) -> str:
    """對工具、bundle 與 handler release 的唯一 canonical projection 雜湊。"""
    投影 = {"allowed_tools": list(_解析允許工具(列[6])), "skill_bundle_hash": 列[14],
          "tool_handler_release": 列[8]}
    return hashlib.sha256(_正規JSON(投影).encode("utf-8")).hexdigest()


def _解析正規JSON(原文: object) -> Any:
    """有界解析並拒絕 duplicate、nonfinite 與非 canonical JSON。"""
    if type(原文) is not str or len(原文.encode("utf-8")) > _JSON上限:
        raise ValueError
    def 物件(對):
        """由 object pairs 建立 exact dict，遇重複鍵立即拒絕。"""
        結果 = {}
        for 鍵, 值 in 對:
            if type(鍵) is not str or 鍵 in 結果:
                raise ValueError
            結果[鍵] = 值
        return 結果
    值 = json.loads(原文, object_pairs_hook=物件,
                    parse_constant=lambda _值: (_ for _ in ()).throw(ValueError()))
    if _正規JSON(值) != 原文:
        raise ValueError
    return 值


def _正規JSON(值: Any) -> str:
    """以固定 UTF-8 JSON 規則建立唯一文字表示。"""
    return json.dumps(值, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _驗證路徑(值: object) -> Path:
    """拒絕 missing、symlink、非 regular、空檔與 resolve 前後換 inode。"""
    if type(值) not in (str, _本機路徑型別):
        raise ValueError
    路徑 = Path(值)
    前 = os.lstat(路徑)
    if not stat.S_ISREG(前.st_mode) or 前.st_size <= 0:
        raise ValueError
    結果 = 路徑.resolve(strict=True)
    後 = os.stat(結果)
    if (前.st_dev, 前.st_ino) != (後.st_dev, 後.st_ino):
        raise ValueError
    return 結果


def _清理(連線: object) -> BaseException | None:
    """盡力 rollback/close；只回傳第一個 exact 控制流程例外。"""
    if 連線 is None:
        return None
    控制 = None
    try:
        連線.rollback()  # type: ignore[attr-defined]
    except _控制流程 as 錯誤:
        控制 = 錯誤
    except BaseException:
        pass
    try:
        連線.close()  # type: ignore[attr-defined]
    except _控制流程 as 錯誤:
        if 控制 is None:
            控制 = 錯誤
    except BaseException:
        pass
    return 控制


def _重拋控制(控制: BaseException) -> NoReturn:
    """去除 exception graph 後保留控制流程 identity 與 args 重拋。"""
    控制.__cause__ = 控制.__context__ = None
    控制.__suppress_context__ = True
    try:
        raise 控制.with_traceback(None)
    except _控制流程:
        del 控制
        raise


def _是識別(值: object) -> bool:
    """只接受 exact str 安全識別碼。"""
    return type(值) is str and _識別.fullmatch(值) is not None


def _是雜湊(值: object) -> bool:
    """只接受 exact 小寫 SHA-256 字串。"""
    return type(值) is str and _雜湊.fullmatch(值) is not None


def _是時間(值: object) -> bool:
    """接受 NULL 或 finite nonnegative exact SQLite 數值。"""
    return 值 is None or (type(值) in (int, float) and math.isfinite(值) and 值 >= 0)


def _拒絕() -> NoReturn:
    """拋出無輸入內容、無 exception chain 的固定公開錯誤。"""
    raise 發布快照儲存庫錯誤(_固定錯誤) from None
