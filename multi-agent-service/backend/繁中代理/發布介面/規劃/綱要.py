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


class 規劃服務:
    """在記憶體中管理 owner-bound、具固定期限且不可執行的草稿。"""

    def __init__(
        self,
        *,
        存續秒數: float = 86400,
        識別碼產生器: Callable[[], str] | None = None,
    ) -> None:
        """建立服務；期限必須為有限正數，識別碼工廠可供測試決定性注入。"""
        if not _是有效時間(存續秒數) or 存續秒數 <= 0:
            del 存續秒數, 識別碼產生器
            _拒絕草稿輸入()
        self._存續秒數 = float(存續秒數)
        self._識別碼產生器 = 識別碼產生器 or (lambda: uuid.uuid4().hex)
        self._草稿: dict[str, 規劃草稿] = {}
        self._鎖 = threading.Lock()

    def 建立草稿(
        self,
        擁有者識別碼: str,
        原始需求: str,
        綱要: Any,
        *,
        現在: float,
    ) -> 規劃草稿:
        """建立 owner-bound 草稿；維持 P01 的純草稿公開契約。"""
        if not _是非空字串(擁有者識別碼) or not _是非空字串(原始需求) or not _是有效時間(現在):
            del 擁有者識別碼, 原始需求, 綱要, 現在
            _拒絕草稿輸入()
        綱要快照 = _建立綱要快照(綱要)
        if 綱要快照 is None:
            del 擁有者識別碼, 原始需求, 綱要, 綱要快照
            _拒絕草稿輸入()
        工廠失敗 = False
        草稿識別碼: Any = None
        try:
            草稿識別碼 = self._識別碼產生器()
        except (KeyboardInterrupt, SystemExit, GeneratorExit):
            del 擁有者識別碼, 原始需求, 綱要, 綱要快照, 草稿識別碼
            raise
        except BaseException:
            工廠失敗 = True
        if 工廠失敗 or not _是非空字串(草稿識別碼):
            del 擁有者識別碼, 原始需求, 綱要, 綱要快照, 草稿識別碼
            _拒絕草稿輸入()
        建立時間 = float(現在)
        到期時間 = 建立時間 + self._存續秒數
        if not math.isfinite(到期時間):
            del 擁有者識別碼, 原始需求, 綱要, 綱要快照, 草稿識別碼
            _拒絕草稿輸入()
        草稿 = 規劃草稿(草稿識別碼, 擁有者識別碼, 原始需求, 綱要快照, 建立時間, 到期時間)
        重複 = False
        with self._鎖:
            if 草稿識別碼 in self._草稿:
                重複 = True
            else:
                self._草稿[草稿識別碼] = 草稿
        if 重複:
            del 擁有者識別碼, 原始需求, 綱要, 綱要快照, 草稿識別碼, 草稿
            _拒絕草稿輸入()
        回傳 = None
        try:
            回傳 = _必須重建公開草稿(草稿)
        except BaseException:
            with self._鎖:
                if self._草稿.get(草稿識別碼) is 草稿:
                    self._草稿.pop(草稿識別碼, None)
            del self, 擁有者識別碼, 原始需求, 綱要, 綱要快照, 草稿識別碼, 草稿, 回傳
            raise
        del 擁有者識別碼, 原始需求, 綱要, 綱要快照, 草稿識別碼, 草稿
        return 回傳

    def 建立授權草稿(
        self,
        協調器: 權限協調器,
        擁有者識別碼: str,
        原始需求: str,
        綱要: Any,
        技能名稱: tuple[str, ...],
        工具名稱: tuple[str, ...],
        *,
        現在: float,
    ) -> 規劃草稿:
        """先建立可信綱要，再經 exact 協調器查詢權威摘要並原子插入。"""
        if type(協調器) is not 權限協調器 or not _是非空字串(擁有者識別碼) or not _是非空字串(原始需求) or not _是有效時間(現在):
            del self, 協調器, 擁有者識別碼, 原始需求, 綱要, 技能名稱, 工具名稱, 現在
            _拒絕草稿輸入()
        綱要快照 = _建立綱要快照(綱要)
        if 綱要快照 is None:
            del self, 協調器, 擁有者識別碼, 原始需求, 綱要, 技能名稱, 工具名稱, 現在, 綱要快照
            _拒絕草稿輸入()
        能力摘要: 釘選能力摘要 | None = None
        try:
            能力摘要 = 協調器.建立能力摘要(擁有者識別碼, 技能名稱, 工具名稱)
        except (KeyboardInterrupt, SystemExit, GeneratorExit):
            del self, 協調器, 擁有者識別碼, 原始需求, 綱要, 技能名稱, 工具名稱, 現在, 綱要快照, 能力摘要
            raise
        except 授權選擇錯誤:
            del self, 協調器, 擁有者識別碼, 原始需求, 綱要, 技能名稱, 工具名稱, 現在, 綱要快照, 能力摘要
            raise
        except BaseException:
            能力摘要 = None
            協調失敗 = True
        else:
            協調失敗 = False
        if 協調失敗 or type(能力摘要) is not 釘選能力摘要:
            del self, 協調器, 擁有者識別碼, 原始需求, 綱要, 技能名稱, 工具名稱, 現在, 綱要快照, 能力摘要, 協調失敗
            raise 授權選擇錯誤("規劃能力未獲授權") from None
        工廠失敗 = False
        草稿識別碼: Any = None
        try:
            草稿識別碼 = self._識別碼產生器()
        except (KeyboardInterrupt, SystemExit, GeneratorExit):
            del self, 協調器, 擁有者識別碼, 原始需求, 綱要, 技能名稱, 工具名稱, 現在, 綱要快照, 能力摘要, 草稿識別碼
            raise
        except BaseException:
            工廠失敗 = True
        if 工廠失敗 or not _是非空字串(草稿識別碼):
            del self, 協調器, 擁有者識別碼, 原始需求, 綱要, 技能名稱, 工具名稱, 現在, 綱要快照, 能力摘要, 草稿識別碼
            _拒絕草稿輸入()
        建立時間 = float(現在)
        到期時間 = 建立時間 + self._存續秒數
        if not math.isfinite(到期時間):
            del self, 協調器, 擁有者識別碼, 原始需求, 綱要, 技能名稱, 工具名稱, 現在, 綱要快照, 能力摘要, 草稿識別碼
            _拒絕草稿輸入()
        草稿 = 規劃草稿(草稿識別碼, 擁有者識別碼, 原始需求, 綱要快照, 建立時間, 到期時間, 能力摘要)
        重複 = False
        with self._鎖:
            if 草稿識別碼 in self._草稿:
                重複 = True
            else:
                self._草稿[草稿識別碼] = 草稿
        if 重複:
            del self, 協調器, 擁有者識別碼, 原始需求, 綱要, 技能名稱, 工具名稱, 現在, 綱要快照, 能力摘要, 草稿識別碼, 草稿
            _拒絕草稿輸入()
        回傳 = None
        try:
            回傳 = _必須重建公開草稿(草稿)
        except BaseException:
            with self._鎖:
                if self._草稿.get(草稿識別碼) is 草稿:
                    self._草稿.pop(草稿識別碼, None)
            del self, 協調器, 擁有者識別碼, 原始需求, 綱要, 技能名稱, 工具名稱, 現在, 綱要快照, 能力摘要, 草稿識別碼, 草稿, 回傳
            raise
        del self, 協調器, 擁有者識別碼, 原始需求, 綱要, 技能名稱, 工具名稱, 現在, 綱要快照, 能力摘要, 草稿識別碼, 草稿
        return 回傳

    def 讀取草稿(self, 擁有者識別碼: str, 草稿識別碼: str, *, 現在: float) -> 規劃草稿:
        """依 owner 讀取未到期草稿；缺少、跨 owner、過期或非法查詢皆丟固定錯誤。"""
        if not _是有效草稿查詢(擁有者識別碼, 草稿識別碼, 現在):
            del 擁有者識別碼, 草稿識別碼, 現在
            _拒絕草稿存取()
        來源 = 回傳 = None
        try:
            來源 = self._取得有效草稿(擁有者識別碼, 草稿識別碼, 現在)
            回傳 = _必須重建公開草稿(來源)
        except BaseException:
            del self, 擁有者識別碼, 草稿識別碼, 現在, 來源, 回傳
            raise
        del self, 擁有者識別碼, 草稿識別碼, 現在, 來源
        return 回傳

    def 確認發布值(
        self,
        擁有者識別碼: str,
        草稿識別碼: str,
        *,
        slug: str,
        response_schema: Any,
        docs: str,
        endpoint_limit: int,
        credential_limit: int,
        現在: float,
    ) -> 發布值確認:
        """在鎖外驗證值，再以草稿 identity/generation CAS 原子取代確認。"""
        if not _是有效草稿查詢(擁有者識別碼, 草稿識別碼, 現在):
            del self, 擁有者識別碼, 草稿識別碼, slug, response_schema, docs, endpoint_limit, credential_limit, 現在
            _拒絕草稿存取()
        if not _是有效發布純量(slug, docs, endpoint_limit, credential_limit):
            del self, 擁有者識別碼, 草稿識別碼, slug, response_schema, docs, endpoint_limit, credential_limit, 現在
            _拒絕發布值輸入()
        目標 = 目標世代 = None
        存取失敗 = False
        with self._鎖:
            try:
                目標 = self._取得有效草稿_已鎖定(擁有者識別碼, 草稿識別碼, 現在)
                目標世代 = 目標._世代
            except 草稿存取錯誤:
                存取失敗 = True
        if 存取失敗 or type(目標世代) is not int:
            del self, 擁有者識別碼, 草稿識別碼, slug, response_schema, docs, endpoint_limit, credential_limit, 現在, 目標, 目標世代, 存取失敗
            _拒絕草稿存取()
        try:
            結構快照 = _建立回應結構快照(response_schema)
        except (KeyboardInterrupt, SystemExit, GeneratorExit):
            del self, 擁有者識別碼, 草稿識別碼, slug, response_schema, docs, endpoint_limit, credential_limit, 現在, 目標, 目標世代
            raise
        if 結構快照 is None:
            del self, 擁有者識別碼, 草稿識別碼, slug, response_schema, docs, endpoint_limit, credential_limit, 現在, 目標, 目標世代, 結構快照
            _拒絕發布值輸入()
        確認 = 發布值確認(草稿識別碼, 目標世代, slug, 結構快照, docs, endpoint_limit, credential_limit)
        目前 = 新草稿 = None
        競態失敗 = False
        with self._鎖:
            目前 = self._草稿.get(草稿識別碼)
            if (
                目前 is not 目標
                or 目前 is None
                or 目前.擁有者識別碼 != 擁有者識別碼
                or 目前._世代 != 目標世代
                or 目前.狀態 != "draft"
                or float(現在) >= 目前.到期時間
            ):
                競態失敗 = True
            else:
                新草稿 = replace(目前, 發布確認=確認)
                object.__setattr__(新草稿, "_世代", 目標世代)
                self._草稿[草稿識別碼] = 新草稿
        if 競態失敗:
            del self, 擁有者識別碼, 草稿識別碼, slug, response_schema, docs, endpoint_limit, credential_limit, 現在, 目標, 目標世代, 存取失敗, 結構快照, 確認, 目前, 新草稿, 競態失敗
            _拒絕草稿存取()
        回傳 = None
        try:
            回傳 = _必須重建公開確認(確認)
        except BaseException:
            del self, 擁有者識別碼, 草稿識別碼, slug, response_schema, docs, endpoint_limit, credential_limit, 現在, 目標, 目標世代, 存取失敗, 結構快照, 確認, 目前, 新草稿, 競態失敗, 回傳
            raise
        del self, 擁有者識別碼, 草稿識別碼, slug, response_schema, docs, endpoint_limit, credential_limit, 現在, 目標, 目標世代, 存取失敗, 結構快照, 確認, 目前, 新草稿, 競態失敗
        return 回傳

    def 讀取已確認草稿(self, 擁有者識別碼: str, 草稿識別碼: str, *, 現在: float) -> 規劃草稿:
        """鎖外重建確認草稿，再以 identity/generation/confirmation CAS 回傳。"""
        if not _是有效草稿查詢(擁有者識別碼, 草稿識別碼, 現在):
            del self, 擁有者識別碼, 草稿識別碼, 現在
            _拒絕草稿存取()
        目標 = 確認 = 公開草稿 = 目前 = None
        目標世代 = 確認草稿識別碼 = 確認草稿世代 = None
        存取失敗 = False
        with self._鎖:
            目標 = self._草稿.get(草稿識別碼)
            if 目標 is None or 目標.擁有者識別碼 != 擁有者識別碼:
                存取失敗 = True
            elif float(現在) >= 目標.到期時間:
                self._草稿.pop(草稿識別碼, None)
                存取失敗 = True
            elif 目標.狀態 != "draft":
                存取失敗 = True
            else:
                目標世代 = 目標._世代
                確認 = 目標.發布確認
                if type(確認) is not 發布值確認:
                    存取失敗 = True
                else:
                    確認草稿識別碼 = 確認.草稿識別碼
                    確認草稿世代 = 確認.草稿世代
                    if 確認草稿識別碼 != 目標.草稿識別碼 or 確認草稿世代 != 目標世代:
                        存取失敗 = True
        if 存取失敗:
            del self, 擁有者識別碼, 草稿識別碼, 現在, 目標, 確認, 公開草稿, 目前, 目標世代, 確認草稿識別碼, 確認草稿世代, 存取失敗
            _拒絕草稿存取()
        try:
            公開草稿 = _重建公開草稿(目標)
        except BaseException:
            del self, 擁有者識別碼, 草稿識別碼, 現在, 目標, 確認, 公開草稿, 目前, 目標世代, 確認草稿識別碼, 確認草稿世代, 存取失敗
            raise
        if 公開草稿 is None:
            del self, 擁有者識別碼, 草稿識別碼, 現在, 目標, 確認, 公開草稿, 目前, 目標世代, 確認草稿識別碼, 確認草稿世代, 存取失敗
            _拒絕草稿存取()
        with self._鎖:
            目前 = self._草稿.get(草稿識別碼)
            if (
                目前 is not 目標
                or 目前 is None
                or 目前.擁有者識別碼 != 擁有者識別碼
                or 目前._世代 != 目標世代
                or 目前.發布確認 is not 確認
                or 確認.草稿識別碼 != 確認草稿識別碼
                or 確認.草稿世代 != 確認草稿世代
                or 目前.狀態 != "draft"
                or float(現在) >= 目前.到期時間
            ):
                存取失敗 = True
        if 存取失敗:
            del self, 擁有者識別碼, 草稿識別碼, 現在, 目標, 確認, 公開草稿, 目前, 目標世代, 確認草稿識別碼, 確認草稿世代, 存取失敗
            _拒絕草稿存取()
        del self, 擁有者識別碼, 草稿識別碼, 現在, 目標, 確認, 目前, 目標世代, 確認草稿識別碼, 確認草稿世代, 存取失敗
        return 公開草稿

    def 更新草稿(self, 擁有者識別碼: str, 草稿識別碼: str, 綱要: Any, *, 現在: float) -> 規劃草稿:
        """更新 owner 的未到期 JSON 快照；保留草稿 identity、建立時間與期限。"""
        if not _是有效草稿查詢(擁有者識別碼, 草稿識別碼, 現在):
            del 擁有者識別碼, 草稿識別碼, 綱要, 現在
            _拒絕草稿存取()
        綱要快照 = _建立綱要快照(綱要)
        if 綱要快照 is None:
            綱要 = 綱要快照 = None
            raise ValueError(_草稿輸入錯誤訊息) from None
        草稿 = 新草稿 = None
        存取失敗 = False
        with self._鎖:
            try:
                草稿 = self._取得有效草稿_已鎖定(擁有者識別碼, 草稿識別碼, 現在)
            except 草稿存取錯誤:
                存取失敗 = True
            if not 存取失敗 and type(草稿) is 規劃草稿:
                新草稿 = replace(草稿, _綱要正規JSON=綱要快照, 發布確認=None)
                object.__setattr__(新草稿, "_世代", 草稿._世代 + 1)
                self._草稿[草稿.草稿識別碼] = 新草稿
            else:
                存取失敗 = True
        if 存取失敗 or type(新草稿) is not 規劃草稿:
            del self, 擁有者識別碼, 草稿識別碼, 綱要, 現在, 綱要快照, 草稿, 新草稿, 存取失敗
            _拒絕草稿存取()
        回傳 = None
        try:
            回傳 = _必須重建公開草稿(新草稿)
        except BaseException:
            del self, 擁有者識別碼, 草稿識別碼, 綱要, 現在, 綱要快照, 草稿, 新草稿, 存取失敗, 回傳
            raise
        del self, 擁有者識別碼, 草稿識別碼, 綱要, 現在, 綱要快照, 草稿, 新草稿, 存取失敗
        return 回傳

    def 重驗授權草稿(
        self,
        協調器: 權限協調器,
        擁有者識別碼: str,
        草稿識別碼: str,
        *,
        現在: float,
    ) -> 規劃草稿:
        """封閉地擷取釘選選擇、查詢一次 FND，再以 identity/generation CAS 重驗。"""
        目標 = 釘選摘要 = 目前摘要 = 項目 = None
        目標世代 = None
        技能名稱: list[str] = []
        工具名稱: list[str] = []
        if type(協調器) is not 權限協調器 or not _是有效草稿查詢(擁有者識別碼, 草稿識別碼, 現在):
            del self, 協調器, 擁有者識別碼, 草稿識別碼, 現在, 目標, 目標世代, 釘選摘要, 目前摘要, 技能名稱, 工具名稱, 項目
            _拒絕草稿存取()
        存取失敗 = False
        try:
            with self._鎖:
                目標 = self._草稿.get(草稿識別碼)
                if 目標 is None or 目標.擁有者識別碼 != 擁有者識別碼:
                    存取失敗 = True
                elif float(現在) >= 目標.到期時間:
                    self._草稿.pop(目標.草稿識別碼, None)
                    存取失敗 = True
                elif 目標.狀態 != "draft" or type(目標.能力摘要) is not 釘選能力摘要:
                    存取失敗 = True
                else:
                    目標世代 = 目標._世代
                    釘選摘要 = 目標.能力摘要
                    for 項目 in 釘選摘要.技能:
                        技能名稱.append(項目.名稱)
                        項目 = None
                    for 項目 in 釘選摘要.工具:
                        工具名稱.append(項目.名稱)
                        項目 = None
        except (KeyboardInterrupt, SystemExit, GeneratorExit):
            del self, 協調器, 擁有者識別碼, 草稿識別碼, 現在, 目標, 目標世代, 釘選摘要, 目前摘要, 技能名稱, 工具名稱, 項目, 存取失敗
            raise
        except BaseException:
            存取失敗 = True
        if 存取失敗:
            del self, 協調器, 擁有者識別碼, 草稿識別碼, 現在, 目標, 目標世代, 釘選摘要, 目前摘要, 技能名稱, 工具名稱, 項目, 存取失敗
            _拒絕草稿存取()
        查詢失敗 = False
        try:
            目前摘要 = 協調器.建立能力摘要(擁有者識別碼, tuple(技能名稱), tuple(工具名稱))
        except (KeyboardInterrupt, SystemExit, GeneratorExit):
            del self, 協調器, 擁有者識別碼, 草稿識別碼, 現在, 目標, 目標世代, 釘選摘要, 目前摘要, 技能名稱, 工具名稱, 項目, 存取失敗, 查詢失敗
            raise
        except BaseException:
            查詢失敗 = True
        技能名稱.clear()
        工具名稱.clear()
        競態失敗 = 漂移 = False
        過時草稿 = 結果 = None
        try:
            with self._鎖:
                結果 = self._草稿.get(草稿識別碼)
                if 結果 is None or 結果.擁有者識別碼 != 擁有者識別碼:
                    競態失敗 = True
                elif float(現在) >= 結果.到期時間:
                    self._草稿.pop(結果.草稿識別碼, None)
                    競態失敗 = True
                elif 結果.狀態 != "draft":
                    競態失敗 = True
                elif 結果 is not 目標 or 結果._世代 != 目標世代 or 結果.能力摘要 != 釘選摘要:
                    競態失敗 = True
                elif 查詢失敗 or type(目前摘要) is not 釘選能力摘要 or 目前摘要.正規JSON != 釘選摘要.正規JSON:
                    過時草稿 = replace(結果)
                    object.__setattr__(過時草稿, "狀態", "stale")
                    object.__setattr__(過時草稿, "_世代", 結果._世代 + 1)
                    self._草稿[草稿識別碼] = 過時草稿
                    漂移 = True
        except (KeyboardInterrupt, SystemExit, GeneratorExit):
            del self, 協調器, 擁有者識別碼, 草稿識別碼, 現在, 目標, 目標世代, 釘選摘要, 目前摘要, 技能名稱, 工具名稱, 項目, 存取失敗, 查詢失敗, 競態失敗, 漂移, 過時草稿, 結果
            raise
        except BaseException:
            競態失敗 = True
        if 競態失敗:
            del self, 協調器, 擁有者識別碼, 草稿識別碼, 現在, 目標, 目標世代, 釘選摘要, 目前摘要, 技能名稱, 工具名稱, 項目, 存取失敗, 查詢失敗, 競態失敗, 漂移, 過時草稿, 結果
            _拒絕草稿存取()
        if 漂移:
            del self, 協調器, 擁有者識別碼, 草稿識別碼, 現在, 目標, 目標世代, 釘選摘要, 目前摘要, 技能名稱, 工具名稱, 項目, 存取失敗, 查詢失敗, 競態失敗, 漂移, 過時草稿, 結果
            raise 規劃已過時錯誤("規劃權限已變更") from None
        if 結果 is None:
            del self, 協調器, 擁有者識別碼, 草稿識別碼, 現在, 目標, 目標世代, 釘選摘要, 目前摘要, 技能名稱, 工具名稱, 項目, 存取失敗, 查詢失敗, 競態失敗, 漂移, 過時草稿, 結果
            _拒絕草稿存取()
        回傳 = None
        try:
            回傳 = _重建公開草稿(結果)
        except BaseException:
            del self, 協調器, 擁有者識別碼, 草稿識別碼, 現在, 目標, 目標世代, 釘選摘要, 目前摘要, 技能名稱, 工具名稱, 項目, 存取失敗, 查詢失敗, 競態失敗, 漂移, 過時草稿, 結果, 回傳
            raise
        if 回傳 is None:
            del self, 協調器, 擁有者識別碼, 草稿識別碼, 現在, 目標, 目標世代, 釘選摘要, 目前摘要, 技能名稱, 工具名稱, 項目, 存取失敗, 查詢失敗, 競態失敗, 漂移, 過時草稿, 結果, 回傳
            _拒絕草稿存取()
        del self, 協調器, 擁有者識別碼, 草稿識別碼, 現在, 目標, 目標世代, 釘選摘要, 目前摘要, 技能名稱, 工具名稱, 項目, 存取失敗, 查詢失敗, 競態失敗, 漂移, 過時草稿, 結果
        return 回傳

    def 呼叫草稿(self, 擁有者識別碼: str, 草稿識別碼: str, *, 現在: float) -> None:
        """驗證 owner 與期限後固定拒絕 draft invoke，絕不建立執行階段。"""
        if not _是有效草稿查詢(擁有者識別碼, 草稿識別碼, 現在):
            del 擁有者識別碼, 草稿識別碼, 現在
            _拒絕草稿存取()
        self._取得有效草稿(擁有者識別碼, 草稿識別碼, 現在)
        raise 草稿不可執行錯誤(_草稿不可執行訊息) from None

    def _取得有效草稿(self, 擁有者識別碼: str, 草稿識別碼: str, 現在: float) -> 規劃草稿:
        """執行共同 owner/expiry gate；任何失敗均以不可列舉固定錯誤拒絕。"""
        草稿 = None
        存取失敗 = False
        with self._鎖:
            try:
                草稿 = self._取得有效草稿_已鎖定(擁有者識別碼, 草稿識別碼, 現在)
            except 草稿存取錯誤:
                存取失敗 = True
        if 存取失敗 or type(草稿) is not 規劃草稿:
            del self, 擁有者識別碼, 草稿識別碼, 現在, 草稿, 存取失敗
            _拒絕草稿存取()
        del self, 擁有者識別碼, 草稿識別碼, 現在, 存取失敗
        return 草稿

    def _取得有效草稿_已鎖定(self, 擁有者識別碼: str, 草稿識別碼: str, 現在: float) -> 規劃草稿:
        """在持有服務鎖時執行共同 owner/expiry gate。"""
        草稿 = None
        存取失敗 = False
        if not _是非空字串(擁有者識別碼) or not _是非空字串(草稿識別碼) or not _是有效時間(現在):
            存取失敗 = True
        else:
            草稿 = self._草稿.get(草稿識別碼)
        if 草稿 is None or (not 存取失敗 and 草稿.擁有者識別碼 != 擁有者識別碼):
            存取失敗 = True
        elif float(現在) >= 草稿.到期時間:
            self._草稿.pop(草稿.草稿識別碼, None)
            存取失敗 = True
        elif 草稿.狀態 != "draft":
            存取失敗 = True
        if 存取失敗 or type(草稿) is not 規劃草稿:
            del self, 擁有者識別碼, 草稿識別碼, 現在, 草稿, 存取失敗
            _拒絕草稿存取()
        del 擁有者識別碼, 草稿識別碼, 現在, 存取失敗
        return 草稿


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
