"""Planner 綱要草稿 aggregate 與 owner-scoped 服務。"""

from __future__ import annotations

import math
import re
import threading
import uuid
from dataclasses import dataclass, field, replace
from typing import Any, Callable, NoReturn

from jsonschema import Draft202012Validator

from ..嚴格JSON import 嚴格JSON錯誤, 建立正規JSON, 解析嚴格JSON
from .權限協調 import 授權工具, 授權技能, 授權選擇錯誤, 權限協調器, 能力摘要 as 釘選能力摘要

_草稿不可用訊息 = "規劃草稿不可用"
_草稿不可執行訊息 = "規劃草稿不可執行"
_草稿輸入錯誤訊息 = "規劃草稿輸入無效"
_發布值輸入錯誤訊息 = "發布值確認輸入無效"

固定限流窗口秒數 = 60
建議端點每窗請求上限 = 60
建議憑證每窗請求上限 = 30
_文件UTF8上限 = 16 * 1024
_slug格式 = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*\Z")


class 草稿存取錯誤(PermissionError):
    """代表草稿不存在、不屬於呼叫者或已經到期。"""


class 草稿不可執行錯誤(RuntimeError):
    """代表呼叫者嘗試執行尚未發布的規劃草稿。"""


class 規劃已過時錯誤(RuntimeError):
    """代表草稿釘選的能力已撤銷或發生權限漂移。"""


@dataclass(frozen=True, slots=True)
class 發布值確認:
    """由擁有者確認並精確綁定某個草稿 identity/generation 的發布值。"""

    草稿識別碼: str
    草稿世代: int
    slug: str
    _回應結構正規JSON: str = field(repr=False)
    docs: str
    endpoint_limit: int
    credential_limit: int
    window_seconds: int = field(default=固定限流窗口秒數, init=False)

    def __post_init__(self) -> None:
        """防禦性驗證所有 fixed slots；schema 僅驗 canonical object，不重跑 meta validator。"""
        草稿識別碼 = 草稿世代 = slug = 正規JSON = docs = endpoint_limit = credential_limit = window_seconds = 結構 = None
        失敗 = False
        try:
            草稿識別碼 = object.__getattribute__(self, "草稿識別碼")
            草稿世代 = object.__getattribute__(self, "草稿世代")
            slug = object.__getattribute__(self, "slug")
            正規JSON = object.__getattribute__(self, "_回應結構正規JSON")
            docs = object.__getattribute__(self, "docs")
            endpoint_limit = object.__getattribute__(self, "endpoint_limit")
            credential_limit = object.__getattribute__(self, "credential_limit")
            window_seconds = object.__getattribute__(self, "window_seconds")
            if type(self) is not 發布值確認 or not _是非空字串(草稿識別碼):
                失敗 = True
            elif type(草稿世代) is not int or 草稿世代 < 0 or type(window_seconds) is not int or window_seconds != 固定限流窗口秒數:
                失敗 = True
            elif not _是有效發布純量(slug, docs, endpoint_limit, credential_limit) or type(正規JSON) is not str:
                失敗 = True
            else:
                結構 = 解析嚴格JSON(正規JSON)
                if type(結構) is not dict or 建立正規JSON(結構) != 正規JSON:
                    失敗 = True
        except (KeyboardInterrupt, SystemExit, GeneratorExit):
            del self, 草稿識別碼, 草稿世代, slug, 正規JSON, docs, endpoint_limit, credential_limit, window_seconds, 結構, 失敗
            raise
        except BaseException:
            失敗 = True
        if 失敗:
            del self, 草稿識別碼, 草稿世代, slug, 正規JSON, docs, endpoint_limit, credential_limit, window_seconds, 結構, 失敗
            _拒絕發布值輸入()

    @property
    def response_schema(self) -> dict[str, Any]:
        """只回傳 response schema 的 detached ordinary JSON object。"""
        return 解析嚴格JSON(self._回應結構正規JSON)


@dataclass(frozen=True, slots=True)
class 規劃草稿:
    """獨立於發布端點與不可變版本的 Planner 草稿快照。"""

    草稿識別碼: str
    擁有者識別碼: str
    原始需求: str = field(repr=False)
    _綱要正規JSON: str = field(repr=False)
    建立時間: float
    到期時間: float
    能力摘要: 釘選能力摘要 | None = None
    發布確認: 發布值確認 | None = None
    狀態: str = field(default="draft", init=False)
    _世代: int = field(default=0, init=False, repr=False, compare=False)

    @property
    def 綱要(self) -> Any:
        """回傳草稿綱要的獨立 JSON 副本，避免外部修改 aggregate。"""
        return 解析嚴格JSON(self._綱要正規JSON)
def _建立綱要快照(綱要: Any) -> str | None:
    """先遞迴建立可信副本，再 canonicalize；失敗不保留呼叫端物件。"""
    可信副本: Any = None
    正規JSON: str | None = None
    try:
        可信副本 = _複製精確JSON值(綱要)
        if 可信副本 is not _JSON複製失敗:
            正規JSON = 建立正規JSON(可信副本)
    except (嚴格JSON錯誤, RecursionError, RuntimeError):
        pass
    綱要 = 可信副本 = None
    return 正規JSON


def _建立回應結構快照(response_schema: Any) -> str | None:
    """單次走訪建立可信 schema，再執行 Draft 2020-12 meta-validation。"""
    可信結構: Any = None
    正規JSON: str | None = None
    try:
        可信結構 = _複製精確JSON值(response_schema)
        if type(可信結構) is not dict:
            return None
        Draft202012Validator.check_schema(可信結構)
        正規JSON = 建立正規JSON(可信結構)
    except (KeyboardInterrupt, SystemExit, GeneratorExit):
        response_schema = 可信結構 = 正規JSON = None
        raise
    except BaseException:
        正規JSON = None
    response_schema = 可信結構 = None
    return 正規JSON


def _重建公開確認(來源: Any) -> 發布值確認 | None:
    """以 explicit locals 重建確認，控制流 traceback 不保留任何來源欄位。"""
    草稿識別碼 = 草稿世代 = slug = 綱要JSON = docs = endpoint_limit = credential_limit = 結果 = None
    失敗 = type(來源) is not 發布值確認
    try:
        if not 失敗:
            草稿識別碼 = object.__getattribute__(來源, "草稿識別碼")
            草稿世代 = object.__getattribute__(來源, "草稿世代")
            slug = object.__getattribute__(來源, "slug")
            綱要JSON = object.__getattribute__(來源, "_回應結構正規JSON")
            docs = object.__getattribute__(來源, "docs")
            endpoint_limit = object.__getattribute__(來源, "endpoint_limit")
            credential_limit = object.__getattribute__(來源, "credential_limit")
            結果 = 發布值確認(草稿識別碼, 草稿世代, slug, 綱要JSON, docs, endpoint_limit, credential_limit)
    except (KeyboardInterrupt, SystemExit, GeneratorExit):
        del 來源, 草稿識別碼, 草稿世代, slug, 綱要JSON, docs, endpoint_limit, credential_limit, 結果, 失敗
        raise
    except BaseException:
        失敗 = True
    if 失敗:
        來源 = 草稿識別碼 = 草稿世代 = slug = 綱要JSON = docs = endpoint_limit = credential_limit = 結果 = None
        return None
    del 來源, 草稿識別碼, 草稿世代, slug, 綱要JSON, docs, endpoint_limit, credential_limit, 失敗
    return 結果


def _重建能力摘要(來源: Any) -> 釘選能力摘要 | None:
    """以 explicit loops 重建 FND children，避免 nested frame 與來源殘留。"""
    技能清單: list[授權技能] = []
    工具清單: list[授權工具] = []
    原技能 = 原工具 = 項目 = 新項目 = 權限修訂 = 原正規JSON = 結果 = None
    失敗 = type(來源) is not 釘選能力摘要
    try:
        if not 失敗:
            原技能 = object.__getattribute__(來源, "技能")
            原工具 = object.__getattribute__(來源, "工具")
            失敗 = type(原技能) is not tuple or type(原工具) is not tuple
        if not 失敗:
            for 項目 in 原技能:
                if type(項目) is not 授權技能:
                    失敗 = True
                    break
                新項目 = 授權技能(object.__getattribute__(項目, "名稱"), object.__getattribute__(項目, "摘要"), object.__getattribute__(項目, "內容sha256參照"))
                技能清單.append(新項目)
                項目 = 新項目 = None
        if not 失敗:
            for 項目 in 原工具:
                if type(項目) is not 授權工具:
                    失敗 = True
                    break
                新項目 = 授權工具(object.__getattribute__(項目, "名稱"), object.__getattribute__(項目, "釘選修訂"))
                工具清單.append(新項目)
                項目 = 新項目 = None
        if not 失敗:
            權限修訂 = object.__getattribute__(來源, "權限修訂")
            原正規JSON = object.__getattribute__(來源, "正規JSON")
            結果 = 釘選能力摘要(權限修訂, tuple(技能清單), tuple(工具清單))
            失敗 = 原正規JSON != 結果.正規JSON
    except (KeyboardInterrupt, SystemExit, GeneratorExit):
        技能清單.clear()
        工具清單.clear()
        del 來源, 技能清單, 工具清單, 原技能, 原工具, 項目, 新項目, 權限修訂, 原正規JSON, 結果, 失敗
        raise
    except BaseException:
        失敗 = True
    技能清單.clear()
    工具清單.clear()
    if 失敗:
        來源 = 原技能 = 原工具 = 項目 = 新項目 = 權限修訂 = 原正規JSON = 結果 = None
        return None
    del 來源, 技能清單, 工具清單, 原技能, 原工具, 項目, 新項目, 權限修訂, 原正規JSON, 失敗
    return 結果


def _重建公開草稿(來源: Any) -> 規劃草稿 | None:
    """完整鎖外重建 detached 草稿，並重新執行確認 schema meta-validation。"""
    草稿識別碼 = 擁有者識別碼 = 原始需求 = 綱要JSON = 綱要值 = 狀態 = None
    建立時間 = 到期時間 = 世代 = 原摘要 = 摘要 = 原確認 = 確認 = 結構 = 結果 = None
    失敗 = type(來源) is not 規劃草稿
    try:
        if not 失敗:
            草稿識別碼 = object.__getattribute__(來源, "草稿識別碼")
            擁有者識別碼 = object.__getattribute__(來源, "擁有者識別碼")
            原始需求 = object.__getattribute__(來源, "原始需求")
            綱要JSON = object.__getattribute__(來源, "_綱要正規JSON")
            建立時間 = object.__getattribute__(來源, "建立時間")
            到期時間 = object.__getattribute__(來源, "到期時間")
            世代 = object.__getattribute__(來源, "_世代")
            狀態 = object.__getattribute__(來源, "狀態")
            失敗 = not _是非空字串(草稿識別碼) or not _是非空字串(擁有者識別碼) or not _是非空字串(原始需求)
        if not 失敗:
            綱要值 = 解析嚴格JSON(綱要JSON) if type(綱要JSON) is str else None
            失敗 = type(綱要JSON) is not str or 建立正規JSON(綱要值) != 綱要JSON
        if not 失敗:
            失敗 = not _是有效時間(建立時間) or not _是有效時間(到期時間) or 到期時間 <= 建立時間
        if not 失敗:
            失敗 = 狀態 != "draft" or type(世代) is not int or 世代 < 0
        if not 失敗:
            原摘要 = object.__getattribute__(來源, "能力摘要")
            摘要 = None if 原摘要 is None else _重建能力摘要(原摘要)
            失敗 = 原摘要 is not None and 摘要 is None
        if not 失敗:
            原確認 = object.__getattribute__(來源, "發布確認")
            確認 = None if 原確認 is None else _重建公開確認(原確認)
            失敗 = 原確認 is not None and (確認 is None or 確認.草稿識別碼 != 草稿識別碼 or 確認.草稿世代 != 世代)
        if not 失敗 and 確認 is not None:
            結構 = 確認.response_schema
            Draft202012Validator.check_schema(結構)
        if not 失敗:
            結果 = 規劃草稿(草稿識別碼, 擁有者識別碼, 原始需求, 綱要JSON, 建立時間, 到期時間, 摘要, 確認)
            object.__setattr__(結果, "_世代", 世代)
    except (KeyboardInterrupt, SystemExit, GeneratorExit):
        del 來源, 草稿識別碼, 擁有者識別碼, 原始需求, 綱要JSON, 綱要值, 狀態, 建立時間, 到期時間, 世代, 原摘要, 摘要, 原確認, 確認, 結構, 結果, 失敗
        raise
    except BaseException:
        失敗 = True
    if 失敗:
        來源 = 草稿識別碼 = 擁有者識別碼 = 原始需求 = 綱要JSON = 綱要值 = 狀態 = None
        建立時間 = 到期時間 = 世代 = 原摘要 = 摘要 = 原確認 = 確認 = 結構 = 結果 = None
        return None
    del 來源, 草稿識別碼, 擁有者識別碼, 原始需求, 綱要JSON, 綱要值, 狀態, 建立時間, 到期時間, 世代, 原摘要, 摘要, 原確認, 確認, 結構, 失敗
    return 結果


def _必須重建公開草稿(來源: 規劃草稿) -> 規劃草稿:
    """以固定草稿錯誤拒絕畸形 authoritative aggregate。"""
    結果 = None
    try:
        結果 = _重建公開草稿(來源)
    except BaseException:
        del 來源, 結果
        raise
    if 結果 is None:
        del 來源, 結果
        _拒絕草稿存取()
    del 來源
    return 結果


def _必須重建公開確認(來源: 發布值確認) -> 發布值確認:
    """只回傳與留存確認無共享 DTO identity 的新確認。"""
    結果 = None
    try:
        結果 = _重建公開確認(來源)
    except BaseException:
        del 來源, 結果
        raise
    if 結果 is None:
        del 來源, 結果
        _拒絕草稿存取()
    del 來源
    return 結果


def _是有效發布純量(slug: Any, docs: Any, endpoint_limit: Any, credential_limit: Any) -> bool:
    """在鎖定、schema 走訪與 meta-validation 前驗證所有 scalar bounds。"""
    if type(slug) is not str or not 1 <= len(slug) <= 63 or _slug格式.fullmatch(slug) is None:
        return False
    if type(docs) is not str or not docs.strip():
        return False
    try:
        if len(docs.encode("utf-8")) > _文件UTF8上限:
            return False
    except UnicodeError:
        return False
    return (
        type(endpoint_limit) is int
        and 1 <= endpoint_limit <= 10_000
        and type(credential_limit) is int
        and 1 <= credential_limit <= 10_000
    )


def _是非空字串(值: Any) -> bool:
    """判斷值是否為非空白字串。"""
    return type(值) is str and bool(值.strip())


def _是有效時間(值: Any) -> bool:
    """判斷值是否為非負、有限且非 bool 的時間數值。"""
    if type(值) not in (int, float):
        return False
    try:
        return math.isfinite(值) and 值 >= 0
    except OverflowError:
        return False


def _是有效草稿查詢(擁有者識別碼: Any, 草稿識別碼: Any, 現在: Any) -> bool:
    """在任何鎖定或快照工作前驗證公開草稿查詢純量。"""
    return _是非空字串(擁有者識別碼) and _是非空字串(草稿識別碼) and _是有效時間(現在)


_JSON複製失敗 = object()


def _複製精確JSON值(值: Any, 路徑: set[int] | None = None) -> Any:
    """只讀 exact built-ins 並一次建立 module-owned JSON tree。"""
    型別 = type(值)
    if 值 is None or 型別 in (str, bool, int):
        return 值
    if 型別 is float:
        if math.isfinite(值):
            return 值
        return _JSON複製失敗
    if 型別 not in (list, dict):
        return _JSON複製失敗
    if 路徑 is None:
        路徑 = set()
    容器識別 = id(值)
    if 容器識別 in 路徑:
        return _JSON複製失敗
    路徑.add(容器識別)
    try:
        if 型別 is list:
            副本 = []
            for 項目 in list.__iter__(值):
                已複製 = _複製精確JSON值(項目, 路徑)
                if 已複製 is _JSON複製失敗:
                    return _JSON複製失敗
                副本.append(已複製)
        else:
            副本 = {}
            for 鍵, 項目 in dict.items(值):
                if type(鍵) is not str:
                    return _JSON複製失敗
                已複製 = _複製精確JSON值(項目, 路徑)
                if 已複製 is _JSON複製失敗:
                    return _JSON複製失敗
                副本[鍵] = 已複製
        return 副本
    finally:
        路徑.remove(容器識別)


def _拒絕草稿存取() -> NoReturn:
    """以固定且不含識別資訊的 domain error 拒絕草稿操作。"""
    raise 草稿存取錯誤(_草稿不可用訊息) from None


def _拒絕草稿輸入() -> NoReturn:
    """以固定且不鏈結底層錯誤的訊息拒絕草稿建立。"""
    raise ValueError(_草稿輸入錯誤訊息) from None


def _拒絕發布值輸入() -> NoReturn:
    """以固定且不鏈結底層 schema 錯誤的訊息拒絕值確認。"""
    raise ValueError(_發布值輸入錯誤訊息) from None
