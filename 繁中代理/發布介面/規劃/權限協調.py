"""PUB Planner 與 FND 權威權限查詢之安全協調邊界。

參數／欄位：不適用；本模組定義權限摘要、協調器與資料庫協調操作。
回傳：不適用；各協調操作的回傳契約由其文件字串分別說明。
例外：匯入相依模組失敗時原樣傳出匯入例外。
副作用：匯入時只定義型別、常數與函式，不查詢權限或修改資料庫。
"""

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
from ..資料庫結構契約 import 遷移帳本 as _發布遷移紀錄
from ..連線隔離 import (
    標記發布連線污染 as _標記狀態連線污染,
    發布連線已污染 as _狀態連線已污染,
)


_識別規則 = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}\Z")
_SHA256規則 = re.compile(r"[0-9a-f]{64}\Z")
_固定錯誤 = "規劃能力未獲授權"
_控制流 = (KeyboardInterrupt, SystemExit, GeneratorExit)
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


class 權限協調器:
    """只經 FND Protocol 選取能力；未知、撤銷或畸形資料皆 fail closed。"""

    def __init__(self, 查詢器: Planner權限查詢) -> None:
        """保存唯一的 FND authoritative query，不建立替代來源。"""
        self._查詢器 = 查詢器

    def 建立能力摘要(self, 擁有者識別碼: str, 技能名稱: tuple[str, ...], 工具名稱: tuple[str, ...]) -> 能力摘要:
        """exact preflight 後查詢一次完整快照，再建立 detached selected subset。"""
        快照 = 結果 = None
        失敗 = False
        try:
            if not _合法識別(擁有者識別碼) or not _合法選擇(技能名稱, 必須非空=True) or not _合法選擇(工具名稱):
                失敗 = True
            else:
                快照 = 安全查詢規劃權限(self._查詢器, 擁有者識別碼)
                結果 = _從完整快照選擇(快照, 技能名稱, 工具名稱)
        except _控制流:
            del self, 擁有者識別碼, 技能名稱, 工具名稱, 快照, 結果, 失敗
            raise
        except BaseException:
            失敗 = True
        if 失敗 or 結果 is None:
            del self, 擁有者識別碼, 技能名稱, 工具名稱, 快照, 結果, 失敗
            _拒絕()
        del 擁有者識別碼, 技能名稱, 工具名稱, 快照, 失敗
        return 結果


class SQLite發布權限協調器:
    """只使用呼叫者的 connection/transaction 撤銷不再獲授權的 current pins。"""

    def 協調權限變更(
        self, 連線: sqlite3.Connection, 擁有者識別碼: str, 欄位: str,
        舊項目: tuple[str, ...], 新項目: tuple[str, ...], 更新時間: float,
    ) -> None:
        """在既有 BEGIN IMMEDIATE 內 CAS 停用受影響 active endpoints；不提交或關閉。"""
        列們 = 列 = 工具 = 技能 = 游標 = None
        失敗 = False
        try:
            if _狀態連線已污染(連線) or 連線.in_transaction is not True:
                raise sqlite3.DatabaseError
            if _發布介面尚未初始化(連線):
                return
            _驗證發布資料表(連線)
            if 欄位 not in {"enabled_tools_json", "enabled_skills_json", "skill_roots_json"}:
                return
            列們 = 連線.execute(
                "SELECT e.id,e.current_version_id,v.allowed_tools_json,v.allowed_skills_json,"
                "v.skill_bundle_manifest_json FROM published_endpoints e "
                "LEFT JOIN published_endpoint_versions v ON v.id=e.current_version_id AND v.endpoint_id=e.id "
                "WHERE e.owner_user_id=? AND e.status='active'",
                (擁有者識別碼,),
            ).fetchall()
            if type(列們) is not list:
                raise sqlite3.DatabaseError
            for 列 in 列們:
                if type(列) is sqlite3.Row:
                    列 = tuple(列)
                if type(列) is not tuple or len(列) != 5:
                    raise sqlite3.DatabaseError
                工具 = _解析名稱陣列(列[2])
                技能 = _解析名稱陣列(列[3])
                _驗證技能manifest(列[4], 技能)
                if _端點受影響(欄位, 舊項目, 新項目, 工具, 技能):
                    游標 = 連線.execute(
                        "UPDATE published_endpoints SET status='disabled',updated_at=? "
                        "WHERE id=? AND owner_user_id=? AND status='active' AND current_version_id=?",
                        (更新時間, 列[0], 擁有者識別碼, 列[1]),
                    )
                    if type(游標.rowcount) is not int or 游標.rowcount != 1:
                        raise sqlite3.DatabaseError
                列 = 工具 = 技能 = 游標 = None
        except _控制流 as 控制:
            _清除控制鏈(控制)
            if type(列們) is list:
                列們.clear()
            del self, 連線, 擁有者識別碼, 欄位, 舊項目, 新項目, 更新時間
            del 列們, 列, 工具, 技能, 游標, 失敗, 控制
            raise
        except BaseException:
            失敗 = True
        if 失敗:
            if type(列們) is list:
                列們.clear()
            del self, 連線, 擁有者識別碼, 欄位, 舊項目, 新項目, 更新時間
            del 列們, 列, 工具, 技能, 游標, 失敗
            raise 發布權限協調錯誤("發布權限協調失敗") from None

    def 重新確認端點(
        self, 連線: sqlite3.Connection, 擁有者識別碼: str,
        端點識別碼: str, 更新時間: float,
    ) -> None:
        """鎖後以 authoritative user_settings 重驗 current pins，僅 disabled 可回 active。"""
        try:
            _執行狀態交易(連線, 擁有者識別碼, 端點識別碼, 更新時間, 重新確認=True)
        except BaseException:
            del self, 連線, 擁有者識別碼, 端點識別碼, 更新時間
            raise

    def 封存端點(
        self, 連線: sqlite3.Connection, 擁有者識別碼: str,
        端點識別碼: str, 更新時間: float,
    ) -> None:
        """鎖後將 active/disabled 轉為 terminal archived。"""
        try:
            _執行狀態交易(連線, 擁有者識別碼, 端點識別碼, 更新時間, 重新確認=False)
        except BaseException:
            del self, 連線, 擁有者識別碼, 端點識別碼, 更新時間
            raise


def 鎖定確認端點可執行(
    連線: sqlite3.Connection, 端點識別碼: str, 版本識別碼: str,
) -> None:
    """供 INV write transaction 鎖後檢查 exact active current version。"""
    列 = None
    失敗 = False
    try:
        if (_狀態連線已污染(連線) or 連線.in_transaction is not True or not _合法識別(端點識別碼)
                or not _合法識別(版本識別碼)):
            raise ValueError
        列 = 連線.execute(
            "SELECT status,current_version_id FROM published_endpoints WHERE id=?",
            (端點識別碼,),
        ).fetchone()
        if type(列) is sqlite3.Row:
            列 = tuple(列)
        if type(列) is not tuple or len(列) != 2 or 列 != ("active", 版本識別碼):
            raise ValueError
    except _控制流 as 控制:
        _清除控制鏈(控制)
        del 連線, 端點識別碼, 版本識別碼, 列, 失敗, 控制
        raise
    except BaseException:
        失敗 = True
    if 失敗:
        del 連線, 端點識別碼, 版本識別碼, 列, 失敗
        raise 發布權限協調錯誤("端點目前不可執行") from None


def _端點受影響(
    欄位: str, 舊項目: tuple[str, ...], 新項目: tuple[str, ...],
    工具: tuple[str, ...], 技能: tuple[str, ...],
) -> bool:
    """空清單與 `*` 均為 unrestricted；roots 無 snapshot identity 時 narrowing fail closed。"""
    新限制 = None if not 新項目 or "*" in 新項目 else frozenset(新項目)
    if 新限制 is None:
        return False
    if 欄位 == "enabled_tools_json":
        return not frozenset(工具).issubset(新限制)
    if 欄位 == "enabled_skills_json":
        return not frozenset(技能).issubset(新限制)
    舊限制 = None if not 舊項目 or "*" in 舊項目 else frozenset(舊項目)
    return bool(技能) and (舊限制 is None or not 舊限制.issubset(新限制))


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
    """驗證 P04 的舊版或新版技能 manifest；不讀取即時來源目錄。

    參數：``原始值`` 是資料庫保存的正規 JSON 文字；``技能`` 是版本快照的
    exact 技能名稱 tuple。回傳：符合舊 exact 2-key 或新 exact 6-key 契約時回傳
    ``None``。例外：型別、技能順序、SHA-256、套件識別或清單參照不符時拋出
    ``ValueError``；三種控制流程例外維持原物件傳出。副作用：只配置有界 JSON
    暫存物件，不讀取檔案系統、不修改資料庫。
    """
    manifest = 項目 = 技能項目 = bundle_id = reference = digest = bundle_hash = None
    名稱串列: list[Any] = []
    try:
        if type(技能) is not tuple:
            raise ValueError
        for 名稱 in 技能:
            if not _合法識別(名稱):
                raise ValueError
        manifest = 解析嚴格JSON(_驗證有界JSON(原始值))
        if type(manifest) is not dict:
            raise ValueError
        鍵集合 = set(manifest.keys())
        if 鍵集合 not in (
            {"permission_revision", "skills"},
            {"permission_revision", "skills", "bundle_id", "manifest_reference", "manifest_digest", "sha256"},
        ) or not _合法識別(manifest.get("permission_revision")):
            raise ValueError
        if len(鍵集合) == 6:
            bundle_id = manifest.get("bundle_id")
            reference = manifest.get("manifest_reference")
            digest = manifest.get("manifest_digest")
            bundle_hash = manifest.get("sha256")
            if (
                not _合法識別(bundle_id)
                or type(reference) is not str
                or reference != f"{bundle_id}/manifest.json"
                or type(digest) is not str
                or _SHA256規則.fullmatch(digest) is None
                or type(bundle_hash) is not str
                or _SHA256規則.fullmatch(bundle_hash) is None
            ):
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
        del 原始值, 技能, manifest, 項目, 技能項目, bundle_id, reference, digest, bundle_hash, 名稱串列
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


def _驗證發布資料表(連線: sqlite3.Connection) -> None:
    """在既有 write lock 下驗完整 ledger 與 P07 端點／版本 schema 指紋。"""
    ledger串列: list[tuple[Any, ...]] = []
    for 資料列 in 連線.execute(
        "SELECT version,name FROM published_api_schema_migrations ORDER BY version"
    ):
        ledger串列.append(tuple(資料列))
    if tuple(ledger串列) != _發布遷移紀錄:
        raise sqlite3.DatabaseError
    schema列: list[tuple[Any, ...]] = []
    for 資料列 in 連線.execute(
        "SELECT type,name,tbl_name,sql FROM sqlite_master "
        "WHERE tbl_name IN ('published_endpoints','published_endpoint_versions') ORDER BY type,name"
    ):
        schema列.append(tuple(資料列))
    原文 = json.dumps(schema列, ensure_ascii=False, separators=(",", ":"))
    if hashlib.sha256(原文.encode("utf-8")).hexdigest() != _P07_SCHEMA指紋:
        raise sqlite3.DatabaseError


def _執行狀態交易(
    連線: sqlite3.Connection, 擁有者: str, 端點: str, 時間: float, *, 重新確認: bool,
) -> None:
    """由生命週期入口擁有交易；拒絕、錯誤與 cleanup control 皆明確排序。"""
    已開始 = 已提交 = 提交中 = 拒絕 = 失敗 = False
    列 = 工具 = 技能 = 工具權限 = 技能權限 = 根權限 = 游標 = None
    清理控制: list[BaseException] = []
    try:
        if (_狀態連線已污染(連線) or not _合法識別(擁有者) or not _合法識別(端點)
                or type(時間) not in (int, float) or not math.isfinite(時間) or 時間 < 0
                or type(重新確認) is not bool):
            raise ValueError
        連線.execute("BEGIN IMMEDIATE")
        已開始 = True
        _驗證發布資料表(連線)
        if 重新確認:
            列 = 連線.execute(
                "SELECT e.status,e.current_version_id,v.allowed_tools_json,v.allowed_skills_json,"
                "v.skill_bundle_manifest_json,s.enabled_tools_json,s.enabled_skills_json,s.skill_roots_json "
                "FROM published_endpoints e JOIN published_endpoint_versions v "
                "ON v.id=e.current_version_id AND v.endpoint_id=e.id "
                "JOIN user_settings s ON s.user_id=e.owner_user_id "
                "WHERE e.id=? AND e.owner_user_id=?",
                (端點, 擁有者),
            ).fetchone()
            if type(列) is sqlite3.Row:
                列 = tuple(列)
            if 列 is None:
                拒絕 = True
            elif type(列) is not tuple or len(列) != 8 or not _合法識別(列[1]):
                raise sqlite3.DatabaseError
            elif 列[0] != "disabled":
                拒絕 = True
            else:
                工具 = _解析名稱陣列(列[2])
                技能 = _解析名稱陣列(列[3])
                _驗證技能manifest(列[4], 技能)
                工具權限 = _解析權限陣列(列[5])
                技能權限 = _解析權限陣列(列[6])
                根權限 = _解析權限陣列(列[7])
                拒絕 = (
                    _端點受影響("enabled_tools_json", (), 工具權限, 工具, 技能)
                    or _端點受影響("enabled_skills_json", (), 技能權限, 工具, 技能)
                    or (bool(技能) and bool(根權限) and "*" not in 根權限)
                )
                if not 拒絕:
                    游標 = 連線.execute(
                        "UPDATE published_endpoints SET status='active',updated_at=? "
                        "WHERE id=? AND owner_user_id=? AND status='disabled' AND current_version_id=?",
                        (時間, 端點, 擁有者, 列[1]),
                    )
                    if type(游標.rowcount) is not int or 游標.rowcount != 1:
                        raise sqlite3.DatabaseError
        else:
            游標 = 連線.execute(
                "UPDATE published_endpoints SET status='archived',updated_at=? "
                "WHERE id=? AND owner_user_id=? AND status IN ('active','disabled')",
                (時間, 端點, 擁有者),
            )
            if type(游標.rowcount) is not int:
                raise sqlite3.DatabaseError
            拒絕 = 游標.rowcount != 1
        if not 拒絕:
            提交中 = True
            連線.execute("COMMIT")
            已開始 = False
            已提交 = True
            提交中 = False
    except _控制流 as 控制:
        _清除控制鏈(控制)
        if 已開始 and 連線.in_transaction:
            清理控制 = _回滾狀態交易(連線)
        清理控制.clear()
        _清除控制鏈(控制)
        del 連線, 擁有者, 端點, 時間, 重新確認, 已開始, 已提交, 提交中, 拒絕, 失敗
        del 列, 工具, 技能, 工具權限, 技能權限, 根權限, 游標, 清理控制, 控制
        raise
    except BaseException:
        if not 已開始:
            失敗 = True
        elif not 提交中 or 連線.in_transaction:
            失敗 = True
        else:
            已開始 = False
            已提交 = True
    if 已開始:
        清理控制 = _回滾狀態交易(連線)
        已開始 = False
    if 已提交:
        return
    遭拒 = 拒絕 and not 失敗
    del 連線, 擁有者, 端點, 時間, 重新確認, 已開始, 已提交, 提交中, 拒絕, 失敗
    del 列, 工具, 技能, 工具權限, 技能權限, 根權限, 游標
    if 清理控制:
        _拋出狀態清理控制(清理控制.pop())
    del 清理控制
    if 遭拒:
        del 遭拒
        raise 發布權限協調錯誤("端點狀態變更遭拒") from None
    del 遭拒
    raise 發布權限協調錯誤("端點狀態變更失敗") from None


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


__all__ = [
    "Planner權限查詢",
    "授權技能",
    "授權工具",
    "規劃權限快照",
    "安全查詢規劃權限",
    "規劃權限查詢錯誤",
    "能力摘要",
    "授權選擇錯誤",
    "權限協調器",
    "發布權限協調錯誤",
    "SQLite發布權限協調器",
    "鎖定確認端點可執行",
]
