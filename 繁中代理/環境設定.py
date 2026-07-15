"""通用環境／設定工具（跨模組共用）。

功能：
    集中與業務無關的環境／設定工具，讓各模組（`儲存`、各 BigQuery 庫、技能各
    模組…）都從此取得，不必反向依賴特定工具模組。

環境變數：
    本機以 `.env` 提供、雲端由平台注入；本模組只負責載入 `.env`。
"""

from __future__ import annotations

import os
import re
from pathlib import Path

from dotenv import load_dotenv

# 核心資料（sessions / messages / 技能）預設 dataset；管理部走自己的 ADMIN_BQ_DATASET。
核心預設資料集 = "agent_core"
環境檔路徑 = Path(__file__).resolve().parents[1] / ".env"


def 載入本機環境檔() -> None:
    """載入專案 `.env`（本機用），不覆寫既有環境變數；雲端無 `.env` 時自動略過。

    參數：無。
    返回值：無。
    """
    load_dotenv(環境檔路徑, override=False)


def 檢查資源名稱(名稱: str, 欄位: str) -> None:
    """檢查資源名稱僅含安全字元，避免插入 SQL 時被注入。

    參數：
        名稱: 待檢查的名稱（專案、資料集、資料表…）。
        欄位: 錯誤訊息用的欄位標籤。
    返回值：無；含不支援字元時丟出 ValueError。
    """
    if not re.fullmatch(r"[A-Za-z0-9_\-]+", 名稱):
        raise ValueError(f"{欄位} 含有不支援的字元：{名稱}")


def 應跳過建表() -> bool:
    """是否跳過 CREATE TABLE（表已存在時加速啟動）。

    參數：無。
    返回值：bool。
    """
    載入本機環境檔()
    return os.getenv("CORE_BQ_SKIP_DDL", "").strip().lower() in {"1", "true", "yes"}


def 讀取核心BigQuery設定() -> dict[str, str | None]:
    """讀取核心資料（sessions / messages / 技能）的 BigQuery 設定。

    參數：無。
    返回值：dict，含 project / dataset / location；缺 CORE_BQ_PROJECT 時丟出 ValueError。
    """
    載入本機環境檔()
    專案 = os.getenv("CORE_BQ_PROJECT", "").strip()
    if not 專案:
        raise ValueError("BigQuery 儲存後端尚未設定：缺少 CORE_BQ_PROJECT")
    資料集 = os.getenv("CORE_BQ_DATASET", 核心預設資料集).strip() or 核心預設資料集
    for 名稱, 值 in [("project", 專案), ("dataset", 資料集)]:
        檢查資源名稱(值, 名稱)
    return {
        "project": 專案,
        "dataset": 資料集,
        "location": os.getenv("CORE_BQ_LOCATION", "").strip() or None,
    }
