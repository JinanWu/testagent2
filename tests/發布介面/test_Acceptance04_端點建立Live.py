"""Acceptance #4 EP-1～EP-3：正式草稿與端點建立契約及 Live E2E。

本模組只從 ``建立CP4ASGI應用程式`` 建立正式應用，不建立手工 FastAPI
應用或替代路由。測試固定 Draft／Create 路徑、方法、OpenAPI 本文與回應、
正式 Session／單次 CSRF 相依身分，以及不得由客戶端聲稱的內部權威欄位。
EP-3 另以真 Login、Owner Authority 與公開 canonical route 驗證發布圖形及秘密邊界。
"""

from __future__ import annotations

import json
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


def _建立正式應用程式(
    暫存目錄: Path, *, 封套工廠=_建立憑證封套,
    草稿存續秒數: float = 3600.0,
):
    """建立不讀隱含環境且可由正式 lifespan 啟動的 CP4 應用。

    參數：
        暫存目錄: pytest 提供的隔離目錄，用來配置 Web DB、Published DB 與 bundle root。
        封套工廠: 只保存至 startup 才呼叫的 explicit credential envelope factory。
        草稿存續秒數: Server Draft 的存續時間，用於 Live expiry 邊界。
    回傳值：
        ``建立CP4ASGI應用程式`` 回傳的正式 FastAPI 應用。
    例外：
        正式設定或應用組裝違反既有契約時，原樣傳出對應例外。
    重要副作用：
        只建立 bundle 目錄；應用建構本身不得建立資料庫或呼叫外部注入。
    """
    技能套件根目錄 = 暫存目錄 / "bundles"
    技能套件根目錄.mkdir()
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
        _安裝固定工具,
        _建立假模型表,
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


def test_正式端點建立只接受伺服器草稿相等確認(tmp_path):
    """固定 Create 不得用客戶端修改值覆寫正式 Draft 的 Planner 內容。

    參數：
        tmp_path: pytest 提供的隔離目錄，用來保存技能、Web DB、Published DB 與 bundle root。
    回傳值：
        無；修改 ``system_prompt`` 的 confirmation 必須固定回 422 且零發布資料。
    例外：
        Draft 路由缺失、正式規劃失敗、修改值被接受或產生發布副作用時測試失敗。
    重要副作用：
        啟動正式 lifespan、建立隔離技能與使用者、建立一份 Draft 並送出一次不相等確認。
    """
    技能根目錄 = tmp_path / "skills"
    技能目錄 = 技能根目錄 / "demo"
    技能目錄.mkdir(parents=True)
    (技能目錄 / "SKILL.md").write_text(
        "---\nname: demo\ndescription: contract skill\n---\n\n# Demo\n",
        encoding="utf-8",
    )
    應用程式 = _建立正式應用程式(tmp_path)
    with TestClient(應用程式, raise_server_exceptions=False) as 客戶端:
        使用者儲存庫 = 使用者庫(tmp_path / "web.sqlite3")
        使用者儲存庫.建立使用者(
            "alice",
            "correct horse",
            roles=["user"],
            enabled_tools=[],
            enabled_skills=["demo"],
            skill_roots=[str(技能根目錄)],
            allowed_workdirs=[str(tmp_path)],
        )
        使用者儲存庫.連線.close()
        登入回應 = 客戶端.post(
            "/api/auth/login",
            json={"username": "alice", "password": "correct horse"},
        )
        assert 登入回應.status_code == 200
        草稿回應 = 客戶端.post(
            草稿路徑,
            json={
                "original_requirement_text": "建立合約測試 API",
                "selected_skills": ["demo"],
                "response_mode": "text",
            },
            headers={"X-CSRF-Token": 登入回應.json()["csrf_token"]},
        )
        assert 草稿回應.status_code == 201
        草稿本文 = 草稿回應.json()
        預覽 = 草稿本文["preview"]
        修改確認 = {
            鍵: 預覽[鍵]
            for 鍵 in ("system_prompt", "input_schema", "response_schema", "human_docs", "rate_limit")
        }
        修改確認["system_prompt"] = "客戶端不得覆寫的提示"
        建立回應 = 客戶端.post(
            端點建立路徑,
            json={
                "draft_id": 草稿本文["draft_id"],
                "slug": "contract-api",
                "configuration_confirmation": 修改確認,
            },
            headers={"X-CSRF-Token": 草稿回應.headers["X-CSRF-Token"]},
        )

    assert (建立回應.status_code, 建立回應.json()) == (
        422,
        {"detail": "管理操作輸入無效"},
    )
    with sqlite3.connect(tmp_path / "published.sqlite3") as 資料庫連線:
        發布端點數量 = 資料庫連線.execute(
            "SELECT COUNT(*) FROM published_endpoints"
        ).fetchone()[0]
    assert 發布端點數量 == 0
