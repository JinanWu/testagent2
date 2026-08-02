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
