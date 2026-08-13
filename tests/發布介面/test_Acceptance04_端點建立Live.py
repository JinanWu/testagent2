"""Acceptance #4 EP-1～EP-3：正式草稿與端點建立契約及 Live E2E。

本模組只從 ``建立CP4ASGI應用程式`` 建立正式應用，不建立手工 FastAPI
應用或替代路由。測試固定 Draft／Create 路徑、方法、OpenAPI 本文與回應、
正式 Session／單次 CSRF 相依身分，以及不得由客戶端聲稱的內部權威欄位。
EP-3 另以真 Login、Owner Authority 與公開 canonical route 驗證發布圖形及秘密邊界。
"""

from __future__ import annotations

import json
import hashlib
import sqlite3
import time
import traceback
from pathlib import Path
from typing import Any

import pytest
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

import 繁中代理.發布介面.生產Published執行 as 生產Published執行模組
from 繁中代理.使用者 import 使用者庫
from 繁中代理.工具 import 工具定義
from 繁中代理.發布介面.asgi import 建立CP4ASGI應用程式
from 繁中代理.發布介面.執行期.工具發布庫 import 工具發布描述, 工具發布註冊
from 繁中代理.發布介面.執行期.模型契約 import 模型回應快照
from 繁中代理.發布介面.憑證.加密 import AESGCM憑證封套
from 繁中代理.發布介面.技能套件.發布器 import 套件發布收據
from 繁中代理.發布介面.生產Published執行 import Published生產設定, 生產Controller建構器
from 繁中代理.發布介面.生產Published管理 import Planner生產設定, 延遲發布管理服務
from 繁中代理.發布介面.生產Published管理 import 草稿規劃服務不可用
from 繁中代理.發布介面.生產組裝 import 建立生產應用程式
from 繁中代理.發布介面.規劃.規劃器供應商 import 決定性假規劃器
from 繁中代理.發布介面.路由.規劃發布 import 發布確認
from 繁中代理.發布介面.路由.網頁認證 import 是模組CSRF相依項
from 繁中代理.發布介面.設定 import 生產設定
from 繁中代理.發布介面.設定 import 網頁CSRFHeader名稱, 網頁CSRFCookie名稱, 網頁工作階段Cookie名稱


草稿路徑 = "/api/published-endpoints/draft"
端點建立路徑 = "/api/published-endpoints"
禁止客戶端聲稱欄位 = (
    "owner_id",
    "service_account_id",
    "role",
    "selected_tools",
    "system_prompt",
)
草稿回應頂層鍵 = {"draft_id", "expires_at", "preview"}
草稿預覽鍵 = {
    "endpoint_name", "suggested_slug", "behavior_summary", "selected_skills",
    "recommended_tools", "tool_capabilities", "system_prompt", "input_schema",
    "response_schema", "human_docs", "rate_limit", "warnings",
}
發布副作用資料表 = {
    "published_endpoints", "published_endpoint_versions", "endpoint_credentials",
    "published_skill_bundles", "service_accounts", "audit_events",
    "published_draft_consumptions", "published_endpoint_version_metadata",
}


def _安裝固定工具(工具發布庫物件) -> None:
    """安裝 Planner Owner Resolver 所需的 exact deterministic tool release。

    參數：
        工具發布庫物件: 正式 startup 建立且由 Planner／Invoke 共用的工具發布庫。
    回傳值：
        無。
    例外：
        無預期例外。
    重要副作用：
        登錄一次 ``acceptance-release``；測試使用者可不啟用其中工具。
    """
    工具發布庫物件.登錄發布(工具發布描述(
        "acceptance-release",
        (工具發布註冊(
            "revision-1",
            工具定義(
                "acceptance-tool", "Acceptance deterministic tool",
                {"type": "object", "properties": {}, "additionalProperties": False},
                lambda _參數: {"ok": True},
            ),
        ),),
    ))


def _建立假模型表() -> dict[str, object]:
    """建立足以通過正式 startup 契約的隔離假模型表。

    參數：
        無。
    回傳值：
        只含 ``fake`` provider 的新字典。
    例外：
        無預期例外。
    重要副作用：
        無；每次呼叫都回傳新的記憶體物件。
    """
    return {"fake": object()}


def _建立憑證封套() -> AESGCM憑證封套:
    """由測試內 explicit key material 建立 exact AES-GCM envelope。

    參數：無。
    回傳值：active version 為一的 ``AESGCM憑證封套``。
    例外：固定 keyring 若違反密碼學契約則原樣傳出。
    重要副作用：只配置記憶體 keyring；不讀環境、檔案系統或資料庫。
    """
    return AESGCM憑證封套({1: b"A" * 32}, 1)


class _記錄假模型:
    """記錄 restart invoke 的釘選模型參數並回傳符合 structured schema 的結果。"""

    def __init__(self) -> None:
        """建立隔離呼叫紀錄；無外部副作用。"""
        self.呼叫: list[dict[str, Any]] = []

    def 產生發布回應(self, **參數):
        """保存本次 detached 參數並回傳 deterministic JSON 模型結果。"""
        self.呼叫.append(json.loads(json.dumps(參數)))
        return 模型回應快照(
            text='{"result":"restart-ok"}', finish_reason="stop",
            usage={"total_tokens": 1}, tool_calls=[],
        )


def _建立正式應用程式(
    暫存目錄: Path, *, 封套工廠=_建立憑證封套,
    草稿存續秒數: float = 3600.0, 模型表工廠=_建立假模型表,
    工具安裝器=_安裝固定工具,
):
    """建立不讀隱含環境且可由正式 lifespan 啟動的 CP4 應用。

    參數：
        暫存目錄: pytest 提供的隔離目錄，用來配置 Web DB、Published DB 與 bundle root。
        封套工廠: 只保存至 startup 才呼叫的 explicit credential envelope factory。
        草稿存續秒數: Server Draft 的存續時間，用於 Live expiry 邊界。
        模型表工廠: startup 建立 Published Runtime provider registry 的 explicit factory。
        工具安裝器: startup 建立 exact tool release registry 的 explicit installer。
    回傳值：
        ``建立CP4ASGI應用程式`` 回傳的正式 FastAPI 應用。
    例外：
        正式設定或應用組裝違反既有契約時，原樣傳出對應例外。
    重要副作用：
        只建立 bundle 目錄；應用建構本身不得建立資料庫或呼叫外部注入。
    """
    技能套件根目錄 = 暫存目錄 / "bundles"
    技能套件根目錄.mkdir(exist_ok=True)
    網頁設定 = 生產設定(
        暫存目錄 / "web.sqlite3",
        ("http://localhost:5173",),
        "fake",
        "fake",
        Cookie安全=False,
        工作階段有效秒數=60,
    )
    發布設定 = Published生產設定(
        暫存目錄 / "published.sqlite3",
        技能套件根目錄,
        工具安裝器,
        模型表工廠,
        Planner設定=Planner生產設定(
            "acceptance-release", lambda 路徑: 使用者庫(路徑),
            lambda: 決定性假規劃器(), 草稿存續秒數,
        ),
        憑證封套工廠=封套工廠,
    )
    return 建立CP4ASGI應用程式(網頁設定, 發布設定)


def _建立直接Published組裝(暫存目錄: Path):
    """建立可精確注入 startup／shutdown lifecycle failure 的正式底層組裝參數。

    參數：``暫存目錄`` 提供隔離 Web／Published DB 與 bundle root。
    回傳值：依序回傳 Web 設定、Published 設定及 Invocation／Draft／Create 三個 proxy。
    例外：正式設定驗證錯誤原樣傳出。
    重要副作用：只建立 bundle 目錄與三個空 proxy；尚不建立資料庫或安裝 authority。
    """
    套件根 = 暫存目錄 / "bundles"
    套件根.mkdir()
    Web設定 = 生產設定(
        暫存目錄 / "web.sqlite3", ("http://localhost:5173",), "fake", "fake",
        Cookie安全=False, 工作階段有效秒數=60,
    )
    Published設定 = Published生產設定(
        暫存目錄 / "published.sqlite3", 套件根, _安裝固定工具, _建立假模型表,
        Planner設定=Planner生產設定(
            "acceptance-release", lambda 路徑: 使用者庫(路徑),
            lambda: 決定性假規劃器(), 3600.0,
        ),
        憑證封套工廠=_建立憑證封套,
    )
    return (
        Web設定,
        Published設定,
        生產Published執行模組.延遲外部呼叫編排器(),
        生產Controller建構器(Published設定)._Published.取得草稿規劃代理(),
        延遲發布管理服務(),
    )


def _取得唯一正式路由(應用程式, 路徑: str) -> APIRoute:
    """從正式應用 inventory 取得指定路徑唯一一條 API 路由。

    參數：
        應用程式: 正式 CP4 FastAPI 應用。
        路徑: 必須唯一存在的 exact HTTP route path。
    回傳值：
        指定路徑唯一的 ``APIRoute``。
    例外：
        路徑缺失或重複時以測試 assertion 明確回報目前 route inventory。
    重要副作用：
        無；只讀取應用路由表。
    """
    符合路由清單 = [
        路由
        for 路由 in 應用程式.routes
        if isinstance(路由, APIRoute) and 路由.path == 路徑
    ]
    assert len(符合路由清單) == 1, (
        f"正式應用必須有且只有一條 {路徑}；目前數量={len(符合路由清單)}；"
        f"inventory={[路由.path for 路由 in 應用程式.routes if isinstance(路由, APIRoute)]}"
    )
    return 符合路由清單[0]


def _解析OpenAPI綱要(規格: dict[str, Any], 綱要: dict[str, Any]) -> dict[str, Any]:
    """解析 OpenAPI 元件參照並回傳可直接檢查的綱要。

    參數：
        規格: 正式應用產生的完整 OpenAPI 文件。
        綱要: 內嵌綱要或只含本地 ``$ref`` 的綱要。
    回傳值：
        內嵌綱要本身，或 ``components.schemas`` 中被參照的綱要。
    例外：
        缺少元件或參照格式錯誤時傳出 ``KeyError``，使契約漂移明確失敗。
    重要副作用：
        無；只讀取輸入字典。
    """
    if "$ref" not in 綱要:
        return 綱要
    元件名稱 = 綱要["$ref"].rsplit("/", 1)[1]
    return 規格["components"]["schemas"][元件名稱]


def _取得請求綱要(規格: dict[str, Any], 路徑: str) -> dict[str, Any]:
    """取得指定正式 POST 操作唯一的 JSON request schema。

    參數：
        規格: 正式應用產生的完整 OpenAPI 文件。
        路徑: Draft 或 Endpoint Create exact path。
    回傳值：
        已解析元件參照的 ``application/json`` request schema。
    例外：
        路徑、POST、必要本文或 JSON media type 缺失時傳出 ``KeyError`` 或 assertion。
    重要副作用：
        無；只讀取 OpenAPI 文件。
    """
    本文契約 = 規格["paths"][路徑]["post"]["requestBody"]
    assert 本文契約["required"] is True
    assert set(本文契約["content"]) == {"application/json"}
    return _解析OpenAPI綱要(
        規格,
        本文契約["content"]["application/json"]["schema"],
    )


def _建立Owner技能與使用者(暫存目錄: Path, 帳號: str, 密碼: str) -> str:
    """建立具真技能及 authoritative tool 授權的 Web owner。

    參數：``暫存目錄`` 定位隔離技能與 Web DB；``帳號``、``密碼`` 是登入資料。
    回傳值：canonical owner user id 字串。
    例外：技能寫入、使用者建立或 SQLite 操作失敗時原樣傳出。
    重要副作用：寫入一份 ``demo`` 技能並新增一位啟用該技能及工具的使用者。
    """
    技能根 = 暫存目錄 / "skills"
    技能目錄 = 技能根 / "demo"
    技能目錄.mkdir(parents=True, exist_ok=True)
    (技能目錄 / "SKILL.md").write_text(
        "---\nname: demo\ndescription: acceptance skill\n---\n\n# Demo\n",
        encoding="utf-8",
    )
    儲存庫 = 使用者庫(暫存目錄 / "web.sqlite3")
    try:
        使用者 = 儲存庫.建立使用者(
            帳號, 密碼, roles=["user"], enabled_tools=["acceptance-tool"],
            enabled_skills=["demo"], skill_roots=[str(技能根)],
            allowed_workdirs=[str(暫存目錄)],
        )
        return str(使用者["id"])
    finally:
        儲存庫.連線.close()


def _登入Owner(客戶端: TestClient, 帳號: str, 密碼: str):
    """以真帳密登入並確認 Cookie，回傳 Login response 與首枚 CSRF。

    參數：``客戶端`` 是已啟動 canonical app client；帳號與密碼屬測試使用者。
    回傳值：二元組 ``(login response, csrf token)``。
    例外：登入契約漂移時以 assertion 失敗。
    重要副作用：建立真 session，並在 client cookie jar 寫入 session／CSRF cookies。
    """
    回應 = 客戶端.post("/api/auth/login", json={"username": 帳號, "password": 密碼})
    assert 回應.status_code == 200
    assert 網頁工作階段Cookie名稱 in 客戶端.cookies
    assert 網頁CSRFCookie名稱 in 客戶端.cookies
    return 回應, 回應.json()["csrf_token"]


def _建立Server草稿(客戶端: TestClient, csrf: str):
    """經 canonical HTTP 建立一份 authoritative owner Draft。

    參數：``客戶端`` 持有真 session；``csrf`` 是本次尚未使用的權杖。
    回傳值：原始 HTTP response，供呼叫端驗證 exact DTO 與 successor token。
    例外：傳輸錯誤原樣傳出；HTTP 契約由呼叫端斷言。
    重要副作用：成功時在 canonical in-memory Draft Aggregate 新增一份草稿並輪替 CSRF。
    """
    return 客戶端.post(
        草稿路徑,
        json={
            "original_requirement_text": "建立 Demo API",
            "selected_skills": ["demo"],
            "response_mode": "structured",
        },
        headers={網頁CSRFHeader名稱: csrf},
    )


def _建立Server確認(預覽: dict[str, Any]) -> dict[str, Any]:
    """從 exact server preview 建立 route 允許的五個顯示值確認。

    參數：``預覽`` 是 Draft 201 回傳的十二鍵 server preview。
    回傳值：只含 Prompt、Input／Response Schema、Docs 與 Rate 的 detached dict。
    例外：必要鍵缺失時傳出 ``KeyError``，使 server DTO 漂移明確失敗。
    重要副作用：無；所有巢狀 JSON 值先經 JSON round-trip 脫離。
    """
    return json.loads(json.dumps({
        "system_prompt": 預覽["system_prompt"],
        "input_schema": 預覽["input_schema"],
        "response_schema": 預覽["response_schema"],
        "human_docs": 預覽["human_docs"],
        "rate_limit": 預覽["rate_limit"],
    }))


def _建立Endpoint(客戶端: TestClient, csrf: str, 草稿本文: dict[str, Any], *, 確認=None):
    """以三鍵 exact body 經 canonical HTTP 嘗試建立 Endpoint。

    參數：client 與 CSRF 驗證真 session；草稿本文供應 ID／preview；確認可覆寫負向案例。
    回傳值：原始 Create HTTP response。
    例外：草稿本文缺鍵或傳輸錯誤原樣傳出。
    重要副作用：成功時建立 v1 publication graph；失敗時產品契約要求不產生 publication。
    """
    配置 = _建立Server確認(草稿本文["preview"]) if 確認 is None else 確認
    return 客戶端.post(
        端點建立路徑,
        json={
            "draft_id": 草稿本文["draft_id"],
            "slug": "demo-api",
            "configuration_confirmation": 配置,
        },
        headers={網頁CSRFHeader名稱: csrf},
    )


def _建立並收斂成功秘密(
    客戶端: TestClient, csrf: str, 草稿本文: dict[str, Any], 暫存目錄: Path, caplog,
) -> tuple[dict[str, Any], dict[str, bool], int]:
    """建立 Endpoint 並在離開 helper 前清除所有一次性明文別名。

    參數：canonical client、fresh CSRF、Server Draft、隔離目錄與 log capture。
    回傳值：不含秘密的 public 欄位、秘密邊界布林證據，以及 CSRF replay 狀態碼。
    例外：HTTP／JSON／檔案讀取錯誤原樣傳出；``finally`` 仍清除 helper 內明文別名。
    重要副作用：執行一次成功 Create 與一次已消耗 CSRF replay；不保存或 assert 明文。
    """
    回應 = 本文 = 初始金鑰 = 初始金鑰文字 = 初始金鑰位元 = 重放 = 結果 = None
    try:
        回應 = _建立Endpoint(客戶端, csrf, 草稿本文)
        本文 = 回應.json()
        初始金鑰 = 本文.get("initial_api_key") if type(本文) is dict else None
        初始金鑰有效 = type(初始金鑰) is str and bool(初始金鑰)
        初始金鑰文字 = 初始金鑰 if type(初始金鑰) is str else ""
        初始金鑰位元 = 初始金鑰文字.encode()
        重放 = _建立Endpoint(客戶端, csrf, 草稿本文)
        公開欄位 = {
            "status_code": 回應.status_code,
            "keys": set(本文) if type(本文) is dict else set(),
            "endpoint_id": 本文.get("endpoint_id") if type(本文) is dict else None,
            "version_id": 本文.get("version_id") if type(本文) is dict else None,
            "version_number": 本文.get("version_number") if type(本文) is dict else None,
            "status": 本文.get("status") if type(本文) is dict else None,
        }
        證據 = {
            "initial_api_key_valid": 初始金鑰有效,
            "response_once": 初始金鑰有效 and 回應.text.count(初始金鑰文字) == 1,
            "replay_absent": 初始金鑰有效 and 初始金鑰文字 not in 重放.text,
            "database_absent": 初始金鑰有效 and 初始金鑰位元 not in (
                暫存目錄 / "published.sqlite3"
            ).read_bytes(),
            "bundles_absent": 初始金鑰有效 and all(
                初始金鑰位元 not in 路徑.read_bytes()
                for 路徑 in (暫存目錄 / "bundles").rglob("*") if 路徑.is_file()
            ),
            "stack_absent": 初始金鑰有效 and 初始金鑰文字 not in "".join(traceback.format_stack()),
            "logs_absent": 初始金鑰有效 and all(
                初始金鑰文字 not in 紀錄.getMessage() for 紀錄 in caplog.records
            ),
        }
        結果 = (公開欄位, 證據, 重放.status_code)
    finally:
        初始金鑰 = 初始金鑰文字 = 初始金鑰位元 = 本文 = 回應 = 重放 = None
        del 初始金鑰, 初始金鑰文字, 初始金鑰位元, 本文, 回應, 重放
    return 結果


def _取得Planner聚合(應用程式):
    """取得 canonical startup 唯一 Draft Aggregate 作 readback。

    參數：``應用程式`` 是已啟動的 CP4 canonical app。
    回傳值：Published lifespan resource 持有的 Planner aggregate。
    例外：資源未啟動或 composition 漂移時傳出 attribute/index 錯誤。
    重要副作用：無；只讀 app state。
    """
    return 應用程式.state.發布介面資源[-1].取得Planner資源().取得規劃服務()


def _取得管理服務(應用程式):
    """取得 canonical startup 安裝的 genuine publication coordinator 作觀測。

    參數：``應用程式`` 是已啟動的 CP4 canonical app。
    回傳值：route proxy 委派的真實 management service。
    例外：資源尚未啟動或 wiring 漂移時傳出明確 attribute/index 錯誤。
    重要副作用：無；只讀 app state，不直接呼叫 coordinator。
    """
    return 應用程式.state.發布介面資源[-1].取得發布管理服務()


def _建立完整副作用快照(暫存目錄: Path, 應用程式) -> dict[str, Any]:
    """建立 Draft 語意、全部 Published tables 與完整 Bundle tree 快照。

    參數：``暫存目錄`` 定位 DB／bundle；``應用程式`` 提供 canonical aggregate readback。
    回傳值：可直接 equality 比較、且不保存一次性明文的 detached snapshot。
    例外：資料表缺失、讀檔或 SQLite 失敗時原樣傳出。
    重要副作用：唯讀 DB、Draft 與檔案樹；不修改任何產品狀態。
    """
    草稿們 = _取得Planner聚合(應用程式)._草稿
    草稿快照 = tuple(sorted((
        識別, 草稿.擁有者識別碼, 草稿.原始需求, 草稿._綱要正規JSON,
        草稿.建立時間, 草稿.到期時間, 草稿.狀態, 草稿._世代,
        getattr(草稿.能力摘要, "正規JSON", None),
        None if 草稿.發布確認 is None else (
            草稿.發布確認.草稿識別碼, 草稿.發布確認.草稿世代,
            草稿.發布確認.slug, 草稿.發布確認.response_schema,
            草稿.發布確認.docs, 草稿.發布確認.endpoint_limit,
            草稿.發布確認.credential_limit,
        ),
    ) for 識別, 草稿 in 草稿們.items()))
    套件根 = 暫存目錄 / "bundles"
    快照: dict[str, Any] = {
        "drafts": 草稿快照,
        "bundle_tree": tuple(sorted(
            (str(路徑.relative_to(套件根)), 路徑.is_dir(),
             None if 路徑.is_dir() else 路徑.read_bytes())
            for 路徑 in 套件根.rglob("*")
        )),
        "tables": {},
    }
    with sqlite3.connect(暫存目錄 / "published.sqlite3") as 連線:
        現有表 = sorted(列[0] for 列 in 連線.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        ))
        assert 發布副作用資料表 <= set(現有表)
        for 表 in 現有表:
            安全表 = 表.replace('"', '""')
            快照["tables"][表] = tuple(連線.execute(
                f'SELECT * FROM "{安全表}" ORDER BY rowid'
            ))
    return 快照


def _斷言Draft語意保留(之前: dict[str, Any], 之後: dict[str, Any]) -> None:
    """確認 Create 失敗只允許內部發布確認欄改變，其餘 Draft 語意不漂移。

    參數：``之前``、``之後`` 是同一案例的完整副作用 snapshots。
    回傳值：無；所有語意保留條件以 assertions 表達。
    例外：Draft identity、owner、需求、preview、expiry、generation 或狀態漂移時失敗。
    重要副作用：無；只比較 detached tuples。
    """
    assert tuple(項目[:-1] for 項目 in 之後["drafts"]) == tuple(
        項目[:-1] for 項目 in 之前["drafts"]
    )


def _斷言Owner可讀且Draft語意保留(
    應用程式, 擁有者識別碼: str, 草稿識別碼: str, 基準草稿,
) -> None:
    """經 shared aggregate 正式 read path 逐欄證明 owner Draft 語意保留。

    參數：canonical app、原 owner、原 draft id，以及失敗前由同一路徑讀得的基準草稿。
    回傳值：無；readback 的 identity、owner、需求、綱要、時間、狀態、世代與能力逐欄相等。
    例外：owner 不可讀、草稿過期或任一欄漂移時以產品錯誤或 assertion 失敗。
    重要副作用：唯讀 shared aggregate；不直接呼叫 publication coordinator。
    """
    讀回 = _取得Planner聚合(應用程式).讀取草稿(
        擁有者識別碼, 草稿識別碼, 現在=time.time(),
    )
    assert 讀回.草稿識別碼 == 基準草稿.草稿識別碼 == 草稿識別碼
    assert 讀回.擁有者識別碼 == 基準草稿.擁有者識別碼 == 擁有者識別碼
    assert 讀回.原始需求 == 基準草稿.原始需求
    assert 讀回.綱要 == 基準草稿.綱要
    assert 讀回.建立時間 == 基準草稿.建立時間
    assert 讀回.到期時間 == 基準草稿.到期時間
    assert 讀回.狀態 == 基準草稿.狀態 == "draft"
    assert 讀回._世代 == 基準草稿._世代
    assert 讀回.能力摘要 == 基準草稿.能力摘要


def _安裝PreWrite零進入探針(
    monkeypatch: pytest.MonkeyPatch, 管理服務,
) -> dict[str, int]:
    """在識別、熵、Bundle 與 P04 入口安裝進入即失敗的計數探針。

    參數：pytest monkeypatch 與 canonical genuine coordinator。
    回傳值：四個入口的可斷言 counter mapping。
    例外：任一入口遭錯誤進入時立即拋出 ``AssertionError('[REDACTED]')``。
    重要副作用：暫時替換 coordinator 實例的四個 pre-write dependency seams。
    """
    次數 = {"identifier": 0, "entropy": 0, "bundle": 0, "p04": 0}

    def 不得進入(名稱: str):
        """建立只計數且固定遮罩失敗的入口 sentinel。"""
        def 探針(*_參數, **_關鍵字):
            """記錄意外進入並以固定遮罩訊息中止。"""
            次數[名稱] += 1
            raise AssertionError("[REDACTED]")
        return 探針

    monkeypatch.setattr(管理服務, "_識別碼產生器", 不得進入("identifier"))
    monkeypatch.setattr(管理服務, "_隨機位元組", 不得進入("entropy"))
    monkeypatch.setattr(管理服務._套件發布器, "發布", 不得進入("bundle"))
    monkeypatch.setattr(管理服務._端點發布服務, "發布已準備圖形", 不得進入("p04"))
    return 次數


def _以Fresh登入重試同Draft(
    客戶端: TestClient, 帳號: str, 密碼: str, 草稿本文: dict[str, Any],
):
    """以同 owner 真 Login 取得 fresh CSRF，經 canonical HTTP 重試同一 Draft。

    參數：canonical client、owner 帳密及原 Server Draft response body。
    回傳值：原始 Create HTTP response。
    例外：Login 契約由 ``_登入Owner`` 固定；傳輸錯誤原樣傳出。
    重要副作用：建立 fresh session，並以 server exact 五鍵 confirmation 發布同一 Draft。
    """
    _, fresh_csrf = _登入Owner(客戶端, 帳號, 密碼)
    return _建立Endpoint(客戶端, fresh_csrf, 草稿本文)


def _斷言固定錯誤且不洩漏(回應, 暫存目錄: Path) -> None:
    """驗證 canonical error 不含 Path、Manifest、Ciphertext、Hash、Traceback 或 Internal ID。

    參數：``回應`` 是負向 HTTP response；``暫存目錄`` 提供本案內部路徑 marker。
    回傳值：無。
    例外：任何 marker 出現在 public response 時以 assertion 失敗。
    重要副作用：無；只讀 response text。
    """
    for 標記 in (
        str(暫存目錄), "SKILL.md", "manifest", "ciphertext", "key_hash",
        "Traceback", "service_account", "bundle_id", "credential_id",
    ):
        assert 標記 not in 回應.text


def test_canonical_OpenAPI包含唯一draft與endpoint_create(tmp_path):
    """固定正式 Draft／Create 的唯一 POST、strict schema、public 201 DTO 與 canonical 身分相依。

    參數：
        tmp_path: pytest 提供的隔離目錄，用來建立明確正式設定。
    回傳值：
        無；所有 frozen contract 都以 assertion 表達。
    例外：
        正式路由缺失、重複、方法漂移、schema 漂移或相依身分不一致時測試失敗。
    重要副作用：
        建立 bundle 目錄與應用物件；不啟動 lifespan、不建立資料庫、不呼叫服務。
    """
    應用程式 = _建立正式應用程式(tmp_path)
    assert not (tmp_path / "web.sqlite3").exists()
    assert not (tmp_path / "published.sqlite3").exists()

    草稿路由 = _取得唯一正式路由(應用程式, 草稿路徑)
    建立路由 = _取得唯一正式路由(應用程式, 端點建立路徑)
    assert 草稿路由.methods == 建立路由.methods == {"POST"}

    規格 = 應用程式.openapi()
    assert set(規格["paths"][草稿路徑]) == {"post"}
    assert set(規格["paths"][端點建立路徑]) == {"post"}

    草稿綱要 = _取得請求綱要(規格, 草稿路徑)
    assert 草稿綱要["additionalProperties"] is False
    assert set(草稿綱要["required"]) == {
        "original_requirement_text",
        "selected_skills",
        "response_mode",
    }
    assert set(草稿綱要["properties"]) == set(草稿綱要["required"])

    建立綱要 = _取得請求綱要(規格, 端點建立路徑)
    assert 建立綱要["additionalProperties"] is False
    assert set(建立綱要["required"]) == {
        "draft_id",
        "slug",
        "configuration_confirmation",
    }
    assert set(建立綱要["properties"]) == set(建立綱要["required"])
    assert 建立綱要["properties"]["slug"] == {
        "type": "string",
        "minLength": 1,
        "maxLength": 63,
        "pattern": "^[a-z0-9][a-z0-9-]*$",
    }
    assert 建立綱要["properties"]["configuration_confirmation"]["type"] == "object"

    成功回應綱要 = _解析OpenAPI綱要(
        規格,
        規格["paths"][端點建立路徑]["post"]["responses"]["201"]
        ["content"]["application/json"]["schema"],
    )
    公開回應欄位 = {
        "endpoint_id",
        "version_id",
        "version_number",
        "status",
        "initial_api_key",
    }
    assert set(成功回應綱要["required"]) == 公開回應欄位
    assert set(成功回應綱要["properties"]) == 公開回應欄位
    assert 公開回應欄位.isdisjoint({
        "service_account_id",
        "bundle_id",
        "bundle_path",
        "manifest_path",
        "credential_id",
        "ciphertext",
    })

    草稿相依清單 = [相依.call for 相依 in 草稿路由.dependant.dependencies]
    建立相依清單 = [相依.call for 相依 in 建立路由.dependant.dependencies]
    目前工作階段路由 = _取得唯一正式路由(應用程式, "/api/auth/me")
    目前工作階段相依清單 = [相依.call for 相依 in 目前工作階段路由.dependant.dependencies]
    assert len(草稿相依清單) == 2
    assert 草稿相依清單 == 建立相依清單
    assert len(目前工作階段相依清單) == 1
    assert 草稿相依清單[0] is 目前工作階段相依清單[0]
    assert 是模組CSRF相依項(草稿相依清單[1])


def test_construction零封套呼叫且startup共用identity並於shutdown撤銷Create(tmp_path):
    """固定 envelope startup exact-once、A3 authority identity 共用與舊 Create proxy 關閉。

    參數：``tmp_path`` 提供隔離 Web／Published DB、bundle 與技能路徑。
    回傳值：無；construction、startup identity 與 shutdown assertions 全部成立。
    例外：factory 呼叫時機／次數、authority identity 或 proxy 撤銷漂移時測試失敗。
    重要副作用：啟動並關閉一次 canonical lifespan，建立兩個 SQLite DB；不發布端點。
    """
    封套呼叫: list[str] = []

    def 建立可觀測封套() -> AESGCM憑證封套:
        """記錄一次 startup 呼叫並回傳 exact 測試 envelope。

        參數：無。
        回傳值：新的 exact ``AESGCM憑證封套``。
        例外：底層 envelope 驗證錯誤原樣傳出。
        重要副作用：附加一筆記憶體事件，不讀 DB／FS／環境。
        """
        封套呼叫.append("envelope")
        return _建立憑證封套()

    應用程式 = _建立正式應用程式(tmp_path, 封套工廠=建立可觀測封套)
    assert 封套呼叫 == []
    捕捉管理代理 = None
    with TestClient(應用程式, raise_server_exceptions=False):
        assert 封套呼叫 == ["envelope"]
        Published資源 = 應用程式.state.發布介面資源[-1]
        Planner資源 = Published資源.取得Planner資源()
        管理服務 = Published資源.取得發布管理服務()
        assert Planner資源 is not None and 管理服務 is not None
        assert 管理服務._草稿服務 is Planner資源.取得規劃服務()
        assert 管理服務._擁有者解析器 is Planner資源.取得擁有者解析器()
        assert Planner資源.取得工具發布庫() is Published資源._工具庫
        assert 管理服務._套件協調器 is Published資源._技能套件協調器
        捕捉管理代理 = Published資源._發布管理代理

    assert 捕捉管理代理 is not None and 封套呼叫 == ["envelope"]
    with pytest.raises(RuntimeError, match="發布管理服務不可用"):
        捕捉管理代理.原子發布(
            擁有者使用者識別碼="owner",
            確認=發布確認("draft", "endpoint", {}),
        )


@pytest.mark.parametrize("失敗階段", ["key", "management"])
def test_key或management啟動失敗後兩proxy關閉且零部分authority(
    tmp_path, monkeypatch: pytest.MonkeyPatch, 失敗階段: str,
):
    """固定 secret／coordinator 任一步失敗皆撤銷 Draft 與 Create 且不寫 endpoint。

    參數：``tmp_path`` 提供隔離資源；``monkeypatch`` 注入 management 建構失敗；
    ``失敗階段`` 選擇 key factory 或 coordinator constructor。
    回傳值：無；固定 startup error、exact-once factory、proxy 與資料庫 assertions 成立。
    例外：預期 lifespan 固定拋 ``RuntimeError``；代理則各拋其 fail-closed 錯誤。
    重要副作用：建立並失敗清理一次 canonical lifespan，不建立任何發布端點。
    """
    套件根 = tmp_path / "bundles"
    套件根.mkdir()
    Web設定 = 生產設定(
        tmp_path / "web.sqlite3", ("http://localhost:5173",), "fake", "fake",
        Cookie安全=False, 工作階段有效秒數=60,
    )
    工廠呼叫: list[str] = []

    def 建立測試封套() -> AESGCM憑證封套:
        """記錄 exact-once startup 呼叫，並依案例回傳封套或拋 sentinel。

        參數：無。
        回傳值：management 失敗案例回傳 exact envelope。
        例外：key 失敗案例拋出 ``LookupError`` sentinel。
        重要副作用：只附加一筆記憶體事件。
        """
        工廠呼叫.append("envelope")
        if 失敗階段 == "key":
            raise LookupError("key unavailable")
        return _建立憑證封套()

    if 失敗階段 == "management":
        monkeypatch.setattr(
            生產Published執行模組, "發布管理協調器",
            lambda **_參數: (_ for _ in ()).throw(LookupError("management unavailable")),
        )
    Published設定 = Published生產設定(
        tmp_path / "published.sqlite3", 套件根, _安裝固定工具, _建立假模型表,
        Planner設定=Planner生產設定(
            "acceptance-release", lambda 路徑: 使用者庫(路徑),
            lambda: 決定性假規劃器(), 3600.0,
        ),
        憑證封套工廠=建立測試封套,
    )
    建構器 = 生產Controller建構器(Published設定)
    草稿代理 = 建構器._Published.取得草稿規劃代理()
    管理代理 = 建構器._Published.取得發布管理代理()
    應用程式 = 建立生產應用程式(Web設定, 建構器)
    assert 工廠呼叫 == []

    with pytest.raises(RuntimeError, match="發布介面啟動失敗"):
        with TestClient(應用程式):
            pass

    assert 工廠呼叫 == ["envelope"]
    with pytest.raises(草稿規劃服務不可用):
        草稿代理.建立草稿("owner", "需求", ("demo",), "text", 現在=1.0)
    with pytest.raises(RuntimeError, match="發布管理服務不可用"):
        管理代理.原子發布(
            擁有者使用者識別碼="owner",
            確認=發布確認("draft", "endpoint", {}),
        )
    with sqlite3.connect(tmp_path / "published.sqlite3") as 資料庫連線:
        assert 資料庫連線.execute("SELECT COUNT(*) FROM published_endpoints").fetchone()[0] == 0


@pytest.mark.parametrize("錯誤種類", ["ordinary", "control-flow"])
def test_management安裝成功後立即失敗仍關閉兩proxy且零endpoint(
    tmp_path, monkeypatch: pytest.MonkeyPatch, 錯誤種類: str,
):
    """固定 Create authority 已裝入但安裝 wrapper 隨即失敗時仍完整 fail closed。

    參數：``tmp_path`` 提供隔離 DB；``monkeypatch`` 包裝 exact 安裝；``錯誤種類``
    選擇 ordinary ``RuntimeError`` 或 control-flow ``SystemExit``。
    回傳值：無；原失敗 identity、Draft／Create proxy 關閉及零 endpoint 全部成立。
    例外：測試精確捕捉注入 sentinel；任何 lifecycle 漂移皆由 assertion 回報。
    重要副作用：執行一次正式底層 startup，於 Create 安裝後立即失敗並清理全部 authority。
    """
    Web設定, Published設定, 呼叫代理, 草稿代理, 管理代理 = _建立直接Published組裝(tmp_path)
    安裝錯誤: BaseException = (
        RuntimeError("installed then ordinary failure")
        if 錯誤種類 == "ordinary"
        else SystemExit("installed then control-flow failure")
    )
    原安裝 = 延遲發布管理服務.安裝

    def 安裝後失敗(self, 管理服務) -> None:
        """先完成真實 slot 安裝，再拋指定 exact startup sentinel。

        參數：``self`` 是 Create proxy；``管理服務`` 是本次 exact coordinator。
        回傳值：不返回。
        例外：固定拋測試建立的 ordinary 或 control-flow sentinel。
        重要副作用：先以原實作開啟 Create authority，迫使 failure cleanup 負責撤銷。
        """
        原安裝(self, 管理服務)
        raise 安裝錯誤

    monkeypatch.setattr(延遲發布管理服務, "安裝", 安裝後失敗)
    try:
        生產Published執行模組._建立Published資源(
            Web設定, Published設定, 呼叫代理, 草稿代理, 管理代理,
        )
    except BaseException as 實際錯誤:
        assert 實際錯誤 is 安裝錯誤
    else:
        pytest.fail("management 安裝後 sentinel 必須終止 startup")

    with pytest.raises(草稿規劃服務不可用):
        草稿代理.建立草稿("owner", "需求", ("demo",), "text", 現在=1.0)
    with pytest.raises(RuntimeError, match="發布管理服務不可用"):
        管理代理.原子發布(
            擁有者使用者識別碼="owner", 確認=發布確認("draft", "endpoint", {}),
        )
    with sqlite3.connect(tmp_path / "published.sqlite3") as 資料庫連線:
        assert 資料庫連線.execute("SELECT COUNT(*) FROM published_endpoints").fetchone()[0] == 0


def test_ordinary_startup搭配cleanup_control_flow重拋cleanup_exact_identity(
    tmp_path, monkeypatch: pytest.MonkeyPatch,
):
    """固定 ordinary startup failure 之後的第一個 cleanup control-flow 具有重拋優先權。

    參數：``tmp_path`` 提供隔離 DB；``monkeypatch`` 精確包裝 management install／clear。
    回傳值：無；最終例外必須是 cleanup ``SystemExit`` 的 exact object，且兩 proxy 關閉。
    例外：測試捕捉 cleanup sentinel；identity 或 fail-closed 漂移時 assertion 失敗。
    重要副作用：先真實安裝 Create authority，再於 startup 與 cleanup 各注入一次失敗。
    """
    Web設定, Published設定, 呼叫代理, 草稿代理, 管理代理 = _建立直接Published組裝(tmp_path)
    啟動錯誤 = RuntimeError("ordinary startup sentinel")
    清理錯誤 = SystemExit("cleanup control-flow sentinel")
    原安裝 = 延遲發布管理服務.安裝
    原清除 = 延遲發布管理服務.清除

    def 安裝後ordinary失敗(self, 管理服務) -> None:
        """完成真實安裝後拋 ordinary startup sentinel。

        參數：``self`` 與 ``管理服務`` 是本次 exact proxy／coordinator。
        回傳值：不返回。
        例外：固定拋 exact ordinary startup sentinel。
        重要副作用：先開啟 Create authority，再迫使 startup 進入 failure cleanup。
        """
        原安裝(self, 管理服務)
        raise 啟動錯誤

    def 清除後control_flow失敗(self, 管理服務) -> None:
        """完成真實撤銷後拋 exact cleanup control-flow sentinel。

        參數：``self`` 與 ``管理服務`` 是已安裝的 exact proxy／coordinator。
        回傳值：不返回。
        例外：固定拋 exact ``SystemExit`` sentinel。
        重要副作用：先撤銷 Create slot，使後續允許已撤銷的收斂路徑可安全重入。
        """
        原清除(self, 管理服務)
        raise 清理錯誤

    monkeypatch.setattr(延遲發布管理服務, "安裝", 安裝後ordinary失敗)
    monkeypatch.setattr(延遲發布管理服務, "清除", 清除後control_flow失敗)
    try:
        生產Published執行模組._建立Published資源(
            Web設定, Published設定, 呼叫代理, 草稿代理, 管理代理,
        )
    except BaseException as 實際錯誤:
        assert 實際錯誤 is 清理錯誤
    else:
        pytest.fail("cleanup control-flow sentinel 必須覆蓋 ordinary startup failure")

    with pytest.raises(草稿規劃服務不可用):
        草稿代理.建立草稿("owner", "需求", ("demo",), "text", 現在=1.0)
    with pytest.raises(RuntimeError, match="發布管理服務不可用"):
        管理代理.原子發布(
            擁有者使用者識別碼="owner", 確認=發布確認("draft", "endpoint", {}),
        )


@pytest.mark.parametrize("錯誤種類", ["ordinary", "control-flow"])
def test_shutdown_management_planner_invoke多重失敗保留第一個identity(
    tmp_path, monkeypatch: pytest.MonkeyPatch, 錯誤種類: str,
):
    """固定 shutdown management→Planner→Invocation 多重失敗只回報第一個同類錯誤。

    參數：``tmp_path`` 提供成功 startup；``monkeypatch`` 注入三階段失敗；
    ``錯誤種類`` 選擇三個 ordinary，或前兩個 control-flow 加一個 ordinary。
    回傳值：無；三階段皆被嘗試，且最終保留 management 第一個 exact identity。
    例外：測試捕捉第一個 sentinel；錯誤覆寫、短路或順序漂移時 assertion 失敗。
    重要副作用：建立一次正式底層資源，直接執行同步 shutdown owner 並在測試末收斂注入資源。
    """
    Web設定, Published設定, 呼叫代理, 草稿代理, 管理代理 = _建立直接Published組裝(tmp_path)
    資源 = 生產Published執行模組._建立Published資源(
        Web設定, Published設定, 呼叫代理, 草稿代理, 管理代理,
    )
    Planner資源 = 資源._Planner資源
    編排器 = 資源._編排器
    assert Planner資源 is not None and 編排器 is not None
    if 錯誤種類 == "ordinary":
        第一錯誤, 第二錯誤, 第三錯誤 = (
            RuntimeError("management first"), LookupError("planner second"), ValueError("invoke third"),
        )
    else:
        第一錯誤, 第二錯誤, 第三錯誤 = (
            SystemExit("management first"), KeyboardInterrupt("planner second"), RuntimeError("invoke third"),
        )
    事件: list[str] = []

    def management失敗(_服務) -> None:
        """記錄 management 階段並拋第一個 exact sentinel。"""
        事件.append("management")
        raise 第一錯誤

    def planner失敗() -> None:
        """記錄 Planner 階段並拋第二個 exact sentinel。"""
        事件.append("planner")
        raise 第二錯誤

    def invoke失敗(_編排器) -> None:
        """記錄 Invocation 階段並拋第三個 exact sentinel。"""
        事件.append("invoke")
        raise 第三錯誤

    with monkeypatch.context() as 注入:
        注入.setattr(管理代理, "清除", management失敗)
        注入.setattr(Planner資源, "_清除同步", planner失敗)
        注入.setattr(呼叫代理, "清除", invoke失敗)
        try:
            資源._執行關閉同步()
        except BaseException as 實際錯誤:
            assert 實際錯誤 is 第一錯誤
        else:
            pytest.fail("shutdown 多重失敗必須重拋第一個 exact sentinel")

    assert 事件 == ["management", "planner", "invoke"]
    Planner資源._清除同步()
    呼叫代理.清除(編排器)


@pytest.mark.parametrize("錯誤", [RuntimeError("credential clear"), SystemExit("credential clear")])
def test_shutdown_credential清除失敗仍完成後續清理並保留exact錯誤(
    tmp_path, monkeypatch: pytest.MonkeyPatch, 錯誤: BaseException,
):
    """credential clear失敗不得短路Planner、Invocation、registries與強參照清理。"""
    Web設定, Published設定, 呼叫代理, 草稿代理, 管理代理 = _建立直接Published組裝(tmp_path)
    憑證代理輸入 = 生產Published執行模組.延遲憑證管理服務()
    資源 = 生產Published執行模組._建立Published資源(
        Web設定, Published設定, 呼叫代理, 草稿代理, 管理代理, 憑證代理輸入,
    )
    憑證代理 = 資源._憑證管理代理
    Planner資源 = 資源._Planner資源
    編排器 = 資源._編排器
    工具庫 = 資源._工具庫
    assert 憑證代理 is not None and Planner資源 is not None and 編排器 is not None and 工具庫 is not None
    事件: list[str] = []
    原Planner清除 = Planner資源._清除同步
    原Invocation清除 = 呼叫代理.清除
    原工具清除 = 工具庫.清除所有發布

    def credential失敗(_服務, _世代) -> None:
        事件.append("credential")
        raise 錯誤

    def planner清除() -> None:
        事件.append("planner")
        原Planner清除()

    def invocation清除(_編排器) -> None:
        事件.append("invoke")
        原Invocation清除(_編排器)

    def 工具清除() -> None:
        事件.append("tools")
        原工具清除()

    monkeypatch.setattr(憑證代理, "清除", credential失敗)
    monkeypatch.setattr(Planner資源, "_清除同步", planner清除)
    monkeypatch.setattr(呼叫代理, "清除", invocation清除)
    monkeypatch.setattr(工具庫, "清除所有發布", 工具清除)
    with pytest.raises(type(錯誤)) as 捕捉:
        資源._執行關閉同步()
    assert 捕捉.value is 錯誤
    assert 事件 == ["credential", "planner", "invoke", "tools"]
    assert 資源._憑證管理代理 is None and 資源._憑證管理服務 is None
    assert 資源._Planner資源 is None and 資源._編排器 is None
    assert 資源._工具庫 is None and 資源._模型表 == {}


@pytest.mark.parametrize("請求方法", ["GET", "PUT", "PATCH", "DELETE"])
@pytest.mark.parametrize("路徑", [草稿路徑, 端點建立路徑])
def test_正式草稿與端點建立拒絕其他HTTP方法(tmp_path, 請求方法: str, 路徑: str):
    """固定 Draft／Create 除 POST 外的 GET／PUT／PATCH／DELETE 都回 405。

    參數：
        tmp_path: pytest 提供的隔離目錄。
        請求方法: pytest 參數化提供的禁止方法。
        路徑: pytest 參數化提供的 Draft 或 Endpoint Create 路徑。
    回傳值：
        無；回應必須精確為 HTTP 405。
    例外：
        正式路由缺失或錯誤接受其他方法時測試失敗。
    重要副作用：
        建立測試客戶端並送出一個不啟動服務操作的 HTTP 請求。
    """
    應用程式 = _建立正式應用程式(tmp_path)
    with TestClient(應用程式, raise_server_exceptions=False) as 客戶端:
        回應 = 客戶端.request(請求方法, 路徑)
    assert 回應.status_code == 405


@pytest.mark.parametrize("禁止欄位", 禁止客戶端聲稱欄位)
def test_正式端點建立拒絕客戶端權威聲稱且零發布副作用(tmp_path, 禁止欄位: str):
    """固定客戶端 Owner／SA／Role／Tools／Prompt 聲稱為 422 且不建立發布資料。

    參數：
        tmp_path: pytest 提供的隔離目錄。
        禁止欄位: 每次送入的一個禁止 top-level JSON key。
    回傳值：
        無；驗證固定錯誤與 Published DB 零 endpoint rows。
    例外：
        權威欄位未被 strict body 拒絕、路由缺失或產生發布副作用時測試失敗。
    重要副作用：
        啟動正式 lifespan、建立隔離測試使用者、登入並送出一次無效 Create 請求。
    """
    應用程式 = _建立正式應用程式(tmp_path)
    with TestClient(應用程式, raise_server_exceptions=False) as 客戶端:
        使用者儲存庫 = 使用者庫(tmp_path / "web.sqlite3")
        使用者儲存庫.建立使用者(
            "alice",
            "correct horse",
            roles=["user"],
            enabled_tools=[],
            enabled_skills=[],
            skill_roots=[],
            allowed_workdirs=[str(tmp_path)],
        )
        使用者儲存庫.連線.close()
        登入回應 = 客戶端.post(
            "/api/auth/login",
            json={"username": "alice", "password": "correct horse"},
        )
        assert 登入回應.status_code == 200
        建立本文 = {
            "draft_id": "draft-contract",
            "slug": "contract-api",
            "configuration_confirmation": {"system_prompt": "server-preview-value"},
            禁止欄位: "forged-client-claim",
        }
        建立回應 = 客戶端.post(
            端點建立路徑,
            json=建立本文,
            headers={"X-CSRF-Token": 登入回應.json()["csrf_token"]},
        )

    assert (建立回應.status_code, 建立回應.json()) == (
        422,
        {"detail": {"code": "invalid_request"}},
    )
    with sqlite3.connect(tmp_path / "published.sqlite3") as 資料庫連線:
        發布端點數量 = 資料庫連線.execute(
            "SELECT COUNT(*) FROM published_endpoints"
        ).fetchone()[0]
    assert 發布端點數量 == 0


def test_真Login草稿建立Endpoint成功並readback完整圖形與一次性秘密(tmp_path, caplog):
    """走完整 canonical Login→Draft→Create，驗 exact DTO、authority、DB／Bundle 與 [REDACTED]。

    參數：``tmp_path`` 提供隔離持久層；``caplog`` 收集本案產品 log 作秘密缺席證據。
    回傳值：無；EP-3 成功契約全部以 assertions 表達。
    例外：任何 HTTP、authority、graph、validator 或秘密邊界漂移時測試失敗。
    重要副作用：建立一位 owner、一份 Draft、一個 active v1 publication 與初始 credential。
    """
    應用程式 = _建立正式應用程式(tmp_path)
    # expected literals 由測試人工凍結，不從 Pydantic model 或 OpenAPI 自生。
    with TestClient(應用程式, raise_server_exceptions=False) as 客戶端:
        擁有者 = _建立Owner技能與使用者(tmp_path, "alice", "correct horse")
        登入回應, login_csrf = _登入Owner(客戶端, "alice", "correct horse")
        assert 登入回應.json()["user"]["id"] == 擁有者
        草稿回應 = _建立Server草稿(客戶端, login_csrf)
        assert 草稿回應.status_code == 201
        草稿本文 = 草稿回應.json()
        assert set(草稿本文) == 草稿回應頂層鍵
        assert set(草稿本文["preview"]) == 草稿預覽鍵
        assert 草稿本文["preview"]["selected_skills"] == ["demo"]
        assert 草稿本文["preview"]["recommended_tools"] == ["acceptance-tool"]
        assert 草稿本文["preview"]["rate_limit"] == {
            "endpoint_per_minute": 60, "credential_per_minute": 30,
        }
        聚合草稿 = _取得Planner聚合(應用程式)._草稿[草稿本文["draft_id"]]
        assert 聚合草稿.擁有者識別碼 == 擁有者
        assert [項目.名稱 for 項目 in 聚合草稿.能力摘要.技能] == ["demo"]
        assert [項目.名稱 for 項目 in 聚合草稿.能力摘要.工具] == ["acceptance-tool"]

        公開欄位, 秘密證據, 重放狀態 = _建立並收斂成功秘密(
            客戶端, 草稿回應.headers[網頁CSRFHeader名稱], 草稿本文, tmp_path, caplog,
        )
        assert 公開欄位["status_code"] == 201, "[REDACTED]"
        assert 公開欄位["keys"] == {
            "endpoint_id", "version_id", "version_number", "status", "initial_api_key",
        }, "[REDACTED]"
        assert 公開欄位["version_number"] == 1, "[REDACTED]"
        assert 公開欄位["status"] == "active", "[REDACTED]"
        assert 重放狀態 == 403, "[REDACTED]"
        assert all(秘密證據.values()), "[REDACTED]"
        建立端點識別碼 = 公開欄位["endpoint_id"]
        建立版本識別碼 = 公開欄位["version_id"]

        with sqlite3.connect(tmp_path / "published.sqlite3") as 連線:
            連線.row_factory = sqlite3.Row
            端點 = dict(連線.execute("SELECT * FROM published_endpoints").fetchone())
            版本 = dict(連線.execute("SELECT * FROM published_endpoint_versions").fetchone())
            憑證 = dict(連線.execute("SELECT * FROM endpoint_credentials").fetchone())
            服務帳號 = dict(連線.execute("SELECT * FROM service_accounts").fetchone())
            套件 = dict(連線.execute("SELECT * FROM published_skill_bundles").fetchone())
            稽核 = dict(連線.execute("SELECT * FROM audit_events").fetchone())
            消耗 = dict(連線.execute("SELECT * FROM published_draft_consumptions").fetchone())
            版本中繼 = dict(連線.execute("SELECT * FROM published_endpoint_version_metadata").fetchone())
            for 表 in (
                "published_endpoints", "published_endpoint_versions", "endpoint_credentials",
                "published_skill_bundles", "service_accounts", "audit_events",
                "published_draft_consumptions", "published_endpoint_version_metadata",
            ):
                assert 連線.execute(f'SELECT COUNT(*) FROM "{表}"').fetchone()[0] == 1
        assert 端點["id"] == 建立端點識別碼
        assert 端點["owner_user_id"] == 擁有者
        assert 端點["service_account_id"] == 服務帳號["id"]
        assert 端點["current_version_id"] == 版本["id"] == 建立版本識別碼
        assert 版本["endpoint_id"] == 端點["id"]
        assert (端點["status"], 版本["version_number"]) == ("active", 1)
        assert 版本["created_by_user_id"] == 擁有者
        assert 消耗 == {
            "draft_id": 草稿本文["draft_id"], "endpoint_id": 端點["id"],
            "consumed_at": 端點["created_at"],
        }
        assert 版本中繼 == {
            "version_id": 版本["id"], "publication_source": "initial_draft",
            "prompt_changed": 0, "skills_changed": 0, "tools_changed": 0,
            "model_changed": 0, "docs_changed": 0,
        }
        assert json.loads(版本["allowed_skills_json"]) == ["demo"]
        assert json.loads(版本["allowed_tools_json"]) == ["acceptance-tool"]
        assert set(json.loads(版本["tool_schema_snapshot_json"])) == {"acceptance-tool"}
        assert 版本["tool_runtime_revision"] == "acceptance-release"
        assert 憑證["endpoint_id"] == 端點["id"]
        assert 憑證["created_by_user_id"] == 擁有者
        assert 套件["version_id"] == 版本["id"]
        assert 套件["state"] == "published"
        assert 稽核["endpoint_id"] == 端點["id"]
        assert 稽核["resource_id"] == 端點["id"]
        assert 稽核["action"] == "endpoint_published"
        assert 稽核["actor_type"] == "user"
        assert 稽核["actor_id"] == 擁有者
        assert 稽核["outcome"] == "success"
        assert 稽核["resource_type"] == "published_endpoint"
        稽核中繼 = json.loads(稽核["metadata_json"])
        assert 稽核中繼 == {
            "version_id": 版本["id"], "version_number": 版本["version_number"],
            "bundle_id": 套件["bundle_id"], "bundle_hash": 套件["bundle_hash"],
            "credential_id": 憑證["id"], "service_account_id": 服務帳號["id"],
        }
        收據 = 套件發布收據(
            套件["bundle_id"], 套件["manifest_reference"], 套件["manifest_digest"],
            套件["bundle_hash"], 套件["total_bytes"], tmp_path / "bundles" / 套件["bundle_id"],
        )
        投影 = _取得管理服務(應用程式)._套件協調器.讀取已驗證清單(收據)
        assert 投影.bundle_id == 套件["bundle_id"]
        assert 投影.endpoint_id == 端點["id"]
        assert 投影.endpoint_version_id == 版本["id"]
        assert 投影.version_number == 1
        assert [項目.name for 項目 in 投影.source_skills] == ["demo"]
        套件目錄 = tmp_path / "bundles" / 套件["bundle_id"]
        清單位元組 = (套件目錄 / "manifest.json").read_bytes()
        清單 = json.loads(清單位元組)
        assert hashlib.sha256(清單位元組).hexdigest() == 套件["manifest_digest"] == 收據.清單摘要
        for 檔案 in 清單["copied_files"]:
            內容 = (套件目錄 / 檔案["path"]).read_bytes()
            assert len(內容) == 檔案["size_bytes"]
            assert hashlib.sha256(內容).hexdigest() == 檔案["sha256"]
            assert 清單["copied_file_hashes"][檔案["path"]] == 檔案["sha256"]
        三元組 = sorted(
            [[檔案["path"], 檔案["size_bytes"], 檔案["sha256"]] for 檔案 in 清單["copied_files"]],
            key=lambda 項目: 項目[0].encode("utf-8"),
        )
        重算套件雜湊 = hashlib.sha256(json.dumps(
            三元組, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False,
        ).encode("utf-8")).hexdigest()
        版本清單 = json.loads(版本["skill_bundle_manifest_json"])
        assert 重算套件雜湊 == 清單["bundle_hash"] == 套件["bundle_hash"] == 收據.套件雜湊
        assert 版本清單["bundle_id"] == 套件["bundle_id"]
        assert 版本清單["manifest_reference"] == 套件["manifest_reference"]
        assert 版本清單["manifest_digest"] == 套件["manifest_digest"]
        assert 版本清單["sha256"] == 套件["bundle_hash"]


修改確認案例 = {
    "Prompt": ("system_prompt", "client-modified"),
    "Tools": (None, None),
    "Input Schema": ("input_schema", {"type": "null"}),
    "Response Schema": ("response_schema", {"type": "null"}),
    "Rate": ("rate_limit", {"endpoint_per_minute": 1, "credential_per_minute": 1}),
}


@pytest.mark.parametrize("案例", sorted(修改確認案例))
def test_修改確認由genuine_coordinator恰一次拒絕且保留Draft與零發布副作用(
    tmp_path, monkeypatch: pytest.MonkeyPatch, 案例: str,
):
    """修改五類權威值，固定 genuine coordinator 一次、權威拒絕且 pre-write 零進入。

    參數：``tmp_path`` 隔離狀態；``monkeypatch`` 安裝探針；``案例`` 選修改類別。
    回傳值：無；拒絕、owner readback、完整零副作用與同 Draft HTTP retry 均以 assertions 固定。
    例外：route 因果、權威撤銷、Draft retention 或 retry 契約漂移時測試失敗。
    重要副作用：Tools 案例經真使用者庫撤權再恢復；每案最後成功發布原 Draft。
    """
    應用程式 = _建立正式應用程式(tmp_path)
    with TestClient(應用程式, raise_server_exceptions=False) as 客戶端:
        擁有者 = _建立Owner技能與使用者(tmp_path, "alice", "correct horse")
        _, csrf = _登入Owner(客戶端, "alice", "correct horse")
        草稿回應 = _建立Server草稿(客戶端, csrf)
        assert 草稿回應.status_code == 201
        草稿本文 = 草稿回應.json()
        草稿識別碼 = 草稿本文["draft_id"]
        基準草稿 = _取得Planner聚合(應用程式).讀取草稿(
            擁有者, 草稿識別碼, 現在=time.time(),
        )
        修改確認 = _建立Server確認(草稿本文["preview"])
        鍵, 值 = 修改確認案例[案例]
        if 案例 == "Tools":
            使用者儲存庫 = 使用者庫(tmp_path / "web.sqlite3")
            try:
                使用者儲存庫.設定權限欄位(
                    "alice", "enabled_tools_json", ["revoked-tool"],
                )
            finally:
                使用者儲存庫.連線.close()
        else:
            修改確認[鍵] = 值
        之前 = _建立完整副作用快照(tmp_path, 應用程式)
        管理服務 = _取得管理服務(應用程式)
        原發布 = 管理服務.原子發布
        呼叫次數 = 0

        def 記錄後委派(*參數, **關鍵字):
            """記錄 genuine coordinator 呼叫後委派原方法，保留真實權威判斷。

            參數：位置與關鍵字參數完全沿用 ``原子發布``。
            回傳值：原 genuine coordinator 的實際結果。
            例外：原方法控制流程原樣傳出。
            重要副作用：只增加記憶體 counter，產品行為仍由原方法執行。
            """
            nonlocal 呼叫次數
            呼叫次數 += 1
            return 原發布(*參數, **關鍵字)

        monkeypatch.setattr(管理服務, "原子發布", 記錄後委派)
        prewrite次數 = _安裝PreWrite零進入探針(monkeypatch, 管理服務)
        回應 = _建立Endpoint(
            客戶端, 草稿回應.headers[網頁CSRFHeader名稱], 草稿本文, 確認=修改確認,
        )
        之後 = _建立完整副作用快照(tmp_path, 應用程式)

        assert 呼叫次數 == 1
        assert prewrite次數 == {"identifier": 0, "entropy": 0, "bundle": 0, "p04": 0}
        預期錯誤 = (
            (500, {"detail": "發布管理服務失敗"})
            if 案例 == "Tools"
            else (422, {"detail": "管理操作輸入無效"})
        )
        assert (回應.status_code, 回應.json()) == 預期錯誤
        _斷言固定錯誤且不洩漏(回應, tmp_path)
        _斷言Draft語意保留(之前, 之後)
        _斷言Owner可讀且Draft語意保留(應用程式, 擁有者, 草稿識別碼, 基準草稿)
        assert 之後["tables"] == 之前["tables"]
        assert 之後["bundle_tree"] == 之前["bundle_tree"] == ()

        monkeypatch.undo()
        if 案例 == "Tools":
            使用者儲存庫 = 使用者庫(tmp_path / "web.sqlite3")
            try:
                使用者儲存庫.設定權限欄位(
                    "alice", "enabled_tools_json", ["acceptance-tool"],
                )
            finally:
                使用者儲存庫.連線.close()
        重試 = _以Fresh登入重試同Draft(
            客戶端, "alice", "correct horse", 草稿本文,
        )
        assert 重試.status_code == 201


@pytest.mark.parametrize("缺少鍵", sorted(_建立Server確認({
    "system_prompt": "p", "input_schema": None, "response_schema": {},
    "human_docs": "d", "rate_limit": {},
})))
def test_partial_confirmation每個缺失鍵皆422且genuine一次與完整零副作用(
    tmp_path, monkeypatch: pytest.MonkeyPatch, 缺少鍵: str,
):
    """逐一刪除 canonical confirmation 五鍵，固定 genuine 一次且所有 pre-write 邊界為零。

    參數：隔離目錄、pytest monkeypatch 與本案刪除的 canonical 鍵。
    回傳值：無；HTTP 422、coordinator 因果、DB／Bundle 零副作用及 owner readback 均固定。
    例外：Production 接受任何非完整 confirmation 或提早進入發布 primitive 時測試失敗。
    重要副作用：建立一份 Draft 並送出一次 partial Create；不得建立 publication。
    """
    應用程式 = _建立正式應用程式(tmp_path)
    with TestClient(應用程式, raise_server_exceptions=False) as 客戶端:
        擁有者 = _建立Owner技能與使用者(tmp_path, "alice", "correct horse")
        _, csrf = _登入Owner(客戶端, "alice", "correct horse")
        草稿回應 = _建立Server草稿(客戶端, csrf)
        assert 草稿回應.status_code == 201
        草稿本文 = 草稿回應.json()
        草稿識別碼 = 草稿本文["draft_id"]
        基準草稿 = _取得Planner聚合(應用程式).讀取草稿(
            擁有者, 草稿識別碼, 現在=time.time(),
        )
        partial = _建立Server確認(草稿本文["preview"])
        partial.pop(缺少鍵)
        之前 = _建立完整副作用快照(tmp_path, 應用程式)
        管理服務 = _取得管理服務(應用程式)
        原發布 = 管理服務.原子發布
        呼叫次數 = 0

        def 記錄後委派(*參數, **關鍵字):
            """記錄 genuine coordinator 一次並委派 partial confirmation 真判斷。"""
            nonlocal 呼叫次數
            呼叫次數 += 1
            return 原發布(*參數, **關鍵字)

        monkeypatch.setattr(管理服務, "原子發布", 記錄後委派)
        prewrite次數 = _安裝PreWrite零進入探針(monkeypatch, 管理服務)
        回應 = _建立Endpoint(
            客戶端, 草稿回應.headers[網頁CSRFHeader名稱], 草稿本文, 確認=partial,
        )
        之後 = _建立完整副作用快照(tmp_path, 應用程式)

        assert 呼叫次數 == 1
        assert prewrite次數 == {"identifier": 0, "entropy": 0, "bundle": 0, "p04": 0}
        assert (回應.status_code, 回應.json()) == (422, {"detail": "管理操作輸入無效"})
        _斷言Owner可讀且Draft語意保留(應用程式, 擁有者, 草稿識別碼, 基準草稿)
        assert 之後["tables"] == 之前["tables"]
        assert 之後["bundle_tree"] == 之前["bundle_tree"] == ()


@pytest.mark.parametrize("案例", ["Foreign", "Expired", "Missing"])
def test_不可用Draft的canonical_Create固定不可枚舉且完整發布狀態不變(tmp_path, 案例: str):
    """固定 Foreign／Expired／Missing Draft Create 同一 404，並保持完整 Published DB／FS。

    參數：``tmp_path`` 隔離狀態；``案例`` 選跨 owner、過期或不存在 Draft。
    回傳值：無。
    例外：錯誤可枚舉、可用 Draft 語意漂移或任何 publication side effect 時測試失敗。
    重要副作用：建立一份 Draft；Foreign 另登入第二 owner；expired lookup 依 aggregate 契約淘汰過期項目。
    """
    存續 = 0.001 if 案例 == "Expired" else 3600.0
    應用程式 = _建立正式應用程式(tmp_path, 草稿存續秒數=存續)
    with TestClient(應用程式, raise_server_exceptions=False) as 客戶端:
        擁有者 = _建立Owner技能與使用者(tmp_path, "alice", "correct horse")
        _, csrf = _登入Owner(客戶端, "alice", "correct horse")
        草稿回應 = _建立Server草稿(客戶端, csrf)
        assert 草稿回應.status_code == 201
        草稿本文 = 草稿回應.json()
        原草稿識別碼 = 草稿本文["draft_id"]
        基準草稿 = None if 案例 == "Expired" else _取得Planner聚合(應用程式).讀取草稿(
            擁有者, 原草稿識別碼, 現在=time.time(),
        )
        建立csrf = 草稿回應.headers[網頁CSRFHeader名稱]
        if 案例 == "Foreign":
            登出 = 客戶端.post("/api/auth/logout", headers={網頁CSRFHeader名稱: 建立csrf})
            assert 登出.status_code == 204
            _建立Owner技能與使用者(tmp_path, "bob", "another horse")
            _, 建立csrf = _登入Owner(客戶端, "bob", "another horse")
        elif 案例 == "Expired":
            time.sleep(0.02)
        else:
            草稿本文 = dict(草稿本文)
            草稿本文["draft_id"] = "missing-draft"
        之前 = _建立完整副作用快照(tmp_path, 應用程式)
        回應 = _建立Endpoint(客戶端, 建立csrf, 草稿本文)
        之後 = _建立完整副作用快照(tmp_path, 應用程式)
        if 案例 != "Expired":
            _斷言Owner可讀且Draft語意保留(
                應用程式, 擁有者, 原草稿識別碼, 基準草稿,
            )

    assert (回應.status_code, 回應.json()) == (404, {"detail": "找不到發布草稿"})
    _斷言固定錯誤且不洩漏(回應, tmp_path)
    if 案例 == "Expired":
        assert len(之前["drafts"]) == 1 and 之後["drafts"] == ()
    else:
        _斷言Draft語意保留(之前, 之後)
    assert 之後["tables"] == 之前["tables"]
    assert 之後["bundle_tree"] == 之前["bundle_tree"] == ()


def test_已消耗CSRF重放Create固定403且management完全不進入(tmp_path, monkeypatch: pytest.MonkeyPatch):
    """用已消耗 Login token 重放 Create，固定 403 且 genuine management operation 零進入。

    參數：``tmp_path`` 隔離狀態；``monkeypatch`` 對 genuine service 安裝不得進入 sentinel。
    回傳值：無。
    例外：CSRF 未在 route 前拒絕、Draft 漂移或 DB／Bundle 有副作用時測試失敗。
    重要副作用：只建立一份 Draft；重放請求不得觸發 publication。
    """
    應用程式 = _建立正式應用程式(tmp_path)
    with TestClient(應用程式, raise_server_exceptions=False) as 客戶端:
        _建立Owner技能與使用者(tmp_path, "alice", "correct horse")
        _, 已消耗csrf = _登入Owner(客戶端, "alice", "correct horse")
        草稿回應 = _建立Server草稿(客戶端, 已消耗csrf)
        assert 草稿回應.status_code == 201
        草稿本文 = 草稿回應.json()
        呼叫次數 = 0
        原發布 = _取得管理服務(應用程式).原子發布

        def 記錄後委派(*參數, **關鍵字):
            """若 CSRF gate 漂移則記錄並委派 genuine coordinator。

            參數：完全沿用 ``原子發布`` 的位置與關鍵字參數。
            回傳值：原 genuine coordinator 結果。
            例外：原方法控制流程原樣傳出。
            重要副作用：只有 gate 錯誤放行時才增加 counter 並可能產生產品副作用。
            """
            nonlocal 呼叫次數
            呼叫次數 += 1
            return 原發布(*參數, **關鍵字)

        monkeypatch.setattr(_取得管理服務(應用程式), "原子發布", 記錄後委派)
        之前 = _建立完整副作用快照(tmp_path, 應用程式)
        回應 = _建立Endpoint(客戶端, 已消耗csrf, 草稿本文)
        之後 = _建立完整副作用快照(tmp_path, 應用程式)

    assert 回應.status_code == 403
    assert 呼叫次數 == 0
    assert 之後 == 之前
    _斷言固定錯誤且不洩漏(回應, tmp_path)


@pytest.mark.parametrize("失敗階段", ["Bundle", "SQLite"])
def test_代表性Bundle或SQLite失敗經canonical_Create固定500且零active_publication(
    tmp_path, monkeypatch: pytest.MonkeyPatch, 失敗階段: str,
):
    """以代表性 FS／DB seam 固定 HTTP 500、秘密缺席與零 active publication。

    參數：``tmp_path`` 提供隔離資源；``monkeypatch`` 注入 genuine dependency failure；
    ``失敗階段`` 選擇 Bundle Publisher 或 SQLite P04 邊界。
    回傳值：無；固定錯誤、Draft 語意與 Published DB assertions 全部成立。
    例外：HTTP 映射洩漏內部細節，或失敗留下 active graph 時測試失敗。
    重要副作用：經 canonical HTTP 建立 Draft 並嘗試一次 Create；SQLite 案例可依既有
    orphan protocol 移動已發布 Bundle，該跨資源細節留給 A4-04 驗證。
    """
    應用程式 = _建立正式應用程式(tmp_path)
    with TestClient(應用程式, raise_server_exceptions=False) as 客戶端:
        擁有者 = _建立Owner技能與使用者(tmp_path, "alice", "correct horse")
        _, csrf = _登入Owner(客戶端, "alice", "correct horse")
        草稿回應 = _建立Server草稿(客戶端, csrf)
        assert 草稿回應.status_code == 201
        草稿本文 = 草稿回應.json()
        草稿識別碼 = 草稿本文["draft_id"]
        基準草稿 = _取得Planner聚合(應用程式).讀取草稿(
            擁有者, 草稿識別碼, 現在=time.time(),
        )
        管理服務 = _取得管理服務(應用程式)

        if 失敗階段 == "Bundle":
            def 套件發布失敗(_發布器, **_參數):
                """在任何 Bundle bytes 產生前拋出含內部 marker 的代表性 FS error。"""
                raise OSError(f"{tmp_path}/manifest/ciphertext/key_hash")

            monkeypatch.setattr(type(管理服務._套件發布器), "發布", 套件發布失敗)
        else:
            def 圖形發布失敗(_服務, *_參數, **_關鍵字):
                """在 P04 入口拋出含內部 marker 的代表性 SQLite error。"""
                raise sqlite3.OperationalError(f"{tmp_path}/published.sqlite3 credential_id")

            monkeypatch.setattr(type(管理服務._端點發布服務), "發布已準備圖形", 圖形發布失敗)

        之前 = _建立完整副作用快照(tmp_path, 應用程式)
        回應 = _建立Endpoint(
            客戶端, 草稿回應.headers[網頁CSRFHeader名稱], 草稿本文,
        )
        之後 = _建立完整副作用快照(tmp_path, 應用程式)

        assert (回應.status_code, 回應.json()) == (500, {"detail": "發布管理服務失敗"})
        _斷言固定錯誤且不洩漏(回應, tmp_path)
        _斷言Draft語意保留(之前, 之後)
        _斷言Owner可讀且Draft語意保留(應用程式, 擁有者, 草稿識別碼, 基準草稿)
        assert 之後["tables"] == 之前["tables"]
        assert all(之後["tables"][表] == () for 表 in 發布副作用資料表)
        if 失敗階段 == "Bundle":
            assert 之後["bundle_tree"] == 之前["bundle_tree"] == ()

        monkeypatch.undo()
        重試 = _以Fresh登入重試同Draft(
            客戶端, "alice", "correct horse", 草稿本文,
        )
        assert 重試.status_code == 201


def test_Canonical_Create後Version路由使用真P05建立v2並切換pointer(tmp_path):
    """走 canonical Login→Draft→Create→Version，證明三路由組裝安裝真 P05 authority。

    參數：``tmp_path`` 隔離 Web／Published DB、技能來源與 bundle root。
    回傳值：無；HTTP 201 DTO 與 SQLite current pointer 以 assertions 表達。
    例外：session、CSRF、Version proxy、P05 或 bundle 任一斷鏈時測試失敗。
    重要副作用：建立 owner、v1、v2 及兩份不可變 bundle，並把 current pointer 切到 v2。
    """
    應用 = _建立正式應用程式(tmp_path)
    with TestClient(應用, raise_server_exceptions=False) as 客戶端:
        _建立Owner技能與使用者(tmp_path, "alice", "correct horse")
        _, csrf = _登入Owner(客戶端, "alice", "correct horse")
        草稿 = _建立Server草稿(客戶端, csrf)
        assert 草稿.status_code == 201
        建立 = _建立Endpoint(客戶端, 草稿.headers[網頁CSRFHeader名稱], 草稿.json())
        assert 建立.status_code == 201, 建立.text
        預覽 = 草稿.json()["preview"]
        版本 = 客戶端.post(
            f"/api/published-endpoints/{建立.json()['endpoint_id']}/versions",
            json={"configuration": {
                "original_requirement_text": "建立 Demo API",
                "system_prompt": 預覽["system_prompt"],
                "model_config_snapshot": {
                    "provider": "fake", "model": "fake", "temperature": 0.0,
                    "max_tokens": 4096, "timeout_seconds": 60.0,
                    "structured_output": True, "schema_retry_count": 1,
                },
                "retry_policy": {"max_attempts": 1},
                "input_schema": 預覽["input_schema"],
                "response_schema": 預覽["response_schema"],
            }},
            headers={網頁CSRFHeader名稱: 建立.headers[網頁CSRFHeader名稱]},
        )
        assert 版本.status_code == 201, 版本.text
        版本本文 = 版本.json()
        assert 版本本文 == {
            "endpoint_id": 建立.json()["endpoint_id"],
            "version_id": 版本本文["version_id"],
            "version_number": 2,
            "current_version_id": 版本本文["version_id"],
            "schema_changed": False,
        }

    with sqlite3.connect(tmp_path / "published.sqlite3") as 連線:
        assert 連線.execute(
            "SELECT current_version_id FROM published_endpoints WHERE id=?",
            (建立.json()["endpoint_id"],),
        ).fetchone() == (版本本文["version_id"],)
        assert 連線.execute(
            "SELECT COUNT(*) FROM published_endpoint_versions WHERE endpoint_id=?",
            (建立.json()["endpoint_id"],),
        ).fetchone() == (2,)


def test_Create成功後來源刪除且重啟仍由v1_bundle完成Invoke(tmp_path):
    """走 canonical Create→shutdown→restart→Invoke，證明 v1 runtime 不依賴 live source。

    參數：``tmp_path`` 供兩個 successive canonical apps 共用 Web／Published DB 與 bundle root。
    回傳值：無；Create DTO、bundle bytes、restart HTTP 200 與 provider snapshot 皆以 assertions 表達。
    例外：建構、startup、Create、reconciliation、credential 或 runtime 任一斷鏈時測試失敗。
    重要副作用：建立 owner 與 v1 publication，關閉第一 app 後刪除來源技能，再啟動第二 app 呼叫。
    """
    第一模型 = _記錄假模型()
    第一應用 = _建立正式應用程式(
        tmp_path, 模型表工廠=lambda: {"fake": 第一模型},
    )
    初始金鑰 = None
    with TestClient(第一應用, raise_server_exceptions=False) as 客戶端:
        _建立Owner技能與使用者(tmp_path, "alice", "correct horse")
        _, csrf = _登入Owner(客戶端, "alice", "correct horse")
        草稿回應 = _建立Server草稿(客戶端, csrf)
        assert 草稿回應.status_code == 201
        建立回應 = _建立Endpoint(
            客戶端, 草稿回應.headers[網頁CSRFHeader名稱], 草稿回應.json(),
        )
        assert 建立回應.status_code == 201, 建立回應.text
        建立本文 = 建立回應.json()
        初始金鑰 = 建立本文["initial_api_key"]
        assert 建立本文["version_number"] == 1 and 建立本文["status"] == "active"

    with sqlite3.connect(tmp_path / "published.sqlite3") as 連線:
        套件識別碼, 清單摘要 = 連線.execute(
            "SELECT bundle_id,manifest_digest FROM published_skill_bundles"
        ).fetchone()
    清單路徑 = tmp_path / "bundles" / 套件識別碼 / "manifest.json"
    原始清單 = 清單路徑.read_bytes()
    assert hashlib.sha256(原始清單).hexdigest() == 清單摘要
    (tmp_path / "skills" / "demo" / "SKILL.md").unlink()

    第二模型 = _記錄假模型()
    重啟應用 = _建立正式應用程式(
        tmp_path, 模型表工廠=lambda: {"fake": 第二模型},
    )
    with TestClient(重啟應用, raise_server_exceptions=False) as 客戶端:
        呼叫回應 = 客戶端.post(
            "/v1/endpoints/demo-api/invoke",
            json={"input": {"question": "restart"}},
            headers={"Authorization": f"Bearer {初始金鑰}"},
        )
    assert 呼叫回應.status_code == 200, 呼叫回應.text
    assert len(第二模型.呼叫) == 1
    provider參數 = json.dumps(第二模型.呼叫[0], ensure_ascii=False, sort_keys=True)
    assert "# Demo" in provider參數
    assert 清單路徑.read_bytes() == 原始清單
    初始金鑰 = None


@pytest.mark.parametrize("破壞", ["manifest", "file", "receipt", "provider", "tool"])
def test_Canonical_Create後重啟竄改或缺runtime_pin皆在模型呼叫前拒絕(tmp_path, 破壞):
    """由真 Create producer 建 v1，逐一證明 runtime tamper 與 missing pin fail closed。

    參數：``tmp_path`` 隔離兩次 app lifecycle；``破壞`` 選 manifest、file、receipt、provider 或 tool。
    回傳值：無；HTTP 固定錯誤、零 provider call 與原始 v1 identity 皆以 assertions 表達。
    例外：任一破壞越過 loader／registry 邊界或改用 live source fallback 時測試失敗。
    重要副作用：每案發布一個 v1，App B startup 後只修改指定 authority，隨後呼叫 stable URL。
    """
    建立應用 = _建立正式應用程式(tmp_path)
    初始金鑰 = None
    with TestClient(建立應用, raise_server_exceptions=False) as 客戶端:
        _建立Owner技能與使用者(tmp_path, "alice", "correct horse")
        _, csrf = _登入Owner(客戶端, "alice", "correct horse")
        草稿 = _建立Server草稿(客戶端, csrf)
        assert 草稿.status_code == 201
        建立 = _建立Endpoint(客戶端, 草稿.headers[網頁CSRFHeader名稱], 草稿.json())
        assert 建立.status_code == 201
        初始金鑰 = 建立.json()["initial_api_key"]

    with sqlite3.connect(tmp_path / "published.sqlite3") as 連線:
        套件識別碼, 版本識別碼 = 連線.execute(
            "SELECT bundle_id,version_id FROM published_skill_bundles"
        ).fetchone()
    套件根 = tmp_path / "bundles" / 套件識別碼
    清單路徑 = 套件根 / "manifest.json"
    清單 = json.loads(清單路徑.read_bytes())
    模型 = _記錄假模型()
    模型表工廠 = (
        (lambda: {"other": 模型}) if 破壞 == "provider" else (lambda: {"fake": 模型})
    )
    def 安裝缺少釘選工具(工具發布庫物件) -> None:
        """保留 acceptance release，但刻意只安裝非 v1 釘選的合法工具。"""
        工具發布庫物件.登錄發布(工具發布描述(
            "acceptance-release",
            (工具發布註冊(
                "other-revision",
                工具定義(
                    "other-tool", "Other deterministic tool",
                    {"type": "object", "properties": {}, "additionalProperties": False},
                    lambda _參數: {"ok": True},
                ),
            ),),
        ))

    工具安裝器 = 安裝缺少釘選工具 if 破壞 == "tool" else _安裝固定工具
    重啟應用 = _建立正式應用程式(
        tmp_path, 模型表工廠=模型表工廠, 工具安裝器=工具安裝器,
    )
    with TestClient(重啟應用, raise_server_exceptions=False) as 客戶端:
        if 破壞 == "manifest":
            清單路徑.chmod(0o644)
            清單路徑.write_bytes(b"{}")
        elif 破壞 == "file":
            目標 = 套件根 / 清單["copied_files"][0]["path"]
            目標.chmod(0o644)
            目標.write_bytes(b"tampered")
        elif 破壞 == "receipt":
            with sqlite3.connect(tmp_path / "published.sqlite3") as 連線:
                immutable觸發器 = [
                    列[0] for 列 in 連線.execute(
                        "SELECT name FROM sqlite_master WHERE type='trigger' AND tbl_name='published_skill_bundles'"
                    )
                ]
                assert immutable觸發器
                for 名稱 in immutable觸發器:
                    連線.execute(f'DROP TRIGGER "{名稱}"')
                連線.execute(
                    "UPDATE published_skill_bundles SET bundle_hash=? WHERE version_id=?",
                    ("0" * 64, 版本識別碼),
                )
        回應 = 客戶端.post(
            "/v1/endpoints/demo-api/invoke",
            json={"input": {"question": "must-fail"}},
            headers={"Authorization": f"Bearer {初始金鑰}"},
        )
    assert 回應.status_code == 500
    預期錯誤碼 = "internal_error" if 破壞 == "receipt" else "endpoint_misconfigured"
    assert 回應.json()["error"]["code"] == 預期錯誤碼
    assert 模型.呼叫 == []
    初始金鑰 = None
