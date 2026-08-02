"""PUB P04 端點發布 DTO 與 SQLite 原子交易。"""

from __future__ import annotations

import json
import hashlib
import math
import os

import re
import sqlite3
import stat
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, NoReturn

from ..憑證.儲存庫 import _allowlist_json有效
from .綱要 import 發布值確認, 規劃草稿, _重建公開草稿

_JSON_UTF8上限 = 1024 * 1024
_字串UTF8上限 = 64 * 1024
_識別上限 = 128
_最多節點 = 10_000
_最大深度 = 64
_識別格式 = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}\Z")
_禁止秘密鍵 = re.compile(
    r"(?:^|[_-])(?:api[_-]?key|raw[_-]?key|provider[_-]?token|access[_-]?token|"
    r"refresh[_-]?token|authorization|password|private[_-]?key|secret)(?:$|[_-])",
    re.IGNORECASE,
)
_輸入錯誤訊息 = "端點發布輸入無效"
_發布錯誤訊息 = "端點發布失敗"
_schema指紋 = "3e33dee54d88e50cdd217a277087f692c6b4b341d9c7befbdd399af2fe957939"
_遷移ledger = (
    (1, "0001_建立發布端點核心.sql"), (2, "0002_建立憑證與稽核.sql"),
    (3, "0003_建立呼叫事件與工具紀錄.sql"), (4, "0004_建立限流與遮蔽資料.sql"),
    (5, "0005_建立網頁工作階段.sql"), (6, "0006_擴充稽核事件契約.sql"),
    (7, "0007_建立不可逆遮蔽墓碑.sql"), (8, "0008_建立五年保存候選索引.sql"),
    (9, "0009_建立保存相依識別索引.sql"), (10, "0010_建立來源驗證失敗節流.sql"),
    (11, "0011_重建空憑證為CRED結構.sql"),
)


class 端點發布輸入錯誤(ValueError):
    """代表發布 DTO、草稿或純量未通過 exact preflight。"""


class 端點發布錯誤(RuntimeError):
    """代表資料庫發布無法原子完成。"""


@dataclass(frozen=True, slots=True)
class 發布版本快照:
    """完整對應 published v1 欄位的 detached 快照。"""

    original_requirement_text: str = field(repr=False)
    system_prompt: str = field(repr=False)
    allowed_skills: list[str] = field(repr=False)
    allowed_tools: list[str] = field(repr=False)
    tool_schema_snapshot: dict[str, Any] = field(repr=False)
    tool_runtime_revision: str
    model_config_snapshot: dict[str, Any] = field(repr=False)
    retry_policy: dict[str, Any] = field(repr=False)
    skill_bundle_manifest: dict[str, Any] = field(repr=False)
    input_schema: dict[str, Any] | None = field(repr=False)
    response_schema: dict[str, Any] = field(repr=False)
    created_by_user_id: str

    def __post_init__(self) -> None:
        """以單次 exact traversal 建立 module-owned JSON tree。"""
        try:
            _重建版本快照(self, 建構中=True)
        except BaseException:
            del self
            raise


@dataclass(frozen=True, slots=True, repr=False)
class 已準備初始憑證:
    """只攜帶已加密憑證材料，結構上不可能接收 plaintext key。"""

    name: str
    purpose: str
    key_version: int
    key_nonce: bytes
    key_ciphertext: bytes
    key_hash: str
    key_prefix: str
    key_last4: str
    expires_at: float
    ip_allowlist: list[Any]
    rate_limit_requests: int
    created_by_user_id: str

    def __post_init__(self) -> None:
        """驗證已準備密文、hash、生命週期與 canonical allowlist。"""
        try:
            _重建初始憑證(self, 建構中=True)
        except BaseException:
            del self
            raise


@dataclass(frozen=True, slots=True)
class 端點發布結果:
    """只揭露新圖形的非敏感識別碼與固定 v1 狀態。"""

    endpoint_id: str
    version_id: str
    credential_id: str
    service_account_id: str
    version_number: int = field(default=1, init=False)
    status: str = field(default="active", init=False)

    def __post_init__(self) -> None:
        """拒絕偽造固定結果或非法識別碼。"""
        if type(self) is not 端點發布結果:
            _拒絕輸入()
        for 值 in (self.endpoint_id, self.version_id, self.credential_id, self.service_account_id):
            if not _是識別(值):
                _拒絕輸入()
        if self.version_number != 1 or self.status != "active":
            _拒絕輸入()


def _發布前驗證(owner: Any, draft: Any, snapshot: Any, credential: Any, now: Any) -> tuple[Any, ...]:
    """不觸發 callback/DB 地重建 DTO，並精確投影釘選能力摘要。"""
    草稿副本 = 版本副本 = 憑證副本 = 確認 = 綱要 = 摘要 = 項目 = None
    manifest = manifest技能 = schema = 投影 = 結果 = None
    技能: list[str] = []
    工具: list[str] = []
    失敗 = not _是識別(owner) or not _是有限非負(now) or type(draft) is not 規劃草稿
    try:
        if not 失敗:
            失敗 = object.__getattribute__(draft, "擁有者識別碼") != owner
        if not 失敗:
            草稿副本 = _重建公開草稿(draft)
            失敗 = type(草稿副本) is not 規劃草稿
        if not 失敗:
            失敗 = 草稿副本.狀態 != "draft" or now >= 草稿副本.到期時間
        if not 失敗:
            確認 = 草稿副本.發布確認
            失敗 = type(確認) is not 發布值確認 or 確認.草稿識別碼 != 草稿副本.草稿識別碼 or 確認.草稿世代 != 草稿副本._世代
        if not 失敗:
            版本副本 = _重建版本快照(snapshot)
            憑證副本 = _重建初始憑證(credential)
            失敗 = 版本副本.created_by_user_id != owner or 憑證副本.created_by_user_id != owner
        if not 失敗:
            綱要 = 草稿副本.綱要
            失敗 = type(綱要) is not dict or type(綱要.get("system_prompt")) is not str
        if not 失敗:
            失敗 = 版本副本.original_requirement_text != 草稿副本.原始需求 or 版本副本.system_prompt != 綱要["system_prompt"]
        if not 失敗:
            失敗 = 版本副本.response_schema != 確認.response_schema or 憑證副本.rate_limit_requests != 確認.credential_limit
        if not 失敗:
            摘要 = 草稿副本.能力摘要
            if 摘要 is not None:
                for 項目 in 摘要.技能:
                    技能.append(項目.名稱)
                    項目 = None
                for 項目 in 摘要.工具:
                    工具.append(項目.名稱)
                    項目 = None
                失敗 = 技能 != 版本副本.allowed_skills or 工具 != 版本副本.allowed_tools
                manifest = 版本副本.skill_bundle_manifest
                manifest技能 = manifest.get("skills") if type(manifest) is dict else None
                if type(manifest) is not dict or manifest.get("permission_revision") != 摘要.權限修訂 or type(manifest技能) is not list or len(manifest技能) != len(摘要.技能):
                    失敗 = True
                if not 失敗:
                    for 索引 in range(len(摘要.技能)):
                        項目 = 摘要.技能[索引]
                        投影 = manifest技能[索引]
                        if type(投影) is not dict or len(投影) != 2 or "name" not in 投影 or "content_sha256_reference" not in 投影 or 投影["name"] != 項目.名稱 or 投影["content_sha256_reference"] != 項目.內容sha256參照:
                            失敗 = True
                            break
                        項目 = 投影 = None
                if type(版本副本.tool_schema_snapshot) is not dict or len(版本副本.tool_schema_snapshot) != len(工具) or frozenset(dict.keys(版本副本.tool_schema_snapshot)) != frozenset(工具):
                    失敗 = True
                if not 失敗:
                    for 項目 in 摘要.工具:
                        schema = 版本副本.tool_schema_snapshot[項目.名稱]
                        if type(schema) is not dict or schema.get("revision") != 項目.釘選修訂:
                            失敗 = True
                            break
                        項目 = schema = None
        if not 失敗:
            結果 = (草稿副本, 版本副本, 憑證副本, 確認)
    except (KeyboardInterrupt, SystemExit, GeneratorExit):
        技能.clear()
        工具.clear()
        del owner, draft, snapshot, credential, now, 草稿副本, 版本副本, 憑證副本, 確認, 綱要, 摘要, 項目, manifest, manifest技能, schema, 投影, 結果, 技能, 工具, 失敗
        raise
    except BaseException:
        失敗 = True
    技能.clear()
    工具.clear()
    if 失敗 or 結果 is None:
        del owner, draft, snapshot, credential, now, 草稿副本, 版本副本, 憑證副本, 確認, 綱要, 摘要, 項目, manifest, manifest技能, schema, 投影, 結果, 技能, 工具, 失敗
        _拒絕輸入()
    del owner, draft, snapshot, credential, now, 草稿副本, 版本副本, 憑證副本, 確認, 綱要, 摘要, 項目, manifest, manifest技能, schema, 投影, 技能, 工具, 失敗
    return 結果


def _呼叫發布callbacks(工廠: tuple[Callable[[], str], ...], 時鐘: Callable[[], float]) -> tuple[Any, ...]:
    """在任何 open 前一次完成四個識別工廠與時鐘。"""
    值: list[Any] = []
    callback = 輸出 = 結果 = None
    callback失敗 = False
    try:
        for callback in 工廠:
            輸出 = callback()
            值.append(輸出)
            callback = 輸出 = None
        輸出 = 時鐘()
        值.append(輸出)
        輸出 = None
    except (KeyboardInterrupt, SystemExit, GeneratorExit):
        值.clear()
        del 工廠, 時鐘, 值, callback, 輸出, 結果, callback失敗
        raise
    except BaseException:
        值.clear()
        callback失敗 = True
    if not callback失敗 and len(值) == 5:
        for 輸出 in 值[:4]:
            if not _是識別(輸出):
                callback失敗 = True
                break
            輸出 = None
    else:
        callback失敗 = True
    if callback失敗 or len(set(值[:4])) != 4 or not _是有限非負(值[4]):
        值.clear()
        del 工廠, 時鐘, 值, callback, 輸出, 結果, callback失敗
        _拒絕發布()
    結果 = tuple(值)
    值.clear()
    del 工廠, 時鐘, 值, callback, 輸出, callback失敗
    return 結果


def _驗證既有資料庫路徑(原路徑: Any) -> tuple[Path, tuple[int, int]]:
    """拒絕 missing、symlink、非 regular 與空檔，再釘住解析後 inode。"""
    路徑 = 前 = 解析 = 後 = 結果 = None
    失敗 = False
    try:
        路徑 = Path(原路徑).expanduser()
        前 = 路徑.lstat()
        if stat.S_ISLNK(前.st_mode) or not stat.S_ISREG(前.st_mode) or 前.st_size <= 0:
            raise ValueError
        解析 = 路徑.resolve(strict=True)
        後 = 解析.stat()
        if (前.st_dev, 前.st_ino) != (後.st_dev, 後.st_ino):
            raise ValueError
        結果 = (解析, (後.st_dev, 後.st_ino))
    except (KeyboardInterrupt, SystemExit, GeneratorExit):
        del 原路徑, 路徑, 前, 解析, 後, 結果, 失敗
        raise
    except BaseException:
        失敗 = True
    if 失敗 or 結果 is None:
        del 原路徑, 路徑, 前, 解析, 後, 結果, 失敗
        _拒絕發布()
    del 原路徑, 路徑, 前, 解析, 後, 失敗
    return 結果


def _驗證已開啟資料庫路徑(連線: sqlite3.Connection, 路徑: Path, 身分: tuple[int, int]) -> None:
    """連線建立後驗證 inode；任何失敗均在傳播前恰關閉一次。"""
    路徑狀態 = 資料庫列 = 列 = 主檔 = 主檔狀態 = None
    失敗 = False
    關閉控制: list[BaseException] = []
    try:
        路徑狀態 = 路徑.lstat()
        資料庫列 = 連線.execute("PRAGMA database_list").fetchall()
        for 列 in 資料庫列:
            if 列[1] == "main":
                主檔 = 列[2]
                break
            列 = None
        if 主檔 is None:
            raise ValueError
        主檔狀態 = os.stat(主檔)
        失敗 = (
            stat.S_ISLNK(路徑狀態.st_mode) or not stat.S_ISREG(路徑狀態.st_mode)
            or (路徑狀態.st_dev, 路徑狀態.st_ino) != 身分
            or (主檔狀態.st_dev, 主檔狀態.st_ino) != 身分
        )
    except (KeyboardInterrupt, SystemExit, GeneratorExit) as 控制:
        _清除例外鏈(控制)
        關閉控制 = _安全關閉(連線)
        關閉控制.clear()
        _清除例外鏈(控制)
        del 控制
        del 連線, 路徑, 身分, 路徑狀態, 資料庫列, 列, 主檔, 主檔狀態, 失敗, 關閉控制
        raise
    except BaseException:
        失敗 = True
    if 失敗:
        關閉控制 = _安全關閉(連線)
        del 連線, 路徑, 身分, 路徑狀態, 資料庫列, 列, 主檔, 主檔狀態, 失敗
        if 關閉控制:
            _拋出清理控制(關閉控制.pop())
        del 關閉控制
        _拒絕發布()
    del 連線, 路徑, 身分, 路徑狀態, 資料庫列, 列, 主檔, 主檔狀態, 失敗, 關閉控制


def _建立JSON副本(來源: Any, *, 拒絕秘密鍵: bool = False) -> Any:
    """以 exact built-ins 單次走訪建立 bounded canonical JSON tree。"""
    計數 = [0]
    描述: list[tuple[Any, tuple[Any, ...]]] = []
    結果 = 容器 = 原項目 = 目前項目 = 原值 = 目前值 = 編碼 = None
    索引 = 0
    try:
        結果 = _複製JSON節點(來源, set(), 0, 計數, 描述, 拒絕秘密鍵)
        for 容器, 原項目 in 描述:
            目前項目 = tuple(list.__iter__(容器)) if type(容器) is list else tuple(dict.items(容器))
            if len(目前項目) != len(原項目):
                raise ValueError
            for 索引 in range(len(原項目)):
                原值 = 原項目[索引]
                目前值 = 目前項目[索引]
                if type(容器) is list:
                    if 目前值 is not 原值:
                        raise ValueError
                elif 目前值[0] is not 原值[0] or 目前值[1] is not 原值[1]:
                    raise ValueError
                原值 = 目前值 = None
            容器 = 原項目 = 目前項目 = None
        編碼 = json.dumps(結果, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
        if len(編碼) > _JSON_UTF8上限:
            raise ValueError
    except BaseException:
        描述.clear()
        計數.clear()
        if type(結果) is list or type(結果) is dict:
            結果.clear()
        del 來源, 拒絕秘密鍵, 計數, 描述, 結果, 容器, 原項目, 目前項目, 原值, 目前值, 編碼, 索引
        raise
    描述.clear()
    計數.clear()
    del 來源, 拒絕秘密鍵, 計數, 描述, 容器, 原項目, 目前項目, 原值, 目前值, 編碼, 索引
    return 結果


def _複製JSON節點(
    來源: Any, 路徑: set[int], 深度: int, 計數: list[int],
    描述: list[tuple[Any, tuple[Any, ...]]], 拒絕秘密鍵: bool,
) -> Any:
    """遞迴複製 exact JSON；每一個遞迴 traceback frame 都自行清除。"""
    容器識別 = 原項目 = 項目 = 鍵 = 已複製 = 結果 = None
    結果串列: list[Any] = []
    結果物件: dict[str, Any] = {}
    已加入路徑 = False
    try:
        計數[0] += 1
        if 計數[0] > _最多節點 or 深度 > _最大深度:
            raise ValueError
        if 來源 is None or type(來源) is bool or type(來源) is int:
            結果 = 來源
        elif type(來源) is float:
            if not math.isfinite(來源):
                raise ValueError
            結果 = 來源
        elif type(來源) is str:
            if len(來源.encode("utf-8")) > _字串UTF8上限:
                raise ValueError
            結果 = 來源
        else:
            if type(來源) not in (list, dict):
                raise ValueError
            容器識別 = id(來源)
            if 容器識別 in 路徑:
                raise ValueError
            路徑.add(容器識別)
            已加入路徑 = True
            if type(來源) is list:
                原項目 = tuple(list.__iter__(來源))
                描述.append((來源, 原項目))
                for 項目 in 原項目:
                    已複製 = _複製JSON節點(項目, 路徑, 深度 + 1, 計數, 描述, 拒絕秘密鍵)
                    結果串列.append(已複製)
                    項目 = 已複製 = None
                結果 = 結果串列
            else:
                原項目 = tuple(dict.items(來源))
                描述.append((來源, 原項目))
                for 鍵, 項目 in 原項目:
                    if type(鍵) is not str or (拒絕秘密鍵 and _禁止秘密鍵.search(鍵)):
                        raise ValueError
                    已複製 = _複製JSON節點(項目, 路徑, 深度 + 1, 計數, 描述, 拒絕秘密鍵)
                    結果物件[鍵] = 已複製
                    鍵 = 項目 = 已複製 = None
                結果 = 結果物件
            路徑.remove(容器識別)
            已加入路徑 = False
    except BaseException:
        if 已加入路徑 and type(容器識別) is int:
            路徑.discard(容器識別)
        描述.clear()
        計數.clear()
        結果串列.clear()
        結果物件.clear()
        if type(結果) is list or type(結果) is dict:
            結果.clear()
        del 來源, 路徑, 深度, 計數, 描述, 拒絕秘密鍵, 容器識別, 原項目, 項目, 鍵, 已複製, 結果, 結果串列, 結果物件, 已加入路徑
        raise
    結果串列 = []
    結果物件 = {}
    del 來源, 路徑, 深度, 計數, 描述, 拒絕秘密鍵, 容器識別, 原項目, 項目, 鍵, 已複製, 結果串列, 結果物件, 已加入路徑
    return 結果


def _是字串陣列(值: Any) -> bool:
    """確認 exact list 只含唯一、bounded exact strings。"""
    if type(值) is not list:
        return False
    已見: set[str] = set()
    for 項目 in 值:
        if not _是識別(項目) or 項目 in 已見:
            return False
        已見.add(項目)
    return True


def _是文字(值: Any) -> bool:
    """確認非空 bounded exact UTF-8 string。"""
    return _是短文字(值, _字串UTF8上限)


def _是短文字(值: Any, 上限: int) -> bool:
    """確認 exact string 非空、無前後空白且字元長度 bounded。"""
    return type(值) is str and 值.strip() == 值 and bool(值) and len(值) <= 上限


def _是識別(值: Any) -> bool:
    """確認 bounded canonical identifier。"""
    return type(值) is str and len(值) <= _識別上限 and _識別格式.fullmatch(值) is not None


def _是有限非負(值: Any) -> bool:
    """確認 exact int/float 可安全轉成有限非負 REAL。"""
    if type(值) not in (int, float):
        return False
    try:
        return math.isfinite(值) and 值 >= 0
    except (OverflowError, ValueError):
        return False


def _是正整數(值: Any) -> bool:
    """確認 SQLite 可接受的 bounded 正整數。"""
    return type(值) is int and 0 < 值 <= 2**63 - 1


def _拒絕輸入() -> NoReturn:
    """清除呼叫 frame 後建立固定且不鏈結的輸入錯誤。"""
    raise 端點發布輸入錯誤(_輸入錯誤訊息) from None


def _拒絕發布() -> NoReturn:
    """建立固定、fresh 且不鏈結底層 SQLite/callback 錯誤的發布錯誤。"""
    raise 端點發布錯誤(_發布錯誤訊息) from None
