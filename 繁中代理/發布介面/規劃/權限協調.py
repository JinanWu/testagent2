"""PUB Planner 與 FND 權威權限查詢之安全協調邊界。"""

from __future__ import annotations

import hashlib
import json
import math
import re
import sqlite3
from dataclasses import dataclass, field
from typing import Any, NoReturn

from ..協定 import (
    Planner權限查詢,
    安全查詢規劃權限,
    授權工具,
    授權技能,
    規劃權限快照,
    規劃權限查詢錯誤,
)
from ..嚴格JSON import 建立正規JSON, 解析嚴格JSON
from ..連線隔離 import (
    標記發布連線污染 as _標記狀態連線污染,
    發布連線已污染 as _狀態連線已污染,
)


_識別規則 = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}\Z")
_SHA256規則 = re.compile(r"[0-9a-f]{64}\Z")
_固定錯誤 = "規劃能力未獲授權"
_控制流 = (KeyboardInterrupt, SystemExit, GeneratorExit)
_發布遷移紀錄 = (
    (1, "0001_建立發布端點核心.sql"),
    (2, "0002_建立憑證與稽核.sql"),
    (3, "0003_建立呼叫事件與工具紀錄.sql"),
    (4, "0004_建立限流與遮蔽資料.sql"),
    (5, "0005_建立網頁工作階段.sql"),
    (6, "0006_擴充稽核事件契約.sql"),
    (7, "0007_建立不可逆遮蔽墓碑.sql"),
    (8, "0008_建立五年保存候選索引.sql"),
    (9, "0009_建立保存相依識別索引.sql"),
    (10, "0010_建立來源驗證失敗節流.sql"),
    (11, "0011_重建空憑證為CRED結構.sql"),
)
_P07_SCHEMA指紋 = "6b27cff1307ecc1cbbd9ee4b7690eb0f26ed4bc775b636f9c99c4df3da2f4e62"



class 授權選擇錯誤(RuntimeError):
    """代表能力選擇或權威權限讀取無法安全完成。"""


class 發布權限協調錯誤(RuntimeError):
    """代表發布端點權限或狀態無法安全協調。"""


def _清除控制鏈(控制: BaseException) -> None:
    """清除控制流既有敏感鏈結而不改變 identity 或 args。"""
    控制.__cause__ = 控制.__context__ = None
    控制.__suppress_context__ = True


def _回滾狀態交易(連線: sqlite3.Connection) -> list[BaseException]:
    """ordinary 回滾失敗且交易仍開啟時關閉連線，隔離部分狀態。"""
    結果: list[BaseException] = []
    try:
        連線.execute("ROLLBACK")
    except _控制流 as 控制:
        _清除控制鏈(控制)
        控制 = 控制.with_traceback(None)
        結果.append(控制)
        del 控制
    except BaseException:
        try:
            if 連線.in_transaction:
                連線.close()
        except _控制流 as 控制:
            _清除控制鏈(控制)
            結果.append(控制.with_traceback(None))
            _標記狀態連線污染(連線)
            del 控制
        except BaseException:
            _標記狀態連線污染(連線)
    del 連線
    return 結果


def _拋出狀態清理控制(控制: BaseException) -> NoReturn:
    """以 fresh traceback 拋回 exact cleanup control。"""
    try:
        raise 控制.with_traceback(None)
    except _控制流:
        del 控制
        raise


@dataclass(frozen=True, slots=True)
class 能力摘要:
    """從 detached FND DTO 選出的 canonical immutable 能力子集。"""

    權限修訂: str
    技能: tuple[授權技能, ...]
    工具: tuple[授權工具, ...]
    正規JSON: str = field(init=False, repr=False)

    def __post_init__(self) -> None:
        """只接受已由協調器重建的 exact FND DTO 與 canonical 排序。"""
        資料 = 技能資料 = 工具資料 = 項目 = None
        失敗 = False
        try:
            if not _摘要有效(self):
                失敗 = True
            else:
                技能資料 = []
                for 項目 in self.技能:
                    技能資料.append({"name": 項目.名稱, "summary": 項目.摘要, "content_sha256_reference": 項目.內容sha256參照})
                    項目 = None
                工具資料 = []
                for 項目 in self.工具:
                    工具資料.append({"name": 項目.名稱, "revision": 項目.釘選修訂})
                    項目 = None
                資料 = {"permission_revision": self.權限修訂, "skills": 技能資料, "tools": 工具資料}
                object.__setattr__(self, "正規JSON", 建立正規JSON(資料))
        except _控制流:
            del self, 資料, 技能資料, 工具資料, 項目, 失敗
            raise
        except BaseException:
            失敗 = True
        if 失敗:
            del self, 資料, 技能資料, 工具資料, 項目, 失敗
            raise ValueError("能力摘要格式無效") from None


def _驗證有界JSON(原始值: Any) -> str:
    """在 FND parser 前以 quote/escape-aware 掃描限制 bytes、深度與節點。"""
    if type(原始值) is not str or len(原始值.encode("utf-8")) > 1024 * 1024:
        raise ValueError
    堆疊: list[str] = []
    索引 = 節點數 = 0
    期待值 = True
    while 索引 < len(原始值):
        字元 = 原始值[索引]
        if 字元.isspace():
            索引 += 1
            continue
        if 字元 == '"':
            是值 = 期待值
            索引 += 1
            while 索引 < len(原始值):
                if 原始值[索引] == "\\":
                    索引 += 2
                    continue
                if 原始值[索引] == '"':
                    索引 += 1
                    break
                索引 += 1
            if 是值:
                節點數 += 1
                期待值 = False
        elif 字元 in "[{":
            if 期待值:
                節點數 += 1
            堆疊.append(字元)
            if len(堆疊) > 64:
                raise ValueError
            期待值 = 字元 == "["
            索引 += 1
        elif 字元 in "]}":
            if 堆疊:
                堆疊.pop()
            期待值 = False
            索引 += 1
        elif 字元 == ":":
            期待值 = True
            索引 += 1
        elif 字元 == ",":
            期待值 = bool(堆疊 and 堆疊[-1] == "[")
            索引 += 1
        else:
            if 期待值:
                節點數 += 1
                期待值 = False
            索引 += 1
            while 索引 < len(原始值) and 原始值[索引] not in " \t\r\n,]}":
                索引 += 1
        if 節點數 > 10_000:
            raise ValueError
    return 原始值


def _解析名稱陣列(原始值: Any) -> tuple[str, ...]:
    """只接受 bounded immutable snapshot 的 exact JSON list[str]。"""
    值 = 項目 = None
    結果: list[str] = []
    try:
        值 = 解析嚴格JSON(_驗證有界JSON(原始值))
        if type(值) is not list:
            raise ValueError
        for 項目 in 值:
            if not _合法識別(項目) or 項目 in 結果:
                raise ValueError
            結果.append(項目)
            項目 = None
        return tuple(結果)
    except _控制流:
        if type(值) is list:
            值.clear()
        結果.clear()
        del 原始值, 值, 項目, 結果
        raise


def _解析權限陣列(原始值: Any) -> tuple[str, ...]:
    """解析 user_settings，另允許代表 unrestricted 的單一星號。"""
    值 = None
    try:
        值 = 解析嚴格JSON(_驗證有界JSON(原始值))
        if type(值) is not list:
            raise ValueError
        if 值 == ["*"] or not 值:
            return tuple(值)
        值.clear()
        值 = None
        return _解析名稱陣列(原始值)
    except _控制流:
        if type(值) is list:
            值.clear()
        del 原始值, 值
        raise


def _驗證技能manifest(原始值: Any, 技能: tuple[str, ...]) -> None:
    """驗證 P04 實際 manifest shape；不解析或讀取 live roots/filesystem。"""
    manifest = 項目 = 技能項目 = None
    名稱串列: list[Any] = []
    try:
        manifest = 解析嚴格JSON(_驗證有界JSON(原始值))
        if (type(manifest) is not dict
                or set(manifest.keys()) != {"permission_revision", "skills"}
                or not _合法識別(manifest.get("permission_revision"))):
            raise ValueError
        項目 = manifest.get("skills")
        if type(項目) is not list or len(項目) != len(技能):
            raise ValueError
        名稱串列: list[Any] = []
        for 技能項目 in 項目:
            if (type(技能項目) is not dict
                    or set(技能項目.keys()) != {"name", "content_sha256_reference"}
                    or type(技能項目.get("content_sha256_reference")) is not str
                    or _SHA256規則.fullmatch(技能項目["content_sha256_reference"]) is None):
                raise ValueError
            名稱串列.append(技能項目.get("name"))
        if tuple(名稱串列) != 技能:
            raise ValueError
    except _控制流:
        if type(項目) is list:
            項目.clear()
        if type(manifest) is dict:
            manifest.clear()
        名稱串列.clear()
        del 原始值, 技能, manifest, 項目, 技能項目, 名稱串列
        raise


def _發布介面尚未初始化(連線: sqlite3.Connection) -> bool:
    """僅在Published三個核心表全不存在時允許legacy-only資料庫no-op。"""
    名稱: set[Any] = set()
    for 資料列 in 連線.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name IN "
        "('published_api_schema_migrations','published_endpoints','published_endpoint_versions')"
    ):
        名稱.add(資料列[0])
    return not 名稱


def _從完整快照選擇(
    快照: 規劃權限快照,
    技能名稱: tuple[str, ...],
    工具名稱: tuple[str, ...],
) -> 能力摘要 | None:
    """只讀 detached FND 快照，依完整快照順序產生 deterministic 子集。"""
    技能集合 = set(技能名稱)
    工具集合 = set(工具名稱)
    技能串列: list[授權技能] = []
    工具串列: list[授權工具] = []
    項目 = None
    try:
        for 項目 in 快照.技能:
            if 項目.名稱 in 技能集合:
                技能串列.append(授權技能(項目.名稱, 項目.摘要, 項目.內容sha256參照))
            項目 = None
        for 項目 in 快照.工具:
            if 項目.名稱 in 工具集合:
                工具串列.append(授權工具(項目.名稱, 項目.釘選修訂))
            項目 = None
        技能 = tuple(技能串列)
        工具 = tuple(工具串列)
        if len(技能) != len(技能名稱) or len(工具) != len(工具名稱):
            return None
        return 能力摘要(快照.權限修訂, 技能, 工具)
    except _控制流:
        del 快照, 技能名稱, 工具名稱, 技能集合, 工具集合, 技能串列, 工具串列, 項目
        raise


def _摘要有效(摘要: 能力摘要) -> bool:
    """驗證摘要只含 exact、唯一且 deterministic 排序的 FND DTO。"""
    技能名稱: list[str] = []
    工具名稱: list[str] = []
    項目 = None
    try:
        if not _合法識別(摘要.權限修訂) or type(摘要.技能) is not tuple or type(摘要.工具) is not tuple:
            return False
        for 項目 in 摘要.技能:
            if type(項目) is not 授權技能:
                return False
            技能名稱.append(項目.名稱)
            項目 = None
        for 項目 in 摘要.工具:
            if type(項目) is not 授權工具:
                return False
            工具名稱.append(項目.名稱)
            項目 = None
        return bool(技能名稱) and _名稱唯一且排序(技能名稱) and _名稱唯一且排序(工具名稱)
    except _控制流:
        del 摘要, 技能名稱, 工具名稱, 項目
        raise
    except BaseException:
        return False


def _名稱唯一且排序(名稱串列: list[str]) -> bool:
    """不以 comprehension 建立敏感名稱集合。"""
    已見: set[str] = set()
    前項: str | None = None
    名稱 = None
    try:
        for 名稱 in 名稱串列:
            if 名稱 in 已見 or (前項 is not None and 名稱 < 前項):
                return False
            已見.add(名稱)
            前項 = 名稱
            名稱 = None
        return True
    except _控制流:
        del 名稱串列, 已見, 前項, 名稱
        raise


def _合法識別(值: Any) -> bool:
    """只接受與 FND 契約相同的 exact canonical identifier。"""
    return type(值) is str and _識別規則.fullmatch(值) is not None


def _合法選擇(值: Any, *, 必須非空: bool = False) -> bool:
    """在 FND helper 前拒絕非 exact tuple、非法名稱、重複或非排序選擇。"""
    if type(值) is not tuple or (必須非空 and not 值):
        return False
    名稱串列: list[str] = []
    項目 = None
    try:
        for 項目 in 值:
            if not _合法識別(項目):
                return False
            名稱串列.append(項目)
            項目 = None
        return _名稱唯一且排序(名稱串列)
    except _控制流:
        del 值, 必須非空, 名稱串列, 項目
        raise
    except BaseException:
        return False


def _拒絕() -> NoReturn:
    """以固定、不鏈結底層資料的錯誤 fail closed。"""
    raise 授權選擇錯誤(_固定錯誤) from None


