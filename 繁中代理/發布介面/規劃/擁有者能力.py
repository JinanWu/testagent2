"""由權威使用者設定與 exact 工具發布重建 owner-scoped 發布能力。

參數：轉接器固定使用者庫、工具發布庫與 exact handler release。
回傳：公開查詢回 detached 規劃快照；發布解析另回技能來源與管理者權威。
例外：撤銷、漂移、畸形 authority 或資源失敗一律固定映射為能力錯誤。
副作用：每次公開操作皆重查使用者庫、工具發布庫與 bounded 技能檔案系統。
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
import re
import traceback
from typing import Any, NoReturn, Protocol

from 繁中代理.基本工具 import 取得技能根目錄清單
from 繁中代理.使用者 import 使用者上下文
from 繁中代理.工具 import 工具定義
from ..協定 import 授權工具, 授權技能, 規劃權限快照
from ..嚴格JSON import 建立正規JSON, 解析嚴格JSON
from ..安全技能目錄 import 安全技能描述, 建立錨定安全技能目錄
from ..執行期.工具發布庫 import 工具發布庫, 工具發布註冊
from .權限協調 import 能力摘要

_識別 = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}\Z")
_控制流 = (KeyboardInterrupt, SystemExit, GeneratorExit)
_固定錯誤 = "擁有者發布能力不可用"


class 使用者權威來源(Protocol):
    """定義擁有者權威的唯一讀取協定。

    參數：實作者提供可依使用者識別查詢的權威來源。
    回傳：協定方法回傳完整使用者上下文。
    例外：查詢例外契約由實作者定義。
    副作用：協定本身無副作用；實作者可查詢外部權威。
    """
    def 建立使用者上下文(self, user_id: str | None = None) -> 使用者上下文:
        """依使用者識別讀取完整上下文。

        參數：使用者識別可為明確字串或空值。
        回傳：回傳實作者提供的完整使用者上下文。
        例外：查詢與資料失敗由實作者定義。
        副作用：可依實作者行為查詢外部權威。
        """
        ...


class 擁有者能力錯誤(RuntimeError):
    """表示擁有者能力無法安全重建。

    參數：沿用執行期錯誤建構參數。
    回傳：不適用。
    例外：由轉接器建立並拋出本例外。
    副作用：建構本身無外部副作用。
    """


@dataclass(frozen=True, slots=True)
class 發布技能來源:
    """保存已重驗技能的名稱、來源根與內容雜湊。

    參數：欄位為技能名稱、來源根目錄與內容雜湊。
    回傳：建構不可變發布技能來源。
    例外：欄位驗證例外由資料類別機制原樣傳出。
    副作用：僅配置不可變資料。
    """
    名稱: str
    根目錄: Path
    內容sha256: str


@dataclass(frozen=True, slots=True)
class 已解析發布能力:
    """保存當次 detached 權限、selected skill roots 與管理者權威。

    參數：權限快照為目前投影，技能來源為釘選選擇，管理權限由目前角色推導。
    回傳：建構不可變的已解析發布能力。
    例外：欄位與權威驗證例外由轉接器邊界處理。
    副作用：僅配置不可變資料。
    """
    權限快照: 規劃權限快照
    技能來源: tuple[發布技能來源, ...]
    具有管理權限: bool

    def 建立技能表(self) -> dict[str, Path]:
        """建立發布器使用的技能根目錄輸入。

        參數：無參數。
        回傳：回傳新的技能名稱到根目錄字典。
        例外：路徑重建失敗時傳出對應例外。
        副作用：僅配置新的容器與路徑值。
        """
        return {項目.名稱: Path(項目.根目錄) for 項目 in self.技能來源}


@dataclass(frozen=True, slots=True)
class _能力資料:
    """保存單次能力重建結果。

    參數：欄位為權限快照、技能描述與管理權威。
    回傳：建構不可變的內部能力資料。
    例外：欄位驗證例外由資料類別機制原樣傳出。
    副作用：僅配置不可變資料。
    """
    快照: 規劃權限快照
    描述: tuple[安全技能描述, ...]
    管理者: bool


def _是識別(值: object) -> bool:
    """驗證精確且有界的識別字串。

    參數：值為待驗證物件。
    回傳：回傳是否為合格識別的布林值。
    例外：無預期例外。
    副作用：無外部副作用。
    """
    return type(值) is str and _識別.fullmatch(值) is not None


def _正規集合(值: Any, 容器型別: type, 上限: int) -> tuple[str, ...]:
    """驗證並排序權威字串容器。

    參數：值、預期容器型別與項目數上限。
    回傳：回傳排序且無重複的字串元組。
    例外：容器型別、識別或數量無效時拋值錯誤。
    副作用：僅配置新的不可變容器。
    """
    if type(值) is not 容器型別 or len(值) > 上限 or any(not _是識別(項目) for 項目 in 值):
        raise ValueError
    結果 = tuple(sorted(值))
    if len(結果) != len(set(結果)):
        raise ValueError
    return 結果


def _可選權限(值: Any) -> frozenset[str] | None:
    """驗證可選的精確權限集合。

    參數：值為空值或待驗證的集合。
    回傳：回傳空值或脫離來源容器的不可變集合。
    例外：集合型別、識別或數量無效時拋值錯誤。
    副作用：僅配置新的不可變集合。
    """
    if 值 is None:
        return None
    return frozenset(_正規集合(值, set, 10_000))


def _技能roots(值: Any) -> tuple[Path, ...]:
    """驗證並詞法正規化有界技能根設定。

    參數：值為可選技能根清單；空值表示讀取預設設定。
    回傳：回傳依詞法絕對路徑排序的路徑元組。
    例外：容器、項目型別或數量無效時拋值錯誤。
    副作用：空值時讀取預設設定；不解析符號連結。
    """
    原始 = 取得技能根目錄清單({"_skill_roots": None}) if 值 is None else 值
    if type(原始) is not list or len(原始) > 32:
        raise ValueError
    路徑型別 = type(Path())
    根們: list[Path] = []
    for 項目 in 原始:
        if type(項目) not in (str, 路徑型別):
            raise ValueError
        根們.append(Path(os.path.abspath(os.path.expanduser(os.fspath(Path(項目))))))
    根們.sort(key=str)
    return tuple(根們)


def _重建快照(快照: 規劃權限快照) -> 規劃權限快照:
    """重建脫離來源容器的權限快照。

    參數：快照為待複製的規劃權限資料。
    回傳：回傳全新且不可變的規劃權限快照。
    例外：畸形欄位由資料契約例外原樣傳出。
    副作用：僅配置新的資料物件。
    """
    return 規劃權限快照(快照.權限修訂,
        tuple(授權技能(項.名稱, 項.摘要, 項.內容sha256參照) for 項 in 快照.技能),
        tuple(授權工具(項.名稱, 項.釘選修訂) for 項 in 快照.工具))


def _清除控制鏈(控制: BaseException) -> None:
    """清除控制流程例外的敏感鏈。

    參數：控制為必須維持物件身分與參數的控制流程例外。
    回傳：無回傳值。
    例外：吞掉清理期間的所有失敗。
    副作用：清除追蹤框架、原因與上下文，但保留原物件及參數。
    """
    原參數 = None
    有參數 = False
    try:
        原參數 = object.__getattribute__(控制, "args")
        有參數 = True
    except BaseException:
        pass
    try:
        traceback.clear_frames(object.__getattribute__(控制, "__traceback__"))
    except BaseException:
        pass
    for 欄位, 值 in (("__cause__", None), ("__context__", None), ("__suppress_context__", True)):
        try:
            object.__setattr__(控制, 欄位, 值)
        except BaseException:
            pass
    if 有參數:
        try:
            if object.__getattribute__(控制, "args") is not 原參數:
                object.__setattr__(控制, "args", 原參數)
        except BaseException:
            pass


def _拒絕() -> NoReturn:
    """以固定錯誤拒絕擁有者能力操作。

    參數：無參數。
    回傳：不會正常回傳。
    例外：固定拋擁有者能力錯誤。
    副作用：僅造成控制流程轉移。
    """
    raise 擁有者能力錯誤(_固定錯誤) from None
