"""Canonical CP3 ASGI application factories；import時不讀環境或建立資料庫。"""

from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path
from typing import cast

from fastapi import FastAPI

from .嚴格JSON import 解析嚴格JSON
from .生產Web代理 import 生產Web代理建構器
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


def 解析環境生產設定(環境: Mapping[str, str]) -> 生產設定:
    """從明確環境mapping解析exact production settings。

    必填DB path、JSON origins與provider；cookie只接受true/false，TTL只接受
    2至6位ASCII decimal。此函數不建立路徑或連線。
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


def 建立環境應用程式() -> FastAPI:
    """供``uvicorn --factory``使用；只在factory呼叫時讀取process environment。"""
    return 建立ASGI應用程式(解析環境生產設定(os.environ))


__all__ = ("建立ASGI應用程式", "建立環境應用程式", "解析環境生產設定")
