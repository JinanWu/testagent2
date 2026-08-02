"""MGT M02 草稿、原子發布與不可變版本管理路由。"""

from __future__ import annotations

import math
import re
import struct
from dataclasses import dataclass, field
from functools import partial
from inspect import signature
from typing import Annotated, Any, Literal, Protocol, cast, get_type_hints

from fastapi import APIRouter, Depends, HTTPException, Path
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, JsonValue, StrictStr, field_validator

from 繁中代理.使用者 import 使用者上下文

_識別格式 = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}\Z")
_錯誤對照 = {
    "invalid": (422, "管理操作輸入無效"),
    "draft_not_found": (404, "找不到發布草稿"),
    "endpoint_not_found": (404, "找不到發布端點"),
    "forbidden": (403, "沒有發布端點管理權限"),
    "status_conflict": (409, "發布端點狀態衝突"),
    "concurrency": (409, "發布端點已由其他操作更新"),
    "internal": (500, "發布管理服務失敗"),
}


class _嚴格請求(BaseModel):
    """拒絕額外欄位與型別轉換的管理請求基底。"""

    model_config = ConfigDict(extra="forbid", strict=True, populate_by_name=False)

    @field_validator("*", mode="after")
    @classmethod
    def 驗證JSON資源(cls, 值: Any) -> Any:
        """限制巢狀 JSON 的深度、節點與字串 UTF-8 大小。"""
        if type(值) is dict:
            _檢查JSON(值, 0, [0])
        return 值


class 建立草稿請求(_嚴格請求):
    """Planner 需求與內容；只建立不可呼叫的暫存草稿。"""

    原始需求文字: Annotated[StrictStr, Field(alias="original_requirement_text", min_length=1, max_length=16_384)]
    規劃器內容: Annotated[dict[str, JsonValue], Field(alias="planner_content")]


class 發布端點請求(_嚴格請求):
    """使用者對草稿、slug 與配置的明確發布確認。"""

    草稿識別碼: Annotated[StrictStr, Field(alias="draft_id", min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_.:-]+$")]
    短名: Annotated[StrictStr, Field(alias="slug", min_length=1, max_length=63, pattern=r"^[a-z0-9][a-z0-9-]*$")]
    配置確認: Annotated[dict[str, JsonValue], Field(alias="configuration_confirmation")]


class 建立版本請求(_嚴格請求):
    """新不可變版本的完整配置確認。"""

    配置: Annotated[dict[str, JsonValue], Field(alias="configuration")]


@dataclass(frozen=True, slots=True)
class 規劃內容:
    """傳給整合服務的 detached Planner 投影。"""

    原始需求: str
    內容: dict[str, JsonValue]


@dataclass(frozen=True, slots=True)
class 發布確認:
    """傳給單一原子發布操作的確認投影。"""

    草稿識別碼: str
    短名: str
    配置: dict[str, JsonValue]


@dataclass(frozen=True, slots=True)
class 草稿建立結果:
    """草稿建立後唯一可公開的識別、期限與預覽。"""

    草稿識別碼: Annotated[str, Field(alias="draft_id")]
    到期時間: Annotated[float, Field(alias="expires_at")]
    預覽: Annotated[dict[str, JsonValue], Field(alias="preview")]


@dataclass(frozen=True, slots=True)
class 端點發布結果:
    """原子發布 receipt；初始明文金鑰只供本次成功回應。"""

    端點識別碼: Annotated[str, Field(alias="endpoint_id")]
    版本識別碼: Annotated[str, Field(alias="version_id")]
    版本編號: Annotated[int, Field(alias="version_number")]
    狀態: Annotated[str, Field(alias="status")]
    初始API金鑰: Annotated[str, Field(alias="initial_api_key")] = field(repr=False)


@dataclass(frozen=True, slots=True)
class 版本建立結果:
    """新版本與目前指標完成原子切換後的 receipt。"""

    端點識別碼: Annotated[str, Field(alias="endpoint_id")]
    版本識別碼: Annotated[str, Field(alias="version_id")]
    版本編號: Annotated[int, Field(alias="version_number")]
    目前版本識別碼: Annotated[str, Field(alias="current_version_id")]
    結構已變更: Annotated[bool, Field(alias="schema_changed")]


@dataclass(frozen=True, slots=True)
class 管理操作錯誤:
    """整合服務唯一可回傳的固定錯誤分類。"""

    種類: Literal["invalid", "draft_not_found", "endpoint_not_found", "forbidden", "status_conflict", "concurrency", "internal"]


class 發布管理服務(Protocol):
    """由整合層提供草稿與兩個原子寫入操作。"""

    def 建立草稿(self, *, 擁有者使用者識別碼: str, 規劃: 規劃內容) -> 草稿建立結果 | 管理操作錯誤:
        """建立不具發布副作用的暫存草稿。"""
        ...

    def 原子發布(self, *, 擁有者使用者識別碼: str, 確認: 發布確認) -> 端點發布結果 | 管理操作錯誤:
        """以單一原子操作發布端點、首版與初始憑證。"""
        ...

    def 原子建立並切換版本(
        self, *, 擁有者使用者識別碼: str, 是否管理者: bool, 端點識別碼: str,
        配置: dict[str, JsonValue],
    ) -> 版本建立結果 | 管理操作錯誤:
        """原子建立不可變版本並切換目前版本指標。"""
        ...


def _呼叫服務(服務: 發布管理服務, 方法名稱: str, 重建器=None, 重建參數=(), **參數):
    """在同一 totalized 邊界呼叫服務並重建不可信回執。"""
    結果 = 方法 = 安全結果 = None
    控制流: list[BaseException] = []
    失敗 = False
    try:
        try:
            方法 = object.__getattribute__(服務, 方法名稱)
            結果 = 方法(**參數)
            if type(結果) is 管理操作錯誤:
                安全結果 = _重建管理錯誤(結果)
            elif 重建器 is None:
                安全結果 = 結果
            else:
                安全結果 = 重建器(結果, *重建參數)
        except (KeyboardInterrupt, SystemExit, GeneratorExit) as 錯誤:
            控制流.append(錯誤)
        except BaseException:
            失敗 = True
        if 控制流:
            安全結果 = None
            _重拋控制流(控制流)
        if 失敗:
            安全結果 = None
            _無效結果()
        return 安全結果
    finally:
        服務 = 方法名稱 = 重建器 = 重建參數 = 參數 = 方法 = 結果 = 安全結果 = None


def _重拋控制流(暫存: list[BaseException]) -> None:
    """pop 後重拋原控制流，使 production frame 不保留其 args。"""
    raise 暫存.pop()


def _重建管理錯誤(來源: 管理操作錯誤) -> 管理操作錯誤:
    """重建 exact error receipt，拒絕竄改種類。"""
    種類 = object.__getattribute__(來源, "種類")
    if type(種類) is not str or 種類 not in _錯誤對照:
        raise ValueError
    return 管理操作錯誤(種類)


def _拋出錯誤(錯誤: 管理操作錯誤) -> None:
    """只接受模組自有 exact error DTO 並套固定映射。"""
    try:
        種類 = object.__getattribute__(錯誤, "種類")
        狀態, 訊息 = _錯誤對照[種類]
    except BaseException:
        raise HTTPException(status_code=500, detail="發布管理服務失敗") from None
    raise HTTPException(status_code=狀態, detail=訊息)


def _重建身份(身份: 使用者上下文) -> tuple[str, bool]:
    """沿用 M01 的精確可信 session identity 契約。"""
    身份類型 = 使用者 = 管理者 = 結果 = None
    try:
        身份類型 = type(身份)
        if 身份類型 is not 使用者上下文:
            raise HTTPException(status_code=500, detail="使用者身份不符合契約")
        使用者 = object.__getattribute__(身份, "user_id")
        管理者 = object.__getattribute__(身份, "is_admin")
        if not _是識別(使用者) or type(管理者) is not bool:
            raise HTTPException(status_code=500, detail="使用者身份不符合契約")
        結果 = (使用者, 管理者)
        return 結果
    finally:
        身份 = 身份類型 = 使用者 = 管理者 = 結果 = None


def _重建草稿結果(來源: object) -> 草稿建立結果:
    """驗證並重建模組自有的安全草稿結果。"""
    識別 = 到期 = 預覽 = 安全結果 = None
    控制流: list[BaseException] = []
    失敗 = type(來源) is not 草稿建立結果
    try:
        try:
            if not 失敗:
                識別 = object.__getattribute__(來源, "草稿識別碼")
                到期 = object.__getattribute__(來源, "到期時間")
                預覽 = object.__getattribute__(來源, "預覽")
                失敗 = not _是識別(識別) or not _是時間(到期)
            if not 失敗:
                安全結果 = 草稿建立結果(識別, 到期, _複製JSON物件(預覽))
        except (KeyboardInterrupt, SystemExit, GeneratorExit) as 錯誤:
            控制流.append(錯誤)
        except BaseException:
            失敗 = True
        if 控制流:
            安全結果 = None
            _重拋控制流(控制流)
        if 失敗:
            安全結果 = None
            _無效結果()
        return 安全結果
    finally:
        來源 = 識別 = 到期 = 預覽 = 安全結果 = None


def _重建發布結果(來源: object) -> 端點發布結果:
    """先驗證所有非秘密槽，最後一次讀取並立即封裝初始金鑰。"""
    端點 = 版本 = 編號 = 狀態 = 金鑰 = 安全結果 = None
    控制流: list[BaseException] = []
    失敗 = type(來源) is not 端點發布結果
    try:
        try:
            if not 失敗:
                端點 = object.__getattribute__(來源, "端點識別碼")
                版本 = object.__getattribute__(來源, "版本識別碼")
                編號 = object.__getattribute__(來源, "版本編號")
                狀態 = object.__getattribute__(來源, "狀態")
                失敗 = not _是識別(端點) or not _是識別(版本) or type(編號) is not int or 編號 != 1 or type(狀態) is not str or 狀態 != "active"
            if not 失敗:
                金鑰 = object.__getattribute__(來源, "初始API金鑰")
                if _是金鑰(金鑰):
                    安全結果 = 端點發布結果(端點, 版本, 編號, 狀態, 金鑰)
                else:
                    失敗 = True
        except (KeyboardInterrupt, SystemExit, GeneratorExit) as 錯誤:
            控制流.append(錯誤)
        except BaseException:
            失敗 = True
        if 控制流:
            安全結果 = None
            _重拋控制流(控制流)
        if 失敗:
            安全結果 = None
            _無效結果()
        return 安全結果
    finally:
        來源 = 端點 = 版本 = 編號 = 狀態 = 金鑰 = 安全結果 = None


def _重建版本結果(來源: object, 端點識別碼: str) -> 版本建立結果:
    """逐槽精確驗證並重建與 authoritative 路徑端點一致的結果。"""
    回執端點 = 版本 = 編號 = 目前版本 = 已變更 = 安全結果 = None
    回執端點類型 = 版本類型 = 編號類型 = 目前版本類型 = 已變更類型 = None
    端點比較 = 版本比較 = None
    控制流: list[BaseException] = []
    失敗 = type(來源) is not 版本建立結果
    try:
        try:
            if not 失敗:
                回執端點 = 版本建立結果.端點識別碼.__get__(來源, 版本建立結果)
                回執端點類型 = type(回執端點)
                失敗 = 回執端點類型 is not str or not _是識別(回執端點)
            if not 失敗:
                失敗 = type(端點識別碼) is not str or not _是識別(端點識別碼)
            if not 失敗:
                端點比較 = str.__eq__(回執端點, 端點識別碼)
                失敗 = type(端點比較) is not bool or not 端點比較
            if not 失敗:
                版本 = 版本建立結果.版本識別碼.__get__(來源, 版本建立結果)
                版本類型 = type(版本)
                失敗 = 版本類型 is not str or not _是識別(版本)
            if not 失敗:
                編號 = 版本建立結果.版本編號.__get__(來源, 版本建立結果)
                編號類型 = type(編號)
                失敗 = 編號類型 is not int or 編號 < 2 or 編號 > 2_147_483_647
            if not 失敗:
                目前版本 = 版本建立結果.目前版本識別碼.__get__(來源, 版本建立結果)
                目前版本類型 = type(目前版本)
                失敗 = 目前版本類型 is not str or not _是識別(目前版本)
            if not 失敗:
                版本比較 = str.__eq__(目前版本, 版本)
                失敗 = type(版本比較) is not bool or not 版本比較
            if not 失敗:
                已變更 = 版本建立結果.結構已變更.__get__(來源, 版本建立結果)
                已變更類型 = type(已變更)
                失敗 = 已變更類型 is not bool
            if not 失敗:
                安全結果 = 版本建立結果(回執端點, 版本, 編號, 目前版本, 已變更)
        except (KeyboardInterrupt, SystemExit, GeneratorExit) as 錯誤:
            控制流.append(錯誤)
        except BaseException:
            失敗 = True
        if 控制流:
            安全結果 = None
            _重拋控制流(控制流)
        if 失敗:
            安全結果 = None
            _無效結果()
        return 安全結果
    finally:
        來源 = 端點識別碼 = 回執端點 = 版本 = 編號 = 目前版本 = 已變更 = None
        回執端點類型 = 版本類型 = 編號類型 = 目前版本類型 = 已變更類型 = None
        端點比較 = 版本比較 = 安全結果 = 錯誤 = None


def _無效結果() -> None:
    """以固定且不洩漏內容的服務失敗中止。"""
    raise HTTPException(status_code=500, detail="發布管理服務失敗") from None


def _是識別(值: Any) -> bool:
    """判斷值是否為有界 ASCII 識別碼。"""
    值類型 = 符合 = 結果 = None
    try:
        值類型 = type(值)
        if 值類型 is not str:
            結果 = False
        else:
            符合 = _識別格式.fullmatch(值)
            結果 = 符合 is not None
        return 結果
    finally:
        值 = 值類型 = 符合 = 結果 = None


def _是時間(值: Any) -> bool:
    """判斷值是否為非負有限時間。"""
    值類型 = 有限 = 結果 = None
    try:
        值類型 = type(值)
        if 值類型 not in (int, float):
            結果 = False
        else:
            有限 = math.isfinite(值)
            結果 = 有限 and 值 >= 0
        return 結果
    except (OverflowError, ValueError):
        結果 = False
        return 結果
    finally:
        值 = 值類型 = 有限 = 結果 = None


def _是金鑰(值: Any) -> bool:
    """判斷值是否符合初始 API 金鑰的基本界限。"""
    值類型 = 字元長度 = 去空白值 = 去空白相同 = 編碼值 = 位元長度 = 結果 = None
    try:
        值類型 = type(值)
        if 值類型 is not str:
            結果 = False
        else:
            字元長度 = str.__len__(值)
            if not 16 <= 字元長度 <= 512:
                結果 = False
            else:
                去空白值 = str.strip(值)
                去空白相同 = str.__eq__(去空白值, 值)
                if type(去空白相同) is not bool or not 去空白相同:
                    結果 = False
                else:
                    編碼值 = str.encode(值, "utf-8")
                    位元長度 = bytes.__len__(編碼值)
                    結果 = 位元長度 <= 2_048
        return 結果
    except UnicodeError:
        結果 = False
        return 結果
    finally:
        值 = 值類型 = 字元長度 = 去空白值 = 去空白相同 = None
        編碼值 = 位元長度 = 結果 = None


def _檢查JSON(值: JsonValue, 深度: int, 計數: list[int]) -> None:
    """檢查 Pydantic 已建立的 exact JSON tree 資源界限。"""
    計數[0] += 1
    if 計數[0] > 2_000 or 深度 > 24:
        raise ValueError("JSON 超出資源限制")
    if type(值) is str and len(值.encode("utf-8")) > 16_384:
        raise ValueError("JSON 字串過長")
    if type(值) is float and not math.isfinite(值):
        raise ValueError("JSON 數字無效")
    if type(值) is int and 值.bit_length() > 4096:
        raise ValueError("JSON 整數過大")
    if type(值) is list:
        for 項目 in 值:
            _檢查JSON(項目, 深度 + 1, 計數)
    elif type(值) is dict:
        if len(值) > 256:
            raise ValueError("JSON object 過大")
        for 鍵, 項目 in 值.items():
            if type(鍵) is not str or len(鍵.encode("utf-8")) > 256:
                raise ValueError("JSON key 無效")
            _檢查JSON(項目, 深度 + 1, 計數)


def _複製JSON物件(值: object) -> dict[str, JsonValue]:
    """單次有界遍歷建立快照，再重播來源描述器以拒絕並行變更。"""
    物件 = 安全快照 = 擷取 = 計數 = 結果 = 錯誤 = None
    控制流: list[BaseException] = []
    失敗 = False
    try:
        try:
            失敗 = type(值) is not dict
            if not 失敗:
                物件 = cast(dict[str, JsonValue], 值)
                擷取 = []
                計數 = [0]
                安全快照 = _建立JSON快照(物件, 0, 計數, 擷取)
                _重播JSON容器(擷取)
        except (KeyboardInterrupt, SystemExit, GeneratorExit) as 錯誤:
            控制流.append(錯誤)
        except BaseException as 錯誤:
            失敗 = True
        if 控制流:
            安全快照 = None
            _重拋控制流(控制流)
        if 失敗 or type(安全快照) is not dict:
            安全快照 = None
            _無效結果()
        結果 = cast(dict[str, JsonValue], 安全快照)
        return 結果
    finally:
        值 = 物件 = 安全快照 = 擷取 = 計數 = 結果 = 錯誤 = None


def _建立JSON快照(值, 深度: int, 計數: list[int], 擷取: list[tuple]) -> JsonValue:
    """以 built-in descriptors 驗證並複製，同時記錄來源形狀與子值。"""
    安全值 = 子項 = 鍵們 = 觀察值 = 部分 = 來源 = None
    長度 = 索引 = 鍵 = 值類型 = 編碼值 = 捕捉紀錄 = None
    鍵值對 = 鍵項迭代器 = 多餘項目 = None
    try:
        來源 = 值
        計數[0] += 1
        if 計數[0] > 2_000 or 深度 > 24:
            raise ValueError("JSON 超出資源限制")
        值類型 = type(來源)
        if 來源 is None or 值類型 is bool:
            安全值 = 來源
        elif 值類型 is str:
            編碼值 = 來源.encode("utf-8")
            if len(編碼值) > 16_384:
                raise ValueError("JSON 字串過長")
            安全值 = 來源
        elif 值類型 is int:
            if 來源.bit_length() > 4096:
                raise ValueError("JSON 整數過大")
            安全值 = 來源
        elif 值類型 is float:
            if not math.isfinite(來源):
                raise ValueError("JSON 數字無效")
            安全值 = 來源
        elif 值類型 is list:
            長度 = list.__len__(來源)
            if 長度 > 2_000:
                raise ValueError("JSON list 過大")
            部分 = []
            觀察值 = []
            索引 = 0
            while 索引 < 長度:
                子項 = list.__getitem__(來源, 索引)
                觀察值.append(子項)
                部分.append(_建立JSON快照(子項, 深度 + 1, 計數, 擷取))
                索引 += 1
            捕捉紀錄 = (來源, None, tuple(觀察值))
            擷取.append(捕捉紀錄)
            安全值 = 部分
        elif 值類型 is dict:
            長度 = dict.__len__(來源)
            if 長度 > 256:
                raise ValueError("JSON object 過大")
            部分 = {}
            鍵們 = []
            觀察值 = []
            鍵項迭代器 = iter(dict.items(來源))
            索引 = 0
            while 索引 < 長度:
                鍵值對 = next(鍵項迭代器, None)
                if type(鍵值對) is not tuple or tuple.__len__(鍵值對) != 2:
                    raise ValueError("JSON object 已變更")
                鍵 = tuple.__getitem__(鍵值對, 0)
                子項 = tuple.__getitem__(鍵值對, 1)
                值類型 = type(鍵)
                if 值類型 is not str:
                    raise ValueError("JSON key 無效")
                編碼值 = str.encode(鍵, "utf-8")
                if bytes.__len__(編碼值) > 256:
                    raise ValueError("JSON key 無效")
                鍵們.append(鍵)
                觀察值.append(子項)
                dict.__setitem__(部分, 鍵, _建立JSON快照(子項, 深度 + 1, 計數, 擷取))
                索引 += 1
            多餘項目 = next(鍵項迭代器, None)
            if 多餘項目 is not None:
                raise ValueError("JSON object 已變更")
            鍵們 = tuple(鍵們)
            捕捉紀錄 = (來源, 鍵們, tuple(觀察值))
            擷取.append(捕捉紀錄)
            安全值 = 部分
        else:
            raise ValueError("JSON 值無效")
        return 安全值
    finally:
        值 = 來源 = 安全值 = 子項 = 鍵們 = 觀察值 = 部分 = None
        計數 = 擷取 = 長度 = 索引 = 鍵 = 值類型 = 編碼值 = 捕捉紀錄 = None
        鍵值對 = 鍵項迭代器 = 多餘項目 = None


def _重播JSON容器(擷取: list[tuple]) -> None:
    """有界重讀每個來源容器，確認形狀、順序與每個子項未變。"""
    紀錄 = 來源 = 鍵們 = 觀察值 = 原值 = 現值 = 鍵 = 現鍵 = None
    紀錄索引 = 索引 = 現長度 = 擷取長度 = 鍵迭代器 = 鍵值對 = 多餘項目 = None
    現鍵們 = 現觀察值 = 編碼值 = 比較結果 = None
    try:
        紀錄索引 = 0
        while 紀錄索引 < len(擷取):
            紀錄 = 擷取[紀錄索引]
            來源, 鍵們, 觀察值 = 紀錄
            現長度 = list.__len__(來源) if 鍵們 is None else dict.__len__(來源)
            if 鍵們 is None:
                if 現長度 != len(觀察值):
                    raise ValueError("JSON list 已變更")
                索引 = 0
                while 索引 < len(觀察值):
                    原值 = 觀察值[索引]
                    現值 = list.__getitem__(來源, 索引)
                    比較結果 = _JSON子項相同(原值, 現值)
                    if 比較結果 is not True:
                        raise ValueError("JSON 容器已變更")
                    索引 += 1
            else:
                擷取長度 = tuple.__len__(鍵們)
                if 現長度 != 擷取長度:
                    raise ValueError("JSON object 已變更")
                if 現長度 > 256:
                    raise ValueError("JSON object 過大")
                現鍵們 = []
                現觀察值 = []
                鍵迭代器 = iter(dict.items(來源))
                索引 = 0
                while 索引 < 現長度:
                    鍵值對 = next(鍵迭代器, None)
                    if type(鍵值對) is not tuple or tuple.__len__(鍵值對) != 2:
                        raise ValueError("JSON object 已變更")
                    現鍵 = tuple.__getitem__(鍵值對, 0)
                    現值 = tuple.__getitem__(鍵值對, 1)
                    if type(現鍵) is not str:
                        raise ValueError("JSON key 無效")
                    編碼值 = str.encode(現鍵, "utf-8")
                    if bytes.__len__(編碼值) > 256:
                        raise ValueError("JSON key 無效")
                    現鍵們.append(現鍵)
                    現觀察值.append(現值)
                    索引 += 1
                多餘項目 = next(鍵迭代器, None)
                if 多餘項目 is not None or len(現鍵們) != len(鍵們):
                    raise ValueError("JSON object 已變更")
                索引 = 0
                while 索引 < len(鍵們):
                    鍵 = 鍵們[索引]
                    現鍵 = 現鍵們[索引]
                    if type(鍵) is not str or type(現鍵) is not str:
                        raise ValueError("JSON key 無效")
                    比較結果 = str.__eq__(鍵, 現鍵)
                    if type(比較結果) is not bool or not 比較結果:
                        raise ValueError("JSON object 已變更")
                    原值 = 觀察值[索引]
                    現值 = 現觀察值[索引]
                    if not _JSON子項相同(原值, 現值):
                        raise ValueError("JSON 容器已變更")
                    索引 += 1
            紀錄索引 += 1
    finally:
        擷取 = 紀錄 = 來源 = 鍵們 = 觀察值 = 原值 = 現值 = 鍵 = 現鍵 = None
        紀錄索引 = 索引 = 現長度 = 擷取長度 = 鍵迭代器 = 鍵值對 = 多餘項目 = None
        現鍵們 = 現觀察值 = 編碼值 = 比較結果 = None


def _JSON子項相同(原值, 現值) -> bool:
    """容器比較 identity；精確純量比較型別與不遺失的值。"""
    原類型 = 現類型 = 原浮點位元 = 現浮點位元 = 比較結果 = None
    try:
        原類型 = type(原值)
        現類型 = type(現值)
        if 原類型 in (list, dict):
            比較結果 = 現值 is 原值
        elif 現類型 is not 原類型:
            比較結果 = False
        elif 原類型 is float:
            原浮點位元 = struct.pack("!d", 原值)
            現浮點位元 = struct.pack("!d", 現值)
            比較結果 = 原浮點位元 == 現浮點位元
        else:
            比較結果 = 現值 == 原值
        return 比較結果
    finally:
        原值 = 現值 = 原類型 = 現類型 = None
        原浮點位元 = 現浮點位元 = 比較結果 = None
