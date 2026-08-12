"""Canonical CP3／CP4 ASGI application factories。

參數：
    公開工廠接受 exact immutable 設定；環境工廠只在被呼叫時讀取 process environment。
返回值：
    建立含 canonical routes 與 lifespan-owned resources 的 FastAPI app。
例外：
    設定違約固定為 ``ValueError``；startup 一般錯誤由 lifespan 固定映射。
副作用：
    import 不讀環境或建立資料庫；app construction 也不執行 migration 或 callbacks。
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path
from typing import cast

from fastapi import FastAPI

from ..模型供應商 import GeminiADC供應商
from .嚴格JSON import 解析嚴格JSON
from .生產Web代理 import 生產Web代理建構器
from .生產Published執行 import Published生產設定, 生產Controller建構器
from .生產技能工具 import 安裝生產技能工具
from .生產組裝 import 建立生產應用程式
from .設定 import 生產設定

資料庫環境名稱 = "TESTAGENT2_DB_PATH"
來源環境名稱 = "TESTAGENT2_WEB_ORIGINS"
供應器環境名稱 = "TESTAGENT2_MODEL_PROVIDER"
安全Cookie環境名稱 = "TESTAGENT2_COOKIE_SECURE"
工作階段TTL環境名稱 = "TESTAGENT2_SESSION_TTL_SECONDS"
模型名稱環境名稱 = "TESTAGENT2_MODEL_NAME"
Gemini專案環境名稱 = "AIAGENT_GCP_PROJECT"
Gemini位置環境名稱 = "AIAGENT_GCP_LOCATION"
Web資料庫環境名稱 = 資料庫環境名稱
Published資料庫環境名稱 = "TESTAGENT2_PUBLISHED_DB_PATH"
技能套件根環境名稱 = "TESTAGENT2_PUBLISHED_BUNDLE_ROOT"
_錯誤路徑別名 = frozenset(("TESTAGENT2_WEB_DB_PATH", "TESTAGENT2_BUNDLE_ROOT"))
_核准設定環境名稱 = frozenset((
    資料庫環境名稱, 來源環境名稱, 供應器環境名稱, 安全Cookie環境名稱,
    工作階段TTL環境名稱, 模型名稱環境名稱, Gemini專案環境名稱,
    Gemini位置環境名稱, Published資料庫環境名稱, 技能套件根環境名稱,
))
_來源JSON最大位元組 = 16_384
_來源最大數量 = 64
_單一來源最大位元組 = 2_048


def 建立ASGI應用程式(設定: 生產設定) -> FastAPI:
    """由明確設定建立完整CP3 Web Agent應用程式。

    參數：
        設定: 不可變、已驗證的生產設定。
    返回值：
        含auth、Chat、sessions、skills及lifespan resources的FastAPI app。
    例外：
        ValueError: 設定或provider不符合production契約。
    副作用：
        只建立app與routers；資料庫、migration及runtime延後至lifespan startup。
    """
    if type(設定) is not 生產設定:
        raise ValueError("ASGI設定無效")
    return 建立生產應用程式(設定, 生產Web代理建構器())


def 建立CP4ASGI應用程式(設定: 生產設定, Published設定: Published生產設定) -> FastAPI:
    """由明確 executable 注入建立完整 CP4 Controller 應用程式。

    參數：
        設定: exact CP3 生產設定與 Web DB authority。
        Published設定: immutable CP4 設定與獨立 Published DB authority。
    返回值：
        含 Web 與 exact ``POST /v1/endpoints/{slug}/invoke`` 的 FastAPI app。
    例外：
        設定不合 exact production contract 時拋 ``ValueError``。
    副作用：
        只組裝 app/router；FS identity、installer、registry、DB 與 bundle FS 延至 lifespan。
    """
    if type(設定) is not 生產設定 or type(Published設定) is not Published生產設定:
        raise ValueError("ASGI設定無效") from None
    return 建立生產應用程式(設定, 生產Controller建構器(Published設定))


建立Canonical應用程式 = 建立CP4ASGI應用程式


def 解析環境生產設定(環境: Mapping[str, str]) -> 生產設定:
    """從明確環境mapping解析exact production settings。

    參數：
        環境: 提供必填 DB path、JSON origins、provider 與可選模型設定的 mapping。
    返回值：
        已 exact 驗證且不含 fallback 的不可變 ``生產設定``。
    例外：
        欄位缺失、額外型別行為或值域違約一律固定為 ``ValueError``；控制流程例外原樣傳出。
    副作用：
        只解析記憶體 mapping；不建立路徑、開啟連線或讀取 process environment。
    """
    if not isinstance(環境, Mapping):
        raise ValueError("ASGI設定無效")
    try:
        資料庫文字 = 環境.get(資料庫環境名稱)
        來源文字 = 環境.get(來源環境名稱)
        供應器 = 環境.get(供應器環境名稱)
        安全文字 = 環境.get(安全Cookie環境名稱, "true")
        TTL文字 = 環境.get(工作階段TTL環境名稱, "86400")
        模型名稱 = 環境.get(模型名稱環境名稱)
        Gemini專案 = 環境.get(Gemini專案環境名稱)
        Gemini位置 = 環境.get(Gemini位置環境名稱)
        if type(來源文字) is not str or len(來源文字.encode("utf-8")) > _來源JSON最大位元組:
            raise ValueError
        來源值 = 解析嚴格JSON(來源文字 or "")
        if (
            type(來源值) is not list or not 來源值 or len(來源值) > _來源最大數量
            or any(type(來源) is not str or len(來源.encode("utf-8")) > _單一來源最大位元組 for 來源 in 來源值)
        ):
            raise ValueError
        if 安全文字 not in {"true", "false"}:
            raise ValueError
        if not 2 <= len(TTL文字) <= 6 or not TTL文字.isascii() or not TTL文字.isdecimal():
            raise ValueError
        if type(資料庫文字) is not str or 供應器 not in {"fake", "gemini-adc"}:
            raise ValueError
        if 供應器 == "fake":
            if 模型名稱 != "fake" or Gemini專案 is not None or Gemini位置 is not None:
                raise ValueError
        elif any(type(值) is not str for 值 in (模型名稱, Gemini專案, Gemini位置)):
            raise ValueError
        return 生產設定(
            Path(資料庫文字),
            tuple(來源值),
            供應器,
            cast(str, 模型名稱),
            Gemini專案,
            Gemini位置,
            Cookie安全=安全文字 == "true",
            工作階段有效秒數=int(TTL文字),
        )
    except (KeyboardInterrupt, SystemExit, GeneratorExit):
        raise
    except Exception:
        raise ValueError("ASGI設定無效") from None


def 解析Canonical環境設定(環境: Mapping[str, str]) -> tuple[生產設定, Published生產設定]:
    """解析完整 Controller 的 canonical 路徑與固定 production authorities。

    只接受三個 canonical 路徑名稱；legacy DB alias 與未知
    ``TESTAGENT2_PUBLISHED_*`` 一律拒絕。路徑 identity 的 filesystem 檢查仍由
    lifespan 在任何 migration/provider callback 前執行，使本函式保持零 I/O。
    """
    try:
        if not isinstance(環境, Mapping):
            raise ValueError
        for 名稱 in 環境:
            if type(名稱) is not str:
                raise ValueError
            if 名稱 in _錯誤路徑別名:
                raise ValueError
            if (
                名稱.startswith("TESTAGENT2_") or 名稱.startswith("AIAGENT_")
            ) and 名稱 not in _核准設定環境名稱:
                raise ValueError
        Web文字 = 環境.get(Web資料庫環境名稱)
        Published文字 = 環境.get(Published資料庫環境名稱)
        根文字 = 環境.get(技能套件根環境名稱)
        if any(type(值) is not str or not 值 for 值 in (Web文字, Published文字, 根文字)):
            raise ValueError
        Web路徑, Published路徑, 根路徑 = Path(Web文字), Path(Published文字), Path(根文字)
        if any(not 路徑.is_absolute() or ".." in 路徑.parts for 路徑 in (Web路徑, Published路徑, 根路徑)):
            raise ValueError
        if Web路徑 == Published路徑:
            raise ValueError
        明示供應器 = 環境.get(供應器環境名稱)
        if 明示供應器 not in (None, "gemini-adc"):
            raise ValueError
        Web環境 = dict(環境)
        Web環境[資料庫環境名稱] = Web文字
        Web環境[供應器環境名稱] = "gemini-adc"
        Web設定 = 解析環境生產設定(Web環境)

        def 建立模型註冊表() -> dict[str, object]:
            """lifespan startup 建立唯一 application-owned Gemini ADC authority。"""
            return {"gemini-adc": GeminiADC供應商(
                Web設定.模型名稱,
                cast(str, Web設定.Gemini專案識別碼),
                cast(str, Web設定.Gemini位置),
            )}

        Published設定 = Published生產設定(
            Published路徑, 根路徑, 安裝生產技能工具, 建立模型註冊表,
        )
        return Web設定, Published設定
    except (KeyboardInterrupt, SystemExit, GeneratorExit):
        raise
    except BaseException:
        raise ValueError("Canonical環境設定無效") from None


def 建立環境應用程式() -> FastAPI:
    """供 ``uvicorn --factory`` 使用並延遲建立完整 Controller。

    參數：
        無；設定來源固定為目前 ``os.environ``。
    返回值：
        由已驗證環境設定建立的 CP4 FastAPI app。
    例外：
        環境設定違約時固定 ``ValueError``；app construction 例外原樣傳出。
    副作用：
        呼叫時讀取 process environment 並建立 app，不在此階段建立資料庫。
    """
    return 建立Canonical應用程式(*解析Canonical環境設定(os.environ))


__all__ = (
    "建立ASGI應用程式", "建立CP4ASGI應用程式", "建立Canonical應用程式",
    "建立環境應用程式", "解析環境生產設定", "解析Canonical環境設定",
)
