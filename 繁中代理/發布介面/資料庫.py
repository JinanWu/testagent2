"""發布介面 SQLite manifest discovery 與初始化入口。"""

from __future__ import annotations

import os
import re
import sqlite3
import stat
from pathlib import Path

from .遷移執行器 import 執行遷移, 遷移執行錯誤, 遷移項目

_遷移檔名格式 = re.compile(r"^(?P<version>[0-9]{4})_.+\.sql$")
_錯誤訊息 = "發布介面遷移 manifest 不符合契約"


def 載入發布介面遷移(
    遷移目錄: str | Path | None = None,
    *,
    資料庫路徑: str | Path | None = None,
) -> tuple[遷移項目, ...]:
    """載入 pending 遷移；參數是 manifest 目錄/資料庫路徑，回傳遷移 tuple；只讀 pending SQL，錯誤丟遷移執行錯誤。"""
    目錄 = _取得遷移目錄(遷移目錄)
    檔案項目 = _列舉遷移檔名(目錄, 拒絕根連結=遷移目錄 is not None)
    已套用 = _讀取已套用ledger(資料庫路徑) if 資料庫路徑 is not None else {}
    _比對已套用名稱(檔案項目, 已套用)
    結果: list[遷移項目] = []
    for 版本, 名稱 in 檔案項目:
        if 已套用.get(版本) == 名稱:
            continue
        結果.append(遷移項目(版本, 名稱, _讀取一般檔文字(目錄, 名稱)))
    return tuple(結果)


def 初始化發布介面資料庫(資料庫路徑: str | Path, 遷移目錄: str | Path | None = None) -> tuple[int, ...]:
    """套用 pending 遷移；參數是 SQLite 路徑/manifest 目錄，回傳套用版本；會讀寫資料庫，錯誤丟遷移執行錯誤。"""
    return 執行遷移(資料庫路徑, 載入發布介面遷移(遷移目錄, 資料庫路徑=資料庫路徑))


def _取得遷移目錄(遷移目錄: str | Path | None) -> Path:
    """解析 manifest 目錄；參數是指定目錄或 None，回傳 Path，無 I/O/例外副作用。"""
    if 遷移目錄 is None:
        return Path(__file__).with_name("遷移")
    return Path(遷移目錄)


def _列舉遷移檔名(目錄: Path, *, 拒絕根連結: bool) -> tuple[tuple[int, str], ...]:
    """列舉檔名；參數是目錄/symlink 策略，回傳排序版本檔名；會讀目錄，非法 manifest 丟遷移執行錯誤。"""
    讀取失敗 = False
    try:
        狀態 = os.lstat(目錄)
        if 拒絕根連結 and stat.S_ISLNK(狀態.st_mode):
            _拒絕()
        if not stat.S_ISDIR(狀態.st_mode):
            _拒絕()
        名稱清單 = sorted(項目.name for 項目 in 目錄.iterdir())
    except OSError:
        讀取失敗 = True
    if 讀取失敗:
        _拒絕()
    結果: list[tuple[int, str]] = []
    版本集合: set[int] = set()
    for 名稱 in 名稱清單:
        if 名稱.startswith("."):
            continue
        match = _遷移檔名格式.match(名稱)
        if match is None:
            if 名稱.endswith(".sql"):
                _拒絕()
            continue
        版本 = int(match.group("version"))
        if 版本 <= 0 or 版本 in 版本集合:
            _拒絕()
        版本集合.add(版本)
        結果.append((版本, 名稱))
    結果.sort()
    if not 結果 or [版本 for 版本, _ in 結果] != list(range(1, len(結果) + 1)):
        _拒絕()
    return tuple(結果)


def _讀取已套用ledger(資料庫路徑: str | Path) -> dict[int, str]:
    """讀 ledger；參數是 SQLite 路徑，回傳 version/name dict；會開資料庫，查詢失敗丟固定遷移執行錯誤。"""
    try:
        連線 = sqlite3.connect(str(資料庫路徑))
        try:
            exists = 連線.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='published_api_schema_migrations'"
            ).fetchone()
            if exists is None:
                return {}
            return {int(row[0]): str(row[1]) for row in 連線.execute("SELECT version,name FROM published_api_schema_migrations")}
        finally:
            連線.close()
    except sqlite3.Error:
        raise 遷移執行錯誤("發布介面資料庫遷移狀態不符合契約") from None


def _比對已套用名稱(檔案項目: tuple[tuple[int, str], ...], 已套用: dict[int, str]) -> None:
    """比對 manifest/ledger 名稱；參數是兩者映射，無回傳/I/O，衝突丟固定錯誤。"""
    manifest = dict(檔案項目)
    for 版本, 名稱 in 已套用.items():
        if manifest.get(版本) != 名稱:
            raise 遷移執行錯誤("發布介面資料庫遷移狀態不符合契約") from None


def _讀取一般檔文字(目錄: Path, 名稱: str) -> str:
    """安全讀 SQL；參數是目錄/檔名，回傳 UTF-8 文字；會 O_NOFOLLOW+fstat，讀取錯誤丟固定遷移執行錯誤。"""
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    fd: int | None = None
    讀取失敗 = False
    try:
        fd = os.open(目錄 / 名稱, flags)
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            _拒絕()
        with os.fdopen(fd, "r", encoding="utf-8", errors="strict") as 檔案:
            fd = None
            return 檔案.read()
    except (OSError, UnicodeDecodeError):
        讀取失敗 = True
    finally:
        if fd is not None:
            os.close(fd)
    if 讀取失敗:
        _拒絕()
    _拒絕()


def _拒絕() -> None:
    """拒絕 manifest；無參數/回傳，副作用是丟固定遷移執行錯誤且 from None。"""
    raise 遷移執行錯誤(_錯誤訊息) from None
