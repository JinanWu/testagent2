"""A6-06：Acceptance #6 產品端對端關閉。

本檔以 canonical production composition（``建立生產應用程式``）組裝真實 ASGI 應用程式，
使用真 Cookie Session 與 single-use CSRF，走真實 HTTP 完成
Login → Draft → Publish v1 → Invoke → 改 Live Skill → 再 Invoke → Publish v2 → Restart，
並以檔案系統與 SQLite readback 證明每個 Endpoint Version 都載入自己的不可變 Bundle。

Draft 已由 Acceptance #3 掛進 canonical live app；Publish／Version 仍屬 Acceptance #4。
本檔的 inventory test 直接驗證 canonical app；其餘 Acceptance #6 案例因發布服務需要共享
同一個測試 Draft Aggregate，會在特殊 E2E app 內以完整真實管理 Router 取代 canonical
Draft Router，不使用 service fake，也不把此替代組合誤當成 canonical inventory 證據。
"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import 繁中代理.發布介面.生產Published執行 as 生產Published
from 繁中代理.使用者 import 使用者庫
from 繁中代理.工具 import 工具定義
from 繁中代理.發布介面.asgi import 建立CP4ASGI應用程式
from 繁中代理.發布介面.執行期.工具發布庫 import 工具發布庫, 工具發布描述, 工具發布註冊
from 繁中代理.發布介面.執行期.模型契約 import 模型回應快照
from 繁中代理.發布介面.憑證.加密 import AESGCM憑證封套
from 繁中代理.發布介面.技能套件.協調器 import 技能套件協調器
from 繁中代理.發布介面.技能套件.發布器 import 技能套件發布器
from 繁中代理.發布介面.技能套件.載入器 import (
    已發布技能套件載入器, 技能套件定位, 技能套件載入錯誤,
)
from 繁中代理.發布介面.相依項 import 發布介面相依項
from 繁中代理.發布介面.生產Published執行 import Published生產設定, 生產Controller建構器
from 繁中代理.發布介面.生產Published管理 import Planner生產設定
from 繁中代理.發布介面.生產組裝 import 建立生產應用程式
from 繁中代理.發布介面.規劃.擁有者能力 import 擁有者能力轉接器
from 繁中代理.發布介面.規劃.發布管理 import 發布管理協調器
from 繁中代理.發布介面.規劃.版本服務 import SQLite版本配置服務
from 繁中代理.發布介面.規劃.端點發布 import SQLite端點發布服務
from 繁中代理.發布介面.規劃.綱要 import 規劃服務
from 繁中代理.發布介面.規劃.規劃器服務 import 伺服器端草稿規劃服務
from 繁中代理.發布介面.規劃.規劃器供應商 import 決定性假規劃器
from 繁中代理.發布介面.設定 import 生產設定
from 繁中代理.發布介面.路由.規劃發布 import 建立安全規劃發布路由器

_技能名稱 = "alpha"
_原始技能正文 = (
    "---\nname: alpha\ndescription: Alpha skill\n---\n# Alpha\n原始發布內容\n"
)
_竄改技能正文 = (
    "---\nname: alpha\ndescription: Alpha skill\n---\n# Alpha\n事後竄改內容\n"
)
_模型設定 = {
    "provider": "fake", "model": "model-1", "temperature": 0.0,
    "max_tokens": 64, "timeout_seconds": 3.0,
    "structured_output": True, "schema_retry_count": 1,
}
_重試政策 = {"max_attempts": 1}
_需求文字 = "建立 Alpha API"
_帳號 = "owner"
_密碼 = "correct horse battery"


class _記錄模型:
    """記錄每次 production executor 呼叫的最小真實模型。"""

    def __init__(self) -> None:
        """建立空呼叫紀錄。"""
        self.呼叫: list[dict] = []

    def 產生發布回應(self, **參數):
        """記錄完整呼叫參數並回傳固定 runtime DTO。"""
        self.呼叫.append(參數)
        return 模型回應快照('{"result":"ok"}', "stop", {"total_tokens": 3}, [])


def _建立工具發布描述() -> 工具發布描述:
    """建立 v1／v2 共用的固定 pinned tool release。"""
    return 工具發布描述("release-1", (工具發布註冊("revision-1", 工具定義(
        "alpha-tool", "Alpha tool",
        {"type": "object", "properties": {}, "additionalProperties": False},
        lambda _參數: "ok",
    )),))


def _安裝工具發布(儲存庫: 工具發布庫) -> None:
    """在 Published startup 安裝 exact pinned release 一次。"""
    儲存庫.登錄發布(_建立工具發布描述())


class _E2E建構器:
    """以共享測試 Aggregate 的完整管理 Router 取代 canonical Draft Router。"""

    def __init__(self, Published設定: Published生產設定, 管理相依) -> None:
        """保存 canonical Controller builder 與管理服務工廠。"""
        self._Controller = 生產Controller建構器(Published設定)
        self._管理相依 = 管理相依

    def 建立附加相依項(self, 設定, 目前工作階段相依, CSRF相依) -> 發布介面相依項:
        """只在 A6 特殊 E2E app 中替換 Draft Router，避免重複 Route。"""
        基礎 = self._Controller.建立附加相依項(設定, 目前工作階段相依, CSRF相依)
        草稿路由器們 = tuple(
            路由器 for 路由器 in 基礎.路由器清單
            if any(getattr(路由, "path", None) == "/api/published-endpoints/draft" for 路由 in 路由器.routes)
        )
        if len(草稿路由器們) != 1:
            raise AssertionError("A6 E2E 預期 exact 一個 canonical Draft Router")
        保留路由器 = tuple(路由器 for 路由器 in 基礎.路由器清單 if 路由器 is not 草稿路由器們[0])
        管理路由器 = 建立安全規劃發布路由器(
            self._管理相依["草稿規劃服務"], self._管理相依["發布服務"],
            目前工作階段相依, CSRF相依,
        )
        return 發布介面相依項(
            (*保留路由器, 管理路由器), 基礎.資源工廠清單,
        )


def _建立管理相依(
    技能根: Path, 網頁資料庫: Path, 發布資料庫: Path, 套件根: Path,
) -> dict:
    """以真實 primitive 建立草稿與發布服務，不使用任何 fake service。"""
    使用者庫物件 = 使用者庫(網頁資料庫)
    使用者 = 使用者庫物件.建立使用者(
        _帳號, _密碼, roles=["admin"], enabled_tools=["alpha-tool"],
        enabled_skills=[_技能名稱], skill_roots=[str(技能根)],
        allowed_workdirs=[str(技能根)],
    )
    工具庫 = 工具發布庫()
    工具庫.登錄發布(_建立工具發布描述())
    解析器 = 擁有者能力轉接器(使用者庫物件, 工具庫, "release-1")
    草稿服務 = 規劃服務(存續秒數=3600)
    草稿規劃服務 = 伺服器端草稿規劃服務(
        解析器, 決定性假規劃器(), 草稿服務=草稿服務,
    )
    次數: dict[str, int] = {}

    def 識別碼(前綴: str) -> str:
        """為每種 graph identity 建立穩定序號。"""
        次數[前綴] = 次數.get(前綴, 0) + 1
        return f"{前綴}-{次數[前綴]}"

    def 未使用識別() -> str:
        """prepared 路徑不得呼叫 legacy 識別工廠。"""
        return "unused"

    端點服務 = SQLite端點發布服務(
        發布資料庫, 未使用識別, 未使用識別, 未使用識別, 未使用識別, time.time,
    )
    版本服務 = SQLite版本配置服務(發布資料庫, 未使用識別, time.time)
    發布服務 = 發布管理協調器(
        草稿服務=草稿服務, 擁有者解析器=解析器,
        套件發布器物件=技能套件發布器(套件根),
        套件協調器物件=技能套件協調器(套件根, 孤兒保留秒數=3600, 時鐘=time.time),
        端點發布服務=端點服務, 版本配置服務=版本服務,
        憑證封套=AESGCM憑證封套({1: b"K" * 32}, 1),
        模型設定=dict(_模型設定), 重試政策=dict(_重試政策),
        時鐘=time.time, 識別碼產生器=識別碼,
    )
    return {
        "草稿規劃服務": 草稿規劃服務, "發布服務": 發布服務,
        "使用者庫": 使用者庫物件, "擁有者": str(使用者["id"]),
    }


def _建立應用(tmp_path: Path, 模型: _記錄模型, 管理相依: dict):
    """以 canonical production composition 建立含管理路由的真實應用程式。"""
    網頁資料庫 = tmp_path / "web.sqlite3"
    發布資料庫 = tmp_path / "published.sqlite3"
    套件根 = tmp_path / "bundles"
    套件根.mkdir(exist_ok=True)
    網頁設定 = 生產設定(
        網頁資料庫, ("http://localhost:5173",), "fake", "fake",
        Cookie安全=False, 工作階段有效秒數=3600,
    )
    Published設定 = Published生產設定(
        發布資料庫, 套件根, _安裝工具發布, lambda: {"fake": 模型}, 60.0,
    )
    return 建立生產應用程式(網頁設定, _E2E建構器(Published設定, 管理相依))


def _建立環境(tmp_path: Path):
    """建立技能來源、真實使用者與 canonical 應用程式。"""
    技能根 = tmp_path / "skills"
    (技能根 / _技能名稱).mkdir(parents=True)
    (技能根 / _技能名稱 / "SKILL.md").write_text(_原始技能正文, encoding="utf-8")
    模型 = _記錄模型()
    管理相依 = _建立管理相依(
        技能根, tmp_path / "web.sqlite3", tmp_path / "published.sqlite3",
        tmp_path / "bundles",
    )
    應用 = _建立應用(tmp_path, 模型, 管理相依)
    return {
        "應用": 應用, "模型": 模型, "技能主檔": 技能根 / _技能名稱 / "SKILL.md",
        "套件根": tmp_path / "bundles", "發布資料庫": tmp_path / "published.sqlite3",
        "管理相依": 管理相依, "技能根": 技能根,
    }


def _登入(客戶端: TestClient) -> str:
    """以真實帳密取得 cookie session 與首枚 single-use CSRF。"""
    回應 = 客戶端.post(
        "/api/auth/login", json={"username": _帳號, "password": _密碼},
    )
    assert 回應.status_code == 200, 回應.text
    return 回應.json()["csrf_token"]


def _接續CSRF(客戶端: TestClient, 回應) -> str:
    """取出 single-use CSRF 輪替後的接續權杖。"""
    接續 = 回應.headers.get("X-CSRF-Token") or 客戶端.cookies.get("published_web_csrf")
    assert 接續, "管理路由必須回傳接續 CSRF 權杖"
    return 接續


def _收據列(發布資料庫: Path) -> list[dict]:
    """讀取全部套件收據。"""
    連線 = sqlite3.connect(發布資料庫)
    try:
        連線.row_factory = sqlite3.Row
        return [dict(列) for 列 in 連線.execute(
            "SELECT * FROM published_skill_bundles ORDER BY published_at,bundle_id"
        )]
    finally:
        連線.close()


def _樹指紋(根: Path) -> list[tuple[str, int, str, int]]:
    """建立套件樹逐檔 bytes 與模式指紋。"""
    項目: list[tuple[str, int, str, int]] = []
    for 目前, 目錄們, 檔案們 in os.walk(根):
        目錄們.sort()
        for 名稱 in sorted(檔案們):
            路徑 = Path(目前) / 名稱
            資料 = 路徑.read_bytes()
            項目.append((
                str(路徑.relative_to(根)), len(資料),
                hashlib.sha256(資料).hexdigest(), 路徑.stat().st_mode & 0o777,
            ))
    return 項目


def _以精確版本載入(套件根: Path, 收據: dict):
    """以 DB 收據建立 exact 定位並用 production loader 讀回該版本 Bundle。"""
    定位 = 技能套件定位(
        version_id=收據["version_id"], bundle_id=收據["bundle_id"],
        manifest_reference=收據["manifest_reference"],
        manifest_digest=收據["manifest_digest"],
        bundle_hash=收據["bundle_hash"], total_bytes=收據["total_bytes"],
    )

    class 提供者:
        """只回應 exact version 的 authoritative provider。"""
        def 取得技能套件定位(self, endpoint_version_id: str):
            assert endpoint_version_id == 收據["version_id"]
            return 定位

    return 已發布技能套件載入器(套件根, 提供者()).載入技能套件快照(
        收據["version_id"], 收據["bundle_hash"], 收據["manifest_reference"],
        "endpoint_version_snapshot",
    )


def _技能內容(快照) -> bytes:
    """取出快照中 SKILL.md 的內容。"""
    for 檔案 in 快照.files:
        if 檔案.path.endswith("SKILL.md"):
            return 檔案.content
    raise AssertionError("快照缺少 SKILL.md")


def test_canonical_live_openapi必須含Draft_Publish_Version與Invoke路徑(tmp_path):
    """Acceptance #6 要求四類路徑都在 canonical live app 上公開。"""
    tmp_path = tmp_path.resolve()
    套件根 = tmp_path / "bundles"
    套件根.mkdir()
    網頁設定 = 生產設定(
        tmp_path / "web.sqlite3", ("http://localhost:5173",), "fake", "fake",
        Cookie安全=False, 工作階段有效秒數=3600,
    )
    Published設定 = Published生產設定(
        tmp_path / "published.sqlite3", 套件根, _安裝工具發布,
        lambda: {"fake": _記錄模型()}, 60.0,
        Planner設定=Planner生產設定(
            "release-1", lambda 路徑: 使用者庫(路徑),
            lambda: 決定性假規劃器(), 3600.0,
        ),
        憑證封套工廠=lambda: AESGCM憑證封套({1: b"K" * 32}, 1),
    )
    規格 = 建立CP4ASGI應用程式(網頁設定, Published設定).openapi()
    路徑集 = set(規格["paths"])

    缺少 = {
        路徑 for 路徑 in (
            "/api/published-endpoints/draft",
            "/api/published-endpoints",
            "/api/published-endpoints/{endpoint_id}/versions",
            "/v1/endpoints/{slug}/invoke",
        ) if 路徑 not in 路徑集
    }
    assert 缺少 == set(), (
        f"canonical live app 缺少管理路由：{sorted(缺少)}；"
        "管理路由器已在 繁中代理/發布介面/路由/規劃發布.py 實作但未接入生產組裝"
    )


def test_產品E2E_v1與v2各自Bundle且Restart後不漂移(tmp_path):
    """走真實 HTTP 完成 v1→改 Live Skill→v2→Restart 全程並逐項 readback。"""
    tmp_path = tmp_path.resolve()
    環境 = _建立環境(tmp_path)

    with TestClient(環境["應用"]) as 客戶端:
        csrf = _登入(客戶端)

        草稿 = 客戶端.post(
            "/api/published-endpoints/draft",
            json={
                "original_requirement_text": _需求文字,
                "selected_skills": [_技能名稱], "response_mode": "structured",
            },
            headers={"X-CSRF-Token": csrf},
        )
        assert 草稿.status_code == 201, 草稿.text
        csrf = _接續CSRF(客戶端, 草稿)
        草稿識別碼 = 草稿.json()["draft_id"]
        預覽 = 草稿.json()["preview"]

        發布 = 客戶端.post(
            "/api/published-endpoints",
            json={
                "draft_id": 草稿識別碼, "slug": "alpha-api",
                "configuration_confirmation": {
                    "system_prompt": 預覽["system_prompt"],
                    "input_schema": 預覽["input_schema"],
                    "response_schema": 預覽["response_schema"],
                    "human_docs": 預覽["human_docs"],
                    "rate_limit": 預覽["rate_limit"],
                },
            },
            headers={"X-CSRF-Token": csrf},
        )
        assert 發布.status_code == 201, 發布.text
        csrf = _接續CSRF(客戶端, 發布)
        v1 = 發布.json()
        金鑰 = v1["initial_api_key"]

        # ---- 驗 B1／M1／R1 ----
        收據列 = _收據列(環境["發布資料庫"])
        assert len(收據列) == 1
        R1 = 收據列[0]
        assert R1["version_id"] == v1["version_id"]
        B1根 = 環境["套件根"] / R1["bundle_id"]
        M1原始 = (B1根 / "manifest.json").read_bytes()
        M1 = json.loads(M1原始)
        assert hashlib.sha256(M1原始).hexdigest() == R1["manifest_digest"]
        assert M1["endpoint_version_id"] == v1["version_id"]
        assert M1["version_number"] == 1
        assert M1["bundle_hash"] == R1["bundle_hash"]
        B1指紋 = _樹指紋(B1根)

        # ---- Invoke v1 ----
        呼叫一 = 客戶端.post(
            "/v1/endpoints/alpha-api/invoke",
            json={"input": {"question": "hi"}},
            headers={"Authorization": f"Bearer {金鑰}"},
        )
        assert 呼叫一.status_code == 200, 呼叫一.text
        assert len(環境["模型"].呼叫) == 1
        第一次參數 = json.dumps(環境["模型"].呼叫[0], default=str, ensure_ascii=False)
        assert "原始發布內容" in 第一次參數

        # ---- 修改 Live Skill 後再 Invoke v1，結果不得漂移 ----
        環境["技能主檔"].write_text(_竄改技能正文, encoding="utf-8")
        呼叫二 = 客戶端.post(
            "/v1/endpoints/alpha-api/invoke",
            json={"input": {"question": "hi"}},
            headers={"Authorization": f"Bearer {金鑰}"},
        )
        assert 呼叫二.status_code == 200, 呼叫二.text
        assert len(環境["模型"].呼叫) == 2
        第二次參數 = json.dumps(環境["模型"].呼叫[1], default=str, ensure_ascii=False)
        assert "原始發布內容" in 第二次參數, "v1 Runtime 不得讀到 Live Skill 新內容"
        assert "事後竄改內容" not in 第二次參數

        # ---- Publish v2 ----
        版本 = 客戶端.post(
            f"/api/published-endpoints/{v1['endpoint_id']}/versions",
            json={"configuration": {
                "original_requirement_text": _需求文字,
                "system_prompt": 預覽["system_prompt"],
                "model_config_snapshot": _模型設定,
                "retry_policy": _重試政策,
                "input_schema": None,
                "response_schema": 預覽["response_schema"],
            }},
            headers={"X-CSRF-Token": csrf},
        )
        assert 版本.status_code == 201, 版本.text
        v2 = 版本.json()
        assert v2["version_id"] != v1["version_id"]
        assert v2["version_number"] == 2
        assert v2["current_version_id"] == v2["version_id"]

        # ---- 驗 B2／M2／R2 與 B1 完全不變 ----
        收據列 = _收據列(環境["發布資料庫"])
        assert len(收據列) == 2
        R2 = [列 for 列 in 收據列 if 列["version_id"] == v2["version_id"]][0]
        assert R2["bundle_id"] != R1["bundle_id"]
        B2根 = 環境["套件根"] / R2["bundle_id"]
        M2 = json.loads((B2根 / "manifest.json").read_bytes())
        assert M2["version_number"] == 2
        assert M2["endpoint_version_id"] == v2["version_id"]
        assert _樹指紋(B1根) == B1指紋, "v2 發布後 B1 必須逐 byte 不變"
        assert [列 for 列 in 收據列 if 列["version_id"] == v1["version_id"]] == [R1]

    # ---- Restart：以同一組 DB／Bundle 根重新啟動 canonical app ----
    重啟模型 = _記錄模型()
    重啟應用 = _建立應用(tmp_path, 重啟模型, 環境["管理相依"])
    with TestClient(重啟應用) as 客戶端:
        重啟呼叫 = 客戶端.post(
            "/v1/endpoints/alpha-api/invoke",
            json={"input": {"question": "hi"}},
            headers={"Authorization": f"Bearer {金鑰}"},
        )
        assert 重啟呼叫.status_code == 200, 重啟呼叫.text
        # slug 解析到 current＝v2，因此應讀到 v2 發布當下（已變更）的技能內容
        參數 = json.dumps(重啟模型.呼叫[-1], default=str, ensure_ascii=False)
        assert "事後竄改內容" in 參數, "Restart 後 current 應為 v2 並載入 B2"

    # ---- Restart 後 Exact Loader：v1 仍載 B1、v2 載 B2 ----
    重啟後收據 = _收據列(環境["發布資料庫"])
    assert len(重啟後收據) == 2
    重啟後R1 = [列 for 列 in 重啟後收據 if 列["version_id"] == v1["version_id"]][0]
    重啟後R2 = [列 for 列 in 重啟後收據 if 列["version_id"] == v2["version_id"]][0]
    assert 重啟後R1 == R1 and 重啟後R2 == R2, "Restart 不得改寫收據"

    v1快照 = _以精確版本載入(環境["套件根"], 重啟後R1)
    v2快照 = _以精確版本載入(環境["套件根"], 重啟後R2)
    assert _技能內容(v1快照) == _原始技能正文.encode(), "Restart 後 v1 必須仍載 B1"
    assert _技能內容(v2快照) == _竄改技能正文.encode(), "Restart 後 v2 必須載 B2"
    assert v1快照.skill_bundle_hash != v2快照.skill_bundle_hash
    assert v1快照.manifest_digest == R1["manifest_digest"]
    assert v2快照.manifest_digest == R2["manifest_digest"]
    assert _樹指紋(B1根) == B1指紋, "Restart 不得改動既有 Bundle"


def test_Startup_Reconciliation可重入(tmp_path):
    """連續多次 startup 必須得到相同的 Bundle 與收據狀態。"""
    tmp_path = tmp_path.resolve()
    環境 = _建立環境(tmp_path)
    with TestClient(環境["應用"]) as 客戶端:
        csrf = _登入(客戶端)
        草稿 = 客戶端.post(
            "/api/published-endpoints/draft",
            json={"original_requirement_text": _需求文字,
                  "selected_skills": [_技能名稱], "response_mode": "structured"},
            headers={"X-CSRF-Token": csrf},
        )
        assert 草稿.status_code == 201, 草稿.text
        csrf = _接續CSRF(客戶端, 草稿)
        預覽 = 草稿.json()["preview"]
        發布 = 客戶端.post(
            "/api/published-endpoints",
            json={"draft_id": 草稿.json()["draft_id"], "slug": "alpha-api",
                  "configuration_confirmation": {
                      "system_prompt": 預覽["system_prompt"],
                      "input_schema": 預覽["input_schema"],
                      "response_schema": 預覽["response_schema"],
                      "human_docs": 預覽["human_docs"],
                      "rate_limit": 預覽["rate_limit"]}},
            headers={"X-CSRF-Token": csrf},
        )
        assert 發布.status_code == 201, 發布.text

    收據基準 = _收據列(環境["發布資料庫"])
    套件基準 = sorted(項目.name for 項目 in 環境["套件根"].iterdir())
    for _ in range(3):
        with TestClient(_建立應用(tmp_path, _記錄模型(), 環境["管理相依"])):
            pass
        assert _收據列(環境["發布資料庫"]) == 收據基準
        assert sorted(項目.name for 項目 in 環境["套件根"].iterdir()) == 套件基準
        assert not any(
            項目.name.startswith(".stage-") for 項目 in 環境["套件根"].iterdir()
        )


def test_HTTP回應不洩漏source_path或Bundle根(tmp_path):
    """管理與呼叫回應都不得投影 Skill 來源路徑或 Bundle Root。"""
    tmp_path = tmp_path.resolve()
    環境 = _建立環境(tmp_path)
    敏感 = (str(環境["技能根"]), str(環境["套件根"]))
    with TestClient(環境["應用"]) as 客戶端:
        csrf = _登入(客戶端)
        草稿 = 客戶端.post(
            "/api/published-endpoints/draft",
            json={"original_requirement_text": _需求文字,
                  "selected_skills": [_技能名稱], "response_mode": "structured"},
            headers={"X-CSRF-Token": csrf},
        )
        assert 草稿.status_code == 201, 草稿.text
        csrf = _接續CSRF(客戶端, 草稿)
        預覽 = 草稿.json()["preview"]
        發布 = 客戶端.post(
            "/api/published-endpoints",
            json={"draft_id": 草稿.json()["draft_id"], "slug": "alpha-api",
                  "configuration_confirmation": {
                      "system_prompt": 預覽["system_prompt"],
                      "input_schema": 預覽["input_schema"],
                      "response_schema": 預覽["response_schema"],
                      "human_docs": 預覽["human_docs"],
                      "rate_limit": 預覽["rate_limit"]}},
            headers={"X-CSRF-Token": csrf},
        )
        assert 發布.status_code == 201, 發布.text
        呼叫 = 客戶端.post(
            "/v1/endpoints/alpha-api/invoke",
            json={"input": {"question": "hi"}},
            headers={"Authorization": f"Bearer {發布.json()['initial_api_key']}"},
        )
        for 回應 in (草稿, 發布, 呼叫):
            for 路徑 in 敏感:
                assert 路徑 not in 回應.text
            assert "source_path" not in 回應.text

    連線 = sqlite3.connect(環境["發布資料庫"])
    try:
        稽核 = "".join(
            str(列) for 列 in 連線.execute("SELECT * FROM audit_events")
        )
    finally:
        連線.close()
    for 路徑 in 敏感:
        assert 路徑 not in 稽核
    assert "source_path" not in 稽核


def test_管理路由必須回傳輪替後的接續CSRF(tmp_path):
    """single-use CSRF 被消耗後，接續權杖必須經 header 或 Set-Cookie 回到客戶端。

    否則真實瀏覽器每次登入只能完成一個管理寫入請求，Draft→Publish→Version
    這條產品主線在真 HTTP 下無法完成。
    """
    tmp_path = tmp_path.resolve()
    環境 = _建立環境(tmp_path)
    with TestClient(環境["應用"]) as 客戶端:
        csrf = _登入(客戶端)
        草稿 = 客戶端.post(
            "/api/published-endpoints/draft",
            json={"original_requirement_text": _需求文字,
                  "selected_skills": [_技能名稱], "response_mode": "structured"},
            headers={"X-CSRF-Token": csrf},
        )
        assert 草稿.status_code == 201, 草稿.text
        接續 = 草稿.headers.get("X-CSRF-Token") or 客戶端.cookies.get("published_web_csrf")
        assert 接續 is not None and 接續 != csrf, (
            "管理路由消耗了 single-use CSRF 卻未回傳接續權杖："
            "handler 自行回傳 JSONResponse，FastAPI 不會合併相依項注入的 Response "
            "header／Set-Cookie（對照 繁中代理/發布介面/路由/聊天.py 回傳 dict 即正常）"
        )


def _發布v1並取得金鑰(客戶端: TestClient) -> tuple[dict, str]:
    """走真實 HTTP 完成 Draft 與 Publish v1。"""
    csrf = _登入(客戶端)
    草稿 = 客戶端.post(
        "/api/published-endpoints/draft",
        json={"original_requirement_text": _需求文字,
              "selected_skills": [_技能名稱], "response_mode": "structured"},
        headers={"X-CSRF-Token": csrf},
    )
    assert 草稿.status_code == 201, 草稿.text
    預覽 = 草稿.json()["preview"]
    csrf = _接續CSRF(客戶端, 草稿)
    發布 = 客戶端.post(
        "/api/published-endpoints",
        json={"draft_id": 草稿.json()["draft_id"], "slug": "alpha-api",
              "configuration_confirmation": {
                  "system_prompt": 預覽["system_prompt"],
                  "input_schema": 預覽["input_schema"],
                  "response_schema": 預覽["response_schema"],
                  "human_docs": 預覽["human_docs"],
                  "rate_limit": 預覽["rate_limit"]}},
        headers={"X-CSRF-Token": csrf},
    )
    assert 發布.status_code == 201, 發布.text
    return 發布.json(), 發布.json()["initial_api_key"]


@pytest.mark.parametrize("竄改對象", ["skill", "manifest"])
def test_Tamper後Loader在模型呼叫前固定拒絕(tmp_path, 竄改對象: str):
    """竄改已發布 Bundle 後，Loader 必須在任何模型呼叫前關閉失敗。"""
    tmp_path = tmp_path.resolve()
    環境 = _建立環境(tmp_path)
    with TestClient(環境["應用"]) as 客戶端:
        _v1, 金鑰 = _發布v1並取得金鑰(客戶端)
        assert 環境["模型"].呼叫 == [], "發布本身不得呼叫模型"

        收據 = _收據列(環境["發布資料庫"])[0]
        套件根 = 環境["套件根"] / 收據["bundle_id"]
        目標 = (
            套件根 / "manifest.json" if 竄改對象 == "manifest"
            else 套件根 / _技能名稱 / "SKILL.md"
        )
        # Bundle 已封為唯讀，必須先放寬才能模擬 offline tamper，之後復原模式
        父目錄 = 目標.parent
        os.chmod(套件根, 0o755)
        if 父目錄 != 套件根:
            os.chmod(父目錄, 0o755)
        os.chmod(目標, 0o644)
        原始 = 目標.read_bytes()
        目標.write_bytes(原始.replace(b"Alpha", b"Bravo") if b"Alpha" in 原始
                         else 原始 + b" ")
        os.chmod(目標, 0o444)
        if 父目錄 != 套件根:
            os.chmod(父目錄, 0o555)
        os.chmod(套件根, 0o555)

        回應 = 客戶端.post(
            "/v1/endpoints/alpha-api/invoke",
            json={"input": {"question": "hi"}},
            headers={"Authorization": f"Bearer {金鑰}"},
        )

    assert 回應.status_code != 200, 回應.text
    assert 環境["模型"].呼叫 == [], "Loader 必須在模型呼叫前拒絕被竄改的 Bundle"
    for 敏感 in (str(環境["技能根"]), str(環境["套件根"]), "source_path"):
        assert 敏感 not in 回應.text


def test_Tamper後Exact_Loader直接拒絕(tmp_path):
    """以 production loader 直接讀取被竄改 Bundle 必須固定失敗且不洩漏細節。"""
    tmp_path = tmp_path.resolve()
    環境 = _建立環境(tmp_path)
    with TestClient(環境["應用"]) as 客戶端:
        _發布v1並取得金鑰(客戶端)
    收據 = _收據列(環境["發布資料庫"])[0]
    assert _技能內容(_以精確版本載入(環境["套件根"], 收據)) == _原始技能正文.encode()

    目標 = 環境["套件根"] / 收據["bundle_id"] / _技能名稱 / "SKILL.md"
    os.chmod(環境["套件根"] / 收據["bundle_id"], 0o755)
    os.chmod(目標.parent, 0o755)
    os.chmod(目標, 0o644)
    目標.write_bytes(_竄改技能正文.encode())
    os.chmod(目標, 0o444)
    os.chmod(目標.parent, 0o555)
    os.chmod(環境["套件根"] / 收據["bundle_id"], 0o555)

    with pytest.raises(技能套件載入錯誤) as 資訊:
        _以精確版本載入(環境["套件根"], 收據)
    assert 資訊.value.__cause__ is None and 資訊.value.__suppress_context__
    for 敏感 in (str(環境["技能根"]), str(環境["套件根"])):
        assert 敏感 not in str(資訊.value)
