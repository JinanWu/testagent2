"""PUB P04 端點發布資料傳輸物件與 SQLite 原子交易。

參數：不適用；本模組定義發布快照、初始憑證、結果與交易服務。
回傳：不適用；各資料型別與發布操作的回傳契約由其文件字串分別說明。
例外：匯入相依模組失敗時原樣傳出匯入例外。
副作用：匯入時只定義型別、常數與函式，不開啟資料庫或發布端點。
"""

from __future__ import annotations

import json
import math
import os

import re
import sqlite3
import stat
import traceback
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, NoReturn

from ..憑證.儲存庫 import _allowlist_json有效
from ..資料庫結構契約 import 驗證資料庫結構
from ..技能套件.儲存庫 import 套件收據儲存庫
from ..技能套件.發布器 import 套件發布收據
from .綱要 import 發布值確認, 規劃草稿, _重建公開草稿

_JSON_UTF8上限 = 1024 * 1024
_字串UTF8上限 = 64 * 1024
_識別上限 = 128
_最多節點 = 10_000
_最大深度 = 64
_識別格式 = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}\Z")
_禁止秘密鍵 = re.compile(
    r"(?:^|[_-])(?:api[_-]?key|raw[_-]?key|provider[_-]?token|access[_-]?token|"
    r"refresh[_-]?token|authorization|password|private[_-]?key|secret)(?:$|[_-])",
    re.IGNORECASE,
)
_輸入錯誤訊息 = "端點發布輸入無效"
_發布錯誤訊息 = "端點發布失敗"


class 端點發布輸入錯誤(ValueError):
    """代表發布 DTO、草稿或純量未通過 exact preflight。"""


class 端點發布錯誤(RuntimeError):
    """代表資料庫發布無法原子完成。"""


@dataclass(frozen=True, slots=True)
class 發布版本快照:
    """完整對應已發布第一版欄位的脫離快照。

    欄位：需求、系統提示詞、技能與工具、工具結構及修訂、模型與重試設定、技能套件
    清單、輸入與回應結構，以及建立者識別碼共同描述待發布版本。
    回傳：建立已重建可變 JSON 樹且通過固定契約驗證的不可變快照。
    例外：欄位型別、界限、JSON 或契約無效時拋出 ``端點發布輸入錯誤``；
    控制流程例外原樣傳出。
    副作用：建構時以脫離副本取代可變欄位，不讀寫資料庫或其他外部資源。
    """

    original_requirement_text: str = field(repr=False)
    system_prompt: str = field(repr=False)
    allowed_skills: list[str] = field(repr=False)
    allowed_tools: list[str] = field(repr=False)
    tool_schema_snapshot: dict[str, Any] = field(repr=False)
    tool_runtime_revision: str
    model_config_snapshot: dict[str, Any] = field(repr=False)
    retry_policy: dict[str, Any] = field(repr=False)
    skill_bundle_manifest: dict[str, Any] = field(repr=False)
    input_schema: dict[str, Any] | None = field(repr=False)
    response_schema: dict[str, Any] = field(repr=False)
    created_by_user_id: str

    def __post_init__(self) -> None:
        """以單次精確走訪驗證並建立模組自有的 JSON 樹。

        參數：無額外參數；讀取目前快照實例的全部發布欄位。
        回傳：重建與驗證成功時回傳 ``None``。
        例外：輸入型別、界限、JSON 或欄位契約不符時傳出 ``端點發布輸入錯誤``；
        控制流程例外原樣傳出。
        副作用：以不可變欄位取代可變輸入的脫離副本，不執行外部輸入輸出。
        """
        try:
            _重建版本快照(self, 建構中=True)
        except BaseException:
            del self
            raise


@dataclass(frozen=True, slots=True, repr=False)
class 已準備初始憑證:
    """只攜帶已加密憑證材料，結構上不接受明文金鑰。

    欄位：名稱、用途、金鑰版本、nonce、密文、摘要、前綴、末四碼、到期時間、
    允許位址、速率上限與建立者識別碼描述已準備憑證。
    回傳：建立已驗證密文材料與脫離允許清單的不可變憑證資料。
    例外：欄位型別、密文、摘要、界限或允許清單無效時拋出 ``端點發布輸入錯誤``；
    控制流程例外原樣傳出。
    副作用：建構時重建可變允許清單，不讀寫資料庫或接觸明文金鑰。
    """

    name: str
    purpose: str
    key_version: int
    key_nonce: bytes
    key_ciphertext: bytes
    key_hash: str
    key_prefix: str
    key_last4: str
    expires_at: float
    ip_allowlist: list[Any]
    rate_limit_requests: int
    created_by_user_id: str

    def __post_init__(self) -> None:
        """驗證已準備密文、摘要、生命週期與正規允許清單。

        參數：無額外參數；讀取目前憑證實例的密文與公開中繼欄位。
        回傳：重建與驗證成功時回傳 ``None``。
        例外：輸入型別、密文、摘要、界限或允許清單不符時傳出
        ``端點發布輸入錯誤``；控制流程例外原樣傳出。
        副作用：以脫離副本取代可變允許清單，不存取資料庫或明文金鑰。
        """
        try:
            _重建初始憑證(self, 建構中=True)
        except BaseException:
            del self
            raise


@dataclass(frozen=True, slots=True)
class 已準備發布識別:
    """保存協調器一次預配的完整發布圖形、套件與稽核識別。

    參數：四個既有圖形識別、套件識別、稽核識別與建立時間必須共同來自
    同一次協調，且六個識別不得碰撞。
    回傳：建立 exact、不可變且無實例字典的預配識別快照。
    例外：欄位型別、格式、唯一性或時間不符時拋出 ``端點發布輸入錯誤``。
    副作用：只驗證並保存不可變純量，不存取資料庫或呼叫外部工廠。
    """

    endpoint_id: str
    version_id: str
    credential_id: str
    service_account_id: str
    套件識別碼: str
    稽核識別碼: str
    created_at: float

    def __post_init__(self) -> None:
        """拒絕非 exact、碰撞或無效時間的預配識別集合。

        參數：無額外參數；精確讀取目前實例的七個固定欄位。
        回傳：全部欄位通過驗證時回傳 ``None``。
        例外：實例型別、識別格式、唯一性或時間不符時拋出 ``端點發布輸入錯誤``。
        副作用：只檢查不可變純量，不存取資料庫或其他外部資源。
        """
        if type(self) is not 已準備發布識別:
            _拒絕輸入()
        識別碼 = (
            self.endpoint_id, self.version_id, self.credential_id,
            self.service_account_id, self.套件識別碼, self.稽核識別碼,
        )
        if any(not _是識別(值) for 值 in 識別碼) or len(set(識別碼)) != 6:
            _拒絕輸入()
        if not _是有限非負(self.created_at):
            _拒絕輸入()


@dataclass(frozen=True, slots=True)
class 端點發布結果:
    """只揭露新圖形的非敏感識別碼與固定 v1 狀態。"""

    endpoint_id: str
    version_id: str
    credential_id: str
    service_account_id: str
    version_number: int = field(default=1, init=False)
    status: str = field(default="active", init=False)

    def __post_init__(self) -> None:
        """拒絕偽造固定結果或非法識別碼。"""
        if type(self) is not 端點發布結果:
            _拒絕輸入()
        for 值 in (self.endpoint_id, self.version_id, self.credential_id, self.service_account_id):
            if not _是識別(值):
                _拒絕輸入()
        if self.version_number != 1 or self.status != "active":
            _拒絕輸入()


class SQLite端點發布服務:
    """在既有完整遷移 SQLite 中原子建立端點、第一版、服務帳號與憑證。

    參數：建構時接收資料庫路徑、四個識別工廠、時鐘與可替換連線工廠。
    回傳：建立可重複呼叫的發布服務；各發布方法回傳不可變發布結果。
    例外：建構只保存依賴；發布時依輸入或交易邊界傳出固定發布例外。
    副作用：建構不開啟資源；發布方法會開啟資料庫、執行單一交易並關閉連線。
    """

    def __init__(
        self, database_path: str | Path, endpoint_id_factory: Callable[[], str],
        version_id_factory: Callable[[], str], credential_id_factory: Callable[[], str],
        service_account_id_factory: Callable[[], str], clock: Callable[[], float],
        connection_factory: Callable[..., sqlite3.Connection] = sqlite3.connect,
    ) -> None:
        """保存路徑與 callback；所有 callback 都會在交易開始前完成。"""
        self._資料庫路徑 = database_path
        self._識別工廠 = (endpoint_id_factory, version_id_factory, credential_id_factory, service_account_id_factory)
        self._時鐘 = clock
        self._連線工廠 = connection_factory

    def 發布(
        self, owner_user_id: str, draft: 規劃草稿, version_snapshot: 發布版本快照,
        prepared_credential: 已準備初始憑證, now: float,
    ) -> 端點發布結果:
        """完整 preflight 後，以單一連線與 BEGIN IMMEDIATE 發布固定 v1 圖形。"""
        草稿副本 = 版本副本 = 憑證副本 = 確認 = 識別碼 = 建立時間 = 路徑 = 身分 = uri = 連線 = 結果 = None
        發布失敗 = False
        try:
            草稿副本, 版本副本, 憑證副本, 確認 = _發布前驗證(
                owner_user_id, draft, version_snapshot, prepared_credential, now,
            )
            識別碼 = _呼叫發布callbacks(self._識別工廠, self._時鐘)
            建立時間 = 識別碼[4]
            路徑, 身分 = _驗證既有資料庫路徑(self._資料庫路徑)
            uri = 路徑.as_uri() + "?mode=rw"
            連線 = self._連線工廠(uri, uri=True, timeout=30.0, isolation_level=None)
            _驗證已開啟資料庫路徑(連線, 路徑, 身分)
            _驗證並寫入(連線, owner_user_id, 草稿副本, 版本副本, 憑證副本, 確認, 識別碼, 建立時間)
            結果 = 端點發布結果(識別碼[0], 識別碼[1], 識別碼[2], 識別碼[3])
        except (KeyboardInterrupt, SystemExit, GeneratorExit):
            del self, owner_user_id, draft, version_snapshot, prepared_credential, now, 草稿副本, 版本副本, 憑證副本, 確認, 識別碼, 建立時間, 路徑, 身分, uri, 連線, 結果, 發布失敗
            raise
        except (端點發布輸入錯誤, 端點發布錯誤):
            del self, owner_user_id, draft, version_snapshot, prepared_credential, now, 草稿副本, 版本副本, 憑證副本, 確認, 識別碼, 建立時間, 路徑, 身分, uri, 連線, 結果, 發布失敗
            raise
        except BaseException:
            發布失敗 = True
        if 發布失敗:
            del self, owner_user_id, draft, version_snapshot, prepared_credential, now, 草稿副本, 版本副本, 憑證副本, 確認, 識別碼, 建立時間, 路徑, 身分, uri, 連線, 結果, 發布失敗
            _拒絕發布()
        del self, owner_user_id, draft, version_snapshot, prepared_credential, now, 草稿副本, 版本副本, 憑證副本, 確認, 識別碼, 建立時間, 路徑, 身分, uri, 連線, 發布失敗
        return 結果

    def 發布已準備圖形(
        self, owner_user_id: str, draft: 規劃草稿, version_snapshot: 發布版本快照,
        prepared_credential: 已準備初始憑證, 預配識別: 已準備發布識別,
        套件收據: 套件發布收據, *, 請求識別碼: str | None,
        寫入前權威確認: Callable[[], object] | None = None,
    ) -> 端點發布結果:
        """重驗呼叫端預配關係，於同一立即交易寫入圖形、收據與發布稽核。

        參數：擁有者、草稿、版本與憑證描述圖形；預配識別、套件收據及可空請求
        識別碼描述協調結果；``寫入前權威確認`` 為可空且無參數 callback，會在
        ``BEGIN IMMEDIATE`` 與 schema 驗證後、任何 INSERT 前呼叫。
        回傳：提交成功後回傳只含四個圖形識別的 ``端點發布結果``。
        例外：輸入與關係不符時拋出 ``端點發布輸入錯誤``；交易失敗時拋出
        ``端點發布錯誤``；callback 的一般例外會固定映射為前述發布錯誤，三種
        控制流程例外保留原物件 identity、``args``，並清除 cause/context/suppress。
        副作用：完整預檢後開啟一條連線，以單一立即交易寫入圖形、套件收據與稽核列，
        最後提交或回滾並關閉連線。
        """
        草稿副本 = 版本副本 = 憑證副本 = 確認 = 識別碼 = 收據 = 路徑 = 身分 = uri = 連線 = 結果 = None
        發布失敗 = False
        try:
            識別碼 = _驗證預配識別(預配識別)
            草稿副本, 版本副本, 憑證副本, 確認 = _發布前驗證(
                owner_user_id, draft, version_snapshot, prepared_credential, 識別碼[6],
            )
            收據 = _驗證預配關係(識別碼, 版本副本, 套件收據, 請求識別碼)
            if 寫入前權威確認 is not None and not callable(寫入前權威確認):
                _拒絕輸入()
            路徑, 身分 = _驗證既有資料庫路徑(self._資料庫路徑)
            uri = 路徑.as_uri() + "?mode=rw"
            連線 = self._連線工廠(uri, uri=True, timeout=30.0, isolation_level=None)
            _驗證已開啟資料庫路徑(連線, 路徑, 身分)
            _驗證並寫入(
                連線, owner_user_id, 草稿副本, 版本副本, 憑證副本, 確認,
                識別碼, 識別碼[6], 收據, 識別碼[5], 請求識別碼,
                寫入前權威確認,
            )
            結果 = 端點發布結果(*識別碼[:4])
        except (KeyboardInterrupt, SystemExit, GeneratorExit):
            del self, owner_user_id, draft, version_snapshot, prepared_credential, 預配識別, 套件收據, 請求識別碼, 寫入前權威確認, 草稿副本, 版本副本, 憑證副本, 確認, 識別碼, 收據, 路徑, 身分, uri, 連線, 結果, 發布失敗
            raise
        except (端點發布輸入錯誤, 端點發布錯誤):
            del self, owner_user_id, draft, version_snapshot, prepared_credential, 預配識別, 套件收據, 請求識別碼, 寫入前權威確認, 草稿副本, 版本副本, 憑證副本, 確認, 識別碼, 收據, 路徑, 身分, uri, 連線, 結果, 發布失敗
            raise
        except BaseException:
            發布失敗 = True
        if 發布失敗:
            del self, owner_user_id, draft, version_snapshot, prepared_credential, 預配識別, 套件收據, 請求識別碼, 寫入前權威確認, 草稿副本, 版本副本, 憑證副本, 確認, 識別碼, 收據, 路徑, 身分, uri, 連線, 結果, 發布失敗
            _拒絕發布()
        del self, owner_user_id, draft, version_snapshot, prepared_credential, 預配識別, 套件收據, 請求識別碼, 寫入前權威確認, 草稿副本, 版本副本, 憑證副本, 確認, 識別碼, 收據, 路徑, 身分, uri, 連線, 發布失敗
        return 結果


def _驗證預配識別(來源: Any) -> tuple[str, str, str, str, str, str, float]:
    """重讀 exact DTO slots，避免信任 frozen instance identity。

    參數：``來源`` 是不可信的預配識別候選物件。
    回傳：依欄位宣告順序排列的六個識別與建立時間不可變 tuple。
    例外：型別、欄位讀取、識別唯一性或時間不符時拋出 ``端點發布輸入錯誤``。
    副作用：只讀取固定 slots 並配置新 tuple，不存取外部資源。
    """
    if type(來源) is not 已準備發布識別:
        _拒絕輸入()
    結果 = tuple(object.__getattribute__(來源, 欄位) for 欄位 in 已準備發布識別.__dataclass_fields__)
    if (
        len(結果) != 7 or any(not _是識別(值) for 值 in 結果[:6])
        or len(set(結果[:6])) != 6 or not _是有限非負(結果[6])
    ):
        _拒絕輸入()
    return 結果  # type: ignore[return-value]


def _驗證預配關係(
    識別碼: tuple[Any, ...], 快照: 發布版本快照,
    收據: 套件發布收據, 請求識別碼: str | None,
) -> 套件發布收據:
    """建立脫離收據並重驗版本投影、路徑及預配識別關係。

    參數：識別 tuple 與版本快照是已重建圖形；收據仍屬不可信呼叫端；請求識別碼
    可為 ``None`` 或合法識別。
    回傳：以一次精確 slots 讀取建立、由本模組持有的新 ``套件發布收據``。
    例外：型別、欄位、摘要、路徑或跨物件關係不符時拋出 ``端點發布輸入錯誤``。
    副作用：只讀取不可信收據一次並配置脫離快照，不開啟資料庫或存取檔案系統。
    """
    if type(收據) is not 套件發布收據 or (請求識別碼 is not None and not _是識別(請求識別碼)):
        _拒絕輸入()
    try:
        收據欄位 = tuple(
            object.__getattribute__(收據, 欄位)
            for 欄位 in 套件發布收據.__dataclass_fields__
        )
        收據快照 = 套件發布收據(*收據欄位)
    except (KeyboardInterrupt, SystemExit, GeneratorExit):
        del 識別碼, 快照, 收據, 請求識別碼
        raise
    except BaseException:
        _拒絕輸入()
    清單投影 = 快照.skill_bundle_manifest
    路徑 = 收據快照.路徑
    if (
        type(清單投影) is not dict or 收據快照.套件識別碼 != 識別碼[4]
        or 清單投影.get("bundle_id") != 識別碼[4]
        or 清單投影.get("manifest_reference") != 收據快照.清單參照
        or 清單投影.get("manifest_digest") != 收據快照.清單摘要
        or 清單投影.get("sha256") != 收據快照.套件雜湊
        or type(收據快照.清單參照) is not str
        or 收據快照.清單參照 != f"{識別碼[4]}/manifest.json"
        or type(收據快照.清單摘要) is not str or re.fullmatch(r"[0-9a-f]{64}", 收據快照.清單摘要) is None
        or type(收據快照.套件雜湊) is not str or re.fullmatch(r"[0-9a-f]{64}", 收據快照.套件雜湊) is None
        or type(收據快照.總位元組數) is not int or not 0 <= 收據快照.總位元組數 <= 4 * 1024 * 1024
        or type(路徑) is not type(Path()) or 路徑.name != 識別碼[4]
    ):
        _拒絕輸入()
    return 收據快照


def _發布前驗證(owner: Any, draft: Any, snapshot: Any, credential: Any, now: Any) -> tuple[Any, ...]:
    """不觸發 callback/DB 地重建 DTO，並精確投影釘選能力摘要。"""
    草稿副本 = 版本副本 = 憑證副本 = 確認 = 綱要 = 摘要 = 項目 = None
    manifest = manifest技能 = schema = 投影 = 結果 = None
    技能: list[str] = []
    工具: list[str] = []
    失敗 = not _是識別(owner) or not _是有限非負(now) or type(draft) is not 規劃草稿
    try:
        if not 失敗:
            失敗 = object.__getattribute__(draft, "擁有者識別碼") != owner
        if not 失敗:
            草稿副本 = _重建公開草稿(draft)
            失敗 = type(草稿副本) is not 規劃草稿
        if not 失敗:
            失敗 = 草稿副本.狀態 != "draft" or now >= 草稿副本.到期時間
        if not 失敗:
            確認 = 草稿副本.發布確認
            失敗 = type(確認) is not 發布值確認 or 確認.草稿識別碼 != 草稿副本.草稿識別碼 or 確認.草稿世代 != 草稿副本._世代
        if not 失敗:
            版本副本 = _重建版本快照(snapshot)
            憑證副本 = _重建初始憑證(credential)
            失敗 = 版本副本.created_by_user_id != owner or 憑證副本.created_by_user_id != owner
        if not 失敗:
            綱要 = 草稿副本.綱要
            失敗 = type(綱要) is not dict or type(綱要.get("system_prompt")) is not str
        if not 失敗:
            失敗 = 版本副本.original_requirement_text != 草稿副本.原始需求 or 版本副本.system_prompt != 綱要["system_prompt"]
        if not 失敗:
            失敗 = 版本副本.response_schema != 確認.response_schema or 憑證副本.rate_limit_requests != 確認.credential_limit
        if not 失敗:
            摘要 = 草稿副本.能力摘要
            if 摘要 is not None:
                for 項目 in 摘要.技能:
                    技能.append(項目.名稱)
                    項目 = None
                for 項目 in 摘要.工具:
                    工具.append(項目.名稱)
                    項目 = None
                失敗 = 技能 != 版本副本.allowed_skills or 工具 != 版本副本.allowed_tools
                manifest = 版本副本.skill_bundle_manifest
                manifest技能 = manifest.get("skills") if type(manifest) is dict else None
                if type(manifest) is not dict or manifest.get("permission_revision") != 摘要.權限修訂 or type(manifest技能) is not list or len(manifest技能) != len(摘要.技能):
                    失敗 = True
                if not 失敗:
                    for 索引 in range(len(摘要.技能)):
                        項目 = 摘要.技能[索引]
                        投影 = manifest技能[索引]
                        if type(投影) is not dict or len(投影) != 2 or "name" not in 投影 or "content_sha256_reference" not in 投影 or 投影["name"] != 項目.名稱 or 投影["content_sha256_reference"] != 項目.內容sha256參照:
                            失敗 = True
                            break
                        項目 = 投影 = None
                if type(版本副本.tool_schema_snapshot) is not dict or len(版本副本.tool_schema_snapshot) != len(工具) or frozenset(dict.keys(版本副本.tool_schema_snapshot)) != frozenset(工具):
                    失敗 = True
                if not 失敗:
                    for 項目 in 摘要.工具:
                        schema = 版本副本.tool_schema_snapshot[項目.名稱]
                        if type(schema) is not dict or schema.get("revision") != 項目.釘選修訂:
                            失敗 = True
                            break
                        項目 = schema = None
        if not 失敗:
            結果 = (草稿副本, 版本副本, 憑證副本, 確認)
    except (KeyboardInterrupt, SystemExit, GeneratorExit):
        技能.clear()
        工具.clear()
        del owner, draft, snapshot, credential, now, 草稿副本, 版本副本, 憑證副本, 確認, 綱要, 摘要, 項目, manifest, manifest技能, schema, 投影, 結果, 技能, 工具, 失敗
        raise
    except BaseException:
        失敗 = True
    技能.clear()
    工具.clear()
    if 失敗 or 結果 is None:
        del owner, draft, snapshot, credential, now, 草稿副本, 版本副本, 憑證副本, 確認, 綱要, 摘要, 項目, manifest, manifest技能, schema, 投影, 結果, 技能, 工具, 失敗
        _拒絕輸入()
    del owner, draft, snapshot, credential, now, 草稿副本, 版本副本, 憑證副本, 確認, 綱要, 摘要, 項目, manifest, manifest技能, schema, 投影, 技能, 工具, 失敗
    return 結果


def _呼叫發布callbacks(工廠: tuple[Callable[[], str], ...], 時鐘: Callable[[], float]) -> tuple[Any, ...]:
    """在任何 open 前一次完成四個識別工廠與時鐘。"""
    值: list[Any] = []
    callback = 輸出 = 結果 = None
    callback失敗 = False
    try:
        for callback in 工廠:
            輸出 = callback()
            值.append(輸出)
            callback = 輸出 = None
        輸出 = 時鐘()
        值.append(輸出)
        輸出 = None
    except (KeyboardInterrupt, SystemExit, GeneratorExit):
        值.clear()
        del 工廠, 時鐘, 值, callback, 輸出, 結果, callback失敗
        raise
    except BaseException:
        值.clear()
        callback失敗 = True
    if not callback失敗 and len(值) == 5:
        for 輸出 in 值[:4]:
            if not _是識別(輸出):
                callback失敗 = True
                break
            輸出 = None
    else:
        callback失敗 = True
    if callback失敗 or len(set(值[:4])) != 4 or not _是有限非負(值[4]):
        值.clear()
        del 工廠, 時鐘, 值, callback, 輸出, 結果, callback失敗
        _拒絕發布()
    結果 = tuple(值)
    值.clear()
    del 工廠, 時鐘, 值, callback, 輸出, callback失敗
    return 結果


def _驗證既有資料庫路徑(原路徑: Any) -> tuple[Path, tuple[int, int]]:
    """拒絕 missing、symlink、非 regular 與空檔，再釘住解析後 inode。"""
    路徑 = 前 = 解析 = 後 = 結果 = None
    失敗 = False
    try:
        路徑 = Path(原路徑).expanduser()
        前 = 路徑.lstat()
        if stat.S_ISLNK(前.st_mode) or not stat.S_ISREG(前.st_mode) or 前.st_size <= 0:
            raise ValueError
        解析 = 路徑.resolve(strict=True)
        後 = 解析.stat()
        if (前.st_dev, 前.st_ino) != (後.st_dev, 後.st_ino):
            raise ValueError
        結果 = (解析, (後.st_dev, 後.st_ino))
    except (KeyboardInterrupt, SystemExit, GeneratorExit):
        del 原路徑, 路徑, 前, 解析, 後, 結果, 失敗
        raise
    except BaseException:
        失敗 = True
    if 失敗 or 結果 is None:
        del 原路徑, 路徑, 前, 解析, 後, 結果, 失敗
        _拒絕發布()
    del 原路徑, 路徑, 前, 解析, 後, 失敗
    return 結果


def _驗證已開啟資料庫路徑(連線: sqlite3.Connection, 路徑: Path, 身分: tuple[int, int]) -> None:
    """連線建立後驗證資料庫 inode；任何失敗均在傳播前恰關閉一次。

    參數：連線是剛開啟且尚未移交交易的 SQLite 連線；路徑與身分是開啟前釘選值。
    回傳：連線所見主檔仍符合釘選身分時回傳 ``None``。
    例外：查詢、解析或身分不符時拋出 ``端點發布錯誤``；控制流程例外原樣傳出。
    副作用：查詢 SQLite 主檔路徑；任何失敗都會關閉目前連線。
    """
    路徑狀態 = 資料庫列 = 列 = 主檔 = 主檔狀態 = None
    失敗 = False
    關閉控制: list[BaseException] = []
    try:
        路徑狀態 = 路徑.lstat()
        資料庫列 = 連線.execute("PRAGMA database_list").fetchall()
        for 列 in 資料庫列:
            if 列[1] == "main":
                主檔 = 列[2]
                break
            列 = None
        if 主檔 is None:
            raise ValueError
        主檔狀態 = os.stat(主檔)
        失敗 = (
            stat.S_ISLNK(路徑狀態.st_mode) or not stat.S_ISREG(路徑狀態.st_mode)
            or (路徑狀態.st_dev, 路徑狀態.st_ino) != 身分
            or (主檔狀態.st_dev, 主檔狀態.st_ino) != 身分
        )
    except (KeyboardInterrupt, SystemExit, GeneratorExit) as 控制:
        _清除例外框架(控制)
        _清除例外鏈(控制)
        關閉控制 = _安全關閉(連線)
        關閉控制.clear()
        _清除例外鏈(控制)
        del 控制
        del 連線, 路徑, 身分, 路徑狀態, 資料庫列, 列, 主檔, 主檔狀態, 失敗, 關閉控制
        raise
    except BaseException:
        失敗 = True
    if 失敗:
        關閉控制 = _安全關閉(連線)
        del 連線, 路徑, 身分, 路徑狀態, 資料庫列, 列, 主檔, 主檔狀態, 失敗
        if 關閉控制:
            _拋出清理控制(關閉控制.pop())
        del 關閉控制
        _拒絕發布()
    del 連線, 路徑, 身分, 路徑狀態, 資料庫列, 列, 主檔, 主檔狀態, 失敗, 關閉控制


def _驗證並寫入(
    連線: sqlite3.Connection, owner: str, draft: 規劃草稿, snapshot: 發布版本快照,
    credential: 已準備初始憑證, confirmation: 發布值確認,
    ids: tuple[Any, ...], created_at: float, 套件收據: 套件發布收據 | None = None,
    稽核識別碼: str | None = None, 請求識別碼: str | None = None,
    寫入前權威確認: Callable[[], object] | None = None,
) -> None:
    """鎖住資料庫結構後驗證指紋，並以明確狀態機完成單一交易。

        參數：連線由本函式完成交易及關閉；擁有者、草稿、快照、憑證、確認、
        識別碼與建立時間共同描述待發布的固定第一版圖形；可空
        ``寫入前權威確認`` 是無參數 callback。
        回傳：成功提交並關閉連線後回傳 ``None``。
        例外：callback、交易或結構失敗固定映射為 ``端點發布錯誤``；回滾或關閉
        時的控制流程例外依清理契約原樣傳出，原始 callback traceback locals 會
        被清除。
        副作用：設定外鍵及驗證函式、開始 ``BEGIN IMMEDIATE``，先呼叫 callback
        再寫入端點圖形、提交並關閉連線；失敗時回滾且關閉連線。
    """
    已開始 = False
    已提交 = False
    交易失敗 = False
    ledger = rows = raw = endpoint_id = version_id = credential_id = account_id = None
    收據儲存庫 = 中繼資料 = None
    回滾控制: list[BaseException] = []
    關閉控制: list[BaseException] = []
    try:
        連線.execute("PRAGMA foreign_keys = ON")
        if 連線.execute("PRAGMA foreign_keys").fetchone() != (1,):
            raise sqlite3.DatabaseError
        連線.create_function(
            "published_ip_allowlist_valid", 1, _allowlist_json有效, deterministic=True,
        )
        連線.execute("BEGIN IMMEDIATE")
        已開始 = True
        驗證資料庫結構(連線)
        if 寫入前權威確認 is not None:
            寫入前權威確認()
        endpoint_id, version_id, credential_id, account_id = ids[:4]
        if 套件收據 is not None:
            if 稽核識別碼 != ids[5]:
                raise sqlite3.DatabaseError
            套件收據 = _驗證預配關係(ids, snapshot, 套件收據, 請求識別碼)
        elif 稽核識別碼 is not None or 請求識別碼 is not None:
            raise sqlite3.DatabaseError
        _執行一列(連線, "INSERT INTO service_accounts(id,created_at,disabled_at) VALUES(?,?,NULL)", (account_id, created_at))
        _執行一列(
            連線,
            "INSERT INTO published_endpoints(id,owner_user_id,service_account_id,slug,status,current_version_id,created_at,updated_at,rate_limit_requests,rate_limit_window_seconds) VALUES(?,?,?,?,?,NULL,?,?,?,?)",
            (endpoint_id, owner, account_id, confirmation.slug, "active", created_at, created_at, confirmation.endpoint_limit, confirmation.window_seconds),
        )
        _執行一列(
            連線,
            "INSERT INTO published_endpoint_versions(id,endpoint_id,version_number,original_requirement_text,system_prompt,allowed_skills_json,allowed_tools_json,tool_schema_snapshot_json,tool_runtime_revision,model_config_snapshot_json,retry_policy_json,skill_bundle_manifest_json,input_schema_json,response_schema_json,schema_changed,created_by_user_id,created_at) VALUES(?,?,1,?,?,?,?,?,?,?,?,?,?,?,0,?,?)",
            (version_id, endpoint_id, snapshot.original_requirement_text, snapshot.system_prompt,
             _正規JSON(snapshot.allowed_skills), _正規JSON(snapshot.allowed_tools), _正規JSON(snapshot.tool_schema_snapshot),
             snapshot.tool_runtime_revision, _正規JSON(snapshot.model_config_snapshot), _正規JSON(snapshot.retry_policy),
             _正規JSON(snapshot.skill_bundle_manifest), None if snapshot.input_schema is None else _正規JSON(snapshot.input_schema),
             _正規JSON(snapshot.response_schema), snapshot.created_by_user_id, created_at),
        )
        _執行一列(連線, "UPDATE published_endpoints SET current_version_id=? WHERE id=? AND current_version_id IS NULL", (version_id, endpoint_id))
        _執行一列(
            連線,
            "INSERT INTO endpoint_credentials(id,endpoint_id,name,purpose,key_version,key_nonce,key_ciphertext,key_hash,key_prefix,key_last4,expires_at,last_used_at,created_at,updated_at,revoked_at,ip_allowlist_json,rate_limit_requests,created_by_user_id,revision) VALUES(?,?,?,?,?,?,?,?,?,?,?,NULL,?,?,NULL,?,?,?,0)",
            (credential_id, endpoint_id, credential.name, credential.purpose, credential.key_version,
             credential.key_nonce, credential.key_ciphertext, credential.key_hash, credential.key_prefix,
             credential.key_last4, credential.expires_at, created_at, created_at,
             _正規JSON(credential.ip_allowlist), credential.rate_limit_requests, credential.created_by_user_id),
        )
        if 套件收據 is not None:
            收據儲存庫 = object.__new__(套件收據儲存庫)
            收據儲存庫.連線 = 連線
            收據儲存庫.新增(版本識別碼=version_id, 收據=套件收據, 發布時間=created_at)
            中繼資料 = _正規JSON({
                "version_id": version_id, "version_number": 1,
                "bundle_id": ids[4], "bundle_hash": 套件收據.套件雜湊,
                "credential_id": credential_id, "service_account_id": account_id,
            })
            _執行一列(
                連線,
                "INSERT INTO audit_events(id,event_id,occurred_at,action,outcome,actor_type,actor_id,resource_type,resource_id,request_id,endpoint_id,invocation_id,metadata_json,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,NULL,?,?)",
                (稽核識別碼, 稽核識別碼, created_at, "endpoint_published", "success", "user", owner,
                 "published_endpoint", endpoint_id, 請求識別碼, endpoint_id, 中繼資料, created_at),
            )
        連線.execute("COMMIT")
        已開始 = False
        已提交 = True
    except (KeyboardInterrupt, SystemExit, GeneratorExit) as 控制:
        _清除例外框架(控制)
        if 已開始:
            回滾控制 = _安全回滾(連線)
        關閉控制 = _安全關閉(連線)
        回滾控制.clear()
        關閉控制.clear()
        _清除例外鏈(控制)
        del 控制
        del 連線, owner, draft, snapshot, credential, confirmation, ids, created_at, 套件收據, 稽核識別碼, 請求識別碼, 寫入前權威確認, 已開始, 已提交, 交易失敗, ledger, rows, raw, endpoint_id, version_id, credential_id, account_id, 收據儲存庫, 中繼資料, 回滾控制, 關閉控制
        raise
    except BaseException as 錯誤:
        _清除例外框架(錯誤)
        if 已開始:
            回滾控制 = _安全回滾(連線)
        關閉控制 = _安全關閉(連線)
        交易失敗 = True
    if 交易失敗:
        del 連線, owner, draft, snapshot, credential, confirmation, ids, created_at, 套件收據, 稽核識別碼, 請求識別碼, 寫入前權威確認, 已開始, 已提交, 交易失敗, ledger, rows, raw, endpoint_id, version_id, credential_id, account_id, 收據儲存庫, 中繼資料
        if 回滾控制:
            關閉控制.clear()
            _拋出清理控制(回滾控制.pop())
        if 關閉控制:
            _拋出清理控制(關閉控制.pop())
        del 回滾控制, 關閉控制
        _拒絕發布()
    關閉控制 = _安全關閉(連線)
    del 連線, owner, draft, snapshot, credential, confirmation, ids, created_at, 套件收據, 稽核識別碼, 請求識別碼, 寫入前權威確認, 已開始, 已提交, 交易失敗, ledger, rows, raw, endpoint_id, version_id, credential_id, account_id, 收據儲存庫, 中繼資料, 回滾控制
    if 關閉控制:
        _拋出清理控制(關閉控制.pop())
    del 關閉控制


def _執行一列(連線: sqlite3.Connection, sql: str, parameters: tuple[Any, ...]) -> None:
    """執行必須正好影響一列的交易 statement。"""
    try:
        if 連線.execute(sql, parameters).rowcount != 1:
            raise sqlite3.DatabaseError
    except (KeyboardInterrupt, SystemExit, GeneratorExit):
        del 連線, sql, parameters
        raise


def _正規JSON(值: Any) -> str:
    """只 canonicalize preflight 已重建的 module-owned exact JSON。"""
    try:
        return json.dumps(值, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    except (KeyboardInterrupt, SystemExit, GeneratorExit):
        del 值
        raise


def _安全回滾(連線: sqlite3.Connection) -> list[BaseException]:
    """忽略 ordinary rollback 錯誤，回傳已去鏈結的控制流。"""
    結果: list[BaseException] = []
    try:
        連線.execute("ROLLBACK")
    except (KeyboardInterrupt, SystemExit, GeneratorExit) as 控制:
        _清除例外鏈(控制)
        控制.__traceback__ = None
        結果.append(控制)
    except BaseException:
        pass
    del 連線
    return 結果


def _安全關閉(連線: sqlite3.Connection) -> list[BaseException]:
    """忽略 ordinary close 錯誤，回傳已去鏈結的控制流。"""
    結果: list[BaseException] = []
    try:
        連線.close()
    except (KeyboardInterrupt, SystemExit, GeneratorExit) as 控制:
        _清除例外鏈(控制)
        控制.__traceback__ = None
        結果.append(控制)
    except BaseException:
        pass
    del 連線
    return 結果


def _清除例外鏈(控制: BaseException) -> None:
    """原地移除控制流既有與隱式 exception graph。"""
    控制.__cause__ = None
    控制.__context__ = None
    控制.__suppress_context__ = True


def _清除例外框架(錯誤: BaseException) -> None:
    """清除 callback 失敗留下的 traceback locals 與例外鏈。

    參數：``錯誤`` 是權威確認 callback 或交易步驟傳出的原始例外。
    回傳：無。例外：清理本身的任何失敗皆抑制；原始例外仍由呼叫端處理。
    副作用：清空可清除 traceback frame 的區域變數，並移除 cause、context 與
    suppress 狀態；不改變例外類型、identity 或 ``args``。
    """
    try:
        _清除例外鏈(錯誤)
    except BaseException:
        pass
    try:
        traceback.clear_frames(錯誤.__traceback__)
    except BaseException:
        pass


def _拋出清理控制(控制: BaseException) -> NoReturn:
    """以 exact identity 重拋，且 helper frame 不保留控制流別名。"""
    _清除例外鏈(控制)
    try:
        raise 控制.with_traceback(None)
    except (KeyboardInterrupt, SystemExit, GeneratorExit):
        del 控制
        raise


def _重建版本快照(來源: 發布版本快照, *, 建構中: bool = False) -> 發布版本快照:
    """讀取每個 fixed slot 並重建快照；不信任 frozen instance identity。"""
    值: dict[str, Any] = {}
    名稱 = 項目 = 結果 = None
    失敗 = False
    try:
        if type(來源) is not 發布版本快照:
            失敗 = True
        else:
            for 名稱 in 發布版本快照.__dataclass_fields__:
                值[名稱] = object.__getattribute__(來源, 名稱)
                名稱 = None
            if not _是文字(值["original_requirement_text"]) or not _是文字(值["system_prompt"]):
                失敗 = True
            elif not _是識別(值["tool_runtime_revision"]) or not _是識別(值["created_by_user_id"]):
                失敗 = True
            else:
                for 名稱 in ("allowed_skills", "allowed_tools", "tool_schema_snapshot", "retry_policy", "skill_bundle_manifest", "response_schema"):
                    值[名稱] = _建立JSON副本(值[名稱])
                    名稱 = None
                值["model_config_snapshot"] = _建立JSON副本(值["model_config_snapshot"], 拒絕秘密鍵=True)
                if 值["input_schema"] is not None:
                    值["input_schema"] = _建立JSON副本(值["input_schema"])
                if not _是字串陣列(值["allowed_skills"]) or not _是字串陣列(值["allowed_tools"]):
                    失敗 = True
                else:
                    for 名稱 in ("tool_schema_snapshot", "model_config_snapshot", "retry_policy", "skill_bundle_manifest", "response_schema"):
                        if type(值[名稱]) is not dict:
                            失敗 = True
                            break
                        名稱 = None
                if 值["input_schema"] is not None and type(值["input_schema"]) is not dict:
                    失敗 = True
        if not 失敗:
            if 建構中:
                for 名稱, 項目 in dict.items(值):
                    object.__setattr__(來源, 名稱, 項目)
                    名稱 = 項目 = None
                結果 = 來源
            else:
                結果 = 發布版本快照(**值)
    except (KeyboardInterrupt, SystemExit, GeneratorExit):
        值.clear()
        del 來源, 建構中, 值, 名稱, 項目, 結果, 失敗
        raise
    except BaseException:
        失敗 = True
    值.clear()
    if 失敗 or 結果 is None:
        del 來源, 建構中, 值, 名稱, 項目, 結果, 失敗
        _拒絕輸入()
    del 來源, 建構中, 值, 名稱, 項目, 失敗
    return 結果


def _重建初始憑證(來源: 已準備初始憑證, *, 建構中: bool = False) -> 已準備初始憑證:
    """重讀 exact slots 並複製敏感 bytes 與 allowlist。"""
    值: dict[str, Any] = {}
    名稱 = 項目 = 結果 = None
    失敗 = False
    try:
        if type(來源) is not 已準備初始憑證:
            失敗 = True
        else:
            for 名稱 in 已準備初始憑證.__dataclass_fields__:
                值[名稱] = object.__getattribute__(來源, 名稱)
                名稱 = None
            if not _是有限非負(值["expires_at"]):
                失敗 = True
            elif not _是正整數(值["key_version"]):
                失敗 = True
            elif type(值["key_nonce"]) is not bytes or len(值["key_nonce"]) != 12:
                失敗 = True
            elif type(值["key_ciphertext"]) is not bytes or len(值["key_ciphertext"]) != 62:
                失敗 = True
            elif type(值["key_hash"]) is not str or len(值["key_hash"]) != 64 or any(字元 not in "0123456789abcdef" for 字元 in 值["key_hash"]):
                失敗 = True
            elif not _是短文字(值["name"], 120) or not _是短文字(值["purpose"], 1000):
                失敗 = True
            elif not _是識別(值["created_by_user_id"]):
                失敗 = True
            elif not _是短文字(值["key_prefix"], 32) or type(值["key_last4"]) is not str or len(值["key_last4"]) != 4:
                失敗 = True
            elif not _是正整數(值["rate_limit_requests"]) or 值["rate_limit_requests"] > 10_000:
                失敗 = True
            else:
                值["ip_allowlist"] = _建立JSON副本(值["ip_allowlist"])
                if type(值["ip_allowlist"]) is not list:
                    失敗 = True
                值["key_nonce"] = bytes(值["key_nonce"])
                值["key_ciphertext"] = bytes(值["key_ciphertext"])
        if not 失敗:
            if 建構中:
                for 名稱, 項目 in dict.items(值):
                    object.__setattr__(來源, 名稱, 項目)
                    名稱 = 項目 = None
                結果 = 來源
            else:
                結果 = 已準備初始憑證(**值)
    except (KeyboardInterrupt, SystemExit, GeneratorExit):
        值.clear()
        del 來源, 建構中, 值, 名稱, 項目, 結果, 失敗
        raise
    except BaseException:
        失敗 = True
    值.clear()
    if 失敗 or 結果 is None:
        del 來源, 建構中, 值, 名稱, 項目, 結果, 失敗
        _拒絕輸入()
    del 來源, 建構中, 值, 名稱, 項目, 失敗
    return 結果


def _建立JSON副本(來源: Any, *, 拒絕秘密鍵: bool = False) -> Any:
    """以 exact built-ins 單次走訪建立 bounded canonical JSON tree。"""
    計數 = [0]
    描述: list[tuple[Any, tuple[Any, ...]]] = []
    結果 = 容器 = 原項目 = 目前項目 = 原值 = 目前值 = 編碼 = None
    索引 = 0
    try:
        結果 = _複製JSON節點(來源, set(), 0, 計數, 描述, 拒絕秘密鍵)
        for 容器, 原項目 in 描述:
            目前項目 = tuple(list.__iter__(容器)) if type(容器) is list else tuple(dict.items(容器))
            if len(目前項目) != len(原項目):
                raise ValueError
            for 索引 in range(len(原項目)):
                原值 = 原項目[索引]
                目前值 = 目前項目[索引]
                if type(容器) is list:
                    if 目前值 is not 原值:
                        raise ValueError
                elif 目前值[0] is not 原值[0] or 目前值[1] is not 原值[1]:
                    raise ValueError
                原值 = 目前值 = None
            容器 = 原項目 = 目前項目 = None
        編碼 = json.dumps(結果, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
        if len(編碼) > _JSON_UTF8上限:
            raise ValueError
    except BaseException:
        描述.clear()
        計數.clear()
        if type(結果) is list or type(結果) is dict:
            結果.clear()
        del 來源, 拒絕秘密鍵, 計數, 描述, 結果, 容器, 原項目, 目前項目, 原值, 目前值, 編碼, 索引
        raise
    描述.clear()
    計數.clear()
    del 來源, 拒絕秘密鍵, 計數, 描述, 容器, 原項目, 目前項目, 原值, 目前值, 編碼, 索引
    return 結果


def _複製JSON節點(
    來源: Any, 路徑: set[int], 深度: int, 計數: list[int],
    描述: list[tuple[Any, tuple[Any, ...]]], 拒絕秘密鍵: bool,
) -> Any:
    """遞迴複製 exact JSON；每一個遞迴 traceback frame 都自行清除。"""
    容器識別 = 原項目 = 項目 = 鍵 = 已複製 = 結果 = None
    結果串列: list[Any] = []
    結果物件: dict[str, Any] = {}
    已加入路徑 = False
    try:
        計數[0] += 1
        if 計數[0] > _最多節點 or 深度 > _最大深度:
            raise ValueError
        if 來源 is None or type(來源) is bool or type(來源) is int:
            結果 = 來源
        elif type(來源) is float:
            if not math.isfinite(來源):
                raise ValueError
            結果 = 來源
        elif type(來源) is str:
            if len(來源.encode("utf-8")) > _字串UTF8上限:
                raise ValueError
            結果 = 來源
        else:
            if type(來源) not in (list, dict):
                raise ValueError
            容器識別 = id(來源)
            if 容器識別 in 路徑:
                raise ValueError
            路徑.add(容器識別)
            已加入路徑 = True
            if type(來源) is list:
                原項目 = tuple(list.__iter__(來源))
                描述.append((來源, 原項目))
                for 項目 in 原項目:
                    已複製 = _複製JSON節點(項目, 路徑, 深度 + 1, 計數, 描述, 拒絕秘密鍵)
                    結果串列.append(已複製)
                    項目 = 已複製 = None
                結果 = 結果串列
            else:
                原項目 = tuple(dict.items(來源))
                描述.append((來源, 原項目))
                for 鍵, 項目 in 原項目:
                    if type(鍵) is not str or (拒絕秘密鍵 and _禁止秘密鍵.search(鍵)):
                        raise ValueError
                    已複製 = _複製JSON節點(項目, 路徑, 深度 + 1, 計數, 描述, 拒絕秘密鍵)
                    結果物件[鍵] = 已複製
                    鍵 = 項目 = 已複製 = None
                結果 = 結果物件
            路徑.remove(容器識別)
            已加入路徑 = False
    except BaseException:
        if 已加入路徑 and type(容器識別) is int:
            路徑.discard(容器識別)
        描述.clear()
        計數.clear()
        結果串列.clear()
        結果物件.clear()
        if type(結果) is list or type(結果) is dict:
            結果.clear()
        del 來源, 路徑, 深度, 計數, 描述, 拒絕秘密鍵, 容器識別, 原項目, 項目, 鍵, 已複製, 結果, 結果串列, 結果物件, 已加入路徑
        raise
    結果串列 = []
    結果物件 = {}
    del 來源, 路徑, 深度, 計數, 描述, 拒絕秘密鍵, 容器識別, 原項目, 項目, 鍵, 已複製, 結果串列, 結果物件, 已加入路徑
    return 結果


def _是字串陣列(值: Any) -> bool:
    """確認 exact list 只含唯一、bounded exact strings。"""
    if type(值) is not list:
        return False
    已見: set[str] = set()
    for 項目 in 值:
        if not _是識別(項目) or 項目 in 已見:
            return False
        已見.add(項目)
    return True


def _是文字(值: Any) -> bool:
    """確認非空 bounded exact UTF-8 string。"""
    return _是短文字(值, _字串UTF8上限)


def _是短文字(值: Any, 上限: int) -> bool:
    """確認 exact string 非空、無前後空白且字元長度 bounded。"""
    return type(值) is str and 值.strip() == 值 and bool(值) and len(值) <= 上限


def _是識別(值: Any) -> bool:
    """確認 bounded canonical identifier。"""
    return type(值) is str and len(值) <= _識別上限 and _識別格式.fullmatch(值) is not None


def _是有限非負(值: Any) -> bool:
    """確認 exact int/float 可安全轉成有限非負 REAL。"""
    if type(值) not in (int, float):
        return False
    try:
        return math.isfinite(值) and 值 >= 0
    except (OverflowError, ValueError):
        return False


def _是正整數(值: Any) -> bool:
    """確認 SQLite 可接受的 bounded 正整數。"""
    return type(值) is int and 0 < 值 <= 2**63 - 1


def _拒絕輸入() -> NoReturn:
    """清除呼叫 frame 後建立固定且不鏈結的輸入錯誤。"""
    raise 端點發布輸入錯誤(_輸入錯誤訊息) from None


def _拒絕發布() -> NoReturn:
    """建立固定、fresh 且不鏈結底層 SQLite/callback 錯誤的發布錯誤。"""
    raise 端點發布錯誤(_發布錯誤訊息) from None
