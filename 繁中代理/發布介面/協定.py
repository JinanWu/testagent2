"""發布介面稽核與 Planner 權威能力查詢共同協定。"""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Protocol

from .領域模型 import AuditAppendReceipt
from .領域模型 import AuditEvent

_識別格式 = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}\Z")
_sha256格式 = re.compile(r"[0-9a-f]{64}\Z")
_固定錯誤 = "無法取得規劃權限快照"
_控制流 = (KeyboardInterrupt, SystemExit, GeneratorExit)


class AuditEventSink(Protocol):
    """接收並持久化公開稽核事件的協定。

    event 是 AuditEvent exact type；回傳 AuditAppendReceipt。持久化是外部副作用；
    caller 若無法取得 committed=True 且 event_id 相符的 receipt，必須 fail closed。
    """

    def append_audit_event(self, event: AuditEvent, /) -> AuditAppendReceipt:
        """附加單一稽核事件並回傳 append receipt。

        參數: event 為要持久化的 AuditEvent exact type。
        回傳: AuditAppendReceipt，表示事件是否已提交以及提交序號。
        副作用: 將 event 附加至持久稽核紀錄；不可靠時 caller 必須 fail closed。
        """
        ...


class 規劃權限查詢錯誤(Exception):
    """權威權限快照無法安全取得。"""


def _是識別(值: object) -> bool:
    """確認值是 canonical 安全識別字串。"""
    return type(值) is str and _識別格式.fullmatch(值) is not None


def _是摘要(值: object) -> bool:
    """確認值是單行且有界的摘要。"""
    return type(值) is str and 0 < len(值) <= 500 and 值.splitlines() == [值]


@dataclass(frozen=True, slots=True)
class 授權技能:
    """已授權技能的最小內容參照；不含路徑或可變技能物件。"""

    名稱: str
    摘要: str
    內容sha256參照: str

    def __post_init__(self) -> None:
        """驗證技能識別、摘要與內容雜湊參照。"""
        名稱 = 摘要 = 內容參照 = None
        失敗 = False
        try:
            名稱 = object.__getattribute__(self, "名稱")
            摘要 = object.__getattribute__(self, "摘要")
            內容參照 = object.__getattribute__(self, "內容sha256參照")
            if not _是識別(名稱) or not _是摘要(摘要):
                失敗 = True
            elif type(內容參照) is not str or _sha256格式.fullmatch(內容參照) is None:
                失敗 = True
        except _控制流:
            del self, 名稱, 摘要, 內容參照, 失敗
            raise
        except BaseException:
            失敗 = True
        if 失敗:
            del self, 名稱, 摘要, 內容參照, 失敗
            raise ValueError("授權技能無效") from None


@dataclass(frozen=True, slots=True)
class 授權工具:
    """已授權工具的名稱及固定修訂。"""

    名稱: str
    釘選修訂: str

    def __post_init__(self) -> None:
        """驗證工具名稱與不可變修訂。"""
        名稱 = 釘選修訂 = None
        失敗 = False
        try:
            名稱 = object.__getattribute__(self, "名稱")
            釘選修訂 = object.__getattribute__(self, "釘選修訂")
            if not _是識別(名稱) or not _是識別(釘選修訂):
                失敗 = True
        except _控制流:
            del self, 名稱, 釘選修訂, 失敗
            raise
        except BaseException:
            失敗 = True
        if 失敗:
            del self, 名稱, 釘選修訂, 失敗
            raise ValueError("授權工具無效") from None


@dataclass(frozen=True, slots=True)
class 規劃權限快照:
    """某擁有者在單一權限修訂下的完整權威能力快照。"""

    權限修訂: str
    技能: tuple[授權技能, ...]
    工具: tuple[授權工具, ...]

    def __post_init__(self) -> None:
        """驗證 exact 容器、DTO、唯一性及決定性排序。"""
        權限修訂 = 技能 = 工具 = 項目 = 名稱 = 摘要 = 內容參照 = 釘選修訂 = 前一名稱 = None
        失敗 = False
        try:
            權限修訂 = object.__getattribute__(self, "權限修訂")
            技能 = object.__getattribute__(self, "技能")
            工具 = object.__getattribute__(self, "工具")
            if not _是識別(權限修訂) or type(技能) is not tuple or type(工具) is not tuple:
                失敗 = True
            else:
                for 項目 in 技能:
                    if type(項目) is not 授權技能:
                        失敗 = True
                        break
                    名稱 = object.__getattribute__(項目, "名稱")
                    摘要 = object.__getattribute__(項目, "摘要")
                    內容參照 = object.__getattribute__(項目, "內容sha256參照")
                    if not _是識別(名稱) or not _是摘要(摘要):
                        失敗 = True
                        break
                    if type(內容參照) is not str or _sha256格式.fullmatch(內容參照) is None:
                        失敗 = True
                        break
                if not 失敗:
                    for 項目 in 工具:
                        if type(項目) is not 授權工具:
                            失敗 = True
                            break
                        名稱 = object.__getattribute__(項目, "名稱")
                        釘選修訂 = object.__getattribute__(項目, "釘選修訂")
                        if not _是識別(名稱) or not _是識別(釘選修訂):
                            失敗 = True
                            break
                if not 失敗:
                    前一名稱 = None
                    for 項目 in 技能:
                        名稱 = object.__getattribute__(項目, "名稱")
                        if 前一名稱 is not None and 名稱 <= 前一名稱:
                            失敗 = True
                            break
                        前一名稱 = 名稱
                if not 失敗:
                    前一名稱 = None
                    for 項目 in 工具:
                        名稱 = object.__getattribute__(項目, "名稱")
                        if 前一名稱 is not None and 名稱 <= 前一名稱:
                            失敗 = True
                            break
                        前一名稱 = 名稱
        except _控制流:
            del self, 權限修訂, 技能, 工具, 項目, 名稱, 摘要, 內容參照, 釘選修訂, 前一名稱, 失敗
            raise
        except BaseException:
            失敗 = True
        if 失敗:
            del self, 權限修訂, 技能, 工具, 項目, 名稱, 摘要, 內容參照, 釘選修訂, 前一名稱, 失敗
            raise ValueError("規劃權限快照無效") from None


class Planner權限查詢(Protocol):
    """只提供 owner-scoped 完整權威快照；不得使用全域、即時或 fallback 來源。"""

    def 查詢規劃權限(self, 擁有者: str, /) -> 規劃權限快照:
        """回傳該擁有者目前完整且決定性排序的授權能力。"""
        ...


def 安全查詢規劃權限(查詢器: Planner權限查詢, 擁有者: str, /) -> 規劃權限快照:
    """預檢擁有者後單次查詢，並重建可信的完整快照。"""
    if not _是識別(擁有者):
        del 查詢器, 擁有者
        raise 規劃權限查詢錯誤(_固定錯誤) from None

    方法 = 原始 = 權限修訂 = 原始技能 = 原始工具 = None
    技能清單 = 工具清單 = 項目 = 名稱 = 摘要 = 內容參照 = 釘選修訂 = 結果 = None
    失敗 = False
    try:
        方法 = getattr(查詢器, "查詢規劃權限")
        原始 = 方法(擁有者)
        if type(原始) is not 規劃權限快照:
            raise ValueError
        權限修訂 = object.__getattribute__(原始, "權限修訂")
        原始技能 = object.__getattribute__(原始, "技能")
        原始工具 = object.__getattribute__(原始, "工具")
        if not _是識別(權限修訂) or type(原始技能) is not tuple or type(原始工具) is not tuple:
            raise ValueError

        技能清單 = []
        for 項目 in 原始技能:
            if type(項目) is not 授權技能:
                raise ValueError
            名稱 = object.__getattribute__(項目, "名稱")
            摘要 = object.__getattribute__(項目, "摘要")
            內容參照 = object.__getattribute__(項目, "內容sha256參照")
            if not _是識別(名稱) or not _是摘要(摘要):
                raise ValueError
            if type(內容參照) is not str or _sha256格式.fullmatch(內容參照) is None:
                raise ValueError
            技能清單.append(授權技能(名稱, 摘要, 內容參照))

        工具清單 = []
        for 項目 in 原始工具:
            if type(項目) is not 授權工具:
                raise ValueError
            名稱 = object.__getattribute__(項目, "名稱")
            釘選修訂 = object.__getattribute__(項目, "釘選修訂")
            if not _是識別(名稱) or not _是識別(釘選修訂):
                raise ValueError
            工具清單.append(授權工具(名稱, 釘選修訂))

        結果 = 規劃權限快照(權限修訂, tuple(技能清單), tuple(工具清單))
    except _控制流:
        del 查詢器, 擁有者, 方法, 原始, 權限修訂, 原始技能, 原始工具
        del 技能清單, 工具清單, 項目, 名稱, 摘要, 內容參照, 釘選修訂, 結果, 失敗
        raise
    except BaseException:
        失敗 = True

    if 失敗:
        del 查詢器, 擁有者, 方法, 原始, 權限修訂, 原始技能, 原始工具
        del 技能清單, 工具清單, 項目, 名稱, 摘要, 內容參照, 釘選修訂, 結果, 失敗
        raise 規劃權限查詢錯誤(_固定錯誤) from None
    del 查詢器, 擁有者, 方法, 原始, 權限修訂, 原始技能, 原始工具
    del 技能清單, 工具清單, 項目, 名稱, 摘要, 內容參照, 釘選修訂, 失敗
    assert 結果 is not None
    return 結果
