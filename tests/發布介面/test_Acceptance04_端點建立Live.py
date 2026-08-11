"""Acceptance #4 EP-1：凍結正式草稿與端點建立 HTTP 契約的 RED 測試。

本模組只從 ``建立CP4ASGI應用程式`` 建立正式應用，不建立手工 FastAPI
應用或替代路由。測試固定 Draft／Create 路徑、方法、OpenAPI 本文與回應、
正式 Session／單次 CSRF 相依身分，以及不得由客戶端聲稱的內部權威欄位。
目前預期因正式應用尚未掛載兩條管理路由而 RED；不得在本卡修改 production source。
"""

from __future__ import annotations

import sqlite3
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
from 繁中代理.發布介面.生產Published執行 import Published生產設定, 生產Controller建構器
from 繁中代理.發布介面.生產Published管理 import Planner生產設定, 延遲發布管理服務
from 繁中代理.發布介面.生產Published管理 import 草稿規劃服務不可用
from 繁中代理.發布介面.生產組裝 import 建立生產應用程式
from 繁中代理.發布介面.規劃.規劃器供應商 import 決定性假規劃器
from 繁中代理.發布介面.路由.規劃發布 import 發布確認
from 繁中代理.發布介面.路由.網頁認證 import 是模組CSRF相依項
from 繁中代理.發布介面.設定 import 生產設定


草稿路徑 = "/api/published-endpoints/draft"
端點建立路徑 = "/api/published-endpoints"
禁止客戶端聲稱欄位 = (
    "owner_id",
    "service_account_id",
    "role",
    "selected_tools",
    "system_prompt",
)


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


def _建立正式應用程式(暫存目錄: Path, *, 封套工廠=_建立憑證封套):
    """建立不讀隱含環境且可由正式 lifespan 啟動的 CP4 應用。

    參數：
        暫存目錄: pytest 提供的隔離目錄，用來配置 Web DB、Published DB 與 bundle root。
        封套工廠: 只保存至 startup 才呼叫的 explicit credential envelope factory。
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
            lambda: 決定性假規劃器(), 3600.0,
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
