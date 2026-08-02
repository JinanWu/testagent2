"""發布介面 SQLite manifest discovery 與初始化入口。"""

from __future__ import annotations

import os
import re
import sqlite3
import stat
from pathlib import Path

from .遷移執行器 import 執行遷移, 遷移執行錯誤, 遷移項目

_遷移檔名格式 = re.compile(r"^(?P<version>[0-9]{4})_[^/]+\.sql$")
_錯誤訊息 = "發布介面遷移 manifest 不符合契約"
_列舉目錄名稱 = os.listdir


def 載入發布介面遷移(
    遷移目錄: str | Path | None = None,
    *,
    資料庫路徑: str | Path | None = None,
) -> tuple[遷移項目, ...]:
    """載入 pending 遷移；參數是 manifest 目錄/資料庫路徑，回傳遷移 tuple；只讀 pending SQL，錯誤丟遷移執行錯誤。"""
    目錄 = _取得遷移目錄(遷移目錄)
    root_fd = _開啟遷移目錄(目錄)
    try:
        檔案項目 = _列舉遷移檔名(root_fd)
        已套用 = _讀取已套用ledger(資料庫路徑) if 資料庫路徑 is not None else {}
        _比對已套用名稱(檔案項目, 已套用)
        結果: list[遷移項目] = []
        for 版本, 名稱 in 檔案項目:
            if 已套用.get(版本) == 名稱:
                continue
            結果.append(遷移項目(版本, 名稱, _讀取一般檔文字(root_fd, 名稱)))
        return tuple(結果)
    finally:
        os.close(root_fd)


def 初始化發布介面資料庫(資料庫路徑: str | Path, 遷移目錄: str | Path | None = None) -> tuple[int, ...]:
    """套用 pending 遷移；參數是 SQLite 路徑/manifest 目錄，回傳套用版本；會讀寫資料庫，錯誤丟遷移執行錯誤。"""
    return 執行遷移(資料庫路徑, 載入發布介面遷移(遷移目錄, 資料庫路徑=資料庫路徑))


def _取得遷移目錄(遷移目錄: str | Path | None) -> Path:
    """解析 manifest 目錄；參數是指定目錄或 None，回傳 Path，無 I/O/例外副作用。"""
    if 遷移目錄 is None:
        return Path(__file__).with_name("遷移")
    return Path(遷移目錄)


def _開啟遷移目錄(目錄: Path) -> int:
    """開啟並釘住 manifest 目錄；參數是 Path，回傳目錄 fd；不支援安全 fd API 或非法根目錄時丟固定錯誤。"""
    if not _支援安全目錄讀取():
        _拒絕()
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    root_fd: int | None = None
    try:
        root_fd = os.open(目錄, flags)
        if not stat.S_ISDIR(os.fstat(root_fd).st_mode):
            os.close(root_fd)
            root_fd = None
            _拒絕()
    except OSError:
        if root_fd is not None:
            os.close(root_fd)
        _拒絕()
    return root_fd


def _支援安全目錄讀取() -> bool:
    """檢查平台支援；無參數，回傳是否有 O_NOFOLLOW/O_DIRECTORY/open(dir_fd)/listdir(fd)。"""
    return (
        hasattr(os, "O_NOFOLLOW")
        and hasattr(os, "O_DIRECTORY")
        and os.open in os.supports_dir_fd
        and os.listdir in os.supports_fd
    )


def _列舉遷移檔名(root_fd: int) -> tuple[tuple[int, str], ...]:
    """從已釘住目錄fd列舉檔名；參數是目錄fd，回傳排序版本檔名；非法 manifest 丟固定錯誤。"""
    try:
        名稱清單 = sorted(_列舉目錄名稱(root_fd))
    except OSError:
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
    """唯讀讀 ledger；參數是 SQLite 路徑，回傳 version/name dict；不存在不建立，讀取失敗丟固定錯誤。"""
    path = Path(資料庫路徑).expanduser()
    try:
        path.stat()
    except FileNotFoundError:
        return {}
    except OSError:
        raise 遷移執行錯誤("發布介面資料庫遷移狀態不符合契約") from None
    try:
        uri = path.resolve(strict=False).as_uri() + "?mode=ro"
        連線 = sqlite3.connect(uri, uri=True)
        try:
            exists = 連線.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='published_api_schema_migrations'"
            ).fetchone()
            if exists is None:
                return {}
            return {int(row[0]): str(row[1]) for row in 連線.execute("SELECT version,name FROM published_api_schema_migrations")}
        finally:
            連線.close()
    except (OSError, ValueError, sqlite3.Error):
        raise 遷移執行錯誤("發布介面資料庫遷移狀態不符合契約") from None


def _比對已套用名稱(檔案項目: tuple[tuple[int, str], ...], 已套用: dict[int, str]) -> None:
    """比對 manifest/ledger 名稱；參數是兩者映射，無回傳/I/O，衝突丟固定錯誤。"""
    manifest = dict(檔案項目)
    for 版本, 名稱 in 已套用.items():
        if manifest.get(版本) != 名稱:
            raise 遷移執行錯誤("發布介面資料庫遷移狀態不符合契約") from None


def _讀取一般檔文字(root_fd: int, 名稱: str) -> str:
    """從已釘住目錄fd安全讀SQL；參數是目錄fd/檔名，回傳UTF-8文字；錯誤丟固定遷移執行錯誤。"""
    if "/" in 名稱:
        _拒絕()
    flags = os.O_RDONLY | os.O_NOFOLLOW
    fd: int | None = None
    讀取失敗 = False
    try:
        fd = os.open(名稱, flags, dir_fd=root_fd)
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
