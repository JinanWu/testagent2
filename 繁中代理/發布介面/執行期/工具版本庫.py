"""提供不可變、依修訂釘選的發布執行期工具版本庫。

參數／欄位：公開物件接收工具定義、明確修訂及工具快照，不接受目前版或最新版指標。
回傳／不適用：可建立脫離權威狀態的修訂副本、快照項目與版本釘選工具登錄器。
例外：資料外形、摘要、複製或版本庫操作失敗會收斂為 ``工具快照錯誤``；控制流程例外原樣傳出。
副作用：登錄與移除會在鎖內修改行程記憶體；使用過的工具名稱與修訂組合永久留在墓碑集合。
"""

from __future__ import annotations

import copy
from dataclasses import dataclass
import hashlib
import hmac
import json
import math
import re
import threading
import types
from typing import Any, Protocol

from ...工具 import 工具定義
from ..嚴格JSON import 建立正規JSON, 解析嚴格JSON
from .工具結果 import 工具設定錯誤, 工具逾時

_識別碼 = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}")
_雜湊 = re.compile(r"[0-9a-f]{64}")
_控制流程例外 = (KeyboardInterrupt, SystemExit, GeneratorExit)
_固定錯誤 = "發布工具快照不可用"


class 工具快照錯誤(RuntimeError):
    """工具版本或快照未通過 fail-closed 邊界時的固定錯誤。"""


@dataclass(frozen=True, slots=True)
class 工具快照項目:
    """端點版本保存的 exact 工具名稱、修訂與內容摘要。"""

    name: str
    revision: str
    digest: str

    def __post_init__(self) -> None:
        """拒絕 forged subclass、非正規識別碼與非小寫 SHA-256。"""
        if (
            type(self) is not 工具快照項目
            or not _是識別碼(self.name)
            or not _是識別碼(self.revision)
            or type(self.digest) is not str
            or _雜湊.fullmatch(self.digest) is None
        ):
            raise 工具快照錯誤(_固定錯誤) from None


@dataclass(frozen=True, slots=True)
class _工具修訂:
    """版本庫內部 module-owned 工具修訂。"""
    名稱: str
    修訂名稱: str
    摘要: str
    說明: str
    參數JSON: str
    處理函數: object


class 工具修訂提供者(Protocol):
    """只按 exact name/revision 取得工具修訂的提供者。"""

    def 取得工具修訂(self, 名稱: str, 修訂名稱: str) -> object:
        """取得指定修訂；找不到時必須回傳 None。"""


class 工具版本庫:
    """保存多個工具修訂；不提供 current/latest fallback。"""

    def __init__(self) -> None:
        """建立含永久 identity tombstone、但不含 current 指標的空修訂表。"""
        self._修訂: dict[tuple[str, str], _工具修訂] = {}
        self._已使用識別: set[tuple[str, str]] = set()
        self._鎖 = threading.Lock()

    def 登錄修訂(self, 修訂名稱: str, 工具: 工具定義) -> 工具快照項目:
        """首次登錄 identity；移除後仍永久禁止重用。"""
        修訂 = 鍵 = 項目 = None
        失敗 = 重複 = False
        try:
            修訂 = _建立修訂(修訂名稱, 工具)
            鍵 = (修訂.名稱, 修訂.修訂名稱)
            項目 = 工具快照項目(
                name=修訂.名稱, revision=修訂.修訂名稱, digest=修訂.摘要
            )
        except _控制流程例外:
            self = 修訂名稱 = 工具 = 修訂 = 鍵 = 項目 = None
            raise
        except BaseException:
            失敗 = True
        if not 失敗:
            try:
                with self._鎖:
                    if 鍵 in self._已使用識別:
                        重複 = True
                    else:
                        self._修訂[鍵] = 修訂
                        self._已使用識別.add(鍵)
            except _控制流程例外:
                self = 修訂名稱 = 工具 = 修訂 = 鍵 = 項目 = None
                raise
            except BaseException:
                失敗 = True
        if 失敗 or 重複:
            self = 修訂名稱 = 工具 = 修訂 = 鍵 = 項目 = None
            raise 工具快照錯誤(_固定錯誤) from None
        return 項目

    def 取得工具修訂(self, 名稱: str, 修訂名稱: str) -> object:
        """只回傳 exact 修訂的 detached 副本；絕不洩漏 authoritative state。"""
        if not _是識別碼(名稱) or not _是識別碼(修訂名稱):
            return None
        已存 = 欄位 = 副本 = None
        失敗 = False
        try:
            with self._鎖:
                已存 = self._修訂.get((名稱, 修訂名稱))
                if 已存 is None:
                    return None
                欄位 = (
                    object.__getattribute__(已存, "名稱"),
                    object.__getattribute__(已存, "修訂名稱"),
                    object.__getattribute__(已存, "摘要"),
                    object.__getattribute__(已存, "說明"),
                    object.__getattribute__(已存, "參數JSON"),
                    object.__getattribute__(已存, "處理函數"),
                )
        except _控制流程例外:
            self = 名稱 = 修訂名稱 = 已存 = 欄位 = None
            raise
        except BaseException:
            失敗 = True
        已存 = None
        if not 失敗:
            try:
                副本 = _重建已存修訂(欄位)
            except _控制流程例外:
                self = 名稱 = 修訂名稱 = 欄位 = 副本 = None
                raise
            except BaseException:
                失敗 = True
        if 失敗:
            self = 名稱 = 修訂名稱 = 已存 = 欄位 = 副本 = None
            raise 工具快照錯誤(_固定錯誤) from None
        return 副本

    def 移除修訂(self, 名稱: str, 修訂名稱: str) -> None:
        """只移除 live 修訂；永久 identity tombstone 不受影響。"""
        if _是識別碼(名稱) and _是識別碼(修訂名稱):
            with self._鎖:
                self._修訂.pop((名稱, 修訂名稱), None)


class 版本釘選工具登錄器:
    """只公開建立當下由端點版本快照解析出的 detached 工具。"""

    def __init__(self, 修訂清單: tuple[_工具修訂, ...]) -> None:
        """從已正規化修訂建立不可回退的名稱索引。"""
        self._工具 = {項目.名稱: 項目 for 項目 in 修訂清單}

    def 列出工具結構(self) -> list[dict[str, Any]]:
        """每次回傳新 schema tree，避免呼叫端修改釘選內容。"""
        結果 = []
        for 工具 in self._工具.values():
            結果.append({
                "type": "function",
                "function": {
                    "name": 工具.名稱,
                    "description": 工具.說明,
                    "parameters": 解析嚴格JSON(工具.參數JSON),
                },
            })
        return 結果

    def 呼叫工具(self, 名稱: str, 參數: dict[str, Any]) -> str:
        """以 exact 名稱呼叫釘選 handler；不做別名或 live owner fallback。"""
        if type(名稱) is not str or type(參數) is not dict:
            return _失敗結果()
        工具 = self._工具.get(名稱)
        if 工具 is None:
            return _失敗結果()
        try:
            參數副本 = _複製JSON物件(參數, 禁止頂層底線=True)
            結果 = 工具.處理函數(參數副本)  # type: ignore[operator]
            return json.dumps({"success": True, "result": 結果}, ensure_ascii=False)
        except _控制流程例外:
            工具 = 參數副本 = 結果 = None
            del self, 名稱, 參數
            raise
        except BaseException as 錯誤:
            類別 = type(錯誤)
            if 類別 is 工具逾時 or 類別 is 工具設定錯誤:
                工具 = 參數副本 = 結果 = 類別 = None
                del self, 名稱, 參數
                raise
            工具 = 參數副本 = 結果 = None
            del self, 名稱, 參數
            return _失敗結果()


def 建立版本釘選工具登錄器(
    提供者: 工具修訂提供者, 快照: tuple[工具快照項目, ...]
) -> 版本釘選工具登錄器:
    """依重建、重複檢查、提供者驗證三階段解析 exact revisions。"""
    if type(快照) is not tuple:
        提供者 = 快照 = None
        raise 工具快照錯誤(_固定錯誤) from None

    # Phase A：只讀取、驗證並重建所有 DTO；不得雜湊名稱或查詢提供者。
    已預檢: list[工具快照項目] = []
    失敗 = False
    不可信項目 = 名稱 = 修訂名稱 = 摘要 = 項目 = None
    try:
        for 不可信項目 in 快照:
            if type(不可信項目) is not 工具快照項目:
                失敗 = True
                break
            名稱 = object.__getattribute__(不可信項目, "name")
            修訂名稱 = object.__getattribute__(不可信項目, "revision")
            摘要 = object.__getattribute__(不可信項目, "digest")
            if not _是識別碼(名稱) or not _是識別碼(修訂名稱) or (
                type(摘要) is not str or _雜湊.fullmatch(摘要) is None
            ):
                失敗 = True
                break
            項目 = 工具快照項目(name=名稱, revision=修訂名稱, digest=摘要)
            已預檢.append(項目)
    except _控制流程例外:
        提供者 = 快照 = 不可信項目 = 名稱 = 修訂名稱 = 摘要 = 項目 = 已預檢 = None
        raise
    except BaseException:
        失敗 = True
    if 失敗:
        提供者 = 快照 = 不可信項目 = 名稱 = 修訂名稱 = 摘要 = 項目 = 已預檢 = None
        raise 工具快照錯誤(_固定錯誤) from None

    # Phase B：所有 DTO 已可信後，才以 exact str 做名稱重複檢查。
    try:
        失敗 = _有重複工具名稱(已預檢)
    except _控制流程例外:
        不可信項目 = 名稱 = 修訂名稱 = 摘要 = 項目 = 已預檢 = None
        del 提供者, 快照
        raise
    except BaseException:
        失敗 = True
    if 失敗:
        不可信項目 = 名稱 = 修訂名稱 = 摘要 = 項目 = 已預檢 = None
        del 提供者, 快照
        raise 工具快照錯誤(_固定錯誤) from None

    # Phase C：完整預檢及去重後，才允許提供者 lookup 與 digest 驗證。
    已解析: list[_工具修訂] = []
    不可信修訂 = 修訂 = None
    try:
        for 項目 in 已預檢:
            不可信修訂 = 提供者.取得工具修訂(項目.name, 項目.revision)
            修訂 = _正規化提供者修訂(不可信修訂)
            if (
                修訂 is None or 修訂.名稱 != 項目.name
                or 修訂.修訂名稱 != 項目.revision
                or not hmac.compare_digest(修訂.摘要, 項目.digest)
            ):
                失敗 = True
                break
            已解析.append(修訂)
    except _控制流程例外:
        提供者 = 快照 = 不可信項目 = 名稱 = 修訂名稱 = 摘要 = 項目 = None
        不可信修訂 = 修訂 = 已預檢 = 已解析 = None
        raise
    except BaseException:
        失敗 = True
    if 失敗:
        提供者 = 快照 = 不可信項目 = 名稱 = 修訂名稱 = 摘要 = 項目 = None
        不可信修訂 = 修訂 = 已預檢 = 已解析 = None
        raise 工具快照錯誤(_固定錯誤) from None
    return 版本釘選工具登錄器(tuple(已解析))


def _有重複工具名稱(項目們: list[工具快照項目]) -> bool:
    """Phase B 專用：只雜湊已重建 DTO 的 exact 名稱。"""
    已看: set[str] = set()
    項目 = 名稱 = None
    try:
        for 項目 in 項目們:
            名稱 = 項目.name
            if 名稱 in 已看:
                return True
            已看.add(名稱)
        return False
    except BaseException:
        項目們 = 已看 = 項目 = 名稱 = None
        raise


def _建立修訂(修訂名稱: str, 工具: 工具定義) -> _工具修訂:
    """驗證既有工具定義並封存處理函數綁定。

    參數／欄位：``修訂名稱`` 是明確修訂識別；``工具`` 必須是 exact ``工具定義``。
    回傳／不適用：回傳含正規參數結構、內容摘要及獨立處理函數綁定的內部修訂。
    例外：識別、欄位、JSON、摘要或複製不合法時拋出固定 ``工具快照錯誤``；控制流程例外原樣傳出。
    副作用：讀取輸入工具並配置脫離副本，不修改輸入工具或版本庫狀態。
    """
    名稱 = 說明 = 參數結構 = 原處理函數 = None
    if type(工具) is not 工具定義:
        修訂名稱 = 工具 = 名稱 = 說明 = 參數結構 = 原處理函數 = None
        raise 工具快照錯誤(_固定錯誤) from None
    捕捉失敗 = False
    try:
        名稱 = object.__getattribute__(工具, "名稱")
        說明 = object.__getattribute__(工具, "說明")
        參數結構 = object.__getattribute__(工具, "參數結構")
        原處理函數 = object.__getattribute__(工具, "處理函數")
    except _控制流程例外:
        修訂名稱 = 工具 = 名稱 = 說明 = 參數結構 = 原處理函數 = None
        raise
    except BaseException:
        捕捉失敗 = True
    工具 = None
    if 捕捉失敗 or not _是識別碼(修訂名稱) or not _是識別碼(名稱) or (
        type(說明) is not str or type(參數結構) is not dict or not callable(原處理函數)
    ):
        修訂名稱 = 工具 = 名稱 = 說明 = 參數結構 = 原處理函數 = None
        raise 工具快照錯誤(_固定錯誤) from None
    失敗 = False
    處理函數 = None
    try:
        參數JSON = 建立正規JSON(_複製JSON物件(參數結構))
        摘要 = 計算工具修訂摘要(
            name=名稱, revision=修訂名稱, description=說明,
            parameters=解析嚴格JSON(參數JSON),
        )
        處理函數 = _封存處理函數(原處理函數)
    except _控制流程例外:
        名稱 = 說明 = 參數結構 = 原處理函數 = 參數JSON = 摘要 = 處理函數 = None
        del 修訂名稱
        raise
    except BaseException:
        失敗 = True
    if 失敗:
        名稱 = 說明 = 參數結構 = 原處理函數 = 參數JSON = 摘要 = 處理函數 = None
        del 修訂名稱
        raise 工具快照錯誤(_固定錯誤) from None
    return _工具修訂(名稱, 修訂名稱, 摘要, 說明, 參數JSON, 處理函數)


def _重建已存修訂(欄位) -> _工具修訂:
    """在 lifecycle lock 外重建 revision 與 fresh handler binding。"""
    副本 = 處理函數 = None
    try:
        處理函數 = _封存處理函數(欄位[5])
        副本 = _工具修訂(*欄位[:5], 處理函數)
        return 副本
    except BaseException:
        欄位 = 副本 = 處理函數 = None
        raise


def _正規化提供者修訂(值: object) -> _工具修訂 | None:
    """通過 exact 型別防線後重建模組自有的脫離修訂。

    參數／欄位：``值`` 是不可信提供者所回傳的候選內部修訂。
    回傳／不適用：欄位、正規 JSON 與摘要皆相符時回傳新修訂，普通驗證失敗則回傳 ``None``。
    例外：鍵盤中斷、系統結束與產生器結束原樣傳出；其他讀取、驗證或複製例外收斂為 ``None``。
    副作用：會重新計算摘要並複製處理函數綁定，不修改候選值或權威版本庫。
    """
    if type(值) is not _工具修訂:
        return None
    try:
        欄位清單 = []
        for 名稱 in ("名稱", "修訂名稱", "摘要", "說明", "參數JSON", "處理函數"):
            欄位值 = object.__getattribute__(值, 名稱)
            欄位清單.append(欄位值)
        欄位 = tuple(欄位清單)
        if not _修訂欄位合法(欄位) or not hmac.compare_digest(
            欄位[2], 計算工具修訂摘要(
                name=欄位[0], revision=欄位[1], description=欄位[3],
                parameters=解析嚴格JSON(欄位[4]),
            )
        ):
            return None
        處理函數 = _封存處理函數(欄位[5])
        return _工具修訂(*欄位[:5], 處理函數)
    except _控制流程例外:
        值 = 名稱 = 欄位值 = 欄位清單 = 欄位 = 處理函數 = None
        raise
    except BaseException:
        值 = 名稱 = 欄位值 = 欄位清單 = 欄位 = 處理函數 = None
        return None


def _封存處理函數(處理函數: object) -> object:
    """建立 release-owned binding；函數 closure/globals 是共享 runtime state。"""
    封存 = 綁定 = 預設值 = 關鍵字預設 = 註解 = 屬性 = None
    try:
        if type(處理函數) is types.FunctionType:
            綁定 = (
                object.__getattribute__(處理函數, "__code__"),
                object.__getattribute__(處理函數, "__globals__"),
                object.__getattribute__(處理函數, "__name__"),
                object.__getattribute__(處理函數, "__defaults__"),
                object.__getattribute__(處理函數, "__closure__"),
                object.__getattribute__(處理函數, "__kwdefaults__"),
                object.__getattribute__(處理函數, "__annotations__"),
                object.__getattribute__(處理函數, "__dict__"),
                object.__getattribute__(處理函數, "__qualname__"),
                object.__getattribute__(處理函數, "__module__"),
                object.__getattribute__(處理函數, "__doc__"),
            )
            處理函數 = None
            # 先用 exact built-in copy 脫離函數可替換的頂層 dict binding；不觸發內容回呼。
            關鍵字預設 = None if 綁定[5] is None else dict.copy(綁定[5])
            註解 = dict.copy(綁定[6])
            屬性 = dict.copy(綁定[7])
            預設值 = copy.deepcopy(綁定[3])
            關鍵字預設 = copy.deepcopy(關鍵字預設)
            註解 = copy.deepcopy(註解)
            屬性 = copy.deepcopy(屬性)
            封存 = types.FunctionType(
                綁定[0], 綁定[1], 綁定[2], 預設值, 綁定[4],
            )
            封存.__kwdefaults__ = 關鍵字預設
            封存.__annotations__ = 註解
            封存.__dict__.update(屬性)
            封存.__qualname__ = 綁定[8]
            封存.__module__ = 綁定[9]
            封存.__doc__ = 綁定[10]
        else:
            封存 = copy.deepcopy(處理函數)
            if 封存 is 處理函數:
                raise TypeError("unsafe callable copy")
        if not callable(封存):
            raise TypeError("non-callable copy")
        return 封存
    except BaseException:
        處理函數 = 封存 = 綁定 = 預設值 = 關鍵字預設 = 註解 = 屬性 = None
        raise


def _修訂欄位合法(欄位: tuple[object, ...]) -> bool:
    """驗證提供者 DTO 的所有 exact 欄位。"""
    return (
        len(欄位) == 6 and _是識別碼(欄位[0]) and _是識別碼(欄位[1])
        and type(欄位[2]) is str and _雜湊.fullmatch(欄位[2]) is not None
        and type(欄位[3]) is str and type(欄位[4]) is str and callable(欄位[5])
    )


def 計算工具修訂摘要(
    *, name: str, revision: str, description: str, parameters: dict[str, Any]
) -> str:
    """計算依修訂定址之正規內容的 SHA-256 權威摘要。

    參數／欄位：``name``、``revision``、``description`` 與 ``parameters`` 分別是線路相容名稱、修訂、說明及待正規化參數結構。
    回傳／不適用：回傳正規內容 UTF-8 位元組的小寫 SHA-256 十六進位字串。
    例外：識別或 exact 型別不合法時拋出 ``工具快照錯誤``；JSON 複製與正規化錯誤原樣傳出。
    副作用：只建立參數結構脫離副本及暫存摘要資料，不修改呼叫端物件或版本庫。
    """
    參數 = 資料 = None
    try:
        if not _是識別碼(name) or not _是識別碼(revision) or type(description) is not str or type(parameters) is not dict:
            raise 工具快照錯誤(_固定錯誤)
        參數 = _複製JSON物件(parameters)
        資料 = {"name": name, "revision": revision, "description": description, "parameters": 參數}
        return hashlib.sha256(建立正規JSON(資料).encode("utf-8")).hexdigest()
    except BaseException:
        參數 = 資料 = None
        del name, revision, description, parameters
        raise


def _取得字典項目(字典: dict[str, Any]) -> Any:
    """提供可測試的 built-in dict.items 單次走訪邊界。"""
    return dict.items(字典)


def _複製JSON物件(值: dict[str, Any], *, 禁止頂層底線: bool = False) -> dict[str, Any]:
    """以單次 exact 遞迴走訪建立 detached JSON object。"""
    已看: set[int] = set()
    try:
        return _複製JSON值(值, 已看, 禁止頂層底線)
    except BaseException:
        值 = 已看 = None
        del 禁止頂層底線
        raise


def _複製JSON值(目前: Any, 已看: set[int], 禁止底線鍵: bool = False) -> Any:
    """單次走訪一個 JSON value，拒絕循環、非有限數與型別子類。"""
    複本 = 項目 = 鍵 = 子值 = None
    目前識別 = id(目前)
    try:
        if 目前 is None or type(目前) in (str, bool, int):
            return 目前
        if type(目前) is float:
            if not math.isfinite(目前):
                raise 工具快照錯誤(_固定錯誤)
            return 目前
        if type(目前) is list:
            if 目前識別 in 已看:
                raise 工具快照錯誤(_固定錯誤)
            已看.add(目前識別)
            複本 = []
            for 項目 in list.__iter__(目前):
                複本.append(_複製JSON值(項目, 已看))
            已看.remove(目前識別)
            return 複本
        if type(目前) is dict:
            if 目前識別 in 已看:
                raise 工具快照錯誤(_固定錯誤)
            已看.add(目前識別)
            複本 = {}
            for 鍵, 子值 in _取得字典項目(目前):
                if type(鍵) is not str or (禁止底線鍵 and 鍵.startswith("_")):
                    raise 工具快照錯誤(_固定錯誤)
                複本[鍵] = _複製JSON值(子值, 已看)
            已看.remove(目前識別)
            return 複本
        raise 工具快照錯誤(_固定錯誤)
    except BaseException:
        目前 = 已看 = 複本 = 項目 = 鍵 = 子值 = None
        del 禁止底線鍵, 目前識別
        raise


def _是識別碼(值: object) -> bool:
    """只接受固定長度與字元集的 exact str。"""
    return type(值) is str and _識別碼.fullmatch(值) is not None


def _失敗結果() -> str:
    """建立不洩漏工具名稱或例外內容的固定失敗結果。"""
    return json.dumps({"success": False, "error": "發布工具不可用"}, ensure_ascii=False)
