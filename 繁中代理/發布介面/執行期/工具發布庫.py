"""Release-addressed 且封存的 Published Runtime 工具發布庫。

參數：公開 DTO 與儲存庫只接受 exact 型別、exact release 與 ordered tools。
回傳：依 exact release 建立新鮮發布檢視與版本釘選工具登錄器。
例外：一般輸入、複製及生命週期錯誤固定映射為 ``工具發布錯誤``；控制流程例外原樣傳出。
副作用：成功登錄或移除會原子修改行程內發布表；已用 release 墓碑永久保留。
"""

from __future__ import annotations

from dataclasses import dataclass
import re
import threading
from typing import NoReturn

from ...工具 import 工具定義
from ..嚴格JSON import 解析嚴格JSON
from .工具版本庫 import 工具快照項目, 工具版本庫, 建立版本釘選工具登錄器, 版本釘選工具登錄器

_識別格式 = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}\Z")
_固定錯誤 = "工具發布不可用"
_控制流程例外 = (KeyboardInterrupt, SystemExit, GeneratorExit)

class 工具發布錯誤(RuntimeError):
    """表示發布描述、release identity 或封存內容不可用。

    參數：沿用 ``RuntimeError`` 訊息參數。回傳：不適用。
    例外：建構只可能傳出基底類別標準錯誤。副作用：無。
    """

def _拒絕() -> NoReturn:
    """拋出不含原始內容的固定錯誤。

    參數：無。回傳：不會正常回傳。例外：一律拋出 ``工具發布錯誤``。副作用：無。
    """
    raise 工具發布錯誤(_固定錯誤) from None

def _是識別(值: object) -> bool:
    """驗證 release、revision 或工具名稱的 exact 有界格式。

    參數：``值`` 是待驗證物件。回傳：符合格式時為真。例外：無。副作用：無。
    """
    return type(值) is str and _識別格式.fullmatch(值) is not None

@dataclass(frozen=True, slots=True)
class 工具發布註冊:
    """描述一個發布內 ordered 工具 revision 與 legacy 工具輸入。

    參數／欄位：``revision`` 是 exact 修訂；``tool`` 是待封存 ``工具定義``。
    回傳：不可變註冊 DTO。例外：欄位外形不符時為固定 ``工具發布錯誤``。副作用：無。
    """

    revision: str
    tool: 工具定義

    def __post_init__(self) -> None:
        """拒絕 DTO 子類、非法 revision 與非 exact 工具定義。

        參數：由 dataclass 提供目前實例。回傳：無。例外：不合法時固定失敗。副作用：無。
        """
        if type(self) is not 工具發布註冊 or not _是識別(self.revision) or type(self.tool) is not 工具定義:
            _拒絕()

@dataclass(frozen=True, slots=True)
class 工具發布描述:
    """描述單一 handler release 與保持順序的工具註冊。

    參數／欄位：``handler_release`` 是唯一發布 identity；``tools`` 是 exact tuple。
    回傳：不可變發布 DTO。例外：外形不符時固定失敗。副作用：無。
    """

    handler_release: str
    tools: tuple[工具發布註冊, ...]

    def __post_init__(self) -> None:
        """先驗證 release 與容器，不走訪不可信註冊內容。

        參數：由 dataclass 提供目前實例。回傳：無。例外：不合法時固定失敗。副作用：無。
        """
        if type(self) is not 工具發布描述 or not _是識別(self.handler_release) or type(self.tools) is not tuple:
            _拒絕()

@dataclass(frozen=True, slots=True)
class _發布內容:
    """保存模組自有發布、依序快照與其專屬工具版本庫。

    參數／欄位：``handler_release`` 是發布識別，``snapshots`` 是依序快照，``revisions`` 是專屬版本庫。
    回傳／不適用：作為不可變內部資料物件供發布庫及發布檢視持有，不直接提供公開回傳契約。
    例外：建構只可能傳出資料類別配置的標準例外；輸入可信性由建立發布內容邊界負責。
    副作用：建構只保存既有物件參照，不查詢或修改專屬版本庫。
    """

    handler_release: str
    snapshots: tuple[工具快照項目, ...]
    revisions: 工具版本庫

@dataclass(frozen=True, slots=True)
class 工具發布版:
    """單一 release 的 sealed 檢視；只提供 exact revision 與新鮮衍生物。

    參數：由發布庫以 module-owned 內容建立。回傳：發布檢視。
    例外：衍生資料失敗時固定映射。副作用：建構及讀取不修改 authoritative 發布表。
    """

    _內容: _發布內容

    @property
    def handler_release(self) -> str:
        """回傳此檢視的 exact release；參數：無；例外與副作用：無。"""
        return self._內容.handler_release

    @property
    def tools(self) -> tuple[工具發布註冊, ...]:
        """重建 ordered detached 註冊。

        參數：無。回傳：每次新建 tuple、DTO、schema 與 handler binding。
        例外：任何一般重建失敗固定映射。副作用：不修改已發布內容。
        """
        失敗 = False
        try:
            結果 = []
            for 項目 in self._內容.snapshots:
                修訂 = self._內容.revisions.取得工具修訂(項目.name, 項目.revision)
                if 修訂 is None:
                    _拒絕()
                工具 = 工具定義(修訂.名稱, 修訂.說明, 解析嚴格JSON(修訂.參數JSON), 修訂.處理函數)
                結果.append(工具發布註冊(修訂.修訂名稱, 工具))
            return tuple(結果)
        except _控制流程例外:
            raise
        except 工具發布錯誤:
            raise
        except BaseException:
            失敗 = True
        if 失敗:
            _拒絕()

    @property
    def 描述(self) -> 工具發布描述:
        """回傳新鮮 immutable 描述；參數：無；例外：重建失敗時固定；副作用：無。"""
        return 工具發布描述(self.handler_release, self.tools)

    def 取得工具修訂(self, 名稱: str, 修訂名稱: str) -> object:
        """供既有釘選解析器依 exact identity 取得 module-private revision。

        參數：工具名稱與修訂名稱。回傳：detached revision 或 ``None``。例外：控制流程原樣。副作用：無。
        """
        return self._內容.revisions.取得工具修訂(名稱, 修訂名稱)

    def 建立工具登錄器(self) -> 版本釘選工具登錄器:
        """建立只含此 release ordered tools 的 fresh 釘選登錄器。

        參數：無。回傳：新登錄器。例外：一般解析失敗固定映射。副作用：無。
        """
        失敗 = False
        try:
            結果 = 建立版本釘選工具登錄器(self, self._內容.snapshots)
        except _控制流程例外:
            raise
        except BaseException:
            失敗 = True
        if 失敗:
            _拒絕()
        return 結果

class 工具發布庫:
    """以 one-shot release identity 保存 sealed 工具發布，不提供目前或預設版。

    參數：建構不接受參數。回傳：依 exact release 的 fresh 檢視。
    例外：一般發布生命週期失敗固定映射。副作用：登錄與移除會在鎖內原子更新記憶體表。
    """

    def __init__(self) -> None:
        """建立空發布表與永久墓碑；參數與回傳：無；例外：標準配置錯誤；副作用：配置鎖。"""
        self._發布: dict[str, _發布內容] = {}
        self._已使用: set[str] = set()
        self._鎖 = threading.Lock()

    def 登錄發布(self, 描述: 工具發布描述) -> 工具發布版:
        """完整預檢並封存後原子安裝 one-shot release。

        參數：exact ``工具發布描述``。回傳：新鮮發布檢視。
        例外：重複、墓碑、DTO、schema 或 handler 問題固定失敗；控制流程原樣。
        副作用：只有完整預檢成功且 identity 未用時才一次加入發布與墓碑。
        """
        失敗 = False
        try:
            內容 = _建立發布內容(描述)
            with self._鎖:
                if 內容.handler_release in self._已使用:
                    _拒絕()
                self._發布[內容.handler_release] = 內容
                self._已使用.add(內容.handler_release)
        except _控制流程例外:
            raise
        except 工具發布錯誤:
            raise
        except BaseException:
            失敗 = True
        if 失敗:
            _拒絕()
        return _建立發布檢視(內容)

    def 取得發布(self, handler_release: str) -> 工具發布版 | None:
        """只依 exact release 取得 fresh sealed 檢視，不做 fallback。

        參數：``handler_release``。回傳：新檢視或 ``None``。例外：控制流程原樣。副作用：無。
        """
        if not _是識別(handler_release):
            return None
        with self._鎖:
            內容 = self._發布.get(handler_release)
        return None if 內容 is None else _建立發布檢視(內容)

    def 移除發布(self, handler_release: str) -> None:
        """移除 live release 且保留永久墓碑。

        參數：exact release。回傳：無。例外：無預期例外。副作用：可能原子移除 live 發布。
        """
        if _是識別(handler_release):
            with self._鎖:
                self._發布.pop(handler_release, None)

def _建立發布內容(不可信描述: 工具發布描述) -> _發布內容:
    """重建完整輸入、拒絕重名並在獨立版本庫封存所有 revisions。

    參數：呼叫端描述。回傳：module-owned sealed 內容。例外：一般失敗固定映射。副作用：只修改未公開暫存庫。
    """
    if type(不可信描述) is not 工具發布描述:
        _拒絕()
    release = object.__getattribute__(不可信描述, "handler_release")
    註冊們 = object.__getattribute__(不可信描述, "tools")
    if not _是識別(release) or type(註冊們) is not tuple:
        _拒絕()
    庫, 快照們, 已看 = 工具版本庫(), [], set()
    for 不可信註冊 in 註冊們:
        if type(不可信註冊) is not 工具發布註冊:
            _拒絕()
        revision = object.__getattribute__(不可信註冊, "revision")
        不可信工具 = object.__getattribute__(不可信註冊, "tool")
        if not _是識別(revision) or type(不可信工具) is not 工具定義:
            _拒絕()
        名稱 = object.__getattribute__(不可信工具, "名稱")
        if not _是識別(名稱) or 名稱 in 已看:
            _拒絕()
        工具 = 工具定義(
            名稱,
            object.__getattribute__(不可信工具, "說明"),
            object.__getattribute__(不可信工具, "參數結構"),
            object.__getattribute__(不可信工具, "處理函數"),
        )
        快照們.append(庫.登錄修訂(revision, 工具))
        已看.add(名稱)
    return _發布內容(release, tuple(快照們), 庫)

def _建立發布檢視(內容: _發布內容) -> 工具發布版:
    """從 authoritative 內容建立不共享 repository 或 handler binding 的檢視。

    參數：module-owned 發布內容。回傳：完全 detached 的發布版。例外：重建失敗傳出供公開邊界固定映射。副作用：無。
    """
    註冊們 = []
    for 項目 in 內容.snapshots:
        修訂 = 內容.revisions.取得工具修訂(項目.name, 項目.revision)
        if 修訂 is None:
            _拒絕()
        工具 = 工具定義(修訂.名稱, 修訂.說明, 解析嚴格JSON(修訂.參數JSON), 修訂.處理函數)
        註冊們.append(工具發布註冊(修訂.修訂名稱, 工具))
    return 工具發布版(_建立發布內容(工具發布描述(內容.handler_release, tuple(註冊們))))
