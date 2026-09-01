"""Canonical uvicorn factory入口；import本模組不讀環境、不建立資料庫。"""

from 繁中代理.發布介面.asgi import 建立環境應用程式 as 建立應用程式

__all__ = ("建立應用程式",)
